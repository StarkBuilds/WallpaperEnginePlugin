"""
config.py — Load and validate the wallpaper manager configuration.

Uses Python's built-in `tomllib` module (available since Python 3.11) to parse
TOML config files.  TOML is like a cleaner version of .properties / YAML —
it supports comments, typed values, and nested sections.

The config file lives at: ~/.config/wallpaper_manager.toml

This module provides a `Config` dataclass that holds all settings with sensible
defaults, so the user only needs to specify what they want to change.
"""

import logging
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Default paths ────────────────────────────────────────────────────────────
# XDG_CONFIG_HOME is a Linux standard for where config files live.
# It's almost always ~/.config/ — think of it as Linux's equivalent of
# Windows %APPDATA% or macOS ~/Library/Preferences/.
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_FILE = CONFIG_DIR / "wallpaper_manager.toml"

# XDG_DATA_HOME is where application data (logs, state) lives.
# Usually ~/.local/share/ — like a per-user /var/lib/.
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
APP_DATA_DIR = DATA_DIR / "wallpaper_manager"
LOG_FILE = APP_DATA_DIR / "wallpaper_manager.log"


@dataclass
class DriveConfig:
    """Settings for the NTFS drive that holds Workshop content."""

    uuid: str = ""
    """Partition UUID — a stable identifier that doesn't change between boots,
    unlike device paths like /dev/nvme0n1p3 which can shift."""


@dataclass
class ResilienceConfig:
    """Settings for crash-recovery behavior (Stage 3)."""

    max_retries: int = 5
    """How many times to restart after consecutive crashes before giving up."""

    retry_delay_seconds: int = 3
    """Seconds to wait between restart attempts."""


@dataclass
class Config:
    """
    All wallpaper manager settings.

    This is a 'dataclass' — Python's equivalent of a Java record or a Lombok
    @Data class.  It auto-generates __init__, __repr__, __eq__ etc. from
    the field declarations below.
    """

    binary: str = "linux-wallpaperengine"
    """Path to the linux-wallpaperengine executable.
    If it's on your $PATH (which it will be after `yay -S`), just the name works.
    Otherwise, provide an absolute path like /usr/bin/linux-wallpaperengine."""

    assets_dir: str = ""
    """Path to the Wallpaper Engine assets folder.
    linux-wallpaperengine needs this to render wallpapers (shaders, textures, etc.).
    If left empty, resolve_paths() will try to find it automatically.
    e.g. /run/media/arghadeep/Windows/Program Files (x86)/Steam/steamapps/common/wallpaper_engine/assets"""

    workshop_dir: str = ""
    """Full path to the Workshop content folder on the mounted drive.
    If left empty, resolve_paths() will try to find it automatically.
    e.g. /run/media/arghadeep/Windows/Program Files (x86)/Steam/steamapps/workshop/content/431960"""

    wallpaper: str = ""
    """Which wallpaper to load.  Can be:
    - A numeric Workshop ID like "1845706469" (resolved relative to workshop_dir)
    - An absolute path to a wallpaper folder"""

    scaling: str = ""
    """Scaling mode passed to linux-wallpaperengine.
    Options: "fill", "stretch", "fit", "default", or "" (let the tool decide)."""

    screen: str = ""
    """Which screen/monitor to target (e.g. "HDMI-A-1", "DP-1").
    Leave empty to apply to all screens."""

    drive: DriveConfig = field(default_factory=DriveConfig)
    """NTFS drive mounting configuration."""

    resilience: ResilienceConfig = field(default_factory=ResilienceConfig)
    """Crash resilience configuration."""


def load_config(path: Path = CONFIG_FILE) -> Config:
    """
    Load configuration from a TOML file on disk.

    If the file doesn't exist, returns a Config with all defaults.
    Missing keys in the file are filled with defaults too — so you only
    need to specify what you want to override.

    Parameters
    ----------
    path : Path
        Path to the TOML file.  Defaults to ~/.config/wallpaper_manager.toml

    Returns
    -------
    Config
        The fully populated configuration object.
    """
    if not path.exists():
        print(
            f"[config] No config file found at {path} — using defaults.",
            file=sys.stderr,
        )
        print(
            f"[config] Copy the example config to {path} and edit it.",
            file=sys.stderr,
        )
        return Config()

    # tomllib.load() requires a file opened in binary mode ("rb").
    # This is a Python quirk — TOML files are UTF-8 text, but the parser
    # wants raw bytes so it can handle encoding itself.
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    # Build the Config from the parsed TOML dict.
    # We use .get(key, default) everywhere so missing keys don't crash.
    drive_raw = raw.get("drive", {})
    resilience_raw = raw.get("resilience", {})

    config = Config(
        binary=raw.get("binary", Config.binary),
        assets_dir=raw.get("assets_dir", Config.assets_dir),
        workshop_dir=raw.get("workshop_dir", Config.workshop_dir),
        wallpaper=raw.get("wallpaper", Config.wallpaper),
        scaling=raw.get("scaling", Config.scaling),
        screen=raw.get("screen", Config.screen),
        drive=DriveConfig(
            uuid=drive_raw.get("uuid", DriveConfig.uuid),
        ),
        resilience=ResilienceConfig(
            max_retries=resilience_raw.get(
                "max_retries", ResilienceConfig.max_retries
            ),
            retry_delay_seconds=resilience_raw.get(
                "retry_delay_seconds", ResilienceConfig.retry_delay_seconds
            ),
        ),
    )

    # Auto-detect any paths the user didn't explicitly set
    resolve_paths(config)

    return config


