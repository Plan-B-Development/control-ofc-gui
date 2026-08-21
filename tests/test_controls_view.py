"""Pure Controls view helpers (DEC-214; DEC-219 Phase 7.3).

The Qt-free derivations behind the Controls page: unassigned fans + per-member
RPM map (DEC-214), plus the Phase 7.3 extractions — curve floor, GPU-divergence
annotation, renew cadence, override-rejection feedback, sensor-combo label, and
card-size persistence. All headless (no widget construction).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from control_ofc.api.models import SensorReading
from control_ofc.services.controls_view import (
    curve_min_output_floor,
    divergent_gpu_output,
    member_rpm_map,
    override_rejection_feedback,
    parse_stored_card_size,
    prune_card_sizes,
    renew_interval_ms,
    sensor_combo_label,
    unassigned_fan_ids,
)
from control_ofc.services.profile_service import ControlMember, LogicalControl


@dataclass
class _Fan:
    id: str
    rpm: int | None = None


def test_unassigned_fan_ids_excludes_assigned():
    fans = [_Fan("openfan:0"), _Fan("openfan:1"), _Fan("hwmon:pwm1")]
    controls = [
        LogicalControl(
            id="r1",
            name="CPU",
            members=[ControlMember(source="openfan", member_id="openfan:0")],
        )
    ]
    assert unassigned_fan_ids(fans, controls) == ["openfan:1", "hwmon:pwm1"]


def test_unassigned_fan_ids_all_when_no_controls():
    fans = [_Fan("a"), _Fan("b")]
    assert unassigned_fan_ids(fans, []) == ["a", "b"]


def test_member_rpm_map_resolves_and_blanks_unknown():
    control = LogicalControl(
        id="r1",
        name="Intake",
        members=[
            ControlMember(source="openfan", member_id="openfan:0"),
            ControlMember(source="openfan", member_id="openfan:1"),  # no reading
        ],
    )
    fan_map = {"openfan:0": _Fan("openfan:0", rpm=1151)}
    result = member_rpm_map(control, fan_map)
    assert result == {"openfan:0": 1151, "openfan:1": None}


def test_member_rpm_map_none_when_reading_has_no_rpm():
    control = LogicalControl(
        id="r1",
        name="X",
        members=[ControlMember(source="hwmon", member_id="hwmon:pwm1")],
    )
    fan_map = {"hwmon:pwm1": _Fan("hwmon:pwm1", rpm=None)}
    assert member_rpm_map(control, fan_map) == {"hwmon:pwm1": None}


# ─── curve_min_output_floor (DEC-219 Phase 7.3) ──────────────────────────


class TestCurveMinOutputFloor:
    def test_derives_role_floor_from_members(self):
        cpu = ControlMember(source="hwmon", member_id="hwmon:nct6799:pwm1", member_label="CPU Fan")
        ctrl = LogicalControl(id="r1", name="CPU", curve_id="c1", members=[cpu])
        profile = SimpleNamespace(controls=[ctrl])
        # A CPU/pump member → 30% role floor even before the control is migrated.
        assert curve_min_output_floor(profile, "c1") == 30.0

    def test_zero_when_no_control_references_curve(self):
        ctrl = LogicalControl(id="r1", name="CPU", curve_id="c1", members=[])
        profile = SimpleNamespace(controls=[ctrl])
        assert curve_min_output_floor(profile, "other") == 0.0

    def test_explicit_minimum_wins_when_higher(self):
        chassis = ControlMember(source="openfan", member_id="openfan:0")  # 20% role
        ctrl = LogicalControl(
            id="r1", name="Case", curve_id="c1", members=[chassis], minimum_pct=55.0
        )
        profile = SimpleNamespace(controls=[ctrl])
        assert curve_min_output_floor(profile, "c1") == 55.0


# ─── divergent_gpu_output (DEC-119) ──────────────────────────────────────


class TestDivergentGpuOutput:
    def _mixed(self):
        gpu = ControlMember(source="amd_gpu", member_id="amd_gpu:fan")
        chassis = ControlMember(source="openfan", member_id="openfan:0")
        return LogicalControl(id="r1", name="Mixed", members=[gpu, chassis])

    def test_returns_gpu_output_when_divergent(self):
        assert divergent_gpu_output(self._mixed(), 60.0, {"amd_gpu:fan": 30.0}) == 30.0

    def test_none_within_threshold(self):
        assert divergent_gpu_output(self._mixed(), 30.5, {"amd_gpu:fan": 30.0}) is None

    def test_none_for_gpu_only_control(self):
        gpu = ControlMember(source="amd_gpu", member_id="amd_gpu:fan")
        gpu_only = LogicalControl(id="r2", name="GPU", members=[gpu])
        assert divergent_gpu_output(gpu_only, 60.0, {"amd_gpu:fan": 30.0}) is None

    def test_none_when_no_member_outputs(self):
        assert divergent_gpu_output(self._mixed(), 60.0, {}) is None


# ─── renew_interval_ms (F-2) ─────────────────────────────────────────────


class TestRenewIntervalMs:
    def test_none_when_nothing_held(self):
        assert renew_interval_ms({}, 5000) is None

    def test_seconds_to_ms(self):
        assert renew_interval_ms({"a": 5}, 5000) == 5000

    def test_takes_the_minimum_cadence(self):
        assert renew_interval_ms({"a": 5, "b": 3}, 5000) == 3000

    def test_missing_renew_secs_uses_fallback(self):
        assert renew_interval_ms({"a": None}, 9000) == 9000

    def test_floored_at_1000ms(self):
        assert renew_interval_ms({"a": 0.5}, 5000) == 1000


# ─── override_rejection_feedback (DEC-163) ───────────────────────────────


class TestOverrideRejectionFeedback:
    def test_thermal_abort_is_critical(self):
        msg, cls = override_rejection_feedback("thermal_abort")
        assert "thermal emergency" in msg
        assert cls == "CriticalChip"

    def test_stale_fencing_token_is_warning(self):
        msg, cls = override_rejection_feedback("stale_fencing_token")
        assert "superseded" in msg
        assert cls == "WarningChip"

    def test_benign_codes_stay_silent(self):
        assert override_rejection_feedback("override_expired") is None
        assert override_rejection_feedback("not_found") is None


# ─── sensor_combo_label (DEC-157) ────────────────────────────────────────


class TestSensorComboLabel:
    def test_cpu_sensor_is_starred(self):
        s = SensorReading(
            id="cpu0", kind="cpu_temp", label="Tctl", value_c=42.5, chip_name="k10temp"
        )
        label = sensor_combo_label(s, {})
        assert label.startswith("★ ")
        assert "42.5" in label

    def test_generic_sensor_not_starred(self):
        s = SensorReading(
            id="mb0", kind="MbTemp", label="SYSTIN", value_c=30.0, chip_name="nct6799"
        )
        assert not sensor_combo_label(s, {}).startswith("★")

    def test_missing_value_hides_temperature(self):
        s = SensorReading(id="x", kind="MbTemp", label="x", value_c=None, chip_name="nct6799")
        assert "°C" not in sensor_combo_label(s, {})


# ─── card-size persistence ───────────────────────────────────────────────


class TestParseStoredCardSize:
    def test_valid_list_and_tuple(self):
        assert parse_stored_card_size([320, 200]) == (320, 200)
        assert parse_stored_card_size((320, 200)) == (320, 200)

    def test_int_coercible_strings(self):
        assert parse_stored_card_size(["320", "200"]) == (320, 200)

    def test_malformed_is_none(self):
        assert parse_stored_card_size([320]) is None
        assert parse_stored_card_size("nope") is None
        assert parse_stored_card_size([1, "x"]) is None
        assert parse_stored_card_size(None) is None


class TestPruneCardSizes:
    def test_drops_unknown_keeps_known(self):
        sizes = {"c1": [1, 2], "gone": [3, 4]}
        prune_card_sizes(sizes, {"c1"})
        assert sizes == {"c1": [1, 2]}

    def test_empty_known_drops_all(self):
        sizes = {"a": [1, 1]}
        prune_card_sizes(sizes, set())
        assert sizes == {}


def test_skip_reason_map_covers_the_wire_vocabulary():
    """Every `skipped_controls[].reason` the daemon can send must have wording.

    DEC-257's mechanism, applied to a second wire field. A presentation map keyed
    off a wire vocabulary drifted silently once already — `thermal_state` carried
    three values the daemon never sends and was missing the two that mean it is
    actively forcing fans, so a live thermal recovery rendered as a neutral grey
    pill. This pins the 273-i map the same way, before it can happen twice.

    The map is also allowed to be a strict superset of nothing: an extra key is
    just as wrong, because it is wording for a state that cannot occur and will
    never be seen or corrected.
    """
    from control_ofc.api.models import SKIP_REASON_VALUES
    from control_ofc.services.controls_view import _SKIP_REASONS

    wire = set(SKIP_REASON_VALUES)
    keys = set(_SKIP_REASONS)
    assert not wire - keys, (
        f"controls_view._SKIP_REASONS is missing daemon reasons {sorted(wire - keys)} — "
        "a control skipped for one would render with the vague fallback tooltip"
    )
    assert not keys - wire, (
        f"controls_view._SKIP_REASONS carries reasons the daemon never sends "
        f"{sorted(keys - wire)} — dead wording nobody will ever see or correct"
    )


def test_skipped_control_feedback_renders_an_unknown_reason():
    """A newer daemon may add a reason token this build has never heard of.
    Returning nothing would restore the exact silence 273-i exists to end, so the
    fallback must still name the situation."""
    from control_ofc.services.controls_view import skipped_control_feedback

    chip, tooltip = skipped_control_feedback("a_reason_from_the_future")
    assert chip == "Not controlled"
    assert "not commanding" in tooltip


def test_skipped_control_feedback_names_the_cause_when_known():
    from control_ofc.services.controls_view import skipped_control_feedback

    _, tooltip = skipped_control_feedback("sync_unresolvable")
    assert "mirrors" in tooltip
    assert "hold their last speed" in tooltip


def test_a_malformed_reason_does_not_crash_the_poll_path():
    """`_filter_fields` does no type coercion, so a non-conforming daemon can put
    any JSON value in `reason` — and it then keys a dict. `{"reason": []}` raised
    `TypeError: unhashable type` on the 1 Hz poll path: a crash, not a degraded
    render. `unavailable_sensors` never keys a dict on a wire value, which is why
    this asymmetry is ours to handle.
    """
    from control_ofc.services.controls_view import skipped_control_feedback

    for bad in ([], {}, None, 42, ["mix_unresolvable"]):
        chip, tooltip = skipped_control_feedback(bad)
        assert chip == "Not controlled"
        assert tooltip, f"a {type(bad).__name__} reason must still render something"
