"""AIO-MB Phase 6 (DEC-318): the Hardware page surfaces the cooling stack.

Phase 6 creates no new intelligence — it surfaces what Phases 1-5 already know.
So the tests that matter are the ones asserting the GUI does not *lose* or
*distort* that knowledge on the way to the screen:

* requested PWM and readback PWM stay two numbers, never one (§6);
* a driver that says nothing reads as **Unknown**, not as "unsupported" (§4);
* a normal motherboard AIO produces **zero** warnings (§18);
* nothing that was never tested is reported as PASS (§15);
* the diagnostics buttons invoke the EXISTING implementation, proven at the
  call site rather than at the payload — "extracting a rule does not test the
  call site" has bitten this project six times.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton

from control_ofc.api.models import (
    VALIDATION_KIND_LIFECYCLE,
    Capabilities,
    CharacterizationRun,
    CharPoint,
    CharSummary,
    ConnectionState,
    ControlCapability,
    CoolingDevice,
    CoolingDeviceInventory,
    FanReading,
    HwmonHeader,
    SensorReading,
    ValidationFinding,
    ValidationSession,
    parse_fans,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.characterization_view import build_characterization_view
from control_ofc.services.cooling_device_view import (
    NOT_CONTROLLED_TEXT,
    build_cooling_device_view,
    pump_strategy_text,
)
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.header_inspector_view import (
    OWNER_DAEMON,
    OWNER_EXTERNAL,
    STATUS_NORMAL,
    SUPPORTED,
    UNAVAILABLE,
    UNKNOWN,
    UNKNOWN_TEXT,
    build_header_inspector_view,
    build_header_inspector_views,
    control_ownership,
    requested_pct,
)
from control_ofc.services.validation_view import build_validation_session_view
from control_ofc.services.verify_view import build_verify_result_view
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.widgets.pwm_header_card import PwmHeaderCard
from control_ofc.ui.widgets.validation_session_dialog import ValidationSessionDialog

# ── fixtures ─────────────────────────────────────────────────────────────────


def _caps(**control) -> Capabilities:
    control.setdefault("header_roles", True)
    control.setdefault("pwm_characterization", True)
    control.setdefault("cooling_devices", True)
    control.setdefault("validation_sessions", True)
    return Capabilities(control=ControlCapability(**control))


def _pump_header(**kw) -> HwmonHeader:
    base = {
        "id": "hwmon:nct6799:isa-0a20:pwm5:AIO_PUMP",
        "label": "AIO_PUMP",
        "chip_name": "nct6799",
        "device_id": "isa-0a20",
        "pwm_index": 5,
        "supports_enable": True,
        "rpm_available": True,
        "is_writable": True,
        "role": "pump",
        "role_source": "label",
        "effective_min_pwm_pct": 30,
        "stop_permitted": False,
    }
    base.update(kw)
    return HwmonHeader(**base)


def _fan_header(**kw) -> HwmonHeader:
    base = {
        "id": "hwmon:nct6799:isa-0a20:pwm1:CPU_FAN",
        "label": "CPU_FAN",
        "chip_name": "nct6799",
        "device_id": "isa-0a20",
        "pwm_index": 1,
        "supports_enable": True,
        "rpm_available": True,
        "is_writable": True,
        "role": "cpu_fan",
        "role_source": "label",
    }
    base.update(kw)
    return HwmonHeader(**base)


def _reading(header_id: str, **kw) -> FanReading:
    base = {
        "id": header_id,
        "source": "hwmon",
        "rpm": 1284,
        "last_commanded_pwm": 38,
        "pwm_readback_pct": 38,
        "pwm_commanded_pct": 38,
        "pwm_enable_mode": 1,
        "age_ms": 500,
    }
    base.update(kw)
    return FanReading(**base)


# ── §6: the two PWM axes must never collapse ─────────────────────────────────


def test_requested_and_readback_are_separate_numbers():
    """The acceptance criterion, asserted where the values disagree.

    Equal values would pass even if both rows were wired to the same field —
    which is precisely the bug §6 forbids and the reason DEC-318 added a
    single-producer command field at all.
    """
    view = build_header_inspector_view(
        _pump_header(),
        reading=_reading(
            "hwmon:nct6799:isa-0a20:pwm5:AIO_PUMP",
            pwm_commanded_pct=45,
            pwm_readback_pct=30,
        ),
        capabilities=_caps(),
    )
    rows = {r.label: r.value for r in view.live_rows}
    assert rows["Requested PWM"] == "45%"
    assert rows["Readback PWM"] == "30%"
    assert rows["Requested PWM"] != rows["Readback PWM"]


def test_requested_pwm_prefers_the_single_producer_field():
    """`pwm_commanded_pct` outranks `last_commanded_pwm`, which is ambiguous."""
    value, approximate = requested_pct(
        FanReading(id="h", pwm_commanded_pct=45, last_commanded_pwm=30)
    )
    assert (value, approximate) == (45, False)


def test_pre_dec318_daemon_flags_its_requested_value_as_approximate():
    """An older daemon's only candidate MAY be a readback (AIO5-a).

    It is still shown — a blank would be less useful — but never silently, and
    the caveat travels with it as a note the card renders as a tooltip.
    """
    value, approximate = requested_pct(FanReading(id="h", last_commanded_pwm=30))
    assert (value, approximate) == (30, True)

    view = build_header_inspector_view(
        _pump_header(),
        reading=_reading(
            "hwmon:nct6799:isa-0a20:pwm5:AIO_PUMP",
            pwm_commanded_pct=None,
            last_commanded_pwm=30,
        ),
        capabilities=_caps(),
    )
    assert view.requested_is_approximate is True
    note = next(r.note for r in view.live_rows if r.label == "Requested PWM")
    assert note, "an approximated command must carry its caveat"


def test_new_wire_field_round_trips_through_the_parser():
    fans = parse_fans({"fans": [{"id": "h1", "source": "hwmon", "pwm_commanded_pct": 45}]})
    assert fans[0].pwm_commanded_pct == 45


def test_absent_command_is_unknown_never_zero():
    """`None` must never render as 0% — a pump at "0%" is the truthfulness bug
    the optional wire field exists to prevent."""
    view = build_header_inspector_view(
        _pump_header(),
        reading=_reading(
            "hwmon:nct6799:isa-0a20:pwm5:AIO_PUMP",
            pwm_commanded_pct=None,
            last_commanded_pwm=None,
        ),
        capabilities=_caps(),
    )
    rows = {r.label: r.value for r in view.live_rows}
    assert rows["Requested PWM"] == UNKNOWN_TEXT
    assert "0%" not in rows["Requested PWM"]


# ── §6: control ownership uses the daemon's own rule ─────────────────────────


def test_control_ownership_matches_the_daemons_rule():
    assert control_ownership(FanReading(id="h", pwm_enable_mode=1)) == OWNER_DAEMON
    assert control_ownership(FanReading(id="h", pwm_enable_mode=2)) == OWNER_EXTERNAL
    # Absent is UNKNOWN, never "external" — a driver with no `pwm_enable`
    # attribute is not evidence that something else is in control.
    assert control_ownership(FanReading(id="h", pwm_enable_mode=None)) == UNKNOWN
    assert control_ownership(None) == UNKNOWN


# ── §4: the capability vocabulary, and its most common case ──────────────────


def test_empty_enable_modes_reads_as_unknown_not_unsupported():
    """EMPTY MEANS UNKNOWN, and it is the majority case.

    The daemon's chip table covers `it87` and `nct6775` and nothing else, so
    most boards report an empty list. Rendering that as "none supported" would
    report working hardware as crippled on the majority of machines.
    """
    view = build_header_inspector_view(
        _pump_header(supported_pwm_enable_modes=[]), capabilities=_caps()
    )
    row = next(r for r in view.capability_rows if r.label == "Supported control modes")
    assert row.value == UNKNOWN
    assert "Unsupported" not in row.value


def test_known_enable_modes_are_listed():
    view = build_header_inspector_view(
        _pump_header(supported_pwm_enable_modes=[0, 1, 2]), capabilities=_caps()
    )
    row = next(r for r in view.capability_rows if r.label == "Supported control modes")
    assert row.value == "0, 1, 2"


def test_supported_capabilities_read_as_supported():
    """The positive case, so the Unknown/Unavailable tests below cannot pass by
    the vocabulary having collapsed to a single token."""
    view = build_header_inspector_view(
        _pump_header(is_writable=True, supports_enable=True, rpm_available=True),
        reading=_reading("hwmon:nct6799:isa-0a20:pwm5:AIO_PUMP"),
        capabilities=_caps(),
    )
    caps = {r.label: r.value for r in view.capability_rows}
    assert caps["PWM write"] == SUPPORTED
    assert caps["pwm_enable"] == SUPPORTED
    assert caps["RPM telemetry"] == SUPPORTED
    assert caps["PWM readback"] == SUPPORTED


def test_unsupported_attributes_show_unavailable_not_invented_values():
    """§3: "do not invent values for unsupported attributes"."""
    view = build_header_inspector_view(
        _fan_header(rpm_available=False, pwm_freq_hz=None, tach_pulses_per_rev=None),
        capabilities=_caps(),
    )
    caps = {r.label: r.value for r in view.capability_rows}
    assert caps["RPM telemetry"] == UNAVAILABLE
    assert caps["PWM base frequency"] == UNKNOWN_TEXT
    assert caps["Tach pulses/rev"] == UNKNOWN


def test_missing_optional_capability_fields_do_not_raise():
    """A pre-2.31 daemon omits every audit field; the card must still build."""
    bare = HwmonHeader(id="hwmon:it87:isa-0290:pwm1:pwm1", pwm_index=1, chip_name="it87")
    view = build_header_inspector_view(bare, capabilities=None)
    assert view.header_id == bare.id
    assert view.live_rows and view.capability_rows and view.safety_rows


# ── §5: classification and safety ────────────────────────────────────────────


def test_pump_safety_information_renders():
    view = build_header_inspector_view(_pump_header(), capabilities=_caps())
    safety = {r.label: r.value for r in view.safety_rows}
    assert safety["Role"] == "Pump"
    assert safety["Role source"] == "Hardware label"
    assert safety["Device safety floor"] == "30%"
    assert safety["Fan stop"] == "Prohibited"
    assert view.pump_protected is True


def test_the_floor_is_labelled_as_a_DEVICE_floor_not_a_control_floor():
    """`effective_min_pwm_pct` excludes the profile's own `minimum_pct`/`stop_pct`.

    Calling it "the floor for this control" would over-claim; the wording is
    part of the contract, so it is pinned.
    """
    view = build_header_inspector_view(_pump_header(), capabilities=_caps())
    labels = [r.label for r in view.safety_rows]
    assert "Device safety floor" in labels
    assert not any("control" in label.lower() for label in labels)


def test_unknown_floor_renders_as_dash_never_zero():
    view = build_header_inspector_view(
        _fan_header(effective_min_pwm_pct=None, stop_permitted=None), capabilities=_caps()
    )
    floor = next(r.value for r in view.safety_rows if r.label == "Device safety floor")
    assert floor == UNKNOWN_TEXT


def test_a_user_downgraded_pump_is_still_protected():
    """DEC-312: the wire `role` is the DISPLAY role and can be downgraded, while
    the daemon's safety predicate is a UNION. Reading `role` alone is a bug."""
    header = _pump_header(role="chassis_fan", role_source="user_assigned")
    view = build_header_inspector_view(header, capabilities=_caps())
    assert view.role_label == "Chassis fan", "the display role is shown as assigned"
    assert view.pump_protected is True, "but the safety predicate still protects it"
    assert next(r.value for r in view.safety_rows if r.label == "Fan stop") == "Prohibited"


