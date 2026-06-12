"""Tests for profile management, curve interpolation, and v1 migration."""

from __future__ import annotations

import json

import pytest

from control_ofc.services.profile_service import (
    PROFILE_SCHEMA_VERSION,
    ControlMember,
    ControlMode,
    CurveConfig,
    CurvePoint,
    CurveType,
    LogicalControl,
    Profile,
    ProfileService,
    default_profiles,
)

# ---------------------------------------------------------------------------
# Graph curve interpolation
# ---------------------------------------------------------------------------


def test_graph_curve_interpolation_within_range():
    curve = CurveConfig(
        type=CurveType.GRAPH,
        points=[
            CurvePoint(30.0, 20.0),
            CurvePoint(50.0, 50.0),
            CurvePoint(70.0, 80.0),
        ],
    )
    assert curve.interpolate(40.0) == 35.0


def test_graph_curve_interpolation_at_points():
    curve = CurveConfig(
        type=CurveType.GRAPH,
        points=[CurvePoint(30.0, 20.0), CurvePoint(70.0, 80.0)],
    )
    assert curve.interpolate(30.0) == 20.0
    assert curve.interpolate(70.0) == 80.0


def test_graph_curve_interpolation_below_range():
    curve = CurveConfig(
        type=CurveType.GRAPH,
        points=[CurvePoint(30.0, 20.0), CurvePoint(70.0, 80.0)],
    )
    assert curve.interpolate(10.0) == 20.0


def test_graph_curve_interpolation_above_range():
    curve = CurveConfig(
        type=CurveType.GRAPH,
        points=[CurvePoint(30.0, 20.0), CurvePoint(70.0, 80.0)],
    )
    assert curve.interpolate(90.0) == 80.0


def test_graph_curve_interpolation_empty():
    curve = CurveConfig(type=CurveType.GRAPH, points=[])
    assert curve.interpolate(50.0) == 50.0


# ---------------------------------------------------------------------------
# Linear curve interpolation
# ---------------------------------------------------------------------------


def test_linear_curve_within_range():
    curve = CurveConfig(
        type=CurveType.LINEAR,
        start_temp_c=30.0,
        start_output_pct=20.0,
        end_temp_c=70.0,
        end_output_pct=80.0,
    )
    assert curve.interpolate(50.0) == pytest.approx(50.0)


def test_linear_curve_below_range():
    curve = CurveConfig(
        type=CurveType.LINEAR,
        start_temp_c=30.0,
        start_output_pct=20.0,
        end_temp_c=70.0,
        end_output_pct=80.0,
    )
    assert curve.interpolate(10.0) == 20.0


def test_linear_curve_above_range():
    curve = CurveConfig(
        type=CurveType.LINEAR,
        start_temp_c=30.0,
        start_output_pct=20.0,
        end_temp_c=70.0,
        end_output_pct=80.0,
    )
    assert curve.interpolate(90.0) == 80.0


# ---------------------------------------------------------------------------
# Flat curve
# ---------------------------------------------------------------------------


def test_flat_curve_returns_constant():
    curve = CurveConfig(type=CurveType.FLAT, flat_output_pct=65.0)
    assert curve.interpolate(30.0) == 65.0
    assert curve.interpolate(80.0) == 65.0
    assert curve.interpolate(0.0) == 65.0


# ---------------------------------------------------------------------------
# Stepped curve interpolation (DEC-148)
# ---------------------------------------------------------------------------


def test_stepped_curve_holds_lower_point():
    """Stepped holds each point's output until the next point's temp — no ramp.
    The same points a graph would interpolate to 35% at 40°C stay at the lower
    point's 20% under the staircase rule."""
    curve = CurveConfig(
        type=CurveType.STEPPED,
        points=[
            CurvePoint(30.0, 20.0),
            CurvePoint(50.0, 50.0),
            CurvePoint(70.0, 80.0),
        ],
    )
    assert curve.interpolate(40.0) == 20.0  # a graph curve would return 35.0
    assert curve.interpolate(49.9) == 20.0
    assert curve.interpolate(60.0) == 50.0


