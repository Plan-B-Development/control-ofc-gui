"""View-model for a validation session (AIO-MB Phase 5).

Qt-free by design — the widget that renders this is a thin renderer over
``ValidationSessionView``, in the ``services/*_view.py`` pattern this project
uses for every page derivation. Phase 5 built the model; **Phase 6 (DEC-318) added
the panel and the export button** that draw it — `ui/widgets/validation_session_dialog.py`
— so nothing here decides spacing, colour or layout. Phase 6 deliberately shipped
**no chart**: the brief prefers a stable table, and `TimelineChart` cannot render a
session's sample array without a new plot (register row `AIO6-b`).

What it does decide is the part that is not presentation taste:

* **The daemon owns result meaning; this module owns only the wording.** A
  finding arrives pre-decided as a stable token, and nothing here recalculates
  PASS/FAIL or re-derives a device-side override. That split is the whole point
  of the Phase 5/6 contract: a GUI that re-decided would be a second copy of a
  diagnostic rule, and the two would drift.
* **Unrecognised tokens render.** A finding id or result state this GUI does not
  know is shown humanised rather than dropped (the 273-i rule) — a newer daemon
  adding a finding must not make evidence vanish from the user's screen.
* **`unavailable` is never styled as a failure.** The hardware simply does not
  expose what the finding would need, and a red row would say something untrue
  about the user's cooler. Same for `not_tested`, which means nobody ran it.
* **A floor or a duty is shown only where it is known.** ``None`` on the wire
  means "the daemon did not say" and renders as an em dash, never as ``0%`` —
  the same truthfulness rule Phase 4 established for ``effective_min_pwm_pct``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..api.models import (
    VALIDATION_RESULT_FAIL,
    VALIDATION_RESULT_INTERRUPTED,
    VALIDATION_RESULT_NOT_OBSERVED,
    VALIDATION_RESULT_NOT_TESTED,
    VALIDATION_RESULT_OBSERVED,
    VALIDATION_RESULT_PASS,
    VALIDATION_RESULT_UNAVAILABLE,
    VALIDATION_RESULT_UNKNOWN,
    VALIDATION_STATE_CANCELLED,
    VALIDATION_STATE_COMPLETED,
    VALIDATION_STATE_ERROR,
    VALIDATION_STATE_INTERRUPTED,
    VALIDATION_STATE_RECORDING,
    ValidationEvidence,
    ValidationFinding,
    ValidationSession,
)

#: Shown wherever a value is genuinely unknown. Never substitute 0 or "None".
UNKNOWN_TEXT = "—"

#: Wording for each finding id (§8). The GUI owns this; the daemon sends tokens.
FINDING_LABELS = {
    "pwm_header_control": "PWM header control",
    "pwm_readback": "PWM readback",
    "pump_rpm_telemetry": "Pump RPM telemetry",
    "radiator_rpm_telemetry": "Radiator RPM telemetry",
    "pwm_response_characterization": "PWM response characterisation",
    "response_latency": "Response latency",
    "startup_lifecycle_behaviour": "Startup / lifecycle behaviour",
    "pwm_rpm_divergence": "PWM/RPM divergence",
    "possible_device_override": "Possible device-side control",
    "bios_ec_control_reclaim": "BIOS/EC control reclaim",
    "thermal_safety": "Thermal safety",
    "control_restoration": "Control restoration",
    "coolant_telemetry": "Coolant telemetry",
    "daemon_restart_recovery": "Daemon restart recovery",
}

#: Wording for each result token (§7).
RESULT_LABELS = {
    VALIDATION_RESULT_PASS: "Pass",
    VALIDATION_RESULT_FAIL: "Fail",
    VALIDATION_RESULT_OBSERVED: "Observed",
    VALIDATION_RESULT_NOT_OBSERVED: "None observed",
    VALIDATION_RESULT_NOT_TESTED: "Not tested",
    VALIDATION_RESULT_UNKNOWN: "Unknown",
    VALIDATION_RESULT_UNAVAILABLE: "Unavailable",
    VALIDATION_RESULT_INTERRUPTED: "Interrupted",
}

#: How each result should read visually. Note what is NOT here: `unavailable`
#: and `not_tested` are muted, never bad. Absent capability is not a fault, and
#: an untested item is not a failed one — styling either as an error would tell
#: the user their cooler is broken when nothing of the sort was established.
RESULT_TONES = {
    VALIDATION_RESULT_PASS: "ok",
    VALIDATION_RESULT_FAIL: "bad",
    VALIDATION_RESULT_OBSERVED: "info",
    VALIDATION_RESULT_NOT_OBSERVED: "ok",
    VALIDATION_RESULT_NOT_TESTED: "muted",
    VALIDATION_RESULT_UNKNOWN: "muted",
    VALIDATION_RESULT_UNAVAILABLE: "muted",
    VALIDATION_RESULT_INTERRUPTED: "warn",
}

STATE_LABELS = {
    VALIDATION_STATE_RECORDING: "Recording",
    VALIDATION_STATE_COMPLETED: "Completed",
    VALIDATION_STATE_CANCELLED: "Cancelled",
    VALIDATION_STATE_INTERRUPTED: "Interrupted",
    VALIDATION_STATE_ERROR: "Error",
    "idle": "Idle",
}

STATE_TONES = {
    VALIDATION_STATE_RECORDING: "info",
    VALIDATION_STATE_COMPLETED: "ok",
    VALIDATION_STATE_CANCELLED: "muted",
    VALIDATION_STATE_INTERRUPTED: "warn",
    VALIDATION_STATE_ERROR: "bad",
    "idle": "muted",
}

_EVIDENCE_LABELS = {
    "pwm_characterization": "PWM/RPM characterisation",
    "pwm_verify": "PWM control verification",
}

_EVENT_LABELS = {
    "session_started": "Session started",
    "session_stopped": "Session stopped",
    "profile_activated": "Profile activated",
    "manual_override_started": "Manual override started",
    "manual_override_ended": "Manual override ended",
    "thermal_failsafe_entered": "Thermal failsafe engaged",
    "thermal_failsafe_cleared": "Thermal failsafe cleared",
    "control_reclaimed": "Control reclaimed by BIOS/EC",
    "control_restored": "Control restored",
    "suspend": "Suspend",
    "resume": "Resume",
    "daemon_restart_observed": "Daemon restart",
    "characterization_started": "Characterisation started",
    "characterization_completed": "Characterisation completed",
    "verify_started": "Verification started",
    "verify_completed": "Verification completed",
    "user_marker": "Marker",
    "sample_limit_reached": "Sample limit reached",
}


def humanise_token(token: str) -> str:
    """Render an unrecognised wire token readably rather than dropping it."""
    return token.replace("_", " ").strip().capitalize() or UNKNOWN_TEXT


def finding_label(finding_id: str) -> str:
    return FINDING_LABELS.get(finding_id) or humanise_token(finding_id)


def result_label(state: str) -> str:
    return RESULT_LABELS.get(state) or humanise_token(state)


def result_tone(state: str) -> str:
    """An unknown state is muted, not bad.

    A newer daemon's token must not paint a red row on a machine whose GUI
    simply has not learned the word yet.
    """
    return RESULT_TONES.get(state, "muted")


def event_label(kind: str) -> str:
    return _EVENT_LABELS.get(kind) or humanise_token(kind)


def evidence_label(kind: str) -> str:
    return _EVIDENCE_LABELS.get(kind) or humanise_token(kind)


def format_elapsed(ms: int) -> str:
    """``1h 04m 12s`` / ``4m 12s`` / ``12s``. Negative or absent reads as ``—``."""
    if ms is None or ms < 0:
        return UNKNOWN_TEXT
    total = ms // 1000
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _fmt_pct(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value}%"


def _fmt_rpm(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value} RPM"


@dataclass(frozen=True)
class FindingRow:
    """One summary line, ready to render."""

    finding_id: str
    label: str
    state: str
    state_label: str
    tone: str
    detail: str
    member_label: str
    evidence_label: str


@dataclass(frozen=True)
class EvidenceRow:
    """One referenced diagnostic."""

    kind: str
    label: str
    member_label: str
    outcome: str
    outcome_label: str
    tone: str
    detail: str
    run_id: str


@dataclass(frozen=True)
class MemberTelemetryRow:
    """A member's telemetry across the whole session, summarised.

    Per member, never averaged across members: two radiators keep two rows, which
    is the identity-preservation rule the recorded telemetry is built around.
    """

    member_id: str
    label: str
    role_label: str
    samples: int
    requested_range: str
    readback_range: str
    rpm_range: str
    rpm_available: bool


@dataclass(frozen=True)
class ValidationSessionView:
    session_id: str
    device_name: str
    state: str
    state_label: str
    state_tone: str
    kind: str
    elapsed_text: str
    sample_count: int
    event_count: int
    recording: bool
    interrupted_note: str
    limit_note: str
    diagnostics_note: str
    findings: list[FindingRow] = field(default_factory=list)
    evidence: list[EvidenceRow] = field(default_factory=list)
    members: list[MemberTelemetryRow] = field(default_factory=list)


def _range_text(values: list[int], fmt: Callable[[int | None], str]) -> str:
    if not values:
        return UNKNOWN_TEXT
    low, high = min(values), max(values)
    return fmt(low) if low == high else f"{fmt(low)} to {fmt(high)}"


def build_finding_row(
    finding: ValidationFinding,
    *,
    display_name: Callable[[str], str] | None = None,
) -> FindingRow:
    member = finding.member_id or ""
    label = ""
    if member:
        label = display_name(member) if display_name else member
    return FindingRow(
        finding_id=finding.id,
        label=finding_label(finding.id),
        state=finding.state,
        state_label=result_label(finding.state),
        tone=result_tone(finding.state),
        detail=finding.detail or "",
        member_label=label,
        evidence_label=evidence_label(finding.evidence_kind) if finding.evidence_kind else "",
    )


def build_evidence_row(
    ev: ValidationEvidence,
    *,
    display_name: Callable[[str], str] | None = None,
) -> EvidenceRow:
    label = display_name(ev.member_id) if display_name else ev.member_id
    return EvidenceRow(
        kind=ev.kind,
        label=evidence_label(ev.kind),
        member_label=label,
        outcome=ev.outcome,
        outcome_label=result_label(ev.outcome),
        tone=result_tone(ev.outcome),
        detail=ev.detail or "",
        run_id=ev.run_id or "",
    )


def build_member_rows(
    session: ValidationSession,
    *,
    display_name: Callable[[str], str] | None = None,
) -> list[MemberTelemetryRow]:
    """One row per member declared at session start.

    Built from the metadata's member list rather than from whichever ids happen
    to appear in the samples: a member that reported nothing at all must still
    show, as a row saying so, or its absence would read as "not part of this
    cooler" instead of "this cooler told us nothing about it".
    """
    rows: list[MemberTelemetryRow] = []
    for member in session.metadata.members:
        requested: list[int] = []
        readback: list[int] = []
        rpm: list[int] = []
        seen = 0
        for sample in session.samples:
            for m in sample.members:
                if m.member_id != member.member_id:
                    continue
                seen += 1
                if m.requested_pct is not None:
                    requested.append(m.requested_pct)
                if m.readback_pct is not None:
                    readback.append(m.readback_pct)
                if m.rpm is not None:
                    rpm.append(m.rpm)
        label = (
            display_name(member.member_id) if display_name else (member.label or member.member_id)
        )
        rows.append(
            MemberTelemetryRow(
                member_id=member.member_id,
                label=label,
                role_label=humanise_token(member.member_kind),
                samples=seen,
                requested_range=_range_text(requested, _fmt_pct),
                readback_range=_range_text(readback, _fmt_pct),
                rpm_range=_range_text(rpm, _fmt_rpm),
                # Distinguishes "no tach on this header" from "this fan was
                # stopped" — reporting 0 RPM for the first would be a lie.
                rpm_available=bool(rpm),
            )
        )
    return rows


def build_validation_session_view(
    session: ValidationSession,
    *,
    display_name: Callable[[str], str] | None = None,
) -> ValidationSessionView:
    """Derive everything a Phase 6 panel needs from one session."""
    diagnostics = session.requested_diagnostics
    if diagnostics:
        diagnostics_note = ", ".join(evidence_label(d) for d in diagnostics)
    else:
        # A passive session is a legitimate, deliberate choice — say so plainly
        # rather than leaving a blank that reads as a configuration mistake.
        diagnostics_note = "Recording only — no diagnostics were run."

    interrupted_note = ""
    if session.state == VALIDATION_STATE_INTERRUPTED:
        reason = humanise_token(session.interrupted_reason or "")
        interrupted_note = (
            f"Recording stopped unexpectedly ({reason}). "
            "Everything captured before that point is preserved; nothing after it was recorded."
        )

    limit_note = ""
    if session.sample_limit_reached:
        limit_note = "The session reached its sample limit and finalised itself."

    return ValidationSessionView(
        session_id=session.session_id,
        device_name=session.metadata.device_name or session.metadata.cooling_device_id,
        state=session.state,
        state_label=STATE_LABELS.get(session.state) or humanise_token(session.state),
        state_tone=STATE_TONES.get(session.state, "muted"),
        kind=session.kind,
        elapsed_text=format_elapsed(session.samples[-1].elapsed_ms if session.samples else 0),
        sample_count=len(session.samples),
        event_count=len(session.events),
        recording=session.is_recording,
        interrupted_note=interrupted_note,
        limit_note=limit_note,
        diagnostics_note=diagnostics_note,
        findings=[build_finding_row(f, display_name=display_name) for f in session.findings],
        evidence=[build_evidence_row(e, display_name=display_name) for e in session.evidence],
        members=build_member_rows(session, display_name=display_name),
    )
