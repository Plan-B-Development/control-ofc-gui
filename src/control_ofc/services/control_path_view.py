"""Headless view-model for PWM-to-tach control-path discovery (AIO Phase 8 §2, §6.2).

Renders what the daemon measured. It derives no verdict: ``relationship``,
``confidence`` and ``measurement_resolution_ms`` are computed once, daemon-side,
and are carried through verbatim — the same rule the characterisation view
follows, and for the same reason (re-deriving them here is how the two copies
start disagreeing).

Two presentation rules worth keeping:

* **A no-response result is not a failure.** The Overview is explicit — do not
  label an unexpected RPM response as hardware failure unless evidence supports
  it. A header may legitimately drive no tach-reporting device, or drive one that
  is running under its own internal control. So ``no_tach_response`` is rendered
  in an informational tone, never a critical one.
* **Unrecognised tokens render verbatim** (273-i), so a newer daemon's new
  relationship is visible rather than silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from control_ofc.api.models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_UNKNOWN,
    CONTROL_PATH_AMBIGUOUS,
    CONTROL_PATH_CONFIRMED,
    CONTROL_PATH_MULTIPLE,
    CONTROL_PATH_NO_RESPONSE,
    CONTROL_PATH_PROBABLE,
    ControlPathRecord,
    ControlPathRun,
)

UNKNOWN_TEXT = "—"

RELATIONSHIP_LABELS: dict[str, str] = {
    CONTROL_PATH_CONFIRMED: "Confirmed",
    CONTROL_PATH_PROBABLE: "Probable",
    CONTROL_PATH_AMBIGUOUS: "Ambiguous",
    CONTROL_PATH_NO_RESPONSE: "No tach response",
    CONTROL_PATH_MULTIPLE: "Multiple responses",
}

#: Tone per relationship.
#:
#: ``no_tach_response`` is **info, not critical**, and ``ambiguous`` is a
#: warning rather than a failure. Neither is evidence of broken hardware.
RELATIONSHIP_TONES: dict[str, str] = {
    CONTROL_PATH_CONFIRMED: "ok",
    CONTROL_PATH_PROBABLE: "ok",
    CONTROL_PATH_AMBIGUOUS: "warn",
    CONTROL_PATH_NO_RESPONSE: "info",
    CONTROL_PATH_MULTIPLE: "info",
}

CONFIDENCE_LABELS: dict[str, str] = {
    CONFIDENCE_HIGH: "High",
    CONFIDENCE_MEDIUM: "Medium",
    CONFIDENCE_LOW: "Low",
    CONFIDENCE_UNKNOWN: "Unknown",
}

CONFIDENCE_TONES: dict[str, str] = {
    CONFIDENCE_HIGH: "ok",
    CONFIDENCE_MEDIUM: "warn",
    CONFIDENCE_LOW: "warn",
    CONFIDENCE_UNKNOWN: "muted",
}

DIRECTION_LABELS: dict[str, str] = {
    "positive": "RPM rises with duty",
    "negative": "RPM falls with duty",
}

#: Restore outcomes that mean the header is back where it was found. Anything
#: else leaves it moved and must be surfaced prominently (§1: "Restoration
#: failure must be surfaced prominently").
_RESTORE_OK = {"", "pending", "restored"}

_RESTORE_NOTES: dict[str, str] = {
    "write_failed": (
        "The header could not be returned to its original duty. It is holding the "
        "last duty the test wrote."
    ),
    "skipped_thermal_force": (
        "Thermal safety took over during the test, so the header was left at the "
        "forced duty rather than restored. Do not set it manually — the daemon "
        "will hand control back when the system cools."
    ),
    "skipped_shutting_down": (
        "The daemon began shutting down during the test, so its own shutdown "
        "restore owns the header rather than this diagnostic."
    ),
    "no_original_duty": (
        "The header's pre-test duty could not be read, so there was nothing to "
        "restore it to. It is holding the last duty the test wrote."
    ),
}


def humanise_token(token: str) -> str:
    return token.replace("_", " ").strip().capitalize() if token else UNKNOWN_TEXT


def relationship_label(token: str) -> str:
    return RELATIONSHIP_LABELS.get(token) or humanise_token(token)


def relationship_tone(token: str) -> str:
    """Tone for a relationship; an unrecognised one is muted, never critical."""
    return RELATIONSHIP_TONES.get(token, "muted")


def confidence_label(token: str) -> str:
    return CONFIDENCE_LABELS.get(token) or humanise_token(token)


def confidence_tone(token: str) -> str:
    return CONFIDENCE_TONES.get(token, "muted")


def restore_note(run: ControlPathRun | None) -> str:
    """Prominent warning when the header was not put back, else ``""``.

    Reads ``restore_outcome`` — the daemon's stable reason token — rather than
    the derived ``restore_failed`` boolean, so the wording can say *why*. An
    unrecognised outcome still produces a warning rather than silence, because
    the safe default for "we do not recognise this restore result" is to tell
    the user something happened.
    """
    if run is None:
        return ""
    outcome = run.restore_outcome or ""
    if outcome in _RESTORE_OK and not run.restore_failed:
        return ""
    note = _RESTORE_NOTES.get(outcome)
    if note:
        return note
    return (
        f"The header may not have been returned to its original duty "
        f"(restore result: {outcome or 'unknown'})."
    )


def _fmt_rpm(value: int | None) -> str:
    return f"{value} RPM" if value is not None else UNKNOWN_TEXT


def _fmt_change(pct: float | None) -> str:
    if pct is None:
        return UNKNOWN_TEXT
    return f"{pct:+.0f}%"


@dataclass
class CandidateRow:
    """One responding tach channel."""

    tach_id: str
    label: str
    monitor_only: bool
    confidence: str
    confidence_word: str
    confidence_tone: str
    direction_text: str
    baseline_text: str
    perturbed_text: str
    change_text: str
    repeatability_text: str


@dataclass
class QuietRow:
    """A channel that was watched and did not respond.

    Present so the result can say what it looked at. §2's example output lists
    the non-responding channels explicitly, and it matters: "fan1: no meaningful
    response" is evidence, whereas an absent row is indistinguishable from a
    channel nobody checked.
    """

    tach_id: str
    label: str
    monitor_only: bool
    baseline_text: str
    perturbed_text: str


@dataclass
class ControlPathView:
    """The rendered discovery result."""

    header_label: str = ""
    status_text: str = "Ready to start."
    progress_text: str = ""
    running: bool = False
    can_cancel: bool = False
    relationship: str = ""
    relationship_word: str = ""
    relationship_tone: str = "muted"
    confidence_word: str = ""
    confidence_tone: str = "muted"
    perturbation_text: str = ""
    resolution_text: str = ""
    candidates: list[CandidateRow] = field(default_factory=list)
    quiet: list[QuietRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Non-empty when the header was left moved. Rendered prominently.
    restore_warning: str = ""
    has_result: bool = False


def build_control_path_view(
    run: ControlPathRun | None,
    *,
    header_label: str,
) -> ControlPathView:
    """Render a discovery run. ``None`` is the pre-start state."""
    if run is None:
        return ControlPathView(header_label=header_label)

    view = ControlPathView(
        header_label=header_label,
        running=run.is_running,
        can_cancel=run.is_running,
        restore_warning=restore_note(run),
    )

    total = run.requested_cycles or len(run.cycles)
    if total:
        view.progress_text = f"{len(run.cycles)} of {total} cycles"

    if run.baseline_pct or run.perturbed_pct:
        arrow = "→"
        view.perturbation_text = (
            f"{run.baseline_pct}% {arrow} {run.perturbed_pct}% "
            f"({run.direction or 'up'}, {run.window_seconds}s per step)"
        )

    if run.is_running:
        view.status_text = f"Measuring… holding each duty for {run.window_seconds}s."
    elif run.state == "complete":
        view.status_text = "Finished."
    elif run.state:
        # `cancelled` / `aborted` / `failed`, plus anything a newer daemon adds.
        view.status_text = humanise_token(run.state)
        if run.detail:
            view.status_text = f"{view.status_text} — {run.detail}"

    summary = run.summary
    if summary is None:
        return view

    view.has_result = True
    view.relationship = summary.relationship
    view.relationship_word = relationship_label(summary.relationship)
    view.relationship_tone = relationship_tone(summary.relationship)
    view.confidence_word = confidence_label(summary.confidence)
    view.confidence_tone = confidence_tone(summary.confidence)

    if summary.measurement_resolution_ms is not None:
        view.resolution_text = (
            f"Telemetry updates about every {summary.measurement_resolution_ms} ms"
        )
    else:
        # §4: UNKNOWN rather than a guess, and said out loud rather than left
        # blank — a blank reads as "fine".
        view.resolution_text = "Telemetry update rate: unknown"

    responded = {c.tach_id for c in summary.candidates}
    view.candidates = [
        CandidateRow(
            tach_id=c.tach_id,
            label=c.label or c.tach_id,
            monitor_only=c.monitor_only,
            confidence=c.confidence,
            confidence_word=confidence_label(c.confidence),
            confidence_tone=confidence_tone(c.confidence),
            direction_text=DIRECTION_LABELS.get(c.direction) or humanise_token(c.direction),
            baseline_text=_fmt_rpm(c.baseline_rpm),
            perturbed_text=_fmt_rpm(c.perturbed_rpm),
            change_text=_fmt_change(c.change_pct),
            repeatability_text=f"responded in {c.cycles_responded} of {c.cycles_total} cycles",
        )
        for c in summary.candidates
    ]

    # Quiet channels, from the LAST cycle — one really-measured pair rather than
    # an average across cycles.
    last_cycle = run.cycles[-1] if run.cycles else None
    if last_cycle is not None:
        by_id = {o.tach_id: o for o in last_cycle.observations}
        for ch in run.channels:
            if ch.tach_id in responded:
                continue
            obs = by_id.get(ch.tach_id)
            view.quiet.append(
                QuietRow(
                    tach_id=ch.tach_id,
                    label=ch.label or ch.tach_id,
                    monitor_only=ch.monitor_only,
                    baseline_text=_fmt_rpm(obs.baseline_rpm) if obs else UNKNOWN_TEXT,
                    perturbed_text=_fmt_rpm(obs.perturbed_rpm) if obs else UNKNOWN_TEXT,
                )
            )

    view.notes = list(summary.confidence_notes)
    return view


def relationship_summary_line(record: ControlPathRecord | None) -> str:
    """The one-line "Control relationship" row for a header card (§6.3).

    ``""`` when nothing has been discovered — the card omits the row rather than
    showing an empty one, because a row reading "—" implies a test that ran and
    found nothing.
    """
    if record is None or not record.relationship:
        return ""
    target = record.tach_labels[0] if record.tach_labels else ""
    if len(record.tach_labels) > 1:
        target = f"{target} +{len(record.tach_labels) - 1} more"
    arrow = f" → {target}" if target else ""
    return f"{relationship_label(record.relationship)}{arrow}"
