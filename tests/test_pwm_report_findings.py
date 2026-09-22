"""The PWM Test Report's findings: every scoping rule, restoration, exposure
(DEC-404 Stage 4).

Each rule is asserted with its OPPOSITE arm too (CLAUDE.md, DEC-324): a
splitter scope that appears without a declared splitter, or a pump note on a
header that is not a pump, is the stuck-predicate failure a one-armed test
cannot see.
"""

from __future__ import annotations

import pytest

from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report.catalog import (
    TEST_PAIRING,
    TEST_PROBE,
    TEST_SWEEP,
    TEST_VERIFY,
)
from control_ofc.services.pwm_report.exposure import curve_minimum, member_exposures
from control_ofc.services.pwm_report.final_state import READBACK_TOLERANCE_PCT
from control_ofc.services.pwm_report.findings import (
    NOT_TESTED_BY_DESIGN,
    SEVERITY_ATTENTION,
    SEVERITY_INFO,
    SEVERITY_OBSERVATION,
    derive_findings,
)
from tests.pwm_report_fixtures import (
    CPU,
    EMPTY,
    OPENFAN,
    PUMP,
    SYS,
    bundle,
    channel,
    control,
    fan,
    graph,
    header,
    new_doc,
    probe_run,
    profile,
    sweep_run,
    verify_body,
)


def _step(sid: str, cid: str, test: str, result: dict | None, status: str = d.STEP_COMPLETE):
    return {
        "step_id": sid,
        "channel_id": cid,
        "test": test,
        "status": status,
        "reason": "",
        "result": result,
        "restore_outcome": (result or {}).get("restore_outcome", "restored") if result else "",
    }


def _doc(
    steps: list[dict],
    *,
    channels: list[dict] | None = None,
    base_fans: list[dict] | None = None,
    final_fans: list[dict] | None = None,
    final_status: dict | None = None,
    profile_body: dict | None = None,
    facts: dict | None = None,
    unavailable: dict | None = None,
    final_missing: bool = False,
    headers: list[dict] | None = None,
) -> dict:
    channels = channels or [channel(CPU, in_profile=True)]
    doc = new_doc(channels, facts=facts, unavailable=unavailable)
    base_fans = base_fans if base_fans is not None else [fan(CPU)]
    headers = headers if headers is not None else [header(CPU)]
    doc["snapshots"]["baseline"] = bundle(
        fans=base_fans, headers=headers, profile_body=profile_body
    )
    doc["configuration"] = d.extract_configuration(doc["snapshots"]["baseline"])
    if not final_missing:
        doc["snapshots"]["final"] = bundle(
            fans=final_fans if final_fans is not None else base_fans,
            headers=headers,
            status=final_status,
            profile_body=profile_body,
        )
    doc["steps"] = steps
    doc["state"] = d.STATE_COMPLETE
    return doc


