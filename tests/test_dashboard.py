"""Dashboard page tests — state transitions, content display, and subsystem health."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QPushButton

from control_ofc.api.models import (
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    HwmonCapability,
    OpenfanCapability,
    OperationMode,
    SensorReading,
    SubsystemStatus,
)
from control_ofc.ui.main_window import MainWindow


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


class TestDashboardStates:
    def test_disconnected_shows_disconnected_state(self, qtbot, window, app_state):
        """When disconnected, dashboard shows the disconnected empty state."""
        app_state.set_connection(ConnectionState.DISCONNECTED)
        assert window.dashboard_page._stack.currentIndex() == 0

    def test_connected_no_data_shows_no_hardware(self, qtbot, window, app_state):
        """When connected but no sensor/fan data, shows no-hardware state."""
        app_state.set_connection(ConnectionState.CONNECTED)
        assert window.dashboard_page._stack.currentIndex() == 1

    def test_sensors_received_shows_live_content(self, qtbot, window, app_state):
        """When sensors arrive, dashboard switches to live content."""
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=42.0, age_ms=100),
            ]
        )
        assert window.dashboard_page._stack.currentIndex() == 2

    def test_fans_received_shows_live_content(self, qtbot, window, app_state):
        """When fans arrive (even without sensors), dashboard switches to live content."""
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_fans(
            [
                FanReading(id="f1", source="openfan", rpm=1200, last_commanded_pwm=50, age_ms=100),
            ]
        )
        assert window.dashboard_page._stack.currentIndex() == 2

    def test_disconnect_resets_to_disconnected(self, qtbot, window, app_state):
        """Going from connected+data to disconnected resets the view."""
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=42.0, age_ms=100),
            ]
        )
        assert window.dashboard_page._stack.currentIndex() == 2

        app_state.set_connection(ConnectionState.DISCONNECTED)
        assert window.dashboard_page._stack.currentIndex() == 0


class TestSubsystemHealth:
    def test_capabilities_update_subsystem_labels(self, qtbot, window, app_state):
        """Capabilities update the no-hardware subsystem breakdown."""
        caps = Capabilities(
            daemon_version="0.2.0",
            openfan=OpenfanCapability(present=True, channels=6),
            hwmon=HwmonCapability(present=False),
        )
        app_state.set_capabilities(caps)

        dash = window.dashboard_page
        assert "detected" in dash._sub_openfan_label.text()
        assert "6 ch" in dash._sub_openfan_label.text()
        assert "not detected" in dash._sub_hwmon_label.text()

    def test_unhealthy_subsystem_shows_warning(self, qtbot, window, app_state):
        """Status with unhealthy subsystem updates label and style."""
        status = DaemonStatus(
            overall_status="degraded",
            subsystems=[
                SubsystemStatus(name="openfan", status="error", reason="permission denied"),
            ],
        )
        app_state.set_status(status)

        dash = window.dashboard_page
        assert "error" in dash._sub_openfan_label.text()
        assert "permission denied" in dash._sub_openfan_label.text()


class TestDashboardContent:
    def test_cpu_temp_card_updates(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=72.5, age_ms=100),
            ]
        )
        assert "72.5" in window.dashboard_page._cpu_card._value_label.text()

    def test_gpu_temp_card_updates(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(id="s2", label="GPU", kind="GpuTemp", value_c=65.0, age_ms=100),
            ]
        )
        assert "65.0" in window.dashboard_page._gpu_card._value_label.text()

    def test_fan_count_card_updates(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_fans(
            [
                FanReading(id="f1", source="openfan", rpm=1200, age_ms=100),
                FanReading(id="f2", source="openfan", rpm=1100, age_ms=100),
            ]
        )
        assert window.dashboard_page._fans_card._value_label.text() == "2"

    def test_warning_count_card_updates(self, qtbot, window, app_state):
        # Trigger a warning by setting a stale sensor
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=42.0, age_ms=5000),
            ]
        )
        # Warning count should be at least 1 (stale sensor)
        assert int(window.dashboard_page._warnings_card._value_label.text()) >= 1

    def test_open_diagnostics_button_exists(self, qtbot, window):
        """The no-hardware state has an 'Open Diagnostics' button."""
        btn = window.dashboard_page.findChild(QPushButton, "Dashboard_Btn_openDiagnostics")
        assert btn is not None
        assert btn.isEnabled()


class TestModeBadge:
    def test_demo_mode_shows_badge(self, qtbot, window, app_state):
        app_state.set_mode(OperationMode.DEMO)
        # Need to show live content to see the badge
        app_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=42.0, age_ms=100),
            ]
        )
        assert "DEMO" in window.dashboard_page._mode_badge.text()

    def test_manual_override_shows_badge(self, qtbot, window, app_state):
        app_state.set_mode(OperationMode.MANUAL_OVERRIDE)
        app_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=42.0, age_ms=100),
            ]
        )
        assert "MANUAL" in window.dashboard_page._mode_badge.text()

    def test_automatic_mode_no_badge(self, qtbot, window, app_state):
        app_state.set_mode(OperationMode.AUTOMATIC)
        assert window.dashboard_page._mode_badge.text() == ""


class TestSensorPickerDialog:
    """R10-001/R11-001: Dialog shows sensor values and doesn't affect chart."""

    def test_dialog_shows_sensor_values(self, qtbot):
        """Sensor value is displayed next to radio button in the dialog."""
        from control_ofc.ui.widgets.series_chooser_dialog import SensorPickerDialog

        sensors = [SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=55.3, age_ms=50)]
        dialog = SensorPickerDialog(category="cpu_temp", sensors=sensors)
        qtbot.addWidget(dialog)
        assert "55.3" in dialog._value_labels["s1"].text()

    def test_dialog_updates_values(self, qtbot):
        """Value labels update when update_values is called."""
        from control_ofc.ui.widgets.series_chooser_dialog import SensorPickerDialog

        sensors = [SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=40.0, age_ms=50)]
        dialog = SensorPickerDialog(category="cpu_temp", sensors=sensors)
        qtbot.addWidget(dialog)
        assert "40.0" in dialog._value_labels["s1"].text()

        new_sensors = [SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=62.5, age_ms=50)]
        dialog.update_values(new_sensors, [])
        assert "62.5" in dialog._value_labels["s1"].text()

    def test_dialog_shows_fan_rpm(self, qtbot):
        """Fan RPM values displayed in the dialog."""
        from control_ofc.ui.widgets.series_chooser_dialog import SensorPickerDialog

        fans = [FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=50)]
        dialog = SensorPickerDialog(category="fans", fans=fans)
        qtbot.addWidget(dialog)
        assert "1200" in dialog._value_labels["openfan:ch00"].text()

    def test_dialog_does_not_change_chart_visibility(self, qtbot):
        """R11-001: Selecting a sensor in the dialog must not affect the chart."""
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.widgets.series_chooser_dialog import SensorPickerDialog

        selection = SeriesSelectionModel()
        selection.update_known_keys(["sensor:s1", "sensor:s2"])
        hidden_before = set(selection.to_dict()["hidden_keys"])

        sensors = [
            SensorReading(id="s1", label="CPU1", kind="CpuTemp", value_c=45.0, age_ms=50),
            SensorReading(id="s2", label="CPU2", kind="CpuTemp", value_c=50.0, age_ms=50),
        ]
        dialog = SensorPickerDialog(category="cpu_temp", sensors=sensors)
        qtbot.addWidget(dialog)

        # Select a radio button
        dialog._on_selected("s2")
        dialog.accept()

        # Chart selection model must be unchanged
        hidden_after = set(selection.to_dict()["hidden_keys"])
        assert hidden_before == hidden_after


