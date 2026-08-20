"""
launcher.py — Launch and manage the linux-wallpaperengine subprocess.

This is the core of Stage 1.  It's conceptually similar to what you'd do in
Java with ProcessBuilder:

    Java:
        new ProcessBuilder("linux-wallpaperengine", "--bg", id)
            .redirectOutput(logFile)
            .start();

    Python:
        subprocess.Popen(
            ["linux-wallpaperengine", "--bg", id],
            stdout=log_file, stderr=log_file,
        )

Key differences from Java's Process API:
- subprocess.Popen() doesn't block — it starts the process and returns immediately
- .poll() checks if the process has exited (returns None if still running)
- .terminate() sends SIGTERM (polite "please exit"), .kill() sends SIGKILL (force)
- .wait(timeout=N) blocks until the process exits or the timeout expires
"""

import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path

from wallpaper_manager.config import Config

logger = logging.getLogger(__name__)


class WallpaperLauncher:
    """
    Manages the lifecycle of a single linux-wallpaperengine process.

    Usage:
        launcher = WallpaperLauncher(config)
        launcher.start()       # launches the wallpaper
        ...
        launcher.stop()        # cleanly shuts it down
        launcher.is_running()  # check if still alive
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        # The subprocess handle — None when no process is running.
        # In Java terms, this is like holding a reference to a Process object.
        self._process: subprocess.Popen | None = None

    def _resolve_wallpaper_path(self) -> str:
        """
        Turn the wallpaper config value into a path that linux-wallpaperengine
        can understand.

        If it's a numeric Workshop ID like "1845706469", we resolve it to the
        full path:  workshop_dir / 1845706469
        If it's already an absolute path, we use it as-is.
        """
        wallpaper = self.config.wallpaper

        # Check if it looks like a Workshop ID (all digits)
        if wallpaper.isdigit():
            full_path = Path(self.config.workshop_dir) / wallpaper
            if full_path.exists():
                return str(full_path)
            else:
                # The folder doesn't exist yet (maybe drive not mounted).
                # Return the ID as-is — linux-wallpaperengine might resolve it
                # on its own if assets are configured.
                logger.warning(
                    "Workshop folder %s does not exist. "
                    "Passing raw ID to linux-wallpaperengine.",
                    full_path,
                )
                return wallpaper
        else:
            # It's a path — Security check: resolve it and make sure it exists
            # to prevent path traversal exploits or passing garbage.
            resolved = Path(wallpaper).resolve()
            if not resolved.exists():
                logger.warning("Wallpaper path does not exist: %s", resolved)
            return str(resolved)

    def _build_command(self, wallpaper_override: str | None = None) -> list[str]:
        """
        Build the command-line arguments list.

        linux-wallpaperengine usage (from the upstream repo):
            linux-wallpaperengine [options] <wallpaper_id_or_path>

        Key flags:
            --screen-root <name>   Target a specific monitor (e.g. HDMI-A-1)
            --bg <id_or_path>      Set the background for a screen
            --scaling <mode>       fill / stretch / fit / default
            --assets-dir <path>    Custom path for WE assets
            --set-property K=V     Override a wallpaper property

        Parameters
        ----------
        wallpaper_override : str | None
            If provided, use this wallpaper ID/path instead of config.wallpaper.
            Used when switch_wallpaper updates the config mid-flight.
        """
        # Verify the binary exists and is executable
        binary = self.config.binary
        if not Path(binary).is_absolute():
            # It's a name like "linux-wallpaperengine" — check $PATH
            resolved = shutil.which(binary)
            if resolved is None:
                raise FileNotFoundError(
                    f"'{binary}' not found on $PATH. "
                    f"Install it with: yay -S linux-wallpaperengine-git"
                )
            binary = resolved
            
        if not os.access(binary, os.X_OK):
            raise PermissionError(
                f"The binary at '{binary}' is not executable. "
                f"Check its permissions with: ls -l '{binary}'"
            )

        # Use override or resolve from config
        if wallpaper_override:
            self.config.wallpaper = wallpaper_override
        
        # Prepare the wallpaper (sanitize/unpack)
        from wallpaper_manager.sanitizer import prepare_wallpaper
        wallpaper_path = prepare_wallpaper(self.config.workshop_dir, self.config.wallpaper)
        
        # The wallpaper folder name is the workshop ID
        wp_id = Path(wallpaper_path).name

        cmd = [binary]

        # ── Global options (before screen targeting) ─────────────────
        if self.config.assets_dir:
            cmd.extend(["--assets-dir", self.config.assets_dir])
            
        if self.config.fps:
            cmd.extend(["--fps", str(self.config.fps)])

        if self.config.silent:
            cmd.append("--silent")

        # Wayland layer: 'bottom' is the correct layer for KDE Plasma.
        # 'background' is invisible on KDE because Plasma's desktop shell occupies it.
        # On wlroots compositors (Sway, Hyprland), 'background' would work instead.
        cmd.extend(["--layer", "bottom"])
        
        # Disable mouse input on the wallpaper surface so clicks pass through
        # to the KDE desktop underneath (allows right-click menu + desktop icons)
        cmd.append("--disable-mouse")
            
        # ── Wallpaper property overrides ─────────────────────────────
        # --set-property expects a SINGLE string: "key=value"
        # For color values with spaces like "0 0 0", the entire "key=0 0 0"
        # must be ONE element in the subprocess args list.
        wp_props = self.config.properties.get(wp_id, {})
        for key, val in wp_props.items():
            prop_str = f"{key}={val}"
            cmd.extend(["--set-property", prop_str])
            logger.debug("  property override: --set-property %s", prop_str)

        # ── Screen Targeting ─────────────────────────────────────────
        # linux-wallpaperengine opens as a floating window unless we specify
        # --screen-root (or --screen-span). We want it as a desktop background!
        screens_to_target = []
        
        if self.config.screen:
            screens_to_target = [self.config.screen]
        else:
            # Auto-detect screens using kscreen-doctor (KDE Plasma standard)
            try:
                import re
                res = subprocess.run(
                    ["kscreen-doctor", "-o"], 
                    capture_output=True, text=True, timeout=2
                )
                
                # kscreen-doctor outputs ANSI color codes, strip them first
                ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
                clean_output = ansi_escape.sub('', res.stdout)
                
                for line in clean_output.splitlines():
                    if line.startswith("Output:"):
                        parts = line.split()
                        if len(parts) >= 3:
                            screens_to_target.append(parts[2])
            except Exception as e:
                logger.warning("Failed to auto-detect screens with kscreen-doctor: %s", e)

        if screens_to_target:
            for screen in screens_to_target:
                cmd.extend(["--screen-root", screen])
                if self.config.scaling:
                    cmd.extend(["--scaling", self.config.scaling])
                cmd.extend(["--bg", wallpaper_path])
        else:
            logger.warning("No screens detected! linux-wallpaperengine will open as a window.")
            if self.config.scaling:
                cmd.extend(["--scaling", self.config.scaling])
            cmd.append(wallpaper_path)

        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the subprocess."""
        env = os.environ.copy()
        
        if self.config.disable_gl_threaded_optimizations:
            env["__GL_THREADED_OPTIMIZATIONS"] = "0"
            logger.info("Set __GL_THREADED_OPTIMIZATIONS=0 (OpenGL glitch workaround)")
            
        return env

    def start(self) -> None:
        """
        Launch the linux-wallpaperengine process.

        stdout and stderr from the child process are piped to our logger.
        We don't capture them into memory (that could grow unbounded for a
        long-running process) — instead we redirect to subprocess.PIPE and
        could read in a thread, but for simplicity we log to files directly.

        Raises FileNotFoundError if the binary isn't found.
        """
        if self._process is not None and self.is_running():
            logger.warning("Wallpaper engine is already running (PID %d).", self._process.pid)
            return

        cmd = self._build_command()
        env = self._build_env()
        
        logger.info("Starting wallpaper engine: %s", " ".join(cmd))
        logger.debug("Subprocess argv (raw list): %r", cmd)

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # merge stderr into stdout
                preexec_fn=os.setsid,
                env=env,
            )
            if self._process is not None:
                logger.info(
                    "Wallpaper engine started with PID %d.", self._process.pid
                )
        except FileNotFoundError:
            logger.error("Binary not found: %s", cmd[0])
            raise
        except Exception:
            logger.exception("Failed to start wallpaper engine.")
            raise

    def stop(self) -> bool:
        """
        Gracefully stop the wallpaper engine process.

        Strategy:
        1. Send SIGTERM (like clicking X on a window — polite shutdown request)
        2. Wait up to 5 seconds for it to exit
        3. If it's still running, send SIGKILL (force kill — like End Task)

        Returns True if the process was stopped, False if nothing was running.
        """
        proc = self._process
        if proc is None or not self.is_running():
            logger.info("No wallpaper engine process to stop.")
            return False

        pid = proc.pid
        logger.info("Stopping wallpaper engine (PID %d)...", pid)

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)

            try:
                proc.wait(timeout=5)
                logger.info("Wallpaper engine stopped gracefully.")
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Wallpaper engine didn't stop in 5s — sending SIGKILL."
                )
                os.killpg(os.getpgid(pid), signal.SIGKILL)
                proc.wait(timeout=3)
                logger.info("Wallpaper engine force-killed.")
        except ProcessLookupError:
            logger.info("Process already exited.")
        except Exception:
            logger.exception("Error stopping wallpaper engine.")

        self._process = None
        return True

    def is_running(self) -> bool:
        """
        Check if the wallpaper engine process is still alive.

        .poll() returns None if the process is still running, or the
        exit code if it has finished.  This is a non-blocking check —
        it doesn't wait for the process to exit.
        """
        if self._process is None:
            return False
        return self._process.poll() is None

    def get_exit_code(self) -> int | None:
        """
        Get the exit code of the last process, or None if still running.

        Exit codes:
        - 0 = success (normal exit)
        - Negative = killed by signal (e.g. -9 = SIGKILL, -15 = SIGTERM)
        - Positive = error (application-specific)
        """
        if self._process is None:
            return None
        return self._process.poll()

    def read_output(self) -> str:
        """
        Read any available stdout/stderr from the process.
        Returns empty string if no output or process not running.
        """
        proc = self._process
        if proc is None or proc.stdout is None:
            return ""

        try:
            import select

            ready, _, _ = select.select([proc.stdout], [], [], 0)
            if ready:
                data = proc.stdout.read(4096)
                if data:
                    return data.decode("utf-8", errors="replace")
        except Exception:
            pass

        return ""
