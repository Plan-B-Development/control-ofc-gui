"""Pure unit tests for the dashboard fan-grouping view-model (DEC-176/177).

No QApplication — fan_grouping is Qt-free. Readings/profiles are constructed
directly (deterministic by construction). Covers 1B-over-1A grouping, every
FanState (including the roster-derived OFFLINE), the DEC-047 zero-RPM guard,
worst-of precedence, honest aggregates, and deterministic ordering.
"""

from __future__ import annotations

from control_ofc.api.models import FanReading, OverrideStatusEntry
from control_ofc.services.fan_grouping import FanState, build_fan_groups
from control_ofc.services.profile_service import (
    ControlMember,
    CurveConfig,
    LogicalControl,
    Profile,
)


def ID(fid: str) -> str:
    return fid


def _fan(fid, source="openfan", rpm=900, pwm=40, age_ms=200, stall=None):
    return FanReading(
        id=fid, source=source, rpm=rpm, last_commanded_pwm=pwm, age_ms=age_ms, stall_detected=stall
    )


def _member_profile(fid, *, source="hwmon", label="CPU Fan", sensor="hwmon:cpu:Tctl", cid="c1"):
    """A profile whose single control owns ``fid``."""
    return Profile(
        name="p",
        curves=[CurveConfig(id="cv", sensor_id=sensor)],
        controls=[
            LogicalControl(
                id=cid,
                curve_id="cv",
                members=[ControlMember(source=source, member_id=fid, member_label=label)],
            )
        ],
    )


def _by_label(groups):
    return {g.label: g for g in groups}


def _call(fans, **kw):
    kw.setdefault("fan_zones", {})
    kw.setdefault("display_name", ID)
    kw.setdefault("active_profile", None)
    kw.setdefault("overrides", [])
    return build_fan_groups(fans, **kw)


# ---------------------------------------------------------------------------
# Grouping: 1B user zones over 1A role/source fallback
# ---------------------------------------------------------------------------


def test_user_zone_grouping():
    groups = _call([_fan("openfan:ch00")], fan_zones={"openfan:ch00": "Front Intake"})
    assert len(groups) == 1
    g = groups[0]
    assert g.label == "Front Intake" and g.is_user_zone is True
    assert g.tiles[0].fan_id == "openfan:ch00"


def test_unassigned_falls_back_to_role_with_profile():
    prof = _member_profile("hwmon:x:pwm1:CPU", source="hwmon", label="CPU Fan")
    groups = _call([_fan("hwmon:x:pwm1:CPU", source="hwmon")], active_profile=prof)
    g = groups[0]
    assert g.label == "CPU / Pump" and g.is_user_zone is False
    assert g.tiles[0].role == "cpu_or_pump"


def test_unassigned_falls_back_to_source_without_profile():
    groups = _call(
        [
            _fan("openfan:ch00", source="openfan"),
            _fan("hwmon:y:pwm1:SYS", source="hwmon"),
            _fan("amd_gpu:0000:2d:00.0", source="amd_gpu", rpm=0),
            _fan("intel_gpu:0000:03:00.0", source="intel_gpu", rpm=0, pwm=None),
        ]
    )
    labels = _by_label(groups)
    assert set(labels) == {"OpenFan", "Motherboard (hwmon)", "AMD GPU", "Intel GPU"}
    assert all(g.is_user_zone is False for g in groups)
    assert labels["OpenFan"].tiles[0].role is None  # no profile => no role


def test_zone_overlays_role_membership():
    # A fan that IS a profile member but is also zoned goes to the zone (1B > 1A).
    prof = _member_profile("hwmon:x:pwm1:CPU", source="hwmon", label="CPU Fan")
    groups = _call(
        [_fan("hwmon:x:pwm1:CPU", source="hwmon")],
        fan_zones={"hwmon:x:pwm1:CPU": "Loop"},
        active_profile=prof,
    )
    assert groups[0].label == "Loop" and groups[0].is_user_zone is True
    # role is still resolved on the tile even though grouping used the zone.
    assert groups[0].tiles[0].role == "cpu_or_pump"


def test_display_name_flows_through():
    groups = _call([_fan("openfan:ch00")], display_name=lambda f: "My Fan" if f else f)
    assert groups[0].tiles[0].display_name == "My Fan"


# ---------------------------------------------------------------------------
# Per-tile state derivation (one per state — failure-path coverage)
# ---------------------------------------------------------------------------


def test_state_normal():
    g = _call([_fan("openfan:ch00", rpm=900, pwm=40)])[0]
    assert g.tiles[0].state == FanState.NORMAL


def test_state_stall():
    g = _call([_fan("openfan:ch00", rpm=0, pwm=70, stall=True)])[0]
    assert g.tiles[0].state == FanState.STALL


