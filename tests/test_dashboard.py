"""Dashboard page tests — state transitions, content display, and subsystem health."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

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
    parse_status,
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

    def test_a_wedged_engine_is_surfaced_on_the_dashboard(self, qtbot, window, app_state):
        """Release review, 2026-08-10.

        DEC-249 added an `engine` subsystem because the profile engine is the
        sole PWM writer and owns the 105 C emergency, so its death had been
        invisible behind a green /status. The Dashboard loop matched only
        openfan/hwmon, so the new signal was dropped on the page CLAUDE.md
        designates for "is the system healthy?" — the ADR's whole point, undone
        one layer above it.
        """
        status = DaemonStatus(
            overall_status="degraded",
            subsystems=[
                SubsystemStatus(name="engine", status="crit", reason="no tick for 31s"),
            ],
        )
        app_state.set_status(status)

        dash = window.dashboard_page
        assert not dash._engine_banner.isHidden(), (
            "a stalled sole-PWM-writer must be visible on the default page"
        )
        text = dash._engine_banner._message_label.text()
        assert "stopped" in text
        assert "no tick for 31s" in text
        assert "105" in text, "the user must be told thermal protection is gone too"

    def test_a_slow_engine_is_not_reported_as_a_dead_one(self, qtbot, window, app_state):
        """Release review round 2, 2026-08-10 — a P1 in the round-1 fix.

        The banner branched only on `== "ok"` and sent every other wire value
        down the critical path. The daemon's values are ok|warn|crit, and it
        added `warn` in DEC-259 for exactly this reason: reporting a slow tick
        as a stopped engine is, in the daemon's own words, "exactly inverted".

        The canonical slow tick is the 105 C force_all walking ten OpenFan
        channels at up to a second each — so the critical wording claimed
        thermal protection was off, and told the user to restart the sole PWM
        writer, at the moment it was saving their hardware.
        """
        status = DaemonStatus(
            overall_status="degraded",
            subsystems=[
                SubsystemStatus(
                    name="engine",
                    status="warn",
                    reason="tick still running - a slow write is holding it up",
                ),
            ],
        )
        app_state.set_status(status)

        dash = window.dashboard_page
        assert not dash._engine_banner.isHidden(), "a slow engine is still worth showing"
        text = dash._engine_banner._message_label.text()
        assert "stopped" not in text
        assert "not driving your fans" not in text
        assert "Restart" not in text, (
            "telling the user to restart the sole PWM writer mid-emergency is the "
            "worst possible advice"
        )
        assert "thermal protection is active" in text

    def test_an_older_daemon_that_omits_engine_shows_nothing(self, qtbot, window, app_state):
        """Absence is not a failure — daemons before 2.17.0 have no `engine` entry."""
        status = DaemonStatus(
            overall_status="ok",
            subsystems=[SubsystemStatus(name="openfan", status="ok", reason="")],
        )
        app_state.set_status(status)

        assert window.dashboard_page._engine_banner.isHidden()

    def test_the_banner_clears_when_engine_recovers_or_disappears(self, qtbot, window, app_state):
        """The transition latch must not strand a shown banner.

        `_last_engine_status` suppresses repeat renders, so a banner raised on
        `crit` is only ever cleared by a *transition*. Two ways out matter: the
        engine recovers, and the entry vanishes entirely (a daemon downgrade, or
        any payload that stops carrying it). Both must reach `hide_banner`, or
        the user is told their fans are dead forever.
        """
        dash = window.dashboard_page

        app_state.set_status(
            DaemonStatus(
                overall_status="degraded",
                subsystems=[SubsystemStatus(name="engine", status="crit", reason="not ticking")],
            )
        )
        assert not dash._engine_banner.isHidden()

        # Recovery.
        app_state.set_status(
            DaemonStatus(
                overall_status="ok",
                subsystems=[SubsystemStatus(name="engine", status="ok", reason="")],
            )
        )
        assert dash._engine_banner.isHidden()

        # Raise it again, then drop the entry from the payload entirely.
        app_state.set_status(
            DaemonStatus(
                overall_status="degraded",
                subsystems=[SubsystemStatus(name="engine", status="crit", reason="not ticking")],
            )
        )
        assert not dash._engine_banner.isHidden()
        app_state.set_status(DaemonStatus(overall_status="ok", subsystems=[]))
        assert dash._engine_banner.isHidden(), (
            "the entry vanishing must clear the banner, not strand it"
        )

    def test_a_warn_to_crit_escalation_swaps_the_wording(self, qtbot, window, app_state):
        """Both are non-ok, so a latch keyed on "did it change" is not enough —
        it must re-render when the *severity* changes."""
        dash = window.dashboard_page

        app_state.set_status(
            DaemonStatus(
                overall_status="degraded",
                subsystems=[SubsystemStatus(name="engine", status="warn", reason="slow tick")],
            )
        )
        assert "stopped" not in dash._engine_banner._message_label.text()

        app_state.set_status(
            DaemonStatus(
                overall_status="degraded",
                subsystems=[SubsystemStatus(name="engine", status="crit", reason="not ticking")],
            )
        )
        assert "stopped" in dash._engine_banner._message_label.text(), (
            "an escalation from slow to stopped must upgrade the message"
        )

    def test_a_healthy_engine_stays_out_of_the_way(self, qtbot, window, app_state):
        """The banner is a warning surface; a permanent "engine: ok" is noise."""
        status = DaemonStatus(
            overall_status="ok",
            subsystems=[SubsystemStatus(name="engine", status="ok", reason="")],
        )
        app_state.set_status(status)

        assert window.dashboard_page._engine_banner.isHidden()

    def test_subsystem_ok_is_not_flagged_as_warning(self, qtbot, window, app_state):
        """ "ok" is the daemon's healthy sentinel — the dashboard must NOT raise a
        WarningChip for it (dashboard_page _on_status_updated branches on
        ``!= "ok"``). Exercised through the real parse_status → set_status path."""
        status = parse_status(
            {
                "overall_status": "ok",
                "subsystems": [
                    {"name": "openfan", "status": "ok", "reason": ""},
                    {"name": "hwmon", "status": "ok", "reason": ""},
                ],
                "counters": {},
            }
        )
        app_state.set_status(status)

        dash = window.dashboard_page
        of = dash.findChild(QLabel, "Dashboard_Label_subOpenfan")
        hw = dash.findChild(QLabel, "Dashboard_Label_subHwmon")
        assert of.property("class") != "WarningChip"
        assert hw.property("class") != "WarningChip"

    def test_subsystem_warn_and_crit_flag_warning_chip(self, qtbot, window, app_state):
        """Non-"ok" statuses (the daemon emits "warn"/"crit") surface as a
        WarningChip with the status string shown to the operator. A daemon rename
        of "ok" would make even healthy subsystems hit this path — the contract
        is pinned here and by the daemon's `health_status_display_wire_strings`."""
        status = parse_status(
            {
                "overall_status": "crit",
                "subsystems": [
                    {"name": "openfan", "status": "warn", "reason": "readings stale"},
                    {"name": "hwmon", "status": "crit", "reason": "never received data"},
                ],
                "counters": {},
            }
        )
        app_state.set_status(status)

        dash = window.dashboard_page
        of = dash.findChild(QLabel, "Dashboard_Label_subOpenfan")
        hw = dash.findChild(QLabel, "Dashboard_Label_subHwmon")
        assert of.property("class") == "WarningChip"
        assert "warn" in of.text()
        assert hw.property("class") == "WarningChip"
        assert "crit" in hw.text()


