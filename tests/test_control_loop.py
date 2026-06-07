"""Tests for control loop — curve evaluation, hysteresis, write suppression."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_ofc.api.models import (
    ConnectionState,
    FanReading,
    OperationMode,
    SensorReading,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import ControlLoopService, _dispatch_write
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


@pytest.fixture()
def state(qtbot):
    s = AppState()
    s.connection = ConnectionState.CONNECTED
    s.mode = OperationMode.AUTOMATIC
    s.sensors = [
        SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=50.0, age_ms=500),
    ]
    s.fans = [
        FanReading(id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=40, age_ms=500),
    ]
    return s


@pytest.fixture()
def profile_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    return svc


@pytest.fixture()
def demo_service():
    mock = MagicMock()
    mock.set_fan_pwm = MagicMock()
    return mock


def _make_profile_with_curve(temp_points, sensor_id="cpu_temp", target_id="openfan:ch00"):
    """Helper: create a profile with one logical control and a graph curve."""
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


class TestHysteresis:
    """2 deg C deadband prevents fan oscillation."""

    def test_rising_temp_evaluates_normally(self, state, profile_service, qtbot):
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)

        loop._cycle()
        ts = loop._target_states.get("test_control")
        assert ts is not None
        assert ts.last_transition_temp == 50.0
        assert ts.last_commanded_pwm == pytest.approx(50.0)

    def test_falling_within_deadband_holds(self, state, profile_service, qtbot):
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)

        loop._cycle()
        first_pwm = loop._target_states["test_control"].last_commanded_pwm

        state.sensors = [
            SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=49.0, age_ms=500),
        ]
        loop._cycle()

        assert loop._target_states["test_control"].last_commanded_pwm == first_pwm

    def test_falling_beyond_deadband_updates(self, state, profile_service, qtbot):
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)

        loop._cycle()
        first_pwm = loop._target_states["test_control"].last_commanded_pwm

        state.sensors = [
            SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=47.0, age_ms=500),
        ]
        loop._cycle()

        new_pwm = loop._target_states["test_control"].last_commanded_pwm
        assert new_pwm != first_pwm
        assert new_pwm == pytest.approx(45.5, abs=0.1)

    def test_profile_change_resets_hysteresis(self, state, profile_service, qtbot):
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)
        loop._cycle()
        assert len(loop._target_states) > 0

        loop._on_profile_changed("Other")
        assert len(loop._target_states) == 0

    def test_manual_override_exit_resets_hysteresis(self, state, profile_service, qtbot):
        """Entering then exiting manual override clears hysteresis state."""
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)
        loop._cycle()
        assert len(loop._target_states) > 0

        # Enter manual override — hysteresis state preserved during override
        loop.set_manual_override(True)
        assert len(loop._target_states) > 0

        # Exit manual override — hysteresis state must be cleared
        loop.set_manual_override(False)
        assert len(loop._target_states) == 0


class TestWriteSuppression:
    """1% PWM threshold prevents sub-perceptible churn."""

    def test_small_change_suppressed(self, state, profile_service, qtbot):
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=40, age_ms=500
            ),
        ]
        loop = ControlLoopService(state, profile_service)
        assert loop._should_write("openfan:ch00", 40.5, {f.id: f for f in state.fans}) is False

    def test_meaningful_change_allowed(self, state, profile_service, qtbot):
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=40, age_ms=500
            ),
        ]
        loop = ControlLoopService(state, profile_service)
        assert loop._should_write("openfan:ch00", 42.0, {f.id: f for f in state.fans}) is True

    def test_no_prior_pwm_allows_write(self, state, profile_service, qtbot):
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=None, age_ms=500
            ),
        ]
        loop = ControlLoopService(state, profile_service)
        assert loop._should_write("openfan:ch00", 50.0, {f.id: f for f in state.fans}) is True


class TestManualOverride:
    """Manual override pauses curve evaluation."""

    def test_enter_manual_override(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        loop.set_manual_override(True)
        assert loop.manual_override is True
        assert state.mode == OperationMode.MANUAL_OVERRIDE

    def test_exit_manual_override(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        loop.set_manual_override(True)
        loop.set_manual_override(False)
        assert loop.manual_override is False
        assert state.mode == OperationMode.AUTOMATIC

    def test_manual_override_skips_cycle(self, state, profile_service, qtbot):
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)
        loop.set_manual_override(True)
        loop._cycle()
        assert len(loop._target_states) == 0

    def test_profile_change_exits_override(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        loop.set_manual_override(True)
        loop._on_profile_changed("New Profile")
        assert loop.manual_override is False


class TestReevaluateNow:
    """Public reevaluate_now() bypasses the active_profile_changed signal
    so that re-activating the already-active profile still pushes the
    latest curve outputs. Regression for profile activation delay where
    ``state.active_profile_changed`` is suppressed on an unchanged name."""

    def test_reevaluate_resets_hysteresis(self, state, profile_service, qtbot):
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)
        loop._running = True
        loop._cycle()
        ts = loop._target_states["test_control"]
        ts.last_transition_temp = 99.0
        ts.last_commanded_pwm = 99.0

        loop.reevaluate_now()
        # Hysteresis cleared and cycle re-ran — the stale 99.0 anchors
        # must have been wiped and replaced with fresh values.
        new_ts = loop._target_states["test_control"]
        assert new_ts.last_transition_temp != 99.0
        assert new_ts.last_commanded_pwm != 99.0

    def test_reevaluate_runs_cycle_when_running(self, state, profile_service, qtbot):
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)
        loop._running = True  # simulate start() without the timer
        loop._target_states.clear()

        loop.reevaluate_now()
        # _cycle ran — target state for the control must exist
        assert "test_control" in loop._target_states

    def test_reevaluate_skips_cycle_when_not_running(self, state, profile_service, qtbot):
        """When the loop is not running, reevaluate clears hysteresis but
        does not execute a cycle (mirrors the timer-gated _cycle path)."""
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)
        loop._cycle()  # prime target_states while "running"
        assert len(loop._target_states) > 0
        loop._running = False

        loop.reevaluate_now()
        # _cycle was not called, so after _reset_hysteresis the dict stays empty
        assert len(loop._target_states) == 0

    def test_reevaluate_exits_manual_override(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        loop.set_manual_override(True)
        assert loop.manual_override is True

        loop.reevaluate_now()
        assert loop.manual_override is False

    def test_reevaluate_writes_new_curve_output(self, state, profile_service, qtbot):
        """After reevaluate, the control loop must issue a write for the
        current curve's output even when no profile_changed signal fired."""
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        # Daemon-reported last_commanded is 40% but curve at 50 deg C → 50%.
        # _should_write must see the 10% gap and write.
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=40, age_ms=500
            ),
        ]

        mock_client = MagicMock()
        loop = ControlLoopService(state, profile_service, client=mock_client)
        loop._running = True

        loop.reevaluate_now()
        mock_client.set_openfan_pwm.assert_called_once()
        _args, _ = mock_client.set_openfan_pwm.call_args, None
        assert mock_client.set_openfan_pwm.call_args[0][0] == 0
        assert mock_client.set_openfan_pwm.call_args[0][1] == 50


