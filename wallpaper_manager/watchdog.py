"""
watchdog.py — Crash resilience for the wallpaper engine (Stage 3).

## What problem does this solve?

linux-wallpaperengine can crash for various reasons:
- Broken wallpaper assets (corrupted scene files, missing textures)
- GPU driver issues (Wayland compositor reset, VRAM pressure)
- Transient OpenGL errors

Without a watchdog, the wallpaper just goes black and stays that way until
you manually restart it. With the watchdog, transient crashes are recovered
automatically, and persistent crashes (broken wallpaper) are detected and
stopped after a configurable number of retries.

## Design — the Supervisor pattern

This is the same pattern used by:
- systemd's Restart= directive
- Docker's --restart=on-failure
- Erlang/OTP supervisors
- Spring Boot Actuator's health checks + restart

The idea: wrap the subprocess in a loop that monitors its health.
If it dies unexpectedly, restart it. But cap the retries so we don't
hammer a broken wallpaper forever.

## How the retry counter works

    start wallpaper
         |
         v
    ┌─ monitor loop ──────────────────────────────┐
    │   process alive? ──yes──> sleep 1s, loop     │
    │        |                                     │
    │       no (crashed!)                          │
    │        |                                     │
    │   ran for > 60s? ──yes──> reset counter to 0 │
    │        |                  (transient crash)   │
    │       no                                     │
    │        |                                     │
    │   counter < max? ──yes──> wait, restart       │
    │        |                                     │
    │       no                                     │
    │        |                                     │
    │   GIVE UP (persistently broken)              │
    └──────────────────────────────────────────────┘

The 60-second threshold distinguishes:
- Transient crashes: process ran fine for a while, then died (GPU hiccup, 
  compositor restart). Reset the counter — it was probably a one-off.
- Persistent crashes: process dies within seconds of starting, every time.
  Something is fundamentally wrong (broken wallpaper). Stop retrying.
"""

import logging
import threading
import time

from wallpaper_manager.config import Config
from wallpaper_manager.launcher import WallpaperLauncher

logger = logging.getLogger(__name__)

# If the process runs for at least this many seconds before crashing,
# we consider it a "transient" crash and reset the failure counter.
STABILITY_THRESHOLD_SECONDS = 60


