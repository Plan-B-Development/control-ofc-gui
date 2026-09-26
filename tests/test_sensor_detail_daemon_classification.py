"""Daemon-classification section in the sensor detail dialog (Phase 4 / DEC-200).

``build_sensor_detail_html`` is a pure HTML builder — tested directly, no widget.
"""

from __future__ import annotations

from control_ofc.api.models import InventoryTempSensor, SensorReading
from control_ofc.knowledge.sensor_knowledge import classify_sensor
from control_ofc.ui.widgets.sensor_detail_dialog import build_sensor_detail_html


def _sensor() -> SensorReading:
    return SensorReading(id="hwmon:k10temp:x:Tctl", label="Tctl", chip_name="k10temp", value_c=50.0)


def _html(sensor: SensorReading, dc=None) -> str:
    cls = classify_sensor(sensor.chip_name, sensor.label, sensor.temp_type)
    return build_sensor_detail_html(sensor, None, dc, classification=cls)


def test_daemon_classification_section_present():
    dc = InventoryTempSensor(
        id="hwmon:k10temp:x:Tctl",
        classification="cpu_tctl",
        confidence="high",
        rationale="k10temp Tctl control temperature",
    )
    html = _html(_sensor(), dc)
    assert "Daemon classification" in html
    assert "cpu_tctl" in html
    assert "k10temp Tctl control temperature" in html


def test_daemon_classification_absent_when_not_provided():
    html = _html(_sensor())
    assert "Daemon classification" not in html


def test_daemon_classification_rationale_is_escaped():
    dc = InventoryTempSensor(id="x", classification="cpu_tctl", rationale="<script>x</script>")
    html = _html(_sensor(), dc)
    # The daemon rationale is HTML-escaped, not injected as a live tag.
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
