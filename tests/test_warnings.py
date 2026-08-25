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
        assert len(warn_state.unacknowledged_warnings) == 1
        assert "stale" in warn_state.unacknowledged_warnings[0]["message"].lower()

    def test_stalled_fan_creates_warning(self, warn_state):
        warn_state.set_fans(
            [
                FanReading(id="f1", source="openfan", rpm=0, age_ms=50, stall_detected=True),
            ]
        )
        assert warn_state.warning_count >= 1
        stall_warnings = [
            w for w in warn_state.unacknowledged_warnings if "stall" in w["message"].lower()
        ]
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
        assert len(warn_state.unacknowledged_warnings) == 0


STALE_S1 = SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=5000)
FRESH_S1 = SensorReading(id="s1", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=100)


class TestAcknowledgement:
    """DEC-282 changed what acknowledgement *means*, and these tests changed with it.

    It used to add the condition's key to a set that was never pruned, so
    ``warning_count`` went to zero and stayed there — including for every later
    recurrence of that condition, for the rest of the session. It now marks the
    current *occurrence* as seen. Two consequences are asserted below and neither
    held before:

    * a condition that is still happening keeps counting (acknowledgement is not
      resolution — a user cannot make a fan spin by clicking a button);
    * a recurrence after recovery is a new occurrence and alerts again.
    """

    def test_acknowledge_silences_attention_but_not_health(self, warn_state):
        """Acknowledging a **still-present** condition must not claim it resolved.

        This is the half the old ``test_clear_resets_count`` had backwards: it
        asserted ``warning_count == 0`` after clearing, while the sensor was still
        stale. The count is a health number, so it stays at 1; what drops to zero is
        the attention number.
        """
        warn_state.set_sensors([STALE_S1])
        assert warn_state.warning_count == 1
        assert warn_state.unacknowledged_count == 1

        warn_state.acknowledge_all()

        assert warn_state.warning_count == 1, "the sensor is still stale"
        assert warn_state.unacknowledged_count == 0, "but the user has now seen it"
        assert len(warn_state.unacknowledged_warnings) == 0

    def test_acknowledge_emits_content_signal(self, qtbot, warn_state):
        """Was ``warnings_cleared``; that signal is gone. Acknowledgement is an
        ordinary content change, so it travels on ``warnings_changed`` like every
        other one."""
        warn_state.set_sensors([STALE_S1])
        with qtbot.waitSignal(warn_state.warnings_changed, timeout=500):
            warn_state.acknowledge_all()

    def test_continuously_present_condition_does_not_re_alert(self, warn_state):
        """The legitimate half of the old ``test_clear_acknowledges_warnings``.

        A condition that never went away is **one** occurrence, so re-observing it on
        the next poll must not resurrect it into the attention count — otherwise
        acknowledgement would be undone a second after it was given, and the event log
        would gain a line per poll.
        """
        warn_state.set_sensors([STALE_S1])
        warn_state.acknowledge_all()

        warn_state.set_sensors([STALE_S1])  # same stale sensor, next poll

        assert warn_state.unacknowledged_count == 0, "still the same occurrence"
        assert warn_state.warning_count == 1, "and still genuinely stale"

    def test_recurrence_after_recovery_alerts_again(self, warn_state):
        """The half the old test forbade, and the reason it had to change.

        Acknowledging a stall at 12:00 must not mute the same fan stalling again at
        14:30. Under the old suppression set it did, permanently, for the session.
        """
        warn_state.set_sensors([STALE_S1])
        assert warn_state.unacknowledged_count == 1, "presence, before asserting recovery"
        warn_state.acknowledge_all()
        warn_state.set_sensors([FRESH_S1])  # condition clears
        assert warn_state.warning_count == 0

        warn_state.set_sensors([STALE_S1])  # ...and comes back

        assert warn_state.warning_count == 1
        assert warn_state.unacknowledged_count == 1, "a new occurrence must be seen"

    def test_acknowledge_when_empty_is_safe(self, warn_state):
        warn_state.acknowledge_all()  # no crash
        assert warn_state.warning_count == 0

    def test_new_warning_after_acknowledge_triggers(self, warn_state):
        """A genuinely new warning (different ID) triggers after acknowledgement.

        Unchanged in intent from the original — it was already correct, because it
        used a *different* key and so never hit the suppression set. It is kept as the
        control case for ``test_recurrence_after_recovery_alerts_again``, which asserts
        the same thing for the *same* key.
        """
        warn_state.set_sensors([STALE_S1])
        warn_state.acknowledge_all()
        warn_state.set_sensors(
            [
                SensorReading(id="s2", label="GPU", kind="GpuTemp", value_c=60.0, age_ms=5000),
            ]
        )
        assert warn_state.unacknowledged_count == 1  # new warning from s2


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
        assert len(page._displayable_fan_keys) == 0  # RPM=0 → not displayable

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
        assert len(page._displayable_fan_keys) == 1

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
        assert len(page._displayable_fan_keys) == 0

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
        assert len(page._displayable_fan_keys) == 1  # labeled → displayable

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
        assert len(page._displayable_fan_keys) == 1  # PWM>0 → displayable

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
        assert len(page._displayable_fan_keys) == 2  # only ch00 and hwmon:fan1
