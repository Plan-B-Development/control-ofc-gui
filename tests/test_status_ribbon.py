"""DEC-208: the global status ribbon (redesign header)."""

from __future__ import annotations

from control_ofc.api.models import ConnectionState
from control_ofc.ui.status_ribbon import StatusRibbon, format_uptime


def test_format_uptime_buckets():
    assert format_uptime(None) == "—"
    assert format_uptime(-5) == "—"
    assert format_uptime(9) == "9s"
    assert format_uptime(69) == "1m 9s"
    assert format_uptime(3600 + 28 * 60 + 9) == "1h 28m 9s"


def test_ribbon_brand_and_objectname(qtbot):
    ribbon = StatusRibbon()
    qtbot.addWidget(ribbon)
    assert ribbon.objectName() == "StatusRibbon_Root"
    assert ribbon._brand_text.text() == "Control-OFC"
    assert ribbon._brand_text.objectName() == "StatusRibbon_Brand_text"


def test_ribbon_connection_state_updates_label_and_led(qtbot):
    ribbon = StatusRibbon()
    qtbot.addWidget(ribbon)
    ribbon.set_connection_state(ConnectionState.CONNECTED)
    assert ribbon._daemon_label.text() == "Connected"
    assert ribbon._daemon_label.property("class") == "SuccessChip"
    assert ribbon._daemon_led._role == "ok"
    ribbon.set_connection_state(ConnectionState.DISCONNECTED)
    assert ribbon._daemon_label.property("class") == "CriticalChip"
    assert ribbon._daemon_led._role == "crit"


def test_ribbon_thermal_state_shows_and_hides(qtbot):
    ribbon = StatusRibbon()
    qtbot.addWidget(ribbon)
    ribbon.set_thermal_state("emergency")
    assert not ribbon._thermal_pill.isHidden()
    assert ribbon._thermal_pill.state() == "critical"
    ribbon.set_thermal_state(None)
    assert ribbon._thermal_pill.isHidden()
    ribbon.set_thermal_state("unrecognised")  # unknown -> stays hidden, not shown blank
    assert ribbon._thermal_pill.isHidden()


def test_ribbon_thermal_pill_text_reflects_state(qtbot):
    """Audit 2026-07-29 4.1: the pill label + state must track the thermal state,
    not just its visibility (the wiring was exercised but the pill contents were
    never asserted)."""
    ribbon = StatusRibbon()
    qtbot.addWidget(ribbon)
    # StatusPill renders its label upper-cased.
    ribbon.set_thermal_state("normal")
    assert ribbon._thermal_pill.text() == "THERMAL OK"
    assert ribbon._thermal_pill.state() == "ok"
    ribbon.set_thermal_state("recovery")
    assert ribbon._thermal_pill.text() == "THERMAL: RECOVERY"
    assert ribbon._thermal_pill.state() == "warning"
    ribbon.set_thermal_state("emergency")
    assert ribbon._thermal_pill.text() == "THERMAL: EMERGENCY"
    assert ribbon._thermal_pill.state() == "critical"


def test_ribbon_uptime(qtbot):
    ribbon = StatusRibbon()
    qtbot.addWidget(ribbon)
    ribbon.set_uptime(3661)
    assert ribbon._uptime_label.text() == "1h 1m 1s"


def test_ribbon_warning_count_badge(qtbot):
    ribbon = StatusRibbon()
    qtbot.addWidget(ribbon)
    assert ribbon._alert_badge.isHidden()
    ribbon.set_warning_count(3)
    assert not ribbon._alert_badge.isHidden()
    assert ribbon._alert_badge.text() == "3"
    ribbon.set_warning_count(0)
    assert ribbon._alert_badge.isHidden()


def test_ribbon_alerts_clicked_signal(qtbot):
    ribbon = StatusRibbon()
    qtbot.addWidget(ribbon)
    with qtbot.waitSignal(ribbon.alerts_clicked):
        ribbon._alerts_btn.click()
