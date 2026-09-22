"""Saving and loading PWM Test Reports (DEC-404; S4-8 amended).

Reports live in ``paths.reports_dir()`` (``$XDG_DATA_HOME/control-ofc/reports``)
as one compact JSON file each, written with ``atomic_write`` — a checkpoint after
every step, so a crash mid-run loses at most the step in flight. Nothing is ever
deleted automatically (D-b).

**Reopen limit: 16 MiB, the report's own** (S4-8, amended by the user). The
trace is stored column-wise at 1 Hz for up to three hours, measured at ~1.5 MB
an hour on a 19-fan / 24-sensor machine, which the shared 4 MiB import cap
(``paths.MAX_IMPORT_BYTES``) cannot hold. The bounded read is kept — only the
bound differs — so a crafted file still cannot exhaust memory.

A report found ``in_progress`` when loaded was cut off by a crash or a kill:
:func:`load_report` marks it ``interrupted``, marks the steps it never reached,
and re-derives its findings, so it reads as the partial record it is.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from control_ofc.paths import atomic_write, load_json_capped, reports_dir
from control_ofc.services.pwm_report import document as d

log = logging.getLogger(__name__)

#: S4-8 (amended): the report's own reopen limit.
REPORT_MAX_BYTES = 16 * 1024 * 1024

_FILE_PREFIX = "pwm-report-"

REASON_CUT_OFF = (
    "Control-OFC stopped before this run finished (it was closed or crashed), so "
    "the report ends where the last saved step does."
)


def report_path(report_id: str, directory: Path | None = None) -> Path:
    """``pwm-report-<report id>.json``. The id is generated, never user text."""
    safe = "".join(ch for ch in report_id if ch.isalnum() or ch in "-_")
    return (directory or reports_dir()) / f"{_FILE_PREFIX}{safe}.json"


def serialise(doc: dict) -> str:
    """Compact JSON. ``allow_nan=False`` because a non-finite literal would make
    the file unreadable by :func:`load_report` (``load_json_capped`` rejects it)."""
    return json.dumps(doc, separators=(",", ":"), allow_nan=False)


def save_report(doc: dict, directory: Path | None = None) -> Path:
    """Write *doc* atomically and return its path."""
    path = report_path(str(doc.get("report_id") or "unnamed"), directory)
    atomic_write(path, serialise(doc))
    return path


def load_report(path: Path) -> tuple[dict, bool]:
    """Load, validate and (if it was cut off) repair a report.

    Returns ``(document, repaired)``. Raises ``OSError`` for an unreadable file,
    ``ValueError`` for one over :data:`REPORT_MAX_BYTES` or not valid JSON, and
    :class:`~control_ofc.services.pwm_report.document.ReportSchemaError` (a
    ``ValueError``) for one that is not a report this build can read.
    """
    doc = d.validate_document(load_json_capped(path, max_bytes=REPORT_MAX_BYTES))
    if doc.get("state") != d.STATE_IN_PROGRESS:
        return doc, False
    mark_cut_off(doc)
    return doc, True


def mark_cut_off(doc: dict) -> None:
    """Turn an ``in_progress`` document into the ``interrupted`` record it is."""
    from control_ofc.services.pwm_report.findings import derive_findings

    for step in doc.get("steps") or []:
        if step.get("status") == d.STEP_RUNNING:
            step["status"] = d.STEP_INTERRUPTED
            step["reason"] = (
                REASON_CUT_OFF + " The daemon restores a header itself when its diagnostic ends."
            )
        elif step.get("status") == d.STEP_PENDING:
            step["status"] = d.STEP_NOT_TESTED
            step["reason"] = REASON_CUT_OFF
    doc["state"] = d.STATE_INTERRUPTED
    doc["state_reason"] = REASON_CUT_OFF
    if not doc.get("final_note"):
        doc["final_note"] = "The final state was never read."
    findings, actions = derive_findings(doc)
    doc["findings"] = findings
    doc["actions"] = actions


#: How much of a file's head :func:`_looks_in_progress` reads.
_HEAD_BYTES = 512


def _looks_in_progress(path: Path) -> bool:
    """Cheap pre-check, so recovery does not parse every saved report.

    History is never pruned automatically (D-b) and a report can reach
    :data:`REPORT_MAX_BYTES`, so loading every file on every open would grow
    without bound. :func:`serialise` writes the keys in
    :func:`~control_ofc.services.pwm_report.document.new_document`'s order, which
    puts ``state`` within the first few dozen bytes; a file that does not match
    is either finished or not ours, and a full load is only paid for a match.
    """
    try:
        with path.open("rb") as f:
            head = f.read(_HEAD_BYTES)
    except OSError:
        return False
    return b'"state":"in_progress"' in head


def recover_cut_off_reports(directory: Path | None = None) -> list[Path]:
    """Repair every report left ``in_progress`` by a crash; returns their paths.

    Best effort, per file: one unreadable report must not stop the others being
    repaired, and none of this may raise into the caller.
    """
    folder = directory or reports_dir()
    repaired: list[Path] = []
    try:
        candidates = sorted(folder.glob(f"{_FILE_PREFIX}*.json"))
    except OSError:
        return repaired
    for path in candidates:
        if not _looks_in_progress(path):
            continue
        try:
            doc, was_cut_off = load_report(path)
        except (OSError, ValueError) as exc:
            log.warning("Skipping unreadable PWM test report %s: %s", path, exc)
            continue
        if not was_cut_off:
            continue
        try:
            atomic_write(path, serialise(doc))
            repaired.append(path)
        except (OSError, ValueError) as exc:
            log.warning("Could not save repaired PWM test report %s: %s", path, exc)
    return repaired
