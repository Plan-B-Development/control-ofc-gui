"""Shared hardware-readiness severity helpers (DEC-200).

Maps the daemon's ``GET /inventory/readiness`` severity vocabulary
(``ok``/``info``/``warning``/``critical``) to the GUI's
``(word, glyph, css_class, rank)`` tuple, plus the auto-expand rank threshold and
the severity-badge minimum width. Consumed by the Hardware page's readiness
rendering (``services/hardware_view``).

The standalone ``InventoryReadinessView`` widget was retired in DEC-216 (with the
legacy Diagnostics page); the Hardware page renders readiness itself. The module
name is kept so the live ``_severity_chip`` / ``_AUTO_EXPAND_RANK`` import path is
unchanged.
"""

from __future__ import annotations

from control_ofc.ui.hwmon_guidance import severity_display

_SEVERITY_BADGE_MIN_WIDTH = 104  # so "⛔ CRITICAL" never clips


def _severity_chip(severity: str) -> tuple[str, str, str, int]:
    """Map a daemon severity (``ok``/``info``/``warning``/``critical``) to the
    GUI's ``(word, glyph, css_class, rank)``.

    The daemon vocabulary differs from the GUI's ``severity_display`` (which uses
    ``warn`` not ``warning`` and has no ``ok``), so normalise here: ``warning`` →
    ``warn`` and ``ok`` → a Success chip. Unknown values degrade to the
    forgiving ``severity_display`` default (info-tier).
    """
    s = (severity or "").lower()
    if s == "ok":
        return ("OK", "✓", "SuccessChip", 0)
    disp = severity_display("warn" if s == "warning" else s)
    return (disp.word, disp.glyph, disp.css_class, disp.rank)


# Auto-expand an item's detail when it is at least warning-tier.
_AUTO_EXPAND_RANK = severity_display("warn").rank
