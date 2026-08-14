"""
main.py — Entry point for the wallpaper manager (Stage 1).

This is what you run:
    python3 -m wallpaper_manager.main

It loads the config, starts the wallpaper engine, and waits for you to
press Ctrl+C (which sends SIGINT) to shut down cleanly.

Why `python3 -m wallpaper_manager.main` instead of `python3 main.py`?
---------------------------------------------------------------------------
The `-m` flag tells Python to run a module by its package path.  This ensures
that relative imports (like `from wallpaper_manager.config import ...`) work
correctly.  If you run `python3 main.py` directly, Python doesn't know about
the package structure and imports will fail.

This is similar to how in Java you run `java -cp . com.example.Main` instead
of `java Main.java` when you have package imports.
"""

import logging
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from wallpaper_manager.config import (
    APP_DATA_DIR,
    LOG_FILE,
    Config,
    load_config,
    validate_config,
)
from wallpaper_manager.watchdog import Watchdog


def setup_logging() -> None:
    """
    Configure Python's logging system.

    We set up two outputs (called "handlers" in Python logging):
    1. Console (stderr) — so you can see what's happening in the terminal
    2. Rotating file — for persistent debugging

    RotatingFileHandler works like Log4j's RollingFileAppender:
    - maxBytes: when the log file reaches this size, it's rotated
    - backupCount: how many old log files to keep (e.g. .log.1, .log.2)
    """
    # Ensure the log directory exists
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Root logger — all loggers in our app inherit from this
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Format: timestamp [LEVEL] module — message
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — INFO and above
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    # File handler — DEBUG and above (more verbose)
    # 5 MB per file, keep 3 backups = max 20 MB of logs
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def output_reader(launcher: WallpaperLauncher, logger: logging.Logger) -> None:
    """
    Read stdout/stderr from the wallpaper engine process and log it.

    This runs in a background thread so it doesn't block the main loop.
    It reads line-by-line from the process's stdout pipe and writes each
    line to our log file.

    Think of it like a CompletableFuture.runAsync() in Java that consumes
    a process's output stream.
    """
    if launcher._process is None or launcher._process.stdout is None:
        return

    try:
        for line in launcher._process.stdout:
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.info("[wallpaper-engine] %s", text)
    except Exception:
        pass  # Process ended or pipe broken — that's fine


