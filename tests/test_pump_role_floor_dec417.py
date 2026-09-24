"""DEC-417 (`TS-w`): a header's pump ROLE reaches the displayed floor.

The daemon floors a member at 30% when its labels say CPU/pump OR its header is
assigned the ``pump`` role (``assigned_role_is_pump`` → ``member_effective_floor``).
The GUI mirrored only the label terms, so a member authored before its header was
assigned ``pump`` — or through a picker that never tags it — showed 20%.

By the user's choices: a live display mirror (the profile is not touched), on
``HwmonHeader.role == "pump"``, for the control card's Min badge and the Dashboard
fan-card state only. The manual slider keeps the label-derived floor.

Every fixture is a label-less header (``pwm2`` on an it8696, which publishes no
label files) with a role-less member label, so the label classifier says chassis
and the role is the ONLY evidence — the case the defect lives in.
"""

from __future__ import annotations

from dataclasses import replace

from control_ofc.api.models import FanReading, HwmonHeader
from control_ofc.services.controls_view import min_pwm_badge
from control_ofc.services.fan_cards_view import FanState, build_fan_card_vms
from control_ofc.services.profile_service import (
    CONTROL_ROLE_CHASSIS,
    CONTROL_ROLE_CPU_PUMP,
    ROLE_MINIMUM_PCT,
    ControlMember,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
    infer_member_role,
    member_minimum_pct,
    pump_role_floor_pct,
    pump_role_header_ids,
)
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.widgets.control_card import ControlCard

PUMP_FLOOR = ROLE_MINIMUM_PCT[CONTROL_ROLE_CPU_PUMP]
CHASSIS_FLOOR = ROLE_MINIMUM_PCT[CONTROL_ROLE_CHASSIS]


def _header(index: int = 2, role: str = "chassis_fan") -> HwmonHeader:
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
    """Precondition for every test below: the label terms alone say chassis, so
    any 30% seen here can only have come from the role."""
    assert infer_member_role(_member(_header(role="pump"))) == CONTROL_ROLE_CHASSIS
    assert PUMP_FLOOR > CHASSIS_FLOOR


# ── The shared predicate ──────────────────────────────────────────────


class TestPumpRoleHeaderIds:
    def test_only_the_pump_role_counts(self):
        pump, rad, cpu = _header(1, "pump"), _header(2, "radiator_fan"), _header(3, "cpu_fan")
        assert pump_role_header_ids([pump, rad, cpu]) == frozenset({pump.id})

    def test_no_headers_is_no_roles(self):
        assert pump_role_header_ids(None) == frozenset()
        assert pump_role_header_ids([]) == frozenset()

    def test_only_an_hwmon_member_takes_the_role(self):
        """An id collision from another source must not borrow a header's role."""
        header = _header(role="pump")
        ids = pump_role_header_ids([header])
        stray = ControlMember(source="openfan", member_id=header.id)
        assert pump_role_floor_pct([stray], ids) == 0.0
        assert pump_role_floor_pct([_member(header)], ids) == PUMP_FLOOR


class TestMemberMinimumPct:
    def test_the_role_raises_the_member_floor(self):
        header = _header(role="pump")
        member = _member(header)
        control = _control(member)
        with_role = member_minimum_pct(control, member, pump_role_header_ids([header]))
        without = member_minimum_pct(control, member, frozenset())
        assert (without, with_role) == (CHASSIS_FLOOR, PUMP_FLOOR)

    def test_union_only_never_lowers_a_higher_floor(self):
        header = _header(role="pump")
        member = _member(header)
        control = _control(member, minimum_pct=45.0)
        assert member_minimum_pct(control, member, pump_role_header_ids([header])) == 45.0

    def test_the_role_reaches_only_its_own_member(self):
        """The daemon floors the assigned member, not its neighbours."""
        pump_h, fan_h = _header(1, "pump"), _header(2, "chassis_fan")
        pump, fan = _member(pump_h, "Loop A"), _member(fan_h, "Rad")
        control = _control(pump, fan)
        ids = pump_role_header_ids([pump_h, fan_h])
        assert member_minimum_pct(control, pump, ids) == PUMP_FLOOR
        assert member_minimum_pct(control, fan, ids) == CHASSIS_FLOOR


# ── The badge view-model ──────────────────────────────────────────────