def _two_openfan_controls():
    """Profile: control A (curve) on ch00, control B (curve) on ch01."""
    curve = CurveConfig(
        id="cv",
        name="CV",
        type=CurveType.GRAPH,
        sensor_id="cpu_temp",
        points=[CurvePoint(30, 20), CurvePoint(70, 80)],
    )
    a = LogicalControl(
        id="A",
        name="A",
        mode=ControlMode.CURVE,
        curve_id="cv",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    b = LogicalControl(
        id="B",
        name="B",
        mode=ControlMode.CURVE,
        curve_id="cv",
        members=[ControlMember(source="openfan", member_id="openfan:ch01")],
    )
    return Profile(id="t2", name="T2", controls=[a, b], curves=[curve])


class TestPerControlManual:
    """Per-card transient manual override (Decision 1A): pins one control's
    members to a fixed PWM without freezing the others and without touching
    the saved profile."""

    def _two_fans(self, state):
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=40, age_ms=500
            ),
            FanReading(
                id="openfan:ch01", source="openfan", rpm=800, last_commanded_pwm=40, age_ms=500
            ),
        ]

    def test_set_control_manual_writes_fixed_pwm(self, state, profile_service, qtbot):
        self._two_fans(state)
        profile_service._profiles["t2"] = _two_openfan_controls()
        profile_service.set_active("t2")
        mock_client = MagicMock()
        loop = ControlLoopService(state, profile_service, client=mock_client)
        loop._running = True

        loop.set_control_manual("A", 75.0)

        assert loop.is_control_manual("A") is True
        writes = {c.args[0]: c.args[1] for c in mock_client.set_openfan_pwm.call_args_list}
        assert writes[0] == 75  # control A pinned to the manual value

    def test_manual_control_isolated_from_curve_control(self, state, profile_service, qtbot):
        """A second, curve-driven control keeps evaluating while A is manual."""
        self._two_fans(state)
        profile_service._profiles["t2"] = _two_openfan_controls()
        profile_service.set_active("t2")
        mock_client = MagicMock()
        loop = ControlLoopService(state, profile_service, client=mock_client)
        loop._running = True

        loop.set_control_manual("A", 75.0)

        writes = {c.args[0]: c.args[1] for c in mock_client.set_openfan_pwm.call_args_list}
        assert writes[0] == 75  # A: manual
        assert writes[1] == 50  # B: curve at 50 deg C → 50%, unaffected
        assert loop.is_control_manual("B") is False

    def test_clear_control_manual_returns_to_curve(self, state, profile_service, qtbot):
        self._two_fans(state)
        profile_service._profiles["t2"] = _two_openfan_controls()
        profile_service.set_active("t2")
        mock_client = MagicMock()
        loop = ControlLoopService(state, profile_service, client=mock_client)
        loop._running = True

        loop.set_control_manual("A", 75.0)
        loop.clear_control_manual("A")

        assert loop.is_control_manual("A") is False
        # The last write to channel 0 must be the curve output (50), not 75.
        ch0_writes = [
            c.args[1] for c in mock_client.set_openfan_pwm.call_args_list if c.args[0] == 0
        ]
        assert ch0_writes[-1] == 50

    def test_profile_change_clears_manual(self, state, profile_service, qtbot):
        profile_service._profiles["t2"] = _two_openfan_controls()
        profile_service.set_active("t2")
        loop = ControlLoopService(state, profile_service)
        loop._running = True
        loop.set_control_manual("A", 75.0)

        loop._on_profile_changed("New Profile")

        assert loop.is_control_manual("A") is False
        assert loop._manual_controls == {}

    def test_global_override_takes_precedence(self, state, profile_service, qtbot):
        """The Fan Wizard's global override short-circuits the whole cycle,
        so a per-control manual write must not fire while it is active."""
        self._two_fans(state)
        profile_service._profiles["t2"] = _two_openfan_controls()
        profile_service.set_active("t2")
        mock_client = MagicMock()
        loop = ControlLoopService(state, profile_service, client=mock_client)
        loop._running = True

        loop.set_manual_override(True)  # global wizard mode
        loop.set_control_manual("A", 75.0)  # would write, but global gate wins

        mock_client.set_openfan_pwm.assert_not_called()