def _rules(findings: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in findings:
        out.setdefault(f["rule"], []).append(f)
    return out


# ── No global verdict; not-tested-by-design always present ───────────────────


def test_there_is_no_global_verdict_and_the_by_design_list_is_always_there():
    findings, _ = derive_findings(_doc([]))
    rules = _rules(findings)
    assert not any(r.startswith(("overall", "verdict", "summary")) for r in rules)
    for rule, _text in NOT_TESTED_BY_DESIGN:
        assert rule in rules, f"{rule} must be listed even when nothing ran"
    # The Overview's nine unverifiable properties, each its own UNVERIFIED row.
    unverifiable = [f for f in findings if f["rule"].startswith("unverifiable.")]
    assert len(unverifiable) == 9
    assert {f["provenance"] for f in unverifiable} == {"UNVERIFIED"}


def test_every_finding_carries_the_full_shape():
    doc = _doc([_step("s1", CPU, TEST_VERIFY, verify_body())])
    findings, _ = derive_findings(doc)
    for f in findings:
        assert set(f) >= {
            "id",
            "rule",
            "channel_id",
            "statement",
            "result",
            "provenance",
            "scope",
            "evidence",
            "severity",
        }
        assert f["severity"] in (SEVERITY_ATTENTION, SEVERITY_OBSERVATION, SEVERITY_INFO)


# ── Verify ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("token", "result", "severity"),
    [
        ("effective", "pass", SEVERITY_INFO),
        ("pwm_enable_reverted", "observed", SEVERITY_ATTENTION),
        ("pwm_value_clamped", "observed", SEVERITY_ATTENTION),
        ("no_rpm_effect", "observed", SEVERITY_OBSERVATION),
        ("rpm_unavailable", "unavailable", SEVERITY_INFO),
        ("pwm_readback_unavailable", "unavailable", SEVERITY_INFO),
        ("a_token_from_a_newer_daemon", "unknown", SEVERITY_INFO),
    ],
)
def test_verify_tokens_map_to_scoped_findings(token, result, severity):
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_VERIFY, verify_body(token))]))
    (f,) = _rules(findings)["verify.result"]
    assert (f["result"], f["severity"]) == (result, severity)
    assert "one test duty (60 %)" in f["scope"], "a verify claim names the one duty it tested"
    if token == "a_token_from_a_newer_daemon":
        assert token in f["statement"], "an unrecognised token is rendered, never dropped"


# ── Splitter scoping (user facts) ───────────────────────────────────────────


@pytest.mark.parametrize("fans_behind", [3, 1, None])
def test_a_declared_splitter_scopes_every_rpm_claim(fans_behind):
    facts = {CPU: {"fans_behind": fans_behind}} if fans_behind else {}
    steps = [
        _step("s1", CPU, TEST_VERIFY, verify_body()),
        _step("s2", CPU, TEST_SWEEP, sweep_run()),
    ]
    findings, _ = derive_findings(_doc(steps, facts=facts))
    rules = _rules(findings)
    for rule in ("verify.result", "sweep.response"):
        scope = rules[rule][0]["scope"]
        if fans_behind and fans_behind > 1:
            assert f"{fans_behind} fans share this header" in scope, rule
        else:
            assert "share this header" not in scope, rule
    assert ("facts.splitter" in rules) is bool(fans_behind and fans_behind > 1)


# ── Sweep ───────────────────────────────────────────────────────────────────


def test_a_sweep_claim_names_its_tested_range():
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_SWEEP, sweep_run())]))
    for f in findings:
        if f["rule"] in ("sweep.response", "sweep.readback", "sweep.stability"):
            assert "between 20 % and 100 %" in f["scope"], f["rule"]


def test_a_device_override_is_an_observation_not_a_fault():
    run = sweep_run()
    run["summary"]["possible_device_override"] = True
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_SWEEP, run)]))
    (f,) = _rules(findings)["sweep.device_override"]
    assert f["severity"] == SEVERITY_OBSERVATION and f["result"] == "observed"
    assert "not a fault" in f["statement"]
    # The opposite arm.
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_SWEEP, sweep_run())]))
    assert "sweep.device_override" not in _rules(findings)


def test_interference_during_a_sweep_needs_attention():
    run = sweep_run()
    run["summary"]["interference_detected"] = True
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_SWEEP, run)]))
    assert _rules(findings)["sweep.interference"][0]["severity"] == SEVERITY_ATTENTION


def test_a_non_monotonic_leg_is_named():
    run = sweep_run()
    run["summary"]["monotonic"] = False
    run["summary"]["monotonic_rising"] = False
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_SWEEP, run)]))
    (f,) = _rules(findings)["sweep.response"]
    assert "NOT follow duty monotonically on the rising leg" in f["statement"]
    assert "followed duty on the falling leg" in f["statement"]
    assert f["severity"] == SEVERITY_OBSERVATION


def test_not_settled_stability_is_inconclusive():
    run = sweep_run()
    run["summary"]["stability_verdict"] = "not_settled"
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_SWEEP, run)]))
    assert _rules(findings)["sweep.stability"][0]["result"] == "unknown"


