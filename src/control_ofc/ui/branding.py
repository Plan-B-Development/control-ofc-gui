"""Branding helpers — icon loading."""

from __future__ import annotations

from PySide6.QtGui import QIcon

from control_ofc.paths import assets_dir


def load_app_icon() -> QIcon | None:
    """Load the application icon from assets."""
    svg_path = assets_dir() / "app_icon" / "app_icon.svg"
    if svg_path.exists():
        return QIcon(str(svg_path))
    return None