# ── §18: a normal motherboard AIO raises NO warnings ─────────────────────────


def test_a_normal_motherboard_aio_produces_no_warnings():
    """Alarm fatigue is the failure mode §18 names.

    No coolant telemetry, no `pwm_freq`, no `fanN_pulses` and one tach behind a
    splitter are all NORMAL. If any of them escalated, this fails.
    """
    header = _pump_header(
        pwm_freq_hz=None,
        tach_pulses_per_rev=None,
        supported_pwm_enable_modes=[],
        rpm_min_threshold=None,
        rpm_max_threshold=None,
    )
    view = build_header_inspector_view(
        header,
        reading=_reading(header.id),
        capabilities=_caps(),
    )
    assert view.status == STATUS_NORMAL
    assert view.status_state == "ok"
    escalated = [
        r.label
        for r in (*view.live_rows, *view.capability_rows, *view.safety_rows)
        if r.state in ("warn", "critical") and r.label != "Fan stop"
    ]
    assert escalated == [], f"normal hardware escalated: {escalated}"


def test_a_fan_alarm_does_escalate():
    """The counterpart: the guard above must not pass by never escalating."""
    header = _fan_header()
    view = build_header_inspector_view(
        header,
        reading=_reading(header.id, fan_alarm=True),
        capabilities=_caps(),
    )
    assert view.status_state == "critical"