# ── Pairing ─────────────────────────────────────────────────────────────────


def _pairing(relationship: str, **extra) -> dict:
    return {
        "run_id": "cp-1",
        "state": "complete",
        "cycles": [
            {"cycle": 1, "observations": [{"noise_floor_from_cycle_1": False}]},
            {
                "cycle": 2,
                "baseline_settled": extra.pop("settled", True),
                "observations": [{"noise_floor_from_cycle_1": extra.pop("fallback", False)}],
            },
        ],
        "summary": {
            "relationship": relationship,
            "confidence": "high",
            "measurement_resolution_ms": 2000,
            "candidates": [
                {
                    "tach_id": "fan1",
                    "label": "fan1",
                    "direction": "positive",
                    "cycles_responded": 2,
                    "cycles_total": 2,
                }
            ],
        },
        "restore_outcome": "restored",
    }


@pytest.mark.parametrize(
    ("relationship", "result"),
    [
        ("confirmed", "pass"),
        ("probable", "observed"),
        ("ambiguous", "unknown"),
        ("no_tach_response", "not_observed"),
        ("multiple_responses", "observed"),
    ],
)
def test_pairing_relationships(relationship, result):
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_PAIRING, _pairing(relationship))]))
    assert _rules(findings)["pairing.result"][0]["result"] == result


def test_pairing_scope_says_when_a_baseline_did_not_settle():
    unsettled = _pairing("confirmed", settled=False, fallback=True)
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_PAIRING, unsettled)]))
    scope = _rules(findings)["pairing.result"][0]["scope"]
    assert "did not settle" in scope and "first, unperturbed cycle" in scope
    findings, _ = derive_findings(_doc([_step("s1", CPU, TEST_PAIRING, _pairing("confirmed"))]))
    assert "did not settle" not in _rules(findings)["pairing.result"][0]["scope"]


# ── Probe ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("outcome", "severity"),
    [
        ("stall_and_restart_found", SEVERITY_INFO),
        ("no_stall_down_to_0", SEVERITY_INFO),
        ("did_not_restart_below_20", SEVERITY_ATTENTION),
        ("stalled_at_or_above_20", SEVERITY_ATTENTION),
        ("no_fan_detected", SEVERITY_INFO),
    ],
)
def test_probe_outcomes(outcome, severity):
    ch = [channel(SYS)]
    doc = _doc(
        [_step("s1", SYS, TEST_PROBE, probe_run(outcome))],
        channels=ch,
        base_fans=[fan(SYS)],
        headers=[header(SYS)],
    )
    findings, _ = derive_findings(doc)
    (f,) = _rules(findings)["probe.result"]
    assert f["severity"] == severity
    assert "refreshes every 2000 ms" in f["scope"]


def test_a_stuck_fan_at_full_needs_attention():
    run = probe_run("did_not_restart_below_20")
    run["restart_failed_at_full"] = True
    doc = _doc(
        [_step("s1", SYS, TEST_PROBE, run)],
        channels=[channel(SYS)],
        base_fans=[fan(SYS)],
        headers=[header(SYS)],
    )
    findings, _ = derive_findings(doc)
    assert _rules(findings)["probe.stuck"][0]["severity"] == SEVERITY_ATTENTION


# ── Channel states ──────────────────────────────────────────────────────────


def test_a_pump_is_below_30_not_tested_by_design_and_only_a_pump():
    doc = _doc(
        [],
        channels=[channel(PUMP, pump=True, role="pump"), channel(CPU)],
        base_fans=[fan(PUMP), fan(CPU)],
        headers=[header(PUMP, role="pump", stop_permitted=False, floor=30), header(CPU)],
    )
    findings, _ = derive_findings(doc)
    pump_notes = _rules(findings)["channel.pump_floor"]
    assert [f["channel_id"] for f in pump_notes] == [PUMP]


