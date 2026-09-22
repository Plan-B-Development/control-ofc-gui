"""The PWM Test Report document, version 1 (DEC-404).

A plain JSON-able ``dict`` rather than a dataclass tree, on purpose: the report
keeps the daemon's responses **verbatim** as evidence, and a typed model would
drop every field this GUI does not model yet — the same loss the response
observer in ``DaemonClient`` exists to prevent. The shape is fixed by the
builders below and checked on load by :func:`validate_document`.

Top-level keys::

    kind, schema_version, report_id, state, state_reason,
    created_at, started_at, finished_at,
    environment, configuration, user_facts, channels, plan,
    snapshots {baseline, final}, steps[], trace, findings[], actions,
    provenance_legend

Everything the daemon said is under ``snapshots`` and ``steps`` exactly as it
said it. Everything the GUI *derived* — ``environment``, ``configuration``,
``findings`` — is recomputable from those, and is labelled with the provenance
class it was derived under.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

from control_ofc.api.models import (
    PROVENANCE_COMMANDED,
    PROVENANCE_DERIVED,
    PROVENANCE_DEVICE_METADATA,
    PROVENANCE_OBSERVED,
    PROVENANCE_UNVERIFIED,
    PROVENANCE_USER_METADATA,
)

DOCUMENT_KIND = "control-ofc.pwm-test-report"
SCHEMA_VERSION = 1

STATE_IN_PROGRESS = "in_progress"
STATE_COMPLETE = "complete"
STATE_CANCELLED = "cancelled"
STATE_INTERRUPTED = "interrupted"
STATE_ABORTED = "aborted"
STATES = frozenset(
    {STATE_IN_PROGRESS, STATE_COMPLETE, STATE_CANCELLED, STATE_INTERRUPTED, STATE_ABORTED}
)

#: Step statuses. ``not_tested`` is neutral — a skipped test is not a failed one.
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_COMPLETE = "complete"
STEP_NOT_TESTED = "not_tested"
STEP_CANCELLED = "cancelled"
STEP_ABORTED = "aborted"
STEP_INTERRUPTED = "interrupted"
STEP_ERROR = "error"

#: The provenance legend every report carries, in the Overview's six classes
#: (``api/models.py``). Worded for a reader who has never seen the app.
PROVENANCE_LEGEND: Mapping[str, str] = {
    PROVENANCE_OBSERVED: "Measured at the hardware interface during this run.",
    PROVENANCE_COMMANDED: "A value Control-OFC asked for — not proof the hardware did it.",
    PROVENANCE_DERIVED: "Worked out from measured values by a stated rule.",
    PROVENANCE_USER_METADATA: "Something you told the report. Not measured.",
    PROVENANCE_DEVICE_METADATA: "Reported by the driver or firmware about itself.",
    PROVENANCE_UNVERIFIED: "Could not be confirmed either way.",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_report_id(now_iso: str | None = None) -> str:
    """``20260922T101530Z-3f9a1c`` — sortable, and unique without a counter."""
    stamp = (now_iso or utc_now_iso()).replace("-", "").replace(":", "")
    return f"{stamp}-{secrets.token_hex(3)}"


def new_document(
    *,
    report_id: str,
    created_at: str,
    gui_facts: Mapping[str, object],
    channels: Iterable[Mapping[str, object]],
    user_facts: Mapping[str, object],
    plan: Mapping[str, object],
) -> dict:
    """A fresh ``in_progress`` document. The runner fills in the rest."""
    return {
        "kind": DOCUMENT_KIND,
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "state": STATE_IN_PROGRESS,
        "state_reason": "",
        "created_at": created_at,
        "started_at": created_at,
        "finished_at": None,
        "environment": {"gui": dict(gui_facts)},
        "configuration": {},
        "user_facts": dict(user_facts),
        "channels": [dict(c) for c in channels],
        "plan": dict(plan),
        "snapshots": {"baseline": None, "final": None},
        "steps": [],
        "trace": None,
        "findings": [],
        "actions": {"reapply_profile": False},
        "provenance_legend": dict(PROVENANCE_LEGEND),
    }


# ── Reading a snapshot bundle ─────────────────────────────────────────────────
#
# A snapshot is ``{endpoint_name: {"status", "body", "error", "fetched_at"}}``
# exactly as the worker captured it. These readers never raise: a missing or
# malformed body reads as "not available", which the report then says.


def snapshot_body(snapshot: object, name: str) -> object:
    """The raw body one endpoint returned, or ``None``."""
    if not isinstance(snapshot, Mapping):
        return None
    entry = snapshot.get(name)
    if not isinstance(entry, Mapping):
        return None
    status = entry.get("status")
    if not isinstance(status, int) or status >= 400:
        return None
    return entry.get("body")


def _dict(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def fan_entries(snapshot: object) -> dict[str, Mapping]:
    """``/fans`` entries by stable id."""
    body = _dict(snapshot_body(snapshot, "fans"))
    return {
        str(f.get("id")): f
        for f in _list(body.get("fans"))
        if isinstance(f, Mapping) and f.get("id")
    }


def header_entries(snapshot: object) -> dict[str, Mapping]:
    """``/hwmon/headers`` entries by stable id."""
    body = _dict(snapshot_body(snapshot, "headers"))
    return {
        str(h.get("id")): h
        for h in _list(body.get("headers"))
        if isinstance(h, Mapping) and h.get("id")
    }


def status_body(snapshot: object) -> Mapping:
    return _dict(snapshot_body(snapshot, "status"))


def profile_hash(profile_body: object) -> str | None:
    """A content hash of the active profile document, so two reports can say
    "same configuration" without either of them trusting a name."""
    if not isinstance(profile_body, Mapping) or not profile_body:
        return None
    canonical = json.dumps(profile_body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_environment(snapshot: object, gui_facts: Mapping[str, object]) -> dict:
    """The environment section, from the baseline snapshot plus the GUI's own
    facts. Every value is what a component reported about itself
    (DEVICE_METADATA) or what the GUI read locally; nothing is inferred."""
    caps = _dict(snapshot_body(snapshot, "capabilities"))
    hw = _dict(snapshot_body(snapshot, "hardware"))
    board = _dict(hw.get("board"))
    chips = [
        {
            "chip_name": c.get("chip_name"),
            "device_id": c.get("device_id"),
            "expected_driver": c.get("expected_driver"),
            "header_count": c.get("header_count"),
        }
        for c in _list(_dict(hw.get("hwmon")).get("chips_detected"))
        if isinstance(c, Mapping)
    ]
    modules = [
        {
            "name": m.get("name"),
            "loaded": m.get("loaded"),
            "in_mainline": m.get("in_mainline"),
            "version": m.get("version"),
            "srcversion": m.get("srcversion"),
            "out_of_tree": m.get("out_of_tree"),
        }
        for m in _list(hw.get("kernel_modules"))
        if isinstance(m, Mapping)
    ]
    return {
        "gui": dict(gui_facts),
        "daemon": {
            "daemon_version": caps.get("daemon_version"),
            "api_version": caps.get("api_version"),
            # DEC-405: the kernel as the DAEMON read it — beside the GUI's own
            # `uname`, because the two are separate processes and a report
            # compared across a reboot must not assume they agree.
            "kernel_release": hw.get("kernel_release"),
        },
        "board": {
            "vendor": board.get("vendor"),
            "name": board.get("name"),
            "bios_version": board.get("bios_version"),
            "bios_date": board.get("bios_date"),
        },
        "cpu_vendor": hw.get("cpu_vendor"),
        "chips": chips,
        "kernel_modules": modules,
        "provenance": PROVENANCE_DEVICE_METADATA,
    }


def extract_configuration(snapshot: object) -> dict:
    """What Control-OFC was configured to do when the run began."""
    status = status_body(snapshot)
    active = _dict(snapshot_body(snapshot, "profile_active"))
    profile = snapshot_body(snapshot, "profile")
    hw = _dict(snapshot_body(snapshot, "hardware"))
    thermal = _dict(hw.get("thermal_safety"))
    headers = header_entries(snapshot)
    return {
        "active_profile_id": status.get("active_profile_id") or active.get("profile_id"),
        "active_profile_name": status.get("active_profile_name") or active.get("profile_name"),
        "profile_hash": profile_hash(profile),
        "headers": {
            hid: {
                "role": h.get("role"),
                "role_source": h.get("role_source"),
                "effective_min_pwm_pct": h.get("effective_min_pwm_pct"),
                "stop_permitted": h.get("stop_permitted"),
                "is_writable": h.get("is_writable"),
                "cooling_device_id": h.get("cooling_device_id"),
            }
            for hid, h in sorted(headers.items())
        },
        # DEC-308: the trip point is per-machine; this is the value the daemon
        # reports acting on, never a literal.
        "emergency_threshold_c": thermal.get("emergency_threshold_c"),
        "release_threshold_c": thermal.get("release_threshold_c"),
        "thermal_state": status.get("thermal_state"),
        "cooling_devices": snapshot_body(snapshot, "cooling_devices"),
    }


# ── Loading ───────────────────────────────────────────────────────────────────


class ReportSchemaError(ValueError):
    """A file that is not a PWM Test Report this build can read."""


def validate_document(doc: object) -> dict:
    """Check the shape of a loaded report. Raises :class:`ReportSchemaError`.

    Deliberately shallow: it guards what the renderer dereferences, not every
    leaf. The evidence inside is the daemon's own and is rendered as data.
    """
    if not isinstance(doc, dict):
        raise ReportSchemaError("not a JSON object")
    if doc.get("kind") != DOCUMENT_KIND:
        raise ReportSchemaError("not a Control-OFC PWM Test Report")
    version = doc.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ReportSchemaError("schema_version missing")
    if version > SCHEMA_VERSION:
        raise ReportSchemaError(
            f"written by a newer Control-OFC (report schema {version}; this build reads "
            f"up to {SCHEMA_VERSION})"
        )
    if doc.get("state") not in STATES:
        raise ReportSchemaError("unknown report state")
    for key, kind in (
        ("report_id", str),
        ("environment", dict),
        ("configuration", dict),
        ("user_facts", dict),
        ("plan", dict),
        ("snapshots", dict),
    ):
        if not isinstance(doc.get(key), kind):
            raise ReportSchemaError(f"'{key}' is missing or malformed")
    for key in ("channels", "steps", "findings"):
        if not isinstance(doc.get(key), list):
            raise ReportSchemaError(f"'{key}' is missing or malformed")
    return doc
