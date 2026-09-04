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
    VALIDATION_KIND_VALIDATION,
    VALIDATION_STATE_RECORDING,
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
    ValidationMemberRole,
    ValidationMemberSample,
    ValidationMetadata,
    ValidationSample,
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


def _session(**kw) -> ValidationSession:
    """A populated session, so `_render` has members and findings to draw."""
    base = {
        "session_id": "vs-1",
        "state": VALIDATION_STATE_RECORDING,
        # `build_member_rows` reads the metadata roster, not the sample ids: a
        # member that reported nothing must still render a row saying so.
        "metadata": ValidationMetadata(
            cooling_device_id="aio0",
            device_name="AIO Cooling System",
            pump_member=_pump_header().id,
            members=[
                ValidationMemberRole(
                    member_id=_pump_header().id,
                    label="AIO_PUMP",
                    role="pump",
                    member_kind="pump",
                    pump_protected=True,
                )
            ],
        ),
        "started_unix_ms": 1_700_000_000_000,
        "requested_diagnostics": ["pwm_verify"],
        "sweep_members": [_pump_header().id],
        "samples": [
            ValidationSample(
                elapsed_ms=1000,
                unix_ms=1_700_000_001_000,
                temperature_c=54.0,
                temperature_sensor="cpu:pkg",
                thermal_state="normal",
                members=[
                    ValidationMemberSample(
                        member_id=_pump_header().id,
                        role="pump",
                        requested_pct=45,
                        readback_pct=44,
                        rpm=2100,
                    )
                ],
            )
        ],
        "findings": [
            ValidationFinding(id="pump_never_stopped", state="pass", detail="held above 30%")
        ],
    }
    base.update(kw)
    return ValidationSession(**base)


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


# ── Call sites: the page's interaction layer (register row `AUD3-a`) ─────────
#
# `test_characterise_button_invokes_the_existing_dialog_path` above stops at the
# page boundary: it replaces `_open_characterization` with a recorder, so the
# method's own body — the union predicate, the three signal connections, the
# dialog lifecycle — never ran. Measured, `hardware_page.py` sat at 71% with
# `_open_characterization` and `_open_validation` wholly missing, which made
#
#     is_pump=header_is_pump_protected(header, self._capabilities())
#
# replaceable by `is_pump=False` with the entire suite green — and every user
# with a pump would silently lose the warning that their pump is about to be
# swept. The tests below execute those bodies for real.


def _stub_workers(page):
    """Neutralise the QThread workers; the call sites under test sit above them.

    Each `_ensure_*_worker` builds a real `DaemonClient` against a socket path
    and starts a thread. What these tests are about is the code *after* that
    check — the dialog construction and its connections — so the gate is
    satisfied without any I/O. The hop this skips is covered on its own by
    `TestWorkerRequestSignalsReachTheirWorker` below, which starts the real
    thread and injects a fake client into it.
    """
    page._ensure_char_worker = lambda: True  # type: ignore[method-assign]
    page._ensure_validation_worker = lambda: True  # type: ignore[method-assign]


def _no_exec(monkeypatch, cls):
    """Patch a dialog's blocking `exec()` and hand back what the page built."""
    built = []

    def fake_exec(dialog):
        built.append(dialog)
        return 0

    monkeypatch.setattr(cls, "exec", fake_exec, raising=True)
    return built


class TestCharacterizationCallSite:
    """[SAFETY] DEC-312: the dialog's pump copy is decided by the UNION."""

    def test_the_card_button_opens_the_dialog_with_the_union_predicate(self, qtbot, monkeypatch):
        """A header the user downgraded to `chassis_fan` that the hardware
        labels PUMP is still pump-protected daemon-side.

        The assertion is a RELATIONSHIP against the predicate rather than the
        literal `True`, so it cannot be satisfied by a call site that reads the
        wire `role` — which for this fixture says `chassis_fan`.
        """
        from control_ofc.services.pump_protection import header_is_pump_protected
        from control_ofc.ui.widgets.pwm_characterization_dialog import (
            PwmCharacterizationDialog,
        )

        downgraded = _pump_header(role="chassis_fan", role_source="user_assigned")
        page, state = _page(qtbot)
        state.set_hwmon_headers([downgraded, _fan_header()])
        page._header_cards.clear()
        page._refresh_cooling_section()
        _stub_workers(page)
        built = _no_exec(monkeypatch, PwmCharacterizationDialog)

        card = next(c for c in page.findChildren(PwmHeaderCard) if c.header_id() == downgraded.id)
        button = card.findChild(QPushButton, f"HeaderCard_Btn_characterize_{_slug(downgraded.id)}")
        button.click()

        assert len(built) == 1, "the real button must reach the real dialog"
        expected = header_is_pump_protected(downgraded, state.capabilities)
        assert expected is True, "the fixture must exercise the union, not agree with `role`"
        assert built[0]._is_pump == expected
        assert "never stopped" in built[0]._warnings.text()

    def test_a_chassis_header_opens_without_the_pump_copy(self, qtbot, monkeypatch):
        """The opposite branch, so the assertion above is not vacuously true."""
        from control_ofc.services.pump_protection import header_is_pump_protected
        from control_ofc.ui.widgets.pwm_characterization_dialog import (
            PwmCharacterizationDialog,
        )

        page, state = _page(qtbot)
        _stub_workers(page)
        built = _no_exec(monkeypatch, PwmCharacterizationDialog)
        page._open_characterization(_fan_header().id)

        assert built[0]._is_pump is False
        assert header_is_pump_protected(_fan_header(), state.capabilities) is False
        assert "never stopped" not in built[0]._warnings.text()

    def test_the_dialogs_three_signals_reach_the_pages_worker_requests(self, qtbot, monkeypatch):
        from control_ofc.ui.widgets.pwm_characterization_dialog import (
            PwmCharacterizationDialog,
        )

        page, _ = _page(qtbot)
        _stub_workers(page)
        built = _no_exec(monkeypatch, PwmCharacterizationDialog)
        page._open_characterization(_pump_header().id)
        dialog = built[0]

        seen: list[str] = []
        page._char_start_request.connect(lambda *_a: seen.append("start"))
        page._char_poll_request.connect(lambda: seen.append("poll"))
        page._char_cancel_request.connect(lambda: seen.append("cancel"))

        dialog.start_requested.emit(_pump_header().id, None, None)
        dialog.poll_requested.emit()
        dialog.cancel_requested.emit()
        assert seen == ["start", "poll", "cancel"]

    def test_an_unknown_header_opens_nothing(self, qtbot, monkeypatch):
        from control_ofc.ui.widgets.pwm_characterization_dialog import (
            PwmCharacterizationDialog,
        )

        page, _ = _page(qtbot)
        _stub_workers(page)
        built = _no_exec(monkeypatch, PwmCharacterizationDialog)
        page._open_characterization("hwmon:nope:dev:pwm9:GONE")
        page._open_characterization("")
        assert built == []


