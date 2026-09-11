"""Tests for the Dashboard fan-card view-model (DEC-222).

No QApplication — ``fan_cards_view`` is pure. Readings and profiles are built by
hand so each case pins one rule of the control-keyed grouping.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    FanReading,
    Freshness,
    HwmonHeader,
    OverrideStatusEntry,
)
from control_ofc.services.fan_cards_view import (
    READ_ONLY_PREFIX,
    FanState,
    build_fan_card_vms,
    is_fan_controllable,
)
from control_ofc.services.profile_service import (
    ControlMember,
    CurveConfig,
    CurvePoint,
    LogicalControl,
    Profile,
)


def _fan(fan_id="openfan:ch00", source="openfan", rpm=1200, pwm=45, age_ms=100, **kw):
    return FanReading(
        id=fan_id, source=source, rpm=rpm, last_commanded_pwm=pwm, age_ms=age_ms, **kw
    )


def _profile(*controls, curves=()):
    return Profile(id="p1", name="P", controls=list(controls), curves=list(curves))


def _control(control_id="c1", name="CPU Fans", member_ids=("hwmon:x:pwm1",), curve_id=""):
    return LogicalControl(
        id=control_id,
        name=name,
        curve_id=curve_id,
        members=[ControlMember(source="hwmon", member_id=m, member_label=m) for m in member_ids],
    )


class TestControlGrouping:
    def test_one_card_per_control_not_per_fan(self):
        """The daemon's override API is control-keyed, so the card is too — three
        fans in one control are ONE card that names its blast radius."""
        control = _control(member_ids=("f1", "f2", "f3"))
        fans = [_fan("f1", rpm=1000, pwm=40), _fan("f2", rpm=1400, pwm=50), _fan("f3", rpm=1200)]
        cards = build_fan_card_vms(fans, active_profile=_profile(control), overrides=[])
        assert len(cards) == 1
        assert cards[0].control_id == "c1"
        assert cards[0].fan_count == 3
        assert cards[0].member_fan_ids == ("f1", "f2", "f3")

    def test_aggregates_are_means_over_reporting_members(self):
        control = _control(member_ids=("f1", "f2"))
        fans = [_fan("f1", rpm=1000, pwm=40), _fan("f2", rpm=1400, pwm=50)]
        card = build_fan_card_vms(fans, active_profile=_profile(control), overrides=[])[0]
        assert card.rpm == 1200
        assert card.pwm_pct == 45

    def test_member_without_a_reading_is_offline_not_hidden(self):
        """A profile member the daemon isn't reporting must degrade the card, not
        vanish from it — the truthfulness rule."""
        control = _control(member_ids=("f1", "missing"))
        card = build_fan_card_vms([_fan("f1")], active_profile=_profile(control), overrides=[])[0]
        assert card.state is FanState.OFFLINE
        assert card.fan_count == 2

    def test_control_label_falls_back_to_id_when_unnamed(self):
        card = build_fan_card_vms(
            [_fan("f1")],
            active_profile=_profile(_control(name="", member_ids=("f1",))),
            overrides=[],
        )[0]
        assert card.label == "c1"

    def test_curve_and_temp_come_from_the_controls_own_curve(self):
        curve = CurveConfig(id="cv", name="C", sensor_id="cpu0", points=[CurvePoint(30, 20)])
        control = _control(member_ids=("f1",), curve_id="cv")
        card = build_fan_card_vms(
            [_fan("f1")],
            active_profile=_profile(control, curves=[curve]),
            overrides=[],
            sensor_values={"cpu0": 61.4},
        )[0]
        assert card.curve is curve
        assert card.temp_c == 61.4

    def test_composite_curve_does_not_borrow_a_stale_sensor(self):
        """A Mix/Sync curve keeps whatever sensor_id it last had — the curve editor
        writes the field unconditionally — so trusting sensor_id alone would show an
        unrelated sensor's reading as if it drove this control."""
        from control_ofc.services.profile_service import CurveType

        for curve_type in (CurveType.MIX, CurveType.SYNC):
            curve = CurveConfig(id="cv", name="Composite", type=curve_type, sensor_id="cpu0")
            control = _control(member_ids=("f1",), curve_id="cv")
            card = build_fan_card_vms(
                [_fan("f1")],
                active_profile=_profile(control, curves=[curve]),
                overrides=[],
                sensor_values={"cpu0": 61.4},
            )[0]
            assert card.temp_c is None, curve_type

    def test_composite_curve_without_a_sensor_has_no_temp(self):
        """A Mix/Sync curve has no single sensor, so borrowing one would be a lie."""
        curve = CurveConfig(id="cv", name="Mix", sensor_id="")
        control = _control(member_ids=("f1",), curve_id="cv")
        card = build_fan_card_vms(
            [_fan("f1")],
            active_profile=_profile(control, curves=[curve]),
            overrides=[],
            sensor_values={"cpu0": 61.4},
        )[0]
        assert card.temp_c is None


