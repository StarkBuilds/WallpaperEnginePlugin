"""
gui.py — Wallpaper Engine Desktop Application (PyQt6).

Full desktop app with:
- Main browser window (wallpaper grid + properties sidebar)
- System tray icon for background access
- Dark theme matching the Windows Wallpaper Engine interface
"""

import json
import logging
import os
import signal
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSystemTrayIcon, QMenu, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QFrame,
    QLabel, QPushButton, QCheckBox, QComboBox, QLineEdit, QSlider,
    QColorDialog, QFormLayout, QSizePolicy, QSplitter, QToolBar,
    QStatusBar,
)
from PyQt6.QtGui import (
    QIcon, QAction, QColor, QPixmap, QFont, QPalette, QMovie,
)
from PyQt6.QtCore import (
    QTimer, Qt, QSize, pyqtSignal,
)

from wallpaper_manager.config import Config, save_config
from wallpaper_manager.watchdog import Watchdog

logger = logging.getLogger(__name__)

def humanize_label(key: str) -> str:
    """Clean up raw property keys into human-readable labels."""
    if key.startswith("ui_browse_properties_"):
        key = key.replace("ui_browse_properties_", "")
    key = key.replace("_", " ").replace("-", " ")
    return " ".join(word.capitalize() for word in key.split())


# ═══════════════════════════════════════════════════════════════════════
# Dark Theme Stylesheet
# ═══════════════════════════════════════════════════════════════════════

DARK_THEME = """
QMainWindow, QWidget {
    background-color: #1b1d20;
    color: #e0e0e0;
    font-family: "Inter", "Segoe UI", sans-serif;
    font-size: 13px;
}
QToolBar {
    background-color: #23272e;
    border-bottom: 1px solid #323842;
    padding: 4px;
    spacing: 6px;
}
QStatusBar {
    background-color: #23272e;
    border-top: 1px solid #323842;
    color: #9aa0a6;
    font-size: 11px;
}
QLineEdit {
    background-color: #323842;
    border: 1px solid #323842;
    border-radius: 4px;
    color: #e0e0e0;
    padding: 6px 10px;
    font-size: 13px;
    selection-background-color: #4a90e2;
}
QLineEdit:focus {
    border: 1px solid #4a90e2;
}
QPushButton {
    background-color: #323842;
    border: 1px solid #323842;
    border-radius: 4px;
    color: #e0e0e0;
    padding: 6px 16px;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #3d4450;
    border-color: #4a90e2;
}
QPushButton:pressed {
    background-color: #4a90e2;
}
QPushButton#applyBtn {
    background-color: #4a90e2;
    border: none;
    font-weight: bold;
    color: white;
    padding: 8px 24px;
}
QPushButton#applyBtn:hover {
    background-color: #00b4d8;
}
QPushButton#cancelBtn {
    background-color: #333;
    border: 1px solid #555;
}
QScrollArea {
    border: none;
    background-color: transparent;
}
QScrollBar:vertical {
    background-color: #23272e;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background-color: #323842;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background-color: #4a90e2;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    height: 0px;
}
QComboBox {
    background-color: #323842;
    border: 1px solid #323842;
    border-radius: 4px;
    color: #e0e0e0;
    padding: 4px 8px;
    min-height: 24px;
}
QComboBox:hover {
    border-color: #4a90e2;
}
QComboBox::drop-down {
    border: none;
    padding-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #23272e;
    color: #e0e0e0;
    border: 1px solid #323842;
    selection-background-color: #4a90e2;
}
QCheckBox {
    color: #e0e0e0;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 3px;
    border: 2px solid #323842;
    background-color: #1b1d20;
}
QCheckBox::indicator:checked {
    background-color: #4a90e2;
    border-color: #4a90e2;
}
QCheckBox::indicator:hover {
    border-color: #4a90e2;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #323842;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #4a90e2;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover {
    background: #00b4d8;
}
QSlider::sub-page:horizontal {
    background: #4a90e2;
    border-radius: 3px;
}
QLabel#sectionLabel {
    color: #9aa0a6;
    font-size: 11px;
    font-weight: bold;
    text-transform: uppercase;
    padding-top: 8px;
    border-top: 1px solid #323842;
    margin-top: 4px;
}
QLabel#titleLabel {
    color: #ffffff;
    font-size: 16px;
    font-weight: bold;
}
QLabel#subtitleLabel {
    color: #9aa0a6;
    font-size: 11px;
}
QMenu {
    background-color: #23272e;
    color: #e0e0e0;
    border: 1px solid #323842;
}
QMenu::item:selected {
    background-color: #4a90e2;
}
"""


