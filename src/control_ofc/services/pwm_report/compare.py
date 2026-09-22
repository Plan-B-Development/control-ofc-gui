"""Comparing two PWM Test Reports (DEC-404 Stage 5), as a Qt-free view model.

The rules, from the plan and the user's Stage 5 decisions:

* **Channels pair by stable id only.** An id present in one report and not the
  other is listed on its side. A *possible rename* — same chip, device and
  ``pwmN``, different label — is shown as an inference and **never** paired, so
  a measurement is never attributed to a header it may not belong to.
* **Every difference goes in exactly one category**: hardware/wiring,
  environment, configuration, response, or *not comparable*.
* **A response is compared only like for like.** Different test parameters, a
  test on one side only, a step that did not complete, or a different thermal
  state at the start put it under *not comparable*, with the reason (S5-5).
* **Start temperature is shown, never judged** (S5-5): both values and the
  difference, with no threshold that would call one run "warmer".
* **Deltas are numbers beside each run's own spread** (Stage 1's settled
  statistics). No significance threshold is invented, and there is no verdict.

Reports are ordered earlier → later by ``started_at``; every ``delta`` is
*later minus earlier*.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from control_ofc.api.models import (
    CharPoint,
    parse_characterization_run,
    parse_stall_probe_run,
)
from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report.catalog import (
    SPECS,
    TEST_ORDER,
    TEST_PAIRING,
    TEST_PROBE,
    TEST_SWEEP,
    TEST_VERIFY,
)
from control_ofc.services.pwm_report.view import REPORT_STATE_LABELS, step_status

CATEGORY_HARDWARE = "hardware"
CATEGORY_ENVIRONMENT = "environment"
CATEGORY_CONFIGURATION = "configuration"
CATEGORY_RESPONSE = "response"
CATEGORY_NOT_COMPARABLE = "not_comparable"

CATEGORY_ORDER = (
    CATEGORY_HARDWARE,
    CATEGORY_ENVIRONMENT,
    CATEGORY_CONFIGURATION,
    CATEGORY_RESPONSE,
    CATEGORY_NOT_COMPARABLE,
)
CATEGORY_TITLES: Mapping[str, str] = {
    CATEGORY_HARDWARE: "Hardware and wiring",
    CATEGORY_ENVIRONMENT: "Environment",
    CATEGORY_CONFIGURATION: "Configuration",
    CATEGORY_RESPONSE: "Measured response",
    CATEGORY_NOT_COMPARABLE: "Not comparable",
}

DASH = "—"


@dataclass(frozen=True)
class Difference:
    category: str
    subject: str
    earlier: str
    later: str
    #: ``later - earlier`` with its unit, or empty for a non-numeric value.
    delta: str = ""
    #: The spread each run measured, or why the item is not comparable.
    note: str = ""
    channel_id: str | None = None


@dataclass(frozen=True)
class RunSide:
    report_id: str
    started_at: str
    machine: str
    state_label: str
    gui_version: str
    daemon_version: str


@dataclass(frozen=True)
class Comparison:
    earlier: RunSide
    later: RunSide
    paired: list[tuple[str, str]]
    only_earlier: list[tuple[str, str]]
    only_later: list[tuple[str, str]]
    #: ``(earlier id, later id)`` — shown, never paired.
    possible_renames: list[tuple[str, str]]
    start_conditions: list[Difference]
    differences: list[Difference]
    #: Per category, how many compared values were identical (not listed).
    same_counts: dict[str, int] = field(default_factory=dict)

    def in_category(self, category: str) -> list[Difference]:
        return [x for x in self.differences if x.category == category]


# ── Small readers ─────────────────────────────────────────────────────────────


def _m(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    if value is None or value == "":
        return DASH
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _delta(a: object, b: object, unit: str) -> str:
    if not (_is_num(a) and _is_num(b)):
        return ""
    diff = float(b) - float(a)  # type: ignore[arg-type]
    both_int = isinstance(a, int) and isinstance(b, int)
    body = f"{int(diff):+d}" if both_int else f"{diff:+.1f}"
    body = body.replace("-", "\u2212")  # a typographic minus, as the app writes one
    return f"{body} {unit}".rstrip()


def _with_unit(value: object, unit: str) -> str:
    text = _text(value)
    return text if text == DASH or not unit else f"{text} {unit}"


def _side(doc: Mapping) -> RunSide:
    env = _m(doc.get("environment"))
    state = str(doc.get("state") or "")
    return RunSide(
        report_id=str(doc.get("report_id") or ""),
        started_at=str(doc.get("started_at") or DASH),
        machine=d.machine_summary(doc),
        state_label=REPORT_STATE_LABELS.get(state, (state or DASH, ""))[0],
        gui_version=_text(_m(env.get("gui")).get("gui_version")),
        daemon_version=_text(_m(env.get("daemon")).get("daemon_version")),
    )


def _channels(doc: Mapping) -> dict[str, Mapping]:
    return {
        str(c.get("channel_id")): c
        for c in doc.get("channels") or []
        if isinstance(c, Mapping) and c.get("channel_id")
    }


def rename_key(channel_id: str) -> str | None:
    """``hwmon:chip:device:pwmN`` — the stable id without its label, or ``None``
    for an id that is not an hwmon header id."""
    parts = channel_id.split(":", 4)
    if len(parts) != 5 or parts[0] != "hwmon" or not parts[3].startswith("pwm"):
        return None
    return ":".join(parts[:4])


def _steps_by_key(doc: Mapping) -> dict[tuple[str, str], Mapping]:
    return {
        (str(s.get("channel_id")), str(s.get("test"))): s
        for s in doc.get("steps") or []
        if isinstance(s, Mapping)
    }


def hottest_cpu_at_start(doc: Mapping) -> float | None:
    """The hottest ``cpu_temp`` sensor in the baseline snapshot — the input the
    daemon's thermal safety acts on (CLAUDE.md), so the same one is shown here."""
    baseline = _m(doc.get("snapshots")).get("baseline")
    sensors = _m(d.snapshot_body(baseline, "sensors")).get("sensors")
    values = [
        float(s["value_c"])
        for s in sensors or []
        if isinstance(s, Mapping) and s.get("kind") == "cpu_temp" and _is_num(s.get("value_c"))
    ]
    return max(values) if values else None


