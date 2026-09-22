"""The in-app report, as a Qt-free view model (DEC-404; the project's
view-model + thin-renderer standard).

Everything the Report page shows is computed here from the saved document, so
a report reopened from disk renders exactly as it did when it finished, and
every wording rule is testable without a widget.

Layout, per the plan:

* summary — what was assessed and tested; attention, then observations, then
  not tested;
* one section per channel — facts, the test matrix, its findings, and a
  response chart for a sweep or the probe;
* restoration, environment and configuration;
* the evidence (the daemon's raw payloads) and the provenance legend.

D-c: the ``unknown`` result token reads **"Inconclusive"** here. The
validation-session vocabulary keeps its own "Unknown"; the report borrows the
tokens, not that one word (DEC-404: it answers a different question).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from control_ofc.api.models import (
    VALIDATION_RESULT_NOT_TESTED,
    VALIDATION_RESULT_UNKNOWN,
    parse_characterization_run,
    parse_stall_probe_run,
)
from control_ofc.services.characterization_view import (
    ResponseCurve,
    SeriesPoint,
    build_response_curve,
)
from control_ofc.services.provenance import PROVENANCE_LABELS
from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report import findings as fnd
from control_ofc.services.pwm_report.catalog import (
    SPECS,
    TEST_ORDER,
    TEST_PROBE,
    TEST_SWEEP,
)
from control_ofc.services.pwm_report.setup_facts import (
    NOT_SUPPLIED,
    bios_mode_label,
    connected_label,
    header_facts_from,
)
from control_ofc.services.validation_view import RESULT_LABELS, RESULT_TONES

UNKNOWN_TEXT = "—"

#: D-c. A copy with one word changed — never a mutation of the shared table.
REPORT_RESULT_LABELS: dict[str, str] = {
    **RESULT_LABELS,
    VALIDATION_RESULT_UNKNOWN: "Inconclusive",
}

REPORT_STATE_LABELS: dict[str, tuple[str, str]] = {
    d.STATE_IN_PROGRESS: ("In progress", "info"),
    d.STATE_COMPLETE: ("Complete", "ok"),
    d.STATE_CANCELLED: ("Cancelled", "muted"),
    d.STATE_INTERRUPTED: ("Interrupted", "warn"),
    d.STATE_ABORTED: ("Stopped early (safety)", "warn"),
}

STEP_STATUS_LABELS: dict[str, tuple[str, str]] = {
    d.STEP_PENDING: ("Waiting", "muted"),
    d.STEP_RUNNING: ("Running", "info"),
    d.STEP_COMPLETE: ("Done", "ok"),
    d.STEP_NOT_TESTED: ("Not tested", "muted"),
    d.STEP_CANCELLED: ("Cancelled", "muted"),
    d.STEP_ABORTED: ("Stopped early", "warn"),
    d.STEP_INTERRUPTED: ("Interrupted", "warn"),
    d.STEP_ERROR: ("No result", "warn"),
}

_SEVERITY_TONES = {
    fnd.SEVERITY_ATTENTION: "warn",
    fnd.SEVERITY_OBSERVATION: "info",
    fnd.SEVERITY_INFO: "muted",
}


def result_label(token: str) -> str:
    """Report wording for a result token; an unrecognised one renders verbatim."""
    return REPORT_RESULT_LABELS.get(token, token or UNKNOWN_TEXT)


def result_tone(token: str) -> str:
    return RESULT_TONES.get(token, "muted")


def step_status(status: str) -> tuple[str, str]:
    return STEP_STATUS_LABELS.get(status, (status or UNKNOWN_TEXT, "muted"))


@dataclass(frozen=True)
class FindingRow:
    finding_id: str
    rule: str
    statement: str
    scope: str
    result_label: str
    result_tone: str
    provenance_label: str
    severity: str
    severity_label: str
    severity_tone: str
    channel_id: str | None


@dataclass(frozen=True)
class TestRow:
    title: str
    status_label: str
    status_tone: str
    reason: str = ""


@dataclass(frozen=True)
class ChannelSection:
    channel_id: str
    name: str
    subtitle: str
    facts: list[tuple[str, str]] = field(default_factory=list)
    tests: list[TestRow] = field(default_factory=list)
    findings: list[FindingRow] = field(default_factory=list)
    sweep_curve: ResponseCurve | None = None
    probe_curve: ResponseCurve | None = None
    #: The probe's held duties, one row each, in walk order (see
    #: :data:`PROBE_COLUMNS`). Empty when no probe ran.
    probe_rows: list[tuple[str, ...]] = field(default_factory=list)


#: Columns of the probe's points table — every observation the daemon made at
#: each held duty, so a reader can check a stall/restart claim step by step.
PROBE_COLUMNS = (
    "Phase",
    "Commanded",
    "Accepted",
    "Readback",
    "Mode",
    "RPM before → after",
    "Held",
    "Confirmed at",
    "Samples (zero rpm)",
    "Observation",
)


@dataclass(frozen=True)
class ReportView:
    title: str
    report_id: str
    state_label: str
    state_tone: str
    state_reason: str
    when_line: str
    summary_lines: list[str]
    attention: list[FindingRow]
    observations: list[FindingRow]
    not_tested: list[FindingRow]
    restoration: list[FindingRow]
    channels: list[ChannelSection]
    environment: list[tuple[str, str]]
    configuration: list[tuple[str, str]]
    legend: list[tuple[str, str]]
    can_reapply: bool
    trace_note: str


def _m(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    if value is None or value == "":
        return UNKNOWN_TEXT
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def finding_row(f: Mapping) -> FindingRow:
    severity = str(f.get("severity") or "")
    prov = str(f.get("provenance") or "")
    token = str(f.get("result") or "")
    return FindingRow(
        finding_id=str(f.get("id") or ""),
        rule=str(f.get("rule") or ""),
        statement=str(f.get("statement") or ""),
        scope=str(f.get("scope") or ""),
        result_label=result_label(token),
        result_tone=result_tone(token),
        provenance_label=PROVENANCE_LABELS.get(prov, prov or UNKNOWN_TEXT),
        severity=severity,
        severity_label=fnd.humanise_severity(severity),
        severity_tone=_SEVERITY_TONES.get(severity, "muted"),
        channel_id=f.get("channel_id") if isinstance(f.get("channel_id"), str) else None,
    )


def probe_curve(run_body: object) -> ResponseCurve | None:
    """The probe's walk as a response chart: descent as the falling leg, the
    climb back as the rising leg. Baseline and kick points are not plotted —
    they are not part of the walk."""
    if not isinstance(run_body, Mapping):
        return None
    run = parse_stall_probe_run(dict(run_body))
    falling = [
        SeriesPoint(p.commanded_pct, p.rpm_after)
        for p in run.points
        if p.phase == "descent" and p.rpm_after is not None
    ]
    rising = [
        SeriesPoint(p.commanded_pct, p.rpm_after)
        for p in run.points
        if p.phase == "ascent" and p.rpm_after is not None
    ]
    if not falling and not rising:
        return None
    return ResponseCurve(
        rising=sorted(rising, key=lambda s: s.duty_pct),
        falling=sorted(falling, key=lambda s: s.duty_pct),
        has_data=True,
    )


def _ms(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value / 1000:.1f} s"


def probe_rows(run_body: object) -> list[tuple[str, ...]]:
    """One row per held duty, from the parsed run (every field, verbatim)."""
    if not isinstance(run_body, Mapping):
        return []
    run = parse_stall_probe_run(dict(run_body))
    rows: list[tuple[str, ...]] = []
    for p in sorted(run.points, key=lambda pt: pt.step_index):
        rows.append(
            (
                p.phase or UNKNOWN_TEXT,
                f"{p.commanded_pct} %",
                "yes" if p.command_accepted else "no",
                _pct_or_dash(p.readback_pct),
                _text(p.pwm_enable),
                f"{_text(p.rpm_before)} → {_text(p.rpm_after)}",
                _ms(p.held_ms),
                _ms(p.confirmed_at_ms),
                f"{p.samples} ({p.zero_samples})",
                p.observation or UNKNOWN_TEXT,
            )
        )
    return rows


def probe_facts(run_body: object) -> list[tuple[str, str]]:
    """The probe's derived timing — everything the daemon chose, so a later run
    can be judged against the same envelope (DEC-407 publishes no tunables)."""
    if not isinstance(run_body, Mapping):
        return []
    run = parse_stall_probe_run(dict(run_body))
    source = f" ({run.refresh_source})" if run.refresh_source else ""
    temps = UNKNOWN_TEXT
    if run.start_cpu_temp_c is not None:
        temps = f"{run.start_cpu_temp_c:.1f} °C at the start"
        if run.max_cpu_temp_c is not None:
            temps += f", {run.max_cpu_temp_c:.1f} °C at most"
        temps += f"; the probe stops at +{run.rise_limit_c:g} °C"
    return [
        ("Stall probe — tach refresh", f"{_text(run.refresh_ms)} ms{source}"),
        ("Stall probe — each step held at least", _ms(run.dwell_ms)),
        ("Stall probe — confirmation window", _ms(run.confirm_ms)),
        ("Stall probe — budget below 20 %", _ms(run.budget_ms)),
        ("Stall probe — time spent below 20 %", _ms(run.time_below_floor_ms)),
        ("Stall probe — lowest duty commanded", _pct_or_dash(run.lowest_commanded_pct)),
        (
            "Stall probe — fan at the 20 % baseline",
            f"{_text(run.baseline_rpm)} rpm"
            + ("" if run.baseline_settled is not False else " (had not settled)"),
        ),
        ("Stall probe — CPU temperature", temps),
    ]


def sweep_facts(run_body: object) -> list[tuple[str, str]]:
    """What the sweep actually used, as the daemon echoed it (S4-1): the points
    asked for and the settle, walk and dwell it resolved them to."""
    if not isinstance(run_body, Mapping):
        return []
    run = parse_characterization_run(dict(run_body))
    points = ", ".join(str(p) for p in run.requested_points_pct) or UNKNOWN_TEXT
    return [
        ("Full sweep — duties walked", f"{points} %"),
        ("Full sweep — settle per step (the daemon's)", f"{run.settle_seconds} s"),
        ("Full sweep — both directions", "yes" if run.bidirectional else "no"),
        (
            "Full sweep — stability dwell",
            f"{run.stability_seconds} s" if run.stability_seconds else "none",
        ),
    ]


def sweep_curve(run_body: object) -> ResponseCurve | None:
    if not isinstance(run_body, Mapping):
        return None
    curve = build_response_curve(parse_characterization_run(dict(run_body)))
    return curve if curve.has_data else None


def _channel_section(
    ch: Mapping, doc: Mapping, rows: list[FindingRow], facts_map: Mapping
) -> ChannelSection:
    cid = str(ch.get("channel_id") or "")
    source = str(ch.get("source") or "")
    bits = [source]
    if source == "hwmon":
        role = str(ch.get("role") or "unknown")
        role_src = str(ch.get("role_source") or "none")
        bits.append(f"role {role} ({role_src})")
        bits.append("writable" if ch.get("writable") else "read-only")
    if ch.get("in_profile"):
        bits.append("in the active profile")
    facts: list[tuple[str, str]] = [("Stable id", cid)]
    if source == "hwmon":
        facts.append(("Floor the daemon enforces", _pct_or_dash(ch.get("effective_min_pwm_pct"))))
        facts.append(("Protected as a pump", "yes" if ch.get("pump_protected") else "no"))
    hf = header_facts_from(facts_map.get(cid))
    if not hf.is_blank():
        facts.append(("You said — connected", connected_label(hf.connected)))
        facts.append(
            (
                "You said — fans on this header",
                str(hf.fans_behind) if hf.fans_behind else NOT_SUPPLIED,
            )
        )
        facts.append(("You said — BIOS header mode", bios_mode_label(hf.bios_mode)))
        if hf.notes:
            facts.append(("You said — notes", hf.notes))

    tests: list[TestRow] = []
    sweep: ResponseCurve | None = None
    probe: ResponseCurve | None = None
    rows_probe: list[tuple[str, ...]] = []
    if source == "hwmon" and ch.get("writable"):
        steps = {s.get("test"): s for s in doc.get("steps") or [] if s.get("channel_id") == cid}
        unavailable = _m(_m(_m(doc.get("plan")).get("unavailable")).get(cid))
        for test in TEST_ORDER:
            title = SPECS[test].title
            step = steps.get(test)
            if step is not None:
                label, tone = step_status(str(step.get("status") or ""))
                tests.append(TestRow(title, label, tone, str(step.get("reason") or "")))
                if test == TEST_SWEEP:
                    sweep = sweep_curve(step.get("result"))
                    facts.extend(sweep_facts(step.get("result")))
                elif test == TEST_PROBE:
                    probe = probe_curve(step.get("result"))
                    facts.extend(probe_facts(step.get("result")))
                    rows_probe = probe_rows(step.get("result"))
            elif test in unavailable:
                tests.append(TestRow(title, "Not offered", "muted", str(unavailable[test])))
            else:
                tests.append(TestRow(title, "Not selected", "muted"))
    return ChannelSection(
        channel_id=cid,
        name=str(ch.get("name") or cid),
        subtitle=" · ".join(bits),
        facts=facts,
        tests=tests,
        findings=[r for r in rows if r.channel_id == cid],
        sweep_curve=sweep,
        probe_curve=probe,
        probe_rows=rows_probe,
    )


def _pct_or_dash(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        return UNKNOWN_TEXT
    return f"{value} %"


def _environment_rows(env: Mapping) -> list[tuple[str, str]]:
    gui = _m(env.get("gui"))
    daemon = _m(env.get("daemon"))
    board = _m(env.get("board"))
    rows = [
        ("Control-OFC GUI", _text(gui.get("gui_version"))),
        ("Control-OFC daemon", _text(daemon.get("daemon_version"))),
        ("Daemon API", _text(daemon.get("api_version"))),
        ("Kernel (as the daemon read it)", _text(daemon.get("kernel_release"))),
        ("Kernel (as the GUI read it)", _text(gui.get("kernel"))),
        ("Board", " ".join(str(v) for v in (board.get("vendor"), board.get("name")) if v) or "—"),
        ("BIOS version", _text(board.get("bios_version"))),
        ("BIOS date", _text(board.get("bios_date"))),
        ("CPU vendor", _text(env.get("cpu_vendor"))),
        ("Python", _text(gui.get("python"))),
        ("Qt", _text(gui.get("qt"))),
    ]
    chips = [str(c.get("chip_name")) for c in env.get("chips") or [] if isinstance(c, Mapping)]
    rows.append(("Sensor chips", ", ".join(chips) or UNKNOWN_TEXT))
    for m in env.get("kernel_modules") or []:
        if not isinstance(m, Mapping) or not m.get("loaded"):
            continue
        parts = [f"version {m['version']}" if m.get("version") else "no version published"]
        if m.get("srcversion"):
            parts.append(f"srcversion {m['srcversion']}")
        if m.get("out_of_tree") is True:
            parts.append("built outside the kernel tree")
        rows.append((f"Module {m.get('name')}", ", ".join(parts)))
    return rows


def _configuration_rows(cfg: Mapping) -> list[tuple[str, str]]:
    name = cfg.get("active_profile_name")
    pid = cfg.get("active_profile_id")
    profile = f"{name} ({pid})" if name and pid else _text(name or pid)
    digest = cfg.get("profile_hash")
    return [
        ("Active profile", profile if (name or pid) else "none"),
        ("Profile content hash", str(digest)[:16] if digest else UNKNOWN_TEXT),
        (
            "Thermal emergency trip point (as the daemon reports it)",
            f"{cfg['emergency_threshold_c']} °C"
            if isinstance(cfg.get("emergency_threshold_c"), (int, float))
            else UNKNOWN_TEXT,
        ),
        ("Thermal state at the start", _text(cfg.get("thermal_state"))),
    ]


def _summary_lines(doc: Mapping) -> list[str]:
    channels = doc.get("channels") or []
    headers = [c for c in channels if c.get("source") == "hwmon"]
    other = len(channels) - len(headers)
    steps = doc.get("steps") or []
    ran = [s for s in steps if s.get("status") == d.STEP_COMPLETE]
    tested_headers = {s.get("channel_id") for s in ran}
    not_run = [s for s in steps if s.get("status") != d.STEP_COMPLETE]
    lines = [
        f"Assessed {len(channels)} channel(s): {len(headers)} motherboard header(s) and "
        f"{other} reported read-only.",
        f"{len(ran)} test(s) completed on {len(tested_headers)} header(s); "
        f"{len(not_run)} selected test(s) did not complete.",
    ]
    trace = _m(doc.get("trace"))
    if trace:
        lines.append(
            f"Recorded {trace.get('samples', 0)} one-second samples of every fan and sensor."
        )
    return lines


def build_report_view(doc: Mapping) -> ReportView:
    rows = [finding_row(f) for f in doc.get("findings") or [] if isinstance(f, Mapping)]
    attention = [r for r in rows if r.severity == fnd.SEVERITY_ATTENTION]
    observations = [r for r in rows if r.severity == fnd.SEVERITY_OBSERVATION]
    not_tested = [
        r
        for r in rows
        if r.result_label == result_label(VALIDATION_RESULT_NOT_TESTED)
        and r.severity == fnd.SEVERITY_INFO
    ]
    restoration = [r for r in rows if r.rule.startswith("final.")]
    state = str(doc.get("state") or "")
    state_label, state_tone = REPORT_STATE_LABELS.get(state, (state or UNKNOWN_TEXT, "muted"))
    facts_map = _m(_m(doc.get("user_facts")).get("headers"))
    channels = [
        _channel_section(c, doc, rows, facts_map)
        for c in doc.get("channels") or []
        if isinstance(c, Mapping)
    ]
    trace = _m(doc.get("trace"))
    trace_note = ""
    if trace.get("truncated"):
        minutes = round(int(trace.get("truncated_at_ms") or 0) / 60000)
        trace_note = (
            f"The trace stopped at its three-hour limit (after about {minutes} minutes); "
            "the tests and the final check continued."
        )
    started = doc.get("started_at") or UNKNOWN_TEXT
    finished = doc.get("finished_at") or "not finished"
    legend = [
        (PROVENANCE_LABELS.get(k, k), str(v)) for k, v in _m(doc.get("provenance_legend")).items()
    ]
    return ReportView(
        title="PWM Test Report",
        report_id=str(doc.get("report_id") or ""),
        state_label=state_label,
        state_tone=state_tone,
        state_reason=str(doc.get("state_reason") or ""),
        when_line=f"Started {started} · finished {finished} (UTC)",
        summary_lines=_summary_lines(doc),
        attention=attention,
        observations=observations,
        not_tested=not_tested,
        restoration=restoration,
        channels=channels,
        environment=_environment_rows(_m(doc.get("environment"))),
        configuration=_configuration_rows(_m(doc.get("configuration"))),
        legend=legend,
        can_reapply=bool(_m(doc.get("actions")).get("reapply_profile")),
        trace_note=trace_note,
    )


def evidence_text(doc: Mapping) -> str:
    """The daemon's raw payloads for the evidence disclosure: both snapshots and
    every step, verbatim. The trace is left out — it is data for a chart, not
    something to read — and is in the saved file."""
    evidence = {
        "report_id": doc.get("report_id"),
        "snapshots": doc.get("snapshots"),
        "steps": doc.get("steps"),
    }
    return json.dumps(evidence, indent=2, sort_keys=False, default=str)