# ═══════════════════════════════════════════════════════════════════════
# Wallpaper Thumbnail Card Widget
# ═══════════════════════════════════════════════════════════════════════

class WallpaperCard(QFrame):
    """A clickable thumbnail card representing a single wallpaper."""

    clicked = pyqtSignal(dict)  # Emits the wallpaper info dict

    def __init__(self, wp_info: dict, workshop_dir: str, parent=None):
        super().__init__(parent)
        self.wp_info = wp_info
        self._selected = False

        self.setFixedSize(160, 140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Thumbnail image
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(152, 100)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Try to load preview image
        wp_dir = Path(workshop_dir) / wp_info['id']
        preview_loaded = False
        for ext in ['jpg', 'png', 'gif', 'jpeg']:
            preview_path = wp_dir / f"preview.{ext}"
            if preview_path.exists():
                if ext == 'gif':
                    # For GIF, show just the first frame as static
                    pixmap = QPixmap(str(preview_path))
                    if not pixmap.isNull():
                        self.thumb_label.setPixmap(pixmap.scaled(
                            152, 100,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        ))
                        preview_loaded = True
                else:
                    pixmap = QPixmap(str(preview_path))
                    if not pixmap.isNull():
                        self.thumb_label.setPixmap(pixmap.scaled(
                            152, 100,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        ))
                        preview_loaded = True
                break

        if not preview_loaded:
            self.thumb_label.setText("No Preview")
            self.thumb_label.setStyleSheet(
                "background-color: #0f3460; color: #666; border-radius: 4px; font-size: 11px;"
            )

        layout.addWidget(self.thumb_label)

        # Title
        title_label = QLabel(wp_info.get('title', wp_info['id']))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setWordWrap(True)
        title_label.setMaximumHeight(30)
        title_label.setStyleSheet("color: #ccc; font-size: 11px; background: transparent;")
        layout.addWidget(title_label)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setStyleSheet(
                "WallpaperCard { background-color: #1a1a5e; border: 2px solid #e94560; border-radius: 6px; }"
            )
        else:
            self.setStyleSheet(
                "WallpaperCard { background-color: #16213e; border: 2px solid transparent; border-radius: 6px; }"
                "WallpaperCard:hover { border-color: #0f3460; background-color: #1a1a3e; }"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.wp_info)
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════════════
# Properties Sidebar Widget
# ═══════════════════════════════════════════════════════════════════════

class PropertiesSidebar(QWidget):
    """Right sidebar showing wallpaper details and editable properties."""

    apply_requested = pyqtSignal(str, dict)  # (wp_id, properties_dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setMaximumWidth(380)

        self._current_wp = None
        self._inputs = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Preview image area
        self.preview_label = QLabel()
        self.preview_label.setFixedHeight(200)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background-color: #1b1d20; border-radius: 0; border-bottom: 1px solid #323842;")
        layout.addWidget(self.preview_label)

        # Info area
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(12, 8, 12, 4)
        info_layout.setSpacing(4)

        self.title_label = QLabel("Select a wallpaper")
        self.title_label.setObjectName("titleLabel")
        self.title_label.setWordWrap(True)
        info_layout.addWidget(self.title_label)

        self.type_label = QLabel("")
        self.type_label.setObjectName("subtitleLabel")
        info_layout.addWidget(self.type_label)

        layout.addWidget(info_widget)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #0f3460; max-height: 1px;")
        layout.addWidget(sep)

        # Properties section header
        props_header = QLabel("Properties")
        props_header.setObjectName("sectionLabel")
        props_header.setStyleSheet(
            "color: #888; font-size: 12px; font-weight: bold; padding: 8px 12px 4px 12px; background: transparent; border: none;"
        )
        layout.addWidget(props_header)

        # Scrollable properties form
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent;")

        self.form_widget = QWidget()
        self.form_widget.setStyleSheet("background: transparent;")
        self.form_layout = QFormLayout(self.form_widget)
        self.form_layout.setContentsMargins(12, 4, 12, 12)
        self.form_layout.setSpacing(10)
        self.form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.form_widget)
        layout.addWidget(self.scroll, 1)

        # Buttons bar
        btn_bar = QWidget()
        btn_bar.setStyleSheet("background-color: #16213e;")
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(12, 8, 12, 8)

        btn_layout.addStretch()

        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.setObjectName("cancelBtn")
        # Standard icon for Reset
        self.reset_btn.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_DialogResetButton))
        self.reset_btn.clicked.connect(self._on_reset)
        btn_layout.addWidget(self.reset_btn)

        self.apply_btn = QPushButton("Apply Changes")
        self.apply_btn.setObjectName("applyBtn")
        # Standard icon for Apply
        self.apply_btn.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_DialogApplyButton))
        self.apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(self.apply_btn)

        layout.addWidget(btn_bar)

    def load_wallpaper(self, wp_info: dict, workshop_dir: str, config: Config):
        """Populate the sidebar with a wallpaper's details and properties."""
        self._current_wp = wp_info
        self._inputs = {}
        self._config = config
        wp_id = wp_info['id']

        # Load preview
        wp_dir = Path(workshop_dir) / wp_id
        preview_loaded = False
        for ext in ['jpg', 'png', 'gif', 'jpeg']:
            preview_path = wp_dir / f"preview.{ext}"
            if preview_path.exists():
                pixmap = QPixmap(str(preview_path))
                if not pixmap.isNull():
                    self.preview_label.setPixmap(pixmap.scaled(
                        350, 200,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    ))
                    preview_loaded = True
                break
        if not preview_loaded:
            self.preview_label.clear()
            self.preview_label.setText("No Preview")

        # Set title
        self.title_label.setText(wp_info.get('title', wp_id))

        # Set type info
        project_json = wp_dir / "project.json"
        wp_type = "Unknown"
        properties = {}
        if project_json.is_file():
            try:
                data = json.loads(project_json.read_text("utf-8"))
                wp_type = data.get("type", "unknown").capitalize()
                properties = data.get("general", {}).get("properties", {})
            except Exception as e:
                logger.warning("Failed to parse project.json for %s: %s", wp_id, e)

        self.type_label.setText(f"Type: {wp_type}  •  ID: {wp_id}")

        # Clear old form
        while self.form_layout.rowCount() > 0:
            self.form_layout.removeRow(0)

        if not properties:
            self.form_layout.addRow(QLabel("No configurable properties."))
            return

        # Get user-saved overrides
        saved_props = config.properties.get(wp_id, {})

        # Sort properties by 'order' if available, then by 'index'
        sorted_props = sorted(
            properties.items(),
            key=lambda x: (x[1].get('order', 999), x[1].get('index', 999))
        )

        for key, prop_def in sorted_props:
            prop_type = prop_def.get("type", "textinput")
            raw_label = prop_def.get("text", key)
            if not raw_label or raw_label == key:
                label_text = humanize_label(key)
            else:
                label_text = humanize_label(raw_label) if "ui_browse_properties" in raw_label else raw_label
            default_val = prop_def.get("value", "")

            # User override takes priority
            current_val = saved_props.get(key, default_val)

            widget = None

            if prop_type == "bool":
                widget = QCheckBox()
                is_checked = str(current_val).strip().lower() in ("1", "true")
                widget.setChecked(is_checked)
                self._inputs[key] = lambda w=widget: "1" if w.isChecked() else "0"

            elif prop_type == "color":
                widget = QPushButton()
                try:
                    parts = [float(x.strip()) for x in str(current_val).replace(',', ' ').split() if x.strip()]
                    r = int(min(1.0, parts[0]) * 255) if len(parts) > 0 else 0
                    g = int(min(1.0, parts[1]) * 255) if len(parts) > 1 else 0
                    b = int(min(1.0, parts[2]) * 255) if len(parts) > 2 else 0
                    color = QColor(r, g, b)
                except Exception:
                    color = QColor(255, 255, 255)

                self._update_color_btn(widget, color)
                we_str = str(current_val)
                widget.setProperty("we_color", we_str)
                widget.clicked.connect(
                    lambda checked, btn=widget, c=color: self._pick_color(btn, c)
                )
                self._inputs[key] = lambda w=widget: str(w.property("we_color") or "")

            elif prop_type == "combo":
                widget = QComboBox()
                options = prop_def.get("options", [])
                for opt in options:
                    widget.addItem(opt.get("label", ""), opt.get("value", ""))
                idx = widget.findData(str(current_val))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
                self._inputs[key] = lambda w=widget: w.currentData()

            elif prop_type == "slider":
                container = QWidget()
                container.setStyleSheet("background: transparent;")
                h_layout = QHBoxLayout(container)
                h_layout.setContentsMargins(0, 0, 0, 0)
                h_layout.setSpacing(8)

                slider = QSlider(Qt.Orientation.Horizontal)
                slider_min = prop_def.get("min", 0)
                slider_max = prop_def.get("max", 100)
                precision = prop_def.get("precision", 0)
                is_integer = prop_def.get("integer", precision == 0)

                if is_integer:
                    slider.setRange(int(slider_min), int(slider_max))
                    try:
                        slider.setValue(int(float(current_val)))
                    except (ValueError, TypeError):
                        slider.setValue(int(slider_min))
                else:
                    # Scale float range to integer range for QSlider
                    scale = 10 ** max(precision, 2)
                    slider.setRange(int(slider_min * scale), int(slider_max * scale))
                    try:
                        slider.setValue(int(float(current_val) * scale))
                    except (ValueError, TypeError):
                        slider.setValue(int(slider_min * scale))

                val_label = QLabel(str(current_val))
                val_label.setMinimumWidth(40)
                val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                val_label.setStyleSheet("color: #aaa; font-size: 12px; background: transparent;")

                if is_integer:
                    slider.valueChanged.connect(lambda v, lbl=val_label: lbl.setText(str(v)))
                    self._inputs[key] = lambda w=slider: str(w.value())
                else:
                    scale_val = 10 ** max(precision, 2)
                    slider.valueChanged.connect(
                        lambda v, lbl=val_label, s=scale_val: lbl.setText(f"{v/s:.{max(precision,2)}f}")
                    )
                    self._inputs[key] = lambda w=slider, s=scale_val: f"{w.value()/s}"

                h_layout.addWidget(slider, 1)
                h_layout.addWidget(val_label)
                widget = container

            elif prop_type == "textinput":
                widget = QLineEdit()
                widget.setText(str(current_val))
                self._inputs[key] = lambda w=widget: w.text()

            else:
                # Fallback to text input
                widget = QLineEdit()
                widget.setText(str(current_val))
                self._inputs[key] = lambda w=widget: w.text()

            if widget:
                label = QLabel(label_text)
                label.setStyleSheet("color: #ccc; background: transparent;")
                self.form_layout.addRow(label, widget)

        # Separator for engine properties
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #323842; max-height: 1px;")
        self.form_layout.addRow(sep)

        # ── Playback Rate Slider ──
        # Not a project.json property, but a global engine argument mapped to --set-property rate=
        rate_container = QWidget()
        rate_container.setStyleSheet("background: transparent;")
        rate_layout = QHBoxLayout(rate_container)
        rate_layout.setContentsMargins(0, 0, 0, 0)
        rate_layout.setSpacing(8)

        rate_slider = QSlider(Qt.Orientation.Horizontal)
        rate_slider.setRange(10, 200) # 10% to 200%
        current_rate = float(saved_props.get("rate", 1.0)) * 100
        rate_slider.setValue(int(current_rate))

        rate_label_val = QLabel(f"{int(current_rate)}%")
        rate_label_val.setMinimumWidth(40)
        rate_label_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        rate_label_val.setStyleSheet("color: #aaa; font-size: 12px; background: transparent;")
        rate_slider.valueChanged.connect(lambda v, lbl=rate_label_val: lbl.setText(f"{v}%"))
        
        self._inputs["rate"] = lambda w=rate_slider: f"{w.value()/100.0:.2f}"

        rate_layout.addWidget(rate_slider, 1)
        rate_layout.addWidget(rate_label_val)

        label_rate = QLabel("Playback Rate")
        label_rate.setStyleSheet("color: #ccc; background: transparent;")
        self.form_layout.addRow(label_rate, rate_container)

        # ── FPS Limit Slider ──
        # Global engine property mapped to --fps
        fps_container = QWidget()
        fps_container.setStyleSheet("background: transparent;")
        fps_layout = QHBoxLayout(fps_container)
        fps_layout.setContentsMargins(0, 0, 0, 0)
        fps_layout.setSpacing(8)

        fps_slider = QSlider(Qt.Orientation.Horizontal)
        fps_slider.setRange(15, 144)
        fps_slider.setValue(config.fps)

        fps_label_val = QLabel(f"{config.fps} FPS")
        fps_label_val.setMinimumWidth(40)
        fps_label_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        fps_label_val.setStyleSheet("color: #aaa; font-size: 12px; background: transparent;")
        fps_slider.valueChanged.connect(lambda v, lbl=fps_label_val: lbl.setText(f"{v} FPS"))
        
        self._fps_input = lambda w=fps_slider: w.value()

        fps_layout.addWidget(fps_slider, 1)
        fps_layout.addWidget(fps_label_val)

        label_fps = QLabel("FPS Limit")
        label_fps.setStyleSheet("color: #ccc; background: transparent;")
        self.form_layout.addRow(label_fps, fps_container)

    def _update_color_btn(self, btn: QPushButton, color: QColor):
        # Match Steam square color picker style
        btn.setFixedSize(32, 24)
        btn.setStyleSheet(
            f"background-color: {color.name()}; border: 1px solid #777; border-radius: 2px;"
        )

    def _pick_color(self, btn: QPushButton, initial: QColor):
        color = QColorDialog.getColor(initial, self, "Pick Color")
        if color.isValid():
            self._update_color_btn(btn, color)
            we_format = f"{color.redF():.5f} {color.greenF():.5f} {color.blueF():.5f}"
            btn.setProperty("we_color", we_format)

    def _on_apply(self):
        if not self._current_wp:
            return
        wp_id = self._current_wp['id']
        props = {}
        for key, getter in self._inputs.items():
            try:
                props[key] = str(getter())
            except Exception as e:
                logger.warning("Failed to read property %s: %s", key, e)
        
        # Save FPS
        if hasattr(self, '_fps_input'):
            try:
                self._config.fps = self._fps_input()
            except Exception as e:
                logger.warning("Failed to read FPS limit: %s", e)

        self.apply_requested.emit(wp_id, props)

    def _on_reset(self):
        """Reset properties to defaults (clear overrides)."""
        if not self._current_wp or not hasattr(self, '_config'):
            return
        
        wp_id = self._current_wp['id']
        if wp_id in self._config.properties:
            del self._config.properties[wp_id]
        
        # Reset FPS to 30 as a standard default
        self._config.fps = 30
        
        # Reload the UI with defaults
        workshop_dir = self._config.workshop_dir
        self.load_wallpaper(self._current_wp, workshop_dir, self._config)