# ── Builder ───────────────────────────────────────────────────────────────────


class _Collector:
    def __init__(self) -> None:
        self.items: list[Difference] = []
        self.same: dict[str, int] = {}

    def value(
        self,
        category: str,
        subject: str,
        a: object,
        b: object,
        *,
        unit: str = "",
        channel_id: str | None = None,
    ) -> None:
        """A configuration-style value: listed only when it differs."""
        if a == b:
            self.same[category] = self.same.get(category, 0) + 1
            return
        self.items.append(
            Difference(
                category,
                subject,
                _with_unit(a, unit),
                _with_unit(b, unit),
                _delta(a, b, unit),
                channel_id=channel_id,
            )
        )

    def add(self, diff: Difference) -> None:
        self.items.append(diff)


def _hardware(col: _Collector, a: Mapping, b: Mapping, paired: Sequence[str]) -> None:
    def chips(doc: Mapping) -> str:
        found = sorted(
            f"{c.get('chip_name')} ({c.get('device_id')})"
            for c in _m(doc.get("environment")).get("chips") or []
            if isinstance(c, Mapping)
        )
        return ", ".join(found) or DASH

    col.value(CATEGORY_HARDWARE, "Sensor chips", chips(a), chips(b))
    cooler_a = _m(_m(a.get("user_facts")).get("cooler"))
    cooler_b = _m(_m(b.get("user_facts")).get("cooler"))
    for key, label in (("model", "Cooler model (you said)"), ("pump_switch", "Pump mode switch")):
        col.value(CATEGORY_HARDWARE, label, cooler_a.get(key), cooler_b.get(key))
    facts_a = _m(_m(a.get("user_facts")).get("headers"))
    facts_b = _m(_m(b.get("user_facts")).get("headers"))
    names = _channels(b)
    for cid in paired:
        fa, fb = _m(facts_a.get(cid)), _m(facts_b.get(cid))
        if not fa and not fb:
            continue
        name = str(names.get(cid, {}).get("name") or cid)
        for key, label in (
            ("connected", "connected"),
            ("fans_behind", "fans on the header"),
            ("bios_mode", "BIOS header mode"),
            ("notes", "notes"),
        ):
            col.value(
                CATEGORY_HARDWARE,
                f"{name} — {label} (you said)",
                fa.get(key),
                fb.get(key),
                channel_id=cid,
            )