class TestOverrideAndState:
    def test_override_on_the_control_marks_the_card(self):
        control = _control(member_ids=("f1",))
        card = build_fan_card_vms(
            [_fan("f1")],
            active_profile=_profile(control),
            overrides=[OverrideStatusEntry(control_id="c1", pwm_percent=70)],
        )[0]
        assert card.overridden is True
        assert card.state is FanState.OVERRIDE

    def test_an_override_on_another_control_does_not_leak(self):
        control = _control(member_ids=("f1",))
        card = build_fan_card_vms(
            [_fan("f1")],
            active_profile=_profile(control),
            overrides=[OverrideStatusEntry(control_id="other", pwm_percent=70)],
        )[0]
        assert card.overridden is False

    def test_a_fault_outranks_the_informational_override(self):
        """Worst-of precedence: STALL must win over OVERRIDE, or a stalled fan
        would read as merely 'manually pinned'."""
        control = _control(member_ids=("f1",))
        fan = _fan("f1", rpm=0)
        fan.stall_detected = True
        card = build_fan_card_vms(
            [fan],
            active_profile=_profile(control),
            overrides=[OverrideStatusEntry(control_id="c1", pwm_percent=70)],
        )[0]
        assert card.state is FanState.STALL

    def test_stale_reading_marks_the_card(self):
        control = _control(member_ids=("f1",))
        fan = _fan("f1", age_ms=60_000)
        assert fan.freshness is not Freshness.FRESH
        card = build_fan_card_vms([fan], active_profile=_profile(control), overrides=[])[0]
        assert card.state is FanState.STALE


class TestLowRpmDerivation:
    """LOW_RPM and its two guards. This logic moved verbatim out of the retired
    fan_grouping module; these port the cover that moved with it."""

    def test_zero_rpm_above_the_floor_is_low_rpm(self):
        """A fan commanded above its floor but reading 0 RPM is the whole point
        of the heuristic — it is spinning down or unplugged."""
        control = _control(member_ids=("f1",))
        card = build_fan_card_vms(
            [_fan("f1", rpm=0, pwm=50)], active_profile=_profile(control), overrides=[]
        )[0]
        assert card.state is FanState.LOW_RPM

    def test_zero_rpm_at_or_below_the_floor_is_not_low_rpm(self):
        """Below the member floor the daemon is not really asking for movement,
        so 0 RPM is expected rather than suspicious."""
        control = _control(member_ids=("f1",))
        control.minimum_pct = 60.0  # floor above the commanded value
        card = build_fan_card_vms(
            [_fan("f1", rpm=0, pwm=50)], active_profile=_profile(control), overrides=[]
        )[0]
        assert card.state is not FanState.LOW_RPM

    def test_no_commanded_pwm_is_not_low_rpm(self):
        """Without a commanded value there is nothing to contradict."""
        control = _control(member_ids=("f1",))
        card = build_fan_card_vms(
            [_fan("f1", rpm=0, pwm=None)], active_profile=_profile(control), overrides=[]
        )[0]
        assert card.state is not FanState.LOW_RPM

    @pytest.mark.parametrize("source", ["amd_gpu", "intel_gpu", "nvidia_gpu"])
    def test_gpu_zero_rpm_idle_is_normal_not_low_rpm(self, source):
        """Zero-RPM idle is normal for a GPU (DEC-047) — flagging it would cry
        wolf on every cool GPU in the machine."""
        control = _control(member_ids=("g1",))
        card = build_fan_card_vms(
            [_fan("g1", source=source, rpm=0, pwm=50)],
            active_profile=_profile(control),
            overrides=[],
        )[0]
        assert card.state is FanState.NORMAL

    def test_low_rpm_outranks_override_but_yields_to_stale(self):
        """Middle of the precedence chain: LOW_RPM > OVERRIDE, STALE > LOW_RPM."""
        control = _control(member_ids=("f1",))
        overridden = build_fan_card_vms(
            [_fan("f1", rpm=0, pwm=50)],
            active_profile=_profile(control),
            overrides=[OverrideStatusEntry(control_id="c1", pwm_percent=50)],
        )[0]
        assert overridden.state is FanState.LOW_RPM

        stale = build_fan_card_vms(
            [_fan("f1", rpm=0, pwm=50, age_ms=60_000)],
            active_profile=_profile(control),
            overrides=[],
        )[0]
        assert stale.state is FanState.STALE