# ═══════════════════════════════════════════════════════════════════════
# Main Browser Window
# ═══════════════════════════════════════════════════════════════════════

class WallpaperBrowserWindow(QMainWindow):
    """Main application window — wallpaper grid browser with properties sidebar."""

    def __init__(self, watchdog: Watchdog, parent=None):
        super().__init__(parent)
        self.watchdog = watchdog
        self.config = watchdog.config
        self._wallpapers = []
        self._cards = []
        self._current_card = None

        self.setWindowTitle("Wallpaper Engine — Linux")
        self.setMinimumSize(900, 600)
        self.resize(1100, 700)
        self.setStyleSheet(DARK_THEME)

        # Window icon
        project_root = Path(__file__).parent.parent
        icon_path = project_root / "icon.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # ── Toolbar ──────────────────────────────────────────────────
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(toolbar)

        # Search
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Search wallpapers...")
        self.search_input.setFixedWidth(250)
        self.search_input.textChanged.connect(self._filter_wallpapers)
        toolbar.addWidget(self.search_input)

        toolbar.addSeparator()

        # Sort dropdown
        sort_label = QLabel("  Sort: ")
        sort_label.setStyleSheet("color: #888; background: transparent;")
        toolbar.addWidget(sort_label)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Name", "ID", "Type"])
        self.sort_combo.setFixedWidth(100)
        self.sort_combo.currentTextChanged.connect(self._sort_wallpapers)
        toolbar.addWidget(self.sort_combo)

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        toolbar.addWidget(spacer)

        # Wallpaper count
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: #666; font-size: 12px; background: transparent; padding-right: 8px;")
        toolbar.addWidget(self.count_label)

        # ── Central Widget (Splitter) ────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background-color: #0f3460; width: 1px; }")

        # Left panel: wallpaper grid
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(8)
        self.grid_layout.setContentsMargins(8, 8, 8, 8)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.grid_scroll.setWidget(self.grid_container)
        left_layout.addWidget(self.grid_scroll)

        splitter.addWidget(left_panel)

        # Right panel: properties sidebar
        self.sidebar = PropertiesSidebar()
        self.sidebar.apply_requested.connect(self._on_apply_properties)
        splitter.addWidget(self.sidebar)

        # Set initial splitter sizes (70/30)
        splitter.setSizes([700, 350])

        self.setCentralWidget(splitter)

        # ── Status Bar ───────────────────────────────────────────────
        self.statusBar().showMessage("Ready")

        # ── Load Wallpapers ──────────────────────────────────────────
        self._scan_and_populate()

    def _scan_wallpapers(self) -> list[dict]:
        """Scan the workshop directory for wallpapers and parse project.json."""
        workshop_dir = Path(self.config.workshop_dir)
        if not workshop_dir.is_dir():
            logger.warning("Workshop dir %s not found.", workshop_dir)
            return []

        wallpapers = []
        for wp_dir in workshop_dir.iterdir():
            if not wp_dir.is_dir() or not wp_dir.name.isdigit():
                continue

            project_json = wp_dir / "project.json"
            wp_info = {
                'id': wp_dir.name,
                'title': wp_dir.name,
                'type': 'unknown',
            }

            if project_json.is_file():
                try:
                    data = json.loads(project_json.read_text("utf-8"))
                    wp_info['title'] = data.get("title", wp_info['title'])
                    wp_info['type'] = data.get("type", "unknown")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            wallpapers.append(wp_info)

        wallpapers.sort(key=lambda x: x['title'].lower())
        logger.info("Found %d wallpapers.", len(wallpapers))
        return wallpapers

    def _scan_and_populate(self):
        """Scan wallpapers and populate the grid."""
        self._wallpapers = self._scan_wallpapers()
        self._populate_grid(self._wallpapers)
        self.count_label.setText(f"{len(self._wallpapers)} wallpapers")

    def _populate_grid(self, wallpapers: list[dict]):
        """Fill the grid with wallpaper cards."""
        # Clear existing
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards = []

        cols = max(1, (self.grid_scroll.viewport().width() - 24) // 168)

        for i, wp in enumerate(wallpapers):
            card = WallpaperCard(wp, self.config.workshop_dir)
            card.clicked.connect(self._on_card_clicked)

            # Highlight currently active wallpaper
            if wp['id'] == self.config.wallpaper:
                card.set_selected(True)
                self._current_card = card

            row = i // cols
            col = i % cols
            self.grid_layout.addWidget(card, row, col)
            self._cards.append(card)

    def _on_card_clicked(self, wp_info: dict):
        """Handle wallpaper card click — select it and load properties."""
        # Deselect previous
        if self._current_card:
            self._current_card.set_selected(False)

        # Select new
        for card in self._cards:
            if card.wp_info['id'] == wp_info['id']:
                card.set_selected(True)
                self._current_card = card
                break

        # Load properties in sidebar
        self.sidebar.load_wallpaper(wp_info, self.config.workshop_dir, self.config)

        # Switch the wallpaper immediately
        if wp_info['id'] != self.config.wallpaper:
            self.statusBar().showMessage(f"Switching to: {wp_info.get('title', wp_info['id'])}...")
            self.watchdog.switch_wallpaper(wp_info['id'])
            self.statusBar().showMessage(
                f"Active: {wp_info.get('title', wp_info['id'])}", 5000
            )

    def _on_apply_properties(self, wp_id: str, props: dict):
        """Handle Apply button from the properties sidebar."""
        logger.info("Applying properties for %s: %s", wp_id, props)

        if wp_id not in self.config.properties:
            self.config.properties[wp_id] = {}
        self.config.properties[wp_id].update(props)

        # Save to disk
        save_config(self.config)

        # Restart wallpaper with new properties
        self.statusBar().showMessage("Applying properties...")
        self.watchdog.switch_wallpaper(wp_id)
        self.statusBar().showMessage("Properties applied!", 5000)

    def _filter_wallpapers(self, text: str):
        """Filter wallpaper grid by search text."""
        text = text.strip().lower()
        if not text:
            filtered = self._wallpapers
        else:
            filtered = [
                wp for wp in self._wallpapers
                if text in wp.get('title', '').lower() or text in wp['id']
            ]
        self._populate_grid(filtered)

    def _sort_wallpapers(self, sort_by: str):
        """Sort wallpapers by the given criteria."""
        if sort_by == "Name":
            self._wallpapers.sort(key=lambda x: x['title'].lower())
        elif sort_by == "ID":
            self._wallpapers.sort(key=lambda x: int(x['id']))
        elif sort_by == "Type":
            self._wallpapers.sort(key=lambda x: x.get('type', '').lower())
        self._populate_grid(self._wallpapers)

    def resizeEvent(self, event):
        """Reflow grid on resize."""
        super().resizeEvent(event)
        if self._cards:
            self._populate_grid(
                [c.wp_info for c in self._cards]
                if not self.search_input.text().strip()
                else [
                    wp for wp in self._wallpapers
                    if self.search_input.text().strip().lower() in wp.get('title', '').lower()
                    or self.search_input.text().strip().lower() in wp['id']
                ]
            )

    def closeEvent(self, event):
        """Hide to tray instead of quitting."""
        event.ignore()
        self.hide()


# ═══════════════════════════════════════════════════════════════════════
# System Tray Icon
# ═══════════════════════════════════════════════════════════════════════

class TrayApp:
    """System tray icon with context menu for quick actions."""

    def __init__(self, watchdog: Watchdog, app: QApplication, browser_window: WallpaperBrowserWindow):
        self.watchdog = watchdog
        self.app = app
        self.browser = browser_window
        self._is_paused = False

        self.app.setDesktopFileName("wallpaper-manager")
        self.app.setQuitOnLastWindowClosed(False)

        # Load icon
        project_root = Path(__file__).parent.parent
        icon_path = project_root / "icon.svg"
        if icon_path.exists():
            self.icon = QIcon(str(icon_path))
            self.app.setWindowIcon(self.icon)
        else:
            self.icon = QIcon.fromTheme("video-display")

        # Tray icon
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(self.icon)
        self.tray.setToolTip("Wallpaper Engine Manager")
        self.tray.activated.connect(self._on_tray_activated)

        # Context menu
        self.menu = QMenu()

        open_action = QAction("Open Browser", self.menu)
        open_action.triggered.connect(self._show_browser)
        self.menu.addAction(open_action)

        self.menu.addSeparator()

        self.auto_start_action = QAction("Auto-play on Drive Mount", checkable=True)
        self.auto_start_action.setChecked(self.watchdog.config.auto_start_on_mount)
        self.auto_start_action.triggered.connect(self._toggle_auto_start)
        self.menu.addAction(self.auto_start_action)

        self.menu.addSeparator()

        self.pause_action = QAction("Pause/Resume")
        self.pause_action.triggered.connect(self.toggle_pause)
        self.menu.addAction(self.pause_action)

        self.next_action = QAction("Next Wallpaper")
        self.next_action.triggered.connect(self.next_wallpaper)
        self.menu.addAction(self.next_action)

        self.menu.addSeparator()

        self.quit_action = QAction("Quit")
        self.quit_action.triggered.connect(self.quit_app)
        self.menu.addAction(self.quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.show()

        # Python signal handling timer
        self.timer = QTimer()
        self.timer.timeout.connect(lambda: None)
        self.timer.start(500)

        # Scan wallpapers list for cycling
        self._wallpapers = browser_window._wallpapers

    def _on_tray_activated(self, reason):
        """Show browser window on tray icon click."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_browser()

    def _show_browser(self):
        self.browser.show()
        self.browser.raise_()
        self.browser.activateWindow()

    def _toggle_auto_start(self, checked: bool):
        self.watchdog.config.auto_start_on_mount = checked
        save_config(self.watchdog.config)
        logger.info("Auto-start on mount set to %s", checked)

    def toggle_pause(self):
        proc = self.watchdog.launcher._process
        if proc is None:
            return
        try:
            if self._is_paused:
                os.kill(proc.pid, signal.SIGCONT)
                self._is_paused = False
            else:
                os.kill(proc.pid, signal.SIGSTOP)
                self._is_paused = True
        except ProcessLookupError:
            pass

    def next_wallpaper(self):
        """Cycle to the next wallpaper (alphabetically)."""
        if not self._wallpapers:
            return

        current = self.watchdog.config.wallpaper
        current_idx = -1
        for i, wp in enumerate(self._wallpapers):
            if wp['id'] == current:
                current_idx = i
                break

        next_idx = (current_idx + 1) % len(self._wallpapers)
        next_wp = self._wallpapers[next_idx]
        logger.info("Next wallpaper: %s", next_wp['id'])
        self.watchdog.switch_wallpaper(next_wp['id'])

    def quit_app(self):
        logger.info("Quit from tray.")
        self.app.quit()


# ═══════════════════════════════════════════════════════════════════════
# Desktop File Generator
# ═══════════════════════════════════════════════════════════════════════

def generate_desktop_file() -> None:
    """Generate .desktop file for KDE app launcher integration."""
    desktop_dir = Path.home() / ".local" / "share" / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = desktop_dir / "wallpaper-manager.desktop"

    content = """[Desktop Entry]
Name=Wallpaper Manager
Exec=/usr/bin/python3 -m wallpaper_manager.main
Icon=wallpaper-manager
Type=Application
Terminal=false
Categories=Utility;
"""
    desktop_file.write_text(content)
    logger.info("Desktop file at %s", desktop_file)


# ═══════════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════════

def run_gui(watchdog: Watchdog) -> None:
    """Initialize and run the PyQt6 application."""
    generate_desktop_file()

    import sys
    app = QApplication(sys.argv)

    # Create the main browser window
    browser = WallpaperBrowserWindow(watchdog)
    browser.show()

    # Create the tray icon
    _tray = TrayApp(watchdog, app, browser)

    logger.info("Starting PyQt6 event loop (GUI mode)...")
    app.exec()
    logger.info("PyQt6 event loop exited.")
