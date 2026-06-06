"""Colour-string validation (Qt-free, layer-neutral).

Shared by the UI layer (theme tokens, chart pens) and the service layer
(app-settings import validation) so a malformed or hostile colour value is
rejected *before* it reaches a Qt stylesheet f-string or pyqtgraph's
``mkPen``. Kept free of Qt/PySide imports so the service layer can reuse it
without depending on the UI (DEC-137).
"""

from __future__ import annotations

import re

# Accept #RGB, #RGBA, #RRGGBB and #RRGGBBAA (leading '#' required). This covers
# every theme token in the bundled themes — including 8-digit ARGB values such
# as ``modal_overlay = "#000000aa"`` — while rejecting named colours ("red"),
# CSS functions ("rgb(...)"), and any QSS-injection payload.
_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


def is_valid_color(value: object) -> bool:
    """Return True if *value* is a hex colour string we accept.

    Hex-only by design: theme tokens are hex by convention, and rejecting
    everything else is the safe anti-injection choice for values that get
    interpolated into a Qt stylesheet.
    """
    return isinstance(value, str) and _HEX_COLOR_RE.fullmatch(value) is not None
