"""2026-06-05 audit remediation — regression tests.

Covers the GUI-side fixes:
- DEC-132: thermal-override stand-down (``thermal_state`` in ``/status``;
  control loop pauses, lease machinery stands down, warning hierarchy).
- P2-4: write-retry decay for persistently failing targets.
- P3-1: ``_PollWorker.poll`` treats parse-shaped exceptions as failed cycles.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_ofc.api.models import (
    ConnectionState,
    DaemonStatus,
    FanReading,
    OperationMode,
    SensorReading,
    parse_status,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import (
    WRITE_RETRY_DECAY_S,
    ControlLoopService,
)
from control_ofc.services.lease_service import LeaseService
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    CurveConfig,
    CurvePoint,
    CurveType,
    LogicalControl,
    Profile,
    ProfileService,
)
from tests.conftest import FakeDaemonClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(temp_points, sensor_id="cpu_temp", target_id="openfan:ch00"):
    points = [CurvePoint(t, p) for t, p in temp_points]
    curve = CurveConfig(
        id="test_curve",
        name="Test Curve",
        type=CurveType.GRAPH,
        sensor_id=sensor_id,
        points=points,
    )
    source = "openfan" if target_id.startswith("openfan") else "hwmon"
    control = LogicalControl(
        id="test_control",
        name="Test Control",
        mode=ControlMode.CURVE,
        curve_id="test_curve",
        members=[ControlMember(source=source, member_id=target_id)],
    )
    return Profile(id="test", name="Test", controls=[control], curves=[curve])


def _openfan_calls(client):
    return [(a, k) for m, a, k in client.calls if m == "set_openfan_pwm"]


@pytest.fixture()
def state(qtbot):
    s = AppState()
    s.connection = ConnectionState.CONNECTED
    s.mode = OperationMode.AUTOMATIC
    s.sensors = [
        SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=50.0, age_ms=500),
    ]
    s.fans = [
        FanReading(
            id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=None, age_ms=500
        ),
    ]
    return s


@pytest.fixture()
def profile_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    return svc


@pytest.fixture()
def loop_with_profile(state, profile_service):
    profile = _make_profile([(30, 20), (70, 80)])
    profile_service._profiles["test"] = profile
    profile_service.set_active("test")
    client = FakeDaemonClient()
    loop = ControlLoopService(state, profile_service, client=client)
    return loop, client, state


# ---------------------------------------------------------------------------
# DEC-132 — thermal_state parsing
# ---------------------------------------------------------------------------


class TestThermalStateParsing:
    def test_parse_status_reads_thermal_state(self):
        status = parse_status({"thermal_state": "emergency"})
        assert status.thermal_state == "emergency"

    def test_parse_status_defaults_to_normal_for_old_daemons(self):
        """Pre-1.13 daemons don't send the field — must default to normal."""
        status = parse_status({"daemon_version": "1.12.2"})
        assert status.thermal_state == "normal"


# ---------------------------------------------------------------------------
# DEC-132 — control loop stand-down
# ---------------------------------------------------------------------------