class TestStatePrecedence:
    """The full worst-of chain: OFFLINE > STALL > STALE > LOW_RPM > OVERRIDE > NORMAL.
    A transposition in _STATE_RANK must fail a test."""

    def test_offline_outranks_stall(self):
        control = _control(member_ids=("f1", "missing"))
        stalling = _fan("f1", rpm=0)
        stalling.stall_detected = True
        card = build_fan_card_vms([stalling], active_profile=_profile(control), overrides=[])[0]
        assert card.state is FanState.OFFLINE

    def test_stall_outranks_stale(self):
        control = _control(member_ids=("f1", "f2"))
        stalling = _fan("f1", rpm=0)
        stalling.stall_detected = True
        card = build_fan_card_vms(
            [stalling, _fan("f2", age_ms=60_000)],
            active_profile=_profile(control),
            overrides=[],
        )[0]
        assert card.state is FanState.STALL

    def test_healthy_control_is_normal(self):
        control = _control(member_ids=("f1", "f2"))
        card = build_fan_card_vms(
            [_fan("f1"), _fan("f2")], active_profile=_profile(control), overrides=[]
        )[0]
        assert card.state is FanState.NORMAL


class TestMemberlessControl:
    """A control with no members yet — what "New Fan Role" produces before the
    user assigns a fan. Until DEC-356 it rendered a card carrying a "No fans"
    chip; the band now shows only controls that are driving fans, so it has none.
    It is still not *faulted* — it simply has nothing to report here, and the
    Controls page is where an unconfigured role is visible and finished."""

    def test_empty_control_gets_no_card(self):
        cards = build_fan_card_vms(
            [], active_profile=_profile(_control(member_ids=())), overrides=[]
        )
        assert cards == []

    def test_an_empty_control_does_not_suppress_its_siblings(self):
        empty = _control(control_id="new", name="New Role", member_ids=())
        live = _control(control_id="c1", name="Chassis", member_ids=("f1",))
        cards = build_fan_card_vms([_fan("f1")], active_profile=_profile(empty, live), overrides=[])
        assert [c.control_id for c in cards] == ["c1"]


class TestCardKeyUniqueness:
    """objectNames and the page's reconcile dict key on card_key. A malformed or
    shared profile can repeat a control id, and keying on that would make one
    card silently overwrite another."""

    def test_duplicate_control_ids_get_distinct_card_keys(self):
        a = _control(control_id="dup", name="A", member_ids=("f1",))
        b = _control(control_id="dup", name="B", member_ids=("f2",))
        cards = build_fan_card_vms(
            [_fan("f1"), _fan("f2")], active_profile=_profile(a, b), overrides=[]
        )
        assert len(cards) == 2
        assert cards[0].card_key != cards[1].card_key
        # control_id stays truthful so the Edit deep-link still names the control.
        assert [c.control_id for c in cards] == ["dup", "dup"]

    def test_an_empty_control_id_is_carried_through_untouched(self):
        """A hand-edited profile can name a control "". Nothing upstream rejects
        it, and since DEC-356 removed the pseudo-card that also keyed on "" there
        is nothing left for it to collide with — but it must still render, and
        ``control_id`` must stay the truthful value the Edit deep-link needs."""
        empty_id = _control(control_id="", name="Oddly Named", member_ids=("f1",))
        cards = build_fan_card_vms([_fan("f1")], active_profile=_profile(empty_id), overrides=[])
        assert [c.control_id for c in cards] == [""]
        assert cards[0].card_key == ""

    def test_card_key_equals_control_id_in_the_normal_case(self):
        control = _control(member_ids=("f1",))
        card = build_fan_card_vms([_fan("f1")], active_profile=_profile(control), overrides=[])[0]
        assert card.card_key == card.control_id == "c1"


