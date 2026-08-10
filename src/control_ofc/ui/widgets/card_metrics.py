"""Shared card sizing for Controls page cards and the Dashboard fan tile.

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
DEFAULT_CARD_SIZE = CARD_SIZE_COMFORTABLE

_TIER_SCALE: dict[str, float] = {
    CARD_SIZE_COMPACT: 0.92,
    CARD_SIZE_COMFORTABLE: 1.0,
    CARD_SIZE_LARGE: 1.18,
}

# Baseline dimensions at the reference 10pt base font, "comfortable" tier.
# Width must hold the Fan Role bottom action row (RPM + Manual/Delete/Edit)
# without squashing the buttons; height is a comfortable floor — content grows
# it further when needed. DEC-129 retuned the height floor to the measured
# content height of the tightened cards (ControlCard 119px / CurveCard 127px
# at 10pt with theme QSS) plus a little headroom — the old 188 floor left
# ~55px of surplus that the layouts spread between text rows, reading as
# bloated line spacing.
_REF_PT = 10
_BASE_WIDTH = 299
_BASE_HEIGHT = 132
# Per-point growth so cards track the theme's text size across the 7-16 range.
#
# DEC-258: was 11, which was measurably too small — the ControlCard's details
# block clipped from 11pt in the default tier and from 9pt in compact, worsening
# to a 79px deficit at 15pt. Re-derived by rendering the card at every size and
# measuring the block's own hint: it needs ~23px/pt (161px at 7pt rising to 372px
# at 15-16pt, plus 42px of card padding), so the reference 280px at 10pt is
# right in isolation, but a steeper slope shrinks the small end too — 7pt began
# eliding where it had not before — so the base is re-anchored to 299 as well.
# 299/23 dominates the measured requirement at every size in the default tier
# (need = hint + 42px padding: 203px at 7pt, 279 at 10, 414 at 15-16).
#
# A constant alone cannot close this class, which is why the curve label now
# elides: curve names are profile-authored and arbitrary-length, so no width is
# ever sufficient for the widest possible content. This makes the *typical* card
# fit; elision makes the worst case survivable.
_WIDTH_PER_PT = 23
_HEIGHT_PER_PT = 10

# Font range mirrors theme.ThemeTokens.base_font_size_pt (7-16).
_MIN_PT = 7
_MAX_PT = 16

# DEC-129: per-card user resize. Drag sizes snap to an absolute lattice —
# multiples of SNAP_STEP_PX, not offsets from the drag start — so two cards
# resized near the same size land on *exactly* the same size. The width floor
# sits just under the smallest width the tier system itself ships, so a user
# shrink can't get meaningfully worse than a size the app already uses; the
# height floor is per-card content (the card layout's minimumSize), enforced
# in snap_size's clamp.
SNAP_STEP_PX = 20


def _smallest_tier_width() -> int:
    """The narrowest width `card_dimensions` can produce, over every tier and pt.

    DERIVED, not written down. It was a hardcoded 220 with a comment claiming
    "compact tier at 7pt ~227px" — true when written, and quietly false after
    DEC-258 re-anchored `_BASE_WIDTH`/`_WIDTH_PER_PT` from 280/11 to 299/23.
    The real smallest became 212, i.e. *below* the resize floor, so a user who
    resized a card could never return it to the size its unresized neighbours
    were already using: dragging down snapped to 220 and stopped.

    Deriving it means the next re-anchor cannot reintroduce that skew.
    """
    return min(
        card_dimensions(pt, tier)[0] for pt in range(_MIN_PT, _MAX_PT + 1) for tier in _TIER_SCALE
    )


def card_dimensions(base_pt: int, tier: str = DEFAULT_CARD_SIZE) -> tuple[int, int]:
    """Return ``(fixed_width, minimum_height)`` for a card.

    Args:
        base_pt: The theme's base font size in points (clamped to 7-16).
        tier: One of the ``CARD_SIZE_*`` tier names; unknown values fall back
            to "comfortable" (1.0x).

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


def card_pane_min_width(base_pt: int, tier: str = DEFAULT_CARD_SIZE) -> int:
    """Minimum width for a scroll pane holding a single column of cards.

    DEC-260. The Controls page hardcoded 300 px for this, a literal that silently
    tracked the old 280 px card. When DEC-258 re-derived the card width to 299 px
    the two drifted apart: the flow container's minimum (card + margins) exceeded
    the pane's viewport, and Qt resolves that with a permanent HORIZONTAL
    scrollbar and the card's right edge — including its resize grip — clipped
    off. At the shipped default 1400x850 window both Controls panes did exactly
    that, which is the same defect class DEC-258 set out to remove.

    So the pane minimum is computed from the card, not guessed alongside it:
    one card, plus the flow layout's own margins, plus the vertical scrollbar the
    pane grows as soon as there is more than one row (that scrollbar takes its
    width out of the viewport, so omitting it reintroduces the overflow at the
    boundary).
    """
    return card_dimensions(base_pt, tier)[0] + _FLOW_MARGIN_PX + _SCROLLBAR_ALLOWANCE_PX


# Horizontal margins the flow container adds around a card column, and the width
# a vertical scrollbar takes out of the viewport. Both measured on this stack;
# the scrollbar allowance is generous by a couple of pixels so a theme with a
# wider scrollbar does not put the pane back into overflow.
_FLOW_MARGIN_PX = 8
_SCROLLBAR_ALLOWANCE_PX = 18


# DEC-238: the Dashboard fan tile. Narrower than a Controls card — it carries a
# title, a fan count, three readings and a curve band, not an editor — and it
# takes a *fixed* width so the flow grid forms tidy columns instead of the ragged
# run content-sized hints produced (measured 267/267/267/251 for four tiles).
#
# Calibrated by rendering the tile at 7/10/16pt against its worst-case content
# ("10000" RPM / "100%" / "-40°C", the longest state chip, and a name long
# enough to elide). The binding row is the metric triple, whose values scale at
# the 1.5x `card_value` role — hence a per-point term steeper than the Controls
# cards': at 13px/pt the readings clipped at 16pt. The name elides rather than
# clipping, so it never drives the width.
_TILE_BASE_WIDTH = 235
_TILE_WIDTH_PER_PT = 17


def fan_tile_width(base_pt: int) -> int:
    """Return the fixed width for a Dashboard fan tile at *base_pt*."""
    try:
        base_pt = int(base_pt)
    except (TypeError, ValueError):
        base_pt = _REF_PT
    base_pt = max(_MIN_PT, min(_MAX_PT, base_pt))
    return _TILE_BASE_WIDTH + (base_pt - _REF_PT) * _TILE_WIDTH_PER_PT


# Evaluated once at import, after `card_dimensions` is defined.
#
# Rounded DOWN onto the snap lattice, and that is load-bearing rather than
# tidiness: `snap_size` rounds an off-lattice floor *up* to the next multiple,
# so an off-lattice floor is silently replaced by a larger one. A first pass at
# this fix used `smallest - 8` = 204 and changed nothing at all — 204 rounds
# straight back up to 220. Only a lattice-aligned floor is the floor it claims
# to be.
#
# Down rather than up for the same reason the constant exists: drag targets are
# multiples of SNAP_STEP_PX, so the smallest tier width (212) is not itself
# reachable by dragging. Rounding down to 200 lets a user shrink a card to at
# least as narrow as its own default; rounding up would strand it wider.
MIN_USER_CARD_WIDTH_PX = (_smallest_tier_width() // SNAP_STEP_PX) * SNAP_STEP_PX