class TestThermalStanddown:
    def test_emergency_pauses_all_writes(self, loop_with_profile):
        loop, client, state = loop_with_profile
        state.daemon_status = DaemonStatus(thermal_state="emergency")

        loop._cycle()

        assert _openfan_calls(client) == [], "no writes during a thermal emergency"
        keys = [w.get("_key") for w in state._external_warnings]
        assert "thermal_standdown" in keys, "stand-down warning must be raised"

    def test_recovery_and_no_sensor_fallback_also_pause(self, loop_with_profile):
        loop, client, state = loop_with_profile
        for thermal in ("recovery", "no_sensor_fallback"):
            state.daemon_status = DaemonStatus(thermal_state=thermal)
            loop._cycle()
            assert _openfan_calls(client) == [], f"no writes while {thermal}"

    def test_unknown_thermal_state_still_stands_down(self, loop_with_profile):
        """A future daemon state the GUI doesn't know must still pause writes."""
        loop, client, state = loop_with_profile
        state.daemon_status = DaemonStatus(thermal_state="some_future_state")

        loop._cycle()

        assert _openfan_calls(client) == []
        assert loop._thermal_standdown is True

    def test_standdown_clears_on_normal_and_resets_hysteresis(self, loop_with_profile):
        loop, client, state = loop_with_profile

        # Enter stand-down.
        state.daemon_status = DaemonStatus(thermal_state="emergency")
        loop._cycle()
        assert loop._thermal_standdown is True
        # Seed hysteresis state to prove the exit edge clears it.
        loop._target_states["seeded"] = None

        # Exit stand-down.
        state.daemon_status = DaemonStatus(thermal_state="normal")
        loop._cycle()

        assert loop._thermal_standdown is False
        assert "seeded" not in loop._target_states, "hysteresis must reset on exit"
        keys = [w.get("_key") for w in state._external_warnings]
        assert "thermal_standdown" not in keys, "warning must clear on exit"
        # Writes resume — the post-standdown cycle evaluated and wrote.
        assert len(_openfan_calls(client)) == 1

    def test_standdown_pauses_lease_machinery(self, loop_with_profile):
        loop, _client, state = loop_with_profile
        lease = MagicMock()
        loop._lease = lease
        state.daemon_status = DaemonStatus(thermal_state="emergency")

        loop._cycle()

        lease.pause_for_thermal_override.assert_called()

    def test_lease_lost_during_standdown_suppresses_bios_warning(self, loop_with_profile):
        loop, _client, state = loop_with_profile
        state.daemon_status = DaemonStatus(thermal_state="emergency")
        loop._cycle()  # latch stand-down

        loop._on_lease_lost("force-taken by thermal-safety")

        keys = [w.get("_key") for w in state._external_warnings]
        assert "lease_lost" not in keys, (
            "expected lease-loss during a thermal override to be silent — "
            "the thermal warning already explains the pause"
        )
        assert state.mode == OperationMode.READ_ONLY, "safety mode change still applies"

    def test_lease_lost_outside_standdown_keeps_warning(self, loop_with_profile):
        loop, _client, state = loop_with_profile

        loop._on_lease_lost("renewal failed")

        keys = [w.get("_key") for w in state._external_warnings]
        assert "lease_lost" in keys

    def test_demo_mode_never_stands_down(self, loop_with_profile):
        loop, _client, state = loop_with_profile
        state.mode = OperationMode.DEMO
        state.daemon_status = DaemonStatus(thermal_state="emergency")
        demo = MagicMock()
        loop._demo = demo

        loop._cycle()

        assert loop._thermal_standdown is False
        demo.set_fan_pwm.assert_called()


# ---------------------------------------------------------------------------
# DEC-132 — lease service stand-down
# ---------------------------------------------------------------------------


class TestLeasePauseForThermalOverride:
    def _held_service(self, qtbot):
        client = MagicMock()
        result = MagicMock()
        result.lease_id = "lease-1"
        result.ttl_seconds = 60
        client.hwmon_lease_take.return_value = result
        svc = LeaseService(client)  # sync mode (no socket_path)
        assert svc.acquire() is True
        assert svc.is_held
        return svc

    def test_pause_drops_lease_without_lease_lost(self, qtbot):
        svc = self._held_service(qtbot)
        lost: list[str] = []
        svc.lease_lost.connect(lost.append)

        svc.pause_for_thermal_override()

        assert not svc.is_held
        assert not svc._renew_timer.isActive()
        assert lost == [], "deliberate stand-down must not emit lease_lost"

    def test_pause_is_idempotent(self, qtbot):
        svc = self._held_service(qtbot)
        svc.pause_for_thermal_override()
        svc.pause_for_thermal_override()  # no-op, no error
        assert not svc.is_held

    def test_acquire_works_after_pause(self, qtbot):
        svc = self._held_service(qtbot)
        svc.pause_for_thermal_override()

        assert svc.acquire() is True
        assert svc.is_held, "lazy re-acquire after the override clears"


# ---------------------------------------------------------------------------
# P2-4 — write-retry decay
# ---------------------------------------------------------------------------