def main() -> None:
    """
    Main entry point.

    Flow:
    1. Set up logging
    2. Load config from ~/.config/wallpaper_manager.toml (+ auto-detect paths)
    3. Mount NTFS drive if UUID configured and not yet mounted (Stage 2)
    4. Re-run path auto-detection (drive is now available)
    5. Validate config
    6. Start wallpaper engine via Watchdog (Stage 3 — auto-restart on crash)
    7. Spin up a thread to capture its output
    8. Wait for SIGINT (Ctrl+C) or SIGTERM
    9. Clean shutdown
    """
    setup_logging()
    logger = logging.getLogger("wallpaper_manager")

    logger.info("=" * 60)
    logger.info("Wallpaper Manager starting up")
    logger.info("=" * 60)

    # ── Load config ──────────────────────────────────────────────
    # First pass: load raw config values and run auto-detection.
    # Note: auto-detection may fail here if the NTFS drive isn't mounted
    # yet (the paths under /run/media/ won't exist).  That's OK — we'll
    # mount the drive next and re-run detection.
    config = load_config()

    # ── Mount drive if needed (Stage 2) ──────────────────────────
    # This must happen BEFORE validation, because:
    #   1. The drive might not be mounted yet (e.g. cold boot)
    #   2. resolve_paths() scans /run/media/ — if the drive isn't mounted,
    #      those directories don't exist and auto-detection returns empty
    #   3. After mounting, we re-run resolve_paths() to pick up the
    #      now-visible Steam directories
    #
    # However, if auto-detection already found both paths (meaning the drive
    # is already accessible — e.g. mounted via KDE auto-mount, /etc/fstab,
    # or a previous udisksctl call), we skip the mount step entirely.
    # This avoids triggering a Polkit password prompt unnecessarily.
    paths_already_resolved = bool(config.assets_dir and config.workshop_dir)

    if config.drive.uuid and not paths_already_resolved:
        from wallpaper_manager.mount import mount_drive, MountError, DriveNotFoundError

        logger.info(
            "Steam paths not yet found — attempting to mount drive UUID=%s",
            config.drive.uuid,
        )

        try:
            mount_point = mount_drive(config.drive.uuid)
            if mount_point:
                logger.info("Drive available at: %s", mount_point)

                # Re-run path auto-detection now that the drive is mounted.
                # If the user already set explicit paths, resolve_paths()
                # respects those and skips detection (it only fills in blanks).
                from wallpaper_manager.config import resolve_paths
                resolve_paths(config)
        except DriveNotFoundError as e:
            logger.error("Drive not found: %s", e)
            logger.error(
                "The NTFS drive with your wallpapers is not connected.\n"
                "  Configured UUID: %s\n"
                "  Check connected drives with: lsblk -o NAME,UUID,LABEL",
                config.drive.uuid,
            )
            sys.exit(1)
        except MountError as e:
            logger.error("Failed to mount drive: %s", e)
            sys.exit(0)  # Exit 0 so systemd doesn't infinite loop if user cancels polkit
    elif paths_already_resolved:
        logger.info(
            "Steam paths already accessible — skipping drive mount step."
        )
    else:
        logger.debug("No drive UUID configured — skipping mount step.")

    logger.info(
        "Config loaded: assets_dir=%s, workshop_dir=%s, wallpaper=%s",
        config.assets_dir,
        config.workshop_dir,
        config.wallpaper,
    )

    errors = validate_config(config)
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        logger.error(
            "Fix the errors above in ~/.config/wallpaper_manager.toml "
            "and try again."
        )
        sys.exit(1)

    # ── Start wallpaper engine with watchdog (Stage 3) ────────────
    # The Watchdog wraps the launcher in a supervisor loop:
    # - If the engine crashes, it auto-restarts after a brief delay
    # - If it crashes too many times in quick succession (consecutive_failures
    #   >= max_retries), it gives up (the wallpaper is probably broken)
    # - If it runs for >60s before crashing, the failure counter resets
    #   (it was a transient glitch, not a broken wallpaper)
    watchdog = Watchdog(config)

    try:
        watchdog.start()
    except FileNotFoundError:
        logger.error(
            "linux-wallpaperengine not found. Install it with:\n"
            "  yay -S linux-wallpaperengine-git"
        )
        sys.exit(1)
    except Exception:
        logger.exception("Failed to start wallpaper engine.")
        sys.exit(1)

    # Start a background thread to read and log the engine's output.
    # daemon=True means this thread will be killed automatically when
    # the main program exits (like a Java daemon thread).
    reader_thread = threading.Thread(
        target=output_reader,
        args=(watchdog.launcher, logging.getLogger("wallpaper_manager.output")),
        daemon=True,
    )
    reader_thread.start()

    # ── Wait for shutdown signal ─────────────────────────────────
    # threading.Event is like Java's CountDownLatch(1) — we can wait on
    # it, and another thread (the signal handler) can trigger it.
    shutdown_event = threading.Event()

    def handle_shutdown(signum, frame):
        """Called when Ctrl+C (SIGINT) or SIGTERM is received."""
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — shutting down...", sig_name)
        
        # If the PyQt6 event loop is running, we must tell it to quit
        # so that it gracefully unwinds and we can proceed to cleanup.
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.quit()
                return
        except ImportError:
            pass
            
        # Fallback if no GUI
        shutdown_event.set()

    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    logger.info("Wallpaper engine is running (with crash resilience). Press Ctrl+C to stop.")
    logger.info(
        "Crash policy: max %d retries, %ds delay between restarts.",
        config.resilience.max_retries,
        config.resilience.retry_delay_seconds,
    )
    logger.info("Logs: %s", LOG_FILE)

    # ── Hook the PyQt6 GUI Event Loop (Stage 5) ──────────────────
    # If PyQt6 is installed, this will block and run the tray icon until
    # the user clicks 'Quit' or we receive a SIGTERM.
    try:
        from wallpaper_manager.gui import run_gui
        run_gui(watchdog)
    except ImportError:
        logger.warning(
            "PyQt6 is not installed. Running in headless mode without System Tray.\n"
            "To enable the GUI: yay -S python-pyqt6"
        )
        # Block until shutdown signal in headless mode
        shutdown_event.wait()

    # ── Clean shutdown ───────────────────────────────────────────
    watchdog.stop()
    logger.info("Wallpaper Manager shut down cleanly.")


if __name__ == "__main__":
    main()
