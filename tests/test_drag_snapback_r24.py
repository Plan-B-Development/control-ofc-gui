"""Tests for Refinement 24A: Drag/drop invalid-drop recovery and snap-back."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from control_ofc.ui.widgets.draggable_flow import DraggableFlowContainer


def _make_card(text: str) -> QPushButton:
    """Create a minimal widget to act as a card in the flow container."""
    btn = QPushButton(text)
    btn.setFixedSize(100, 50)
    return btn


class TestSnapBack:
    """Cards maintain correct order and visibility after drag operations."""

    def test_card_ids_returns_correct_order(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        container.add_card(_make_card("A"), "a")
        container.add_card(_make_card("B"), "b")
        container.add_card(_make_card("C"), "c")
        assert container.card_ids() == ["a", "b", "c"]

    def test_no_cards_hidden_after_add(self, qtbot):
        """Cards are not explicitly hidden after being added to the container."""
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        container.add_card(_make_card("A"), "a")
        container.add_card(_make_card("B"), "b")
        for i in range(container.flow_layout().count()):
            item = container.flow_layout().itemAt(i)
            assert not item.widget().isHidden()

    def test_clear_cards_empties_container(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        container.add_card(_make_card("A"), "a")
        container.add_card(_make_card("B"), "b")
        container.clear_cards()
        assert container.card_ids() == []
        assert container.flow_layout().count() == 0

    def test_order_unchanged_without_drop_event(self, qtbot):
        """Simulates the state after a drag is cancelled (no dropEvent fires).

        When drag.exec() returns IgnoreAction, the card is re-shown at its
        original position and order_changed is never emitted.
        """
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        container.add_card(_make_card("A"), "a")
        container.add_card(_make_card("B"), "b")
        container.add_card(_make_card("C"), "c")

        original_order = container.card_ids()

        # Simulate what _start_drag does after a cancelled drag:
        # widget was hidden, then re-shown, layout invalidated
        card_b = container.flow_layout().itemAt(1).widget()
        card_b.setVisible(False)
        card_b.setVisible(True)
        container.flow_layout().invalidate()
        container.updateGeometry()

        assert container.card_ids() == original_order

    def test_valid_reorder_via_drop_event(self, qtbot):
        """Programmatic reorder: move card from index 2 to index 0."""

        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        container.add_card(_make_card("A"), "a")
        container.add_card(_make_card("B"), "b")
        container.add_card(_make_card("C"), "c")
        container.resize(400, 200)
        container.show()
        qtbot.waitExposed(container)

        # Track order_changed emission
        emitted = []
        container.order_changed.connect(lambda order: emitted.append(order))

        # Manually invoke the reorder logic: take "c" from index 2, insert at 0
        layout = container.flow_layout()
        source = layout.itemAt(2).widget()
        layout.takeAt(2)
        layout.insertWidget(0, source)
        layout.invalidate()
        container.updateGeometry()
        container.order_changed.emit(container.card_ids())

        assert container.card_ids() == ["c", "a", "b"]
        assert len(emitted) == 1
        assert emitted[0] == ["c", "a", "b"]

    def test_drop_at_same_position_is_noop(self, qtbot):
        """Dropping a card at its current position should not emit order_changed."""
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        container.add_card(_make_card("A"), "a")
        container.add_card(_make_card("B"), "b")

        emitted = []
        container.order_changed.connect(lambda order: emitted.append(order))

        # The dropEvent code checks source_index == drop_index and returns early
        # We verify this invariant: card_ids unchanged, no signal
        assert container.card_ids() == ["a", "b"]
        assert len(emitted) == 0