class TestDashboardContent:
    def test_sensor_updates_reach_the_sensors_rail(self, qtbot, window, app_state):
        """DEC-222: the summary cards were removed; a sensor's arrival on the
        Dashboard is now evidenced by the Thermal Sensors rail listing it."""
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=72.5, age_ms=100),
                SensorReading(id="s2", label="GPU", kind="GpuTemp", value_c=65.0, age_ms=100),
            ]
        )
        shown = window.dashboard_page._sensor_panel.displayed_sensor_ids()
        assert "s1" in shown
        assert "s2" in shown

    def test_fan_count_reaches_the_fan_card_header(self, qtbot, window, app_state):
        """DEC-222: the Fans summary card was removed; the fan-cards header now
        reports the control/fan tally. With no profile active both fans land in
        the single Unassigned card."""
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_fans(
            [
                FanReading(id="f1", source="openfan", rpm=1200, age_ms=100),
                FanReading(id="f2", source="openfan", rpm=1100, age_ms=100),
            ]
        )
        # The tally is asserted on the fan half only: how the two fans distribute
        # across controls depends on the fixture profile, but both must be counted.
        assert "2 fans" in window.dashboard_page._fan_count_label.text()

    def test_warning_count_shows_in_footer(self, qtbot, window, app_state):
        """DEC-222: warnings surface in the always-visible footer health rollup
        (the Dashboard status strip that carried the chip was removed)."""
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=42.0, age_ms=5000),
            ]
        )
        assert app_state.warning_count >= 1
        text = window.footer._health_label.text()
        assert any(c.isdigit() for c in text)
        assert "warning" in text

    def test_open_readiness_button_exists_and_routes(self, qtbot, window):
        """The no-hardware state's button opens the Hardware page (readiness),
        matching its message that promises the driver/readiness report."""
        from control_ofc.constants import PAGE_HARDWARE

        btn = window.dashboard_page.findChild(QPushButton, "Dashboard_Btn_openReadiness")
        assert btn is not None
        assert btn.isEnabled()
        btn.click()
        assert window.page_stack.currentIndex() == PAGE_HARDWARE


