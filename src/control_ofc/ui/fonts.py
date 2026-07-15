"""Bundled OFL font registration (DEC-208).

Ships **Space Grotesk** (headings / labels / numeric readouts) and **DM Sans**
(body) with the package and registers them with the Qt font database at startup,
so the green theme's ``font_family`` / ``font_family_heading`` tokens resolve to
the same fonts as the design mockups regardless of what the host has installed.
Both are SIL Open Font License 1.1 (see ``fonts/OFL-*.txt``).

Registration is best-effort: a missing or unreadable file is logged and skipped,
and ``QFont`` falls back to the system font — a font problem must never stop the
GUI from starting. Must be called *after* a ``QApplication`` exists.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# TTF basename -> the primary family name Qt exposes once the file is registered
# (verified with QFontDatabase.applicationFontFamilies). Both are variable fonts,
# so one file per family covers every weight the mockups use.
_BUNDLED_FONTS: dict[str, str] = {
    "SpaceGrotesk.ttf": "Space Grotesk",
    "DMSans.ttf": "DM Sans",
}

_registered = False


def fonts_dir() -> Path:
    """Directory holding the bundled TTFs (works in the dev tree and the wheel)."""
    return Path(__file__).resolve().parent / "fonts"


def register_bundled_fonts() -> list[str]:
    """Register the bundled fonts with the Qt font database (idempotent).

    Returns the family names that are available after the call. Never raises;
    a missing/invalid file is logged and skipped. Requires a live
    ``QApplication`` (call it during startup, after the app is constructed).
    """
    global _registered
    from PySide6.QtGui import QFontDatabase

    if _registered:
        return [fam for fam in _BUNDLED_FONTS.values() if fam in QFontDatabase.families()]

    d = fonts_dir()
    for filename in _BUNDLED_FONTS:
        path = d / filename
        if not path.exists():
            log.warning("Bundled font missing: %s (falling back to system font)", path)
            continue
        if QFontDatabase.addApplicationFont(str(path)) == -1:
            log.warning("Failed to register bundled font: %s", path)
    _registered = True
    return [fam for fam in _BUNDLED_FONTS.values() if fam in QFontDatabase.families()]