class TestMinPwmBadge:
    def test_a_role_lifts_the_badge_and_says_so(self):
        header = _header(role="pump")
        badge = min_pwm_badge(_control(_member(header)), pump_role_header_ids([header]))
        assert badge.floor_pct == PUMP_FLOOR
        assert "assigned the pump role" in badge.tooltip
        # A lone member: nobody else to name.
        assert "other fans" not in badge.tooltip

    def test_without_the_role_it_is_the_chassis_badge(self):
        badge = min_pwm_badge(_control(_member(_header(role="chassis_fan"))), frozenset())
        assert badge.floor_pct == CHASSIS_FLOOR
        assert "assigned the pump role" not in badge.tooltip
        assert "chassis fans" in badge.tooltip

    def test_a_mixed_control_says_whom_the_figure_covers(self):
        pump_h, fan_h = _header(1, "pump"), _header(2, "chassis_fan")
        control = _control(_member(pump_h, "Loop A"), _member(fan_h, "Rad"))
        badge = min_pwm_badge(control, pump_role_header_ids([pump_h, fan_h]))
        assert badge.floor_pct == PUMP_FLOOR
        assert (
            f"It applies to the pump-assigned member; the other fans in this control "
            f"keep {control.minimum_pct:.0f}%." in badge.tooltip
        )

    def test_the_other_fans_get_the_number_the_daemon_holds_them_at(self):
        """Review P3-2: the daemon floors a non-pump member at ``minimum_pct``
        alone. A profile whose minimum sits below the GUI's 20% role default is the
        one case where that differs from the badge's own base — so it is the case
        that discriminates."""
        pump_h, fan_h = _header(1, "pump"), _header(2, "chassis_fan")
        control = _control(_member(pump_h, "Loop A"), _member(fan_h, "Rad"), minimum_pct=10.0)
        badge = min_pwm_badge(control, pump_role_header_ids([pump_h, fan_h]))
        assert control.minimum_pct < CHASSIS_FLOOR  # precondition: the two differ
        assert badge.floor_pct == PUMP_FLOOR
        assert f"the other fans in this control keep {control.minimum_pct:.0f}%." in badge.tooltip
        assert f"keep {CHASSIS_FLOOR:.0f}%" not in badge.tooltip

    def test_a_label_pump_keeps_its_own_wording(self):
        """A pump found by its label raises the whole control, so the assignment
        text (which says it covers one member) must not appear."""
        header = _header(role="pump")
        control = _control(_member(header, "AIO Pump"))
        badge = min_pwm_badge(control, pump_role_header_ids([header]))
        assert badge.floor_pct == PUMP_FLOOR
        assert "derived from a CPU or pump member" in badge.tooltip
        assert "assigned the pump role" not in badge.tooltip

    def test_a_higher_user_floor_is_not_credited_to_the_role(self):
        header = _header(role="pump")
        control = _control(_member(header), minimum_pct=45.0)
        badge = min_pwm_badge(control, pump_role_header_ids([header]))
        assert badge.floor_pct == 45.0
        assert "assigned the pump role" not in badge.tooltip


# ── The card: badge follows, slider does not (the user's split) ────────


def test_card_badge_takes_the_role_and_the_slider_keeps_the_label_floor(qtbot):
    header = _header(role="pump")
    control = _control(_member(header))
    ids = pump_role_header_ids([header])
    card = ControlCard(control, _curves(), pump_header_ids=lambda: ids)
    qtbot.addWidget(card)
    assert card._min_pwm_label.text() == f"Min: {PUMP_FLOOR:.0f}%"
    card._manual_btn.setChecked(True)
    assert card._manual_slider.minimum() == round(card._effective_floor()) == CHASSIS_FLOOR


def test_card_without_a_resolver_shows_no_role(qtbot):
    card = ControlCard(_control(_member(_header(role="pump"))), _curves())
    qtbot.addWidget(card)
    assert card._min_pwm_label.text() == f"Min: {CHASSIS_FLOOR:.0f}%"


# ── Call site 1: the Controls page wires the live headers ──────────────


def test_controls_page_badge_follows_a_role_assigned_after_the_cards(
    qtbot, app_state, profile_service
):
    """The pre-fix path cannot produce this: the card is built while the header is
    a chassis fan, then the role changes on the wire. Only a card holding the page's
    LIVE resolver, repainted on ``headers_updated``, reads the new floor."""
    header = _header(role="chassis_fan")
    app_state.set_hwmon_headers([header])
    control = _control(_member(header))
    profile = Profile(id="p1", name="P", controls=[control], curves=_curves())
    profile_service._profiles[profile.id] = profile
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page.select_profile(profile.id)
    card = page._control_cards[control.id]
    assert card._min_pwm_label.text() == f"Min: {CHASSIS_FLOOR:.0f}%"

    app_state.set_hwmon_headers([replace(header, role="pump")])
    assert card._min_pwm_label.text() == f"Min: {PUMP_FLOOR:.0f}%"
    assert "assigned the pump role" in card._min_pwm_label.toolTip()

    # And back: the mirror is live both ways, as the daemon's assignment term is.
    app_state.set_hwmon_headers([header])
    assert card._min_pwm_label.text() == f"Min: {CHASSIS_FLOOR:.0f}%"


# ── Call site 2: the Dashboard fan card judges LOW_RPM against the floor ─


def _stalled_below_pump_floor(header: HwmonHeader) -> FanReading:
    """0 RPM at a duty between the chassis and pump floors: LOW_RPM only if the
    floor the card uses is the chassis one."""
    duty = round((CHASSIS_FLOOR + PUMP_FLOOR) / 2)
    return FanReading(id=header.id, source="hwmon", rpm=0, pwm_commanded_pct=duty, age_ms=100)


def test_dashboard_card_uses_the_role_floor():
    header = _header(role="pump")
    profile = Profile(id="p1", name="P", controls=[_control(_member(header))], curves=_curves())
    [card] = build_fan_card_vms(
        [_stalled_below_pump_floor(header)],
        active_profile=profile,
        overrides=[],
        headers=[header],
    )
    assert card.state is not FanState.LOW_RPM


def test_dashboard_card_without_the_role_still_flags_low_rpm():
    header = _header(role="chassis_fan")
    profile = Profile(id="p1", name="P", controls=[_control(_member(header))], curves=_curves())
    [card] = build_fan_card_vms(
        [_stalled_below_pump_floor(header)],
        active_profile=profile,
        overrides=[],
        headers=[header],
    )
    assert card.state is FanState.LOW_RPM