# ── §10: device override uses cautious wording ───────────────────────────────


def test_device_override_is_observed_evidence_never_a_failure():
    run = CharacterizationRun(
        run_id="r1",
        header_id="h",
        state="complete",
        points=[],
        summary=CharSummary(
            command_acceptance="pass",
            pwm_readback="pass",
            rpm_response="no_response",
            possible_device_override=True,
        ),
    )
    view = build_characterization_view(run, header_label="AIO_PUMP")
    text = " ".join(view.notes).lower()
    # §10: "do not claim an internal device override as certain". The note must
    # hedge, and must never be worded as a failed write.
    hedges = ("may", "some", "suggests", "possible", "temporarily", "before concluding")
    assert any(h in text for h in hedges), f"override wording is not cautious: {text}"
    assert "fail" not in text, "an override hint must never be worded as a failure"


def test_device_override_hint_never_fires_on_missing_data():
    """ "We cannot see the RPM" is not evidence of an override."""
    view = build_header_inspector_view(
        _pump_header(),
        reading=_reading("hwmon:nct6799:isa-0a20:pwm5:AIO_PUMP", rpm=None),
        capabilities=_caps(),
    )
    assert view.status == STATUS_NORMAL


# ── §9: characterisation timing ──────────────────────────────────────────────


def _run_with_timing() -> CharacterizationRun:
    return CharacterizationRun(
        run_id="r1",
        header_id="h",
        state="complete",
        requested_points_pct=[30, 40],
        points=[
            CharPoint(
                requested_pct=30,
                command_accepted=True,
                readback_pct=30,
                rpm_after=910,
                readback_verdict="match",
                rpm_verdict="changed",
                first_change_ms=410,
                settle_ms=1800,
            ),
            CharPoint(
                requested_pct=40,
                command_accepted=True,
                readback_pct=40,
                rpm_after=1170,
                readback_verdict="match",
                rpm_verdict="changed",
                first_change_ms=380,
                settle_ms=1700,
            ),
        ],
    )