def test_an_empty_header_is_inferred_and_its_stall_flag_explained():
    doc = _doc(
        [],
        channels=[channel(EMPTY), channel(CPU)],
        base_fans=[fan(EMPTY, rpm=0, stall=True), fan(CPU)],
        headers=[header(EMPTY), header(CPU)],
    )
    findings, _ = derive_findings(doc)
    (f,) = _rules(findings)["channel.no_fan"]
    assert f["channel_id"] == EMPTY and "inferred" in f["statement"]
    assert "not a stall" in f["statement"] and f["provenance"] == "DERIVED"
    # Without the flag, no PTR-k explanation is invented.
    doc = _doc(
        [],
        channels=[channel(EMPTY)],
        base_fans=[fan(EMPTY, rpm=0, stall=False)],
        headers=[header(EMPTY)],
    )
    (f,) = _rules(derive_findings(doc)[0])["channel.no_fan"]
    assert "not a stall" not in f["statement"]


def test_a_firmware_controlled_header_is_a_state_not_a_fault():
    doc = _doc(
        [],
        channels=[channel(SYS)],
        base_fans=[fan(SYS, mode=2)],
        headers=[header(SYS)],
    )
    (f,) = _rules(derive_findings(doc)[0])["channel.firmware_controlled"]
    assert f["severity"] == SEVERITY_INFO and "not a fault" in f["statement"]
    doc = _doc([], channels=[channel(SYS)], base_fans=[fan(SYS, mode=1)], headers=[header(SYS)])
    assert "channel.firmware_controlled" not in _rules(derive_findings(doc)[0])


def test_read_only_channels_carry_their_reason():
    doc = _doc(
        [],
        channels=[channel(OPENFAN, source="openfan")],
        base_fans=[fan(OPENFAN, source="openfan")],
        headers=[],
        unavailable={OPENFAN: {"verify": "OpenFan channels are reported read-only"}},
    )
    (f,) = _rules(derive_findings(doc)[0])["channel.read_only"]
    assert "OpenFan channels are reported read-only" in f["statement"]
    assert f["result"] == "not_tested"


def test_tests_not_offered_are_listed_with_the_reason():
    doc = _doc([], unavailable={CPU: {TEST_SWEEP: "Needs control-ofc-daemon 2.52.0 or newer"}})
    (f,) = _rules(derive_findings(doc)[0])["sweep.not_offered"]
    assert "2.52.0" in f["statement"] and f["result"] == "not_tested"


# ── Restoration ─────────────────────────────────────────────────────────────


def _restore_doc(final_fans, *, final_status=None, steps=None, final_missing=False):
    return _doc(
        steps or [_step("s1", CPU, TEST_VERIFY, verify_body())],
        final_fans=final_fans,
        final_status=final_status,
        final_missing=final_missing,
        profile_body=profile([control([CPU])], [graph("c1", [30, 60])]),
    )


def test_a_clean_restoration_passes_every_check():
    findings, actions = derive_findings(_restore_doc([fan(CPU)]))
    finals = [f for f in findings if f["rule"].startswith("final.")]
    assert finals and {f["result"] for f in finals} == {"pass"}
    assert actions == {"reapply_profile": False}


def test_a_changed_mode_fails_and_offers_reapply():
    findings, actions = derive_findings(_restore_doc([fan(CPU, mode=2)]))
    (f,) = _rules(findings)["final.mode"]
    assert f["result"] == "fail" and f["severity"] == SEVERITY_ATTENTION
    assert actions["reapply_profile"] is True


@pytest.mark.parametrize(
    ("readback", "passes"),
    [(40 + READBACK_TOLERANCE_PCT, True), (40 + READBACK_TOLERANCE_PCT + 1, False)],
)
def test_readback_is_judged_with_the_daemons_tolerance(readback, passes):
    findings, actions = derive_findings(_restore_doc([fan(CPU, readback=readback, commanded=40)]))
    (f,) = _rules(findings)["final.readback"]
    assert (f["result"] == "pass") is passes
    assert actions["reapply_profile"] is (not passes)


