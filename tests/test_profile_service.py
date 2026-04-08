"""Tests for profile management, curve interpolation, and v1 migration."""

from __future__ import annotations

import pytest

from onlyfans.services.profile_service import (
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
    from onlyfans.services.profile_service import LogicalControl

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
    assert profile.version == 3
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
    assert profile.version == 3
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
    profiles_dir = tmp_path / "onlyfans" / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "broken.json").write_text("{not valid json!!!")

    svc = ProfileService()
    svc.load()  # must not raise

    # Corrupted file skipped, defaults loaded
    assert len(svc.profiles) == 3
    assert svc.active_profile is not None


def test_from_dict_empty_dict_uses_defaults():
    """from_dict({}) with no fields → valid profile with defaults."""
    p = Profile.from_dict({})
    assert p.name == ""
    assert p.controls == []
    assert p.curves == []
    assert p.version == 3
    assert len(p.id) > 0  # auto-generated UUID


def test_from_dict_missing_controls_and_curves():
    """from_dict with name but no controls/curves → valid empty profile."""
    p = Profile.from_dict({"name": "Sparse", "version": 3})
    assert p.name == "Sparse"
    assert p.controls == []
    assert p.curves == []
