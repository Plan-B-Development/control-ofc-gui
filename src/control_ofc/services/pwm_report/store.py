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
from dataclasses import dataclass
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


def _read(path: Path) -> dict:
    """Read and schema-check one report file. A document nested too deeply for
    the JSON parser is a malformed file (``ValueError``), not a crash."""
    try:
        raw = load_json_capped(path, max_bytes=REPORT_MAX_BYTES)
    except RecursionError as exc:
        raise ValueError("nested too deeply to be a report") from exc
    return d.validate_document(raw)


def load_report(path: Path) -> tuple[dict, bool]:
    """Load, validate and (if it was cut off) repair a report.

    Returns ``(document, repaired)``. Raises ``OSError`` for an unreadable file,
    ``ValueError`` for one over :data:`REPORT_MAX_BYTES` or not valid JSON, and
    :class:`~control_ofc.services.pwm_report.document.ReportSchemaError` (a
    ``ValueError``) for one that is not a report this build can read.
    """
    doc = _read(path)
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


# ── History (DEC-404 Stage 5) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class HistoryEntry:
    """One row of the history list. ``error`` is set for a file in the reports
    folder that could not be read — it is listed, not hidden, so the user can
    see it and delete it."""

    path: Path
    report_id: str
    started_at: str
    machine: str
    tests: str
    state: str
    error: str = ""


def tests_summary(doc: dict) -> str:
    """ "3 of 4 tests completed on 2 headers", or "Read-only snapshot"."""
    steps = [s for s in doc.get("steps") or [] if isinstance(s, dict)]
    if not steps:
        return "Read-only snapshot"
    done = sum(1 for s in steps if s.get("status") == d.STEP_COMPLETE)
    headers = len({s.get("channel_id") for s in steps})
    return f"{done} of {len(steps)} test(s) completed on {headers} header(s)"


def history_entry(path: Path, doc: dict) -> HistoryEntry:
    return HistoryEntry(
        path=path,
        report_id=str(doc.get("report_id") or ""),
        started_at=str(doc.get("started_at") or ""),
        machine=d.machine_summary(doc),
        tests=tests_summary(doc),
        state=str(doc.get("state") or ""),
    )


#: ``path → ((st_ino, st_mtime_ns, st_size), entry)`` for files already listed
#: this session. Reports are never pruned automatically (D-b) and the Reports
#: page is where the window opens, so re-parsing every file on every visit would
#: grow without bound; an unchanged file costs one ``stat`` instead.
_LIST_CACHE: dict[Path, tuple[tuple[int, int, int], HistoryEntry]] = {}


def list_reports(directory: Path | None = None) -> list[HistoryEntry]:
    """Every saved report, newest first. Never raises.

    Each new or changed file is read in full under :data:`REPORT_MAX_BYTES` and
    checked with :func:`~control_ofc.services.pwm_report.document.validate_document`
    — the list shows only what the renderer could open; an unchanged one comes
    from :data:`_LIST_CACHE`. A report still ``in_progress`` is listed as such
    (it is the run in flight, or one recovery could not repair); nothing here
    rewrites a file.
    """
    folder = directory or reports_dir()
    try:
        paths = sorted(folder.glob(f"{_FILE_PREFIX}*.json"))
    except OSError:
        return []
    entries: list[HistoryEntry] = []
    for path in paths:
        try:
            st = path.stat()
            key = (st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
        cached = _LIST_CACHE.get(path)
        if key is not None and cached is not None and cached[0] == key:
            entries.append(cached[1])
            continue
        try:
            entry = history_entry(path, _read(path))
        except (OSError, ValueError) as exc:
            entry = HistoryEntry(path, path.stem, "", "", "", "", error=str(exc) or "unreadable")
        if key is not None:
            _LIST_CACHE[path] = (key, entry)
        entries.append(entry)
    entries.sort(key=lambda e: (e.started_at, e.report_id), reverse=True)
    return entries


def delete_report(path: Path, directory: Path | None = None) -> None:
    """Delete one saved report (D-b: only ever on the user's request).

    Refuses anything that is not a report file directly inside the reports
    folder, so a path handed in from elsewhere can never delete another file.
    """
    folder = (directory or reports_dir()).resolve()
    # The parent is resolved, the name is not: a symlink inside the folder is
    # removed as a link, and never followed to whatever it points at.
    if (
        path.parent.resolve() != folder
        or not path.name.startswith(_FILE_PREFIX)
        or path.suffix != ".json"
    ):
        raise ValueError(f"not a saved report: {path}")
    path.unlink()
