"""Per-card user resize for Controls page cards (DEC-129).

A small bottom-right grip lets the user drag-resize an individual card. Sizes
snap live to an absolute lattice (multiples of ``SNAP_STEP_PX``) so two cards
resized near the same size land on exactly the same size — the visible
step-jump during the drag is the snapping feedback (dashboard-grid style).

The grip *accepts* its mouse press/move/release events, so they are never
delivered to the card itself — this is what keeps a resize drag from
triggering ``DraggableFlowContainer``'s reorder drag (its event filter is
installed on the card only) or the card's click-to-select.

The host card must provide:

- ``set_user_size(width, height) -> tuple[int, int]`` — snap, clamp, apply,
  and return the size actually applied.
- a double-click reset path via the ``reset_requested`` signal.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.card_metrics import SNAP_STEP_PX

# Side length of the square grip hot-zone in the card's bottom-right corner.
GRIP_SIZE_PX = 14
# Inset from the card edge so the dots sit clear of the 8px QSS corner radius.
GRIP_INSET_PX = 2


def snap_size(
    width: int,
    height: int,
    min_width: int,
    min_height: int,
    step: int = SNAP_STEP_PX,
) -> tuple[int, int]:
    """Snap a requested card size to the shared lattice, clamped to minimums.

    Each axis rounds half-up to the nearest multiple of ``step`` (an absolute
    lattice, so equality between cards is exact), then clamps to the minimum
    rounded *up* to the lattice — the result is always on-lattice and never
    below the minimum, so a shrink can't clip card rows.
    """
    step = max(1, int(step))

    def _axis(value: int, floor: int) -> int:
        snapped = ((int(value) + step // 2) // step) * step
        floor_lattice = -(-max(0, int(floor)) // step) * step  # ceil to lattice
        return max(snapped, floor_lattice)

    return _axis(width, min_width), _axis(height, min_height)


class CardResizeGrip(QWidget):
    """Bottom-right drag grip that resizes its parent card on the snap lattice.

    QSizeGrip only works on top-level windows, so this is the in-layout
    equivalent: it consumes its own mouse events, asks the card to apply each
    snapped size live during the drag, and reports the final size on release.
    Double-click requests a reset to the theme-derived default size.
    """

    resize_finished = Signal(int, int)  # applied (width, height) on release
    reset_requested = Signal()  # double-click: restore theme default

    def __init__(self, card: QWidget) -> None:
        super().__init__(card)
        self._card = card
        self._drag_origin: QPoint | None = None
        self._start_size: QSize | None = None
        self._last_applied: tuple[int, int] | None = None
        self.setFixedSize(GRIP_SIZE_PX, GRIP_SIZE_PX)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setToolTip(
            f"Drag to resize this card (snaps to a {SNAP_STEP_PX}px grid).\n"
            "Double-click to reset to the theme size."
        )

    # ── Mouse handling ───────────────────────────────────────────────
    # Every handler accepts its event: nothing here may propagate to the
    # card, or the flow container's reorder filter would start a card drag.

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._start_size = self._card.size()
            self._last_applied = None
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is None or self._start_size is None:
            event.accept()
            return
        delta = event.globalPosition().toPoint() - self._drag_origin
        self._last_applied = self._card.set_user_size(
            self._start_size.width() + delta.x(),
            self._start_size.height() + delta.y(),
        )
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_origin is not None and self._last_applied is not None:
            self.resize_finished.emit(*self._last_applied)
        self._drag_origin = None
        self._start_size = None
        self._last_applied = None
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        self._drag_origin = None
        self._start_size = None
        self._last_applied = None
        self.reset_requested.emit()
        event.accept()

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        """Three theme-tinted dots along the bottom-right diagonal."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        # Read the live theme at paint time so a theme switch restyles the
        # grip without any plumbing through the card.
        painter.setBrush(QColor(active_theme().text_muted))
        right = self.width() - GRIP_INSET_PX
        bottom = self.height() - GRIP_INSET_PX
        for offset in (3, 7, 11):
            painter.drawEllipse(right - offset, bottom - 3, 2, 2)
            painter.drawEllipse(right - 3, bottom - offset, 2, 2)
        painter.end()
