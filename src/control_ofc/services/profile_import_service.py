"""One-time profile import: GUI-fronted migration of local profiles into the
daemon's profile store (DEC-161, migration Phase 2).

The GUI reads the user's own ``~/.config/control-ofc/profiles/`` (a system
daemon can't reliably read each user's ``$XDG_CONFIG_HOME``), migrates each file
to the current schema, and uploads the documents through the daemon's existing
``POST /profiles`` CRUD endpoint — no dedicated import endpoint, no daemon-side
run-once marker. Idempotency comes from the **stable profile id**: a re-run
409-skips ids already in the store. A GUI-side ``daemon_import_prompted`` flag
keeps the startup offer from nagging.

This module is Qt-free so the import logic stays unit-testable with a fake
client; the thin dialog wiring lives in the Settings page / main window.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
from control_ofc.services.profile_service import ImportCandidate, ImportCollection

log = logging.getLogger(__name__)


@dataclass
class ImportOutcome:
    """Per-profile result of an import attempt."""

    source_path: str
    profile_id: str
    name: str
    status: str  # "imported" | "skipped" | "quarantined"
    reason: str = ""


@dataclass
class ImportReport:
    """Aggregated result of an import run, grouped by outcome."""

    imported: list[ImportOutcome] = field(default_factory=list)
    skipped: list[ImportOutcome] = field(default_factory=list)
    quarantined: list[ImportOutcome] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.imported) + len(self.skipped) + len(self.quarantined)


def should_offer_import(caps, settings, *, has_local_profiles: bool, demo: bool) -> bool:
    """Pure gate for the startup auto-offer (DEC-161).

    Offer the one-time import only when the daemon advertises profile storage
    (``control.profile_storage``), we haven't already offered on this install
    (``settings.daemon_import_prompted``), there are local profiles to migrate,
    and we're talking to a real daemon (not demo). Anything missing → no offer.
    """
    if demo:
        return False
    if settings is None or getattr(settings, "daemon_import_prompted", False):
        return False
    control = getattr(caps, "control", None)
    if control is None or not getattr(control, "profile_storage", False):
        return False
    return has_local_profiles


def import_profiles(
    client, collection: ImportCollection, *, on_conflict: str = "skip"
) -> ImportReport:
    """Upload a collection of migrated profiles to the daemon store.

    Each candidate is POSTed via ``client.create_profile``. Outcomes:
    - 201 → imported (the daemon's validation warnings, if any, are noted).
    - 409 ``already_exists`` → skipped (``on_conflict="skip"``) or re-uploaded
      under a fresh id + " (imported)" name (``on_conflict="rename"``).
    - 400 ``validation_error`` → quarantined with a field-violation summary.
    - any other per-request ``DaemonError`` → quarantined with its message.

    Files that could not even be parsed/migrated (``collection.failed``) are
    quarantined first. The batch never aborts on a per-profile error; only a
    transport failure (daemon gone / timed out) propagates, since continuing is
    pointless without a daemon.
    """
    report = ImportReport()

    for source_path, reason in collection.failed:
        report.quarantined.append(
            ImportOutcome(
                source_path=source_path, profile_id="", name="", status="quarantined", reason=reason
            )
        )

    for cand in collection.ready:
        try:
            resp = client.create_profile(cand.document)
            report.imported.append(
                ImportOutcome(
                    source_path=cand.source_path,
                    profile_id=cand.profile_id,
                    name=cand.name,
                    status="imported",
                    reason=_warning_note(resp),
                )
            )
        except (DaemonUnavailable, DaemonTimeout):
            # The daemon vanished mid-batch — abort and let the UI surface it.
            raise
        except DaemonError as e:
            _record_failure(client, cand, e, on_conflict, report)

    return report


def _record_failure(
    client, cand: ImportCandidate, err: DaemonError, on_conflict: str, report: ImportReport
) -> None:
    """Classify a per-request ``DaemonError`` into the report (never raises for
    a per-profile failure — keeps the batch going)."""
    if err.status == 409:
        if on_conflict == "rename" and _try_rename(client, cand, report):
            return
        report.skipped.append(
            ImportOutcome(
                source_path=cand.source_path,
                profile_id=cand.profile_id,
                name=cand.name,
                status="skipped",
                reason="a profile with this id already exists in the daemon store",
            )
        )
        return
    if err.status == 400:
        reason = _violation_summary(err)
    else:
        reason = err.message or err.code or "upload failed"
    report.quarantined.append(
        ImportOutcome(
            source_path=cand.source_path,
            profile_id=cand.profile_id,
            name=cand.name,
            status="quarantined",
            reason=reason,
        )
    )


def _try_rename(client, cand: ImportCandidate, report: ImportReport) -> bool:
    """Re-upload a colliding candidate under a fresh id + " (imported)" name.

    Returns True if the renamed copy was imported. A fresh ``uuid4`` makes a
    second collision practically impossible; if the renamed copy still fails
    validation it is quarantined and we return True (handled). Transport errors
    propagate (the batch aborts)."""
    renamed = dict(cand.document)
    new_id = str(uuid.uuid4())[:8]
    renamed["id"] = new_id
    renamed["name"] = f"{cand.name} (imported)" if cand.name else "Imported profile"
    try:
        resp = client.create_profile(renamed)
    except (DaemonUnavailable, DaemonTimeout):
        raise
    except DaemonError as e:
        report.quarantined.append(
            ImportOutcome(
                source_path=cand.source_path,
                profile_id=new_id,
                name=renamed["name"],
                status="quarantined",
                reason=(
                    _violation_summary(e)
                    if e.status == 400
                    else (e.message or e.code or "upload failed")
                ),
            )
        )
        return True
    report.imported.append(
        ImportOutcome(
            source_path=cand.source_path,
            profile_id=new_id,
            name=renamed["name"],
            status="imported",
            reason=_join_notes("imported as a renamed copy (id collision)", _warning_note(resp)),
        )
    )
    return True


def _warning_note(resp: object) -> str:
    """Summarise the daemon's accept-with-warnings (e.g. a sensor missing on
    this machine) so the user sees why a profile imported but isn't a perfect
    fit. Empty string when there are no warnings."""
    if not isinstance(resp, dict):
        return ""
    warnings = resp.get("warnings")
    if isinstance(warnings, list) and warnings:
        return f"{len(warnings)} warning(s)"
    return ""


def _violation_summary(err: DaemonError) -> str:
    """Build a human-readable reason from a 400's ``field_violations`` details,
    falling back to the envelope message."""
    details = err.details
    violations = details.get("field_violations") if isinstance(details, dict) else None
    if isinstance(violations, list) and violations:
        parts: list[str] = []
        for v in violations:
            if not isinstance(v, dict):
                continue
            f = str(v.get("field", "")).strip()
            d = str(v.get("description") or v.get("reason") or "").strip()
            parts.append(f"{f}: {d}" if f and d else (f or d))
        parts = [p for p in parts if p]
        if parts:
            return "; ".join(parts)
    return err.message or err.code or "validation failed"


def _join_notes(*notes: str) -> str:
    return "; ".join(n for n in notes if n)
