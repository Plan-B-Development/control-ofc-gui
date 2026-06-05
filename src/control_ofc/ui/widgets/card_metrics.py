"""Shared card sizing for Controls page cards.

Both CurveCard and ControlCard derive their dimensions from here so the two
grids stay column-aligned. Sizing is **content-aware**, not a fixed pixel box
(DEC-128):

- **Width is fixed** per card so the flow grid forms tidy, aligned columns.
- **Height is a minimum floor**, not a cap — each card sets ``minimumHeight``
  and lets its ``QVBoxLayout`` grow taller when scaled text needs the room, so
  rows can never clip (the old fixed 220x160 box clipped at large font sizes).

Both dimensions scale with the theme's ``base_font_size_pt`` (7-16) and a
user-selectable size tier (compact / comfortable / large), so the cards honour
the current theme text size automatically while still allowing a manual
density preference.
"""

from __future__ import annotations

# Size tiers (D1=C: auto-scale with font, plus an optional manual multiplier).
CARD_SIZE_COMPACT = "compact"
CARD_SIZE_COMFORTABLE = "comfortable"
CARD_SIZE_LARGE = "large"
CARD_SIZE_TIERS: tuple[str, ...] = (
    CARD_SIZE_COMPACT,
    CARD_SIZE_COMFORTABLE,
    CARD_SIZE_LARGE,
)
DEFAULT_CARD_SIZE = CARD_SIZE_COMFORTABLE

_TIER_SCALE: dict[str, float] = {
    CARD_SIZE_COMPACT: 0.92,
    CARD_SIZE_COMFORTABLE: 1.0,
    CARD_SIZE_LARGE: 1.18,
}

# Baseline dimensions at the reference 10pt base font, "comfortable" tier.
# Width must hold the Fan Role bottom action row (RPM + Manual/Delete/Edit)
# without squashing the buttons; height is a comfortable floor — content grows
# it further when needed.
_REF_PT = 10
_BASE_WIDTH = 280
_BASE_HEIGHT = 188
# Per-point growth so cards track the theme's text size across the 7-16 range.
_WIDTH_PER_PT = 11
_HEIGHT_PER_PT = 14

# Font range mirrors theme.ThemeTokens.base_font_size_pt (7-16).
_MIN_PT = 7
_MAX_PT = 16


def card_dimensions(base_pt: int, tier: str = DEFAULT_CARD_SIZE) -> tuple[int, int]:
    """Return ``(fixed_width, minimum_height)`` for a card.

    Args:
        base_pt: The theme's base font size in points (clamped to 7-16).
        tier: One of :data:`CARD_SIZE_TIERS`; unknown values fall back to
            "comfortable" (1.0x).

    The width is meant to be applied via ``setFixedWidth`` and the height via
    ``setMinimumHeight`` so the card grows past the floor when its content
    needs more vertical space.
    """
    try:
        base_pt = int(base_pt)
    except (TypeError, ValueError):
        base_pt = _REF_PT
    base_pt = max(_MIN_PT, min(_MAX_PT, base_pt))
    scale = _TIER_SCALE.get(tier, 1.0)
    width = round((_BASE_WIDTH + (base_pt - _REF_PT) * _WIDTH_PER_PT) * scale)
    height = round((_BASE_HEIGHT + (base_pt - _REF_PT) * _HEIGHT_PER_PT) * scale)
    return width, height


# Backwards-compatible reference constants: the "comfortable" tier at the
# default 10pt base font. Retained for callers/tests that want a single
# nominal size; live cards compute their own dimensions via card_dimensions().
CARD_WIDTH, CARD_HEIGHT = card_dimensions(_REF_PT, DEFAULT_CARD_SIZE)
