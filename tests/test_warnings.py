"""Tests for the warnings workflow — dialog, clearing, fan filtering, diagnostics."""

from __future__ import annotations

import pytest

from control_ofc.api.models import ConnectionState, FanReading, OperationMode, SensorReading
from control_ofc.services.app_state import AppState


@pytest.fixture()
def warn_state():
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


class TestWarningCount:
    def test_stale_sensor_creates_warning(self, warn_state):
        warn_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=5000),
            ]
        )
        assert warn_state.warning_count == 1
        assert len(warn_state.active_warnings) == 1
        assert "stale" in warn_state.active_warnings[0]["message"].lower()

    def test_stalled_fan_creates_warning(self, warn_state):
        warn_state.set_fans(
            [
                FanReading(id="f1", source="openfan", rpm=0, age_ms=50, stall_detected=True),
            ]
        )
        assert warn_state.warning_count >= 1
        stall_warnings = [w for w in warn_state.active_warnings if "stall" in w["message"].lower()]
        assert len(stall_warnings) == 1

    def test_fresh_data_no_warnings(self, warn_state):
        warn_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=100),
            ]
        )
        warn_state.set_fans(
            [
                FanReading(id="f1", source="openfan", rpm=1200, age_ms=100),
            ]
        )
        assert warn_state.warning_count == 0
        assert len(warn_state.active_warnings) == 0


class TestClearWarnings:
    def test_clear_resets_count(self, warn_state):
        warn_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=5000),
            ]
        )
        assert warn_state.warning_count == 1
        warn_state.clear_warnings()
        assert warn_state.warning_count == 0
        assert len(warn_state.active_warnings) == 0

    def test_clear_emits_signal(self, qtbot, warn_state):
        warn_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=5000),
            ]
        )
        with qtbot.waitSignal(warn_state.warnings_cleared, timeout=500):
            warn_state.clear_warnings()

    def test_clear_acknowledges_warnings(self, warn_state):
        """After clear, the same stale sensor doesn't re-trigger the count."""
        warn_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=5000),
            ]
        )
        assert warn_state.warning_count == 1
        warn_state.clear_warnings()
        # Re-update with same stale sensor
        warn_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=5000),
            ]
        )
        assert warn_state.warning_count == 0  # acknowledged, doesn't re-trigger

    def test_clear_when_empty_is_safe(self, warn_state):
        warn_state.clear_warnings()  # no crash
        assert warn_state.warning_count == 0

    def test_new_warning_after_clear_triggers(self, warn_state):
        """A genuinely new warning (different ID) triggers after clear."""
        warn_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=5000),
            ]
        )
        warn_state.clear_warnings()
        # New sensor that's also stale
        warn_state.set_sensors(
            [
                SensorReading(id="s2", label="GPU", kind="GpuTemp", value_c=60.0, age_ms=5000),
            ]
        )
        assert warn_state.warning_count == 1  # new warning from s2


class TestWarningsDialog:
    def test_dialog_shows_warnings(self, qtbot, warn_state):
        from control_ofc.ui.widgets.warnings_dialog import WarningsDialog

        warn_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=5000),
            ]
        )
        dialog = WarningsDialog(warn_state)
        qtbot.addWidget(dialog)
        assert dialog._table.rowCount() == 1
        assert "stale" in dialog._table.item(0, 3).text().lower()

    def test_dialog_empty_state(self, qtbot, warn_state):
        from control_ofc.ui.widgets.warnings_dialog import WarningsDialog

        dialog = WarningsDialog(warn_state)
        qtbot.addWidget(dialog)
        # The dialog should not crash and should show something meaningful
        assert dialog is not None
        assert not hasattr(dialog, "_table")  # no table when empty

    def test_dialog_clear_resets_count(self, qtbot, warn_state):
        from control_ofc.ui.widgets.warnings_dialog import WarningsDialog

        warn_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=5000),
            ]
        )
        dialog = WarningsDialog(warn_state)
        qtbot.addWidget(dialog)
        dialog._on_clear()
        assert warn_state.warning_count == 0


class TestDashboardFanFiltering:
    def test_fan_with_rpm_zero_is_hidden(self, qtbot):
        """RPM=0 (no spinning evidence) should be hidden from dashboard."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        fans = [FanReading(id="openfan:ch05", source="openfan", rpm=0, age_ms=50)]
        state.set_fans(fans)

        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        page._on_fans_updated(fans)
        assert len(page._fan_ids) == 0  # RPM=0 → not displayable

    def test_fan_with_rpm_positive_is_shown(self, qtbot):
        """Fan with real RPM > 0 should be shown."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        fans = [FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=50)]
        state.set_fans(fans)

        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        page._on_fans_updated(fans)
        assert len(page._fan_ids) == 1

    def test_fan_with_rpm_none_is_hidden(self, qtbot):
        """RPM=None (no tach) hwmon fan should be hidden from dashboard."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        fans = [FanReading(id="hwmon:test", source="hwmon", rpm=None, age_ms=50)]
        state.set_fans(fans)

        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        page._on_fans_updated(fans)
        assert len(page._fan_ids) == 0

    def test_labeled_fan_with_rpm_zero_is_shown(self, qtbot):
        """User-labeled fan should show even with RPM=0 (deliberate stop)."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.fan_aliases = {"openfan:ch05": "CPU Pump"}
        fans = [FanReading(id="openfan:ch05", source="openfan", rpm=0, age_ms=50)]
        state.set_fans(fans)

        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        page._on_fans_updated(fans)
        assert len(page._fan_ids) == 1  # labeled → displayable

    def test_actively_controlled_fan_is_shown(self, qtbot):
        """Fan with PWM > 0 should show even if RPM=0 (fan starting up)."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        fans = [
            FanReading(id="openfan:ch01", source="openfan", rpm=0, age_ms=50, last_commanded_pwm=50)
        ]
        state.set_fans(fans)

        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        page._on_fans_updated(fans)
        assert len(page._fan_ids) == 1  # PWM>0 → displayable

    def test_mixed_fans_filtering(self, qtbot):
        """Mix of populated and empty fans — only populated shown."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        fans = [
            FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=50),  # spinning
            FanReading(id="openfan:ch01", source="openfan", rpm=0, age_ms=50),  # empty
            FanReading(id="openfan:ch02", source="openfan", rpm=0, age_ms=50),  # empty
            FanReading(id="hwmon:fan1", source="hwmon", rpm=800, age_ms=50),  # spinning
            FanReading(id="hwmon:fan2", source="hwmon", rpm=0, age_ms=50),  # empty
        ]
        state.set_fans(fans)

        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        page._on_fans_updated(fans)
        assert len(page._fan_ids) == 2  # only ch00 and hwmon:fan1
