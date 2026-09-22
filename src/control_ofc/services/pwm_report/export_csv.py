"""CSV export of a PWM Test Report (DEC-404 Stage 5, S5-2).

Three tables, three files — RFC 4180 is one table per file, and a sweep point,
a probe step and a trace sample have nothing in common but a header id:

* ``sweep``  — every point of every full sweep, one row per walked step;
* ``probe``  — every held duty of every stall probe;
* ``trace``  — the 1 Hz run trace, one row per second, one column per quantity.

Each returns ``None`` when the report holds nothing for it, so the caller writes
only the files that have rows and can say which it wrote.

Rules carried from ``services/validation_export.py`` (the project's other CSV):

* **Absent is not zero.** ``None`` is an empty cell, never ``0``.
* **Columns are derived from the typed model**, so a field the model gains
  cannot silently go missing from the export.

And one this export adds, because its text cells carry daemon labels, user
notes and stable ids that end in a header label: **a text cell that a
spreadsheet would evaluate as a formula is prefixed with ``'``** (the OWASP CSV
injection rule). Numbers are never touched — a negative value stays a number.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping
from dataclasses import fields

from control_ofc.api.models import (
    CharPoint,
    EstimatedRpm,
    PointStability,
    ProbePoint,
    parse_characterization_run,
    parse_stall_probe_run,
)
from control_ofc.services.pwm_report.catalog import TEST_PROBE, TEST_SWEEP
from control_ofc.services.pwm_report.trace import FAN_SERIES

#: Nested dataclass fields of :class:`CharPoint`, flattened as ``name.sub``.
#: A test asserts every other ``CharPoint`` field is a scalar, so a new nested
#: field fails loudly instead of exporting as a repr.
SWEEP_NESTED: Mapping[str, type] = {
    "stability": PointStability,
    "estimated_physical_rpm": EstimatedRpm,
}

_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")

_ROW_LEAD = ("report_id", "channel_id", "channel_name", "run_id")


def cell(value: object) -> str:
    """One cell. ``None`` → empty; bools as ``true``/``false``; a text value a
    spreadsheet would run as a formula gets a leading ``'``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text.startswith(_FORMULA_LEAD):
        return "'" + text
    return text


def _write(header: list[str], rows: Iterable[list[object]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([cell(h) for h in header])
    for row in rows:
        writer.writerow([cell(v) for v in row])
    return buf.getvalue()


def _names(doc: Mapping) -> dict[str, str]:
    return {
        str(c.get("channel_id")): str(c.get("name") or c.get("channel_id"))
        for c in doc.get("channels") or []
        if isinstance(c, Mapping)
    }


def _steps(doc: Mapping, test: str) -> list[Mapping]:
    return [
        s
        for s in doc.get("steps") or []
        if isinstance(s, Mapping) and s.get("test") == test and isinstance(s.get("result"), Mapping)
    ]


def sweep_columns() -> list[str]:
    cols: list[str] = []
    for f in fields(CharPoint):
        nested = SWEEP_NESTED.get(f.name)
        if nested is None:
            cols.append(f.name)
        else:
            cols.extend(f"{f.name}.{sub.name}" for sub in fields(nested))
    return cols


def _sweep_values(point: CharPoint) -> list[object]:
    out: list[object] = []
    for f in fields(CharPoint):
        value = getattr(point, f.name)
        nested = SWEEP_NESTED.get(f.name)
        if nested is None:
            out.append(value)
            continue
        for sub in fields(nested):
            out.append(getattr(value, sub.name) if value is not None else None)
    return out


def sweep_csv(doc: Mapping) -> str | None:
    names = _names(doc)
    rows: list[list[object]] = []
    for step in _steps(doc, TEST_SWEEP):
        run = parse_characterization_run(dict(step["result"]))
        cid = str(step.get("channel_id") or "")
        for point in sorted(run.points, key=lambda p: p.step_index):
            rows.append(
                [doc.get("report_id"), cid, names.get(cid, cid), run.run_id, *_sweep_values(point)]
            )
    if not rows:
        return None
    return _write([*_ROW_LEAD, *sweep_columns()], rows)


def probe_columns() -> list[str]:
    return [f.name for f in fields(ProbePoint)]


def probe_csv(doc: Mapping) -> str | None:
    names = _names(doc)
    rows: list[list[object]] = []
    for step in _steps(doc, TEST_PROBE):
        run = parse_stall_probe_run(dict(step["result"]))
        cid = str(step.get("channel_id") or "")
        for point in sorted(run.points, key=lambda p: p.step_index):
            rows.append(
                [
                    doc.get("report_id"),
                    cid,
                    names.get(cid, cid),
                    run.run_id,
                    *(getattr(point, name) for name in probe_columns()),
                ]
            )
    if not rows:
        return None
    return _write([*_ROW_LEAD, *probe_columns()], rows)


def trace_csv(doc: Mapping) -> str | None:
    """One row per recorded second. Column names are ``<stable id> <quantity>``
    in the wire's own names, so nothing is renamed on the way out. Only the
    recorder's own quantities, and only series exactly as long as ``t_ms``, are
    written — so the file grows with the report, never with samples x keys."""
    trace = doc.get("trace")
    if not isinstance(trace, Mapping):
        return None
    t_ms = trace.get("t_ms")
    if not isinstance(t_ms, list) or not t_ms:
        return None
    fans = trace.get("fans") if isinstance(trace.get("fans"), Mapping) else {}
    temps = trace.get("temps_c") if isinstance(trace.get("temps_c"), Mapping) else {}
    thermal = trace.get("thermal_state") if isinstance(trace.get("thermal_state"), list) else []
    columns: list[tuple[str, list]] = []
    n = len(t_ms)
    for fid in sorted(fans):
        series = fans[fid] if isinstance(fans[fid], Mapping) else {}
        for name in FAN_SERIES:
            values = series.get(name)
            if isinstance(values, list) and len(values) == n:
                columns.append((f"{fid} {name}", values))
    for sid in sorted(temps):
        values = temps[sid]
        if isinstance(values, list) and len(values) == n:
            columns.append((f"{sid} value_c", values))
    states = thermal if len(thermal) == n else [None] * n

    header = ["t_ms", "thermal_state", *(name for name, _ in columns)]
    rows = ([t, states[i], *(values[i] for _, values in columns)] for i, t in enumerate(t_ms))
    return _write(header, rows)


#: ``(file suffix, builder)`` — the order the confirmation lists them in.
TABLES = (("sweep", sweep_csv), ("probe", probe_csv), ("trace", trace_csv))


def csv_tables(doc: Mapping) -> dict[str, str]:
    """``{table: text}`` for every table the report has rows for."""
    out: dict[str, str] = {}
    for suffix, build in TABLES:
        text = build(doc)
        if text is not None:
            out[suffix] = text
    return out


def csv_file_name(doc: Mapping, table: str) -> str:
    """``pwm-report-<report id>-<table>.csv``; the id is filtered to a safe name."""
    rid = "".join(ch for ch in str(doc.get("report_id") or "report") if ch.isalnum() or ch in "-_")
    return f"pwm-report-{rid}-{table}.csv"
