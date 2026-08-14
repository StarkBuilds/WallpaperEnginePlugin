"""
gui.py — System Tray and GUI using PyQt6 (Stage 5, Phase 1).

This module handles the tray icon, the context menu, and the Wayland integration.
"""

import json
import logging
import os
import signal
from pathlib import Path

# We import PyQt6 components
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import QTimer

logger = logging.getLogger(__name__)


def generate_desktop_file() -> None:
    """
    Generates a .desktop file to fix Wayland generic 'W' icon issues.
    
    Wayland compositors (like KWin) use the .desktop file to map a running
    window/application back to its icon and name. We create this file automatically
    if it doesn't exist so the user doesn't have to manually create it.
    """
    desktop_dir = Path.home() / ".local" / "share" / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = desktop_dir / "wallpaper-manager.desktop"
    
    # We always update it just in case the absolute path to python changed
    content = f"""[Desktop Entry]
Name=Wallpaper Manager
Exec=/usr/bin/python3 -m wallpaper_manager.main
Icon=wallpaper-manager
Type=Application
Terminal=false
Categories=Utility;
"""
    desktop_file.write_text(content)
    logger.info("Ensured desktop file exists at %s", desktop_file)


class TrayApp:
    def __init__(self, watchdog, app_instance: QApplication):
        self.watchdog = watchdog
        self.app = app_instance
        self._is_paused = False
        
        # ── Wayland Icon Fix ─────────────────────────────────────────
        # This string must match the filename of the .desktop file
        # (without the .desktop extension). This is the "handshake".
        self.app.setDesktopFileName("wallpaper-manager")
        
        # Don't quit when the last window is closed (since it's a tray app)
        self.app.setQuitOnLastWindowClosed(False)
        
        # Load the custom icon (you'll need to drop an icon.svg in the root)
        # We look in the project root (parent of the wallpaper_manager package)
        project_root = Path(__file__).parent.parent
        icon_path = project_root / "icon.svg"
        
        if icon_path.exists():
            self.icon = QIcon(str(icon_path))
            self.app.setWindowIcon(self.icon)
            logger.info("Loaded custom icon from %s", icon_path)
        else:
            logger.warning("Icon not found at %s, using fallback.", icon_path)
            self.icon = QIcon.fromTheme("video-display") # Fallback icon
            
        # ── Setup Tray Icon ──────────────────────────────────────────
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self.icon)
        self.tray.setToolTip("Wallpaper Engine Manager")
        
        # ── Scan Wallpapers ──────────────────────────────────────────
        self.wallpapers = self._scan_wallpapers()
        
        # ── Build Context Menu ───────────────────────────────────────
        self.menu = QMenu()
        
        self.pause_action = QAction("Pause/Resume")
        self.pause_action.triggered.connect(self.toggle_pause)
        self.menu.addAction(self.pause_action)
        
        self.next_action = QAction("Next Wallpaper")
        self.next_action.triggered.connect(self.next_wallpaper)
        self.menu.addAction(self.next_action)
        
        self.menu.addSeparator()
        
        # Wallpapers Submenu
        self.wallpapers_menu = QMenu("Wallpapers")
        for wp in self.wallpapers:
            action = QAction(wp['title'], self.wallpapers_menu)
            action.triggered.connect(lambda checked, wp_id=wp['id']: self.watchdog.switch_wallpaper(wp_id))
            self.wallpapers_menu.addAction(action)
        
        self.menu.addMenu(self.wallpapers_menu)
        self.menu.addSeparator()
        
        self.quit_action = QAction("Quit")
        self.quit_action.triggered.connect(self.quit_app)
        self.menu.addAction(self.quit_action)
        
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        
        # ── Python Signal Handling Trick ─────────────────────────────
        # The Qt event loop normally blocks Python's signal handlers from running.
        # By adding a dummy timer that wakes up the Python interpreter every 500ms,
        # we allow signals like SIGTERM (from systemd) to be processed cleanly.
        self.timer = QTimer()
        self.timer.timeout.connect(lambda: None)
        self.timer.start(500)

    def toggle_pause(self):
        """Send SIGSTOP or SIGCONT to the linux-wallpaperengine process."""
        process = self.watchdog.launcher._process
        if not process:
            logger.warning("Cannot pause: wallpaper engine is not running.")
            return
            
        try:
            if self._is_paused:
                os.kill(process.pid, signal.SIGCONT)
                self._is_paused = False
                logger.info("Resumed wallpaper engine")
            else:
                os.kill(process.pid, signal.SIGSTOP)
                self._is_paused = True
                logger.info("Paused wallpaper engine")
        except ProcessLookupError:
            logger.error("Process died before we could pause/resume it.")

    def _scan_wallpapers(self) -> list[dict]:
        """Scan the workshop directory for wallpapers and parse project.json."""
        workshop_dir = Path(self.watchdog.config.workshop_dir)
        if not workshop_dir.is_dir():
            logger.warning("Workshop dir %s not found. Submenu will be empty.", workshop_dir)
            return []
            
        wallpapers = []
        for wp_dir in workshop_dir.iterdir():
            if not wp_dir.is_dir() or not wp_dir.name.isdigit():
                continue
                
            project_json = wp_dir / "project.json"
            title = wp_dir.name # Default to ID
            if project_json.is_file():
                try:
                    data = json.loads(project_json.read_text("utf-8"))
                    title = data.get("title", title)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    logger.warning("Failed to parse %s, using ID as title.", project_json)
                    
            wallpapers.append({"id": wp_dir.name, "title": title})
            
        # Sort alphabetically by title
        wallpapers.sort(key=lambda x: x['title'].lower())
        logger.info("Found %d wallpapers in workshop directory.", len(wallpapers))
        return wallpapers

    def next_wallpaper(self):
        """Cycle through the wallpapers alphabetically."""
        if not self.wallpapers:
            return
            
        current = self.watchdog.config.wallpaper
        # Find index of current wallpaper
        current_idx = -1
        for i, wp in enumerate(self.wallpapers):
            if wp['id'] == current:
                current_idx = i
                break
                
        next_idx = (current_idx + 1) % len(self.wallpapers)
        next_id = self.wallpapers[next_idx]['id']
        logger.info("Next wallpaper triggered: switching to %s", next_id)
        self.watchdog.switch_wallpaper(next_id)
        
    def quit_app(self):
        """Triggered from the tray menu to cleanly shut down."""
        logger.info("Quit requested from tray menu.")
        self.app.quit()

def run_gui(watchdog) -> None:
    """
    Initializes the QApplication and the Tray App, then starts the event loop.
    Blocks until the app quits.
    """
    generate_desktop_file()
    
    import sys
    app = QApplication(sys.argv)
    tray_app = TrayApp(watchdog, app)
    
    # We assign it to a local variable to prevent it from being garbage collected
    # while the event loop is running.
    
    logger.info("Starting PyQt6 event loop...")
    app.exec()
    logger.info("PyQt6 event loop exited.")