def _environment(col: _Collector, a: Mapping, b: Mapping) -> None:
    ea, eb = _m(a.get("environment")), _m(b.get("environment"))
    for group, key, label in (
        ("gui", "gui_version", "Control-OFC GUI"),
        ("daemon", "daemon_version", "Control-OFC daemon"),
        ("daemon", "api_version", "Daemon API"),
        ("daemon", "kernel_release", "Kernel (as the daemon read it)"),
        ("gui", "kernel", "Kernel (as the GUI read it)"),
        ("board", "vendor", "Board vendor"),
        ("board", "name", "Board"),
        ("board", "bios_version", "BIOS version"),
        ("board", "bios_date", "BIOS date"),
        ("gui", "python", "Python"),
        ("gui", "qt", "Qt"),
    ):
        col.value(
            CATEGORY_ENVIRONMENT, label, _m(ea.get(group)).get(key), _m(eb.get(group)).get(key)
        )
    col.value(CATEGORY_ENVIRONMENT, "CPU vendor", ea.get("cpu_vendor"), eb.get("cpu_vendor"))

    def modules(env: Mapping) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in env.get("kernel_modules") or []:
            if isinstance(m, Mapping) and m.get("loaded") and m.get("name"):
                ver = m.get("version") or "no version published"
                src = f", srcversion {m['srcversion']}" if m.get("srcversion") else ""
                out[str(m["name"])] = f"{ver}{src}"
        return out

    ma, mb = modules(ea), modules(eb)
    for name in sorted(set(ma) | set(mb)):
        col.value(
            CATEGORY_ENVIRONMENT,
            f"Module {name}",
            ma.get(name, "not loaded"),
            mb.get(name, "not loaded"),
        )


def _configuration(col: _Collector, a: Mapping, b: Mapping, paired: Sequence[str]) -> None:
    ca, cb = _m(a.get("configuration")), _m(b.get("configuration"))
    for key, label, unit in (
        ("active_profile_id", "Active profile id", ""),
        ("active_profile_name", "Active profile name", ""),
        ("profile_hash", "Active profile content", ""),
        ("emergency_threshold_c", "Thermal emergency trip point (as reported)", "°C"),
        ("release_threshold_c", "Thermal release point (as reported)", "°C"),
    ):
        va, vb = ca.get(key), cb.get(key)
        if key == "profile_hash":
            va = str(va)[:16] if va else None
            vb = str(vb)[:16] if vb else None
        col.value(CATEGORY_CONFIGURATION, label, va, vb, unit=unit)

    def canon(value: object) -> str | None:
        if value is None:
            return None
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    devices_a, devices_b = canon(ca.get("cooling_devices")), canon(cb.get("cooling_devices"))
    if devices_a == devices_b:
        col.same[CATEGORY_CONFIGURATION] = col.same.get(CATEGORY_CONFIGURATION, 0) + 1
    else:
        col.add(
            Difference(
                CATEGORY_CONFIGURATION,
                "Cooling devices",
                "recorded" if devices_a else DASH,
                "recorded" if devices_b else DASH,
                note="The declared cooling devices differ; the JSON export holds both.",
            )
        )
    ha, hb = _m(ca.get("headers")), _m(cb.get("headers"))
    names = _channels(b)
    for cid in paired:
        xa, xb = _m(ha.get(cid)), _m(hb.get(cid))
        if not xa and not xb:
            continue
        name = str(names.get(cid, {}).get("name") or cid)
        for key, label, unit in (
            ("role", "role", ""),
            ("role_source", "role source", ""),
            ("effective_min_pwm_pct", "floor the daemon enforces", "%"),
            ("stop_permitted", "may be stopped", ""),
            ("is_writable", "writable", ""),
            ("cooling_device_id", "cooling device", ""),
        ):
            col.value(
                CATEGORY_CONFIGURATION,
                f"{name} — {label}",
                xa.get(key),
                xb.get(key),
                unit=unit,
                channel_id=cid,
            )