def test_stepped_curve_node_is_half_open_lower():
    """Exactly on a node returns that node's output: each segment is the
    half-open interval [p_i, p_{i+1})."""
    curve = CurveConfig(
        type=CurveType.STEPPED,
        points=[CurvePoint(30.0, 20.0), CurvePoint(50.0, 50.0), CurvePoint(70.0, 80.0)],
    )
    assert curve.interpolate(30.0) == 20.0
    assert curve.interpolate(50.0) == 50.0
    assert curve.interpolate(70.0) == 80.0


def test_stepped_curve_below_and_above_range_clamp():
    curve = CurveConfig(
        type=CurveType.STEPPED,
        points=[CurvePoint(30.0, 20.0), CurvePoint(70.0, 80.0)],
    )
    assert curve.interpolate(10.0) == 20.0
    assert curve.interpolate(90.0) == 80.0


def test_stepped_curve_empty_returns_50():
    curve = CurveConfig(type=CurveType.STEPPED, points=[])
    assert curve.interpolate(50.0) == 50.0


# ---------------------------------------------------------------------------
# Unknown curve type fallback
# ---------------------------------------------------------------------------


def test_unknown_curve_type_falls_back_to_flat():
    """Loading a config with an unknown curve type (e.g. old 'mix') falls back to flat."""
    data = {"id": "old_mix", "name": "Old Mix", "type": "mix", "flat_output_pct": 42.0}
    curve = CurveConfig.from_dict(data)
    assert curve.type == CurveType.FLAT
    assert curve.interpolate(50.0) == 42.0


# ---------------------------------------------------------------------------
# Tuning parameters roundtrip
# ---------------------------------------------------------------------------


def test_logical_control_tuning_roundtrip():
    from control_ofc.services.profile_service import LogicalControl

    ctrl = LogicalControl(
        name="Test",
        step_up_pct=5.0,
        step_down_pct=3.0,
        start_pct=30.0,
        stop_pct=20.0,
        offset_pct=2.0,
        minimum_pct=15.0,
    )
    data = ctrl.to_dict()
    restored = LogicalControl.from_dict(data)
    assert restored.step_up_pct == 5.0
    assert restored.step_down_pct == 3.0
    assert restored.start_pct == 30.0
    assert restored.stop_pct == 20.0
    assert restored.offset_pct == 2.0
    assert restored.minimum_pct == 15.0


# ---------------------------------------------------------------------------
# Default profiles
# ---------------------------------------------------------------------------


def test_default_profiles_count():
    profiles = default_profiles()
    assert len(profiles) == 3
    names = {p.name for p in profiles}
    assert names == {"Quiet", "Balanced", "Performance"}


def test_default_profiles_have_controls_and_curves():
    for p in default_profiles():
        assert len(p.controls) >= 1
        assert len(p.curves) >= 1
        # Each control references a valid curve
        curve_ids = {c.id for c in p.curves}
        for ctrl in p.controls:
            assert ctrl.curve_id in curve_ids
        # Each graph curve has 5 points
        for c in p.curves:
            if c.type == CurveType.GRAPH:
                assert len(c.points) == 5


# ---------------------------------------------------------------------------
# Profile roundtrip
# ---------------------------------------------------------------------------


def test_profile_roundtrip():
    original = default_profiles()[0]
    data = original.to_dict()
    restored = Profile.from_dict(data)
    assert restored.name == original.name
    assert len(restored.controls) == len(original.controls)
    assert len(restored.curves) == len(original.curves)
    assert restored.curves[0].points[0].temp_c == original.curves[0].points[0].temp_c


def test_curve_config_roundtrip_graph():
    curve = CurveConfig(
        id="c1",
        name="Test",
        type=CurveType.GRAPH,
        sensor_id="cpu",
        points=[CurvePoint(30.0, 20.0), CurvePoint(80.0, 100.0)],
    )
    data = curve.to_dict()
    restored = CurveConfig.from_dict(data)
    assert restored.type == CurveType.GRAPH
    assert len(restored.points) == 2
    assert restored.sensor_id == "cpu"


