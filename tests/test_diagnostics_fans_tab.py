"""Tests for the Diagnostics ▸ Fans tab after the DEC-124 split.

The Fans tab is now a single-purpose live view: just the Fan Status table, no
vertical splitter and no Hardware Readiness card (that content moved to the new
Troubleshooting tab). These tests lock down the slimmed-down Fans tab and the
overall tab order/labels.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QSplitter, QTableWidget

from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.services.app_state import AppState
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage

EXPECTED_TABS = ["Overview", "Sensors", "Fans", "Troubleshooting", "Event Log"]
FANS_TAB_INDEX = 2
TROUBLESHOOTING_TAB_INDEX = 3


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _make_page(qtbot) -> DiagnosticsPage:
    page = DiagnosticsPage(state=_make_state())
    qtbot.addWidget(page)
    return page


def test_tab_order_and_labels(qtbot):
    page = _make_page(qtbot)
    labels = [page._tabs.tabText(i) for i in range(page._tabs.count())]
    assert labels == EXPECTED_TABS


def test_troubleshooting_tab_index_constant_matches_layout(qtbot):
    # The auto-fetch trigger keys off this index; it must match the real tab.
    page = _make_page(qtbot)
    assert page._troubleshooting_tab_index == TROUBLESHOOTING_TAB_INDEX
    assert page._tabs.tabText(page._troubleshooting_tab_index) == "Troubleshooting"


def test_fans_tab_has_fan_table(qtbot):
    page = _make_page(qtbot)
    table = page.findChild(QTableWidget, "Diagnostics_Table_fans")
    assert table is not None
    assert table.columnCount() == 6


def test_fans_tab_has_no_splitter(qtbot):
    # DEC-124: the old Diagnostics_Splitter_fans is gone with the readiness card.
    page = _make_page(qtbot)
    assert page.findChild(QSplitter, "Diagnostics_Splitter_fans") is None


def test_readiness_frame_not_in_fans_tab_but_on_page(qtbot):
    page = _make_page(qtbot)
    fans_tab = page._tabs.widget(FANS_TAB_INDEX)
    assert page._tabs.tabText(FANS_TAB_INDEX) == "Fans"
    # The readiness card is not under the Fans tab...
    assert fans_tab.findChild(QFrame, "Diagnostics_Frame_hwReadiness") is None
    # ...but it does exist elsewhere on the page (the Troubleshooting tab).
    assert page.findChild(QFrame, "Diagnostics_Frame_hwReadiness") is not None
