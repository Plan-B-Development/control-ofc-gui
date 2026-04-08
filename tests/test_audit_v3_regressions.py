"""V3 Audit regression tests — P0 fixes for hysteresis, div/zero, and CalPoint.

These tests specifically guard against the three P0 bugs found in the V3 audit.
Each test would FAIL on the pre-fix code and PASS on the fixed code.
"""

from __future__ import annotations

from control_ofc.api.models import (
    ConnectionState,
    OperationMode,
    SensorReading,
    parse_calibration_result,
    parse_sensor_history,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import CurveConfig, CurvePoint, CurveType

# ---------------------------------------------------------------------------
# P0-01: Hysteresis deadband must anchor, not follow
# ---------------------------------------------------------------------------


class TestHysteresisDeadbandAnchor:
    """The deadband must hold at the transition point, not follow rising temp."""

    def test_rising_temp_does_not_move_anchor_without_output_change(self):
        """When temp rises within the same curve segment (same output), the
        anchor should NOT move — otherwise the deadband follows upward and
        never holds on a subsequent small drop."""
        from unittest.mock import MagicMock

        from control_ofc.services.control_loop import ControlLoopService

        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)

        profile_svc = MagicMock()
        svc = ControlLoopService(state=state, profile_service=profile_svc)

        # Flat curve: always returns 50% regardless of temp
        flat_curve = CurveConfig(
            id="c1",
            name="Flat",
            type=CurveType.FLAT,
            flat_output_pct=50.0,
        )

        status = MagicMock()
        status.warnings = []

        def make_sensor(temp: float) -> dict[str, SensorReading]:
            return {
                "s1": SensorReading(
                    id="s1",
                    kind="cpu_temp",
                    label="Tctl",
                    value_c=temp,
                    source="hwmon",
                    age_ms=100,
                )
            }

        # Step 1: evaluate at 40C — initial, anchor set at 40C
        result = svc._evaluate_curve_with_hysteresis(
            "ctrl1", flat_curve, make_sensor(40.0), {}, status
        )
        assert result == 50.0
        ts = svc._target_states["ctrl1"]
        anchor_after_40 = ts.last_transition_temp

        # Step 2: evaluate at 42C — same output (50%), anchor should NOT move
        result = svc._evaluate_curve_with_hysteresis(
            "ctrl1", flat_curve, make_sensor(42.0), {}, status
        )
        assert result == 50.0
        assert ts.last_transition_temp == anchor_after_40, (
            "Anchor moved on rising temp with same output — deadband defeated"
        )

        # Step 3: evaluate at 41C (1C drop from 42, within 2C deadband of anchor)
        # This SHOULD hold at 50% because we're within the deadband
        result = svc._evaluate_curve_with_hysteresis(
            "ctrl1", flat_curve, make_sensor(41.0), {}, status
        )
        assert result == 50.0

    def test_real_transition_does_move_anchor(self):
        """When the curve output changes (different segment), the anchor
        SHOULD update to the new transition temperature."""
        from unittest.mock import MagicMock

        from control_ofc.services.control_loop import ControlLoopService

        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)
        profile_svc = MagicMock()
        svc = ControlLoopService(state=state, profile_service=profile_svc)

        # Step curve: 20% at 30C, 80% at 70C
        step_curve = CurveConfig(
            id="c2",
            name="Step",
            type=CurveType.GRAPH,
            points=[
                CurvePoint(temp_c=30, output_pct=20),
                CurvePoint(temp_c=70, output_pct=80),
            ],
        )

        status = MagicMock()
        status.warnings = []

        def make_sensor(temp: float) -> dict[str, SensorReading]:
            return {
                "s1": SensorReading(
                    id="s1",
                    kind="cpu_temp",
                    label="Tctl",
                    value_c=temp,
                    source="hwmon",
                    age_ms=100,
                )
            }

        # Step 1: 30C → 20%
        svc._evaluate_curve_with_hysteresis("ctrl2", step_curve, make_sensor(30.0), {}, status)
        ts = svc._target_states["ctrl2"]
        anchor_30 = ts.last_transition_temp

        # Step 2: 50C → 50% (different output!) — anchor MUST move
        svc._evaluate_curve_with_hysteresis("ctrl2", step_curve, make_sensor(50.0), {}, status)
        assert ts.last_transition_temp != anchor_30, "Anchor did not move on real curve transition"


# ---------------------------------------------------------------------------
# P0-02: Linear curve must not divide by zero
# ---------------------------------------------------------------------------


class TestLinearCurveDivisionByZero:
    """A linear curve with start_temp == end_temp must not crash."""

    def test_equal_start_end_returns_start_output(self):
        curve = CurveConfig(
            id="dz",
            name="DivZero",
            type=CurveType.LINEAR,
            start_temp_c=50.0,
            end_temp_c=50.0,
            start_output_pct=30.0,
            end_output_pct=80.0,
        )
        # At the exact temperature — should return start_output_pct, not crash
        result = curve.interpolate(50.0)
        assert result == 30.0

    def test_below_equal_range_returns_start(self):
        curve = CurveConfig(
            id="dz2",
            name="DivZero2",
            type=CurveType.LINEAR,
            start_temp_c=50.0,
            end_temp_c=50.0,
            start_output_pct=30.0,
            end_output_pct=80.0,
        )
        assert curve.interpolate(40.0) == 30.0

    def test_above_equal_range_returns_end(self):
        curve = CurveConfig(
            id="dz3",
            name="DivZero3",
            type=CurveType.LINEAR,
            start_temp_c=50.0,
            end_temp_c=50.0,
            start_output_pct=30.0,
            end_output_pct=80.0,
        )
        assert curve.interpolate(60.0) == 80.0


# ---------------------------------------------------------------------------
# P0-03: CalPoint/HistoryPoint must not crash on extra fields
# ---------------------------------------------------------------------------


class TestCalPointExtraFields:
    """Daemon responses with extra unknown fields must not crash the parser."""

    def test_calpoint_with_extra_field(self):
        data = {
            "fan_id": "openfan:ch00",
            "points": [
                {
                    "pwm_percent": 50,
                    "rpm": 1200,
                    "future_field": "ignored",
                    "another_new_field": 42,
                },
            ],
            "min_rpm": 0,
            "max_rpm": 2000,
        }
        result = parse_calibration_result(data)
        assert len(result.points) == 1
        assert result.points[0].pwm_percent == 50
        assert result.points[0].rpm == 1200

    def test_historypoint_with_extra_field(self):
        data = {
            "entity_id": "hwmon:k10temp:Tctl",
            "points": [
                {"ts": 1000, "v": 45.5, "extra": "ignored"},
                {"ts": 2000, "v": 46.0, "something_new": True},
            ],
        }
        result = parse_sensor_history(data)
        assert len(result.points) == 2
        assert result.points[0].v == 45.5
        assert result.points[1].ts == 2000

    def test_calpoint_with_missing_optional_field(self):
        """CalPoint with only required fields (no start_pwm, stop_pwm)."""
        data = {
            "fan_id": "openfan:ch00",
            "points": [{"pwm_percent": 100, "rpm": 2000}],
            "min_rpm": 0,
            "max_rpm": 2000,
        }
        result = parse_calibration_result(data)
        assert result.start_pwm is None
        assert result.stop_pwm is None
