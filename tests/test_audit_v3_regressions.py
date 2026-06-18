"""V3 Audit regression tests — P0 fixes for hysteresis, div/zero, and CalPoint.

These tests specifically guard against the three P0 bugs found in the V3 audit.
Each test would FAIL on the pre-fix code and PASS on the fixed code.
"""

from __future__ import annotations

from control_ofc.api.models import (
    parse_calibration_result,
    parse_sensor_history,
)
from control_ofc.services.profile_service import CurveConfig, CurveType

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