# ── Response ──────────────────────────────────────────────────────────────────


def _parameters(step: Mapping) -> dict[str, object]:
    """What a test was asked to do, plus what the daemon echoed back — the
    values two runs must share before their results mean the same thing."""
    out: dict[str, object] = {}
    for key, value in _m(step.get("request")).items():
        out[f"request.{key}"] = value
    result = _m(step.get("result"))
    test = step.get("test")
    if test == TEST_SWEEP:
        for key in ("requested_points_pct", "settle_seconds", "bidirectional", "stability_seconds"):
            out[key] = result.get(key)
    elif test == TEST_PAIRING:
        for key in ("delta_pct", "requested_cycles", "window_seconds"):
            out[key] = result.get(key)
    elif test == TEST_VERIFY:
        out["test_pwm_percent"] = result.get("test_pwm_percent")
    return out


def _not_comparable_reason(
    sa: Mapping | None, sb: Mapping | None, thermal_differs: str
) -> str | None:
    if sa is None or sb is None:
        return "ran in the earlier report only" if sb is None else "ran in the later report only"
    for label, step in (("earlier", sa), ("later", sb)):
        status = str(step.get("status") or "")
        if status != d.STEP_COMPLETE:
            return f"did not complete in the {label} report ({step_status(status)[0].lower()})"
    pa, pb = _parameters(sa), _parameters(sb)
    if pa != pb:
        changed = [
            f"{k}: {_text(pa.get(k))} → {_text(pb.get(k))}"
            for k in sorted(set(pa) | set(pb))
            if pa.get(k) != pb.get(k)
        ]
        return "different test parameters — " + "; ".join(changed)
    if thermal_differs:
        return thermal_differs
    return None


def _num_row(
    subject: str,
    a: object,
    b: object,
    unit: str,
    cid: str,
    note: str = "",
) -> Difference:
    return Difference(
        CATEGORY_RESPONSE,
        subject,
        _with_unit(a, unit),
        _with_unit(b, unit),
        _delta(a, b, unit),
        note,
        cid,
    )


def _spread(point: CharPoint) -> str:
    s = point.stability
    if s is None:
        return "no spread recorded"
    bits = []
    if s.min_rpm is not None and s.max_rpm is not None:
        bits.append(f"{s.min_rpm}\u2013{s.max_rpm} rpm")
    if s.cv_pct is not None:
        bits.append(f"CV {s.cv_pct:.1f} %")
    if s.usable:
        bits.append(f"{s.usable} samples")
    return ", ".join(bits) or "no spread recorded"


def _point_rpm(point: CharPoint) -> int | None:
    """The settled median where the daemon measured one, else the last reading."""
    if point.stability is not None and point.stability.median_rpm is not None:
        return point.stability.median_rpm
    return point.rpm_after


