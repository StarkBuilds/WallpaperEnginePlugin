"""
gui.py — System Tray and GUI using PyQt6 (Stage 5, Phase 1).

This module handles the tray icon, the context menu, and the Wayland integration.
"""

import json
import logging
import os
import signal
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QComboBox, QLineEdit, QColorDialog, 
    QScrollArea, QWidget, QFormLayout
)
from PyQt6.QtGui import QIcon, QAction, QColor
from PyQt6.QtCore import QTimer, Qt

from wallpaper_manager.config import Config, save_config
from wallpaper_manager.watchdog import Watchdog

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
        
        self.config_action = QAction("Configure Wallpaper...")
        self.config_action.triggered.connect(self.open_config)
        self.menu.addAction(self.config_action)
        
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
        proc = self.watchdog.launcher._process
        if proc is None:
            logger.warning("Cannot pause: wallpaper engine is not running.")
            return
            
        try:
            if self._is_paused:
                os.kill(proc.pid, signal.SIGCONT)
                self._is_paused = False
                logger.info("Resumed wallpaper engine")
            else:
                os.kill(proc.pid, signal.SIGSTOP)
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
        
    def open_config(self):
        """Open the configuration window for the current wallpaper."""
        wp_id = self.watchdog.config.wallpaper
        if not wp_id:
            logger.warning("No wallpaper currently selected to configure.")
            return
        
        logger.info("[ConfigUI] Opening config for wallpaper ID: %s", wp_id)
        logger.info("[ConfigUI] Current config.properties keys: %s", 
                     list(self.watchdog.config.properties.keys()))
            
        dialog = ConfigDialog(wp_id, self.watchdog.config, None)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Log what was saved
            saved_props = self.watchdog.config.properties.get(wp_id, {})
            logger.info("[ConfigUI] Properties saved for %s: %s", wp_id, saved_props)
            
            # Save the new properties to disk
            save_config(self.watchdog.config)
            
            # Restart with the SAME wallpaper ID that was configured
            logger.info("[ConfigUI] Restarting wallpaper %s with new properties", wp_id)
            self.watchdog.switch_wallpaper(wp_id)
        else:
            logger.info("[ConfigUI] Config dialog cancelled for %s", wp_id)

    def quit_app(self):
        """Triggered from the tray menu to cleanly shut down."""
        logger.info("Quit requested from tray menu.")
        self.app.quit()


