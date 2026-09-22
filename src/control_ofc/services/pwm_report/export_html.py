"""HTML export of a PWM Test Report and of a comparison (DEC-404 Stage 5).

**Self-contained.** One file: inline CSS, inline SVG charts, no external asset,
no script, no web font. A ``Content-Security-Policy`` meta tag forbids scripts
and every remote load as well, so a file shared around cannot be made to reach
out even by something slipped past the escaping.

**Escaped** (DEC-106). Every dynamic string — a label, an alias, a note, a
daemon token, the evidence — goes through :func:`html.escape`. Charts are built
by :mod:`svg_chart`, which escapes its own text.

**Themed from the app's tokens**, never literals: the dark theme is the default
and the bundled light preset applies under ``prefers-color-scheme: light``.

Content comes from the same view models as the window (``build_report_view``,
``compare_reports``), plus, per S5-3, a trace chart for each tested header and
one for the hottest CPU temperature, and per S5-4 the raw evidence in a closed
``<details>``.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence

from control_ofc.constants import APP_VERSION
from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report import svg_chart as svg
from control_ofc.services.pwm_report.compare import (
    CATEGORY_NOT_COMPARABLE,
    CATEGORY_ORDER,
    CATEGORY_TITLES,
    Comparison,
    Difference,
)
from control_ofc.services.pwm_report.view import (
    PROBE_COLUMNS,
    ChannelSection,
    FindingRow,
    build_report_view,
    evidence_text,
)

_COLOUR = re.compile(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})")

#: CSS variable → theme token. The series tokens are indexed into
#: ``chart_series``.
_TOKEN_VARS = (
    ("bg", "app_bg"),
    ("surface", "surface_1"),
    ("surface2", "surface_2"),
    ("text", "text_primary"),
    ("muted", "text_secondary"),
    ("border", "border_default"),
    ("ok", "status_ok"),
    ("warn", "status_warn"),
    ("crit", "status_crit"),
    ("info", "status_info"),
    ("chart-bg", "chart_bg"),
    ("grid", "chart_grid"),
    ("axis", "chart_axis_text"),
)


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _vars(tokens: object) -> str:
    """``--name: value;`` lines for one theme. A value that is not a plain hex
    colour is skipped: the stylesheet must not carry anything but a colour."""
    lines: list[str] = []
    for var, attr in _TOKEN_VARS:
        value = getattr(tokens, attr, "")
        if isinstance(value, str) and _COLOUR.fullmatch(value):
            lines.append(f"--{var}: {value};")
    series = getattr(tokens, "chart_series", []) or []
    for i, value in enumerate(series[:4], start=1):
        if isinstance(value, str) and _COLOUR.fullmatch(value):
            lines.append(f"--s{i}: {value};")
    return " ".join(lines)


def _themes() -> tuple[object, object]:
    """The default dark theme and the bundled light preset (dark again if the
    preset is missing — the export never fails over a theme)."""
    from control_ofc.ui.theme import bundled_themes_dir, default_dark_theme, load_theme

    dark = default_dark_theme()
    try:
        light = load_theme(bundled_themes_dir() / "solar_light.json")
    except (OSError, ValueError):
        light = dark
    return dark, light


def stylesheet() -> str:
    dark, light = _themes()
    return (
        f":root {{ {_vars(dark)} color-scheme: dark light; }}\n"
        f"@media (prefers-color-scheme: light) {{ :root {{ {_vars(light)} }} }}\n"
        "body { background: var(--bg); color: var(--text); margin: 0 auto; max-width: 980px;"
        " padding: 16px; font: 15px/1.5 system-ui, -apple-system, 'Segoe UI', sans-serif; }\n"
        "h1, h2, h3 { line-height: 1.25; } h2 { margin-top: 28px;"
        " border-bottom: 1px solid var(--border); padding-bottom: 4px; }\n"
        ".meta { color: var(--muted); font-size: 0.9em; }\n"
        ".card { background: var(--surface); border: 1px solid var(--border);"
        " border-radius: 8px; padding: 8px 12px; margin: 8px 0; }\n"
        ".pill { display: inline-block; border-radius: 999px; padding: 1px 10px;"
        " font-size: 0.85em; border: 1px solid currentColor; margin-right: 6px; }\n"
        ".tone-ok { color: var(--ok); } .tone-warn { color: var(--warn); }"
        " .tone-bad { color: var(--crit); } .tone-info { color: var(--info); }"
        " .tone-muted { color: var(--muted); }\n"
        "table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 0.92em; }\n"
        "th, td { border: 1px solid var(--border); padding: 4px 8px; text-align: left;"
        " vertical-align: top; overflow-wrap: anywhere; }\n"
        "th { background: var(--surface2); }\n"
        ".scroll { overflow-x: auto; }\n"
        "details { margin: 8px 0; } summary { cursor: pointer; font-weight: 600; }\n"
        "pre { background: var(--surface); border: 1px solid var(--border); padding: 8px;"
        " overflow-x: auto; font-size: 0.8em; white-space: pre-wrap; overflow-wrap: anywhere; }\n"
        "svg.chart { display: block; max-width: 100%; height: auto; margin: 8px 0; }\n"
        ".plot-bg { fill: var(--chart-bg); } .grid { stroke: var(--grid); stroke-width: 1; }\n"
        ".axis, .axis-label, .legend { fill: var(--axis); font-size: 11px; }\n"
        ".legend { fill: var(--text); }\n"
        "polyline { fill: none; stroke-width: 1.6; }\n"
        ".series-fall, .series-rpm { stroke: var(--s1); } .series-rise, .series-duty"
        " { stroke: var(--s3); } .series-temp { stroke: var(--s4); }\n"
        ".dot { stroke: none; } .series-fall.dot, .series-rpm.dot { fill: var(--s1); }"
        " .series-rise.dot, .series-duty.dot { fill: var(--s3); }"
        " .series-temp.dot { fill: var(--s4); }\n"
        ".shade-thermal { fill: var(--warn); fill-opacity: 0.18; }\n"
        ".shade-thermal-key { stroke: var(--warn); stroke-width: 8; stroke-opacity: 0.4; }\n"
        "@media (max-width: 600px) { body { padding: 12px 16px; } }\n"
    )


def _page(title: str, body: Iterable[str]) -> str:
    return "".join(
        [
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n',
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            "style-src 'unsafe-inline'; img-src data:\">\n",
            f"<title>{_e(title)}</title>\n<style>\n{stylesheet()}</style>\n</head>\n<body>\n",
            *body,
            f'<p class="meta">Exported by Control-OFC GUI {_e(APP_VERSION)}.</p>\n',
            "</body>\n</html>\n",
        ]
    )


def _pill(label: str, tone: str) -> str:
    return f'<span class="pill tone-{_e(tone)}">{_e(label)}</span>'


def _table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    out = ['<div class="scroll"><table><thead><tr>']
    out += [f"<th>{_e(h)}</th>" for h in header]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{_e(v)}</td>" for v in row) + "</tr>")
    out.append("</tbody></table></div>\n")
    return "".join(out)


def _finding(row: FindingRow) -> str:
    meta = [f"Source: {row.provenance_label}"]
    if row.scope:
        meta.insert(0, f"Scope: {row.scope}")
    return (
        f'<div class="card">{_pill(row.result_label, row.result_tone)}{_e(row.statement)}'
        f'<div class="meta">{_e(" · ".join(meta))}</div></div>\n'
    )


def _findings(title: str, rows: Sequence[FindingRow]) -> str:
    parts = [f"<h2>{_e(title)} ({len(rows)})</h2>\n"]
    if not rows:
        parts.append('<p class="meta">Nothing here.</p>\n')
    parts += [_finding(r) for r in rows]
    return "".join(parts)


def _channel(section: ChannelSection) -> str:
    parts = [
        f"<details open><summary>{_e(section.name)}</summary>\n",
        f'<p class="meta">{_e(section.subtitle)}</p>\n',
        _table(("Fact", "Value"), section.facts),
    ]
    if section.tests:
        parts.append(
            _table(
                ("Test", "Status", "Reason"),
                ((t.title, t.status_label, t.reason) for t in section.tests),
            )
        )
    parts += [_finding(r) for r in section.findings]
    if section.sweep_curve is not None:
        parts.append(
            svg.response_chart(
                section.sweep_curve, title=f"{section.name}: full sweep, RPM against duty"
            )
        )
    if section.probe_curve is not None:
        parts.append(
            svg.response_chart(
                section.probe_curve,
                title=f"{section.name}: stall probe, RPM against duty (down, then back up)",
                falling_name="Down from 20 % (toward a stall)",
                rising_name="Back up (restart)",
            )
        )
    if section.probe_rows:
        parts.append(_table(PROBE_COLUMNS, section.probe_rows))
    parts.append("</details>\n")
    return "".join(parts)


# ── The run trace (S5-3) ──────────────────────────────────────────────────────


def tested_channel_ids(doc: Mapping) -> list[str]:
    """Headers a test actually started on, in the report's step order."""
    seen: dict[str, None] = {}
    for step in doc.get("steps") or []:
        if not isinstance(step, Mapping) or not step.get("started_at"):
            continue
        cid = str(step.get("channel_id") or "")
        if cid:
            seen.setdefault(cid, None)
    return list(seen)