def test_characterisation_rows_carry_response_and_settling():
    view = build_characterization_view(_run_with_timing(), header_label="AIO_PUMP")
    assert [r.response for r in view.rows] == ["0.4 s", "0.4 s"]
    assert [r.settling for r in view.rows] == ["1.8 s", "1.7 s"]


def test_characterisation_summary_reports_typical_timings():
    view = build_characterization_view(_run_with_timing(), header_label="AIO_PUMP")
    assert view.response_latency.startswith("~")
    assert view.settling_time.startswith("~")


def test_unmeasured_timing_is_a_dash_not_zero():
    """A header with no tach yields no first-change time. "0.0 s" would report
    an instant response where there was no response to time."""
    run = CharacterizationRun(
        run_id="r",
        header_id="h",
        state="complete",
        points=[
            CharPoint(
                requested_pct=30,
                command_accepted=True,
                readback_pct=30,
                readback_verdict="match",
                rpm_verdict="unavailable",
                first_change_ms=None,
                settle_ms=1800,
            )
        ],
    )
    view = build_characterization_view(run, header_label="x")
    assert view.rows[0].response == UNKNOWN_TEXT
    assert view.response_latency == "", "no measurement means no summary line at all"


# ── §2: cooling-device assembly ──────────────────────────────────────────────


def _device(**kw) -> CoolingDevice:
    base = {
        "id": "aio0",
        "name": "AIO Cooling System",
        "kind": "aio_liquid",
        "pump_member": "hwmon:nct6799:isa-0a20:pwm5:AIO_PUMP",
        "radiator_members": ["hwmon:nct6799:isa-0a20:pwm1:CPU_FAN"],
        "preferred_sensor": "cpu:pkg",
        "coolant_telemetry": "unavailable",
    }
    base.update(kw)
    return CoolingDevice(**base)


def test_topology_shows_pump_radiator_and_sensor_with_live_values():
    pump, fan = _pump_header(), _fan_header()
    view = build_cooling_device_view(
        _device(),
        headers=[pump, fan],
        capabilities=_caps(),
        sensor_labels={"cpu:pkg": "CPU Package"},
        readings=[_reading(pump.id, rpm=1284), _reading(fan.id, rpm=1146)],
        sensor_values={"cpu:pkg": 54.0},
    )
    assert view.pump is not None
    assert view.pump.rpm_text == "1,284 RPM"
    assert view.radiators[0].rpm_text == "1,146 RPM"
    assert view.sensor_label == "CPU Package"
    assert view.sensor_temp_text == "54°C"