def test_state_stale_from_age():
    g = _call([_fan("openfan:ch00", age_ms=5000)])[0]  # >2s => not FRESH
    assert g.tiles[0].state == FanState.STALE


def test_state_low_rpm_no_profile_floor_zero():
    # No profile => floor 0.0; rpm 0 while pwm is just above the floor => LOW_RPM.
    # pwm=1 pins the floor at 0.0: 1 > 0.0 is True, but 1 > 1.0 (a floor-value
    # mutant) is False, which would flip this to NORMAL.
    g = _call([_fan("openfan:ch00", rpm=0, pwm=1)])[0]
    assert g.tiles[0].state == FanState.LOW_RPM


def test_state_override_via_control():
    prof = _member_profile("openfan:ch00", source="openfan", label="Intake", cid="c1")
    g = _call(
        [_fan("openfan:ch00", source="openfan", rpm=900, pwm=80)],
        active_profile=prof,
        overrides=[OverrideStatusEntry(control_id="c1", pwm_percent=80, expires_in_secs=30)],
    )[0]
    assert g.tiles[0].state == FanState.OVERRIDE


def test_state_override_only_for_member_control():
    # An override on a different control must NOT mark this fan.
    prof = _member_profile("openfan:ch00", source="openfan", cid="c1")
    g = _call(
        [_fan("openfan:ch00", source="openfan")],
        active_profile=prof,
        overrides=[OverrideStatusEntry(control_id="OTHER", pwm_percent=80, expires_in_secs=30)],
    )[0]
    assert g.tiles[0].state == FanState.NORMAL


def test_state_offline_via_roster():
    groups = _call(
        [_fan("openfan:ch00")],
        expected_fan_ids={"openfan:ch00", "openfan:ch99"},
    )
    tiles = {t.fan_id: t for g in groups for t in g.tiles}
    assert tiles["openfan:ch99"].state == FanState.OFFLINE
    assert tiles["openfan:ch99"].rpm is None and tiles["openfan:ch99"].age_ms is None


# ---------------------------------------------------------------------------
# DEC-047: zero-RPM idle must NOT be flagged as a fault
# ---------------------------------------------------------------------------


def test_gpu_zero_rpm_idle_is_normal():
    # AMD GPU commanded but idling at 0 RPM => NORMAL, never LOW_RPM (DEC-047).
    g = _call([_fan("amd_gpu:0000:2d:00.0", source="amd_gpu", rpm=0, pwm=30)])[0]
    assert g.tiles[0].state == FanState.NORMAL


def test_zero_pwm_zero_rpm_is_normal():
    # A fan off (pwm 0) reading 0 RPM is correctly off, not LOW_RPM.
    g = _call([_fan("openfan:ch00", rpm=0, pwm=0)])[0]
    assert g.tiles[0].state == FanState.NORMAL


def test_low_rpm_suppressed_below_member_floor():
    # CPU/Pump floor is 30; pwm 20 < floor + rpm 0 => legit idle => NORMAL.
    prof = _member_profile("hwmon:x:pwm1:CPU", source="hwmon", label="CPU Fan")
    g = _call(
        [_fan("hwmon:x:pwm1:CPU", source="hwmon", rpm=0, pwm=20)],
        active_profile=prof,
    )[0]
    assert g.tiles[0].state == FanState.NORMAL


def test_low_rpm_above_member_floor():
    prof = _member_profile("hwmon:x:pwm1:CPU", source="hwmon", label="CPU Fan")
    g = _call(
        [_fan("hwmon:x:pwm1:CPU", source="hwmon", rpm=0, pwm=80)],  # 80 >> 30 floor
        active_profile=prof,
    )[0]
    assert g.tiles[0].state == FanState.LOW_RPM


# ---------------------------------------------------------------------------
# Group worst-of precedence: OFFLINE > STALL > STALE > LOW_RPM > OVERRIDE > NORMAL
# ---------------------------------------------------------------------------


def _zone_group(fans, *, profile=None, overrides=(), expected=None):
    """Force every fan into one zone 'Z' and return that group."""
    zones = {f.id: "Z" for f in fans}
    if expected:
        zones.update({fid: "Z" for fid in expected})
    groups = build_fan_groups(
        fans,
        fan_zones=zones,
        display_name=ID,
        active_profile=profile,
        overrides=list(overrides),
        expected_fan_ids=expected,
    )
    return next(g for g in groups if g.label == "Z")


def test_precedence_offline_beats_stall():
    g = _zone_group(
        [_fan("openfan:ch00", rpm=0, pwm=70, stall=True)],
        expected={"openfan:ch99"},  # missing -> OFFLINE
    )
    assert {t.state for t in g.tiles} == {FanState.STALL, FanState.OFFLINE}
    assert g.state == FanState.OFFLINE