def cpu_sensor_ids(doc: Mapping) -> list[str]:
    baseline = (doc.get("snapshots") or {}).get("baseline")
    body = d.snapshot_body(baseline, "sensors")
    sensors = body.get("sensors") if isinstance(body, Mapping) else None
    return sorted(
        str(s.get("id"))
        for s in sensors or []
        if isinstance(s, Mapping) and s.get("kind") == "cpu_temp" and s.get("id")
    )


def hottest_series(temps: Mapping, ids: Sequence[str], length: int) -> list[float | None]:
    """The hottest of *ids* at each sample; ``None`` where none reported."""
    out: list[float | None] = []
    for i in range(length):
        values = [
            float(v)
            for sid in ids
            if isinstance(temps.get(sid), list)
            and i < len(temps[sid])
            and isinstance(v := temps[sid][i], (int, float))
            and not isinstance(v, bool)
        ]
        out.append(max(values) if values else None)
    return out


def thermal_spans(t_ms: Sequence[int], states: Sequence[object]) -> list[tuple[int, int]]:
    """``(start, end)`` of every run of samples whose thermal state was reported
    and was not ``normal``. An unreported state is never shaded."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for i, t in enumerate(t_ms):
        state = states[i] if i < len(states) else None
        active = state is not None and state != "normal"
        if active and start is None:
            start = t
        if not active and start is not None:
            spans.append((start, t))
            start = None
    if start is not None and t_ms:
        spans.append((start, t_ms[-1]))
    return spans


def _trace_section(doc: Mapping, names: Mapping[str, str]) -> str:
    trace = doc.get("trace")
    if not isinstance(trace, Mapping) or not isinstance(trace.get("t_ms"), list):
        return ""
    t_ms = [int(t) for t in trace["t_ms"] if isinstance(t, (int, float))]
    if not t_ms:
        return ""
    fans = trace.get("fans") if isinstance(trace.get("fans"), Mapping) else {}
    temps = trace.get("temps_c") if isinstance(trace.get("temps_c"), Mapping) else {}
    thermal = trace.get("thermal_state") if isinstance(trace.get("thermal_state"), list) else []
    shading = thermal_spans(t_ms, thermal)
    shade_label = "thermal protection not normal" if shading else ""
    parts = ["<h2>Run trace</h2>\n"]
    charts: list[str] = []
    decimated = False
    for cid in tested_channel_ids(doc):
        cols = fans.get(cid)
        if not isinstance(cols, Mapping):
            continue
        name = names.get(cid, cid)
        chart, dec = svg.time_chart(
            t_ms,
            [
                svg.TimeSeries("RPM", cols.get("rpm") or [], "series-rpm"),
                svg.TimeSeries(
                    "Duty read back (%)",
                    cols.get("pwm_readback_pct") or [],
                    "series-duty",
                    axis="right",
                ),
            ],
            title=f"{name}: RPM and duty read back over the run",
            left_label="RPM",
            right_label="Duty (%)",
            shading=shading,
            shading_label=shade_label,
        )
        charts.append(chart)
        decimated = decimated or dec
    # Only sensors the trace recorded: the snapshot's list is the daemon's
    # verbatim answer, unbounded in a file from elsewhere.
    cpu_ids = [sid for sid in cpu_sensor_ids(doc) if isinstance(temps.get(sid), list)]
    if cpu_ids:
        chart, dec = svg.time_chart(
            t_ms,
            [
                svg.TimeSeries(
                    "Hottest CPU sensor (°C)",
                    hottest_series(temps, cpu_ids, len(t_ms)),
                    "series-temp",
                )
            ],
            title="Hottest CPU temperature over the run",
            left_label="°C",
            shading=shading,
            shading_label=shade_label,
            desc=f"The highest of {len(cpu_ids)} CPU temperature sensor(s) at each second.",
        )
        charts.append(chart)
        decimated = decimated or dec
        parts.append(
            f'<p class="meta">The temperature line is the highest of {len(cpu_ids)} CPU '
            "sensor(s) at each second — the input the daemon's thermal protection uses.</p>\n"
        )
    else:
        parts.append('<p class="meta">No CPU temperature sensor was reported.</p>\n')
    parts.append(
        '<p class="meta">Every fan and sensor is in the CSV and JSON exports; the charts '
        "show only the tested headers. A gap in a line is a second nothing was reported.</p>\n"
    )
    if decimated:
        parts.append(
            f'<p class="meta">Lines longer than {svg.DECIMATE_ABOVE} points are drawn as the '
            "lowest and highest value in each pixel column, so every peak and dip is kept; "
            "the CSV has every sample.</p>\n"
        )
    return "".join(parts + charts)


def report_html(doc: Mapping) -> str:
    vm = build_report_view(doc)
    names = {
        str(c.get("channel_id")): str(c.get("name") or c.get("channel_id"))
        for c in doc.get("channels") or []
        if isinstance(c, Mapping)
    }
    body = [
        f"<h1>{_e(vm.title)}</h1>\n",
        f'<p>{_pill(vm.state_label, vm.state_tone)}<span class="meta">{_e(vm.when_line)}'
        f" · {_e(vm.report_id)}</span></p>\n",
    ]
    if vm.state_reason:
        body.append(f"<p>{_e(vm.state_reason)}</p>\n")
    body.append("<ul>" + "".join(f"<li>{_e(line)}</li>" for line in vm.summary_lines) + "</ul>\n")
    if vm.trace_note:
        body.append(f'<p class="meta">{_e(vm.trace_note)}</p>\n')
    body.append(_findings("Needs attention", vm.attention))
    body.append(_findings("Observations", vm.observations))
    body.append(_findings("Restoration", vm.restoration))
    body.append("<h2>Channels</h2>\n")
    body += [_channel(c) for c in vm.channels]
    body.append(_trace_section(doc, names))
    body.append(_findings("Not tested", vm.not_tested))
    body.append("<h2>Environment</h2>\n" + _table(("", "Value"), vm.environment))
    body.append("<h2>Configuration</h2>\n" + _table(("", "Value"), vm.configuration))
    body.append("<h2>How to read this report</h2>\n" + _table(("Source", "Meaning"), vm.legend))
    body.append(
        "<details><summary>Evidence (the daemon's raw answers)</summary>\n"
        f"<pre>{_e(evidence_text(doc))}</pre></details>\n"
    )
    return _page(f"PWM Test Report — {vm.report_id}", body)


def _diff_table(rows: Sequence[Difference]) -> str:
    return _table(
        ("What", "Earlier", "Later", "Difference", "Note"),
        ((r.subject, r.earlier, r.later, r.delta, r.note) for r in rows),
    )


def comparison_html(cmp: Comparison) -> str:
    e, lt = cmp.earlier, cmp.later
    body = [
        "<h1>PWM Test Report comparison</h1>\n",
        _table(
            ("", "Earlier", "Later"),
            (
                ("Report", e.report_id, lt.report_id),
                ("Started (UTC)", e.started_at, lt.started_at),
                ("Machine", e.machine, lt.machine),
                ("State", e.state_label, lt.state_label),
                (
                    "GUI / daemon",
                    f"{e.gui_version} / {e.daemon_version}",
                    f"{lt.gui_version} / {lt.daemon_version}",
                ),
            ),
        ),
        '<p class="meta">Differences are later minus earlier. No difference is judged '
        "significant or not; each measured value is shown beside the spread its own run "
        "recorded.</p>\n",
        "<h2>Start conditions</h2>\n",
        _diff_table(cmp.start_conditions),
        "<h2>Channels</h2>\n",
        f"<p>Paired by stable id: {len(cmp.paired)}.</p>\n",
    ]
    for title, items in (
        ("Only in the earlier report", cmp.only_earlier),
        ("Only in the later report", cmp.only_later),
    ):
        if items:
            body.append(
                f"<p><strong>{_e(title)}:</strong></p><ul>"
                + "".join(f"<li>{_e(name)} ({_e(cid)})</li>" for cid, name in items)
                + "</ul>\n"
            )
    if cmp.possible_renames:
        body.append(
            "<p><strong>Possibly renamed</strong> (same chip, device and PWM index, different "
            "label — an inference; these are not compared):</p><ul>"
            + "".join(f"<li>{_e(a)} → {_e(b)}</li>" for a, b in cmp.possible_renames)
            + "</ul>\n"
        )
    for category in CATEGORY_ORDER:
        rows = cmp.in_category(category)
        same = cmp.same_counts.get(category, 0)
        body.append(f"<h2>{_e(CATEGORY_TITLES[category])} ({len(rows)})</h2>\n")
        if same and category != CATEGORY_NOT_COMPARABLE:
            body.append(
                f'<p class="meta">{same} other compared value(s) are the same in both '
                "reports.</p>\n"
            )
        body.append(_diff_table(rows) if rows else '<p class="meta">Nothing here.</p>\n')
    return _page("PWM Test Report comparison", body)