def _sweep_rows(name: str, cid: str, ra: Mapping, rb: Mapping) -> list[Difference]:
    run_a = parse_characterization_run(dict(ra))
    run_b = parse_characterization_run(dict(rb))
    rows: list[Difference] = []
    sa, sb = run_a.summary, run_b.summary
    if sa is not None and sb is not None:
        for key, label, unit in (
            ("min_rpm", "lowest RPM", "rpm"),
            ("max_rpm", "highest RPM", "rpm"),
            ("min_responsive_pct", "lowest duty that moved RPM", "%"),
            ("max_responsive_pct", "highest duty that moved RPM", "%"),
            ("hysteresis_pct", "largest rising/falling gap", "%"),
            ("worst_cv_pct", "worst per-point spread (CV)", "%"),
        ):
            rows.append(
                _num_row(
                    f"{name} — full sweep: {label}",
                    getattr(sa, key),
                    getattr(sb, key),
                    unit,
                    cid,
                )
            )
        for key, label in (("monotonic", "RPM rises with duty"),):
            rows.append(
                Difference(
                    CATEGORY_RESPONSE,
                    f"{name} — full sweep: {label}",
                    _text(getattr(sa, key)),
                    _text(getattr(sb, key)),
                    channel_id=cid,
                )
            )

    def by_key(run) -> dict[tuple[str, int], CharPoint]:
        return {(p.direction or "", p.requested_pct): p for p in run.points}

    pa, pb = by_key(run_a), by_key(run_b)
    for key in sorted(set(pa) & set(pb), key=lambda k: (k[0] != "falling", k[0], -k[1])):
        a, b = pa[key], pb[key]
        leg = key[0] or "walk"
        rows.append(
            _num_row(
                f"{name} — {key[1]} % ({leg})",
                _point_rpm(a),
                _point_rpm(b),
                "rpm",
                cid,
                f"earlier: {_spread(a)} · later: {_spread(b)}",
            )
        )
    return rows


def _probe_rows(name: str, cid: str, ra: Mapping, rb: Mapping) -> list[Difference]:
    a = parse_stall_probe_run(dict(ra))
    b = parse_stall_probe_run(dict(rb))
    rows = [
        Difference(
            CATEGORY_RESPONSE,
            f"{name} — stall probe: outcome",
            _text(a.outcome),
            _text(b.outcome),
            channel_id=cid,
        )
    ]
    for key, label, unit in (
        ("stall_duty_pct", "stopped at or below", "%"),
        ("restart_duty_pct", "restarted at", "%"),
        ("lowest_commanded_pct", "lowest duty commanded", "%"),
        ("baseline_rpm", "RPM at the 20 % baseline", "rpm"),
        ("refresh_ms", "tach refresh", "ms"),
    ):
        rows.append(
            _num_row(f"{name} — stall probe: {label}", getattr(a, key), getattr(b, key), unit, cid)
        )
    return rows


def _verify_rows(name: str, cid: str, ra: Mapping, rb: Mapping) -> list[Difference]:
    return [
        Difference(
            CATEGORY_RESPONSE,
            f"{name} — verify: result",
            _text(ra.get("result")),
            _text(rb.get("result")),
            channel_id=cid,
        ),
        _num_row(
            f"{name} — verify: RPM at the test duty",
            _m(ra.get("final_state")).get("rpm"),
            _m(rb.get("final_state")).get("rpm"),
            "rpm",
            cid,
        ),
    ]


def _pairing_rows(name: str, cid: str, ra: Mapping, rb: Mapping) -> list[Difference]:
    sa, sb = _m(ra.get("summary")), _m(rb.get("summary"))

    def top(summary: Mapping) -> tuple[object, object]:
        cands = [c for c in summary.get("candidates") or [] if isinstance(c, Mapping)]
        return (cands[0].get("tach_id"), cands[0].get("change_pct")) if cands else (None, None)

    (ta, ca), (tb, cb) = top(sa), top(sb)
    return [
        Difference(
            CATEGORY_RESPONSE,
            f"{name} — tach pairing: relationship",
            _text(sa.get("relationship")),
            _text(sb.get("relationship")),
            channel_id=cid,
        ),
        Difference(
            CATEGORY_RESPONSE,
            f"{name} — tach pairing: confidence",
            _text(sa.get("confidence")),
            _text(sb.get("confidence")),
            channel_id=cid,
        ),
        Difference(
            CATEGORY_RESPONSE,
            f"{name} — tach pairing: strongest tach",
            _text(ta),
            _text(tb),
            channel_id=cid,
        ),
        _num_row(f"{name} — tach pairing: its change", ca, cb, "%", cid),
    ]


