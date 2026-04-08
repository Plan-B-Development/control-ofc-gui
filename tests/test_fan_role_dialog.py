"""Tests for FanRoleDialog — mode toggle, manual speed, result dict."""

from __future__ import annotations

import pytest

from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
)
from control_ofc.ui.widgets.fan_role_dialog import FanRoleDialog


@pytest.fixture()
def curves():
    return [
        CurveConfig(id="c1", name="Balanced", type=CurveType.GRAPH),
        CurveConfig(id="c2", name="Quiet", type=CurveType.FLAT, flat_output_pct=30.0),
    ]


@pytest.fixture()
def control():
    return LogicalControl(
        id="test_role",
        name="Test Role",
        mode=ControlMode.CURVE,
        curve_id="c1",
        manual_output_pct=65.0,
    )


@pytest.fixture()
def dialog(qtbot, control, curves):
    dlg = FanRoleDialog(control, curves)
    qtbot.addWidget(dlg)
    return dlg


class TestModeToggle:
    def test_curve_mode_hides_manual(self, dialog):
        """In Curve mode, manual widget is hidden, curve widget visible."""
        assert not dialog._curve_widget.isHidden()
        assert dialog._manual_widget.isHidden()

    def test_manual_mode_hides_curve(self, dialog):
        """Switching to Manual hides curve widget, shows manual widget."""
        dialog._mode_combo.setCurrentIndex(1)  # Manual
        assert dialog._curve_widget.isHidden()
        assert not dialog._manual_widget.isHidden()

    def test_mode_switch_back(self, dialog):
        """Switching back to Curve restores visibility."""
        dialog._mode_combo.setCurrentIndex(1)  # Manual
        dialog._mode_combo.setCurrentIndex(0)  # Curve
        assert not dialog._curve_widget.isHidden()
        assert dialog._manual_widget.isHidden()


class TestManualSpeed:
    def test_slider_spin_sync(self, dialog):
        """Slider and spin stay in sync."""
        dialog._mode_combo.setCurrentIndex(1)  # Manual
        dialog._manual_slider.setValue(75)
        assert dialog._manual_spin.value() == 75

        dialog._manual_spin.setValue(30)
        assert dialog._manual_slider.value() == 30

    def test_initial_value_from_control(self, dialog, control):
        """Slider starts at the control's manual_output_pct."""
        assert dialog._manual_slider.value() == 65
        assert dialog._manual_spin.value() == 65


class TestGetResult:
    def test_all_fields_present(self, dialog):
        """get_result returns name, mode, curve_id, manual_output_pct."""
        result = dialog.get_result()
        assert "name" in result
        assert "mode" in result
        assert "curve_id" in result
        assert "manual_output_pct" in result

    def test_curve_mode_result(self, dialog):
        result = dialog.get_result()
        assert result["mode"] == ControlMode.CURVE
        assert result["curve_id"] == "c1"

    def test_manual_mode_result(self, dialog):
        dialog._mode_combo.setCurrentIndex(1)
        dialog._manual_spin.setValue(42)
        result = dialog.get_result()
        assert result["mode"] == ControlMode.MANUAL
        assert result["manual_output_pct"] == 42.0

    def test_name_change(self, dialog):
        dialog._name_edit.setText("Renamed Role")
        result = dialog.get_result()
        assert result["name"] == "Renamed Role"

    def test_empty_name_reverts(self, dialog, control):
        dialog._name_edit.setText("")
        result = dialog.get_result()
        assert result["name"] == control.name