class TestModeBadge:
    """Mode renders in the always-visible footer (DEC-222), so every page shows
    it — not just the Dashboard, as with the removed status strip."""

    def test_demo_mode_shows_label(self, qtbot, window, app_state):
        app_state.set_mode(OperationMode.DEMO)
        assert "Demo" in window.footer._mode_label.text()

    def test_automatic_mode_shows_label(self, qtbot, window, app_state):
        app_state.set_mode(OperationMode.AUTOMATIC)
        assert window.footer._mode_label.text() == "Automatic"


class TestProfilePosition:
    """DEC-222: the status strip was removed, but the Dashboard keeps its own
    profile selector so the landing page can still switch profiles directly."""

    def test_profile_selector_is_on_the_page(self, qtbot, window, app_state):
        page = window.dashboard_page
        assert page._profile_combo.objectName() == "Dashboard_Combo_profile"
        assert page._apply_btn.objectName() == "Dashboard_Btn_apply"
        # Both live on the page's live-content widget, not in a removed strip.
        live = page._stack.widget(page._IDX_LIVE)
        assert page._profile_combo in live.findChildren(type(page._profile_combo))
        assert page._apply_btn in live.findChildren(type(page._apply_btn))


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
    """R12-001: unpolish/polish only when the class actually changes.

    Re-vehicled off the deleted SummaryCard onto ``set_chip_class`` itself, which
    is where the guard lives and which every chip surface uses."""

    def test_skip_if_unchanged_is_a_noop(self, qtbot):
        from PySide6.QtWidgets import QLabel

        from control_ofc.ui.qt_util import set_chip_class

        label = QLabel("x")
        qtbot.addWidget(label)
        set_chip_class(label, "WarningChip")
        # Second call with the same class must be a no-op, not a re-polish.
        set_chip_class(label, "WarningChip", skip_if_unchanged=True)
        assert label.property("class") == "WarningChip"

        set_chip_class(label, "CriticalChip", skip_if_unchanged=True)
        assert label.property("class") == "CriticalChip"


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
        # The fixture loads 3 bundled profiles; fail loudly if that invariant
        # breaks rather than silently skipping the assertion (the old `if` guard
        # turned a broken fixture into a green no-op).
        assert page._profile_combo.count() >= 2
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