class TestValidationCallSite:
    """The seven `.connect()` calls in `_open_validation`, driven for real.

    Dropping `stop_requested` means a recording session cannot be stopped; with
    the body unexecuted, nothing noticed.
    """

    def _open(self, qtbot, monkeypatch, *, lifecycle=False, device_id=""):
        page, state = _page(qtbot, devices=[_device()])
        _stub_workers(page)
        built = _no_exec(monkeypatch, ValidationSessionDialog)
        page._open_validation(lifecycle=lifecycle, device_id=device_id)
        return page, state, built

    def test_every_dialog_signal_reaches_its_worker_request(self, qtbot, monkeypatch):
        page, _, built = self._open(qtbot, monkeypatch)
        assert len(built) == 1
        dialog = built[0]

        seen: list[tuple] = []
        page._validation_start_request.connect(lambda *a: seen.append(("start", a)))
        page._validation_poll_request.connect(lambda: seen.append(("poll", ())))
        page._validation_stop_request.connect(lambda: seen.append(("stop", ())))
        page._validation_cancel_request.connect(lambda: seen.append(("cancel", ())))
        page._validation_marker_request.connect(lambda *a: seen.append(("marker", a)))
        page._validation_measurement_request.connect(lambda *a: seen.append(("measure", a)))

        dialog.start_requested.emit("aio0", "validation", ["pwm_verify"], ["m1"], {"note": "n"})
        dialog.poll_requested.emit()
        dialog.stop_requested.emit()
        dialog.cancel_requested.emit()
        dialog.marker_requested.emit("resumed", "m1")
        dialog.measurement_requested.emit("supply_voltage", 12.1, "V", "m1", "note")

        assert [name for name, _ in seen] == [
            "start",
            "poll",
            "stop",
            "cancel",
            "marker",
            "measure",
        ]
        assert dict(seen)["start"] == ("aio0", "validation", ["pwm_verify"], ["m1"], {"note": "n"})
        assert dict(seen)["measure"] == ("supply_voltage", 12.1, "V", "m1", "note")

    def test_opening_polls_immediately_so_an_existing_session_is_shown(self, qtbot, monkeypatch):
        """A session may still be recording from an earlier visit; opening the
        dialog must not look like a fresh start."""
        page, _ = _page(qtbot, devices=[_device()])
        _stub_workers(page)
        polls: list[int] = []
        page._validation_poll_request.connect(lambda: polls.append(1))
        _no_exec(monkeypatch, ValidationSessionDialog)
        page._open_validation(lifecycle=False)
        assert polls == [1]

    def test_the_export_signal_is_wired_to_the_pages_serializer(self, qtbot, monkeypatch):
        page, _, built = self._open(qtbot, monkeypatch)
        formats: list[str] = []
        page._export_session = lambda fmt: formats.append(fmt)  # type: ignore[method-assign]
        # Re-open so the fresh dialog connects to the patched method.
        built.clear()
        page._open_validation(lifecycle=False)
        built[0].export_requested.emit("csv")
        assert formats == ["csv"]

    def test_the_lifecycle_button_opens_the_lifecycle_kind(self, qtbot, monkeypatch):
        page, _ = _page(qtbot, devices=[_device()])
        _stub_workers(page)
        built = _no_exec(monkeypatch, ValidationSessionDialog)
        page._lifecycle_btn.click()
        assert built[0]._kind == VALIDATION_KIND_LIFECYCLE
        assert "Lifecycle" in built[0].windowTitle()

    def test_the_validation_button_opens_the_validation_kind(self, qtbot, monkeypatch):
        page, _ = _page(qtbot, devices=[_device()])
        _stub_workers(page)
        built = _no_exec(monkeypatch, ValidationSessionDialog)
        page._validation_btn.click()
        assert built[0]._kind != VALIDATION_KIND_LIFECYCLE

    def test_no_configured_device_explains_itself_instead_of_opening(self, qtbot, monkeypatch):
        page, _ = _page(qtbot)  # no cooling devices
        _stub_workers(page)
        built = _no_exec(monkeypatch, ValidationSessionDialog)
        page._open_validation(lifecycle=False)
        assert built == []
        assert "Configure a cooling device first" in page._diag_result.text()

    def test_a_card_targets_its_own_device_not_the_first_one(self, qtbot, monkeypatch):
        """`_resolve_device` honours the id the card emitted. The GUI writes one
        device today, so `devices[0]` would coincide — and be wrong by
        construction the moment a second exists."""
        second = _device(id="aio1", name="Second Loop")
        page, _ = _page(qtbot, devices=[_device(), second])
        _stub_workers(page)
        built = _no_exec(monkeypatch, ValidationSessionDialog)
        page._open_validation(lifecycle=False, device_id="aio1")
        assert built[0]._device_id == "aio1"
        assert "Second Loop" in built[0]._device_lbl.text()

    def test_the_member_pickers_are_populated_from_the_device(self, qtbot, monkeypatch):
        _page, _state, built = self._open(qtbot, monkeypatch)
        member_ids = {mid for mid, _label in built[0]._members}
        assert member_ids == {_pump_header().id, _fan_header().id}

    def test_worker_results_are_routed_to_the_open_dialog(self, qtbot, monkeypatch):
        """`_on_validation_update`/`_error`/`_action_ok` are the worker's only
        route onto the screen and were all uncovered."""
        page, _, built = self._open(qtbot, monkeypatch)
        dialog = built[0]
        page._validation_dialog = dialog  # `exec()` returned, so re-attach it

        page._on_validation_update(_session(state="recording"))
        assert dialog.session() is not None
        assert dialog._stop_btn.isEnabled(), "a recording session must be stoppable"

        page._on_validation_error("unavailable", "Cannot start while hot")
        assert dialog._status_lbl.text() == "Cannot start while hot"
        page._on_validation_error("error", "boom")
        assert "Session error" in dialog._status_lbl.text()

        page._on_validation_action_ok("Event marked.")
        assert dialog._status_lbl.text() == "Event marked."

    def test_start_begins_polling_as_well_as_requesting(self, qtbot, monkeypatch):
        page, _, built = self._open(qtbot, monkeypatch)
        page._validation_dialog = built[0]
        built[0].stop_polling()
        requested: list[tuple] = []
        page._validation_start_request.connect(lambda *a: requested.append(a))
        page._on_validation_start("aio0", "validation", [], [], {})
        assert requested == [("aio0", "validation", [], [], {})]
        assert built[0]._timer.isActive(), "a started session must be polled for"