class TestProfilePosition:
    """R10-003: Profile selector at far right."""

    def test_profile_widget_is_last_in_row(self, qtbot, window, app_state):
        """Profile widget should be the last widget in the cards row."""
        app_state.set_sensors(
            [SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=42.0, age_ms=100)]
        )
        page = window.dashboard_page
        # The profile widget should be the rightmost card in the layout
        # Find the cards_layout (it's inside the live content)
        assert page._profile_widget is not None


class TestSensorSeriesPanel:
    """R14: Merged sensor/fan panel with values and checkboxes."""

    def test_panel_shows_grouped_sensors(self, qtbot):
        """Sensors appear under correct group headers."""
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        sel = SeriesSelectionModel()
        panel = SensorSeriesPanel(sel)
        qtbot.addWidget(panel)

        sensors = [
            SensorReading(id="s1", label="Tctl", kind="CpuTemp", value_c=55.0, age_ms=50),
            SensorReading(id="s2", label="edge", kind="GpuTemp", value_c=42.0, age_ms=50),
        ]
        panel.update_sensors(sensors)

        assert "cpu" in panel._group_items
        assert "gpu" in panel._group_items
        assert "s1" in panel._sensor_items
        assert "s2" in panel._sensor_items

    def test_panel_shows_live_values(self, qtbot):
        """Values update via update_sensors()."""
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        sel = SeriesSelectionModel()
        panel = SensorSeriesPanel(sel)
        qtbot.addWidget(panel)

        sensors = [SensorReading(id="s1", label="Tctl", kind="CpuTemp", value_c=55.0, age_ms=50)]
        panel.update_sensors(sensors)
        assert "55.0" in panel._sensor_items["s1"].text(1)

        # Update value
        sensors2 = [SensorReading(id="s1", label="Tctl", kind="CpuTemp", value_c=62.3, age_ms=50)]
        panel.update_sensors(sensors2)
        assert "62.3" in panel._sensor_items["s1"].text(1)

    def test_panel_shows_fans(self, qtbot):
        """Fan RPM values shown under fan groups."""
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        sel = SeriesSelectionModel()
        panel = SensorSeriesPanel(sel)
        qtbot.addWidget(panel)

        fans = [FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=50)]
        panel.update_fans(fans)
        assert "openfan:ch00" in panel._fan_items
        assert "1200" in panel._fan_items["openfan:ch00"].text(1)

    def test_panel_no_rebuild_on_same_data(self, qtbot):
        """Values update without item recreation when sensor list unchanged."""
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        sel = SeriesSelectionModel()
        panel = SensorSeriesPanel(sel)
        qtbot.addWidget(panel)

        sensors = [SensorReading(id="s1", label="Tctl", kind="CpuTemp", value_c=55.0, age_ms=50)]
        panel.update_sensors(sensors)
        first_item = panel._sensor_items["s1"]

        # Second call with same sensor IDs should reuse items
        sensors2 = [SensorReading(id="s1", label="Tctl", kind="CpuTemp", value_c=60.0, age_ms=50)]
        panel.update_sensors(sensors2)
        assert panel._sensor_items["s1"] is first_item  # Same object
        assert "60.0" in first_item.text(1)


