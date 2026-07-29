"""Shared base for the resizable, grid-aligned cards — ControlCard + CurveCard.

Both cards independently reimplemented the same DEC-128 (font-derived floor
sizing) + DEC-129 (persisted per-card user resize via a corner grip) machinery.
This base dedupes it: a subclass supplies its item id (for the ``resized`` /
``size_reset`` signals and the grip objectName) plus its own content, and
everything about sizing and the grip lives here. It also makes both cards proper
:class:`~control_ofc.ui.components.cards.Card` subclasses instead of re-adding
``class="Card"`` inline.

Subclass contract:

- call ``super().__init__(parent)`` (Card sets ``class="Card"``),
- call :meth:`_init_grid_card` **before** building content (so the grip exists
  before the first ``resizeEvent`` any ``setFixedWidth`` triggers),
- build the content layout, then call :meth:`apply_card_size` once,
- if you override ``resizeEvent``, call ``super().resizeEvent(event)`` so the
  grip stays pinned.
"""

from __future__ import annotations

from PySide6.QtCore import Signal

from control_ofc.ui.components.cards import Card
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.card_metrics import (
    DEFAULT_CARD_SIZE,
    MIN_USER_CARD_WIDTH_PX,
    card_dimensions,
)
from control_ofc.ui.widgets.card_resize import CardResizeGrip, snap_size

# QWIDGETSIZE_MAX — not exported by this PySide6 build; used to undo a fixed
# height when a user size override is cleared (DEC-129).
_QWIDGETSIZE_MAX = 16777215


class ResizableGridCard(Card):
    """A ``.Card`` that is fixed-width (grid-aligned), height-floored, and
    user-resizable via a bottom-right grip with a persisted per-card override.

    Emits ``resized(item_id, w, h)`` on grip release and ``size_reset(item_id)``
    on grip double-click — the Controls page persists both (DEC-129).
    """

    # item_id = control_id / curve_id (whichever the subclass represents).
    resized = Signal(str, int, int)
    size_reset = Signal(str)

    def _init_grid_card(self, item_id: str, grip_object_name: str) -> None:
        """Create the resize grip and initialise the sizing state.

        Call from the subclass ``__init__`` right after ``super().__init__()``
        and before building the content layout.
        """
        self._item_id = item_id
        # DEC-129: persisted per-card override; None = theme-derived sizing.
        self._user_size: tuple[int, int] | None = None
        # Overwritten by the first apply_card_size(); a safe default until then.
        self._card_size_tier = DEFAULT_CARD_SIZE
        # Grip exists before the first resizeEvent (any setFixedWidth below
        # triggers one) so resizeEvent can always reposition it.
        self._grip = CardResizeGrip(self)
        self._grip.setObjectName(grip_object_name)
        self._grip.resize_finished.connect(self._on_grip_resized)
        self._grip.reset_requested.connect(self._on_grip_reset)

    # ─── Public sizing API ───────────────────────────────────────────

    @property
    def user_size(self) -> tuple[int, int] | None:
        """The persisted per-card size override, or None for theme sizing."""
        return self._user_size

    def apply_card_size(
        self,
        base_pt: int,
        tier: str = DEFAULT_CARD_SIZE,
        user_size: tuple[int, int] | None = None,
    ) -> None:
        """Size the card from the theme base font size and a density tier.

        Without a user override: width is fixed so the flow grid stays
        column-aligned; height is a minimum floor (no maximum), so a card with
        scaled-up text or more content grows taller instead of clipping (DEC-128).

        With a user override (DEC-129): both dimensions are fixed to the snapped
        override, re-clamped to the current content minimum at every re-apply —
        so a theme/tier change or content growth clamps the override but never
        clears it. Passing ``user_size=None`` keeps any existing override;
        clearing is explicit via :meth:`clear_user_size`.
        """
        self._card_size_tier = tier
        if user_size is not None:
            self._user_size = self._snap_to_content(*user_size)
        if self._user_size is not None:
            width, height = self._snap_to_content(*self._user_size)
            self.setFixedWidth(width)
            self.setFixedHeight(height)
        else:
            width, height = card_dimensions(base_pt, tier)
            self.setFixedWidth(width)
            # Undo a previous override's fixed height before re-flooring.
            self.setMaximumHeight(_QWIDGETSIZE_MAX)
            self.setMinimumHeight(height)
        self.updateGeometry()

    def set_user_size(self, width: int, height: int) -> tuple[int, int]:
        """Apply a live user resize (grip drag), snapped and clamped.

        Returns the size actually applied so the grip can report it on release.
        """
        applied = self._snap_to_content(width, height)
        self._user_size = applied
        self.setFixedWidth(applied[0])
        self.setFixedHeight(applied[1])
        self.updateGeometry()
        return applied

    def clear_user_size(self) -> None:
        """Drop the per-card override and restore theme-derived sizing."""
        self._user_size = None
        self.apply_card_size(active_theme().base_font_size_pt, self._card_size_tier)

    def _snap_to_content(self, width: int, height: int) -> tuple[int, int]:
        """Snap to the shared lattice, clamped so rows can never clip."""
        return snap_size(
            width,
            height,
            MIN_USER_CARD_WIDTH_PX,
            self.layout().minimumSize().height(),
        )

    # ─── Grip wiring ─────────────────────────────────────────────────

    def _on_grip_resized(self, width: int, height: int) -> None:
        self.resized.emit(self._item_id, width, height)

    def _on_grip_reset(self) -> None:
        self.clear_user_size()
        self.size_reset.emit(self._item_id)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep the resize grip pinned to the bottom-right corner, above the card
        # content (it floats outside the layout).
        self._grip.move(self.width() - self._grip.width(), self.height() - self._grip.height())
        self._grip.raise_()