def validate_config(config: Config) -> list[str]:
    """
    Check the config for problems.  Returns a list of error messages.
    An empty list means everything looks good.

    We don't hard-fail here — the caller decides what's fatal.
    """
    errors: list[str] = []

    if not config.wallpaper:
        errors.append("'wallpaper' is required — set it to a Workshop ID or path.")

    if not config.workshop_dir:
        errors.append(
            "'workshop_dir' could not be found automatically. "
            "Set it explicitly in your TOML config to the path of your "
            "431960/ workshop folder."
        )

    if not config.assets_dir:
        errors.append(
            "'assets_dir' could not be found automatically. "
            "Set it explicitly in your TOML config to the path of your "
            "wallpaper_engine/assets folder."
        )
    elif not Path(config.assets_dir).is_dir():
        errors.append(
            f"'assets_dir' path does not exist: {config.assets_dir}"
        )

    # Check if the binary exists on $PATH or as an absolute path
    if config.binary:
        binary_path = Path(config.binary)
        if binary_path.is_absolute() and not binary_path.exists():
            errors.append(
                f"Binary not found at '{config.binary}'. "
                f"Is linux-wallpaperengine installed?"
            )

    return errors


# ── Smart Path Auto-Detection ────────────────────────────────────────────────
#
# The idea: instead of forcing the user to manually hunt down and type out
# long paths like "/run/media/arghadeep/Windows/Program Files (x86)/Steam/...",
# we scan well-known locations where Steam installs things.
#
# Steam on Linux can live in several places:
#   1. Native install:     ~/.local/share/Steam/
#   2. Flatpak install:    ~/.var/app/com.valvesoftware.Steam/data/Steam/
#   3. Mounted drives:     /run/media/<user>/<label>/  (e.g. your Windows NTFS)
#
# Within any Steam library folder, the structure is always:
#   steamapps/
#     common/wallpaper_engine/assets/     ← assets_dir
#     workshop/content/431960/            ← workshop_dir
#
# This is like Spring Boot's auto-configuration: convention over configuration,
# with explicit config as an override.


# Relative paths within a Steam library folder
_ASSETS_REL = Path("steamapps") / "common" / "wallpaper_engine" / "assets"
_WORKSHOP_REL = Path("steamapps") / "workshop" / "content" / "431960"


def _get_candidate_steam_roots() -> list[Path]:
    """
    Build a list of directories that might contain a Steam installation.

    We check (in priority order):
    1. Native Linux Steam install (~/.local/share/Steam/)
    2. Flatpak Steam install (~/.var/app/.../Steam/)
    3. Every mounted volume under /run/media/<current_user>/
       — this catches NTFS drives, USB sticks, etc.
       — each mounted volume might have Steam at its root or in
         'Program Files (x86)/Steam/' (Windows default)
    """
    candidates: list[Path] = []
    home = Path.home()

    # 1. Native Steam
    candidates.append(home / ".local" / "share" / "Steam")

    # 2. Flatpak Steam
    candidates.append(
        home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam"
    )

    # 3. Mounted volumes under /run/media/<user>/
    #    /run/media/ is where udisksctl and KDE auto-mount external drives.
    #    Each mount point is /run/media/<username>/<drive_label>/
    user = os.environ.get("USER", "")
    media_root = Path("/run/media") / user

    if media_root.is_dir():
        try:
            for mount_point in sorted(media_root.iterdir()):
                if not mount_point.is_dir():
                    continue
                # Steam could be directly under the mount point
                candidates.append(mount_point)
                # Or in common Windows install locations
                candidates.append(mount_point / "Steam")
                candidates.append(mount_point / "SteamLibrary")
                candidates.append(
                    mount_point / "Program Files (x86)" / "Steam"
                )
                candidates.append(mount_point / "Program Files" / "Steam")
        except PermissionError:
            pass

    return candidates


def _find_path(relative: Path, label: str) -> str:
    """
    Search all candidate Steam roots for a directory matching `relative`.

    For example, _find_path("steamapps/common/wallpaper_engine/assets", "assets")
    will check:
      ~/.local/share/Steam/steamapps/common/wallpaper_engine/assets
      /run/media/arghadeep/Windows/Program Files (x86)/Steam/steamapps/common/wallpaper_engine/assets
      ... etc.

    Returns the first existing path, or empty string if none found.
    """
    for root in _get_candidate_steam_roots():
        candidate = root / relative
        if candidate.is_dir():
            logger.info(
                "Auto-detected %s: %s", label, candidate
            )
            return str(candidate)

    logger.debug("Could not auto-detect %s in any standard location.", label)
    return ""


def resolve_paths(config: Config) -> None:
    """
    Fill in assets_dir and workshop_dir by scanning standard Steam locations.

    Only runs auto-detection for paths that are empty (not set in TOML).
    If the user explicitly configured a path, we respect it — this is the
    "explicit config overrides convention" principle.

    This mutates the Config in-place (like a Java setter that modifies
    the object rather than returning a new one).
    """
    if not config.assets_dir:
        logger.info("assets_dir not set — scanning for it...")
        config.assets_dir = _find_path(_ASSETS_REL, "assets_dir")

    if not config.workshop_dir:
        logger.info("workshop_dir not set — scanning for it...")
        config.workshop_dir = _find_path(_WORKSHOP_REL, "workshop_dir")
