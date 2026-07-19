"""Tests for the Dashboard fan-card view-model (DEC-222).

No QApplication — ``fan_cards_view`` is pure. Readings and profiles are built by
hand so each case pins one rule of the control-keyed grouping.
"""

from __future__ import annotations

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
    UNASSIGNED_ID,
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


class TestUnassignedBucket:
    def test_no_profile_puts_every_controllable_fan_in_one_card(self):
        """The state a fresh install is in. Without this the Dashboard would show
        nothing at all — there are no controls to build cards from."""
        cards = build_fan_card_vms(
            [_fan("openfan:ch00"), _fan("openfan:ch01")], active_profile=None, overrides=[]
        )
        assert len(cards) == 1
        assert cards[0].control_id == UNASSIGNED_ID
        assert cards[0].is_unassigned is True
        assert cards[0].fan_count == 2

    def test_fans_claimed_by_a_control_are_not_also_unassigned(self):
        control = _control(member_ids=("f1",))
        cards = build_fan_card_vms(
            [_fan("f1"), _fan("openfan:ch09")], active_profile=_profile(control), overrides=[]
        )
        assert [c.control_id for c in cards] == ["c1", UNASSIGNED_ID]
        assert cards[1].member_fan_ids == ("openfan:ch09",)

    def test_no_unassigned_card_when_every_fan_is_claimed(self):
        control = _control(member_ids=("f1",))
        cards = build_fan_card_vms([_fan("f1")], active_profile=_profile(control), overrides=[])
        assert [c.control_id for c in cards] == ["c1"]

    def test_unassigned_card_has_no_curve_or_temp(self):
        card = build_fan_card_vms([_fan()], active_profile=None, overrides=[])[0]
        assert card.curve is None
        assert card.temp_c is None
        assert card.overridden is False


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

    def test_read_only_fans_stay_out_of_the_unassigned_bucket(self):
        fans = [_fan("openfan:ch00"), _fan("nvidia_gpu:a", source="nvidia_gpu", pwm=None)]
        cards = build_fan_card_vms(fans, active_profile=None, overrides=[])
        unassigned = next(c for c in cards if c.is_unassigned)
        assert unassigned.member_fan_ids == ("openfan:ch00",)
        assert unassigned.fan_count == 1

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
    def test_ordering_is_deterministic_controls_then_unassigned_then_readonly(self):
        control = _control(member_ids=("f1",))
        fans = [
            _fan("nvidia_gpu:z", source="nvidia_gpu", pwm=None),
            _fan("openfan:ch09"),
            _fan("f1"),
        ]
        cards = build_fan_card_vms(fans, active_profile=_profile(control), overrides=[])
        assert [c.control_id for c in cards] == [
            "c1",
            UNASSIGNED_ID,
            f"{READ_ONLY_PREFIX}nvidia_gpu:z",
        ]

    def test_repeated_calls_are_stable(self):
        control = _control(member_ids=("f1",))
        args = ([_fan("f1")],)
        kwargs = {"active_profile": _profile(control), "overrides": []}
        assert build_fan_card_vms(*args, **kwargs) == build_fan_card_vms(*args, **kwargs)

    def test_empty_input_yields_no_cards(self):
        assert build_fan_card_vms([], active_profile=None, overrides=[]) == []