class Watchdog:
    """
    Monitors and auto-restarts the wallpaper engine on crashes.

    Usage:
        watchdog = Watchdog(config)
        watchdog.start()         # launches engine + starts monitoring
        ...
        watchdog.request_stop()  # signals clean shutdown
        watchdog.wait()          # blocks until monitoring thread exits

    The watchdog runs in a background thread so the main thread can
    handle signals (SIGINT/SIGTERM) and the tray GUI (Stage 5).

    This is analogous to a Java ScheduledExecutorService that polls
    a process's health and triggers restarts.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._launcher = WallpaperLauncher(config)
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        
        # Concurrency control for switching wallpapers
        self._switching_lock = threading.Lock()
        self._is_switching = False
        self._is_paused = False

        # Retry settings from config
        self._max_retries = config.resilience.max_retries
        self._retry_delay = config.resilience.retry_delay_seconds

    @property
    def launcher(self) -> WallpaperLauncher:
        """Expose the launcher for output reading etc."""
        return self._launcher

    def start(self, auto_launch: bool = True) -> None:
        """
        Start the wallpaper engine and begin monitoring.

        The initial launch happens on the calling thread (so errors
        propagate immediately). Monitoring then continues in a background
        daemon thread.
        """
        logger.info("Watchdog starting up...")

        if auto_launch:
            # Initial launch — let exceptions propagate to caller
            self._launcher.start()
        else:
            logger.info("Auto-start disabled by config. Waiting for user action.")
            self._is_paused = True

        # Start monitoring in background
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="watchdog-monitor",
            daemon=True,
        )
        self._monitor_thread.start()
        logger.info("Watchdog monitoring thread started.")

    def request_stop(self) -> None:
        """
        Signal the watchdog to stop monitoring and shut down the engine.

        This is non-blocking — call wait() to block until fully stopped.
        Like calling Future.cancel() in Java.
        """
        logger.info("Watchdog shutdown requested.")
        self._stop_event.set()

    def wait(self, timeout: float | None = None) -> None:
        """
        Block until the monitoring thread exits.

        Parameters
        ----------
        timeout : float | None
            Max seconds to wait. None = wait forever.
        """
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=timeout)

    def stop(self) -> None:
        """Convenience: request stop, kill engine, wait for thread."""
        self.request_stop()
        self._launcher.stop()
        self.wait(timeout=5)

    def switch_wallpaper(self, new_wallpaper: str) -> None:
        """
        Switch to a different wallpaper (used by tray GUI in Stage 5).

        Stops the current wallpaper, updates config, and starts the new one.
        Uses a lock and flag to prevent the monitor loop from thinking the
        process crashed while we're intentionally stopping it.
        """
        logger.info("[Switch] Switching wallpaper: %s → %s", self.config.wallpaper, new_wallpaper)
        
        # Log properties that will be applied to the new wallpaper
        wp_props = self.config.properties.get(new_wallpaper, {})
        if wp_props:
            logger.info("[Switch] Properties for %s: %s", new_wallpaper, wp_props)
        else:
            logger.info("[Switch] No custom properties for %s", new_wallpaper)
        
        with self._switching_lock:
            self._is_switching = True
            self._is_paused = False
            
        try:
            self._launcher.stop()
            self.config.wallpaper = new_wallpaper
            self._launcher.start()
            logger.info("[Switch] Wallpaper %s started successfully", new_wallpaper)
        finally:
            with self._switching_lock:
                self._is_switching = False

    def _monitor_loop(self) -> None:
        """
        Main monitoring loop (runs in background thread).

        Continuously checks if the engine process is alive. If it dies:
        1. Check if it ran long enough to be a "transient" crash
        2. If within retry limit, wait and restart
        3. If retry limit reached, give up

        The loop exits when request_stop() is called or retries are exhausted.
        """
        consecutive_failures = 0
        last_start_time = time.monotonic()

        while not self._stop_event.is_set():
            # Check if paused (e.g. auto-start disabled on mount)
            if self._is_paused:
                self._stop_event.wait(timeout=1.0)
                continue

            # Check process health every second
            if self._launcher.is_running():
                self._stop_event.wait(timeout=1.0)
                continue

            # ── Process died ─────────────────────────────────────────
            # If we requested a stop, this is expected — don't restart
            if self._stop_event.is_set():
                break
                
            # If we are in the middle of a manual switch, ignore this "death"
            with self._switching_lock:
                if self._is_switching:
                    self._stop_event.wait(timeout=0.5)
                    continue

            exit_code = self._launcher.get_exit_code()
            runtime = time.monotonic() - last_start_time

            logger.warning(
                "Wallpaper engine exited (code=%s, runtime=%.1fs)",
                exit_code,
                runtime,
            )

            # Did it run long enough to be a transient issue?
            if runtime >= STABILITY_THRESHOLD_SECONDS:
                # It ran for a while — this was probably a one-off glitch
                # (GPU reset, compositor restart, etc.)
                logger.info(
                    "Process ran for %.0fs (>%ds threshold) — "
                    "treating as transient crash, resetting failure counter.",
                    runtime,
                    STABILITY_THRESHOLD_SECONDS,
                )
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                logger.warning(
                    "Process died quickly (%.1fs) — "
                    "consecutive failure %d/%d",
                    runtime,
                    consecutive_failures,
                    self._max_retries,
                )

            # Check retry limit
            if consecutive_failures >= self._max_retries:
                logger.error(
                    "Giving up after %d consecutive failures. "
                    "The wallpaper may be broken — try a different one.\n"
                    "  Last exit code: %s\n"
                    "  Check logs: ~/.local/share/wallpaper_manager/wallpaper_manager.log",
                    self._max_retries,
                    exit_code,
                )
                break

            # Wait before restarting (gives the system time to recover)
            logger.info(
                "Restarting in %d seconds...", self._retry_delay
            )
            self._stop_event.wait(timeout=self._retry_delay)

            if self._stop_event.is_set():
                break

            # Restart
            try:
                logger.info("Restarting wallpaper engine (attempt %d)...", 
                            consecutive_failures + 1)
                self._launcher.start()
                last_start_time = time.monotonic()
            except Exception:
                logger.exception("Failed to restart wallpaper engine.")
                consecutive_failures += 1
                if consecutive_failures >= self._max_retries:
                    logger.error("Max retries reached — giving up.")
                    break

        logger.info("Watchdog monitoring loop exited.")