class TestExportCallSite:
    def test_export_writes_the_chosen_format_through_the_phase5_serializers(
        self, qtbot, monkeypatch, tmp_path
    ):
        import PySide6.QtWidgets as QtW

        page, _ = _page(qtbot, devices=[_device()])
        _stub_workers(page)
        built = _no_exec(monkeypatch, ValidationSessionDialog)
        page._open_validation(lifecycle=False)
        page._validation_dialog = built[0]
        built[0].apply_session(_session(state="completed"))

        for fmt, suffix, probe in (
            ("csv", ".csv", "elapsed_ms"),
            ("json", ".json", '"session_id"'),
        ):
            target = tmp_path / f"out{suffix}"
            # Bound as a default argument: a bare closure over `target` would be
            # a late-binding read, which is only accidentally correct here.
            monkeypatch.setattr(
                QtW.QFileDialog,
                "getSaveFileName",
                lambda *a, _t=str(target), **k: (_t, ""),
            )
            page._export_session(fmt)
            assert target.exists(), f"{fmt} export must write the file"
            assert probe in target.read_text()
            assert "Exported to" in built[0]._status_lbl.text()

    def test_a_cancelled_file_dialog_writes_nothing(self, qtbot, monkeypatch, tmp_path):
        import PySide6.QtWidgets as QtW

        page, _ = _page(qtbot, devices=[_device()])
        _stub_workers(page)
        built = _no_exec(monkeypatch, ValidationSessionDialog)
        page._open_validation(lifecycle=False)
        page._validation_dialog = built[0]
        built[0].apply_session(_session(state="completed"))
        monkeypatch.setattr(QtW.QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
        page._export_session("json")
        assert list(tmp_path.iterdir()) == []

    def test_an_unwritable_path_is_reported_rather_than_swallowed(
        self, qtbot, monkeypatch, tmp_path
    ):
        import PySide6.QtWidgets as QtW

        page, _ = _page(qtbot, devices=[_device()])
        _stub_workers(page)
        built = _no_exec(monkeypatch, ValidationSessionDialog)
        page._open_validation(lifecycle=False)
        page._validation_dialog = built[0]
        built[0].apply_session(_session(state="completed"))
        # A path that is itself a directory: `atomic_write` creates missing
        # parents, so a non-existent directory is NOT an error here.
        bad = tmp_path / "a-directory.json"
        bad.mkdir()
        monkeypatch.setattr(QtW.QFileDialog, "getSaveFileName", lambda *a, **k: (str(bad), ""))
        page._export_session("json")
        assert "Could not write" in built[0]._status_lbl.text()


class TestForgetDeviceCallSite:
    """Metadata only: no role, floor or fan speed changes (DEC-316)."""

    class _Client:
        socket_path = "/nonexistent/control-ofc.sock"

        def __init__(self, *, after=None, raises=None):
            self.deleted: list[str] = []
            self._after = after if after is not None else CoolingDeviceInventory(cooling_devices=[])
            self._raises = raises

        def delete_cooling_device(self, device_id):
            if self._raises:
                raise self._raises
            self.deleted.append(device_id)

        def get_cooling_devices(self):
            return self._after

    def test_the_card_button_deletes_and_pushes_the_inventory_back_to_state(
        self, qtbot, monkeypatch
    ):
        from PySide6.QtWidgets import QMessageBox

        client = self._Client()
        page, state = _page(qtbot, client=client, devices=[_device()])
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        card = next(iter(page._device_cards.values()))
        card._btn_forget.click()

        assert client.deleted == ["aio0"], "the real button must reach the real delete"
        assert state.cooling_devices is not None
        assert list(state.cooling_devices.cooling_devices) == [], (
            "DEC-319: the inventory must reach AppState, or the Controls picker "
            "keeps reserving the forgotten device's fans"
        )
        assert "No fan settings changed" in page._diag_result.text()
        assert page._device_cards == {}

    def test_a_declined_confirmation_deletes_nothing(self, qtbot, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        client = self._Client()
        page, _ = _page(qtbot, client=client, devices=[_device()])
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        next(iter(page._device_cards.values()))._btn_forget.click()
        assert client.deleted == []

    def test_a_rejected_delete_keeps_the_card_and_says_so(self, qtbot):
        from control_ofc.api.errors import DaemonError

        client = self._Client(raises=DaemonError(code="internal_error", message="nope"))
        page, _ = _page(qtbot, client=client, devices=[_device()])
        page._forget_device("aio0")
        assert "Could not forget the device" in page._diag_result.text()
        assert "aio0" in page._device_cards, "a failed delete must not remove the card"

    def test_no_client_is_reported_not_silently_ignored(self, qtbot):
        page, _ = _page(qtbot, devices=[_device()])
        page._forget_device("aio0")
        assert "no daemon connection" in page._diag_result.text()


# ── The dialog's own interactive + rendering layer (row `AUD3-o`) ────────────
#
# Only two tests above touch `ValidationSessionDialog`, and both assert on a
# freshly-constructed widget: a title check and an enablement check. Nothing
# clicked a button, nothing called `apply_session()` with a populated session,
# and nothing asserted that closing stops the poll timer — so deleting
# `self._start_btn.clicked.connect(self._emit_start)` left the whole Phase 6
# suite green. `test_cancel_emits_and_start_emits_no_client_side_point_list` in
# `test_aio_mb_phase3.py` is the sibling doing this correctly, one file away.


class TestValidationDialogButtons:
    """`.click()`, not `_emit_*()` — the connection is the fragile part."""

    def _dialog(self, qtbot, **kw):
        kw.setdefault("members", [(_pump_header().id, "AIO_PUMP"), (_fan_header().id, "CPU_FAN")])
        dialog = ValidationSessionDialog("aio0", "AIO Cooling System", **kw)
        qtbot.addWidget(dialog)
        return dialog

    def test_start_assembles_the_whole_payload_from_the_form(self, qtbot):
        dialog = self._dialog(qtbot)
        seen: list[tuple] = []
        dialog.start_requested.connect(lambda *a: seen.append(a))

        dict(dialog._diag_boxes)["pwm_characterization"].setChecked(True)
        dialog._sweep_combo.setCurrentIndex(dialog._sweep_combo.findData(_fan_header().id))
        dialog._note_edit.setText("  bench run 3  ")
        dialog._start_btn.click()

        assert seen == [
            (
                "aio0",
                VALIDATION_KIND_VALIDATION,
                ["pwm_characterization"],
                [_fan_header().id],
                {"note": "bench run 3"},
            )
        ]

    def test_an_empty_note_sends_no_metadata_key(self, qtbot):
        dialog = self._dialog(qtbot)
        seen: list[tuple] = []
        dialog.start_requested.connect(lambda *a: seen.append(a))
        dialog._start_btn.click()
        assert seen[0][2] == [], "no diagnostic ticked means none requested"
        assert seen[0][3] == [], "the default sweep member is the daemon's choice, not ours"
        assert seen[0][4] == {}, "a blank note must not become an empty metadata value"

    def test_stop_cancel_and_export_buttons_emit(self, qtbot):
        dialog = self._dialog(qtbot)
        seen: list[str] = []
        dialog.stop_requested.connect(lambda: seen.append("stop"))
        dialog.cancel_requested.connect(lambda: seen.append("cancel"))
        dialog.export_requested.connect(lambda fmt: seen.append(f"export:{fmt}"))
        # Enable them the way a recording session does, rather than by hand.
        dialog.apply_session(_session(state=VALIDATION_STATE_RECORDING))
        dialog._stop_btn.click()
        dialog._cancel_btn.click()
        dialog._csv_btn.click()
        dialog._json_btn.click()
        assert seen == ["stop", "cancel", "export:csv", "export:json"]

    def test_mark_event_carries_the_note_field(self, qtbot):
        dialog = self._dialog(qtbot)
        seen: list[tuple] = []
        dialog.marker_requested.connect(lambda *a: seen.append(a))
        dialog.apply_session(_session(state=VALIDATION_STATE_RECORDING))
        dialog._note_edit.setText("resumed from suspend")
        dialog._mark_btn.click()
        assert seen == [("resumed from suspend", "")]

    def test_a_measurement_carries_kind_value_unit_member_and_folded_note(self, qtbot):
        """§17: the wire has no `instrument` field, so it is folded into the
        note. Untrusted free text either way — never read for control."""
        dialog = self._dialog(qtbot)
        seen: list[tuple] = []
        dialog.measurement_requested.connect(lambda *a: seen.append(a))
        dialog.apply_session(_session(state=VALIDATION_STATE_RECORDING))

        dialog._m_kind.setCurrentIndex(dialog._m_kind.findText("12 V supply voltage"))
        dialog._m_member.setCurrentIndex(dialog._m_member.findData(_pump_header().id))
        dialog._m_value.setValue(12.14)
        dialog._m_instrument.setText("Fluke 87V")
        dialog._m_note.setText("at the pump header")
        dialog._m_add.click()

        assert seen == [
            (
                "supply_voltage",
                12.14,
                "V",
                _pump_header().id,
                "Instrument: Fluke 87V — at the pump header",
            )
        ]

    def test_the_unit_follows_the_measurement_kind(self, qtbot):
        dialog = self._dialog(qtbot)
        dialog._m_kind.setCurrentIndex(dialog._m_kind.findText("PWM frequency"))
        assert dialog._m_unit.text() == "Hz"
        dialog._m_kind.setCurrentIndex(dialog._m_kind.findText("Device current"))
        assert dialog._m_unit.text() == "A"


class TestValidationDialogRendering:
    def _dialog(self, qtbot):
        dialog = ValidationSessionDialog("aio0", "AIO", members=[(_pump_header().id, "AIO_PUMP")])
        qtbot.addWidget(dialog)
        return dialog

    def test_a_populated_session_fills_both_tables(self, qtbot):
        dialog = self._dialog(qtbot)
        dialog.apply_session(_session())
        assert dialog._member_table.rowCount() == 1
        assert dialog._member_table.item(0, 0).text()
        assert dialog._findings_table.rowCount() == 1
        # The finding's WORDING is the client's (273-i); only its presence is
        # asserted here, so the view-model stays the single source of it.
        assert dialog._findings_table.item(0, 1).text()
        # `isVisibleTo`, not `isVisible`: the dialog is never shown in a headless
        # test, so `isVisible()` is False for every widget and asserting it would
        # pass with `setVisible(bool(view.members))` deleted — measured.
        assert dialog._member_table.isVisibleTo(dialog) is True
        assert dialog._findings_table.isVisibleTo(dialog) is True

    def test_tables_with_nothing_in_them_stay_hidden(self, qtbot):
        """The other half of `setVisible(bool(...))`, so the assertion above
        cannot be satisfied by a table that is simply always visible."""
        dialog = self._dialog(qtbot)
        dialog.apply_session(_session(metadata=ValidationMetadata(), findings=[]))
        assert dialog._member_table.isVisibleTo(dialog) is False
        assert dialog._findings_table.isVisibleTo(dialog) is False

    def test_requested_and_readback_stay_two_columns_in_the_rendered_table(self, qtbot):
        """§6, asserted where the two disagree — equal values would pass with
        both columns wired to the same field."""
        dialog = self._dialog(qtbot)
        dialog.apply_session(_session())
        headers = [
            dialog._member_table.horizontalHeaderItem(c).text()
            for c in range(dialog._member_table.columnCount())
        ]
        requested = dialog._member_table.item(0, headers.index("Requested")).text()
        readback = dialog._member_table.item(0, headers.index("Readback")).text()
        assert "45" in requested
        assert "44" in readback
        assert requested != readback

    def test_no_session_resets_to_the_ready_state(self, qtbot):
        dialog = self._dialog(qtbot)
        dialog.apply_session(_session())
        dialog.apply_session(None)
        assert dialog._status_lbl.text() == "Ready to start."
        assert dialog._member_table.isVisibleTo(dialog) is False
        assert dialog._findings_table.isVisibleTo(dialog) is False
        assert dialog._start_btn.isEnabled() is True

    def test_a_finished_session_stops_polling_and_still_allows_export(self, qtbot):
        dialog = self._dialog(qtbot)
        dialog.start_polling()
        dialog.apply_session(_session(state="completed"))
        assert dialog._timer.isActive() is False, "a finished session must stop the poll timer"
        assert dialog._stop_btn.isEnabled() is False
        assert dialog._mark_btn.isEnabled() is False
        assert dialog._csv_btn.isEnabled() is True, "finished evidence is still exportable"

    def test_a_recording_session_enables_the_live_actions_and_locks_the_options(self, qtbot):
        dialog = self._dialog(qtbot)
        dialog.apply_session(_session(state=VALIDATION_STATE_RECORDING))
        assert dialog._start_btn.isEnabled() is False
        assert dialog._stop_btn.isEnabled() is True
        assert dialog._mark_btn.isEnabled() is True
        assert dialog._m_add.isEnabled() is True
        assert dialog._options_section.isEnabled() is False, (
            "the session's own options must not change under a running session"
        )

    def test_a_soft_refusal_is_shown_verbatim_and_an_error_is_prefixed(self, qtbot):
        """The daemon declining to move a pump during a thermal event is
        protection working, not a failure."""
        dialog = self._dialog(qtbot)
        dialog.apply_error("unavailable", "Cannot start while hot: Tctl at 91.0°C")
        assert dialog._status_lbl.text() == "Cannot start while hot: Tctl at 91.0°C"
        dialog.apply_error("error", "boom")
        assert dialog._status_lbl.text() == "Session error: boom"

    def test_an_unrecognised_state_renders_rather_than_vanishing(self, qtbot):
        """273-i: a newer daemon must not make a session disappear."""
        dialog = self._dialog(qtbot)
        dialog.apply_session(_session(state="some_future_state"))
        assert dialog._state_pill.text()
        assert dialog._state_pill.accessibleName()

    def test_closing_stops_the_poll_timer_on_both_exit_paths(self, qtbot):
        for close in ("reject", "accept"):
            dialog = self._dialog(qtbot)
            dialog.start_polling()
            assert dialog._timer.isActive()
            getattr(dialog, close)()
            assert dialog._timer.isActive() is False, (
                f"{close}() must stop the 1 Hz poll, or it outlives the dialog"
            )

    def test_the_close_button_stops_polling_too(self, qtbot):
        dialog = self._dialog(qtbot)
        dialog.start_polling()
        dialog._close_btn.click()
        assert dialog._timer.isActive() is False

    def test_the_timer_emits_the_poll_signal(self, qtbot):
        dialog = self._dialog(qtbot)
        polls: list[int] = []
        dialog.poll_requested.connect(lambda: polls.append(1))
        dialog._timer.timeout.emit()
        assert polls == [1]


class TestValidationWorkerRefusalMapping:
    """`_ValidationWorker`'s six verbs and their shared error translation.

    Named in `AUD3-a`'s scope alongside the page: `diagnostics_workers.py` fell
    62% → 56% precisely because Phase 5/6 added this class and nothing drove it.
    The branch that matters is the same one the characterisation worker has — a
    soft safety refusal must reach the dialog as "unavailable" and be shown
    verbatim, because the daemon declining to disturb a pump during a thermal
    event is protection working, not a failure. `_run` is shared by all six
    verbs exactly so the two cannot drift on what counts as a refusal.
    """

    def _worker(self, *, raises=None, returns=None):
        from control_ofc.ui.pages.diagnostics_workers import _ValidationWorker

        worker = _ValidationWorker("/nonexistent/control-ofc.sock")
        calls: list[tuple] = []

        class _Client:
            def __getattr__(self, name):
                def call(*args, **kwargs):
                    calls.append((name, args, kwargs))
                    if raises is not None:
                        raise raises
                    return returns

                return call

        worker._client = _Client()  # `_ensure_client` returns this — no socket
        errors: list[tuple[str, str]] = []
        sessions: list[object] = []
        oks: list[str] = []
        worker.session_error.connect(lambda c, m: errors.append((c, m)))
        worker.session_updated.connect(sessions.append)
        worker.action_ok.connect(oks.append)
        return worker, calls, errors, sessions, oks

    def test_start_forwards_the_whole_payload_and_emits_the_session(self):
        session = _session()
        worker, calls, errors, sessions, _ = self._worker(returns=session)
        worker.do_start("aio0", "validation", ["pwm_verify"], ["m1"], {"note": "n"})
        assert calls[0][0] == "start_validation_session"
        assert calls[0][1] == ("aio0",)
        assert calls[0][2] == {
            "kind": "validation",
            "diagnostics": ["pwm_verify"],
            "sweep_members": ["m1"],
            "metadata": {"note": "n"},
        }
        assert sessions == [session]
        assert errors == []

    def test_empty_optionals_become_none_rather_than_empty_containers(self):
        """The daemon's defaults must be reachable: an explicit `[]` asks for
        *no* diagnostics, which is not the same request as "you choose"."""
        worker, calls, _e, _s, _o = self._worker(returns=None)
        worker.do_start("aio0", "", [], [], {})
        assert calls[0][2] == {
            "kind": None,
            "diagnostics": None,
            "sweep_members": None,
            "metadata": None,
        }

    def test_poll_stop_and_cancel_each_emit_their_session_snapshot(self):
        for verb, method in (
            ("do_poll", "validation_session"),
            ("do_stop", "stop_validation_session"),
            ("do_cancel", "cancel_validation_session"),
        ):
            session = _session()
            worker, calls, errors, sessions, _ = self._worker(returns=session)
            getattr(worker, verb)()
            assert calls[0][0] == method, f"{verb} must call {method}"
            assert sessions == [session]
            assert errors == []

    def test_a_poll_with_no_active_session_emits_none_not_an_error(self):
        """A 404 is `None` from the client: it means no session is recording,
        which the dialog renders as the finished state rather than an error the
        user cannot act on."""
        worker, _c, errors, sessions, _o = self._worker(returns=None)
        worker.do_poll()
        assert sessions == [None]
        assert errors == []

    def test_marker_and_measurement_acknowledge_without_waiting_for_a_poll(self):
        worker, calls, _e, _s, oks = self._worker()
        worker.do_marker("resumed", "m1")
        assert calls[0][0] == "add_validation_marker"
        assert calls[0][2] == {"member_id": "m1"}
        assert oks == ["Event marked."]

        worker, calls, _e, _s, oks = self._worker()
        worker.do_measurement("supply_voltage", 12.1, "V", "m1", "note")
        assert calls[0][0] == "add_validation_measurement"
        assert calls[0][1] == ("supply_voltage", 12.1)
        assert calls[0][2] == {"unit": "V", "member_id": "m1", "note": "note"}
        assert oks == ["External measurement recorded."]

    def test_a_thermal_abort_is_protection_and_is_shown_verbatim(self):
        from control_ofc.api.errors import DaemonError

        worker, _c, errors, _s, _o = self._worker(
            raises=DaemonError(
                code="thermal_abort", message="Cannot start while hot: Tctl at 91.0°C", status=409
            )
        )
        worker.do_start("aio0", "validation", [], [], {})
        assert errors == [("unavailable", "Cannot start while hot: Tctl at 91.0°C")]

    def test_a_retryable_validation_error_is_also_protection(self):
        """DEC-297: the ladder actively forcing is a refusal, not a fault."""
        from control_ofc.api.errors import DaemonError

        worker, _c, errors, _s, _o = self._worker(
            raises=DaemonError(
                code="validation_error",
                message="thermal safety is forcing fan output",
                retryable=True,
                status=400,
            )
        )
        worker.do_stop()
        assert errors == [("unavailable", "thermal safety is forcing fan output")]

    def test_a_genuine_failure_is_still_an_error(self):
        from control_ofc.api.errors import DaemonError

        worker, _c, errors, _s, _o = self._worker(
            raises=DaemonError(code="not_found", message="unknown device", status=404)
        )
        worker.do_cancel()
        assert errors == [("error", "unknown device")]

    def test_a_timeout_and_an_unavailable_daemon_name_the_verb(self):
        from control_ofc.api.errors import DaemonTimeout, DaemonUnavailable

        worker, _c, errors, _s, _o = self._worker(raises=DaemonTimeout())
        worker.do_marker("x", "")
        assert errors == [("unavailable", "Marking an event timed out.")]

        worker, _c, errors, _s, _o = self._worker(raises=DaemonUnavailable())
        worker.do_poll()
        assert errors == [("unavailable", "Daemon unavailable during reading the session.")]

    def test_a_dropped_connection_closes_the_client_so_the_next_call_reconnects(self):
        worker, _c, errors, _s, _o = self._worker(raises=ConnectionError("broken pipe"))
        worker.do_measurement("pwm_duty", 40.0, "%", "", "")
        assert errors == [("unavailable", "Connection lost during recording a measurement.")]
        assert worker._client is None, "a stale client must be dropped, not reused"


class TestWorkerRequestSignalsReachTheirWorker:
    """The hop `_stub_workers` deliberately skips, tested for real.

    The dialog tests above stub `_ensure_char_worker`/`_ensure_validation_worker`
    out, which is right for what they assert — but it leaves the *next* hop
    unproven: the `QueuedConnection`s inside those two `connect` closures, from
    the page's request signals into the worker's slots and back from the worker's
    result signals into the page. Measured: deleting
    `self._char_start_request.connect(w.do_start, …)` **and** the
    `w.session_updated` → `_on_validation_update` connection left the entire
    4134-test suite green.

    No socket is opened: the worker's lazily-built client is replaced with a fake
    after the thread starts, exactly as `test_v1_2_diagnostics.py` does for the
    verify worker. Every test tears the thread down in a `finally`.
    """

    class _Client:
        socket_path = "/nonexistent/control-ofc-worker.sock"

    def _page_with_client(self, qtbot):
        return _page(qtbot, client=self._Client(), devices=[_device()])

    def test_char_requests_reach_the_worker_and_results_reach_the_page(self, qtbot):
        from control_ofc.ui.widgets.pwm_characterization_dialog import (
            PwmCharacterizationDialog,
        )

        page, _ = self._page_with_client(qtbot)
        assert page._ensure_char_worker() is True
        worker = page._char_worker
        assert worker is not None and page._char_thread.isRunning()
        try:
            run = CharacterizationRun(
                run_id="char-1",
                header_id=_pump_header().id,
                state="running",
                requested_points_pct=[40],
                points=[CharPoint(requested_pct=40, command_accepted=True, rpm_after=900)],
            )
            calls: list[tuple] = []

            class _Fake:
                def start_characterization(self, header_id, **kw):
                    calls.append(("start", header_id, kw))
                    return run

                def characterization_status(self):
                    calls.append(("poll",))
                    return run

                def cancel_characterization(self):
                    calls.append(("cancel",))
                    return run

            worker._client = _Fake()

            dialog = PwmCharacterizationDialog(_pump_header().id, "AIO_PUMP", is_pump=True)
            qtbot.addWidget(dialog)
            dialog._started = True
            page._char_dialog = dialog

            page._char_start_request.emit(_pump_header().id, None, None)
            qtbot.waitUntil(lambda: any(c[0] == "start" for c in calls), timeout=3000)
            assert calls[0][1] == _pump_header().id

            page._char_poll_request.emit()
            qtbot.waitUntil(lambda: any(c[0] == "poll" for c in calls), timeout=3000)

            page._char_cancel_request.emit()
            qtbot.waitUntil(lambda: any(c[0] == "cancel" for c in calls), timeout=3000)

            # …and back: the worker's result must reach the open dialog through
            # the page's slot, which is the other half of the same closure.
            qtbot.waitUntil(lambda: dialog._table.rowCount() == 1, timeout=3000)
        finally:
            page._char_dialog = None
            page.cleanup()
        assert page._char_worker is None and page._char_thread is None

    def test_a_char_worker_error_reaches_the_page(self, qtbot):
        from control_ofc.api.errors import DaemonError
        from control_ofc.ui.widgets.pwm_characterization_dialog import (
            PwmCharacterizationDialog,
        )

        page, _ = self._page_with_client(qtbot)
        assert page._ensure_char_worker() is True
        try:

            class _Hot:
                def characterization_status(self):
                    raise DaemonError(
                        code="thermal_abort",
                        message="Cannot run while hot: Tctl at 91.0°C",
                        status=409,
                    )

            page._char_worker._client = _Hot()
            dialog = PwmCharacterizationDialog(_pump_header().id, "AIO_PUMP", is_pump=True)
            qtbot.addWidget(dialog)
            page._char_dialog = dialog

            page._char_poll_request.emit()
            qtbot.waitUntil(
                lambda: dialog._status_lbl.text() == "Cannot run while hot: Tctl at 91.0°C",
                timeout=3000,
            )
        finally:
            page._char_dialog = None
            page.cleanup()

    def test_validation_requests_reach_the_worker_and_results_reach_the_page(self, qtbot):
        page, _ = self._page_with_client(qtbot)
        assert page._ensure_validation_worker() is True
        assert page._validation_worker is not None and page._validation_thread.isRunning()
        try:
            session = _session()
            calls: list[str] = []

            class _Fake:
                def start_validation_session(self, device_id, **kw):
                    calls.append("start")
                    return session

                def validation_session(self):
                    calls.append("poll")
                    return session

                def stop_validation_session(self):
                    calls.append("stop")
                    return session

                def cancel_validation_session(self):
                    calls.append("cancel")
                    return session

                def add_validation_marker(self, detail, **kw):
                    calls.append("marker")

                def add_validation_measurement(self, kind, value, **kw):
                    calls.append("measurement")

            page._validation_worker._client = _Fake()

            dialog = ValidationSessionDialog("aio0", "AIO", members=[])
            qtbot.addWidget(dialog)
            page._validation_dialog = dialog

            page._validation_start_request.emit("aio0", "validation", [], [], {})
            page._validation_poll_request.emit()
            page._validation_stop_request.emit()
            page._validation_cancel_request.emit()
            page._validation_marker_request.emit("resumed", "")
            page._validation_measurement_request.emit("pwm_duty", 40.0, "%", "", "")
            qtbot.waitUntil(lambda: len(calls) == 6, timeout=3000)
            assert set(calls) == {"start", "poll", "stop", "cancel", "marker", "measurement"}

            # `session_updated` → `_on_validation_update` → the dialog…
            qtbot.waitUntil(lambda: dialog.session() is not None, timeout=3000)
            # …and `action_ok` → `_on_validation_action_ok`, a separate connection.
            qtbot.waitUntil(
                lambda: (
                    dialog._status_lbl.text() in ("Event marked.", "External measurement recorded.")
                ),
                timeout=3000,
            )
        finally:
            page._validation_dialog = None
            page.cleanup()
        assert page._validation_worker is None and page._validation_thread is None

    def test_a_validation_worker_error_reaches_the_page(self, qtbot):
        from control_ofc.api.errors import DaemonError

        page, _ = self._page_with_client(qtbot)
        assert page._ensure_validation_worker() is True
        try:

            class _Hot:
                def validation_session(self):
                    raise DaemonError(
                        code="thermal_abort", message="Cannot start while hot", status=409
                    )

            page._validation_worker._client = _Hot()
            dialog = ValidationSessionDialog("aio0", "AIO", members=[])
            qtbot.addWidget(dialog)
            page._validation_dialog = dialog

            page._validation_poll_request.emit()
            qtbot.waitUntil(
                lambda: dialog._status_lbl.text() == "Cannot start while hot", timeout=3000
            )
        finally:
            page._validation_dialog = None
            page.cleanup()

    def test_no_worker_without_a_socket_path(self, qtbot):
        """The gate the entry points rely on: no client means no thread."""
        page, _ = _page(qtbot, devices=[_device()])
        assert page._ensure_char_worker() is False
        assert page._ensure_validation_worker() is False
        assert page._char_thread is None and page._validation_thread is None