def test_a_failed_restore_is_reported_and_a_thermal_skip_does_not_offer_reapply():
    run = sweep_run()
    run["restore_outcome"] = "write_failed"
    run["restore_failed"] = True
    findings, actions = derive_findings(
        _restore_doc([fan(CPU)], steps=[_step("s1", CPU, TEST_SWEEP, run)])
    )
    assert _rules(findings)["final.restore"][0]["result"] == "fail"
    assert actions["reapply_profile"] is True
    run["restore_outcome"] = "skipped_thermal_force"
    findings, actions = derive_findings(
        _restore_doc([fan(CPU)], steps=[_step("s1", CPU, TEST_SWEEP, run)])
    )
    assert _rules(findings)["final.restore"][0]["result"] == "fail"
    # Under a thermal force, re-activating the profile is the wrong instruction.
    assert actions["reapply_profile"] is False


def test_a_stray_override_a_changed_profile_and_a_hot_machine_are_all_reported():
    status = {
        "thermal_state": "emergency",
        "active_profile_id": "other",
        "overrides": [{"control_id": "ctl-x", "pwm_percent": 80, "expires_in_secs": 5}],
    }
    findings, _ = derive_findings(_restore_doc([fan(CPU)], final_status=status))
    rules = _rules(findings)
    assert rules["final.override"][0]["result"] == "fail"
    assert rules["final.profile"][0]["result"] == "fail"
    assert rules["final.thermal"][0]["result"] == "fail"


def test_corrections_during_the_run_are_evidence_of_a_second_writer():
    findings, _ = derive_findings(
        _doc(
            [_step("s1", CPU, TEST_VERIFY, verify_body())],
            base_fans=[fan(CPU, corrections=2)],
            final_fans=[fan(CPU, corrections=5, not_holding=True)],
        )
    )
    rules = _rules(findings)
    assert "3 time(s)" in rules["final.corrections"][0]["statement"]
    assert rules["final.not_holding"][0]["result"] == "fail"
    # An unchanged count is no evidence at all.
    findings, _ = derive_findings(
        _doc(
            [_step("s1", CPU, TEST_VERIFY, verify_body())],
            base_fans=[fan(CPU, corrections=2)],
            final_fans=[fan(CPU, corrections=2)],
        )
    )
    assert "final.corrections" not in _rules(findings)


def test_a_missing_final_state_is_unverified_not_passed():
    findings, actions = derive_findings(_restore_doc(None, final_missing=True))
    rules = _rules(findings)
    assert rules["final.unavailable"][0]["result"] == "unavailable"
    assert "final.mode" not in rules
    assert actions["reapply_profile"] is True


def test_reapply_is_never_offered_without_an_active_profile():
    doc = _doc([_step("s1", CPU, TEST_VERIFY, verify_body())], final_fans=[fan(CPU, mode=2)])
    doc["configuration"]["active_profile_id"] = None
    _, actions = derive_findings(doc)
    assert actions["reapply_profile"] is False


# ── Exposure ────────────────────────────────────────────────────────────────


def test_curve_minimum_per_type():
    assert curve_minimum(graph("g", [35, 20, 80]))[0] == 20
    assert curve_minimum({"type": "linear", "start_output_pct": 25, "end_output_pct": 90})[0] == 25
    assert curve_minimum({"type": "flat", "flat_output_pct": 40})[0] == 40
    assert (
        curve_minimum({"type": "trigger", "trigger_idle_pct": 15, "trigger_load_pct": 90})[0] == 15
    )
    for kind in ("mix", "sync"):
        low, reason = curve_minimum({"type": kind})
        assert low is None and "cannot be read" in reason


