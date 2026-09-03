"""Serializers for a completed validation session (AIO-MB Phase 5).

Qt-free, and deliberately **not** a UI: Phase 5 owns the schema and the bytes,
Phase 6 (DEC-318) added the Export button, the file dialog and the success
message, in ``ui/widgets/validation_session_dialog.py`` + ``ui/pages/hardware_page.py``. There
is no save-file call anywhere in this module — it returns text, and the caller
decides where it goes.

Why the CSV lives here rather than in the daemon
------------------------------------------------
The daemon is JSON-only and has no CSV anywhere; the GUI already owns the one
other export this project has (the support bundle). The brief permits either
("provide serialization **or** retrievable API data"), so the split is: the
**daemon owns every semantic** — findings, result states, device-override
classification — and this module owns only the transcription. Nothing here
decides what a value *means*, which is why there is no threshold, no verdict and
no arithmetic over telemetry below.

The column set is derived mechanically from the typed sample model, so a field
added to ``ValidationMemberSample`` cannot silently go missing from the export.

Two rules the format has to keep
--------------------------------
* **Absent is not zero.** A member with no tach writes an empty cell, never
  ``0``. The whole point of the optional wire fields is that "the daemon did not
  say" survives to the analyst reading the file.
* **Member identity survives.** One row per member per sample, keyed by
  ``member_id`` — never a flattened per-sample average across radiators.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import fields
from typing import Any

from ..api.models import (
    ValidationMemberSample,
    ValidationSample,
    ValidationSession,
)

#: Sample-level columns, in order, before the per-member ones.
_SAMPLE_COLUMNS = (
    "elapsed_ms",
    "unix_ms",
    "temperature_sensor",
    "temperature_c",
    "coolant_c",
    "thermal_state",
)

#: Fields of :class:`ValidationMemberSample` that are not columns in their own
#: right. ``member_id`` leads the member block instead of sitting in the middle.
_MEMBER_LEADING = ("member_id", "role")


def _member_columns() -> tuple[str, ...]:
    """The member columns, derived from the dataclass rather than hardcoded.

    A field added to ``ValidationMemberSample`` appears here automatically. That
    is the point: a hand-maintained list is how an export quietly stops carrying
    a column that the model gained three releases ago.
    """
    names = [f.name for f in fields(ValidationMemberSample)]
    rest = [n for n in names if n not in _MEMBER_LEADING]
    return (*_MEMBER_LEADING, *rest)


def _cell(value: Any) -> str:
    """Render one value.

    ``None`` becomes an empty cell — **never** ``0`` and never ``"None"``. A
    reader must be able to tell "no tach fitted" from "the fan was stopped".
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def sample_csv_header() -> list[str]:
    return [*_SAMPLE_COLUMNS, *_member_columns()]


def _sample_rows(sample: ValidationSample) -> list[list[str]]:
    """One row per member — identity preserved, never averaged."""
    prefix = [_cell(getattr(sample, name, None)) for name in _SAMPLE_COLUMNS]
    if not sample.members:
        # A sample with no members still deserves a row: the temperature and the
        # thermal state at that instant are evidence in their own right.
        return [prefix + ["" for _ in _member_columns()]]
    return [
        prefix + [_cell(getattr(member, name, None)) for name in _member_columns()]
        for member in sample.members
    ]


def session_samples_csv(session: ValidationSession) -> str:
    """The time-series samples as CSV.

    ``\\r\\n`` line endings, per RFC 4180 — spreadsheets on every platform read
    that correctly, which is not true in the other direction.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(sample_csv_header())
    for sample in session.samples:
        for row in _sample_rows(sample):
            writer.writerow(row)
    return buf.getvalue()


def session_events_csv(session: ValidationSession) -> str:
    """The event timeline as CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["elapsed_ms", "unix_ms", "kind", "member_id", "detail"])
    for event in session.events:
        writer.writerow(
            [
                _cell(event.elapsed_ms),
                _cell(event.unix_ms),
                _cell(event.kind),
                _cell(event.member_id),
                _cell(event.detail),
            ]
        )
    return buf.getvalue()


def session_findings_csv(session: ValidationSession) -> str:
    """The evidence summary as CSV.

    Carries the daemon's **tokens**, not this GUI's wording: an analyst reading
    the file, or a future tool parsing it, must see the stable value rather than
    a label that changes when the UI copy changes. Human wording belongs in
    ``services/validation_view.py``, where a person is looking at it.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["finding_id", "state", "member_id", "evidence_kind", "detail"])
    for finding in session.findings:
        writer.writerow(
            [
                _cell(finding.id),
                _cell(finding.state),
                _cell(finding.member_id),
                _cell(finding.evidence_kind),
                _cell(finding.detail),
            ]
        )
    return buf.getvalue()


def session_json(session: ValidationSession) -> str:
    """The whole session as JSON.

    Re-serialized from the typed model rather than passing the daemon's bytes
    through, so what a user exports is exactly what this GUI understood — an
    unparsed field would otherwise appear in the export and nowhere else, which
    makes a bug report describe a document the GUI never read.
    """
    return json.dumps(_session_dict(session), indent=2, sort_keys=False) + "\n"


def _session_dict(session: ValidationSession) -> dict[str, Any]:
    """A plain-dict view of the session.

    Hand-rolled rather than ``dataclasses.asdict`` so the nested Phase 3
    characterisation run — which is a different model's type — is handled
    explicitly instead of by whatever ``asdict`` happens to do with it.
    """
    return {
        "session_id": session.session_id,
        "kind": session.kind,
        "state": session.state,
        "started_unix_ms": session.started_unix_ms,
        "completed_unix_ms": session.completed_unix_ms,
        "sample_limit_reached": session.sample_limit_reached,
        "interrupted_reason": session.interrupted_reason,
        "truncated_at_unix_ms": session.truncated_at_unix_ms,
        "requested_diagnostics": list(session.requested_diagnostics),
        "sweep_members": list(session.sweep_members),
        "metadata": _asdict(session.metadata),
        "findings": [_asdict(f) for f in session.findings],
        "evidence": [_evidence_dict(e) for e in session.evidence],
        "events": [_asdict(e) for e in session.events],
        "external_measurements": [_asdict(m) for m in session.external_measurements],
        "samples": [_sample_dict(s) for s in session.samples],
    }


def _asdict(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return dict(obj)
    if isinstance(obj, list):
        return [_asdict(x) for x in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: _asdict(getattr(obj, f.name)) for f in fields(obj)}
    return obj


def _sample_dict(sample: ValidationSample) -> dict[str, Any]:
    return _asdict(sample)


def _evidence_dict(ev: Any) -> dict[str, Any]:
    return _asdict(ev)
