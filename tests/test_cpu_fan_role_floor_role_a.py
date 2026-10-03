"""`ROLE-a`: an assigned ``cpu_fan`` role reaches the displayed floor and the copy.

A daemon advertising ``control.cpu_fan_role_floor`` floors a member whose header
is assigned ``cpu_fan`` at 30% on the assignment alone
(``assigned_role_earns_hard_floor`` → ``member_effective_floor``), as it always
did for ``pump`` (DEC-417 mirrors that one). An older daemon adds no floor, so the
GUI shows one only where the capability says it is enforced.

Every fixture is a label-less header (``pwm2`` on an it8696, which publishes no
label files) with a role-less member label, so the label classifier says chassis
and the role is the ONLY evidence.
"""

from __future__ import annotations

from dataclasses import replace

from control_ofc.api.models import (
    Capabilities,
    ControlCapability,
    FanReading,
    HeaderRoleResult,
    HwmonHeader,
    OpenFanRole,
)
from control_ofc.services.controls_view import min_pwm_badge
from control_ofc.services.fan_cards_view import FanState, build_fan_card_vms
from control_ofc.services.header_role_view import (
    CPU_FAN_FLOORED_CHOICE,
    ROLE_CHOICES,
    outcome_message,
    role_choices,
)
from control_ofc.services.openfan_role_view import (
    OPENFAN_CPU_FAN_FLOORED_CHOICE,
    OPENFAN_ROLE_CHOICES,
    openfan_role_choices,
)
from control_ofc.services.profile_service import (
    CONTROL_ROLE_CHASSIS,
    CONTROL_ROLE_CPU_PUMP,
    ROLE_MINIMUM_PCT,
    ControlMember,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
    floor_role_header_roles,
    infer_member_role,
    member_minimum_pct,
)
from control_ofc.ui.pages.controls_page import ControlsPage

CPU_FLOOR = ROLE_MINIMUM_PCT[CONTROL_ROLE_CPU_PUMP]
CHASSIS_FLOOR = ROLE_MINIMUM_PCT[CONTROL_ROLE_CHASSIS]

FLOORED = Capabilities(control=ControlCapability(header_roles=True, cpu_fan_role_floor=True))
OLDER = Capabilities(control=ControlCapability(header_roles=True))


def _header(index: int = 2, role: str = "cpu_fan") -> HwmonHeader:
    return HwmonHeader(
        id=f"hwmon:it8696:it87.2624:pwm{index}:pwm{index}",
        label=f"pwm{index}",
        chip_name="it8696",
        device_id="it87.2624",
        pwm_index=index,
        is_writable=True,
        role=role,
    )


def _member(header: HwmonHeader, label: str = "Front") -> ControlMember:
    return ControlMember(source="hwmon", member_id=header.id, member_label=label)


def _control(*members: ControlMember, minimum_pct: float = CHASSIS_FLOOR) -> LogicalControl:
    return LogicalControl(
        id="c1", name="Loop", curve_id="k1", minimum_pct=minimum_pct, members=list(members)
    )


def _curves() -> list[CurveConfig]:
    return [CurveConfig(id="k1", name="K", type=CurveType.FLAT, flat_output_pct=50.0)]


def test_fixture_is_chassis_by_label():
    """Precondition: the label terms alone say chassis, so any 30% seen below can
    only have come from the role."""
    assert infer_member_role(_member(_header())) == CONTROL_ROLE_CHASSIS
    assert CPU_FLOOR > CHASSIS_FLOOR


# ── The shared predicate ──────────────────────────────────────────────


class TestFloorRoleHeaderRoles:
    def test_the_capability_adds_the_cpu_fan_role(self):
        pump, cpu, rad = _header(1, "pump"), _header(2, "cpu_fan"), _header(3, "radiator_fan")
        assert dict(floor_role_header_roles([pump, cpu, rad], FLOORED, ())) == {
            pump.id: "pump",
            cpu.id: "cpu_fan",
        }
        # The opposite branch: an older daemon floors only the pump role.
        assert dict(floor_role_header_roles([pump, cpu, rad], OLDER, ())) == {pump.id: "pump"}
        assert dict(floor_role_header_roles([pump, cpu, rad], None, ())) == {pump.id: "pump"}


class TestMemberMinimumPct:
    def test_an_assigned_cpu_fan_is_floored_only_where_the_daemon_floors_it(self):
        header = _header()
        member = _member(header)
        control = _control(member)
        floored = member_minimum_pct(
            control, member, floor_role_header_roles([header], FLOORED, ())
        )
        older = member_minimum_pct(control, member, floor_role_header_roles([header], OLDER, ()))
        assert (older, floored) == (CHASSIS_FLOOR, CPU_FLOOR)


# ── The badge view-model ──────────────────────────────────────────────


