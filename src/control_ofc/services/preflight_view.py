"""Headless view-model for the diagnostic safety preflight (AIO Phase 8 §6.1).

Renders the daemon's verdict. It does **not** compute one.

That is the whole contract, and §6.1 states it outright: "Do not make the GUI
responsible for enforcing safety; it reflects daemon decisions." So this module
maps stable tokens to wording and tone, and reads ``verdict``/``blocking``
straight off the report. It must never roll the rows up itself — a second copy of
the roll-up rule is a copy that can disagree with the daemon, and the one that
disagrees is always the one the user is looking at.

Unrecognised tokens render verbatim rather than being dropped (273-i): a newer
daemon adding a check must show up as a row the user can read, not as a silently
shorter list that looks like fewer things were checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from control_ofc.api.models import (
    PREFLIGHT_BLOCKED,
    PREFLIGHT_FAIL,
    PREFLIGHT_NOT_APPLICABLE,
    PREFLIGHT_PASS,
    PREFLIGHT_READY,
    PREFLIGHT_UNKNOWN,
    PREFLIGHT_VERDICT_WARN,
    PREFLIGHT_WARN,
    PreflightReport,
)

UNKNOWN_TEXT = "—"

#: Check-id → the label shown in the summary. The daemon owns the ids; the GUI
#: owns the wording, which is why these live here and not on the wire.
CHECK_LABELS: dict[str, str] = {
    "target_discoverable": "Target header",
    "header_role": "Header role",
    "pwm_writable": "PWM write",
    "pwm_readback": "PWM readback",
    "control_ownership": "Control ownership",
    "safe_minimum": "Safe minimum",
    "temperature_source": "Temperature source",
    "thermal_state": "Thermal safety",
    "reclaim_state": "Reclaim state",
    "original_state": "Original state",
    "supporting_cooling": "Supporting cooling",
}

#: Per-state chip wording.
STATE_WORDS: dict[str, str] = {
    PREFLIGHT_PASS: "OK",
    PREFLIGHT_WARN: "CHECK",
    PREFLIGHT_FAIL: "BLOCKED",
    PREFLIGHT_UNKNOWN: "UNKNOWN",
    PREFLIGHT_NOT_APPLICABLE: "N/A",
}

#: Per-state theme tone, keyed to `ui.components.badges.pill_class_for`.
#:
#: ``unknown`` and ``not_applicable`` are **muted, never bad**. §5's rule is that
#: lack of evidence must not become a PASS; the mirror of it — and the one that
#: actually misleads people in a UI — is that it must not become a FAILURE
#: either. Same posture as `validation_view`'s result tones.
STATE_TONES: dict[str, str] = {
    PREFLIGHT_PASS: "ok",
    PREFLIGHT_WARN: "warn",
    PREFLIGHT_FAIL: "crit",
    PREFLIGHT_UNKNOWN: "muted",
    PREFLIGHT_NOT_APPLICABLE: "muted",
}

VERDICT_WORDS: dict[str, str] = {
    PREFLIGHT_READY: "Ready to test",
    PREFLIGHT_VERDICT_WARN: "Ready, with warnings",
    PREFLIGHT_BLOCKED: "Cannot test",
}

VERDICT_TONES: dict[str, str] = {
    PREFLIGHT_READY: "ok",
    PREFLIGHT_VERDICT_WARN: "warn",
    PREFLIGHT_BLOCKED: "crit",
}


def humanise_token(token: str) -> str:
    """``some_unknown_check`` → ``Some unknown check``. For tokens with no label."""
    return token.replace("_", " ").strip().capitalize() if token else UNKNOWN_TEXT


def check_label(check_id: str) -> str:
    return CHECK_LABELS.get(check_id) or humanise_token(check_id)


def state_word(state: str) -> str:
    return STATE_WORDS.get(state) or (state.upper() if state else UNKNOWN_TEXT)


def state_tone(state: str) -> str:
    """Theme tone for a check state; an unrecognised one is muted, never bad."""
    return STATE_TONES.get(state, "muted")


@dataclass
class PreflightRow:
    """One rendered check."""

    check_id: str
    label: str
    state: str
    state_word: str
    tone: str
    detail: str
    #: Did this row cause the block? Read from the report's own ``blocking``
    #: list, never re-derived from ``state``.
    is_blocking: bool = False


@dataclass
class PreflightView:
    """The rendered preflight summary."""

    header_id: str = ""
    diagnostic: str = ""
    verdict: str = ""
    verdict_word: str = ""
    verdict_tone: str = "muted"
    rows: list[PreflightRow] = field(default_factory=list)
    #: True when the daemon said ``blocked``. Read, not derived.
    blocked: bool = False
    #: The reasons to show above the Start button when blocked.
    blocking_reasons: list[str] = field(default_factory=list)
    #: True when there is no report to render — an older daemon, or a request
    #: that failed. Distinct from a report that came back blocked.
    unavailable: bool = False
    unavailable_reason: str = ""

    @property
    def can_start(self) -> bool:
        """May the diagnostic be started?

        An **unavailable** preflight does not block: the daemon still runs its
        own guards on the POST, and refusing to offer the button because an
        advisory endpoint is missing would make an older daemon less usable than
        it was before this feature existed.
        """
        return not self.blocked


def build_preflight_view(
    report: PreflightReport | None,
    *,
    unavailable_reason: str = "",
) -> PreflightView:
    """Render a preflight report.

    ``report=None`` produces the unavailable state, which is advisory rather than
    blocking — see :attr:`PreflightView.can_start`.
    """
    if report is None:
        return PreflightView(
            verdict_word="Safety checks unavailable",
            verdict_tone="muted",
            unavailable=True,
            unavailable_reason=unavailable_reason
            or "This daemon does not publish a diagnostic preflight.",
        )

    blocking = set(report.blocking)
    rows = [
        PreflightRow(
            check_id=c.check_id,
            label=check_label(c.check_id),
            state=c.state,
            state_word=state_word(c.state),
            tone=state_tone(c.state),
            detail=c.detail or "",
            is_blocking=c.check_id in blocking,
        )
        for c in report.checks
    ]
    reasons = [r.detail or r.label for r in rows if r.is_blocking]
    # `P8-aj`: a blocker the daemon NAMED but did not also publish as a check
    # produced no row at all, so `blocked` was true with an empty reason list and
    # the dialog rendered "Cannot run this test:" followed by a bullet and
    # nothing — Start correctly refused, and no reason given, in the one case
    # where the reason matters most. Fall back to the id itself, humanised;
    # a token the user can search for beats silence.
    named = {c.check_id for c in report.checks}
    reasons += [humanise_token(cid) for cid in report.blocking if cid not in named]

    return PreflightView(
        header_id=report.header_id,
        diagnostic=report.diagnostic,
        verdict=report.verdict,
        verdict_word=VERDICT_WORDS.get(report.verdict) or humanise_token(report.verdict),
        verdict_tone=VERDICT_TONES.get(report.verdict, "muted"),
        rows=rows,
        # Read from the daemon's own verdict, and from its own list of blockers.
        # Deliberately NOT `any(r.state == FAIL for r in rows)`: that would be a
        # second copy of the roll-up rule, and the copy the user sees is the one
        # that would be wrong when they disagree.
        #
        # `or bool(report.blocking)` is not a re-derivation — it reads a SECOND
        # daemon-authored field. It matters because `verdict` is matched exactly:
        # a future daemon emitting a token outside {ready, warn, blocked} would
        # otherwise fail OPEN on the one field that gates a hardware-perturbing
        # action. Today the two always agree, so this changes nothing; it means
        # the unknown-token case fails safe rather than by luck.
        blocked=report.verdict == PREFLIGHT_BLOCKED or bool(report.blocking),
        blocking_reasons=reasons,
    )