class TestUnassignedFansGetNoCard:
    """DEC-356 (superseding the DEC-222 clause): the band carries controls that
    are actually driving fans. An unassigned fan is the Controls page's business —
    it counts them on the "Unassigned Fans (N)" button (DEC-233) and is the only
    page that can act on one."""

    def test_no_profile_yields_no_cards_at_all(self):
        """The state a fresh install is in. Was one pooled "Unassigned" card."""
        cards = build_fan_card_vms(
            [_fan("openfan:ch00"), _fan("openfan:ch01")], active_profile=None, overrides=[]
        )
        assert cards == []

    def test_a_controllable_fan_no_control_claims_gets_no_card(self):
        control = _control(member_ids=("f1",))
        cards = build_fan_card_vms(
            [_fan("f1"), _fan("openfan:ch09")], active_profile=_profile(control), overrides=[]
        )
        assert [c.control_id for c in cards] == ["c1"]
        # The unclaimed fan is absent from every card, not merely uncounted.
        assert all("openfan:ch09" not in c.member_fan_ids for c in cards)

    def test_a_claimed_fan_still_gets_its_control_card(self):
        control = _control(member_ids=("f1",))
        cards = build_fan_card_vms([_fan("f1")], active_profile=_profile(control), overrides=[])
        assert [c.control_id for c in cards] == ["c1"]


class TestOnlyLiveControlsGetCards:
    """DEC-356: a card requires at least one member in the poll. The user chose
    this over reporting OFFLINE for a fully dark control, having been shown that
    cost; a *partly* live control still reports its missing members."""

    def test_a_control_with_no_members_assigned_gets_no_card(self):
        cards = build_fan_card_vms(
            [_fan("f1")], active_profile=_profile(_control(member_ids=())), overrides=[]
        )
        assert cards == []

    def test_a_control_whose_members_are_all_absent_gets_no_card(self):
        control = _control(member_ids=("f1", "f2"))
        cards = build_fan_card_vms(
            [_fan("openfan:ch09")], active_profile=_profile(control), overrides=[]
        )
        assert cards == []

    def test_a_partly_live_control_keeps_its_card_and_reports_offline(self):
        """The opposite branch — without it a predicate stuck at "never render"
        would pass the two tests above."""
        control = _control(member_ids=("f1", "f2"))
        cards = build_fan_card_vms([_fan("f1")], active_profile=_profile(control), overrides=[])
        assert [c.control_id for c in cards] == ["c1"]
        assert cards[0].state is FanState.OFFLINE
        assert cards[0].fan_count == 2  # still names its full blast radius


class TestControllability:
    def test_openfan_is_controllable(self):
        assert is_fan_controllable(_fan(source="openfan"), [], None) is True

    def test_hwmon_without_a_header_is_not_controllable(self):
        """No header means no evidence of a write path — we never claim one."""
        assert is_fan_controllable(_fan("hwmon:x:pwm1", source="hwmon"), [], None) is False

    def test_hwmon_read_only_header_is_not_controllable(self):
        header = HwmonHeader(id="hwmon:x:pwm1", is_writable=False)
        assert is_fan_controllable(_fan("hwmon:x:pwm1", source="hwmon"), [header], None) is False

    def test_hwmon_writable_header_is_controllable(self):
        header = HwmonHeader(id="hwmon:x:pwm1", is_writable=True)
        assert is_fan_controllable(_fan("hwmon:x:pwm1", source="hwmon"), [header], None) is True

    def test_amd_gpu_with_pmfw_is_controllable(self):
        caps = Capabilities(amd_gpu=AmdGpuCapability(present=True, fan_control_method="pmfw_curve"))
        assert is_fan_controllable(_fan("amd_gpu:x", source="amd_gpu"), [], caps) is True

    def test_read_only_amd_gpu_is_not_controllable(self):
        caps = Capabilities(amd_gpu=AmdGpuCapability(present=True, fan_control_method="read_only"))
        assert is_fan_controllable(_fan("amd_gpu:x", source="amd_gpu"), [], caps) is False