class TestMinPwmBadge:
    def test_a_cpu_fan_role_lifts_the_badge_and_names_the_role(self):
        header = _header()
        badge = min_pwm_badge(
            _control(_member(header)), floor_role_header_roles([header], FLOORED, ())
        )
        assert badge.floor_pct == CPU_FLOOR
        assert "assigned the CPU-fan role" in badge.tooltip
        # A CPU fan is not a pump: no pump wording.
        assert "pump" not in badge.tooltip

    def test_an_older_daemon_shows_the_chassis_badge(self):
        header = _header()
        badge = min_pwm_badge(
            _control(_member(header)), floor_role_header_roles([header], OLDER, ())
        )
        assert badge.floor_pct == CHASSIS_FLOOR
        assert "assigned the CPU-fan role" not in badge.tooltip

    def test_pump_and_cpu_fan_roles_together_name_both(self):
        pump_h, cpu_h, fan_h = _header(1, "pump"), _header(2), _header(3, "chassis_fan")
        control = _control(_member(pump_h, "A"), _member(cpu_h, "B"), _member(fan_h, "C"))
        badge = min_pwm_badge(control, floor_role_header_roles([pump_h, cpu_h, fan_h], FLOORED, ()))
        assert badge.floor_pct == CPU_FLOOR
        assert "assigned the pump or CPU-fan role" in badge.tooltip
        assert "protects the pump from stalling" in badge.tooltip
        assert (
            f"It applies to the pump or CPU-fan-assigned members; the other fans in "
            f"this control keep {control.minimum_pct:.0f}%." in badge.tooltip
        )


# ── Call site 1: the Controls page, capabilities after headers ─────────


def test_controls_page_badge_follows_capabilities_that_arrive_after_the_headers(
    qtbot, app_state, profile_service
):
    """The headers can land before the capabilities. The card holds the page's
    live resolver, but only a repaint on ``capabilities_updated`` makes it re-read
    them — the pre-fix page repainted on ``headers_updated`` alone."""
    header = _header()
    app_state.set_hwmon_headers([header])
    control = _control(_member(header))
    profile = Profile(id="p1", name="P", controls=[control], curves=_curves())
    profile_service._profiles[profile.id] = profile
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page.select_profile(profile.id)
    card = page._control_cards[control.id]
    assert card._min_pwm_label.text() == f"Min: {CHASSIS_FLOOR:.0f}%"

    app_state.set_capabilities(FLOORED)
    assert card._min_pwm_label.text() == f"Min: {CPU_FLOOR:.0f}%"
    assert "assigned the CPU-fan role" in card._min_pwm_label.toolTip()

    # And the role, live both ways.
    app_state.set_hwmon_headers([replace(header, role="chassis_fan")])
    assert card._min_pwm_label.text() == f"Min: {CHASSIS_FLOOR:.0f}%"


# ── Call site 2: the Dashboard fan card judges LOW_RPM against the floor ─


def _dashboard_state(caps: Capabilities) -> FanState:
    """0 RPM at a duty between the chassis and CPU floors: LOW_RPM only if the
    floor the card uses is the chassis one."""
    header = _header()
    duty = round((CHASSIS_FLOOR + CPU_FLOOR) / 2)
    profile = Profile(id="p1", name="P", controls=[_control(_member(header))], curves=_curves())
    [card] = build_fan_card_vms(
        [FanReading(id=header.id, source="hwmon", rpm=0, pwm_commanded_pct=duty, age_ms=100)],
        active_profile=profile,
        overrides=[],
        headers=[header],
        caps=caps,
        stalled_ids=(),
    )
    return card.state


def test_dashboard_card_uses_the_cpu_fan_role_floor_only_where_enforced():
    assert _dashboard_state(FLOORED) is not FanState.LOW_RPM
    assert _dashboard_state(OLDER) is FanState.LOW_RPM


# ── The picker copy says what this daemon does ─────────────────────────


def _effect(choices, token: str) -> str:
    return next(c.effect for c in choices if c.token == token)


