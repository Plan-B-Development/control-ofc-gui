"""DEC-213: AlertsPanel — inline active-warnings list on the dashboard."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from control_ofc.services.app_state import AppState
from control_ofc.ui.widgets.dashboard_inspector import AlertsPanel


def test_empty_state(qtbot):
    panel = AlertsPanel(AppState())
    qtbot.addWidget(panel)
    assert panel.entry_count() == 0
    assert panel.findChild(QLabel, "Dashboard_Label_alertsEmpty") is not None
    assert panel._count_pill.text() == "0"
    assert panel._count_pill.state() == "ok"


def test_renders_warnings_with_count_and_severity(qtbot):
    state = AppState()
    state.add_warning("warning", "sensor", "CPU Core 3 — 83°C", key="sensor_hot")
    state.add_warning("error", "fan", "Fan stalled", key="fan_stall_ch2")
    panel = AlertsPanel(state)
    qtbot.addWidget(panel)
    assert panel.entry_count() == 2
    assert panel.findChild(QWidget, "Dashboard_Alert_0") is not None
    assert panel.findChild(QWidget, "Dashboard_Alert_1") is not None
    assert panel._count_pill.text() == "2"
    assert panel._count_pill.state() == "crit"  # an error present → critical


def test_refreshes_on_new_warning(qtbot):
    state = AppState()
    panel = AlertsPanel(state)
    qtbot.addWidget(panel)
    assert panel.entry_count() == 0
    state.add_warning("warning", "sensor", "hot", key="k1")
    assert panel.entry_count() == 1  # rebuilt via warning_count_changed
    assert panel._count_pill.state() == "warn"


def test_message_rendered_as_plain_text(qtbot):
    state = AppState()
    state.add_warning("warning", "sensor", "GPU Die — 79°C", key="gpu_hot")
    panel = AlertsPanel(state)
    qtbot.addWidget(panel)
    msg = panel.findChild(QLabel, "Dashboard_Alert_0_message")
    assert msg.text() == "GPU Die — 79°C"
    assert msg.textFormat() == Qt.TextFormat.PlainText


def test_panel_resists_vertical_compression(qtbot):
    """DEC-213: the panel holds its natural height (Minimum vertical policy) so a
    short right rail squeezes the tall sensor list — not these rows into an
    overlapping smear (the bug the smoke render caught)."""
    panel = AlertsPanel(AppState())
    qtbot.addWidget(panel)
    assert panel.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Minimum