class ConfigDialog(QDialog):
    """Dynamic properties configuration window."""
    def __init__(self, wp_id: str, config: Config, parent=None):
        super().__init__(parent)
        self.wp_id = wp_id
        self.config = config
        self.setWindowTitle("Configure Wallpaper")
        self.resize(400, 500)
        
        # Make it stay on top so it doesn't get lost
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        
        self.main_layout = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.main_layout.addWidget(self.scroll)
        
        self.content_widget = QWidget()
        self.form_layout = QFormLayout(self.content_widget)
        self.scroll.setWidget(self.content_widget)
        
        self.inputs = {}
        
        self._load_properties()
        
        # Buttons
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Apply")
        save_btn.clicked.connect(self._save_and_accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        self.main_layout.addLayout(btn_layout)

    def _load_properties(self):
        """
        Load wallpaper properties using `--list-properties` as the primary source
        of truth. Falls back to project.json for combo option labels and ordering.
        """
        import subprocess as sp
        import re
        
        wp_dir = Path(self.config.workshop_dir) / self.wp_id
        
        # ── Step 1: Run --list-properties to get the real property keys/types ──
        cmd = [self.config.binary]
        if self.config.assets_dir:
            cmd.extend(["--assets-dir", self.config.assets_dir])
        cmd.extend(["--list-properties", str(wp_dir)])
        
        logger.info("[ConfigUI] Running: %s", " ".join(cmd))
        
        try:
            result = sp.run(cmd, capture_output=True, text=True, timeout=5)
            raw_output = result.stdout
        except Exception as e:
            logger.error("[ConfigUI] --list-properties failed: %s", e)
            self.form_layout.addRow(QLabel(f"Failed to query properties: {e}"))
            return
        
        if not raw_output.strip():
            self.form_layout.addRow(QLabel("No properties available for this wallpaper."))
            return
        
        # ── Step 2: Parse the --list-properties output ───────────────
        props = {}
        current_key = None
        
        for line in raw_output.splitlines():
            if line.startswith("Running with:") or line.startswith("Using wallpaper"):
                continue
                
            header_match = re.match(r'^(\w+)\s*-\s*(\w+)', line)
            if header_match:
                current_key = header_match.group(1)
                prop_type = header_match.group(2)
                props[current_key] = {"type": prop_type, "text": current_key, "value": "", "options": []}
                continue
            
            if current_key is None:
                continue
                
            stripped = line.strip()
            
            if stripped.startswith("Text:"):
                props[current_key]["text"] = stripped[len("Text:"):].strip()
            elif stripped.startswith("Value:"):
                val_str = stripped[len("Value:"):].strip()
                props[current_key]["value"] = val_str
            elif "=" in stripped and props[current_key]["type"] == "combo":
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    opt_val = parts[0].strip()
                    opt_label = parts[1].strip()
                    props[current_key]["options"].append({"value": opt_val, "label": opt_label})
        
        logger.info("[ConfigUI] Parsed %d properties from --list-properties", len(props))
        
        # ── Step 3: Get user's saved overrides ───────────────────────
        saved_props = self.config.properties.get(self.wp_id, {})
        
        # ── Step 4: Build the UI widgets ─────────────────────────────
        for key, prop in props.items():
            prop_type = prop["type"]
            text = prop["text"]
            default_val = prop["value"]
            
            current_val = saved_props.get(key, default_val)
            widget = None
            
            if prop_type == "boolean":
                widget = QCheckBox()
                is_checked = str(current_val).strip() in ("1", "true", "True")
                widget.setChecked(is_checked)
                self.inputs[key] = lambda w=widget: "1" if w.isChecked() else "0"
                
            elif prop_type == "textinput":
                widget = QLineEdit()
                widget.setText(str(current_val))
                self.inputs[key] = lambda w=widget: w.text()
                
            elif prop_type == "combo":
                widget = QComboBox()
                for opt in prop.get("options", []):
                    widget.addItem(opt.get("label", ""), opt.get("value", ""))
                idx = widget.findData(str(current_val))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
                self.inputs[key] = lambda w=widget: w.currentData()
                
            elif prop_type == "color":
                widget = QPushButton()
                try:
                    parts = [float(x.strip().rstrip(',')) for x in str(current_val).replace(',', ' ').split()]
                    r = int(parts[0] * 255) if len(parts) > 0 else 0
                    g = int(parts[1] * 255) if len(parts) > 1 else 0
                    b = int(parts[2] * 255) if len(parts) > 2 else 0
                    color = QColor(r, g, b)
                except Exception:
                    color = QColor(255, 255, 255)
                    
                self._update_color_btn(widget, color)
                
                try:
                    parts = [float(x.strip().rstrip(',')) for x in str(current_val).replace(',', ' ').split()]
                    we_str = " ".join(f"{p:.5f}" for p in parts[:3])
                except Exception:
                    we_str = str(current_val)
                
                widget.setProperty("we_color", we_str)
                widget.clicked.connect(lambda checked, btn=widget, c=color: self._pick_color(btn, c))
                self.inputs[key] = lambda w=widget: str(w.property("we_color") or "")
                
            else:
                widget = QLineEdit()
                widget.setText(str(current_val))
                self.inputs[key] = lambda w=widget: w.text()
                
            if widget:
                self.form_layout.addRow(text, widget)

    def _update_color_btn(self, btn: QPushButton, color: QColor):
        """Update the button's background color."""
        btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #555; border-radius: 4px; min-height: 24px;")

    def _pick_color(self, btn: QPushButton, initial: QColor):
        color = QColorDialog.getColor(initial, self, "Pick Color")
        if color.isValid():
            self._update_color_btn(btn, color)
            we_format = f"{color.redF():.5f} {color.greenF():.5f} {color.blueF():.5f}"
            btn.setProperty("we_color", we_format)
            
    def _save_and_accept(self):
        """Read all inputs and save to config dict."""
        props = {}
        for key, getter in self.inputs.items():
            val = getter()
            props[key] = str(val)
            
        logger.info("[ConfigUI] Saving properties for %s: %s", self.wp_id, props)
            
        if self.wp_id not in self.config.properties:
            self.config.properties[self.wp_id] = {}
            
        self.config.properties[self.wp_id].update(props)
        self.accept()


def run_gui(watchdog: Watchdog) -> None:
    """
    Initializes the QApplication and the Tray App, then starts the event loop.
    Blocks until the app quits.
    """
    generate_desktop_file()
    
    import sys
    app = QApplication(sys.argv)
    _tray_app = TrayApp(watchdog, app)
    
    logger.info("Starting PyQt6 event loop...")
    app.exec()
    logger.info("PyQt6 event loop exited.")
