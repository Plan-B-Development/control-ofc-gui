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
from control_ofc.services.control_loop import ControlLoopService
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
        assert loop._should_write("openfan:ch00", 40.5) is False

    def test_meaningful_change_allowed(self, state, profile_service, qtbot):
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=40, age_ms=500
            ),
        ]
        loop = ControlLoopService(state, profile_service)
        assert loop._should_write("openfan:ch00", 42.0) is True

    def test_no_prior_pwm_allows_write(self, state, profile_service, qtbot):
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=None, age_ms=500
            ),
        ]
        loop = ControlLoopService(state, profile_service)
        assert loop._should_write("openfan:ch00", 50.0) is True


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