def test_precedence_stall_beats_stale():
    g = _zone_group(
        [
            _fan("openfan:ch00", rpm=0, pwm=70, stall=True),  # STALL
            _fan("openfan:ch01", age_ms=5000),  # STALE
        ]
    )
    assert g.state == FanState.STALL


def test_precedence_stale_beats_low_rpm():
    g = _zone_group(
        [
            _fan("openfan:ch00", age_ms=5000),  # STALE
            _fan("openfan:ch01", rpm=0, pwm=50),  # LOW_RPM
        ]
    )
    assert g.state == FanState.STALE


def test_precedence_low_rpm_beats_override():
    prof = _member_profile("openfan:ch01", source="openfan", cid="c1")
    g = _zone_group(
        [
            _fan("openfan:ch00", rpm=0, pwm=50),  # LOW_RPM (not a member, floor 0)
            _fan("openfan:ch01", source="openfan", rpm=900, pwm=80),  # OVERRIDE (member of c1)
        ],
        profile=prof,
        overrides=[OverrideStatusEntry(control_id="c1", pwm_percent=80, expires_in_secs=30)],
    )
    assert {t.state for t in g.tiles} == {FanState.LOW_RPM, FanState.OVERRIDE}
    assert g.state == FanState.LOW_RPM


def test_precedence_override_beats_normal():
    prof = _member_profile("openfan:ch01", source="openfan", cid="c1")
    g = _zone_group(
        [
            _fan("openfan:ch00", rpm=900, pwm=40),  # NORMAL
            _fan("openfan:ch01", source="openfan", rpm=900, pwm=80),  # OVERRIDE
        ],
        profile=prof,
        overrides=[OverrideStatusEntry(control_id="c1", pwm_percent=80, expires_in_secs=30)],
    )
    assert g.state == FanState.OVERRIDE


# ---------------------------------------------------------------------------
# Aggregates + online/expected
# ---------------------------------------------------------------------------


def test_aggregates_average_ignore_none():
    g = _call(
        [
            _fan("openfan:ch00", rpm=1000, pwm=40),
            _fan("openfan:ch01", rpm=None, pwm=None),
            _fan("openfan:ch02", rpm=2000, pwm=60),
        ]
    )[0]
    assert g.avg_rpm == 1500  # (1000+2000)/2, None skipped
    assert g.avg_pwm_pct == 50  # (40+60)/2


def test_online_counts_only_fresh_present():
    # Three present fans at distinct freshness + one missing-expected. Only the
    # FRESH one is online. The third (INVALID) reading is deliberate: with just a
    # fresh+stale pair the count is 1 under both ``== FRESH`` and ``!= FRESH``, so
    # the operator mutant survives; a second non-fresh reading makes ``!=`` give 2.
    groups = _call(
        [
            _fan("openfan:ch00", age_ms=200),  # FRESH -> online
            _fan("openfan:ch01", age_ms=5000),  # STALE -> not online
            _fan("openfan:ch02", age_ms=15000),  # INVALID -> not online
        ],
        expected_fan_ids={"openfan:ch00", "openfan:ch01", "openfan:ch99"},  # ch99 missing
    )
    g = groups[0]
    assert g.fans_expected == 4  # 3 present + 1 missing-expected
    assert g.fans_online == 1  # only the FRESH one (the ``!= FRESH`` mutant gives 2)


def test_avg_none_when_all_missing():
    g = _call([], expected_fan_ids={"openfan:ch99"})[0]
    assert g.avg_rpm is None and g.avg_pwm_pct is None
    assert g.fans_online == 0 and g.fans_expected == 1


# ---------------------------------------------------------------------------
# Ordering + edges
# ---------------------------------------------------------------------------


def test_ordering_user_zones_first_then_fixed_fallback():
    prof = _member_profile("hwmon:x:pwm1:CPU", source="hwmon", label="CPU Fan", cid="c1")
    groups = _call(
        [
            _fan("hwmon:x:pwm1:CPU", source="hwmon"),  # role: CPU / Pump
            _fan("openfan:ch00", source="openfan"),  # zone: Bravo
            _fan("hwmon:y:pwm1:SYS", source="hwmon"),  # source bucket (not a member)
            _fan("openfan:ch05", source="openfan"),  # zone: Alpha
        ],
        fan_zones={"openfan:ch00": "Bravo", "openfan:ch05": "Alpha"},
        active_profile=prof,
    )
    labels = [g.label for g in groups]
    # user zones first, alphabetical, then fixed fallback order (role before source)
    assert labels == ["Alpha", "Bravo", "CPU / Pump", "Motherboard (hwmon)"]


def test_empty_fans_returns_empty():
    assert _call([]) == []


def test_empty_zones_all_fallback_buckets():
    groups = _call([_fan("openfan:ch00"), _fan("hwmon:y:pwm1:SYS", source="hwmon")])
    assert all(g.is_user_zone is False for g in groups)
    assert {g.label for g in groups} == {"OpenFan", "Motherboard (hwmon)"}