class TestThermalBanner:
    """Dashboard surfaces the daemon's thermal-protection state from /status
    poll diffs (the GUI no longer runs a loop watching thermal_state)."""

    def test_thermal_trip_shows_banner(self, qtbot, window, app_state):
        dash = window.dashboard_page
        assert dash._thermal_banner.isHidden()

        app_state.set_status(DaemonStatus(thermal_state="emergency"))

        assert not dash._thermal_banner.isHidden()
        assert "Thermal protection" in dash._thermal_banner._message_label.text()
        # Contract pin (TEST-1, 2026-07-21 audit): "emergency" is the daemon's
        # real 105 °C-force string (safety_tick.rs). The old test sent a
        # phantom "force" state that only exercised the catch-all branch — a
        # regression breaking the banner for the real emergency state stayed
        # green. Pin the literal so the wire string can't silently drift.
        assert "emergency" in dash._thermal_banner._message_label.text()

    def test_recovery_state_shows_banner(self, qtbot, window, app_state):
        # "recovery" (the two 60% cooldown cycles after release) is a
        # non-"normal" state: the banner must stay up and name it.
        dash = window.dashboard_page
        assert dash._thermal_banner.isHidden()

        app_state.set_status(DaemonStatus(thermal_state="recovery"))

        assert not dash._thermal_banner.isHidden()
        assert "recovery" in dash._thermal_banner._message_label.text()

    def test_no_sensor_fallback_shows_banner_with_state(self, qtbot, window, app_state):
        # DEC-170 contract pin: the daemon's "no_sensor_fallback" thermal_state
        # (forced 40% when no CPU sensor is reachable) must surface the banner and
        # name the specific state, so the contract string can't silently drift.
        dash = window.dashboard_page
        assert dash._thermal_banner.isHidden()

        app_state.set_status(DaemonStatus(thermal_state="no_sensor_fallback"))

        assert not dash._thermal_banner.isHidden()
        assert "no_sensor_fallback" in dash._thermal_banner._message_label.text()

    def test_thermal_recovery_clears_banner(self, qtbot, window, app_state):
        dash = window.dashboard_page
        app_state.set_status(DaemonStatus(thermal_state="emergency"))
        assert not dash._thermal_banner.isHidden()

        app_state.set_status(DaemonStatus(thermal_state="normal"))

        assert dash._thermal_banner.isHidden()

    def test_normal_status_keeps_banner_hidden(self, qtbot, window, app_state):
        dash = window.dashboard_page

        app_state.set_status(DaemonStatus(thermal_state="normal"))

        assert dash._thermal_banner.isHidden()