class TestWriteRetryDecay:
    def _failing_loop(self, state, profile_service):
        from control_ofc.api.errors import DaemonError

        client = FakeDaemonClient()
        client.simulate_persistent_error(
            "set_openfan_pwm",
            DaemonError(code="hw", message="fail", retryable=True, source="serial", status=500),
        )
        profile = _make_profile([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")
        loop = ControlLoopService(state, profile_service, client=client)
        return loop, client

    def test_three_failures_arm_decay_and_suppress_retries(self, state, profile_service):
        loop, client = self._failing_loop(state, profile_service)

        for _ in range(3):
            loop._cycle()
        attempts_after_threshold = len(_openfan_calls(client))
        assert attempts_after_threshold == 3
        assert "openfan:ch00" in loop._write_retry_after, "decay deadline must be armed"

        # Further cycles inside the decay window issue NO writes.
        for _ in range(5):
            loop._cycle()
        assert len(_openfan_calls(client)) == attempts_after_threshold, (
            "P2-4: a permanently failing target must not be retried every "
            "second once past the warning threshold"
        )

    def test_probe_fires_after_deadline_and_rearms_on_failure(self, state, profile_service):
        loop, client = self._failing_loop(state, profile_service)
        for _ in range(3):
            loop._cycle()
        before = len(_openfan_calls(client))

        # Expire the deadline — exactly one probe write goes out.
        loop._write_retry_after["openfan:ch00"] = 0.0
        loop._cycle()
        assert len(_openfan_calls(client)) == before + 1
        # The probe failed → deadline re-armed for the future.
        assert loop._write_retry_after["openfan:ch00"] > 0.0
        loop._cycle()
        assert len(_openfan_calls(client)) == before + 1, "suppressed again after failed probe"

    def test_success_clears_decay(self, state, profile_service):
        loop, client = self._failing_loop(state, profile_service)
        for _ in range(3):
            loop._cycle()

        client.clear_errors()
        loop._write_retry_after["openfan:ch00"] = 0.0
        state.fans[0].last_commanded_pwm = None
        loop._cycle()  # successful probe

        assert "openfan:ch00" not in loop._write_retry_after

    def test_profile_change_clears_decay(self, state, profile_service):
        loop, _client = self._failing_loop(state, profile_service)
        for _ in range(3):
            loop._cycle()
        assert loop._write_retry_after

        loop._on_profile_changed("other")

        assert not loop._write_retry_after, "fresh profile gets immediate attempts"

    def test_decay_constant_is_sane(self):
        # Guards against accidental sub-second or multi-minute decay edits.
        assert 5.0 <= WRITE_RETRY_DECAY_S <= 60.0


# ---------------------------------------------------------------------------
# P3-1 — poll worker parse-error containment
# ---------------------------------------------------------------------------


class TestPollWorkerParseErrors:
    def _worker(self, mock_client):
        from control_ofc.services.polling import _PollWorker

        worker = _PollWorker(socket_path="/tmp/fake.sock")
        worker._ensure_client = MagicMock(return_value=mock_client)
        return worker

    def test_value_error_from_fallback_counts_as_failed_cycle(self, qtbot):
        """A malformed-but-200 payload (ValueError from a fallback leg)
        previously escaped poll()'s DaemonError handler and hit the Qt
        excepthook once per second with no backoff."""
        client = MagicMock()
        client.capabilities.side_effect = ValueError("malformed capabilities payload")
        worker = self._worker(client)

        disconnected: list = []
        worker.disconnected.connect(lambda: disconnected.append(True))

        worker.poll()  # must not raise

        assert disconnected, "parse failure must surface as a failed cycle"
        assert worker._consecutive_failures == 1

    def test_key_error_mid_poll_counts_as_failed_cycle(self, qtbot):
        client = MagicMock()
        client.poll.side_effect = KeyError("sensors")
        client.status.side_effect = KeyError("status")
        worker = self._worker(client)
        worker._poll_count = 1  # skip first-poll capabilities leg

        disconnected: list = []
        worker.disconnected.connect(lambda: disconnected.append(True))

        worker.poll()

        assert disconnected
        assert worker._consecutive_failures == 1
