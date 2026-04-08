"""Branding helpers — icon loading and asset paths."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

from onlyfans.paths import assets_dir


def load_app_icon() -> QIcon | None:
    """Load the application icon from assets."""
    svg_path = assets_dir() / "app_icon" / "app_icon.svg"
    if svg_path.exists():
        return QIcon(str(svg_path))
    return None


def splash_image_path() -> Path | None:
    """Return path to splash image if it exists."""
    p = assets_dir() / "splash" / "splash.png"
    return p if p.exists() else None


def banner_image_path() -> Path | None:
    """Return path to banner image if it exists."""
    p = assets_dir() / "banner.png"
    return p if p.exists() else None
