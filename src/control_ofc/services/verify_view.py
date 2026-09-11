"""View-model for a PWM verify result (AIO-MB Phase 6, DEC-318).

Extracted from ``ui/pages/system_state_page.py::_show_verify_result``, where
this wording lived inline as a ``status_map`` dict plus assembly code. The
extraction is the change, not a tidy-up: Phase 6 makes the Hardware page the
primary entry point for PWM testing (§7), and CLAUDE.md records "a rule that
lives inside one consumer is a rule the other consumers cannot follow" as a
repeat failure here — the accessible-naming rule was refined across three ADRs
while sitting in a private method on one page, leaving fourteen other surfaces
unfixed. Re-deriving this wording on the Hardware page would have been the same
bug in a new coat, on a result whose whole job is to be *precise* about whether
motherboard PWM control works.

Qt-free, so both pages render one object and cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..api.models import HardwareDiagnosticsResult, HwmonHeader, HwmonVerifyResult
from ..ui.hwmon_guidance import dual_chip_verify_hint, verification_guidance

#: The compact per-header verdict shown on a Hardware card (§7).
VERDICT_PASS = "PASS"
VERDICT_WARN = "CHECK"
VERDICT_FAIL = "FAIL"

#: What one verify result says about whether motherboard PWM control *works*.
#:
#: This is a SEPARATE axis from severity, and conflating the two is the DEC-358
#: defect. "How loudly should this be shown?" and "what did we learn about the
#: hardware?" have different answers for the same token: `rpm_unavailable` is
#: quiet (a header with no tach is normal hardware) but tells us *nothing*,
#: while `error:` is loud and also tells us nothing. Only a result that actually
#: exercised the write path is evidence either way.
PWM_EVIDENCE_EFFECTIVE = "effective"
PWM_EVIDENCE_INEFFECTIVE = "ineffective"
PWM_EVIDENCE_INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class VerifyOutcome:
    """Everything one ``POST /hwmon/{id}/verify`` result token means.

    One row per token, because this vocabulary had drifted into three copies —
    the summary/chip map here, a `critical_keys`/`warning_keys`/`short` trio
    inlined in ``system_state_page._show_verify_all_summary``, and an
    `all(outcome == "effective")` test in ``_record_verify_outcome``. They
    disagreed about `rpm_unavailable`, which is what let one sweep show the user
    a green "complete" chip while persisting `ineffective` (DEC-358). *One
    token, one row.*
    """

    summary: str  # the user-facing sentence
    short: str  # compact label for a sweep line
    chip_class: str  # shared theme vocabulary from `theme.py`
    verdict: str  # VERDICT_* — the compact card badge
    evidence: str  # PWM_EVIDENCE_* — what this says about the hardware


#: `pwm_enable_reverted` is the only Critical: it means something else is
#: actively fighting the daemon for the header. A clamp or an unmoved RPM is a
#: Warning because both have benign explanations (a firmware minimum, a fan
#: behind a splitter), and an unavailable RPM is neutral because a header
#: without a tach is normal hardware, not a fault (§18).
#:
#: Evidence column: a clamp and an unmoved RPM are `ineffective` because both
#: are the write path failing to take. `rpm_unavailable` is `inconclusive`: the
#: daemon reaches it only after its reverted-enable and clamped-value guards
#: have both fallen through, and its message is "PWM values held but RPM sensor
#: unavailable" — so the write was accepted as far as the daemon could tell,
#: and only the confirmation is missing. (Not quite "the write landed": if the
#: readback of `pwmN` itself fails, the clamp guard is skipped rather than
#: passed. That is narrower still, and `inconclusive` remains the honest answer
#: for it — `ineffective` would assert a failure nothing established. Row
#: `ACK-m`.) Recording it as `ineffective` is what minted an unclearable alarm
#: on any board with an empty header, a 3-pin fan, or a pump at rest.
_OUTCOMES: dict[str, VerifyOutcome] = {
    "effective": VerifyOutcome(
        "PWM control is working correctly",
        "OK",
        "SuccessChip",
        VERDICT_PASS,
        PWM_EVIDENCE_EFFECTIVE,
    ),
    "pwm_enable_reverted": VerifyOutcome(
        "BIOS/EC reverted pwm_enable — fan control is being overridden",
        "BIOS reclaimed",
        "CriticalChip",
        VERDICT_FAIL,
        PWM_EVIDENCE_INEFFECTIVE,
    ),
    "pwm_value_clamped": VerifyOutcome(
        "PWM value was clamped or ignored by hardware",
        "clamped",
        "WarningChip",
        VERDICT_WARN,
        PWM_EVIDENCE_INEFFECTIVE,
    ),
    "no_rpm_effect": VerifyOutcome(
        "PWM accepted but RPM did not change (fan may be disconnected or stalled)",
        "no RPM change",
        "WarningChip",
        VERDICT_WARN,
        PWM_EVIDENCE_INEFFECTIVE,
    ),
    "rpm_unavailable": VerifyOutcome(
        "PWM write accepted but RPM readback unavailable",
        "no tach",
        "CardMeta",
        VERDICT_WARN,
        PWM_EVIDENCE_INCONCLUSIVE,
    ),
}


def outcome_for(result: str) -> VerifyOutcome:
    """The vocabulary row for one result token.

    Two tokens are not in the table and must not be invented into one:

    * an ``error:`` prefix is the *sweep* failing (transport, permission, a
      daemon refusal), so it is loud but says nothing about the hardware;
    * an unrecognised token comes from a newer daemon. It renders verbatim
      rather than being dropped (the 273-i rule) and stays `inconclusive` —
      guessing an evidence value from a token we do not know is exactly how a
      verdict gets fabricated.
    """
    known = _OUTCOMES.get(result)
    if known is not None:
        return known
    if result.startswith("error:"):
        return VerifyOutcome(
            f"Result: {result}", result, "CriticalChip", VERDICT_FAIL, PWM_EVIDENCE_INCONCLUSIVE
        )
    return VerifyOutcome(
        f"Result: {result}", result, "CardMeta", VERDICT_WARN, PWM_EVIDENCE_INCONCLUSIVE
    )


def verify_sweep_outcome(results: Sequence[str]) -> str | None:
    """What a whole sweep proved about PWM control, or ``None`` if nothing.

    ``None`` means *leave the recorded result alone* — it is not a third
    verdict. A sweep in which every header came back inconclusive (no tach on
    any of them) has not refuted a previous clean test and has not confirmed
    one either; overwriting either way would be a claim nothing measured.

    One bad header still condemns the sweep: a board note says the BIOS may
    override fan control, and one header that did not take the write is exactly
    the case the note is about. But an inconclusive header no longer votes,
    which is the fix — previously it voted *against*.
    """
    evidences = [outcome_for(r).evidence for r in results]
    if any(e == PWM_EVIDENCE_INEFFECTIVE for e in evidences):
        return PWM_EVIDENCE_INEFFECTIVE
    if any(e == PWM_EVIDENCE_EFFECTIVE for e in evidences):
        return PWM_EVIDENCE_EFFECTIVE
    return None


def verify_sweep_chip_class(results: Sequence[str]) -> str:
    """The chip class for a sweep summary, from the same table as the record.

    Derived from `_OUTCOMES` rather than from a second list of "critical" and
    "warning" tokens, so the chip the user sees and the evidence the page
    persists can no longer disagree. The all-inconclusive case is neutral, not
    green: nothing failed, but nothing was demonstrated either.
    """
    outcomes = [outcome_for(r) for r in results]
    if any(o.chip_class == "CriticalChip" for o in outcomes):
        return "CriticalChip"
    if any(o.chip_class == "WarningChip" for o in outcomes):
        return "WarningChip"
    if any(o.evidence == PWM_EVIDENCE_EFFECTIVE for o in outcomes):
        return "SuccessChip"
    return "CardMeta"


@dataclass(frozen=True)
class VerifyResultView:
    """Render-ready presentation of one ``POST /hwmon/{id}/verify`` result."""

    header_id: str
    summary: str
    #: Theme chip class — "SuccessChip" | "WarningChip" | "CriticalChip" | "CardMeta".
    chip_class: str
    #: Compact verdict for a card badge.
    verdict: str
    #: The full multi-line body, already assembled in reading order.
    lines: list[str] = field(default_factory=list)
    #: True when the daemon could not put the header back where it found it.
    restore_failed: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def build_verify_result_view(
    result: HwmonVerifyResult,
    *,
    header: HwmonHeader | None = None,
    diagnostics: HardwareDiagnosticsResult | None = None,
) -> VerifyResultView:
    """Assemble the user-facing verify result.

    ``header`` and ``diagnostics`` are optional: both feed the board-specific
    next-step guidance, and their absence degrades the advice rather than the
    result. An unrecognised ``result`` token renders verbatim rather than being
    dropped (the 273-i rule) — a newer daemon must not make a verdict vanish.
    """
    outcome = outcome_for(result.result)
    summary, chip_class = outcome.summary, outcome.chip_class
    lines = [f"Result: {summary}"]
    if result.details:
        lines.append(result.details)

    init, final = result.initial_state, result.final_state
    if init.rpm is not None and final.rpm is not None:
        lines.append(f"RPM: {init.rpm} → {final.rpm}")

    chip_name = header.chip_name if header else ""
    board_vendor = ""
    expected_chips: list[str] = []
    detected: list[str] = []
    if diagnostics is not None:
        board_vendor = diagnostics.board.vendor
        expected_chips = list(diagnostics.expected_chips)
        detected = [c.chip_name for c in diagnostics.hwmon.chips_detected]

    guidance = verification_guidance(result.result, board_vendor, chip_name)
    if guidance:
        lines.extend(("", f"Next step: {guidance}"))

    dual_hint = dual_chip_verify_hint(result.result, expected_chips, detected)
    if dual_hint:
        lines.extend(("", dual_hint))

    return VerifyResultView(
        header_id=result.header_id,
        summary=summary,
        chip_class=chip_class,
        verdict=outcome.verdict,
        lines=lines,
        restore_failed=bool(getattr(result, "restore_failed", False)),
    )