class TestMemberOutputs:
    """DEC-119: per-member outputs let the card report a GPU member that idles
    below the control-wide value in a mixed control."""

    def test_mixed_gpu_member_diverges(self, state, profile_service, qtbot):
        curve = CurveConfig(
            id="cv",
            name="CV",
            type=CurveType.FLAT,
            sensor_id="cpu_temp",
            flat_output_pct=10.0,
        )
        ctrl = LogicalControl(
            id="mixed",
            name="Mixed",
            mode=ControlMode.CURVE,
            curve_id="cv",
            minimum_pct=20.0,  # chassis floor applies to non-GPU members only
            members=[
                ControlMember(source="openfan", member_id="openfan:ch00"),
                ControlMember(source="amd_gpu", member_id="amd_gpu:0000:03:00.0"),
            ],
        )
        profile_service._profiles["mx"] = Profile(
            id="mx", name="MX", controls=[ctrl], curves=[curve]
        )
        profile_service.set_active("mx")
        loop = ControlLoopService(state, profile_service, client=MagicMock())
        loop._running = True

        captured: list = []
        loop.status_changed.connect(captured.append)
        loop._cycle()

        status = captured[-1]
        members = status.member_outputs["mixed"]
        assert members["openfan:ch00"] == pytest.approx(20.0)  # chassis floored up
        assert members["amd_gpu:0000:03:00.0"] == pytest.approx(10.0)  # GPU not floored
        assert status.control_outputs["mixed"] == pytest.approx(20.0)


