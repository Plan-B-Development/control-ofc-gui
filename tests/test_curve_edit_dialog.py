"""Tests for the CurveEditDialog widget."""

from __future__ import annotations

import pytest

from control_ofc.services.profile_service import CurveConfig, CurveType
from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

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


@pytest.fixture()
def trigger_curve():
    return CurveConfig(
        id="trg01",
        name="Latch",
        type=CurveType.TRIGGER,
        sensor_id="hwmon:k10temp:0:Tctl",
        trigger_idle_temp_c=40.0,
        trigger_load_temp_c=60.0,
        trigger_idle_pct=30.0,
        trigger_load_pct=80.0,
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


class TestTriggerDialog:
    def test_trigger_dialog_shows_correct_values(self, qtbot, trigger_curve):
        dlg = CurveEditDialog(trigger_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        assert dlg._param_spins["trigger_idle_temp_c"].value() == 40.0
        assert dlg._param_spins["trigger_load_temp_c"].value() == 60.0
        assert dlg._param_spins["trigger_idle_pct"].value() == 30.0
        assert dlg._param_spins["trigger_load_pct"].value() == 80.0

    def test_apply_to_curve_trigger(self, qtbot, trigger_curve):
        dlg = CurveEditDialog(trigger_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        dlg._param_spins["trigger_idle_temp_c"].setValue(35.0)
        dlg._param_spins["trigger_load_temp_c"].setValue(65.0)
        dlg._param_spins["trigger_idle_pct"].setValue(20.0)
        dlg._param_spins["trigger_load_pct"].setValue(90.0)
        dlg.apply_to_curve()

        assert trigger_curve.trigger_idle_temp_c == 35.0
        assert trigger_curve.trigger_load_temp_c == 65.0
        assert trigger_curve.trigger_idle_pct == 20.0
        assert trigger_curve.trigger_load_pct == 90.0

    def test_accept_rejects_idle_at_or_above_load(self, qtbot, trigger_curve):
        dlg = CurveEditDialog(trigger_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        dlg._param_spins["trigger_idle_temp_c"].setValue(70.0)  # >= load
        dlg._param_spins["trigger_load_temp_c"].setValue(60.0)
        dlg.accept()
        assert dlg.result() == 0  # QDialog.Rejected — validation blocked accept

    def test_accept_allows_valid_thresholds(self, qtbot, trigger_curve):
        dlg = CurveEditDialog(trigger_curve, sensor_items=SENSOR_ITEMS)
        qtbot.addWidget(dlg)

        dlg._param_spins["trigger_idle_temp_c"].setValue(40.0)
        dlg._param_spins["trigger_load_temp_c"].setValue(60.0)
        dlg.accept()
        assert dlg.result() == 1  # QDialog.Accepted


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


class TestMixDialog:
    """DEC-150: Mix has no sensor selector — it offers a function dropdown and a
    checkable list of candidate curves."""

    def _mix_curve(self):
        return CurveConfig(
            id="mx", name="Mix", type=CurveType.MIX, mix_function="max", mix_curve_ids=["a"]
        )

    def test_no_sensor_combo_for_mix(self, qtbot):
        dlg = CurveEditDialog(self._mix_curve(), sensor_items=SENSOR_ITEMS, mix_candidates=[])
        qtbot.addWidget(dlg)
        assert dlg._sensor_combo is None

    def test_checklist_preselects_current_children(self, qtbot):
        from PySide6.QtCore import Qt

        dlg = CurveEditDialog(
            self._mix_curve(),
            mix_candidates=[("a", "Curve A"), ("b", "Curve B")],
        )
        qtbot.addWidget(dlg)
        states = {
            dlg._mix_list.item(i).data(Qt.ItemDataRole.UserRole): dlg._mix_list.item(i).checkState()
            for i in range(dlg._mix_list.count())
        }
        assert states["a"] == Qt.CheckState.Checked
        assert states["b"] == Qt.CheckState.Unchecked

    def test_apply_writes_function_and_checked_ids(self, qtbot):
        from PySide6.QtCore import Qt

        curve = self._mix_curve()
        dlg = CurveEditDialog(curve, mix_candidates=[("a", "A"), ("b", "B")])
        qtbot.addWidget(dlg)
        dlg._mix_function_combo.setCurrentIndex(dlg._mix_function_combo.findData("average"))
        dlg._mix_list.item(1).setCheckState(Qt.CheckState.Checked)  # add b
        dlg.apply_to_curve()
        assert curve.mix_function == "average"
        assert curve.mix_curve_ids == ["a", "b"]


class TestSyncDialog:
    """DEC-151: Sync has no sensor selector — a control dropdown plus an offset."""

    def _sync_curve(self):
        return CurveConfig(
            id="sy", name="Sync", type=CurveType.SYNC, sync_control_id="c2", sync_offset_pct=10.0
        )

    def test_no_sensor_combo_for_sync(self, qtbot):
        dlg = CurveEditDialog(self._sync_curve(), sync_candidates=[("c1", "One"), ("c2", "Two")])
        qtbot.addWidget(dlg)
        assert dlg._sensor_combo is None

    def test_control_combo_selects_current(self, qtbot):
        dlg = CurveEditDialog(self._sync_curve(), sync_candidates=[("c1", "One"), ("c2", "Two")])
        qtbot.addWidget(dlg)
        assert dlg._sync_control_combo.currentData() == "c2"

    def test_apply_writes_control_and_offset(self, qtbot):
        curve = self._sync_curve()
        dlg = CurveEditDialog(curve, sync_candidates=[("c1", "One"), ("c2", "Two")])
        qtbot.addWidget(dlg)
        dlg._sync_control_combo.setCurrentIndex(dlg._sync_control_combo.findData("c1"))
        dlg._sync_offset_spin.setValue(-7.5)
        dlg.apply_to_curve()
        assert curve.sync_control_id == "c1"
        assert curve.sync_offset_pct == -7.5