def test_curve_config_roundtrip_stepped():
    curve = CurveConfig(
        id="cs",
        name="Stepped",
        type=CurveType.STEPPED,
        sensor_id="cpu",
        points=[CurvePoint(30.0, 20.0), CurvePoint(80.0, 100.0)],
    )
    data = curve.to_dict()
    assert data["type"] == "stepped"
    assert "points" in data  # stepped serializes its points like a graph
    restored = CurveConfig.from_dict(data)
    assert restored.type == CurveType.STEPPED
    assert len(restored.points) == 2
    assert restored.sensor_id == "cpu"


def test_curve_config_roundtrip_linear():
    curve = CurveConfig(
        id="c2",
        name="Linear",
        type=CurveType.LINEAR,
        start_temp_c=25.0,
        end_temp_c=85.0,
        start_output_pct=15.0,
        end_output_pct=95.0,
    )
    data = curve.to_dict()
    restored = CurveConfig.from_dict(data)
    assert restored.type == CurveType.LINEAR
    assert restored.start_temp_c == 25.0
    assert restored.end_output_pct == 95.0


def test_curve_config_roundtrip_flat():
    curve = CurveConfig(id="c3", name="Flat", type=CurveType.FLAT, flat_output_pct=42.0)
    data = curve.to_dict()
    restored = CurveConfig.from_dict(data)
    assert restored.type == CurveType.FLAT
    assert restored.flat_output_pct == 42.0


# ---------------------------------------------------------------------------
# V1 migration
# ---------------------------------------------------------------------------


def test_v1_profile_migration():
    """Old v1 profile with assignments should migrate to v2 with controls + curves."""
    v1_data = {
        "id": "old",
        "name": "Old Profile",
        "version": 1,
        "assignments": [
            {
                "target_id": "openfan:ch00",
                "target_type": "fan",
                "sensor_id": "cpu_temp",
                "curve": {
                    "sensor_id": "cpu_temp",
                    "points": [
                        {"temp_c": 30.0, "output_pct": 20.0},
                        {"temp_c": 70.0, "output_pct": 80.0},
                    ],
                },
                "enabled": True,
            }
        ],
    }
    profile = Profile.from_dict(v1_data)
    assert profile.version == PROFILE_SCHEMA_VERSION
    assert len(profile.controls) == 1
    assert len(profile.curves) == 1
    assert profile.controls[0].curve_id == profile.curves[0].id
    assert len(profile.curves[0].points) == 2
    assert profile.controls[0].members[0].member_id == "openfan:ch00"


def test_v1_profile_migration_group_target():
    """V1 profile with target_type='group' and target_id='all' migrates with empty members."""
    v1_data = {
        "id": "old_group",
        "name": "Old Group Profile",
        "version": 1,
        "assignments": [
            {
                "target_id": "all",
                "target_type": "group",
                "sensor_id": "cpu_temp",
                "curve": {
                    "sensor_id": "cpu_temp",
                    "points": [
                        {"temp_c": 30.0, "output_pct": 25.0},
                        {"temp_c": 80.0, "output_pct": 100.0},
                    ],
                },
                "enabled": True,
            }
        ],
    }
    profile = Profile.from_dict(v1_data)
    assert profile.version == PROFILE_SCHEMA_VERSION
    assert len(profile.controls) == 1
    assert profile.controls[0].name == "Group: all"
    # Group targets have no explicit members (they apply to all fans)
    assert len(profile.controls[0].members) == 0
    assert len(profile.curves) == 1
    assert len(profile.curves[0].points) == 2


# ---------------------------------------------------------------------------
# Profile service
# ---------------------------------------------------------------------------


def test_profile_service_load_creates_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    assert len(svc.profiles) == 3
    assert svc.active_profile is not None


def test_profile_service_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()

    new_p = svc.create_profile("Custom")
    assert svc.get_profile(new_p.id) is not None
    assert len(svc.profiles) == 4

    dup = svc.duplicate_profile(new_p.id, "Custom Copy")
    assert dup is not None
    assert len(svc.profiles) == 5

    assert svc.delete_profile(dup.id) is True
    assert len(svc.profiles) == 4

    assert svc.set_active(new_p.id) is True
    assert svc.active_id == new_p.id


def test_profile_service_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    svc1 = ProfileService()
    svc1.load()
    svc1.create_profile("Persistent")

    svc2 = ProfileService()
    svc2.load()
    names = {p.name for p in svc2.profiles}
    assert "Persistent" in names


