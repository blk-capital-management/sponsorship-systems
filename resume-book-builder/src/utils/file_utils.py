"""File and path utilities."""

import os
import shutil
from datetime import datetime
from pathlib import Path


def ensure_dir(path: Path) -> Path:
    """Create directory (and parents) if it doesn't exist. Returns the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def timestamped_filename(base: str, suffix: str) -> str:
    """Return a filename like 'base_20250519_143022.suffix'."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{ts}{suffix}"


def safe_stem(text: str) -> str:
    """Convert arbitrary text to a safe filename stem (no spaces or special chars)."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in text)
    return safe.strip("_")


def file_size_mb(path: Path) -> float:
    """Return file size in megabytes, rounded to 2 decimal places."""
    return round(path.stat().st_size / (1024 * 1024), 2)


def copy_file(src: Path, dst: Path) -> Path:
    """Copy src to dst, creating parent dirs as needed. Returns dst."""
    ensure_dir(dst.parent)
    shutil.copy2(src, dst)
    return dst


def write_bytes(path: Path, data: bytes) -> Path:
    """Write bytes to path, creating parent dirs. Returns path."""
    ensure_dir(path.parent)
    path.write_bytes(data)
    return path


def resolve_path(raw: str, base: Path) -> Path:
    """Resolve a path that may be relative (to base) or absolute."""
    p = Path(raw)
    if p.is_absolute():
        return p
    return (base / p).resolve()
