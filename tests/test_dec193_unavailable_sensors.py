"""DEC-193: control-eligibility filtering of the curve sensor picker.

A wireless-radio temperature (e.g. an ``ath12k`` WiFi chip) reads ``ENETDOWN``
whenever the radio is down, so the daemon flags it ``control_eligible = false``.
The Controls page must drop such sensors from the curve-source picker — a WiFi
temperature must never be selectable to drive a fan curve — while still showing
its live value for any curve already bound to it (backward compatibility).
"""

from __future__ import annotations

from control_ofc.api.models import SensorReading
from control_ofc.services.profile_service import CurveConfig, CurvePoint
from control_ofc.ui.pages.controls_page import ControlsPage


def _sensor(sid: str, *, control_eligible: bool = True, **kw) -> SensorReading:
    return SensorReading(
        id=sid,
        kind=kw.get("kind", "cpu_temp"),
        label=kw.get("label", sid.split(":")[-1]),
        value_c=kw.get("value_c", 45.0),
        source=kw.get("source", "hwmon"),
        age_ms=kw.get("age_ms", 100),
        chip_name=kw.get("chip_name", "k10temp"),
        control_eligible=control_eligible,
    )


def _picker_ids(page: ControlsPage) -> list[str]:
    combo = page._curve_editor._sensor_combo
    return [combo.itemData(i) for i in range(combo.count())]


def test_ineligible_sensor_excluded_from_curve_picker(qtbot, app_state, profile_service):
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)

    page._on_sensor_values_updated(
        [
            _sensor("hwmon:k10temp:nodev:Tctl"),
            _sensor(
                "hwmon:ath12k_hwmon:phy0:temp1",
                kind="mb_temp",
                chip_name="ath12k_hwmon",
                control_eligible=False,
            ),
        ]
    )

    ids = _picker_ids(page)
    assert "hwmon:k10temp:nodev:Tctl" in ids
    assert "hwmon:ath12k_hwmon:phy0:temp1" not in ids


def test_all_eligible_sensors_offered(qtbot, app_state, profile_service):
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)

    page._on_sensor_values_updated(
        [
            _sensor("hwmon:k10temp:nodev:Tctl"),
            _sensor("hwmon:nct6798:nodev:CPUTIN", kind="mb_temp", chip_name="nct6798"),
        ]
    )

    ids = _picker_ids(page)
    assert "hwmon:k10temp:nodev:Tctl" in ids
    assert "hwmon:nct6798:nodev:CPUTIN" in ids


def test_ineligible_bound_curve_still_shows_live_value(qtbot, app_state, profile_service):
    """An existing curve bound to a now-ineligible sensor keeps showing its live
    value — the filter only governs what is *offered*, never lookups by id."""
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)

    # Bind the editor's curve to the wireless sensor, then feed values.
    page._curve_editor.set_curve(
        CurveConfig(
            sensor_id="hwmon:ath12k_hwmon:phy0:temp1",
            points=[CurvePoint(30.0, 25.0), CurvePoint(60.0, 80.0)],
        )
    )

    page._on_sensor_values_updated(
        [
            _sensor(
                "hwmon:ath12k_hwmon:phy0:temp1",
                kind="mb_temp",
                chip_name="ath12k_hwmon",
                control_eligible=False,
                value_c=49.0,
            ),
        ]
    )

    # Not offered in the picker…
    assert "hwmon:ath12k_hwmon:phy0:temp1" not in _picker_ids(page)
    # …but its live value was still resolved for the bound curve.
    assert page._curve_editor._current_sensor_value == 49.0
