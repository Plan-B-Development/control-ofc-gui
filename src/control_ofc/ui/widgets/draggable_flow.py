"""Draggable flow container — fixed-size cards with drag-to-reorder.

Cards are arranged using FlowLayout (responsive wrapping). Users can
drag cards to reorder them. A visual drop indicator shows where the
card will land. The container emits ``order_changed`` with the new
order of card IDs after a successful drop.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QFrame, QWidget

from control_ofc.ui.widgets.flow_layout import FlowLayout

log = logging.getLogger(__name__)

# Minimum mouse distance before initiating drag
_DRAG_THRESHOLD = 10


class DropIndicator(QFrame):
    """Visual indicator showing where a dragged card will land."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(3)
        self.setMinimumHeight(40)
        self.setProperty("class", "DropIndicator")
        self.hide()


class DraggableFlowContainer(QWidget):
    """Container that arranges children in a flow layout with drag-to-reorder."""

    order_changed = Signal(list)  # list[str] of card IDs in new order

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._layout = FlowLayout(self, margin=4, h_spacing=6, v_spacing=6)
        self._indicator = DropIndicator(self)
        self._drag_source: QWidget | None = None

    def flow_layout(self) -> FlowLayout:
        return self._layout

    def add_card(self, card: QWidget, card_id: str) -> None:
        """Add a card to the flow layout."""
        card.setProperty("card_id", card_id)
        card.installEventFilter(self)
        self._layout.addWidget(card)

    def clear_cards(self) -> None:
        """Remove all cards from the layout and schedule destruction."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item and item.widget():
                w = item.widget()
                w.blockSignals(True)
                w.removeEventFilter(self)
                w.setParent(None)
                w.deleteLater()

    def card_ids(self) -> list[str]:
        """Return current card IDs in layout order."""
        ids = []
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            if item and item.widget():
                cid = item.widget().property("card_id")
                if cid:
                    ids.append(cid)
        return ids

    # ── Event filter for child drag initiation ───────────────────────

    def eventFilter(self, obj: QWidget, event) -> bool:
        from PySide6.QtCore import QEvent

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            obj.setProperty("_drag_start", event.position().toPoint())
            return False

        if event.type() == QEvent.Type.MouseMove and obj.property("_drag_start"):
            start = obj.property("_drag_start")
            if (event.position().toPoint() - start).manhattanLength() >= _DRAG_THRESHOLD:
                self._start_drag(obj)
                obj.setProperty("_drag_start", None)
                return True
            return False

        if event.type() == QEvent.Type.MouseButtonRelease:
            obj.setProperty("_drag_start", None)
            return False

        return False

    def _start_drag(self, widget: QWidget) -> None:
        """Initiate a drag operation for the given card widget."""
        self._drag_source = widget
        card_id = widget.property("card_id") or ""
        order_before = self.card_ids()

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(card_id)
        drag.setMimeData(mime)

        # Create pixmap snapshot of the card
        pixmap = widget.grab()
        drag.setPixmap(
            pixmap.scaled(
                pixmap.size() * 0.8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        drag.setHotSpot(QPoint(pixmap.width() // 3, pixmap.height() // 3))

        widget.setVisible(False)
        result = drag.exec(Qt.DropAction.MoveAction)
        widget.setVisible(True)
        self._indicator.hide()
        self._drag_source = None

        # Force clean relayout after drag completes
        self._layout.invalidate()
        self.updateGeometry()

        if result == Qt.DropAction.IgnoreAction:
            log.debug("Card '%s' drag cancelled — snapped back to original position", card_id)
            # Verify model consistency after cancelled drag
            order_after = self.card_ids()
            if order_before != order_after:
                log.warning(
                    "Card order changed during cancelled drag: before=%s after=%s",
                    order_before,
                    order_after,
                )

    # ── Drop handling ────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if not event.mimeData().hasText():
            return

        pos = event.position().toPoint()
        drop_index = self._index_at_position(pos)
        self._show_indicator(drop_index)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._indicator.hide()

    def dropEvent(self, event) -> None:
        self._indicator.hide()
        if not event.mimeData().hasText():
            return

        card_id = event.mimeData().text()
        pos = event.position().toPoint()
        drop_index = self._index_at_position(pos)

        # Find the source widget and its current index
        source_index = -1
        source_widget = None
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            if item and item.widget() and item.widget().property("card_id") == card_id:
                source_index = i
                source_widget = item.widget()
                break

        if source_widget is None:
            log.warning("Drop event for card '%s' — source widget not found in layout", card_id)
            event.acceptProposedAction()
            return

        if source_index == drop_index:
            event.acceptProposedAction()
            return

        # Remove and reinsert at the new position
        self._layout.takeAt(source_index)
        if drop_index > source_index:
            drop_index -= 1
        self._layout.insertWidget(drop_index, source_widget)
        self._layout.invalidate()
        self.updateGeometry()

        event.acceptProposedAction()
        new_order = self.card_ids()
        log.debug("Card '%s' reordered: %d → %d", card_id, source_index, drop_index)
        self.order_changed.emit(new_order)

    def _index_at_position(self, pos: QPoint) -> int:
        """Determine the insertion index for a drop at the given position."""
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            if item and item.widget() and item.widget().isVisible():
                geom = item.geometry()
                center_x = geom.center().x()
                if pos.y() < geom.bottom() and pos.x() < center_x:
                    return i
        return self._layout.count()

    def _show_indicator(self, index: int) -> None:
        """Position and show the drop indicator at the given index."""
        if index >= self._layout.count():
            # After last item
            last = self._layout.itemAt(self._layout.count() - 1)
            if last and last.widget() and last.widget().isVisible():
                geom = last.geometry()
                self._indicator.move(geom.right() + 2, geom.top())
                self._indicator.setFixedHeight(geom.height())
                self._indicator.show()
            return

        item = self._layout.itemAt(index)
        if item and item.widget():
            geom = item.geometry()
            self._indicator.move(geom.left() - 4, geom.top())
            self._indicator.setFixedHeight(geom.height())
            self._indicator.show()