def test_member_rows_keep_the_two_pwm_axes_apart():
    pump = _pump_header()
    view = build_cooling_device_view(
        _device(),
        headers=[pump],
        capabilities=_caps(),
        readings=[_reading(pump.id, pwm_commanded_pct=45, pwm_readback_pct=30)],
    )
    assert view.pump.requested_text == "45%"
    assert view.pump.readback_text == "30%"


def test_missing_coolant_telemetry_is_not_an_error():
    # Both members present: a MISSING member is a genuine warning, and passing
    # only the pump would make this test pass for the wrong reason.
    view = build_cooling_device_view(
        _device(), headers=[_pump_header(), _fan_header()], capabilities=_caps()
    )
    assert view.coolant_available is False
    assert view.state == "ok", "a motherboard AIO without coolant telemetry is normal"
    assert "error" not in view.coolant_note.lower()
    assert "fail" not in view.coolant_note.lower()


def test_pump_strategy_is_derived_from_the_curve():
    class _Curve:
        def __init__(self, value, sensor=""):
            self.type = type("T", (), {"value": value})()
            self.sensor_id = sensor

    class _Control:
        def __init__(self, mode, curve_id):
            self.mode = type("M", (), {"value": mode})()
            self.curve_id = curve_id
            self.members = [type("Mem", (), {"member_id": "pump"})()]

    class _Profile:
        def __init__(self, curve, mode="curve"):
            self.controls = [_Control(mode, "c1")]
            self._curve = curve

        def get_curve(self, cid):
            return self._curve if cid == "c1" else None

    graph = _Profile(_Curve("graph", "cpu:pkg"))
    assert pump_strategy_text("pump", graph, sensor_labels={"cpu:pkg": "CPU Package"}) == (
        "Automatic · CPU Package"
    )
    assert pump_strategy_text("pump", _Profile(_Curve("flat"))) == "Fixed"
    assert pump_strategy_text("pump", _Profile(_Curve("graph"), mode="manual")) == "Fixed"
    # Nothing drives it, and an unresolvable curve is skipped by the daemon
    # (273-i) — claiming a strategy there would be a lie.
    assert pump_strategy_text("pump", None) == NOT_CONTROLLED_TEXT
    assert pump_strategy_text("other", graph) == NOT_CONTROLLED_TEXT


# ── §20: empty, partial and unsupported states ───────────────────────────────


def test_writable_but_no_tach_still_offers_characterisation():
    """§11/§20: degraded, not blocked — the sweep still proves two of its three
    verdicts (command acceptance and readback)."""
    view = build_header_inspector_view(_fan_header(rpm_available=False), capabilities=_caps())
    assert view.can_test is True
    assert view.can_characterize is True


def test_read_only_header_disables_active_tests_with_a_reason():
    view = build_header_inspector_view(_fan_header(is_writable=False), capabilities=_caps())
    assert view.can_test is False
    assert view.test_disabled_reason, "a disabled action must explain itself (§11)"
    assert view.can_characterize is False
    assert view.characterize_disabled_reason


def test_characterisation_hidden_when_the_daemon_lacks_the_capability():
    view = build_header_inspector_view(
        _fan_header(), capabilities=_caps(pwm_characterization=False)
    )
    assert view.can_characterize is False
    assert "does not support" in view.characterize_disabled_reason


def test_pumps_are_listed_first():
    views = build_header_inspector_views([_fan_header(), _pump_header()], capabilities=_caps())
    assert views[0].pump_protected is True


# ── §15: validation never infers PASS ────────────────────────────────────────


def test_not_tested_and_unavailable_render_neutrally_never_as_errors():
    session = ValidationSession(
        session_id="s1",
        state="completed",
        findings=[
            ValidationFinding(id="daemon_restart_recovery", state="not_tested"),
            ValidationFinding(id="coolant_telemetry", state="unavailable"),
        ],
    )
    view = build_validation_session_view(session)
    tones = {f.finding_id: f.tone for f in view.findings}
    assert tones["daemon_restart_recovery"] not in ("crit", "critical", "warn", "warning")
    assert tones["coolant_telemetry"] not in ("crit", "critical", "warn", "warning")
    labels = {f.finding_id: f.state_label.upper() for f in view.findings}
    assert "PASS" not in labels["daemon_restart_recovery"]


def test_an_unrecognised_finding_renders_rather_than_disappearing():
    """The 273-i rule: a newer daemon must not make a result vanish."""
    session = ValidationSession(
        session_id="s1",
        state="completed",
        findings=[ValidationFinding(id="some_future_check", state="some_future_state")],
    )
    view = build_validation_session_view(session)
    assert len(view.findings) == 1
    assert view.findings[0].label
    assert view.findings[0].state_label