_RESPONSE: Mapping[str, Callable[[str, str, Mapping, Mapping], list[Difference]]] = {
    TEST_VERIFY: _verify_rows,
    TEST_PAIRING: _pairing_rows,
    TEST_SWEEP: _sweep_rows,
    TEST_PROBE: _probe_rows,
}


def _response(col: _Collector, a: Mapping, b: Mapping, paired: Sequence[str]) -> None:
    steps_a, steps_b = _steps_by_key(a), _steps_by_key(b)
    ta = _m(a.get("configuration")).get("thermal_state")
    tb = _m(b.get("configuration")).get("thermal_state")
    thermal_differs = (
        f"the thermal state at the start differed ({_text(ta)} → {_text(tb)})" if ta != tb else ""
    )
    names = _channels(b)
    for cid in paired:
        name = str(names.get(cid, {}).get("name") or cid)
        for test in TEST_ORDER:
            sa, sb = steps_a.get((cid, test)), steps_b.get((cid, test))
            if sa is None and sb is None:
                continue
            reason = _not_comparable_reason(sa, sb, thermal_differs)
            title = SPECS[test].title
            if reason is not None:
                col.add(
                    Difference(
                        CATEGORY_NOT_COMPARABLE,
                        f"{name} — {title}",
                        step_status(str((sa or {}).get("status") or ""))[0] if sa else "not run",
                        step_status(str((sb or {}).get("status") or ""))[0] if sb else "not run",
                        note=reason,
                        channel_id=cid,
                    )
                )
                continue
            if sa is None or sb is None:  # unreachable: the reason covers it
                continue
            for row in _RESPONSE[test](name, cid, _m(sa.get("result")), _m(sb.get("result"))):
                col.add(row)


def _start_conditions(a: Mapping, b: Mapping) -> list[Difference]:
    """S5-5: shown side by side with the difference; never flagged."""
    rows = [
        Difference(
            CATEGORY_NOT_COMPARABLE,
            "Thermal state at the start",
            _text(_m(a.get("configuration")).get("thermal_state")),
            _text(_m(b.get("configuration")).get("thermal_state")),
        )
    ]
    ca, cb = hottest_cpu_at_start(a), hottest_cpu_at_start(b)
    rows.append(
        Difference(
            CATEGORY_NOT_COMPARABLE,
            "Hottest CPU temperature at the start",
            _with_unit(ca, "°C"),
            _with_unit(cb, "°C"),
            _delta(ca, cb, "°C"),
        )
    )
    return rows


def compare_reports(first: Mapping, second: Mapping) -> Comparison:
    """Compare two reports, earlier first by ``started_at``."""
    a, b = (
        (first, second)
        if str(first.get("started_at") or "") <= str(second.get("started_at") or "")
        else (second, first)
    )
    ch_a, ch_b = _channels(a), _channels(b)
    paired_ids = sorted(set(ch_a) & set(ch_b))
    only_a = sorted(set(ch_a) - set(ch_b))
    only_b = sorted(set(ch_b) - set(ch_a))

    def named(ids: Sequence[str], chans: Mapping[str, Mapping]) -> list[tuple[str, str]]:
        return [(cid, str(chans[cid].get("name") or cid)) for cid in ids]

    later_by_key: dict[str, list[str]] = {}
    for y in only_b:
        key = rename_key(y)
        if key is not None:
            later_by_key.setdefault(key, []).append(y)
    renames = [
        (x, y)
        for x in only_a
        if (key := rename_key(x)) is not None
        for y in later_by_key.get(key, [])
    ]
    col = _Collector()
    _hardware(col, a, b, paired_ids)
    _environment(col, a, b)
    _configuration(col, a, b, paired_ids)
    _response(col, a, b, paired_ids)
    return Comparison(
        earlier=_side(a),
        later=_side(b),
        paired=named(paired_ids, ch_b),
        only_earlier=named(only_a, ch_a),
        only_later=named(only_b, ch_b),
        possible_renames=renames,
        start_conditions=_start_conditions(a, b),
        differences=col.items,
        same_counts=dict(col.same),
    )
