"""Daemon-classification section in the sensor detail dialog (Phase 4 / DEC-200).

``build_sensor_detail_html`` is a pure HTML builder — tested directly, no widget.
"""

from __future__ import annotations

from control_ofc.api.models import InventoryTempSensor, SensorReading
from control_ofc.ui.widgets.sensor_detail_dialog import build_sensor_detail_html


def _sensor() -> SensorReading:
    return SensorReading(id="hwmon:k10temp:x:Tctl", label="Tctl", chip_name="k10temp", value_c=50.0)


def test_daemon_classification_section_present():
    dc = InventoryTempSensor(
        id="hwmon:k10temp:x:Tctl",
        classification="cpu_tctl",
        confidence="high",
        rationale="k10temp Tctl control temperature",
    )
    html = build_sensor_detail_html(_sensor(), None, dc)
    assert "Daemon classification" in html
    assert "cpu_tctl" in html
    assert "k10temp Tctl control temperature" in html


def test_daemon_classification_absent_when_not_provided():
    html = build_sensor_detail_html(_sensor(), None)
    assert "Daemon classification" not in html


def test_daemon_classification_rationale_is_escaped():
    dc = InventoryTempSensor(id="x", classification="cpu_tctl", rationale="<script>x</script>")
    html = build_sensor_detail_html(_sensor(), None, dc)
    # The daemon rationale is HTML-escaped, not injected as a live tag.
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