# ── verify wording is shared, not re-derived ─────────────────────────────────


def test_verify_view_is_the_single_source_of_result_wording():
    from control_ofc.api.models import HwmonVerifyResult, HwmonVerifyState

    result = HwmonVerifyResult(
        header_id="h",
        result="pwm_enable_reverted",
        initial_state=HwmonVerifyState(rpm=800),
        final_state=HwmonVerifyState(rpm=800),
    )
    view = build_verify_result_view(result)
    assert view.chip_class == "CriticalChip"
    assert "BIOS/EC" in view.summary
    assert view.verdict == "FAIL"


def test_verify_view_renders_an_unrecognised_token():
    from control_ofc.api.models import HwmonVerifyResult

    view = build_verify_result_view(HwmonVerifyResult(header_id="h", result="future_token"))
    assert "future_token" in view.text


# ── call-site tests: the buttons must invoke the EXISTING implementation ─────


def _page(qtbot, *, client=None, devices=None, profile_service=None):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_capabilities(_caps())
    pump, fan = _pump_header(), _fan_header()
    state.set_hwmon_headers([pump, fan])
    state.set_fans([_reading(pump.id), _reading(fan.id)])
    state.set_sensors([SensorReading(id="cpu:pkg", label="CPU Package", value_c=54.0)])
    if devices is not None:
        state.set_cooling_devices(CoolingDeviceInventory(cooling_devices=devices))
    page = HardwarePage(
        state=state,
        diagnostics_service=DiagnosticsService(state),
        client=client,
        profile_service=profile_service,
    )
    qtbot.addWidget(page)
    return page, state


def test_header_cards_render_for_every_discovered_header(qtbot):
    page, _ = _page(qtbot)
    cards = page.findChildren(PwmHeaderCard)
    assert len(cards) == 2
    assert {c.header_id() for c in cards} == {_pump_header().id, _fan_header().id}


def test_test_control_button_invokes_the_existing_verify_worker(qtbot):
    """`.click()`, not `_handler()` — the connection is what is most likely
    broken, and calling the handler directly would skip it."""
    page, _ = _page(qtbot)
    calls: list[str] = []
    page._run_pwm_verify = lambda header_id: calls.append(header_id)  # type: ignore[method-assign]
    # Rebuild so the freshly created card connects to the patched method.
    page._header_cards.clear()
    page._refresh_cooling_section()
    card = next(c for c in page.findChildren(PwmHeaderCard) if c.header_id() == _pump_header().id)
    button = card.findChild(QPushButton, f"HeaderCard_Btn_test_{_slug(_pump_header().id)}")
    assert button is not None
    button.click()
    assert calls == [_pump_header().id]


def test_characterise_button_invokes_the_existing_dialog_path(qtbot):
    page, _ = _page(qtbot)
    calls: list[str] = []
    page._open_characterization = lambda header_id: calls.append(header_id)  # type: ignore[method-assign]
    page._header_cards.clear()
    page._refresh_cooling_section()
    card = next(c for c in page.findChildren(PwmHeaderCard) if c.header_id() == _pump_header().id)
    button = card.findChild(QPushButton, f"HeaderCard_Btn_characterize_{_slug(_pump_header().id)}")
    assert button is not None
    button.click()
    assert calls == [_pump_header().id]


def _slug(header_id: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in header_id)


def test_pump_strategy_reaches_the_card_from_the_real_profile_service(qtbot):
    """The call-site test whose absence let a P1 ship green.

    `pump_strategy_text` was unit-tested thoroughly and the page fed it
    `getattr(state, "active_profile", None)` — an attribute `AppState` does not
    have — so every user saw "Not controlled" while those unit tests passed.
    This asserts the RENDERED card, which is the only thing that would have
    caught it. CLAUDE.md: "extracting a rule into a testable function does NOT
    test the call site."
    """
    from control_ofc.services.profile_service import (
        ControlMember,
        CurveConfig,
        CurveType,
        LogicalControl,
        Profile,
        ProfileService,
    )

    pump = _pump_header()
    curve = CurveConfig(id="c1", name="Pump", type=CurveType.GRAPH, sensor_id="cpu:pkg")
    profile = Profile(
        id="p1",
        name="Test",
        curves=[curve],
        controls=[
            LogicalControl(
                id="ctl1",
                name="Pump",
                curve_id="c1",
                members=[ControlMember(source="hwmon", member_id=pump.id)],
            )
        ],
    )
    svc = ProfileService()
    svc._profiles = {"p1": profile}
    svc._active_id = "p1"

    page, _ = _page(qtbot, devices=[_device()], profile_service=svc)
    card = page._device_cards["aio0"]
    strategy = card.findChild(QLabel, "CoolingDeviceCard_Fact_Pump_Strategy")
    assert strategy is not None, "the card must render a Pump Strategy row"
    assert strategy.text().startswith("Automatic"), (
        f"a graph curve driving the pump is Automatic, not {strategy.text()!r}"
    )
    assert "CPU Package" in strategy.text(), "and it names the curve's own sensor"


