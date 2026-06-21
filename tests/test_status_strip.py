"""DashboardStatusStrip rendering + poll-age + warning click, and the global
StatusBanner hide-on-dashboard wiring (DEC-176/177)."""

from __future__ import annotations

from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.services.app_state import AppState
from control_ofc.ui.widgets.status_strip import DashboardStatusStrip, format_poll_age

# ---------------------------------------------------------------------------
# Pure poll-age formatting (no widget, no timing)
# ---------------------------------------------------------------------------


def test_format_poll_age_buckets():
    assert format_poll_age(None) == "Not updated yet"
    assert format_poll_age(0.0) == "Updated just now"
    assert format_poll_age(1.9) == "Updated just now"
    assert format_poll_age(3.0) == "Updated 3s ago"
    assert format_poll_age(59) == "Updated 59s ago"
    assert format_poll_age(90) == "Updated 1m ago"
    assert format_poll_age(3700) == "Updated 1h ago"
    assert format_poll_age(-5) == "Updated just now"  # negative clamps to 0


# ---------------------------------------------------------------------------
# Chip rendering — one assertion set per state
# ---------------------------------------------------------------------------


def test_connection_chip_text(qtbot):
    s = DashboardStatusStrip()
    qtbot.addWidget(s)
    s.set_connection_state(ConnectionState.CONNECTED)
    assert s._connection.text() == "Connected"
    assert s._connection.property("class") == "SuccessChip"
    s.set_connection_state(ConnectionState.DEGRADED)
    assert s._connection.text() == "Degraded"
    assert s._connection.property("class") == "WarningChip"
    s.set_connection_state(ConnectionState.DISCONNECTED)
    assert s._connection.text() == "Disconnected"
    assert s._connection.property("class") == "CriticalChip"


def test_mode_chip_text(qtbot):
    s = DashboardStatusStrip()
    qtbot.addWidget(s)
    s.set_operation_mode(OperationMode.AUTOMATIC)
    assert s._mode.text() == "Automatic"
    s.set_operation_mode(OperationMode.MANUAL_OVERRIDE)
    assert s._mode.text() == "Manual Override"
    assert s._mode.property("class") == "ManualBadge"
    s.set_operation_mode(OperationMode.READ_ONLY)
    assert s._mode.text() == "Read-only"
    s.set_operation_mode(OperationMode.DEMO)
    assert s._mode.text() == "Demo mode"
    assert s._mode.property("class") == "DemoBadge"


def test_thermal_chip_text(qtbot):
    s = DashboardStatusStrip()
    qtbot.addWidget(s)
    s.set_thermal_state("normal")
    assert s._thermal.text() == "Thermal OK"
    assert s._thermal.property("class") == "SuccessChip"
    s.set_thermal_state("emergency")
    assert "Emergency" in s._thermal.text()
    assert s._thermal.property("class") == "CriticalChip"
    s.set_thermal_state("recovery")
    assert s._thermal.text() == "Thermal: Recovery"
    assert s._thermal.property("class") == "WarningChip"
    s.set_thermal_state("no_sensor_fallback")
    assert s._thermal.text() == "Thermal: No CPU sensor"
    assert s._thermal.property("class") == "WarningChip"
    # Unknown states are surfaced (never hidden), as a neutral info chip.
    s.set_thermal_state("weird_state")
    assert "weird_state" in s._thermal.text()
    assert s._thermal.property("class") == "InfoChip"


def test_profile_label(qtbot):
    s = DashboardStatusStrip()
    qtbot.addWidget(s)
    s.set_active_profile("Balanced")
    assert s._profile.text() == "Balanced"
    s.set_active_profile("")
    assert s._profile.text() == "No profile"


# ---------------------------------------------------------------------------
# Warning chip: count, singular/plural, visibility, click
# ---------------------------------------------------------------------------


def test_warning_chip_count_and_visibility(qtbot):
    s = DashboardStatusStrip()
    qtbot.addWidget(s)
    s.set_warning_count(0)
    assert s._warning.text() == ""
    assert not s._warning.isVisibleTo(s)
    s.set_warning_count(1)
    assert "1 warning" in s._warning.text()
    assert "warnings" not in s._warning.text()  # singular
    assert s._warning.isVisibleTo(s)
    s.set_warning_count(3)
    assert "3 warnings" in s._warning.text()


def test_warning_chip_click_emits(qtbot):
    s = DashboardStatusStrip()
    qtbot.addWidget(s)
    fired = []
    s.warning_clicked.connect(lambda: fired.append(True))
    s.set_warning_count(2)
    s._warning.click()
    assert fired == [True]


# ---------------------------------------------------------------------------
# Poll-age from an injected timestamp (no real clock)
# ---------------------------------------------------------------------------


def test_poll_age_from_injected_timestamp(qtbot):
    s = DashboardStatusStrip()
    qtbot.addWidget(s)
    s.update_poll_age(now=100.0, last_poll=None)
    assert s._poll_age.text() == "Not updated yet"
    s.update_poll_age(now=105.0, last_poll=100.0)
    assert s._poll_age.text() == "Updated 5s ago"


def test_profile_selector_exposed(qtbot):
    s = DashboardStatusStrip()
    qtbot.addWidget(s)
    assert s.profile_combo is not None
    assert s.apply_btn.text() == "Apply"


# ---------------------------------------------------------------------------
# AppState poll-success timestamp (injectable for tests)
# ---------------------------------------------------------------------------


def test_app_state_mark_poll_success_injectable():
    state = AppState()
    assert state.last_poll_monotonic is None
    state.mark_poll_success(now=42.0)
    assert state.last_poll_monotonic == 42.0


# ---------------------------------------------------------------------------
# Global banner hidden on the dashboard (main_window wiring)
# ---------------------------------------------------------------------------


def test_global_banner_hidden_on_dashboard(qtbot, app_state, profile_service, settings_service):
    from control_ofc.constants import PAGE_CONTROLS, PAGE_DASHBOARD
    from control_ofc.ui.main_window import MainWindow

    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)

    win._on_page_changed(PAGE_DASHBOARD)
    assert not win.status_banner.isVisibleTo(win)  # strip owns status here
    win._on_page_changed(PAGE_CONTROLS)
    assert win.status_banner.isVisibleTo(win)  # other pages keep the banner