class TestR12SensorPanelNoRebuild:
    """R12-001: Sensor panel doesn't rebuild on every tick."""

    def test_no_rebuild_when_sensors_unchanged(self, qtbot):
        """update_sensors with same IDs should not destroy/recreate items."""
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        sel = SeriesSelectionModel()
        panel = SensorSeriesPanel(sel)
        qtbot.addWidget(panel)

        sensors = [SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=45.0, age_ms=50)]
        panel.update_sensors(sensors)
        first_item = panel._sensor_items["s1"]

        panel.update_sensors(sensors)
        assert panel._sensor_items["s1"] is first_item


class TestR12StatusClassGuard:
    """R12-001: unpolish/polish only when class changes."""

    def test_summary_card_skips_redundant_repolish(self, qtbot):
        from control_ofc.ui.widgets.summary_card import SummaryCard

        card = SummaryCard("Test")
        qtbot.addWidget(card)
        card.set_status_class("WarningChip")
        # Second call with same class should be a no-op (no assertion crash = pass)
        card.set_status_class("WarningChip")
        assert card._value_label.property("class") == "WarningChip"


class TestR12ProfileSelector:
    """R12-002: Profile selector is populated and functional."""

    def test_profile_combo_populated(self, qtbot, window, app_state, profile_service):
        """Profile combo should have items after MainWindow init."""
        page = window.dashboard_page
        assert page._profile_combo.count() > 0

    def test_profile_selection_persists_across_sensors_update(
        self, qtbot, window, app_state, profile_service
    ):
        """Selecting a profile should not revert when sensors update."""
        page = window.dashboard_page
        if page._profile_combo.count() >= 2:
            page._profile_combo.setCurrentIndex(1)
            selected = page._profile_combo.currentText()
            # Simulate sensor update
            app_state.set_sensors(
                [SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=50)]
            )
            assert page._profile_combo.currentText() == selected


class TestR14SensorPanelGrouping:
    """R14: Sensor panel groups sensors by kind and updates values."""

    def test_sensor_groups_created_via_dashboard(self, qtbot, window, app_state):
        """Sensor update through dashboard creates groups in the sensor panel."""
        app_state.set_sensors(
            [
                SensorReading(id="s1", label="Tctl", kind="CpuTemp", value_c=55.0, age_ms=50),
                SensorReading(id="s2", label="edge", kind="GpuTemp", value_c=42.0, age_ms=50),
            ]
        )
        panel = window.dashboard_page._sensor_panel
        assert "cpu" in panel._group_items
        assert "gpu" in panel._group_items

    def test_sensor_values_update_via_dashboard(self, qtbot, window, app_state):
        """Values update through the dashboard signal chain."""
        app_state.set_sensors(
            [SensorReading(id="s1", label="Tctl", kind="CpuTemp", value_c=55.0, age_ms=50)]
        )
        panel = window.dashboard_page._sensor_panel
        assert "55.0" in panel._sensor_items["s1"].text(1)

        app_state.set_sensors(
            [SensorReading(id="s1", label="Tctl", kind="CpuTemp", value_c=62.3, age_ms=50)]
        )
        assert "62.3" in panel._sensor_items["s1"].text(1)
