"""The report's findings: what this run observed, each claim scoped to what was
actually tested (DEC-404's evidence rules).

**No global verdict.** There is no "PASS" for the machine. Each finding carries
its own result token (the validation vocabulary), provenance class, scope and
severity, and answers exactly one question. A completed test is not a pass; a
clean abort is not a failure; an absence is never a pass.

Scoping rules, each implemented below and each pinned by a test:

* a response claim names its tested range and never extrapolates;
* a declared splitter/hub scopes every RPM claim to the one tach-reporting fan;
* a pump-protected header is "below 30 % not tested, by design";
* an empty header is "no fan detected (inferred)", and its ``stall_detected``
  is shown as not a stall (``PTR-k``);
* a device override is *observed*, not a fault;
* a firmware-controlled header is a state, not a fault;
* ``unavailable`` and ``not_tested`` are neutral.

Severities: ``attention`` (look at this), ``observation`` (worth knowing, not a
problem by itself), ``info`` (context). Wording is the GUI's; every daemon
token is rendered, never dropped, and never guessed at (273-i).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from control_ofc.api.models import (
    PROVENANCE_DERIVED,
    PROVENANCE_DEVICE_METADATA,
    PROVENANCE_OBSERVED,
    PROVENANCE_UNVERIFIED,
    PROVENANCE_USER_METADATA,
    VALIDATION_RESULT_FAIL,
    VALIDATION_RESULT_INTERRUPTED,
    VALIDATION_RESULT_NOT_OBSERVED,
    VALIDATION_RESULT_NOT_TESTED,
    VALIDATION_RESULT_OBSERVED,
    VALIDATION_RESULT_PASS,
    VALIDATION_RESULT_UNAVAILABLE,
    VALIDATION_RESULT_UNKNOWN,
)
from control_ofc.services.provenance import UNVERIFIABLE
from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report.catalog import (
    SPECS,
    TEST_PAIRING,
    TEST_PROBE,
    TEST_SWEEP,
    TEST_VERIFY,
)
from control_ofc.services.pwm_report.exposure import member_exposures
from control_ofc.services.pwm_report.final_state import (
    CHECK_FAIL,
    CHECK_PASS,
    final_state_checks,
)
from control_ofc.services.pwm_report.setup_facts import header_facts_from

SEVERITY_ATTENTION = "attention"
SEVERITY_OBSERVATION = "observation"
SEVERITY_INFO = "info"
SEVERITIES = (SEVERITY_ATTENTION, SEVERITY_OBSERVATION, SEVERITY_INFO)

#: The characterisation daemon's hard minimum for a walked duty; below it only
#: the stall probe goes (DEC-407). Used only in wording.
_SWEEP_FLOOR_PCT = 20

#: DEC-404: what the report cannot test at all, always listed so an absence
#: never reads as a pass.
NOT_TESTED_BY_DESIGN: tuple[tuple[str, str], ...] = (
    (
        "design.daemon_restart",
        "Daemon stop/start and firmware fallback: a client cannot stop the daemon to "
        "watch what the firmware does without it.",
    ),
    (
        "design.enable_matrix",
        "The pwmN_enable mode matrix: the daemon owns the control mode, and the report "
        "never writes it.",
    ),
    ("design.suspend", "Suspend and resume: the report cannot put the machine to sleep."),
    (
        "design.pump_below_30",
        "A pump below 30 %: never driven there, by design (the pump floor).",
    ),
    (
        "design.coolant",
        "Coolant temperature: a motherboard-connected AIO has no coolant sensor the "
        "daemon can read.",
    ),
    (
        "design.openfan_gpu",
        "Active tests on OpenFan channels and GPU fans: those are reported read-only.",
    ),
    (
        "design.thermal_response",
        "Closed-loop thermal response under load: Control-OFC never launches a "
        "workload. Use a Thermal Observation session on the Hardware page for that.",
    ),
)


class _Collector:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(
        self,
        rule: str,
        statement: str,
        *,
        result: str,
        provenance: str,
        severity: str,
        channel_id: str | None = None,
        scope: str = "",
        evidence: tuple[str, ...] | list[str] = (),
    ) -> None:
        self.items.append(
            {
                "id": f"f{len(self.items) + 1}",
                "rule": rule,
                "channel_id": channel_id,
                "statement": statement,
                "result": result,
                "provenance": provenance,
                "scope": scope,
                "evidence": list(evidence),
                "severity": severity,
            }
        )


def _m(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _pct(value: object) -> str:
    return "—" if _int(value) is None else f"{value} %"


def derive_findings(doc: Mapping) -> tuple[list[dict], dict]:
    """Every finding for a finished (or ended) run, plus the actions it earns.

    Pure: the same document always yields the same findings, so a saved report
    can be re-derived and a comparison can trust both sides were judged alike.
    """
    out = _Collector()
    channels = {c.get("channel_id"): c for c in doc.get("channels") or []}
    names = {cid: str(c.get("name") or cid) for cid, c in channels.items()}
    facts = _m(_m(doc.get("user_facts")).get("headers"))

    _run_state(out, doc)
    for step in doc.get("steps") or []:
        _step_findings(out, step, names, facts)
    reapply = _final_state(out, doc, names)
    _channel_states(out, doc, names)
    _exposure(out, doc, names)
    _configuration(out, doc)
    _user_facts(out, doc, names, facts)
    for rule, text in NOT_TESTED_BY_DESIGN:
        out.add(
            rule,
            text,
            result=VALIDATION_RESULT_NOT_TESTED,
            provenance=PROVENANCE_DERIVED,
            severity=SEVERITY_INFO,
            scope="not testable by this report",
        )
    # The Overview's "software must not claim without external evidence" list —
    # one row each, UNVERIFIED, because an omitted row reads as a pass.
    for key, text in UNVERIFIABLE:
        out.add(
            f"unverifiable.{key}",
            f"{text}: cannot be established from motherboard hwmon.",
            result=VALIDATION_RESULT_NOT_TESTED,
            provenance=PROVENANCE_UNVERIFIED,
            severity=SEVERITY_INFO,
            scope="needs external measurement",
        )
    has_profile = bool(_m(doc.get("configuration")).get("active_profile_id"))
    return out.items, {"reapply_profile": bool(reapply and has_profile)}


# ── Run-level ─────────────────────────────────────────────────────────────────


def _run_state(out: _Collector, doc: Mapping) -> None:
    state = doc.get("state")
    reason = str(doc.get("state_reason") or "")
    if state == d.STATE_COMPLETE:
        return
    words = {
        d.STATE_CANCELLED: "The run was cancelled before every selected test finished.",
        d.STATE_ABORTED: "The run was stopped early by a safety condition.",
        d.STATE_INTERRUPTED: "The run was interrupted before every selected test finished.",
    }.get(str(state), f"The run ended in state '{state}'.")
    out.add(
        "run.state",
        f"{words} {reason}".strip(),
        result=VALIDATION_RESULT_INTERRUPTED,
        provenance=PROVENANCE_OBSERVED,
        severity=SEVERITY_ATTENTION if state == d.STATE_ABORTED else SEVERITY_INFO,
        scope="tests after this point are listed as not tested, with the reason",
    )


# ── Per step ──────────────────────────────────────────────────────────────────


def _splitter_scope(facts: Mapping, cid: str) -> str:
    hf = header_facts_from(facts.get(cid))
    if hf.behind_splitter:
        return (
            f"you said {hf.fans_behind} fans share this header; RPM is from the one fan "
            "whose tach is wired through, not all of them"
        )
    return ""


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def _step_findings(out: _Collector, step: Mapping, names: Mapping, facts: Mapping) -> None:
    cid = str(step.get("channel_id") or "")
    name = names.get(cid, cid)
    test = str(step.get("test") or "")
    title = SPECS[test].title if test in SPECS else test
    status = str(step.get("status") or "")
    reason = str(step.get("reason") or "")
    ev = (f"steps.{step.get('step_id')}",)

    if status == d.STEP_NOT_TESTED:
        out.add(
            f"{test}.not_tested",
            f"{title} on {name}: not tested. {reason}".strip(),
            result=VALIDATION_RESULT_NOT_TESTED,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_INFO,
            channel_id=cid,
            evidence=ev,
        )
        return
    if status in (d.STEP_CANCELLED, d.STEP_ABORTED, d.STEP_INTERRUPTED):
        detail = str(_m(step.get("result")).get("detail") or "")
        out.add(
            f"{test}.ended_early",
            _join_sentences(
                f"{title} on {name}: ended before it finished ({status}).", reason, detail
            ),
            result=VALIDATION_RESULT_INTERRUPTED,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_INFO,
            channel_id=cid,
            scope="any partial readings are in the evidence; no conclusion is drawn from them",
            evidence=ev,
        )
        return
    if status == d.STEP_ERROR:
        out.add(
            f"{test}.no_result",
            f"{title} on {name}: no result. {reason}".strip(),
            result=VALIDATION_RESULT_UNAVAILABLE,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_OBSERVATION,
            channel_id=cid,
            evidence=ev,
        )
        return
    if status != d.STEP_COMPLETE:
        out.add(
            f"{test}.unknown_state",
            f"{title} on {name}: the daemon ended it in a state this version does not "
            f"recognise ('{status}').",
            result=VALIDATION_RESULT_UNKNOWN,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_INFO,
            channel_id=cid,
            evidence=ev,
        )
        return
    result = _m(step.get("result"))
    splitter = _splitter_scope(facts, cid)
    if test == TEST_VERIFY:
        _verify(out, cid, name, result, splitter, ev)
    elif test == TEST_PAIRING:
        _pairing(out, cid, name, result, splitter, ev)
    elif test == TEST_SWEEP:
        _sweep(out, cid, name, result, splitter, ev)
    elif test == TEST_PROBE:
        _probe(out, cid, name, result, splitter, ev)


def _join_sentences(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


def _verify(
    out: _Collector, cid: str, name: str, r: Mapping, splitter: str, ev: tuple[str, ...]
) -> None:
    token = str(r.get("result") or "")
    test_pct = _int(r.get("test_pwm_percent"))
    before = _int(_m(r.get("initial_state")).get("rpm"))
    after = _int(_m(r.get("final_state")).get("rpm"))
    readback = _int(_m(r.get("final_state")).get("pwm_percent"))
    if token == "pump_protected_mid_run":
        # DEC-418 review `C2`: the duty is the one PLANNED, and the window never
        # completed — a stop before the test write wrote nothing at all.
        scope = _join(
            f"a planned test duty of {_pct(test_pct)}, stopped before its "
            f"{r.get('wait_seconds', '?')} s window completed",
            splitter,
        )
    else:
        scope = _join(
            f"one test duty ({_pct(test_pct)}), held for {r.get('wait_seconds', '?')} s",
            splitter,
        )
    rpm = f"{before if before is not None else '—'} → {after if after is not None else '—'} rpm"
    table = {
        "effective": (
            f"{name}: a test write held and the fan responded ({rpm}).",
            VALIDATION_RESULT_PASS,
            SEVERITY_INFO,
        ),
        "pwm_enable_reverted": (
            f"{name}: the header's control mode was switched back while the test held it — "
            "something else (typically the BIOS or EC) reclaimed it.",
            VALIDATION_RESULT_OBSERVED,
            SEVERITY_ATTENTION,
        ),
        "pwm_value_clamped": (
            f"{name}: the duty read back as {_pct(readback)} after a write of {_pct(test_pct)} "
            "— the hardware clamped or ignored it.",
            VALIDATION_RESULT_OBSERVED,
            SEVERITY_ATTENTION,
        ),
        "no_rpm_effect": (
            f"{name}: the write was accepted but RPM did not change ({rpm}). A disconnected "
            "or stalled fan, a fan hub, or a device with its own controller all look like "
            "this.",
            VALIDATION_RESULT_OBSERVED,
            SEVERITY_OBSERVATION,
        ),
        "rpm_unavailable": (
            f"{name}: the write was accepted, but this header has no RPM reading to "
            "confirm a fan responded.",
            VALIDATION_RESULT_UNAVAILABLE,
            SEVERITY_INFO,
        ),
        "pwm_readback_unavailable": (
            f"{name}: the duty could not be read back, so whether the write held is unconfirmed.",
            VALIDATION_RESULT_UNAVAILABLE,
            SEVERITY_INFO,
        ),
        # `TS-aw` / DEC-418: the daemon files this `unavailable` too. No claim
        # about the restore (review `C1`): the daemon may report it failed.
        "pump_protected_mid_run": (
            f"{name}: the header became pump-protected during the test, so the daemon "
            "stopped it before it measured anything.",
            VALIDATION_RESULT_UNAVAILABLE,
            SEVERITY_INFO,
        ),
    }
    statement, result, severity = table.get(
        token,
        (
            f"{name}: the daemon reported '{token}', which this version of Control-OFC "
            "does not recognise.",
            VALIDATION_RESULT_UNKNOWN,
            SEVERITY_INFO,
        ),
    )
    out.add(
        "verify.result",
        statement,
        result=result,
        provenance=PROVENANCE_OBSERVED,
        severity=severity,
        channel_id=cid,
        scope=scope,
        evidence=ev,
    )


def _settled_tail_scope(run: Mapping) -> str:
    """What the spread figures describe, from the points themselves (DEC-405).

    Each point's statistics start at its settle point (``window_start_ms``);
    ``0`` there means the point never settled and its figures span the whole
    hold. The tach refresh is the register's own (``update_interval_ms``).
    """
    stabs = [
        _m(_m(p).get("stability")) for p in run.get("points") or [] if _m(_m(p).get("stability"))
    ]
    starts = [st.get("window_start_ms") for st in stabs]
    never = sum(1 for w in starts if w == 0)
    refresh = [st.get("update_interval_ms") for st in stabs if _int(st.get("update_interval_ms"))]
    parts = ["measured over each point's settled tail (DEC-405)"]
    if never:
        parts.append(f"{never} point(s) never settled, so theirs span the whole hold")
    if refresh:
        parts.append(f"tach refresh {max(refresh)} ms")
    return _join(*parts)


_PAIRING = {
    "confirmed": (VALIDATION_RESULT_PASS, SEVERITY_INFO, "follows"),
    "probable": (VALIDATION_RESULT_OBSERVED, SEVERITY_INFO, "probably follows"),
}


def _pairing(
    out: _Collector, cid: str, name: str, run: Mapping, splitter: str, ev: tuple[str, ...]
) -> None:
    summary = _m(run.get("summary"))
    relationship = str(summary.get("relationship") or "")
    confidence = str(summary.get("confidence") or "unknown")
    cands = [c for c in summary.get("candidates") or [] if isinstance(c, Mapping)]
    notes: list[str] = []
    cycles = [c for c in run.get("cycles") or [] if isinstance(c, Mapping)]
    if any(c.get("baseline_settled") is False for c in cycles):
        notes.append("a baseline did not settle within its wait")
    if any(
        _m(o).get("noise_floor_from_cycle_1") is True
        for c in cycles
        for o in c.get("observations") or []
    ):
        notes.append("its noise floor was taken from the first, unperturbed cycle")
    resolution = _int(summary.get("measurement_resolution_ms"))
    waits = [_int(c.get("settle_wait_ms")) for c in cycles]
    longest_wait = max((w for w in waits if w), default=0)
    if longest_wait:
        notes.append(f"waited up to {longest_wait / 1000:.1f} s for the tachs to settle")
    scope = _join(
        f"{len(cycles)} perturbation cycle(s)",
        f"tach resolution {resolution} ms" if resolution else "tach resolution unknown",
        *notes,
        splitter,
    )
    if relationship in _PAIRING and cands:
        result, severity, verb = _PAIRING[relationship]
        top = cands[0]
        label = str(top.get("label") or top.get("tach_id") or "?")
        out.add(
            "pairing.result",
            f"The tach '{label}' {verb} {name} ({top.get('direction') or '?'}, confidence "
            f"{confidence}, responded in {top.get('cycles_responded')}/"
            f"{top.get('cycles_total')} cycles).",
            result=result,
            provenance=PROVENANCE_DERIVED,
            severity=severity,
            channel_id=cid,
            scope=scope,
            evidence=ev,
        )
        return
    if relationship == "multiple_responses":
        labels = ", ".join(str(c.get("label") or c.get("tach_id")) for c in cands)
        statement = (
            f"More than one tach followed {name} ({labels}) — a splitter or a shared "
            "controller looks like this."
        )
        result, severity = VALIDATION_RESULT_OBSERVED, SEVERITY_OBSERVATION
    elif relationship == "no_tach_response":
        statement = (
            f"No tach responded to {name}. An empty header, a fan behind a hub, or a device "
            "that ignores PWM all look like this."
        )
        result, severity = VALIDATION_RESULT_NOT_OBSERVED, SEVERITY_OBSERVATION
    elif relationship == "ambiguous":
        statement = f"Which tach follows {name} could not be established (ambiguous)."
        result, severity = VALIDATION_RESULT_UNKNOWN, SEVERITY_OBSERVATION
    else:
        statement = f"Tach pairing for {name}: the daemon reported '{relationship or 'nothing'}'."
        result, severity = VALIDATION_RESULT_UNKNOWN, SEVERITY_INFO
    out.add(
        "pairing.result",
        statement,
        result=result,
        provenance=PROVENANCE_DERIVED,
        severity=severity,
        channel_id=cid,
        scope=scope,
        evidence=ev,
    )


def _sweep(
    out: _Collector, cid: str, name: str, run: Mapping, splitter: str, ev: tuple[str, ...]
) -> None:
    s = _m(run.get("summary"))
    lo, hi = _int(s.get("min_tested_pct")), _int(s.get("max_tested_pct"))
    tested = f"tested between {_pct(lo)} and {_pct(hi)}"
    base_scope = _join(tested, splitter)

    acceptance = str(s.get("command_acceptance") or "")
    if acceptance and acceptance != "pass":
        out.add(
            "sweep.acceptance",
            f"{name}: some sweep writes were not accepted ({acceptance}).",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_ATTENTION,
            channel_id=cid,
            scope=base_scope,
            evidence=ev,
        )

    readback = str(s.get("pwm_readback") or "")
    readback_words = {
        "pass": (
            "the duty read back as written at every step.",
            VALIDATION_RESULT_PASS,
            SEVERITY_INFO,
        ),
        "clamped": (
            f"the duty read back differently from the command (clamped at "
            f"{_pct(s.get('clamp_pct'))}).",
            VALIDATION_RESULT_OBSERVED,
            SEVERITY_ATTENTION,
        ),
        "reverted": (
            "something reclaimed the header during the sweep.",
            VALIDATION_RESULT_OBSERVED,
            SEVERITY_ATTENTION,
        ),
        "unavailable": (
            "the duty could not be read back.",
            VALIDATION_RESULT_UNAVAILABLE,
            SEVERITY_INFO,
        ),
    }
    if readback:
        text, result, severity = readback_words.get(
            readback, (f"readback verdict '{readback}'.", VALIDATION_RESULT_UNKNOWN, SEVERITY_INFO)
        )
        out.add(
            "sweep.readback",
            f"{name}: {text}",
            result=result,
            provenance=PROVENANCE_OBSERVED,
            severity=severity,
            channel_id=cid,
            scope=tested,
            evidence=ev,
        )

    response = str(s.get("rpm_response") or "")
    if response == "responsive":
        legs = []
        # Literal lookups, one per leg (DEC-405's per-leg verdicts), rather than
        # a loop over key names: the wire oracle proves a read by the name in
        # lookup position, and a name held in a tuple is not a read it can see.
        for leg, v in (
            ("falling", s.get("monotonic_falling")),
            ("rising", s.get("monotonic_rising")),
        ):
            if v is True:
                legs.append(f"RPM followed duty on the {leg} leg")
            elif v is False:
                legs.append(f"RPM did NOT follow duty monotonically on the {leg} leg")
        if not legs and s.get("monotonic") is not None:
            legs.append(
                "RPM followed duty"
                if s.get("monotonic")
                else "RPM did not follow duty monotonically"
            )
        span = (
            f"RPM moved between {_pct(s.get('min_responsive_pct'))} and "
            f"{_pct(s.get('max_responsive_pct'))}"
        )
        monotonic_false = s.get("monotonic") is False
        out.add(
            "sweep.response",
            f"{name}: " + _join(span, *legs) + ".",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_OBSERVATION if monotonic_false else SEVERITY_INFO,
            channel_id=cid,
            scope=base_scope,
            evidence=ev,
        )
        dead = _int(s.get("dead_zone_upper_pct"))
        if dead is not None:
            out.add(
                "sweep.dead_zone",
                f"{name}: RPM did not change up to {dead} %.",
                result=VALIDATION_RESULT_OBSERVED,
                provenance=PROVENANCE_DERIVED,
                severity=SEVERITY_OBSERVATION,
                channel_id=cid,
                scope=base_scope,
                evidence=ev,
            )
    elif response == "no_response":
        out.add(
            "sweep.response",
            f"{name}: RPM did not respond to duty. An empty header, a fan hub, or a device "
            "with its own controller all look like this.",
            result=VALIDATION_RESULT_NOT_OBSERVED,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_OBSERVATION,
            channel_id=cid,
            scope=base_scope,
            evidence=ev,
        )
    elif response == "unavailable":
        out.add(
            "sweep.response",
            f"{name}: no RPM reading, so the fan's response could not be observed.",
            result=VALIDATION_RESULT_UNAVAILABLE,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_INFO,
            channel_id=cid,
            scope=tested,
            evidence=ev,
        )
    elif response:
        out.add(
            "sweep.response",
            f"{name}: the daemon reported RPM response '{response}'.",
            result=VALIDATION_RESULT_UNKNOWN,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_INFO,
            channel_id=cid,
            scope=tested,
            evidence=ev,
        )

    if s.get("possible_device_override") is True:
        out.add(
            "sweep.device_override",
            f"{name}: the duty read back correctly but RPM did not follow it. A device with "
            "its own controller (an AIO pump, for example) behaves like this — it is an "
            "observation, not a fault.",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_DERIVED,
            severity=SEVERITY_OBSERVATION,
            channel_id=cid,
            scope=base_scope,
            evidence=ev,
        )
    if s.get("interference_detected") is True:
        out.add(
            "sweep.interference",
            f"{name}: the header's control mode changed during the sweep — something else "
            "took control of it.",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_ATTENTION,
            channel_id=cid,
            scope=tested,
            evidence=ev,
        )

    hyst = str(s.get("hysteresis_verdict") or "")
    if hyst == "present":
        out.add(
            "sweep.hysteresis",
            f"{name}: RPM differed between the falling and rising legs by up to "
            f"{s.get('hysteresis_worst_delta_rpm')} rpm "
            f"(at {_pct(s.get('hysteresis_worst_duty_pct'))}).",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_DERIVED,
            severity=SEVERITY_OBSERVATION,
            channel_id=cid,
            scope=_join(
                f"{s.get('hysteresis_compared_points')} duties compared in both directions",
                splitter,
            ),
            evidence=ev,
        )
    elif hyst == "none":
        out.add(
            "sweep.hysteresis",
            f"{name}: no difference between the falling and rising legs was observed.",
            result=VALIDATION_RESULT_NOT_OBSERVED,
            provenance=PROVENANCE_DERIVED,
            severity=SEVERITY_INFO,
            channel_id=cid,
            scope=_join(
                f"{s.get('hysteresis_compared_points')} duties compared in both directions",
                splitter,
            ),
            evidence=ev,
        )

    stab = str(s.get("stability_verdict") or "")
    stability_scope = _join(_settled_tail_scope(run), tested, splitter)
    if stab in ("variable", "unstable"):
        out.add(
            "sweep.stability",
            f"{name}: RPM varied at a steady duty (worst CV {s.get('worst_cv_pct')} %). Tach "
            "variation alone is not evidence of a fault.",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_DERIVED,
            severity=SEVERITY_OBSERVATION,
            channel_id=cid,
            scope=stability_scope,
            evidence=ev,
        )
    elif stab == "not_settled":
        out.add(
            "sweep.stability",
            f"{name}: at least one step never settled within its hold, so its spread "
            "describes the whole hold, not a steady state.",
            result=VALIDATION_RESULT_UNKNOWN,
            provenance=PROVENANCE_DERIVED,
            severity=SEVERITY_OBSERVATION,
            channel_id=cid,
            scope=stability_scope,
            evidence=ev,
        )
    elif stab == "stable":
        out.add(
            "sweep.stability",
            f"{name}: RPM held steady at each duty (worst CV {s.get('worst_cv_pct')} %).",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_DERIVED,
            severity=SEVERITY_INFO,
            channel_id=cid,
            scope=stability_scope,
            evidence=ev,
        )

    if s.get("outside_learned_range") is True:
        out.add(
            "sweep.learned_range",
            f"{name}: PWM command and readback are valid, but RPM is outside the range "
            "learned for this header. The device may be applying internal control, a clamp, "
            "startup behaviour or thermal protection, or may report RPM differently.",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_DERIVED,
            severity=SEVERITY_OBSERVATION,
            channel_id=cid,
            scope=tested,
            evidence=ev,
        )


_PROBE = {
    "stall_and_restart_found": (VALIDATION_RESULT_OBSERVED, SEVERITY_INFO),
    "no_stall_down_to_0": (VALIDATION_RESULT_NOT_OBSERVED, SEVERITY_INFO),
    "did_not_restart_below_20": (VALIDATION_RESULT_OBSERVED, SEVERITY_ATTENTION),
    "stalled_at_or_above_20": (VALIDATION_RESULT_OBSERVED, SEVERITY_ATTENTION),
    "no_fan_detected": (VALIDATION_RESULT_NOT_OBSERVED, SEVERITY_INFO),
}


def _probe(
    out: _Collector, cid: str, name: str, run: Mapping, splitter: str, ev: tuple[str, ...]
) -> None:
    outcome = str(run.get("outcome") or "")
    stall, restart = _int(run.get("stall_duty_pct")), _int(run.get("restart_duty_pct"))
    refresh = _int(run.get("refresh_ms"))
    notes = [
        f"judged on a tach that refreshes every {refresh} ms ({run.get('refresh_source')})"
        if refresh
        else "tach refresh unknown",
    ]
    if run.get("baseline_settled") is False:
        notes.append("the 20 % baseline did not settle")
    scope = _join(*notes, splitter)
    statements = {
        "stall_and_restart_found": (
            f"{name}: the fan stopped at {_pct(stall)} on the way down and started again at "
            f"{_pct(restart)} on the way up"
            + (
                f" — {run.get('hysteresis_pct')} points apart."
                if _int(run.get("hysteresis_pct")) is not None
                else "."
            )
        ),
        "no_stall_down_to_0": f"{name}: the fan kept turning all the way down to 0 %.",
        "did_not_restart_below_20": (
            f"{name}: the fan stopped at {_pct(stall)} and did not start again below 20 %; "
            "the daemon then ran it at 100 % to restart it."
        ),
        "stalled_at_or_above_20": (
            f"{name}: the fan stopped at the probe's 20 % starting point, so it stops at or "
            "above 20 % (a DC-mode header often does)."
        ),
        "no_fan_detected": (
            f"{name}: no fan was detected (0 rpm before the probe and at 20 %), so nothing "
            "below 20 % was written."
        ),
    }
    if outcome in statements:
        result, severity = _PROBE[outcome]
        out.add(
            "probe.result",
            statements[outcome],
            result=result,
            provenance=PROVENANCE_OBSERVED,
            severity=severity,
            channel_id=cid,
            scope=scope,
            evidence=ev,
        )
    else:
        reason = str(run.get("abort_reason") or run.get("detail") or "")
        out.add(
            "probe.result",
            _join_sentences(
                f"{name}: the probe ended without a result ({outcome or 'no outcome'}).", reason
            ),
            result=VALIDATION_RESULT_INTERRUPTED
            if outcome in ("aborted", "cancelled")
            else VALIDATION_RESULT_UNKNOWN,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_INFO,
            channel_id=cid,
            scope=scope,
            evidence=ev,
        )
    if run.get("restart_failed_at_full") is True:
        out.add(
            "probe.stuck",
            f"{name}: during the recovery burst at 100 % the fan still read 0 rpm — it may be "
            "physically stuck. The header was restored regardless.",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_ATTENTION,
            channel_id=cid,
            scope=scope,
            evidence=ev,
        )


# ── Restoration ───────────────────────────────────────────────────────────────


def _final_state(out: _Collector, doc: Mapping, names: Mapping) -> bool:
    reapply = False
    for check in final_state_checks(doc, names):
        if check.result == CHECK_PASS:
            result, severity = VALIDATION_RESULT_PASS, SEVERITY_INFO
        elif check.result == CHECK_FAIL:
            result, severity = VALIDATION_RESULT_FAIL, SEVERITY_ATTENTION
            reapply = reapply or check.reapply_fixes
        else:
            result, severity = VALIDATION_RESULT_UNAVAILABLE, SEVERITY_ATTENTION
            reapply = reapply or check.reapply_fixes
        out.add(
            check.rule,
            check.statement,
            result=result,
            provenance=PROVENANCE_OBSERVED,
            severity=severity,
            channel_id=check.channel_id,
            scope="compared with the state the run began from",
            evidence=check.evidence,
        )
    return reapply


# ── Read-only channel states ──────────────────────────────────────────────────


def _channel_states(out: _Collector, doc: Mapping, names: Mapping) -> None:
    base = _m(doc.get("snapshots")).get("baseline")
    fans = d.fan_entries(base)
    unavailable = _m(_m(doc.get("plan")).get("unavailable"))
    tested = {s.get("channel_id") for s in doc.get("steps") or []}
    for ch in doc.get("channels") or []:
        cid = str(ch.get("channel_id") or "")
        name = names.get(cid, cid)
        fan = _m(fans.get(cid))
        if ch.get("source") != "hwmon":
            reason = next(iter(_m(unavailable.get(cid)).values()), "")
            out.add(
                "channel.read_only",
                f"{name}: reported read-only. {reason}".strip(),
                result=VALIDATION_RESULT_NOT_TESTED,
                provenance=PROVENANCE_OBSERVED,
                severity=SEVERITY_INFO,
                channel_id=cid,
                evidence=("snapshots.baseline.fans",),
            )
            continue
        if ch.get("pump_protected"):
            out.add(
                "channel.pump_floor",
                f"{name}: below 30 % not tested, by design — it is protected as a pump.",
                result=VALIDATION_RESULT_NOT_TESTED,
                provenance=PROVENANCE_DEVICE_METADATA,
                severity=SEVERITY_INFO,
                channel_id=cid,
                evidence=("snapshots.baseline.headers",),
            )
        rpm = _int(fan.get("rpm"))
        if rpm == 0 and cid not in tested and not ch.get("in_profile"):
            stall_note = (
                " Its 'stall detected' flag is not a stall: with no fan connected, 0 rpm under "
                "a non-zero duty is expected (PTR-k)."
                if fan.get("stall_detected") is True
                else ""
            )
            out.add(
                "channel.no_fan",
                f"{name}: no fan detected (inferred from 0 rpm).{stall_note}",
                result=VALIDATION_RESULT_NOT_OBSERVED,
                provenance=PROVENANCE_DERIVED,
                severity=SEVERITY_INFO,
                channel_id=cid,
                evidence=("snapshots.baseline.fans",),
            )
        mode = _int(fan.get("pwm_enable_mode"))
        if mode is not None and mode >= 2 and not ch.get("in_profile"):
            out.add(
                "channel.firmware_controlled",
                f"{name}: controlled by the motherboard firmware (pwm_enable {mode}) — a "
                "state, not a fault. No profile control names it.",
                result=VALIDATION_RESULT_OBSERVED,
                provenance=PROVENANCE_OBSERVED,
                severity=SEVERITY_INFO,
                channel_id=cid,
                evidence=("snapshots.baseline.fans",),
            )
        for test, reason in _m(unavailable.get(cid)).items():
            title = SPECS[test].title if test in SPECS else test
            out.add(
                f"{test}.not_offered",
                f"{title} on {name}: not offered. {reason}",
                result=VALIDATION_RESULT_NOT_TESTED,
                provenance=PROVENANCE_DERIVED,
                severity=SEVERITY_INFO,
                channel_id=cid,
            )


# ── Exposure ──────────────────────────────────────────────────────────────────


def _lowest_verified(steps: list[Mapping]) -> tuple[int | None, Mapping | None]:
    """The lowest duty any completed sweep saw the fan turning at, and the probe
    result for the same header, if one completed."""
    lowest: int | None = None
    probe: Mapping | None = None
    for step in steps:
        if step.get("status") != d.STEP_COMPLETE:
            continue
        result = _m(step.get("result"))
        if step.get("test") == TEST_SWEEP:
            for p in result.get("points") or []:
                p = _m(p)
                duty, rpm = _int(p.get("requested_pct")), _int(p.get("rpm_after"))
                if p.get("command_accepted") and duty is not None and rpm and rpm > 0:
                    lowest = duty if lowest is None else min(lowest, duty)
        elif step.get("test") == TEST_PROBE:
            probe = result
    return lowest, probe


def _exposure(out: _Collector, doc: Mapping, names: Mapping) -> None:
    profile = d.snapshot_body(_m(doc.get("snapshots")).get("baseline"), "profile")
    if not isinstance(profile, Mapping):
        return
    cfg_headers = _m(_m(doc.get("configuration")).get("headers"))
    by_channel: dict[str, list[Mapping]] = {}
    for step in doc.get("steps") or []:
        by_channel.setdefault(str(step.get("channel_id")), []).append(step)
    for cid, steps in sorted(by_channel.items()):
        hdr = _m(cfg_headers.get(cid))
        exposures = member_exposures(
            profile,
            cid,
            header_floor_pct=_int(hdr.get("effective_min_pwm_pct")),
            hard_floor=hdr.get("stop_permitted") is False,
        )
        if not exposures:
            continue
        name = names.get(cid, cid)
        lowest_verified, probe = _lowest_verified(steps)
        for ex in exposures:
            ev = ("snapshots.baseline.profile", *(f"steps.{s.get('step_id')}" for s in steps))
            if ex.lowest_pct is None:
                out.add(
                    "exposure.unbounded",
                    f"{name}: the lowest duty control '{ex.control_name}' can command could not "
                    f"be worked out — {ex.basis}.",
                    result=VALIDATION_RESULT_UNKNOWN,
                    provenance=PROVENANCE_DERIVED,
                    severity=SEVERITY_INFO,
                    channel_id=cid,
                    evidence=ev,
                )
                continue
            low = round(ex.lowest_pct)
            scope = f"from {ex.basis}, in the daemon's tuning order (offset, floor, stop)"
            outcome = str(_m(probe).get("outcome") or "") if probe else ""
            stall = _int(_m(probe).get("stall_duty_pct")) if probe else None
            restart = _int(_m(probe).get("restart_duty_pct")) if probe else None
            if outcome == "stall_and_restart_found" and restart is not None and low < restart:
                out.add(
                    "exposure.below_restart",
                    f"{name}: your profile can command {low} %; in the probe the fan stopped at "
                    f"{_pct(stall)} and restarted only at {restart} %. Below {restart} % a "
                    "stopped fan may not start again.",
                    result=VALIDATION_RESULT_OBSERVED,
                    provenance=PROVENANCE_DERIVED,
                    severity=SEVERITY_ATTENTION,
                    channel_id=cid,
                    scope=scope,
                    evidence=ev,
                )
            elif outcome == "did_not_restart_below_20" and stall is not None and low <= stall:
                out.add(
                    "exposure.below_restart",
                    f"{name}: your profile can command {low} %; in the probe the fan stopped at "
                    f"{stall} % and did not start again below 20 %.",
                    result=VALIDATION_RESULT_OBSERVED,
                    provenance=PROVENANCE_DERIVED,
                    severity=SEVERITY_ATTENTION,
                    channel_id=cid,
                    scope=scope,
                    evidence=ev,
                )
            elif outcome in (
                "stall_and_restart_found",
                "no_stall_down_to_0",
                "did_not_restart_below_20",
            ):
                out.add(
                    "exposure.covered",
                    f"{name}: your profile can command down to {low} %, which the probe covered.",
                    result=VALIDATION_RESULT_OBSERVED,
                    provenance=PROVENANCE_DERIVED,
                    severity=SEVERITY_INFO,
                    channel_id=cid,
                    scope=scope,
                    evidence=ev,
                )
            elif lowest_verified is not None and low < lowest_verified:
                out.add(
                    "exposure.untested",
                    f"{name}: your profile can command {low} %, below the lowest duty tested with "
                    f"the fan turning ({lowest_verified} %). Stall and restart below "
                    f"{lowest_verified} % were not tested.",
                    result=VALIDATION_RESULT_NOT_TESTED,
                    provenance=PROVENANCE_DERIVED,
                    severity=SEVERITY_OBSERVATION,
                    channel_id=cid,
                    scope=scope,
                    evidence=ev,
                )
            elif lowest_verified is None and low < _SWEEP_FLOOR_PCT:
                out.add(
                    "exposure.untested",
                    f"{name}: your profile can command {low} %. Stall and restart below "
                    f"{_SWEEP_FLOOR_PCT} % were not tested in this run.",
                    result=VALIDATION_RESULT_NOT_TESTED,
                    provenance=PROVENANCE_DERIVED,
                    severity=SEVERITY_OBSERVATION,
                    channel_id=cid,
                    scope=scope,
                    evidence=ev,
                )


# ── Configuration ─────────────────────────────────────────────────────────────


def _configuration(out: _Collector, doc: Mapping) -> None:
    profile = d.snapshot_body(_m(doc.get("snapshots")).get("baseline"), "profile")
    if not isinstance(profile, Mapping):
        cfg = _m(doc.get("configuration"))
        if not cfg.get("active_profile_id"):
            out.add(
                "config.no_profile",
                "No profile was active, so the daemon evaluated no curves during the run.",
                result=VALIDATION_RESULT_OBSERVED,
                provenance=PROVENANCE_OBSERVED,
                severity=SEVERITY_INFO,
            )
        return
    controls = [c for c in profile.get("controls") or [] if isinstance(c, Mapping)]
    for control in controls:
        if not control.get("members"):
            out.add(
                "config.empty_control",
                f"Control '{control.get('name') or control.get('id')}' has no members, so it "
                "drives nothing.",
                result=VALIDATION_RESULT_OBSERVED,
                provenance=PROVENANCE_OBSERVED,
                severity=SEVERITY_INFO,
                evidence=("snapshots.baseline.profile",),
            )
    unlimited = [
        c
        for c in controls
        if c.get("members")
        and float(c.get("step_up_pct", 100) or 0) >= 100
        and float(c.get("step_down_pct", 100) or 0) >= 100
    ]
    if unlimited and len(unlimited) == len([c for c in controls if c.get("members")]):
        out.add(
            "config.no_slew",
            "No control limits how fast its output may change (step up/down at 100 % per "
            "second), so a curve can move a fan from one extreme to the other in one tick.",
            result=VALIDATION_RESULT_OBSERVED,
            provenance=PROVENANCE_OBSERVED,
            severity=SEVERITY_INFO,
            evidence=("snapshots.baseline.profile",),
        )


# ── User facts ────────────────────────────────────────────────────────────────


def _user_facts(out: _Collector, doc: Mapping, names: Mapping, facts: Mapping) -> None:
    cooler = _m(_m(doc.get("user_facts")).get("cooler"))
    if not cooler.get("model") and not cooler.get("pump_switch") and not facts:
        out.add(
            "facts.none",
            "No physical setup facts were supplied, so no claim here accounts for splitters, "
            "hubs or a pump's own mode switch.",
            result=VALIDATION_RESULT_UNAVAILABLE,
            provenance=PROVENANCE_USER_METADATA,
            severity=SEVERITY_INFO,
        )
        return
    # A fact the user gave is recorded as theirs, never promoted to a measurement.
    for cid in sorted(facts):
        hf = header_facts_from(facts.get(cid))
        if hf.behind_splitter:
            out.add(
                "facts.splitter",
                f"{names.get(cid, cid)}: you said {hf.fans_behind} fans share this header. "
                "Only one tach can be wired through a splitter, so RPM describes one of them.",
                result=VALIDATION_RESULT_OBSERVED,
                provenance=PROVENANCE_USER_METADATA,
                severity=SEVERITY_INFO,
                channel_id=cid,
            )


def humanise_severity(severity: str) -> str:
    return {
        SEVERITY_ATTENTION: "Needs attention",
        SEVERITY_OBSERVATION: "Observation",
        SEVERITY_INFO: "Information",
    }.get(severity, severity)


def ordered(findings: list[Mapping]) -> Iterator[Mapping]:
    """Attention first, then observations, then the rest — stable within each."""
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    yield from sorted(findings, key=lambda f: rank.get(str(f.get("severity")), len(rank)))
