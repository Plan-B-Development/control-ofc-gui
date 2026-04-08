"""Tests for DraggableFlowContainer — card management, ordering, and drop logic."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QFrame

from control_ofc.ui.widgets.draggable_flow import DraggableFlowContainer, DropIndicator


class TestDropIndicator:
    def test_indicator_starts_hidden(self, qtbot):
        indicator = DropIndicator()
        qtbot.addWidget(indicator)
        assert not indicator.isVisible()

    def test_indicator_has_class_property(self, qtbot):
        indicator = DropIndicator()
        qtbot.addWidget(indicator)
        assert indicator.property("class") == "DropIndicator"


class TestAddAndClear:
    def test_add_card_stores_card_id(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        card = QFrame()
        container.add_card(card, "c1")
        assert card.property("card_id") == "c1"

    def test_card_ids_returns_order(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        for cid in ["a", "b", "c"]:
            container.add_card(QFrame(), cid)
        assert container.card_ids() == ["a", "b", "c"]

    def test_clear_cards_empties(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        for cid in ["x", "y"]:
            container.add_card(QFrame(), cid)
        container.clear_cards()
        assert container.card_ids() == []
        assert container.flow_layout().count() == 0


class TestDropEvent:
    """Test the dropEvent reorder logic using a synthetic QDropEvent."""

    def _make_container(self, qtbot, ids):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        container.resize(400, 200)
        container.show()
        for cid in ids:
            card = QFrame()
            card.setFixedSize(80, 60)
            container.add_card(card, cid)
        # Force layout calculation
        from PySide6.QtWidgets import QApplication

        container.flow_layout().activate()
        QApplication.processEvents()
        return container

    def _fire_drop(self, container, card_id, drop_index):
        """Simulate a drop at a specific index by creating a synthetic event."""
        mime = QMimeData()
        mime.setText(card_id)

        # Get the geometry of the target index to compute drop position
        if drop_index < container.flow_layout().count():
            item = container.flow_layout().itemAt(drop_index)
            if item and item.widget():
                geom = item.geometry()
                pos = QPointF(geom.left() - 1, geom.top() + 1)
            else:
                pos = QPointF(0, 0)
        else:
            # Drop at end
            last = container.flow_layout().itemAt(container.flow_layout().count() - 1)
            if last and last.widget():
                geom = last.geometry()
                pos = QPointF(geom.right() + 10, geom.top() + 1)
            else:
                pos = QPointF(0, 0)

        event = QDropEvent(
            pos,
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        container.dropEvent(event)

    def test_reorder_first_to_last(self, qtbot):
        container = self._make_container(qtbot, ["a", "b", "c"])
        received = []
        container.order_changed.connect(received.append)
        self._fire_drop(container, "a", 3)
        assert container.card_ids() == ["b", "c", "a"]
        assert len(received) == 1

    def test_same_position_is_noop(self, qtbot):
        """Dropping a card at its own position should not emit order_changed."""
        container = self._make_container(qtbot, ["a", "b", "c"])
        received = []
        container.order_changed.connect(received.append)
        # Use dropEvent directly with a position that maps to source index
        mime = QMimeData()
        mime.setText("a")
        # Get item 0 geometry and drop left of center (triggers index 0)
        item = container.flow_layout().itemAt(0)
        geom = item.geometry()
        pos = QPointF(geom.left() + 1, geom.top() + 1)
        event = QDropEvent(
            pos,
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        container.dropEvent(event)
        assert container.card_ids() == ["a", "b", "c"]
        assert len(received) == 0

    def test_drop_unknown_card_id(self, qtbot):
        container = self._make_container(qtbot, ["a", "b"])
        received = []
        container.order_changed.connect(received.append)
        self._fire_drop(container, "unknown", 0)
        # No reorder, no signal
        assert container.card_ids() == ["a", "b"]
        assert len(received) == 0


class TestDragEnterLeave:
    def test_drag_enter_accepts_text_mime(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        event = MagicMock()
        event.mimeData.return_value.hasText.return_value = True
        container.dragEnterEvent(event)
        event.acceptProposedAction.assert_called_once()

    def test_drag_leave_hides_indicator(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        container.show()
        container._indicator.show()
        assert container._indicator.isVisible()
        container.dragLeaveEvent(MagicMock())
        assert not container._indicator.isVisible()


class TestIndexAtPosition:
    def test_empty_container_returns_zero(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        assert container._index_at_position(QPoint(0, 0)) == 0