def test_keys_are_unique_and_slugged():
    groups = _call(
        [_fan("openfan:ch00"), _fan("hwmon:y:pwm1:SYS", source="hwmon")],
        fan_zones={"openfan:ch00": "Front Intake"},
    )
    keys = [g.key for g in groups]
    assert len(keys) == len(set(keys))  # unique objectName keys
    assert all(" " not in k and "/" not in k for k in keys)  # slugged


def test_controlled_by_daemon_and_curve_source():
    prof = _member_profile("openfan:ch00", source="openfan", sensor="hwmon:cpu:Tctl", cid="c1")
    tile = _call([_fan("openfan:ch00", source="openfan")], active_profile=prof)[0].tiles[0]
    assert tile.controlled_by_daemon is True
    assert tile.curve_source == "hwmon:cpu:Tctl"


def test_curve_source_none_for_composite_curve():
    # A composite (Mix/Sync) curve has no single sensor_id -> curve_source None.
    prof = Profile(
        name="p",
        curves=[CurveConfig(id="cv", sensor_id="")],  # composite => empty sensor
        controls=[
            LogicalControl(
                id="c1",
                curve_id="cv",
                members=[ControlMember(source="openfan", member_id="openfan:ch00")],
            )
        ],
    )
    tile = _call([_fan("openfan:ch00", source="openfan")], active_profile=prof)[0].tiles[0]
    assert tile.curve_source is None
    assert tile.controlled_by_daemon is True


def test_no_profile_means_no_role_no_control():
    tile = _call([_fan("openfan:ch00")])[0].tiles[0]
    assert tile.role is None
    assert tile.controlled_by_daemon is False
    assert tile.curve_source is None


# ---------------------------------------------------------------------------
# Tile field pass-through (present + OFFLINE) — pins fields a mutant could null
# ---------------------------------------------------------------------------


def test_present_tile_passthrough_fields():
    # A present tile must carry the live reading's identity/telemetry verbatim.
    # source/age_ms are only asserted for OFFLINE tiles elsewhere, so a mutant
    # nulling them on the present path would otherwise slip through.
    g = _call([_fan("openfan:ch00", source="openfan", rpm=1234, pwm=55, age_ms=500)])[0]
    t = g.tiles[0]
    assert t.source == "openfan"
    assert t.age_ms == 500
    assert t.rpm == 1234
    assert t.pwm_pct == 55


def test_offline_tile_identity_fields():
    # An expected-but-absent fan becomes an OFFLINE tile that must still carry its
    # identity: display_name (called with the fan id, not None), source (from the
    # id prefix) and role (resolved from the profile member).
    prof = _member_profile("hwmon:x:pwm1:CPU", source="hwmon", label="CPU Fan")
    groups = _call(
        [_fan("openfan:ch00")],  # an unrelated present fan
        active_profile=prof,
        expected_fan_ids={"openfan:ch00", "hwmon:x:pwm1:CPU"},  # CPU member missing
        display_name=lambda fid: f"Fan {fid}",
    )
    tiles = {t.fan_id: t for g in groups for t in g.tiles}
    off = tiles["hwmon:x:pwm1:CPU"]
    assert off.state == FanState.OFFLINE
    assert off.display_name == "Fan hwmon:x:pwm1:CPU"  # display_name(fid), not (None)
    assert off.source == "hwmon"  # derived from the id prefix
    assert off.role == "cpu_or_pump"  # resolved from the profile member


def test_intel_gpu_zero_rpm_idle_is_normal():
    # DEC-047 zero-RPM guard applies to BOTH GPU sources. An intel_gpu fan
    # commanded but idling at 0 RPM => NORMAL, never LOW_RPM (amd_gpu is covered
    # by test_gpu_zero_rpm_idle_is_normal). pwm is non-None so the source check is
    # the deciding factor, not the pwm-is-None short-circuit.
    g = _call([_fan("intel_gpu:0000:03:00.0", source="intel_gpu", rpm=0, pwm=30)])[0]
    assert g.tiles[0].state == FanState.NORMAL


def test_bucket_fallbacks_for_unknown_role_and_source():
    # Defensive fallbacks in fan->bucket placement: an unrecognised role falls back
    # to "Chassis", an unrecognised source to "Other".
    from control_ofc.services.fan_grouping import _bucket_for

    by_role = _bucket_for("hwmon:x:pwm1", source="hwmon", role="something_new", fan_zones={})
    assert by_role.label == "Chassis" and by_role.is_user_zone is False

    by_source = _bucket_for("weird:0", source="weird", role=None, fan_zones={})
    assert by_source.label == "Other" and by_source.is_user_zone is False