def test_pump_strategy_is_not_controlled_without_a_profile_service(qtbot):
    """The honest degradation — and the precondition that makes the test above
    meaningful rather than passing for any input."""
    page, _ = _page(qtbot, devices=[_device()])
    card = page._device_cards["aio0"]
    strategy = card.findChild(QLabel, "CoolingDeviceCard_Fact_Pump_Strategy")
    assert strategy is not None
    assert strategy.text() == NOT_CONTROLLED_TEXT


def test_repeated_polls_do_not_grow_the_card(qtbot):
    """Bound the thing that grows, and bound it at ZERO.

    `set_view` runs once per second for the life of the session. An earlier
    version rebuilt every row widget each tick: 9.3 ms per tick for eight cards
    and ~14,600 transient widgets per 30 s, for text that had not changed.
    Nothing leaked, but CLAUDE.md's lesson from DEC-286/287 is that a cost which
    scales with live widget population is one this project has already paid for
    twice — so assert zero growth, not "not much".
    """
    from PySide6.QtCore import QCoreApplication, QEvent, QObject
    from PySide6.QtWidgets import QLayout

    pump = _pump_header()

    def render(rpm: int) -> None:
        view = build_header_inspector_view(
            pump,
            reading=_reading(pump.id, rpm=rpm),
            capabilities=_caps(),
        )
        card.set_view(view)

    card = PwmHeaderCard(
        build_header_inspector_view(pump, reading=_reading(pump.id), capabilities=_caps())
    )
    qtbot.addWidget(card)
    card.show()

    def census() -> tuple[int, int]:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return len(card.findChildren(QLayout)), len(card.findChildren(QObject))

    render(1200)
    before = census()
    for tick in range(50):
        render(1200 + tick)
    after = census()
    assert after == before, (
        f"50 poll ticks grew the card from {before} to {after} "
        "(layouts, objects) — set_view must update in place"
    )
    # Precondition: the values really did change, so this is not passing because
    # nothing was re-rendered at all.
    rpm_row = card.findChild(QLabel, f"Row_Value_{_slug(pump.id)}_live_0")
    assert rpm_row is not None
    assert rpm_row.text() == "1,249 RPM", "the last tick's value must be on screen"


def test_hardware_page_is_usable_with_no_cooling_hardware(qtbot):
    """§20: a concise empty state, and readiness/Super-I/O stay available."""
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_capabilities(_caps())
    page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state))
    qtbot.addWidget(page)
    # Both, not either: production always sets text AND visibility together, so
    # an `or` would pass on a state where the message exists but never shows.
    page.show()
    qtbot.waitExposed(page)
    assert page._cooling_empty.text()
    assert page._cooling_empty.isVisible()
    assert page.findChild(QPushButton, "Hardware_Btn_refresh") is not None
    assert page._superio_card is not None


def test_pump_without_topology_is_offered_configuration_not_an_error(qtbot):
    page, _ = _page(qtbot)
    assert page._device_cards == {}
    assert page.findChildren(PwmHeaderCard), "headers still render independently (§1)"
    text = page._cooling_empty.text().lower()
    assert "configure" in text
    for word in ("error", "failed", "broken"):
        assert word not in text


def test_device_card_renders_when_topology_exists(qtbot):
    page, _ = _page(qtbot, devices=[_device()])
    assert set(page._device_cards) == {"aio0"}


def test_validation_actions_disabled_without_a_configured_device(qtbot):
    page, _ = _page(qtbot)
    assert page._validation_btn.isEnabled() is False
    assert page._validation_btn.toolTip(), "a disabled action explains itself (§11)"


def test_validation_actions_disabled_on_an_older_daemon(qtbot):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_capabilities(_caps(validation_sessions=False))
    state.set_cooling_devices(CoolingDeviceInventory(cooling_devices=[_device()]))
    page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state))
    qtbot.addWidget(page)
    assert page._validation_btn.isEnabled() is False
    assert "does not support" in page._validation_btn.toolTip()