class TestReadinessChip:
    """DEC-206: the cooling-readiness chip + its deep-link to the Hardware page,
    driven by the poll rollup. DEC-222 moved the chip from the Dashboard status
    strip to the always-visible footer, so it is no longer Dashboard-only."""

    def _status_with_readiness(self, **rollup):
        payload = {"overall_status": "ok", "subsystems": []}
        if rollup:
            payload["readiness"] = rollup
        return parse_status(payload)

    def test_rollup_shows_chip_with_count(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_status(
            self._status_with_readiness(overall="warning", warning=1, top_summary="Load it87")
        )
        chip = window.footer._readiness_btn
        assert not chip.isHidden()
        assert "1 to fix" in chip.text()

    def test_absent_rollup_hides_chip(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_status(self._status_with_readiness())  # no readiness key
        assert window.footer._readiness_btn.isHidden()

    def test_demo_mode_hides_chip_even_with_rollup(self, qtbot, window, app_state):
        app_state.set_mode(OperationMode.DEMO)
        app_state.set_status(self._status_with_readiness(overall="critical", critical=1))
        assert window.footer._readiness_btn.isHidden()

    def test_chip_click_opens_hardware_page(self, qtbot, window, app_state):
        # DEC-212: the cooling-readiness chip now opens the Hardware page (was the
        # Diagnostics Readiness sub-tab).
        from control_ofc.constants import PAGE_HARDWARE

        window.dashboard_page.open_readiness.emit()
        assert window.page_stack.currentIndex() == PAGE_HARDWARE
        assert window.page_stack.currentWidget() is window.hardware_page

    def test_clicking_the_actual_chip_fires_open_readiness(self, qtbot, window, app_state):
        """End-to-end wiring: clicking the real chip button → readiness_clicked →
        open_readiness (the strip→dashboard connection, not just the signal)."""
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_status(self._status_with_readiness(overall="warning", warning=1))
        chip = window.footer._readiness_btn
        with qtbot.waitSignal(window.footer.readiness_clicked, timeout=500):
            chip.click()


class TestControlsSubsystemChip:
    """279-a: the Dashboard surfaces the daemon 2.22.0 `controls` subsystem.

    The Dashboard names `openfan`/`hwmon` explicitly and banners `engine`, so the
    fourth subsystem was silently ignored there — and the Dashboard has never
    read `overall_status`, so a user who stays on that page got no signal at all
    that a control is unresolved. Not a regression (nothing there reflected it
    before either), but a real blind spot on the default landing page.
    """

    def _page(self, qtbot):
        from control_ofc.services.app_state import AppState
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=AppState())
        qtbot.addWidget(page)
        return page

    def _status(self, controls_status, reason=""):
        from control_ofc.api.models import DaemonStatus, SubsystemStatus

        return DaemonStatus(
            subsystems=[
                SubsystemStatus(name="openfan", status="ok", reason="readings fresh"),
                SubsystemStatus(name="hwmon", status="ok", reason="readings fresh"),
                SubsystemStatus(name="engine", status="ok", reason="evaluating on schedule"),
                SubsystemStatus(name="controls", status=controls_status, reason=reason),
            ]
        )

    def test_a_warning_appears_with_its_reason(self, qtbot):
        page = self._page(qtbot)
        # `isHidden()`, not `isVisible()`: a widget is only *visible* when every
        # ancestor is shown, and this page never is under offscreen Qt — so
        # `isVisible()` is False regardless and would assert nothing. `isHidden()`
        # reads the explicit flag this code actually sets.
        assert page._sub_controls_label.isHidden(), "precondition: hidden while healthy"

        page._on_status_updated(
            self._status("warn", "1 control not being commanded — their fans hold their last speed")
        )

        assert not page._sub_controls_label.isHidden()
        text = page._sub_controls_label.text()
        assert "Controls" in text and "warn" in text
        assert "not being commanded" in text, (
            f"the daemon's reason is what makes the warning actionable, got {text!r}"
        )

    def test_it_clears_again_when_the_control_is_fixed(self, qtbot):
        """The explicit `ok` branch matters: nothing else ever writes this label.

        Its two siblings are repainted from the capabilities VM on every refresh,
        so a poll that finds them healthy can leave them alone. Without the hide,
        this one would pin the last warning for the rest of the session.
        """
        page = self._page(qtbot)
        page._on_status_updated(self._status("warn", "1 control not being commanded"))
        assert not page._sub_controls_label.isHidden(), "precondition: it warned first"

        page._on_status_updated(self._status("ok", "every control resolves to a curve"))

        assert page._sub_controls_label.isHidden(), (
            "a resolved control must take the warning down — nothing else repaints "
            "this label, so a stale warning would outlive the fault for the session"
        )

    def test_an_older_daemon_omits_it_entirely(self, qtbot):
        """Pre-2.22.0 daemons send three subsystems; the chip stays hidden.

        Establishes the PRESENCE first. Without that, the label is constructed
        hidden and the `elif sub.name == "controls"` branch never runs, so this
        passed with the entire chip block deleted — the vacuous-absence trap
        (DEC-272).
        """
        from control_ofc.api.models import DaemonStatus, SubsystemStatus

        page = self._page(qtbot)
        page._on_status_updated(self._status("warn", "1 control not being commanded"))
        assert not page._sub_controls_label.isHidden(), "precondition: it can show at all"

        page._on_status_updated(
            DaemonStatus(
                subsystems=[
                    SubsystemStatus(name="openfan", status="ok", reason=""),
                    SubsystemStatus(name="hwmon", status="ok", reason=""),
                    SubsystemStatus(name="engine", status="ok", reason=""),
                ]
            )
        )
        assert page._sub_controls_label.isHidden()