class TestReadOnlyCards:
    def test_read_only_fan_gets_its_own_card(self):
        """One card each, not a shared bucket — pooling would average away the
        very reading the card exists to show (DEC-204)."""
        fans = [
            _fan("nvidia_gpu:a", source="nvidia_gpu", pwm=None, duty_pct=55),
            _fan("nvidia_gpu:b", source="nvidia_gpu", pwm=None, duty_pct=70),
        ]
        cards = build_fan_card_vms(fans, active_profile=None, overrides=[])
        assert [c.control_id for c in cards] == [
            f"{READ_ONLY_PREFIX}nvidia_gpu:a",
            f"{READ_ONLY_PREFIX}nvidia_gpu:b",
        ]
        assert [c.duty_pct for c in cards] == [55, 70]
        assert all(c.is_read_only for c in cards)
        assert all(c.fan_count == 1 for c in cards)

    def test_read_only_is_the_one_unclaimed_fan_that_still_gets_a_card(self):
        """DEC-356 dropped the card for an unassigned *controllable* fan. A
        read-only fan is kept, because no page can ever assign it (DEC-102) and
        its firmware duty would otherwise have nowhere to show."""
        fans = [_fan("openfan:ch00"), _fan("nvidia_gpu:a", source="nvidia_gpu", pwm=None)]
        cards = build_fan_card_vms(fans, active_profile=None, overrides=[])
        assert [c.control_id for c in cards] == [f"{READ_ONLY_PREFIX}nvidia_gpu:a"]
        assert cards[0].member_fan_ids == ("nvidia_gpu:a",)

    def test_read_only_fan_inside_a_control_renders_there_instead(self):
        """A hand-edited profile can place one in a control; the control genuinely
        exists, so it is not also given a standalone card."""
        control = _control(member_ids=("nvidia_gpu:a",))
        cards = build_fan_card_vms(
            [_fan("nvidia_gpu:a", source="nvidia_gpu", pwm=None, duty_pct=55)],
            active_profile=_profile(control),
            overrides=[],
        )
        assert [c.control_id for c in cards] == ["c1"]
        assert cards[0].is_read_only is False

    def test_display_name_labels_the_read_only_card(self):
        cards = build_fan_card_vms(
            [_fan("nvidia_gpu:a", source="nvidia_gpu", pwm=None)],
            active_profile=None,
            overrides=[],
            display_name=lambda fid: "RTX 4080 Fan",
        )
        assert cards[0].label == "RTX 4080 Fan"

    def test_label_falls_back_to_the_fan_id_without_a_resolver(self):
        cards = build_fan_card_vms(
            [_fan("nvidia_gpu:a", source="nvidia_gpu", pwm=None)],
            active_profile=None,
            overrides=[],
        )
        assert cards[0].label == "nvidia_gpu:a"


class TestPurity:
    def test_ordering_is_deterministic_controls_then_readonly(self):
        control = _control(member_ids=("f1",))
        fans = [
            _fan("nvidia_gpu:z", source="nvidia_gpu", pwm=None),
            _fan("openfan:ch09"),  # unassigned and controllable → no card (DEC-356)
            _fan("f1"),
        ]
        cards = build_fan_card_vms(fans, active_profile=_profile(control), overrides=[])
        assert [c.control_id for c in cards] == ["c1", f"{READ_ONLY_PREFIX}nvidia_gpu:z"]

    def test_repeated_calls_are_stable(self):
        control = _control(member_ids=("f1",))
        args = ([_fan("f1")],)
        kwargs = {"active_profile": _profile(control), "overrides": []}
        assert build_fan_card_vms(*args, **kwargs) == build_fan_card_vms(*args, **kwargs)

    def test_empty_input_yields_no_cards(self):
        assert build_fan_card_vms([], active_profile=None, overrides=[]) == []