def test_exposure_follows_the_daemons_tuning_order():
    prof = profile([control([SYS], offset_pct=5, minimum_pct=12)], [graph("c1", [3, 60])])
    (ex,) = member_exposures(prof, SYS, header_floor_pct=0, hard_floor=False)
    assert ex.lowest_pct == 12  # 3 + 5 = 8, lifted by the 12 % floor
    prof = profile([control([SYS], stop_pct=15)], [graph("c1", [10, 60])])
    (ex,) = member_exposures(prof, SYS, header_floor_pct=0, hard_floor=False)
    assert ex.lowest_pct == 0 and "stop threshold" in ex.basis
    # A hard (pump) floor is never stop-snapped (DEC-167). The stop threshold
    # must sit ABOVE the floor for this to discriminate: with it below, the
    # floored output never falls under it and the snap cannot fire either way
    # (the first draft of this test, which passed with the exemption deleted).
    prof = profile([control([SYS], stop_pct=40)], [graph("c1", [10, 60])])
    (soft,) = member_exposures(prof, SYS, header_floor_pct=30, hard_floor=False)
    assert soft.lowest_pct == 0, "precondition: without the exemption the floor IS snapped"
    (ex,) = member_exposures(prof, SYS, header_floor_pct=30, hard_floor=True)
    assert ex.lowest_pct == 30


def _exposure_doc(steps, outputs):
    prof = profile([control([SYS])], [graph("c1", outputs)])
    return _doc(
        steps,
        channels=[channel(SYS, in_profile=True)],
        base_fans=[fan(SYS)],
        headers=[header(SYS)],
        profile_body=prof,
    )


def test_exposure_untested_below_the_lowest_verified_duty():
    doc = _exposure_doc([_step("s1", SYS, TEST_SWEEP, sweep_run())], [8, 60])
    (f,) = _rules(derive_findings(doc)[0])["exposure.untested"]
    assert "can command 8 %" in f["statement"] and "(20 %)" in f["statement"]
    assert f["result"] == "not_tested" and f["provenance"] == "DERIVED"


def test_exposure_below_the_measured_restart_needs_attention():
    doc = _exposure_doc([_step("s1", SYS, TEST_PROBE, probe_run())], [8, 60])
    (f,) = _rules(derive_findings(doc)[0])["exposure.below_restart"]
    assert f["severity"] == SEVERITY_ATTENTION
    assert "8 %" in f["statement"] and "restarted only at 12 %" in f["statement"]


def test_exposure_covered_by_the_probe_is_information():
    doc = _exposure_doc([_step("s1", SYS, TEST_PROBE, probe_run())], [14, 60])
    rules = _rules(derive_findings(doc)[0])
    assert "exposure.covered" in rules and "exposure.below_restart" not in rules


def test_a_mix_curve_is_reported_as_unbounded_not_guessed():
    prof = profile([control([SYS], curve_id="m")], [{"id": "m", "type": "mix"}])
    doc = _doc(
        [_step("s1", SYS, TEST_VERIFY, verify_body())],
        channels=[channel(SYS, in_profile=True)],
        base_fans=[fan(SYS)],
        headers=[header(SYS)],
        profile_body=prof,
    )
    (f,) = _rules(derive_findings(doc)[0])["exposure.unbounded"]
    assert f["result"] == "unknown"


# ── Configuration observations ──────────────────────────────────────────────


def test_empty_controls_and_no_slew_limiting_are_observed():
    prof = profile(
        [control([CPU]), {"id": "e", "name": "Empty", "members": []}], [graph("c1", [30, 60])]
    )
    rules = _rules(derive_findings(_doc([], profile_body=prof))[0])
    assert rules["config.empty_control"][0]["statement"].startswith("Control 'Empty'")
    assert "config.no_slew" in rules
    prof = profile([control([CPU], step_up_pct=10.0)], [graph("c1", [30, 60])])
    assert "config.no_slew" not in _rules(derive_findings(_doc([], profile_body=prof))[0])


def test_no_facts_supplied_is_said_once():
    rules = _rules(derive_findings(_doc([]))[0])
    assert len(rules["facts.none"]) == 1
    rules = _rules(derive_findings(_doc([], facts={CPU: {"fans_behind": 2}}))[0])
    assert "facts.none" not in rules


def test_findings_are_deterministic():
    doc = _doc([_step("s1", CPU, TEST_SWEEP, sweep_run())])
    assert derive_findings(doc) == derive_findings(doc)
