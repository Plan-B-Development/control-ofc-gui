"""Draggable flow container — fixed-size cards with drag-to-reorder.

Cards are arranged using FlowLayout (responsive wrapping). Users can drag cards
to reorder them; a drop indicator shows where the card will land and
``order_changed`` fires with the new order. The drag plumbing lives in
:class:`~control_ofc.ui.widgets.reorderable_flow.ReorderableFlow`; this container
owns the clear-and-rebuild card lifecycle used by the Controls page (DEC-129/187).
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from control_ofc.ui.widgets.flow_layout import FlowLayout
from control_ofc.ui.widgets.reorderable_flow import DropIndicator, ReorderableFlow

__all__ = ["DraggableFlowContainer", "DropIndicator"]


class DraggableFlowContainer(ReorderableFlow):
    """Container that arranges children in a flow layout with drag-to-reorder."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = FlowLayout(self, margin=4, h_spacing=6, v_spacing=6)

    def flow_layout(self) -> FlowLayout:
        return self._layout

    def add_card(self, card: QWidget, card_id: str) -> None:
        """Add a card to the flow layout."""
        self._attach_drag(card, card_id)
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