def test_profile_persistence_roundtrips_all_fields(tmp_path, monkeypatch):
    """Save a fully-populated profile, reload, verify every field survives."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    svc1 = ProfileService()
    svc1.load()
    p = svc1.create_profile("Full")

    # Add a curve with non-trivial points
    curve = CurveConfig(
        id="c1",
        name="CPU Fan",
        type=CurveType.GRAPH,
        sensor_id="cpu_temp",
        points=[CurvePoint(30, 20), CurvePoint(55, 60), CurvePoint(80, 100)],
    )
    p.curves.append(curve)

    # Add a control with tuning params and a member
    control = LogicalControl(
        id="ctrl1",
        name="Chassis",
        mode=ControlMode.CURVE,
        curve_id="c1",
        manual_output_pct=42.0,
        members=[ControlMember(source="openfan", member_id="openfan:ch00", member_label="Fan 1")],
        step_up_pct=10.0,
        step_down_pct=5.0,
        offset_pct=3.0,
        minimum_pct=15.0,
        start_pct=25.0,
        stop_pct=10.0,
    )
    p.controls.append(control)
    svc1.save_profile(p)

    # Reload from disk in a fresh service
    svc2 = ProfileService()
    svc2.load()
    reloaded = svc2.get_profile(p.id)
    assert reloaded is not None

    # Verify curves roundtripped
    assert len(reloaded.curves) == 1
    rc = reloaded.curves[0]
    assert rc.id == "c1"
    assert rc.name == "CPU Fan"
    assert rc.type == CurveType.GRAPH
    assert rc.sensor_id == "cpu_temp"
    assert len(rc.points) == 3
    assert rc.points[0].temp_c == 30
    assert rc.points[0].output_pct == 20
    assert rc.points[1].temp_c == 55
    assert rc.points[1].output_pct == 60
    assert rc.points[2].temp_c == 80
    assert rc.points[2].output_pct == 100

    # Verify controls roundtripped
    assert len(reloaded.controls) == 1
    rl = reloaded.controls[0]
    assert rl.id == "ctrl1"
    assert rl.name == "Chassis"
    assert rl.mode == ControlMode.CURVE
    assert rl.curve_id == "c1"
    assert rl.manual_output_pct == 42.0
    assert rl.step_up_pct == 10.0
    assert rl.step_down_pct == 5.0
    assert rl.offset_pct == 3.0
    assert rl.minimum_pct == 15.0
    assert rl.start_pct == 25.0
    assert rl.stop_pct == 10.0

    # Verify members roundtripped
    assert len(rl.members) == 1
    rm = rl.members[0]
    assert rm.source == "openfan"
    assert rm.member_id == "openfan:ch00"
    assert rm.member_label == "Fan 1"


# ---------------------------------------------------------------------------
# Error handling — corrupted / minimal / missing fields
# ---------------------------------------------------------------------------


def test_load_corrupted_json_does_not_crash(tmp_path, monkeypatch):
    """Corrupted JSON on disk → logged warning, defaults created instead."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    profiles_dir = tmp_path / "control-ofc" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "broken.json").write_text("{not valid json!!!")

    svc = ProfileService()
    svc.load()  # must not raise

    # Corrupted file skipped, defaults loaded
    assert len(svc.profiles) == 3
    assert svc.active_profile is not None


def test_load_returns_per_profile_errors(tmp_path, monkeypatch):
    """P3-4: corrupted profiles must be reported so the GUI can surface them
    to the user via a Diagnostics warning, not silently swallowed."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    profiles_dir = tmp_path / "control-ofc" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "broken.json").write_text("{not valid json!!!")
    # A valid profile alongside the broken one so `loaded` is True and we
    # avoid the defaults-creation fallback (which would obscure the error).
    valid = {
        "version": 3,
        "id": "ok",
        "name": "ok",
        "description": "",
        "controls": [],
        "curves": [],
    }
    (profiles_dir / "ok.json").write_text(__import__("json").dumps(valid))

    svc = ProfileService()
    errors = svc.load()

    assert len(errors) == 1
    path, msg = errors[0]
    assert "broken.json" in path
    assert msg  # non-empty
    # The valid profile is still loaded.
    assert any(p.id == "ok" for p in svc.profiles)


def test_load_returns_empty_list_on_clean_load(tmp_path, monkeypatch):
    """A clean load with no broken files returns an empty error list."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    svc = ProfileService()
    errors = svc.load()

    assert errors == []


def test_from_dict_empty_dict_uses_defaults():
    """from_dict({}) with no fields → valid profile with defaults."""
    p = Profile.from_dict({})
    assert p.name == ""
    assert p.controls == []
    assert p.curves == []
    assert p.version == PROFILE_SCHEMA_VERSION
    assert len(p.id) > 0  # auto-generated UUID


def test_from_dict_missing_controls_and_curves():
    """from_dict with name but no controls/curves → valid empty profile."""
    p = Profile.from_dict({"name": "Sparse", "version": 3})
    assert p.name == "Sparse"
    assert p.controls == []
    assert p.curves == []


# ---------------------------------------------------------------------------
# V4 — role-aware minimum_pct, fan_zero_rpm, schema migration (DEC-095)
# ---------------------------------------------------------------------------


def test_role_inference_chassis_for_openfan_only():
    from control_ofc.services.profile_service import (
        CONTROL_ROLE_CHASSIS,
        infer_control_role,
    )

    members = [
        ControlMember(source="openfan", member_id="openfan:ch00", member_label="ch00"),
        ControlMember(source="openfan", member_id="openfan:ch01", member_label="ch01"),
    ]
    assert infer_control_role(members) == CONTROL_ROLE_CHASSIS


def test_role_inference_cpu_pump_when_label_hints():
    from control_ofc.services.profile_service import (
        CONTROL_ROLE_CPU_PUMP,
        infer_control_role,
    )

    # CPU header label
    cpu_members = [
        ControlMember(source="hwmon", member_id="hwmon:nct6775:pwm1", member_label="CPU_FAN"),
    ]
    assert infer_control_role(cpu_members) == CONTROL_ROLE_CPU_PUMP

    # Pump header label
    pump_members = [
        ControlMember(source="hwmon", member_id="hwmon:nct6775:pwm2", member_label="AIO_PUMP"),
    ]
    assert infer_control_role(pump_members) == CONTROL_ROLE_CPU_PUMP

    # Mixed — any CPU/pump member promotes the whole control
    mixed = [
        ControlMember(source="openfan", member_id="openfan:ch00", member_label="ch00"),
        ControlMember(source="hwmon", member_id="hwmon:nct6775:pwm1", member_label="CPU_FAN"),
    ]
    assert infer_control_role(mixed) == CONTROL_ROLE_CPU_PUMP


def test_role_inference_gpu_when_only_amd_gpu():
    from control_ofc.services.profile_service import CONTROL_ROLE_GPU, infer_control_role

    members = [
        ControlMember(source="amd_gpu", member_id="amd_gpu:0000:03:00.0", member_label="9070XT"),
    ]
    assert infer_control_role(members) == CONTROL_ROLE_GPU


def test_role_minimum_pct_values():
    from control_ofc.services.profile_service import (
        CONTROL_ROLE_CHASSIS,
        CONTROL_ROLE_CPU_PUMP,
        CONTROL_ROLE_GPU,
        role_minimum_pct,
    )

    # CPU/pump = 30, chassis = 20, GPU = 0 (firmware enforces its own range)
    assert role_minimum_pct(CONTROL_ROLE_CPU_PUMP) == 30.0
    assert role_minimum_pct(CONTROL_ROLE_CHASSIS) == 20.0
    assert role_minimum_pct(CONTROL_ROLE_GPU) == 0.0


def test_apply_role_floor_raises_chassis_to_20():
    from control_ofc.services.profile_service import apply_role_floor

    ctrl = LogicalControl(
        name="Top fans",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        minimum_pct=0.0,
    )
    changed = apply_role_floor(ctrl)
    assert changed is True
    assert ctrl.minimum_pct == 20.0


def test_apply_role_floor_raises_cpu_to_30():
    from control_ofc.services.profile_service import apply_role_floor

    ctrl = LogicalControl(
        name="Pump",
        members=[
            ControlMember(source="hwmon", member_id="hwmon:nct6775:pwm1", member_label="AIO_PUMP")
        ],
        minimum_pct=10.0,
    )
    assert apply_role_floor(ctrl) is True
    assert ctrl.minimum_pct == 30.0


def test_apply_role_floor_preserves_user_value_above_role():
    from control_ofc.services.profile_service import apply_role_floor

    # User explicitly set 40% — must not be lowered to 30 by policy.
    ctrl = LogicalControl(
        name="Pump",
        members=[
            ControlMember(source="hwmon", member_id="hwmon:nct6775:pwm1", member_label="PUMP")
        ],
        minimum_pct=40.0,
    )
    assert apply_role_floor(ctrl) is False
    assert ctrl.minimum_pct == 40.0


def test_v3_to_v4_migration_raises_cpu_pump_floor():
    """A v3 profile with a CPU member at minimum_pct=0 must migrate to 30."""
    v3 = {
        "id": "legacy",
        "name": "Legacy",
        "version": 3,
        "controls": [
            {
                "id": "c1",
                "name": "CPU",
                "mode": "curve",
                "curve_id": "x",
                "members": [
                    {
                        "source": "hwmon",
                        "member_id": "hwmon:nct6775:pwm1",
                        "member_label": "CPU_FAN",
                    }
                ],
                "minimum_pct": 0.0,
            }
        ],
        "curves": [],
    }
    p = Profile.from_dict(v3)
    assert p.version == PROFILE_SCHEMA_VERSION
    assert p.controls[0].minimum_pct == 30.0


def test_v3_to_v4_migration_does_not_lower_explicit_user_value():
    v3 = {
        "id": "legacy",
        "name": "Legacy",
        "version": 3,
        "controls": [
            {
                "id": "c1",
                "name": "CPU",
                "mode": "curve",
                "curve_id": "x",
                "members": [
                    {
                        "source": "hwmon",
                        "member_id": "hwmon:nct6775:pwm1",
                        "member_label": "CPU_FAN",
                    }
                ],
                "minimum_pct": 50.0,
            }
        ],
        "curves": [],
    }
    p = Profile.from_dict(v3)
    assert p.controls[0].minimum_pct == 50.0


def test_v3_to_v4_migration_chassis_only_raises_to_20():
    v3 = {
        "id": "legacy",
        "name": "Legacy",
        "version": 3,
        "controls": [
            {
                "id": "c1",
                "name": "Top",
                "mode": "curve",
                "curve_id": "x",
                "members": [{"source": "openfan", "member_id": "openfan:ch00"}],
                "minimum_pct": 0.0,
            }
        ],
        "curves": [],
    }
    p = Profile.from_dict(v3)
    assert p.controls[0].minimum_pct == 20.0


def test_fan_zero_rpm_field_roundtrip():
    """ControlMember.fan_zero_rpm survives to_dict/from_dict."""
    m = ControlMember(
        source="amd_gpu",
        member_id="amd_gpu:0000:03:00.0",
        member_label="9070XT",
        fan_zero_rpm=True,
    )
    d = m.to_dict()
    assert d["fan_zero_rpm"] is True
    m2 = ControlMember.from_dict(d)
    assert m2.fan_zero_rpm is True


def test_fan_zero_rpm_default_false_when_missing():
    """Loading a profile without the field must default to False."""
    m = ControlMember.from_dict({"source": "amd_gpu", "member_id": "amd_gpu:0000:03:00.0"})
    assert m.fan_zero_rpm is False


def test_load_resaves_when_migrating_from_v3(tmp_path, monkeypatch):
    """Loading a v3 profile from disk must re-save it at the current schema version."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    profiles_dir = tmp_path / "control-ofc" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    legacy = profiles_dir / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "id": "legacy",
                "name": "Legacy",
                "version": 3,
                "controls": [],
                "curves": [],
            }
        )
    )

    svc = ProfileService()
    errors = svc.load()
    assert errors == []

    # File on disk should now report the current schema version.
    rewritten = json.loads(legacy.read_text())
    assert rewritten["version"] == PROFILE_SCHEMA_VERSION
