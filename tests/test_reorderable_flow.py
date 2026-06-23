"""DEC-187: ReorderableFlow base — the shared drag-reorder plumbing.

The full drag/drop/indicator behaviour is exercised through the two concrete
containers (test_draggable_flow_coverage.py and test_fan_zone_card.py); this file
pins the base's own contract: the flow_layout() hook, card_id tagging, and that
drops are accepted.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QFrame

from control_ofc.ui.widgets.flow_layout import FlowLayout
from control_ofc.ui.widgets.reorderable_flow import ReorderableFlow


class _MiniFlow(ReorderableFlow):
    """Minimal concrete subclass for testing the base in isolation."""

    def __init__(self):
        super().__init__()
        self._layout = FlowLayout(self)

    def flow_layout(self):
        return self._layout

    def add(self, card, cid):
        self._attach_drag(card, cid)
        self._layout.addWidget(card)


class TestReorderableFlowContract:
    def test_flow_layout_must_be_overridden(self, qtbot):
        base = ReorderableFlow()
        qtbot.addWidget(base)
        # card_ids() routes through flow_layout(), which the base leaves abstract.
        with pytest.raises(NotImplementedError):
            base.card_ids()

    def test_attach_drag_tags_card_id_and_preserves_order(self, qtbot):
        flow = _MiniFlow()
        qtbot.addWidget(flow)
        for cid in ("a", "b", "c"):
            flow.add(QFrame(), cid)
        assert flow.card_ids() == ["a", "b", "c"]

    def test_base_accepts_drops(self, qtbot):
        flow = _MiniFlow()
        qtbot.addWidget(flow)
        assert flow.acceptDrops() is True