class TestPickerCopy:
    def test_header_picker_names_the_floor_only_where_the_daemon_applies_it(self):
        floored, older = role_choices(FLOORED), role_choices(OLDER)
        assert _effect(floored, "cpu_fan") == CPU_FAN_FLOORED_CHOICE.effect
        assert "adds no floor" in _effect(older, "cpu_fan")
        # Only the CPU-fan text changes; every other choice is the same.
        assert [c.token for c in floored] == [c.token for c in older]
        assert [c for c in floored if c.token != "cpu_fan"] == [
            c for c in older if c.token != "cpu_fan"
        ]

    def test_the_floored_copy_says_a_cpu_fan_is_still_stoppable(self):
        """DEC-311: the floor is not pump protection; identify still stops it."""
        assert "not a pump" in CPU_FAN_FLOORED_CHOICE.effect
        assert "stops" in CPU_FAN_FLOORED_CHOICE.effect
        assert "not a pump" in OPENFAN_CPU_FAN_FLOORED_CHOICE.effect
        assert CPU_FAN_FLOORED_CHOICE not in ROLE_CHOICES

    def test_openfan_picker_names_the_floor_only_where_the_daemon_applies_it(self):
        floored, older = openfan_role_choices(FLOORED), openfan_role_choices(OLDER)
        assert _effect(floored, "cpu_fan") == OPENFAN_CPU_FAN_FLOORED_CHOICE.effect
        assert _effect(older, "cpu_fan") == _effect(OPENFAN_ROLE_CHOICES, "cpu_fan")
        assert "adds no floor" in _effect(older, "cpu_fan")

    def test_outcome_message_follows_the_capability(self):
        result = HeaderRoleResult(role="cpu_fan", effective_role="cpu_fan")
        floored = outcome_message("X", result, _header(), FLOORED)
        older = outcome_message("X", result, _header(), OLDER)
        assert "safety floor" in floored and "adds no floor" not in floored
        assert "adds no floor" in older and "safety floor" not in older


# ── OpenFan channels: the role is the only evidence (`ROLE-f`) ─────────


def _channel(role: str, fan_id: str = "openfan:ch04") -> OpenFanRole:
    return OpenFanRole(fan_id=fan_id, channel=4, role=role, role_source="user_assigned")


def _openfan_member(member_id: str = "openfan:ch04") -> ControlMember:
    return ControlMember(source="openfan", member_id=member_id, member_label="Channel 4")


class TestOpenFanChannels:
    def test_a_channel_role_floors_its_member_as_the_daemon_does(self):
        """The daemon floors an assigned OpenFan pump (`ROLE-f`) and, with the
        capability, an assigned CPU fan; the display follows both, and the
        daemon's own unpadded-id match (``role_key``)."""
        for role, caps, expected in [
            ("cpu_fan", FLOORED, CPU_FLOOR),
            ("cpu_fan", OLDER, CHASSIS_FLOOR),
            ("pump", OLDER, CPU_FLOOR),
            ("chassis_fan", FLOORED, CHASSIS_FLOOR),
        ]:
            roles = floor_role_header_roles([], caps, [_channel(role)])
            for member_id in ("openfan:ch04", "openfan:ch4"):
                member = _openfan_member(member_id)
                got = member_minimum_pct(_control(member), member, roles)
                assert got == expected, (role, caps, member_id)

    def test_the_badge_names_a_channel_role(self):
        member = _openfan_member()
        badge = min_pwm_badge(
            _control(member), floor_role_header_roles([], FLOORED, [_channel("cpu_fan")])
        )
        assert badge.floor_pct == CPU_FLOOR
        assert "assigned the CPU-fan role" in badge.tooltip

    def test_a_non_channel_openfan_id_borrows_nothing(self):
        header = _header()
        stray = ControlMember(source="openfan", member_id=header.id)
        roles = floor_role_header_roles([header], FLOORED, [_channel("cpu_fan")])
        assert member_minimum_pct(_control(stray), stray, roles) == CHASSIS_FLOOR

    def test_dashboard_card_takes_the_channel_role(self):
        duty = round((CHASSIS_FLOOR + CPU_FLOOR) / 2)
        member = _openfan_member()
        profile = Profile(id="p1", name="P", controls=[_control(member)], curves=_curves())

        def state(channel_roles):
            [card] = build_fan_card_vms(
                [
                    FanReading(
                        id=member.member_id,
                        source="openfan",
                        rpm=0,
                        pwm_commanded_pct=duty,
                        age_ms=100,
                    )
                ],
                active_profile=profile,
                overrides=[],
                headers=[],
                caps=FLOORED,
                openfan_roles=channel_roles,
                stalled_ids=(),
            )
            return card.state

        assert state([_channel("cpu_fan")]) is not FanState.LOW_RPM
        assert state([]) is FanState.LOW_RPM


def test_controls_page_badge_follows_an_openfan_role(qtbot, app_state, profile_service):
    app_state.set_capabilities(FLOORED)
    member = _openfan_member()
    control = _control(member)
    profile = Profile(id="p1", name="P", controls=[control], curves=_curves())
    profile_service._profiles[profile.id] = profile
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page.select_profile(profile.id)
    card = page._control_cards[control.id]
    assert card._min_pwm_label.text() == f"Min: {CHASSIS_FLOOR:.0f}%"

    app_state.set_openfan_roles([_channel("cpu_fan")])
    assert card._min_pwm_label.text() == f"Min: {CPU_FLOOR:.0f}%"