class TestPrerequisites:
    def test_disconnected_fails(self, state, profile_service, qtbot):
        state.connection = ConnectionState.DISCONNECTED
        loop = ControlLoopService(state, profile_service)
        assert loop._prerequisites_met() is False

    def test_read_only_fails(self, state, profile_service, qtbot):
        state.mode = OperationMode.READ_ONLY
        loop = ControlLoopService(state, profile_service)
        assert loop._prerequisites_met() is False

    def test_demo_mode_passes(self, state, profile_service, qtbot):
        state.mode = OperationMode.DEMO
        loop = ControlLoopService(state, profile_service)
        assert loop._prerequisites_met() is True

    def test_automatic_connected_passes(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        assert loop._prerequisites_met() is True


class TestVerifyPause:
    """A1: control loop pauses writes to a header under in-flight verify so
    its 1Hz tick does not race the daemon's 3-second verify wait."""

    def test_pause_blocks_writes_for_header(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        loop.pause_writes_for_header("hwmon:it8696:it87.2624:pwm1:pwm1")
        # Direct write attempt is short-circuited and returns False; nothing
        # should be sent through the demo path even when DEMO mode is set.
        loop._state.mode = OperationMode.DEMO
        loop._demo = MagicMock()
        result = loop._write_target("hwmon:it8696:it87.2624:pwm1:pwm1", 50.0)
        assert result is False
        loop._demo.set_fan_pwm.assert_not_called()

    def test_pause_isolated_to_named_header(self, state, profile_service, qtbot):
        """Pausing one header must not block writes to other headers."""
        loop = ControlLoopService(state, profile_service)
        loop.pause_writes_for_header("hwmon:it8696:it87.2624:pwm1:pwm1")
        loop._state.mode = OperationMode.DEMO
        loop._demo = MagicMock()
        result = loop._write_target("hwmon:it8696:it87.2624:pwm2:pwm2", 50.0)
        assert result is True
        loop._demo.set_fan_pwm.assert_called_once()

    def test_resume_releases_pause(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        loop.pause_writes_for_header("hwmon:it8696:it87.2624:pwm1:pwm1")
        assert loop._is_write_paused("hwmon:it8696:it87.2624:pwm1:pwm1") is True
        loop.resume_writes_for_header("hwmon:it8696:it87.2624:pwm1:pwm1")
        assert loop._is_write_paused("hwmon:it8696:it87.2624:pwm1:pwm1") is False

    def test_overlapping_pauses_share_state_safely(self, state, profile_service, qtbot):
        """Calling pause twice for the same header must not break resume —
        a single resume call ends the pause regardless of how many pauses
        were issued. (Resume is the verify worker's signal that a verify
        finished; the safety timer covers the runaway-pause case.)"""
        loop = ControlLoopService(state, profile_service)
        loop.pause_writes_for_header("hwmon:foo:bar:pwm1:pwm1")
        loop.pause_writes_for_header("hwmon:foo:bar:pwm1:pwm1")
        assert loop._is_write_paused("hwmon:foo:bar:pwm1:pwm1") is True
        loop.resume_writes_for_header("hwmon:foo:bar:pwm1:pwm1")
        assert loop._is_write_paused("hwmon:foo:bar:pwm1:pwm1") is False

    def test_safety_resume_with_stale_token_is_noop(self, state, profile_service, qtbot):
        """A safety callback fired with an outdated token (because a newer
        pause replaced the entry) must not resume the active pause."""
        loop = ControlLoopService(state, profile_service)
        loop.pause_writes_for_header("hwmon:foo:bar:pwm1:pwm1")
        first_token = loop._paused_headers["hwmon:foo:bar:pwm1:pwm1"]

        # Newer pause bumps the token.
        loop.pause_writes_for_header("hwmon:foo:bar:pwm1:pwm1")
        new_token = loop._paused_headers["hwmon:foo:bar:pwm1:pwm1"]
        assert new_token != first_token

        # Stale safety callback (token = first_token) must be a no-op.
        loop._safety_resume("hwmon:foo:bar:pwm1:pwm1", first_token)
        assert loop._is_write_paused("hwmon:foo:bar:pwm1:pwm1") is True

        # Matching safety callback resumes.
        loop._safety_resume("hwmon:foo:bar:pwm1:pwm1", new_token)
        assert loop._is_write_paused("hwmon:foo:bar:pwm1:pwm1") is False

    def test_safety_resume_for_unknown_header_is_noop(self, state, profile_service, qtbot):
        """If the user already called resume_writes_for_header before the
        safety timer fires, the safety callback must not raise."""
        loop = ControlLoopService(state, profile_service)
        # Never paused, never inserted — must not KeyError.
        loop._safety_resume("hwmon:not:paused:pwm1:pwm1", 1)

    def test_pause_empty_header_id_ignored(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        loop.pause_writes_for_header("")
        assert loop._paused_headers == {}
        loop.resume_writes_for_header("")  # must not raise

    def test_paused_header_not_written_during_cycle(self, state, profile_service, qtbot):
        """End-to-end: a paused hwmon header is skipped by the cycle's write
        path. Mirrors the actual race the GUI control loop creates against
        the daemon's verify wait — what A1 prevents."""
        target_id = "hwmon:it8696:it87.2624:pwm1:pwm1"
        profile = _make_profile_with_curve([(30, 20), (70, 80)], target_id=target_id)
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        # Pretend GUI holds a lease (control loop checks this for hwmon writes).
        lease_mock = MagicMock()
        lease_mock.is_held = True
        lease_mock.lease_id = "lease-1"

        mock_client = MagicMock()
        loop = ControlLoopService(
            state, profile_service, client=mock_client, lease_service=lease_mock
        )

        # First cycle without pause — write goes through.
        loop._cycle()
        assert mock_client.set_hwmon_pwm.call_count == 1

        # Pause and re-cycle — no second write.
        mock_client.reset_mock()
        loop.pause_writes_for_header(target_id)
        loop._cycle()
        assert mock_client.set_hwmon_pwm.call_count == 0

        # Resume and re-cycle — temp must change so hysteresis allows a write.
        state.sensors = [
            SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=70.0, age_ms=500),
        ]
        loop.resume_writes_for_header(target_id)
        loop._cycle()
        assert mock_client.set_hwmon_pwm.call_count == 1


class TestDemoWrite:
    def test_demo_write(self, state, profile_service, demo_service, qtbot):
        state.mode = OperationMode.DEMO
        loop = ControlLoopService(state, profile_service, demo_service=demo_service)
        assert loop._write_target("openfan:ch00", 55.0) is True
        demo_service.set_fan_pwm.assert_called_once_with("openfan:ch00", 55)


class TestStaleSensor:
    def test_invalid_sensor_skips(self, state, profile_service, qtbot):
        state.sensors = [
            SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=50.0, age_ms=15000),
        ]
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)
        loop._cycle()
        assert len(loop._target_states) == 0

    def test_stale_sensor_warns_but_continues(self, state, profile_service, qtbot):
        state.sensors = [
            SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=50.0, age_ms=5000),
        ]
        profile = _make_profile_with_curve([(30, 20), (70, 80)])
        profile_service._profiles["test"] = profile
        profile_service.set_active("test")

        loop = ControlLoopService(state, profile_service)
        loop._cycle()
        assert "test_control" in loop._target_states


