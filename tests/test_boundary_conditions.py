"""Boundary condition tests — exact thresholds for write suppression and hysteresis."""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import (
    ConnectionState,
    FanReading,
    OperationMode,
    SensorReading,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import ControlLoopService
from control_ofc.services.profile_service import (
    CurveConfig,
    CurvePoint,
    CurveType,
)


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _make_loop(state: AppState | None = None) -> ControlLoopService:
    state = state or _make_state()
    return ControlLoopService(state=state, profile_service=MagicMock())


def _make_sensor(temp: float) -> dict[str, SensorReading]:
    return {
        "s1": SensorReading(
            id="s1", kind="CpuTemp", label="Tctl", value_c=temp, source="hwmon", age_ms=100
        )
    }


# ---------------------------------------------------------------------------
# Write suppression threshold (PWM_WRITE_THRESHOLD_PCT = 1)
# ---------------------------------------------------------------------------


class TestWriteSuppressionBoundary:
    """Verify exact boundary of the 1% write suppression threshold."""

    def test_delta_below_threshold_is_suppressed(self):
        """0.99% delta → suppressed (< 1)."""
        loop = _make_loop()
        state = loop._state
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=50.0, age_ms=500
            ),
        ]
        assert loop._should_write("openfan:ch00", 50.99) is False

    def test_delta_at_threshold_triggers_write(self):
        """1.0% delta → write (>= 1)."""
        loop = _make_loop()
        state = loop._state
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=50.0, age_ms=500
            ),
        ]
        assert loop._should_write("openfan:ch00", 51.0) is True

    def test_delta_above_threshold_triggers_write(self):
        """2.0% delta → write."""
        loop = _make_loop()
        state = loop._state
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=50.0, age_ms=500
            ),
        ]
        assert loop._should_write("openfan:ch00", 52.0) is True

    def test_negative_delta_at_threshold_triggers_write(self):
        """1.0% delta downward → write."""
        loop = _make_loop()
        state = loop._state
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=50.0, age_ms=500
            ),
        ]
        assert loop._should_write("openfan:ch00", 49.0) is True

    def test_no_prior_pwm_always_writes(self):
        """No last_commanded_pwm → always write (first command)."""
        loop = _make_loop()
        state = loop._state
        state.fans = [
            FanReading(
                id="openfan:ch00", source="openfan", rpm=800, last_commanded_pwm=None, age_ms=500
            ),
        ]
        assert loop._should_write("openfan:ch00", 50.0) is True


# ---------------------------------------------------------------------------
# Hysteresis deadband boundary (HYSTERESIS_DEADBAND_C = 2.0)
# ---------------------------------------------------------------------------


class TestHysteresisDeadbandBoundary:
    """Verify exact boundary of the 2 deg C hysteresis deadband."""

    def _eval(self, loop, temp):
        status = MagicMock()
        status.warnings = []
        curve = CurveConfig(
            id="c1",
            name="Step",
            type=CurveType.GRAPH,
            points=[CurvePoint(temp_c=30, output_pct=20), CurvePoint(temp_c=70, output_pct=80)],
        )
        return loop._evaluate_curve_with_hysteresis("ctrl1", curve, _make_sensor(temp), {}, status)

    def test_at_transition_temp_holds(self):
        """Temp exactly at transition point → within deadband → holds."""
        loop = _make_loop()
        self._eval(loop, 50.0)
        ts = loop._target_states["ctrl1"]
        assert ts.last_transition_temp == 50.0

        result = self._eval(loop, 50.0)
        assert result == ts.last_commanded_pwm

    def test_drop_within_deadband_holds(self):
        """1 deg C drop (50 → 49) → within 2 deg C deadband → holds."""
        loop = _make_loop()
        self._eval(loop, 50.0)
        held_pwm = loop._target_states["ctrl1"].last_commanded_pwm

        result = self._eval(loop, 49.0)
        assert result == held_pwm

    def test_drop_at_exact_boundary_holds(self):
        """Exactly 2 deg C drop (50 → 48) → still within deadband (>=) → holds."""
        loop = _make_loop()
        self._eval(loop, 50.0)
        held_pwm = loop._target_states["ctrl1"].last_commanded_pwm

        result = self._eval(loop, 48.0)
        assert result == held_pwm

    def test_drop_beyond_boundary_reevaluates(self):
        """2.01 deg C drop (50 → 47.99) → outside deadband → re-evaluates."""
        loop = _make_loop()
        self._eval(loop, 50.0)
        anchor = loop._target_states["ctrl1"].last_transition_temp
        assert anchor == 50.0

        result = self._eval(loop, 47.99)
        expected = CurveConfig(
            id="c1",
            name="Step",
            type=CurveType.GRAPH,
            points=[CurvePoint(temp_c=30, output_pct=20), CurvePoint(temp_c=70, output_pct=80)],
        ).interpolate(47.99)
        assert abs(result - expected) < 0.1

    def test_rise_above_transition_no_deadband(self):
        """Rising temperature above transition → no deadband effect."""
        loop = _make_loop()
        self._eval(loop, 50.0)

        result = self._eval(loop, 55.0)
        expected = CurveConfig(
            id="c1",
            name="Step",
            type=CurveType.GRAPH,
            points=[CurvePoint(temp_c=30, output_pct=20), CurvePoint(temp_c=70, output_pct=80)],
        ).interpolate(55.0)
        assert abs(result - expected) < 0.1
