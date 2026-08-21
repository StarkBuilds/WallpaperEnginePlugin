"""
sanitizer.py — Wallpaper Engine PKG Unpacker and Script Sanitizer
"""

import os
import struct
import logging
import subprocess
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

def unpack_pkg(pkg_path: Path, out_dir: Path) -> None:
    """Unpack a Wallpaper Engine .pkg file (PKGV0001/PKGV0022 format) into out_dir."""
    logger.info("Unpacking %s to %s", pkg_path, out_dir)
    with open(pkg_path, 'rb') as f:
        # 1. Read Header
        sig_len = struct.unpack('<I', f.read(4))[0]
        sig = f.read(sig_len).decode('utf-8', errors='ignore')
        if not sig.startswith("PKGV"):
            raise ValueError(f"Invalid PKG signature: {sig}")
            
        num_files = struct.unpack('<I', f.read(4))[0]
        
        # 2. Read File Table
        files = []
        for _ in range(num_files):
            name_len = struct.unpack('<I', f.read(4))[0]
            name = f.read(name_len).decode('utf-8', errors='ignore')
            offset = struct.unpack('<I', f.read(4))[0]
            size = struct.unpack('<I', f.read(4))[0]
            files.append({'name': name, 'offset': offset, 'size': size})
            
        # 3. Extract Files
        data_start = f.tell()
        for file in files:
            out_file = out_dir / file['name']
            out_file.parent.mkdir(parents=True, exist_ok=True)
            
            f.seek(data_start + file['offset'])
            data = f.read(file['size'])
            
            if len(data) != file['size']:
                logger.error("Size mismatch extracting %s: expected %d, got %d", file['name'], file['size'], len(data))
                
            out_file.write_bytes(data)

def sanitize_json_files(target_dir: Path) -> None:
    """Finds all .json files and runs the Node.js Babel sanitizer on them."""
    # We locate sanitizer.js in the project root
    project_root = Path(__file__).parent.parent
    sanitizer_js = project_root / "sanitizer.js"
    import shutil as _shutil
    node_bin_str = _shutil.which("node")
    if not node_bin_str:
        node_bin = Path("/tmp/node-v20.11.1-linux-x64/bin/node")  # legacy fallback
    else:
        node_bin = Path(node_bin_str)
    
    if not node_bin.exists():
        logger.warning("Node.js not found on $PATH or at legacy path. Skipping sanitization. Install with: sudo pacman -S nodejs")
        return
        
    if not sanitizer_js.exists():
        logger.warning("sanitizer.js not found at %s. Skipping.", sanitizer_js)
        return

    for path in target_dir.rglob("*.json"):
        try:
            logger.info("Sanitizing %s", path)
            result = subprocess.run(
                [str(node_bin), str(sanitizer_js), str(path)],
                capture_output=True, text=True, check=True, cwd=project_root
            )
            for line in result.stdout.splitlines():
                if "Sanitized" in line:
                    logger.info("[Babel] %s", line)
        except subprocess.CalledProcessError as e:
            logger.error("Failed to sanitize %s: %s", path, e.stderr)

def prepare_wallpaper(workshop_dir: str, wp_id: str) -> str:
    """
    Checks if the wallpaper is packed. If so, unpacks it to a cache directory,
    sanitizes the JavaScript inside the scene.json files, and returns the path
    to the unpacked directory. Otherwise, returns the original path.
    """
    original_path = Path(workshop_dir) / wp_id
    
    # Are there any .pkg files?
    pkg_files = list(original_path.glob("*.pkg"))
    if not pkg_files:
        return str(original_path)
        
    cache_dir = Path.home() / ".cache" / "wallpaper_manager" / "unpacked" / wp_id
    marker_file = cache_dir / ".sanitized"
    
    if marker_file.exists():
        logger.info("Using cached sanitized wallpaper from %s", cache_dir)
        return str(cache_dir)
        
    logger.info("First run for %s. Unpacking and sanitizing...", wp_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Unpack all .pkg files (usually just scene.pkg)
        for pkg in pkg_files:
            unpack_pkg(pkg, cache_dir)
            
        # Copy non-pkg files (like project.json, preview.gif) so linux-wallpaperengine has everything
        import shutil
        for item in original_path.iterdir():
            if item.suffix != ".pkg": 
                # linux-wallpaperengine usually doesn't need the source shaders directory if pkg is unpacked,
                # but we'll copy it just in case as some wallpapers might require it for correct rendering.
                dest = cache_dir / item.name
                if item.is_dir():
                    if not dest.exists():
                        shutil.copytree(item, dest)
                else:
                    if not dest.exists():
                        shutil.copy2(item, dest)
                        
        # Run JS AST sanitizer
        sanitize_json_files(cache_dir)
        
        # Mark as done
        marker_file.touch()
        return str(cache_dir)
        
    except Exception as e:
        logger.error("Failed to unpack/sanitize wallpaper %s: %s. Falling back to original.", wp_id, e, exc_info=True)
        return str(original_path)
