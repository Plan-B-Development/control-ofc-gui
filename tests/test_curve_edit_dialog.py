"""Tests for the CurveEditDialog widget."""

from __future__ import annotations

import pytest

from onlyfans.services.profile_service import CurveConfig, CurveType
from onlyfans.ui.widgets.curve_edit_dialog import CurveEditDialog

SENSOR_ITEMS = [
    ("hwmon:k10temp:0:Tctl", "CPU Tctl"),
    ("hwmon:amdgpu:0:edge", "GPU Edge"),
    ("hwmon:nct6775:0:SYSTIN", "System"),
]


@pytest.fixture()
def linear_curve():
    return CurveConfig(
        id="lin01",
        name="Silent",
        type=CurveType.LINEAR,
        sensor_id="hwmon:k10temp:0:Tctl",
        start_temp_c=35.0,
        start_output_pct=25.0,
        end_temp_c=75.0,
        end_output_pct=90.0,
    )


@pytest.fixture()
def flat_curve():
    return CurveConfig(
        id="flat01",
        name="FullBlast",
        type=CurveType.FLAT,
        sensor_id="hwmon:amdgpu:0:edge",
        flat_output_pct=80.0,
    )


class TestLinearDialog:
    def test_linear_dialog_shows_correct_values(self, qtbot, linear_curve):
        dlg = CurveEditDialog(linear_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        assert dlg._param_spins["start_temp_c"].value() == 35.0
        assert dlg._param_spins["start_output_pct"].value() == 25.0
        assert dlg._param_spins["end_temp_c"].value() == 75.0
        assert dlg._param_spins["end_output_pct"].value() == 90.0

    def test_apply_to_curve_linear(self, qtbot, linear_curve):
        dlg = CurveEditDialog(linear_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        dlg._param_spins["start_temp_c"].setValue(40.0)
        dlg._param_spins["start_output_pct"].setValue(30.0)
        dlg._param_spins["end_temp_c"].setValue(85.0)
        dlg._param_spins["end_output_pct"].setValue(95.0)
        dlg.apply_to_curve()

        assert linear_curve.start_temp_c == 40.0
        assert linear_curve.start_output_pct == 30.0
        assert linear_curve.end_temp_c == 85.0
        assert linear_curve.end_output_pct == 95.0


class TestFlatDialog:
    def test_flat_dialog_shows_correct_values(self, qtbot, flat_curve):
        dlg = CurveEditDialog(flat_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        assert dlg._flat_spin.value() == 80.0

    def test_apply_to_curve_flat(self, qtbot, flat_curve):
        dlg = CurveEditDialog(flat_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        dlg._flat_spin.setValue(65.0)
        dlg.apply_to_curve()

        assert flat_curve.flat_output_pct == 65.0


class TestSensorCombo:
    def test_sensor_combo_populated(self, qtbot, linear_curve):
        dlg = CurveEditDialog(linear_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        assert dlg._sensor_combo.count() == 3
        assert dlg._sensor_combo.itemData(0) == "hwmon:k10temp:0:Tctl"
        assert dlg._sensor_combo.itemData(1) == "hwmon:amdgpu:0:edge"
        assert dlg._sensor_combo.itemData(2) == "hwmon:nct6775:0:SYSTIN"

    def test_sensor_combo_selects_current(self, qtbot, linear_curve):
        dlg = CurveEditDialog(linear_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        assert dlg._sensor_combo.currentData() == "hwmon:k10temp:0:Tctl"
        assert dlg._sensor_combo.currentText() == "CPU Tctl"

    def test_apply_to_curve_updates_sensor(self, qtbot, linear_curve):
        dlg = CurveEditDialog(linear_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        dlg._sensor_combo.setCurrentIndex(1)
        dlg.apply_to_curve()

        assert linear_curve.sensor_id == "hwmon:amdgpu:0:edge"


class TestNameAndButtons:
    def test_apply_to_curve_updates_name(self, qtbot, linear_curve):
        dlg = CurveEditDialog(linear_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        dlg._name_edit.setText("Quiet Night")
        dlg.apply_to_curve()

        assert linear_curve.name == "Quiet Night"

    def test_save_button_exists(self, qtbot, linear_curve):
        from PySide6.QtWidgets import QPushButton

        dlg = CurveEditDialog(linear_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        save_btn = dlg.findChild(QPushButton, "CurveEditDialog_Btn_save")
        assert save_btn is not None
        assert save_btn.text() == "Save"