class TestStartStop:
    def test_start_stop(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        loop.start()
        assert loop.is_running is True
        loop.stop()
        assert loop.is_running is False

    def test_shutdown(self, state, profile_service, qtbot):
        loop = ControlLoopService(state, profile_service)
        loop.start()
        loop.shutdown()
        assert loop.is_running is False

    def test_shutdown_quits_thread_before_closing_worker_client(
        self, state, profile_service, qtbot
    ):
        """P2-C regression: ControlLoopService.shutdown() must quit + wait
        the write thread BEFORE invoking worker.shutdown() (which closes the
        worker's DaemonClient). Doing it in the opposite order races the
        worker's do_write slot, which reads `worker._client` on the worker
        thread.

        We lock the ordering by spying on the relevant calls and asserting
        their relative order. A real production setup is hard to instrument
        from a test because the thread machinery is internal — so we drive
        the same code path with a minimal stub worker and a stub thread.
        """
        from unittest.mock import MagicMock

        loop = ControlLoopService(state, profile_service)

        events: list[str] = []
        fake_thread = MagicMock()
        fake_thread.quit = MagicMock(side_effect=lambda: events.append("thread.quit"))
        fake_thread.wait = MagicMock(
            side_effect=lambda *args, **kwargs: events.append("thread.wait") or True
        )
        fake_worker = MagicMock()
        fake_worker.shutdown = MagicMock(side_effect=lambda: events.append("worker.shutdown"))

        # Inject the stubs in place of the real worker/thread the service may
        # have created in __init__ (none, since we didn't pass socket_path).
        loop._write_thread = fake_thread
        loop._write_worker = fake_worker

        loop.shutdown()

        # Must observe: thread.quit → thread.wait → worker.shutdown.
        # Critically NOT: worker.shutdown ... thread.quit (the pre-fix order).
        assert events == ["thread.quit", "thread.wait", "worker.shutdown"], (
            f"shutdown order must be quit → wait → close; got {events}"
        )


class TestLeaseGating:
    """Lease acquisition is gated on capabilities.hwmon.present (M4)."""

    def _make_caps(self, hwmon_present: bool):
        from control_ofc.api.models import (
            Capabilities,
            FeatureFlags,
            HwmonCapability,
            OpenfanCapability,
            SafetyLimits,
        )

        return Capabilities(
            openfan=OpenfanCapability(present=True, channels=10, write_support=True),
            hwmon=HwmonCapability(present=hwmon_present, write_support=hwmon_present),
            features=FeatureFlags(),
            limits=SafetyLimits(),
        )

    def test_start_skips_lease_when_hwmon_absent(self, state, profile_service, qtbot):
        """OpenFan-only or GPU-only systems must not try to acquire the lease."""
        state.capabilities = self._make_caps(hwmon_present=False)
        lease = MagicMock()
        lease.is_held = False
        lease.lease_lost = MagicMock()
        lease.lease_lost.connect = MagicMock()

        loop = ControlLoopService(state, profile_service, lease_service=lease)
        loop.start()

        lease.acquire.assert_not_called()

    def test_start_acquires_lease_when_hwmon_present(self, state, profile_service, qtbot):
        state.capabilities = self._make_caps(hwmon_present=True)
        lease = MagicMock()
        lease.is_held = False
        lease.lease_lost = MagicMock()
        lease.lease_lost.connect = MagicMock()

        loop = ControlLoopService(state, profile_service, lease_service=lease)
        loop.start()

        lease.acquire.assert_called_once()

    def test_start_skips_when_capabilities_not_yet_known(self, state, profile_service, qtbot):
        """If capabilities haven't arrived yet, we must not blindly acquire."""
        state.capabilities = None
        lease = MagicMock()
        lease.is_held = False
        lease.lease_lost = MagicMock()
        lease.lease_lost.connect = MagicMock()

        loop = ControlLoopService(state, profile_service, lease_service=lease)
        loop.start()

        lease.acquire.assert_not_called()

    def test_capabilities_update_acquires_lease_when_hwmon_becomes_present(
        self, state, profile_service, qtbot
    ):
        """If hwmon appears after startup (e.g. rescan), lease is acquired."""
        state.capabilities = self._make_caps(hwmon_present=False)
        lease = MagicMock()
        lease.is_held = False
        lease.lease_lost = MagicMock()
        lease.lease_lost.connect = MagicMock()

        loop = ControlLoopService(state, profile_service, lease_service=lease)
        loop.start()
        assert lease.acquire.call_count == 0

        # Hwmon becomes available (e.g. after rescan)
        state.set_capabilities(self._make_caps(hwmon_present=True))

        assert lease.acquire.call_count == 1


class TestManualModeControl:
    """Logical control in manual mode applies fixed output."""

    def test_manual_mode_uses_fixed_output(self, state, profile_service, demo_service, qtbot):
        """Manual mode control writes manual_output_pct to members."""
        state.mode = OperationMode.DEMO
        curve = CurveConfig(id="c1", name="C", type=CurveType.GRAPH, points=[])
        control = LogicalControl(
            id="manual_ctrl",
            name="Manual Test",
            mode=ControlMode.MANUAL,
            manual_output_pct=75.0,
            curve_id="c1",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile = Profile(id="mp", name="ManualProfile", controls=[control], curves=[curve])
        profile_service._profiles["mp"] = profile
        profile_service.set_active("mp")

        loop = ControlLoopService(state, profile_service, demo_service=demo_service)
        loop._cycle()

        demo_service.set_fan_pwm.assert_called_with("openfan:ch00", 75)


class TestCurveTypes:
    """Different curve types interpolate correctly."""

    def test_linear_curve_interpolation(self, state, profile_service, qtbot):
        curve = CurveConfig(
            id="lin",
            name="Linear",
            type=CurveType.LINEAR,
            sensor_id="cpu_temp",
            start_temp_c=30.0,
            start_output_pct=20.0,
            end_temp_c=70.0,
            end_output_pct=80.0,
        )
        control = LogicalControl(
            id="lc",
            name="Linear Control",
            mode=ControlMode.CURVE,
            curve_id="lin",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile = Profile(id="lp", name="LinearProfile", controls=[control], curves=[curve])
        profile_service._profiles["lp"] = profile
        profile_service.set_active("lp")

        loop = ControlLoopService(state, profile_service)
        loop._cycle()

        ts = loop._target_states.get("lc")
        assert ts is not None
        # 50C on 30-70 range: 20 + (50-30)/(70-30) * 60 = 50%
        assert ts.last_commanded_pwm == pytest.approx(50.0)

    def test_flat_curve_constant_output(self, state, profile_service, qtbot):
        curve = CurveConfig(
            id="flat",
            name="Flat",
            type=CurveType.FLAT,
            sensor_id="cpu_temp",
            flat_output_pct=65.0,
        )
        control = LogicalControl(
            id="fc",
            name="Flat Control",
            mode=ControlMode.CURVE,
            curve_id="flat",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile = Profile(id="fp", name="FlatProfile", controls=[control], curves=[curve])
        profile_service._profiles["fp"] = profile
        profile_service.set_active("fp")

        loop = ControlLoopService(state, profile_service)
        loop._cycle()

        ts = loop._target_states.get("fc")
        assert ts is not None
        assert ts.last_commanded_pwm == pytest.approx(65.0)


class TestTuningPipeline:
    """Per-control tuning: offset, minimum, step rate, start/stop."""

    def test_offset_applied(self, state, profile_service, qtbot):
        curve = CurveConfig(
            id="c", name="C", type=CurveType.FLAT, sensor_id="cpu_temp", flat_output_pct=40.0
        )
        control = LogicalControl(
            id="tc",
            name="T",
            mode=ControlMode.CURVE,
            curve_id="c",
            offset_pct=10.0,
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile = Profile(id="tp", name="TP", controls=[control], curves=[curve])
        profile_service._profiles["tp"] = profile
        profile_service.set_active("tp")
        loop = ControlLoopService(state, profile_service)
        loop._cycle()
        # 40% + 10% offset = 50%
        assert loop._target_states["tc"].last_output == pytest.approx(50.0)

    def test_minimum_floor(self, state, profile_service, qtbot):
        curve = CurveConfig(
            id="c", name="C", type=CurveType.FLAT, sensor_id="cpu_temp", flat_output_pct=10.0
        )
        control = LogicalControl(
            id="tc",
            name="T",
            mode=ControlMode.CURVE,
            curve_id="c",
            minimum_pct=25.0,
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile = Profile(id="tp", name="TP", controls=[control], curves=[curve])
        profile_service._profiles["tp"] = profile
        profile_service.set_active("tp")
        loop = ControlLoopService(state, profile_service)
        loop._cycle()
        assert loop._target_states["tc"].last_output == pytest.approx(25.0)

    def test_step_up_rate_limiting(self, state, profile_service, qtbot):
        curve = CurveConfig(
            id="c", name="C", type=CurveType.FLAT, sensor_id="cpu_temp", flat_output_pct=80.0
        )
        control = LogicalControl(
            id="tc",
            name="T",
            mode=ControlMode.CURVE,
            curve_id="c",
            step_up_pct=5.0,
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile = Profile(id="tp", name="TP", controls=[control], curves=[curve])
        profile_service._profiles["tp"] = profile
        profile_service.set_active("tp")
        loop = ControlLoopService(state, profile_service)

        # First cycle: no prior output, goes to 80%
        loop._cycle()
        first = loop._target_states["tc"].last_output

        # Second cycle: step_up=5 means max +5 from previous
        # But we're already at 80, curve says 80, so no change
        loop._cycle()
        second = loop._target_states["tc"].last_output
        assert second == first  # no change needed

    def test_step_down_rate_limiting(self, state, profile_service, qtbot):
        """Output drop limited by step_down_pct per cycle."""
        # Use a graph curve so we can change output by changing temperature
        curve = CurveConfig(
            id="c",
            name="C",
            type=CurveType.GRAPH,
            sensor_id="cpu_temp",
            points=[CurvePoint(30, 20), CurvePoint(70, 80)],
        )
        control = LogicalControl(
            id="tc",
            name="T",
            mode=ControlMode.CURVE,
            curve_id="c",
            step_down_pct=10.0,
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile = Profile(id="tp", name="TP", controls=[control], curves=[curve])
        profile_service._profiles["tp"] = profile
        profile_service.set_active("tp")
        loop = ControlLoopService(state, profile_service)

        # First cycle at 70°C → 80% output
        state.sensors = [
            SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=70.0, age_ms=500),
        ]
        loop._cycle()
        assert loop._target_states["tc"].last_output == pytest.approx(80.0)

        # Drop temp to 30°C → curve says 20%, but step_down=10 limits to 70
        state.sensors = [
            SensorReading(id="cpu_temp", kind="CpuTemp", label="CPU", value_c=30.0, age_ms=500),
        ]
        loop._cycle()
        assert loop._target_states["tc"].last_output == pytest.approx(70.0)

    def test_stop_threshold_snaps_to_zero(self, state, profile_service, qtbot):
        curve = CurveConfig(
            id="c", name="C", type=CurveType.FLAT, sensor_id="cpu_temp", flat_output_pct=15.0
        )
        control = LogicalControl(
            id="tc",
            name="T",
            mode=ControlMode.CURVE,
            curve_id="c",
            stop_pct=20.0,
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile = Profile(id="tp", name="TP", controls=[control], curves=[curve])
        profile_service._profiles["tp"] = profile
        profile_service.set_active("tp")
        loop = ControlLoopService(state, profile_service)
        loop._cycle()
        # 15% < stop_pct=20% -> snaps to 0%
        assert loop._target_states["tc"].last_output == pytest.approx(0.0)

    def test_control_output_in_status(self, state, profile_service, qtbot):
        curve = CurveConfig(
            id="c", name="C", type=CurveType.FLAT, sensor_id="cpu_temp", flat_output_pct=55.0
        )
        control = LogicalControl(
            id="tc",
            name="T",
            mode=ControlMode.CURVE,
            curve_id="c",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile = Profile(id="tp", name="TP", controls=[control], curves=[curve])
        profile_service._profiles["tp"] = profile
        profile_service.set_active("tp")

        loop = ControlLoopService(state, profile_service)
        statuses = []
        loop.status_changed.connect(lambda s: statuses.append(s))
        loop._cycle()
        assert len(statuses) == 1
        assert "tc" in statuses[0].control_outputs
        assert statuses[0].control_outputs["tc"] == pytest.approx(55.0)


class TestDispatchWrite:
    """Finding E: the shared write-dispatch helper routes by target-id prefix
    and preserves each caller's exact call shape (timeout omitted vs present),
    which is what keeps the worker and sync paths behaviourally identical."""

    def test_openfan_route(self):
        c = MagicMock()
        assert _dispatch_write(c, "openfan:ch03", 55, "") is True
        c.set_openfan_pwm.assert_called_once_with(3, 55)

    def test_amd_gpu_route(self):
        c = MagicMock()
        assert _dispatch_write(c, "amd_gpu:0000:2d:00.0", 60, "") is True
        c.set_gpu_fan_speed.assert_called_once_with("0000:2d:00.0", 60)

    def test_hwmon_route_with_lease(self):
        c = MagicMock()
        assert _dispatch_write(c, "hwmon:it8696:pci0:pwm1", 40, "lease-1") is True
        c.set_hwmon_pwm.assert_called_once_with("hwmon:it8696:pci0:pwm1", 40, "lease-1")

    def test_hwmon_without_lease_returns_false_and_does_not_write(self):
        c = MagicMock()
        assert _dispatch_write(c, "hwmon:it8696:pci0:pwm1", 40, "") is False
        c.set_hwmon_pwm.assert_not_called()

    def test_unknown_target_returns_false(self):
        c = MagicMock()
        assert _dispatch_write(c, "bogus:xyz", 40, "lease-1") is False
        c.set_openfan_pwm.assert_not_called()
        c.set_gpu_fan_speed.assert_not_called()
        c.set_hwmon_pwm.assert_not_called()

    def test_timeout_forwarded_when_set(self):
        c = MagicMock()
        _dispatch_write(c, "openfan:ch00", 50, "", timeout=2.0)
        c.set_openfan_pwm.assert_called_once_with(0, 50, timeout=2.0)

    def test_timeout_omitted_when_none(self):
        # The sync/test path passes no timeout; the kwarg must be absent so
        # existing exact-arg assertions (fan wizard, GPU parity) keep matching.
        c = MagicMock()
        _dispatch_write(c, "openfan:ch00", 50, "")
        c.set_openfan_pwm.assert_called_once_with(0, 50)
