"""A Flat curve reads no sensor (daemon: flat resolves without a sensor lookup).

Until the daemon fix a flat curve was evaluated through the single-sensor path,
so the GUI offered a sensor for it. Now that the daemon ignores a flat curve's
``sensor_id``, the GUI must neither offer one for editing nor show a reading as
if it drove the curve. ``CurveConfig.reads_sensor`` is the one rule all three
surfaces use.
"""

from __future__ import annotations

from control_ofc.api.models import SensorReading
from control_ofc.services.profile_service import CurveConfig, CurvePoint, CurveType
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

# The daemon's dispatcher: these four look their sensor up; flat, mix and sync
# do not (`curve_eval.rs::curve_output_for_control`).
_DAEMON_SENSOR_TYPES = {CurveType.GRAPH, CurveType.STEPPED, CurveType.LINEAR, CurveType.TRIGGER}

_SENSORS = [("cpu0", "CPU Tctl"), ("gpu0", "GPU edge")]


def test_reads_sensor_classifies_every_curve_type_as_the_daemon_does():
    for curve_type in CurveType:
        curve = CurveConfig(type=curve_type)
        assert curve.reads_sensor is (curve_type in _DAEMON_SENSOR_TYPES), curve_type


class TestEditDialog:
    def test_flat_dialog_offers_no_sensor_and_keeps_the_one_it_has(self, qtbot):
        curve = CurveConfig(type=CurveType.FLAT, sensor_id="gpu0", flat_output_pct=80.0)
        dlg = CurveEditDialog(curve, sensor_items=_SENSORS)
        qtbot.addWidget(dlg)
        assert dlg._sensor_combo is None
        dlg.apply_to_curve()
        assert curve.sensor_id == "gpu0"  # untouched, not overwritten by a picker

    def test_linear_dialog_still_offers_a_sensor(self, qtbot):
        curve = CurveConfig(type=CurveType.LINEAR, sensor_id="gpu0")
        dlg = CurveEditDialog(curve, sensor_items=_SENSORS)
        qtbot.addWidget(dlg)
        assert dlg._sensor_combo is not None
        assert dlg._sensor_combo.currentData() == "gpu0"


def test_controls_curve_card_shows_no_reading_for_a_flat_curves_kept_sensor(
    qtbot, app_state, profile_service
):
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    profile = page._get_current_profile()
    profile.curves.append(CurveConfig(id="fl", name="Fixed", type=CurveType.FLAT, sensor_id="cpu0"))
    profile.curves.append(
        CurveConfig(
            id="gr",
            name="Graph",
            type=CurveType.GRAPH,
            sensor_id="cpu0",
            points=[CurvePoint(30, 20), CurvePoint(80, 100)],
        )
    )
    page._refresh_all()

    app_state.set_sensors([SensorReading(id="cpu0", label="Tctl", value_c=61.4)])

    assert "61.4" in page._curve_cards["gr"]._sensor_label.text()  # precondition: wired
    assert page._curve_cards["fl"]._sensor_label.text() == "No sensor"