def test_validation_actions_enabled_when_supported_and_configured(qtbot):
    page, _ = _page(qtbot, devices=[_device()])
    assert page._validation_btn.isEnabled() is True
    assert page._lifecycle_btn.isEnabled() is True


# ── one dialog serves both session kinds ─────────────────────────────────────


def test_one_dialog_serves_validation_and_lifecycle(qtbot):
    """Phase 5 Decision 8 made them one engine; two dialogs would be the
    duplication §21 forbids."""
    lifecycle = ValidationSessionDialog("aio0", "AIO", kind=VALIDATION_KIND_LIFECYCLE, members=[])
    qtbot.addWidget(lifecycle)
    # Assert the TITLE only. `_kind` is set from the argument this test just
    # passed in, so `or _kind == ...` was trivially true and the assertion would
    # have passed with the title rendering completely broken.
    assert "Lifecycle" in lifecycle.windowTitle()
    assert lifecycle.findChild(QPushButton, "Validation_Btn_start") is not None

    validation = ValidationSessionDialog("aio0", "AIO", members=[])
    qtbot.addWidget(validation)
    assert "Validation" in validation.windowTitle()
    # Same dialog class, so the same start button exists for both kinds.
    assert validation.findChild(QPushButton, "Validation_Btn_start") is not None


def test_dialog_offers_no_actions_before_a_session_exists(qtbot):
    dialog = ValidationSessionDialog("aio0", "AIO", members=[])
    qtbot.addWidget(dialog)
    assert dialog._start_btn.isEnabled() is True
    assert dialog._stop_btn.isEnabled() is False
    assert dialog._mark_btn.isEnabled() is False
    # Nothing to export from a session that never started — an empty file would
    # read as a failed export.
    assert dialog._csv_btn.isEnabled() is False


# ── existing sections keep working ───────────────────────────────────────────


def test_readiness_and_superio_sections_still_exist(qtbot):
    page, _ = _page(qtbot)
    assert page.findChild(type(page._verdict_pill), "Hardware_Pill_verdict") is not None
    assert page._superio_card is not None
    assert page._cooling_card is not None
    assert page._diagnostics_card is not None


def test_new_sections_cannot_widen_the_application(qtbot):
    """DEC-315 made the app's minimum window width track its WIDEST page.

    A dense new section that reported a large minimum would therefore move every
    user's window, on every page. Asserted as a relationship against the page's
    own scroll area rather than a pixel literal — CI's font set differs from any
    developer's, so a measured constant here would be unportable (the trap that
    reddened all three CI legs once already).
    """
    long_headers = [
        HwmonHeader(
            id=f"hwmon:nct6799:isa-0a20:pwm{i}:A_VERY_LONG_HEADER_LABEL_{i}",
            label=f"A_VERY_LONG_HEADER_LABEL_{i}",
            chip_name="nct6799",
            pwm_index=i,
            rpm_available=True,
            is_writable=True,
            supports_enable=True,
        )
        for i in range(1, 9)
    ]
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_capabilities(_caps())
    state.set_hwmon_headers(long_headers)
    state.set_fans([_reading(h.id) for h in long_headers])
    state.set_cooling_devices(CoolingDeviceInventory(cooling_devices=[_device()]))
    page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state))
    qtbot.addWidget(page)
    page.show()
    qtbot.waitExposed(page)

    # Precondition: the cards really were built, so this is not passing because
    # the section is empty.
    assert page.findChildren(PwmHeaderCard), "the header cards must exist"

    # The page must demand no more width than the scroll area that contains it —
    # a QScrollArea scrolls, so content width is never a window constraint.
    assert page.minimumSizeHint().width() <= page._scroll.minimumSizeHint().width() + 1

    # And it must fit a window narrower than the app's current 1200px design
    # target without forcing it wider.
    page.resize(900, 800)
    qtbot.wait(1)
    assert page.minimumSizeHint().width() <= 900


def test_no_hardcoded_font_size_in_new_widgets():
    """Theme adherence: sizes come from the theme's multipliers, never a literal."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "control_ofc"
    for name in (
        "ui/widgets/pwm_header_card.py",
        "ui/widgets/cooling_device_card.py",
        "ui/widgets/validation_session_dialog.py",
    ):
        text = (root / name).read_text()
        assert "font-size" not in text, f"{name} hardcodes a font size"
