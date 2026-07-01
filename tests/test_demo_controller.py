"""Tests for the demo-mode mini-evaluator (DemoController, DEC-165)."""

from __future__ import annotations

from control_ofc.api.models import SensorReading
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_controller import DemoController
from control_ofc.services.demo_service import DemoService
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


def _setup(curve, ctrl, sensor_temp=60.0):
    state = AppState()
    state.set_sensors(
        [SensorReading(id="cpu", kind="CpuTemp", label="t", value_c=sensor_temp, age_ms=10)]
    )
    demo = DemoService()
    ps = ProfileService()
    prof = Profile(id="p", name="P", controls=[ctrl], curves=[curve])
    ps._profiles["p"] = prof
    ps.set_active("p")
    return DemoController(ps, demo, state), demo


def _flat_control():
    curve = CurveConfig(id="c", type=CurveType.FLAT, sensor_id="cpu", flat_output_pct=70.0)
    ctrl = LogicalControl(
        id="lc",
        curve_id="c",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    return curve, ctrl


def test_evaluates_flat_curve_to_fan_pwm(qtbot):
    dc, demo = _setup(*_flat_control())
    dc.tick()
    assert demo._fan_pwm["openfan:ch00"] == 70


def test_graph_curve_interpolates_at_sensor_temp(qtbot):
    curve = CurveConfig(
        id="c",
        type=CurveType.GRAPH,
        sensor_id="cpu",
        points=[CurvePoint(40.0, 20.0), CurvePoint(80.0, 100.0)],
    )
    ctrl = LogicalControl(
        id="lc",
        curve_id="c",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    dc, demo = _setup(curve, ctrl, sensor_temp=60.0)  # midpoint -> 60%
    dc.tick()
    assert demo._fan_pwm["openfan:ch00"] == 60


def test_mix_curve_falls_back_to_flat_output(qtbot):
    """Demo mode uses the stateless interpolate() only; a Mix curve needs a
    multi-curve resolution context it doesn't have, so it returns its
    flat_output_pct (documented in CLAUDE.md + interpolate()'s docstring). Pin the
    degenerate behavior so a regression (raise / 0 / wrong input) can't slip."""
    curve = CurveConfig(id="c", type=CurveType.MIX, sensor_id="cpu", flat_output_pct=60.0)
    ctrl = LogicalControl(
        id="lc",
        curve_id="c",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    dc, demo = _setup(curve, ctrl)
    dc.tick()
    assert demo._fan_pwm["openfan:ch00"] == 60


def test_sync_curve_falls_back_to_flat_output(qtbot):
    """Demo mode Sync curves likewise return flat_output_pct (Sync mirrors another
    control's output, which the stateless demo evaluator can't resolve)."""
    curve = CurveConfig(id="c", type=CurveType.SYNC, sensor_id="cpu", flat_output_pct=45.0)
    ctrl = LogicalControl(
        id="lc",
        curve_id="c",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    dc, demo = _setup(curve, ctrl)
    dc.tick()
    assert demo._fan_pwm["openfan:ch00"] == 45


def test_manual_pin_overrides_curve(qtbot):
    dc, demo = _setup(*_flat_control())

    dc.set_control_manual("lc", 25.0)
    assert demo._fan_pwm["openfan:ch00"] == 25

    dc.clear_control_manual("lc")
    dc.tick()
    assert demo._fan_pwm["openfan:ch00"] == 70


def test_static_manual_mode_uses_manual_output(qtbot):
    curve = CurveConfig(id="c", type=CurveType.FLAT, sensor_id="cpu", flat_output_pct=70.0)
    ctrl = LogicalControl(
        id="lc",
        mode=ControlMode.MANUAL,
        manual_output_pct=33.0,
        curve_id="c",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    dc, demo = _setup(curve, ctrl)
    dc.tick()
    assert demo._fan_pwm["openfan:ch00"] == 33


def test_emits_outputs_changed(qtbot):
    dc, _demo = _setup(*_flat_control())
    seen: list[dict] = []
    dc.outputs_changed.connect(seen.append)
    dc.tick()
    assert seen and seen[-1]["lc"] == 70.0


def test_no_active_profile_is_noop(qtbot):
    dc = DemoController(ProfileService(), DemoService(), AppState())
    dc.tick()  # must not raise; emits nothing
