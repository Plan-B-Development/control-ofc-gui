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

from ..api.models import (
    GpuVerifyResult,
    HardwareDiagnosticsResult,
    HwmonHeader,
    HwmonVerifyResult,
)
from ..ui.hwmon_guidance import dual_chip_verify_hint, verification_guidance
from .verify_evidence import VerifyEvidence, gpu_verify_evidence, hwmon_verify_evidence

# `VERDICT_PASS` / `VERDICT_WARN` / `VERDICT_FAIL` used to live here, as "the
# compact per-header verdict shown on a Hardware card (§7)". That badge was
# never wired at any point between GUI v2.56.0 and v2.76.5: `_on_verify_ok`
# puts `view.text` in a page-level message area, and `PwmHeaderCard`'s title row
# already carries the role pill. DEC-379 deleted them with `SSN-i`'s answer —
# each surface answers its own narrower question, and a per-header PASS/CHECK/
# FAIL is a THIRD granularity, narrower than either of the two that exist.
#
# Nothing is lost that cannot be re-derived: PASS/CHECK/FAIL was a word for a
# `chip_class`, and both axes it encoded already have a live reader —
# `chip_class` (how loud) and `evidence` (what it means about the hardware).
# A future badge derives from those rather than carrying a third column that
# is assembled and discarded on every result (row `ACK-y`).

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
#: have both *passed*, and its message is "PWM values held but RPM sensor
#: unavailable" — so the write was accepted as far as the daemon could tell,
#: and only the confirmation is missing. Recording it as `ineffective` is what
#: minted an unclearable alarm on any board with an empty header, a 3-pin fan,
#: or a pump at rest.
#:
#: The caveat that used to sit here — that a *failed* readback skips the clamp
#: guard rather than passing it, so `rpm_unavailable` could be returned having
#: established less than it says — **is retracted** (row `ACK-m`, DEC-373).
#: Daemon >= 2.48.0 returns `pwm_readback_unavailable` for that case instead,
#: so the sentence above is now unqualified. Both are `inconclusive`, and that
#: is the point: the split is about what the *user* is told, not about the
#: evidence, which was never anything either way. Against an older daemon the
#: caveat still holds and the unknown-token arm is not involved — that daemon
#: says `rpm_unavailable`, which this table answers correctly for the common
#: case and slightly over-generously for the rare one.
_OUTCOMES: dict[str, VerifyOutcome] = {
    "effective": VerifyOutcome(
        "PWM control is working correctly",
        "OK",
        "SuccessChip",
        PWM_EVIDENCE_EFFECTIVE,
    ),
    "pwm_enable_reverted": VerifyOutcome(
        "BIOS/EC reverted pwm_enable — fan control is being overridden",
        "BIOS reclaimed",
        "CriticalChip",
        PWM_EVIDENCE_INEFFECTIVE,
    ),
    "pwm_value_clamped": VerifyOutcome(
        "PWM value was clamped or ignored by hardware",
        "clamped",
        "WarningChip",
        PWM_EVIDENCE_INEFFECTIVE,
    ),
    "no_rpm_effect": VerifyOutcome(
        "PWM accepted but RPM did not change (fan may be disconnected or stalled)",
        "no RPM change",
        "WarningChip",
        PWM_EVIDENCE_INEFFECTIVE,
    ),
    "rpm_unavailable": VerifyOutcome(
        "PWM write accepted but RPM readback unavailable",
        "no tach",
        "CardMeta",
        PWM_EVIDENCE_INCONCLUSIVE,
    ),
    # `ACK-m` / DEC-373, daemon >= 2.48.0. Deliberately the same three
    # presentation columns as `rpm_unavailable` above: both are neutral, both
    # are `inconclusive`, and neither is a hardware fault. What differs is the
    # *sentence*, which is the whole reason this is a sixth token — the row
    # above claims the write was accepted, and here nothing established that.
    #
    # `CardMeta` rather than `WarningChip` on purpose. A transient sysfs read
    # failure is not a finding about the board, and painting it as one would
    # recreate the DEC-358 alarm this vocabulary exists to stop minting.
    "pwm_readback_unavailable": VerifyOutcome(
        "PWM readback failed, so whether the write held could not be confirmed",
        "no readback",
        "CardMeta",
        PWM_EVIDENCE_INCONCLUSIVE,
    ),
    # `TS-aw` / DEC-418. The header gained pump protection while the test held
    # it (a profile naming it a pump was activated, or it was assigned the
    # pump role), so the daemon stopped the test and floored its restore.
    # Nothing was measured, so it is `inconclusive` and neutral like the two
    # rows above: it is not a finding about the board. It makes no claim about
    # the restore (DEC-418 review `C1`): the daemon can report this token with
    # `restore_failed`, which `build_verify_result_view` renders as its own line
    # (`TS-bk`). The daemon's `details`, rendered beside it, states the floor
    # rule it applied.
    "pump_protected_mid_run": VerifyOutcome(
        "The header became pump-protected during the test, so the daemon stopped "
        "it before it measured anything",
        "stopped: pump",
        "CardMeta",
        PWM_EVIDENCE_INCONCLUSIVE,
    ),
}


#: What an unrecognised token says to the user, for either vocabulary.
#:
#: 273-i says render an unknown token rather than drop it, and this is the
#: sentence that stops the user reading it as a hardware fault: the only way to
#: reach it is a daemon newer than this GUI, so the action is an upgrade, not an
#: investigation. It is deliberately *not* a guess at what the token means —
#: inventing a meaning is how a verdict gets fabricated.
_UNRECOGNISED_HINT = "this daemon reports a result this version of the GUI does not recognise"


def _unrecognised_summary(token: str) -> str:
    """The bare token, with the version-gap hint. No ``Result: `` prefix."""
    return f"{token} ({_UNRECOGNISED_HINT})"


def outcome_for(result: str) -> VerifyOutcome:
    """The vocabulary row for one result token.

    ``summary`` is a bare sentence and **never carries the ``Result: ``
    prefix** — :func:`build_verify_result_view`, the one caller that renders it,
    owns that. Both fallback arms below used to bake the prefix in while the
    caller added it again, so an unrecognised token rendered as ``Result:
    Result: <token>`` (row `ACK-j`). One prefix, one owner.

    Two tokens are not in the table and must not be invented into one:

    * an ``error:`` prefix is the *sweep* failing (transport, permission, a
      daemon refusal), so it is loud but says nothing about the hardware. It
      carries no hint: this GUI synthesises the token itself, so it is not a
      version gap and telling the user to upgrade would be a lie;
    * an unrecognised token comes from a newer daemon. It renders verbatim
      rather than being dropped (the 273-i rule) and stays `inconclusive` —
      guessing an evidence value from a token we do not know is exactly how a
      verdict gets fabricated.
    """
    known = _OUTCOMES.get(result)
    if known is not None:
        return known
    if result.startswith("error:"):
        return VerifyOutcome(result, result, "CriticalChip", PWM_EVIDENCE_INCONCLUSIVE)
    return VerifyOutcome(
        _unrecognised_summary(result), result, "CardMeta", PWM_EVIDENCE_INCONCLUSIVE
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


def verify_sweep_chip_class(results: Sequence[str], *, restore_failed: bool = False) -> str:
    """The chip class for a sweep summary, from the same table as the record.

    Derived from `_OUTCOMES` rather than from a second list of "critical" and
    "warning" tokens, so the chip the user sees and the evidence the page
    persists can no longer disagree. The all-inconclusive case is neutral, not
    green: nothing failed, but nothing was demonstrated either.

    ``restore_failed`` — any header in the sweep was not put back — raises the
    chip to at least a warning, the rule a single result follows (`PTA-s`,
    `TS-bk`). It says nothing about the hardware, so the evidence is untouched.
    """
    outcomes = [outcome_for(r) for r in results]
    if any(o.chip_class == "CriticalChip" for o in outcomes):
        return "CriticalChip"
    if restore_failed or any(o.chip_class == "WarningChip" for o in outcomes):
        return "WarningChip"
    if any(o.evidence == PWM_EVIDENCE_EFFECTIVE for o in outcomes):
        return "SuccessChip"
    return "CardMeta"


#: Appended to a sweep line whose header the daemon could not put back (`PTA-s`).
SWEEP_NOT_RESTORED = "not restored"


def sweep_restore_failed_note(count: int) -> str:
    """The sentence closing a sweep summary in which ``count`` restores failed.

    The single-result sentence (:func:`restore_failed_line`) names the three
    causes and the duties; a sweep line has no room for either, so this keeps
    the one fact the user can act on and what to do about it. Empty for zero.
    """
    if count <= 0:
        return ""
    subject = (
        "1 header could not be put back after its test"
        if count == 1
        else f"{count} headers could not be put back after their tests"
    )
    return (
        f"{subject} and may still be at the test duty. If it stays there, re-activate your profile."
    )


@dataclass(frozen=True)
class VerifyResultView:
    """Render-ready presentation of one ``POST /hwmon/{id}/verify`` result."""

    header_id: str
    #: Theme chip class — "SuccessChip" | "WarningChip" | "CriticalChip" | "CardMeta".
    chip_class: str
    # No `summary` and no `verdict`. Both were produced on every build and read
    # by nothing (row `ACK-y`, DEC-379) — `summary` is still load-bearing INSIDE
    # `build_verify_result_view`, which composes `lines[0]` from it, so what went
    # is the copy on the returned view, not the value. Both consumers render
    # `text`; a caller that wants the bare sentence asks `outcome_for` for it,
    # which is where the vocabulary lives.
    #: The full multi-line body, already assembled in reading order.
    lines: list[str] = field(default_factory=list)
    #: True when the daemon could not put the header back where it found it.
    restore_failed: bool = False
    #: What the test did and read (`WIRE-f`). Its ``summary`` is already in
    #: ``lines``; its ``rows`` are the table behind "Show test evidence".
    evidence: VerifyEvidence = field(default_factory=VerifyEvidence)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


#: Chip classes a failed restore must not be shown under: the header may have
#: been left at the test duty, which is worth a warning whatever the verdict.
_QUIETER_THAN_WARNING = frozenset({"SuccessChip", "CardMeta"})


def restore_failed_line(result: HwmonVerifyResult) -> str:
    """What to say when the daemon could not put the header back (`TS-bk`).

    ``restore_failed`` is one boolean for three causes — a later diagnostic
    took the header over, the daemon was stopping, or the restore write
    failed — and does not say which, so the sentence names all three rather
    than guessing. Each leaves the header possibly at the test duty until
    something writes it again, which is the one fact the user can act on; the
    duty it held before is named because it is where the header belongs.
    Figures the daemon did not report are left out rather than invented.
    """
    test = f" ({result.test_pwm_percent}%)" if result.test_pwm_percent else ""
    before = result.initial_state.pwm_percent
    was = f" It was at {before}% before the test." if before is not None else ""
    return (
        "Restore: the daemon could not put this header back after the test — another "
        "diagnostic took it over, the daemon was stopping, or the restore write "
        f"failed — so it may still be at the test duty{test}.{was} If it stays "
        "there, re-activate your profile."
    )


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

    # `WIRE-f`: the one-line evidence summary replaces the bare RPM line. A
    # verify stopped for a mid-run pump never completed its settle, so the
    # builder claims no after-reading for it (DEC-418 review `C2`).
    evidence = hwmon_verify_evidence(result)
    if evidence.summary:
        lines.append(evidence.summary)
    restore_failed = bool(getattr(result, "restore_failed", False))
    if restore_failed:
        lines.append(restore_failed_line(result))
        if chip_class in _QUIETER_THAN_WARNING:
            chip_class = "WarningChip"

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
        chip_class=chip_class,
        lines=lines,
        restore_failed=restore_failed,
        evidence=evidence,
    )


# ── GPU fan verify — a SEPARATE vocabulary (row `ACK-k`) ─────────────────────
#
# `POST /gpu/{id}/fan/verify` answers with its own token set. Four names are
# shared with the hwmon set above — `effective`, `no_rpm_effect`,
# `pwm_enable_reverted`, `rpm_unavailable` — and TWO of them MEAN SOMETHING
# ELSE, which is the whole reason these are two tables and not one.
#
# **Do not merge them.** DEC-358's rule is "one token, one row", and it is a
# rule *within* a vocabulary — the shared names are what make merging look
# attractive and make it wrong. Concretely: `rpm_unavailable` is neutral
# (`CardMeta`) on the hwmon side, where a header without a tach is ordinary
# hardware, and a `WarningChip` here, where a GPU exposing no fan-RPM sensor is
# unexpected enough to mention. `no_rpm_effect` is a Warning there and Critical
# here for the same reason: a case fan behind a splitter has benign
# explanations, a GPU fan that ignored an applied curve does not.
#
# This lives here rather than inlined in `system_state_page` for DEC-276's
# reason — a rule inside one consumer is a rule the other consumers cannot
# follow — and NOT because the two tables are converging. The type is
# deliberately narrower than `VerifyOutcome`: the GPU path feeds no sweep and no
# board-note evidence, so it has no `short` or `evidence` column. Giving it an
# `evidence` column it does not use is exactly the invitation this comment
# exists to refuse. (It named a `verdict` column too, until DEC-379 deleted that
# one from `VerifyOutcome` as well — the hwmon path had no reader for it either,
# which is the same refusal, arrived at rather later; row `ACK-y`.)


@dataclass(frozen=True)
class GpuVerifyOutcome:
    """What one ``POST /gpu/{id}/fan/verify`` result token means."""

    summary: str  # the user-facing sentence — no ``Result: `` prefix
    chip_class: str  # shared theme vocabulary from `theme.py`


_GPU_OUTCOMES: dict[str, GpuVerifyOutcome] = {
    "effective": GpuVerifyOutcome(
        "GPU fan control is working — the fan responded to the test.",
        "SuccessChip",
    ),
    # Reached through `gpu_result_outcome` only when the card reported
    # `zero_rpm_enabled: true` — the fact that makes a stopped fan normal here.
    "zero_rpm_suppressed": GpuVerifyOutcome(
        "GPU fan control works; zero-RPM idle is enabled on this card, so the fan "
        "stays stopped until the GPU warms past its stop temperature (normal).",
        "SuccessChip",
    ),
    "rpm_unavailable": GpuVerifyOutcome(
        "Write confirmed via curve read-back, but this GPU exposes no fan-RPM sensor.",
        "WarningChip",
    ),
    "curve_not_applied": GpuVerifyOutcome(
        "The GPU ignored the fan-control write.",
        "CriticalChip",
    ),
    "no_rpm_effect": GpuVerifyOutcome(
        "The fan curve was applied but the fan did not respond.",
        "CriticalChip",
    ),
    "pwm_enable_reverted": GpuVerifyOutcome(
        "The BIOS/EC reclaimed GPU fan control during the test.",
        "CriticalChip",
    ),
    "write_failed": GpuVerifyOutcome(
        "The GPU fan write was rejected by the driver/firmware.",
        "CriticalChip",
    ),
}


#: `zero_rpm_suppressed` when the card did not report zero-RPM idle as ON. The
#: daemon (`gpu.rs`) emits the token only when `zero_rpm_enabled` is `true`, so
#: this is a newer or different daemon's case; the summary then must not call a
#: stopped fan "normal" on the strength of a setting nobody read (`WIRE-f`).
_GPU_ZERO_RPM_UNCONFIRMED = GpuVerifyOutcome(
    "The fan curve was applied and the fan stayed stopped; the daemon put it down to "
    "zero-RPM idle, but this card did not report zero-RPM idle as enabled. Re-test "
    "with the GPU under load.",
    "WarningChip",
)


def gpu_result_outcome(result: GpuVerifyResult) -> GpuVerifyOutcome:
    """The row for a whole GPU result — :func:`gpu_outcome_for`, except that
    ``zero_rpm_suppressed`` is called normal only when the card's own
    ``zero_rpm_enabled`` says zero-RPM idle is on (`WIRE-f`)."""
    if result.result == "zero_rpm_suppressed" and result.final_state.zero_rpm_enabled is not True:
        return _GPU_ZERO_RPM_UNCONFIRMED
    return gpu_outcome_for(result.result)


@dataclass(frozen=True)
class GpuVerifyResultView:
    """Render-ready GPU verify result: the lines above the evidence table."""

    chip_class: str
    lines: list[str] = field(default_factory=list)
    evidence: VerifyEvidence = field(default_factory=VerifyEvidence)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def build_gpu_verify_result_view(
    result: GpuVerifyResult, fix_lines: Sequence[str] = ()
) -> GpuVerifyResultView:
    """Assemble one GPU verify result (`WIRE-f`): the verdict, the one-line
    evidence summary, the page's "To fix" lines, and the restore note.

    Moved from ``system_state_page._show_gpu_verify_result`` so the summary
    line and the evidence table come from the same builder as the hwmon path's
    — the two used to report the same test differently. ``fix_lines`` stays the
    caller's (``readiness_report.gpu_verify_problems`` is a widget-layer module).
    """
    outcome = gpu_result_outcome(result)
    lines = [f"Result: {outcome.summary}"]
    evidence = gpu_verify_evidence(result)
    if evidence.summary:
        lines.append(evidence.summary)
    lines.extend(f"• To fix: {fix}" for fix in fix_lines)
    if result.restore_failed:
        lines.append("Note: the GPU fan could not be restored — set it manually if needed.")
    return GpuVerifyResultView(chip_class=outcome.chip_class, lines=lines, evidence=evidence)


def gpu_outcome_for(result: str) -> GpuVerifyOutcome:
    """The GPU vocabulary row for one result token.

    Same contract as :func:`outcome_for`: the summary is bare and the caller
    owns the ``Result: `` prefix, and an unrecognised token renders verbatim
    with the version-gap hint rather than being dropped (273-i).
    """
    known = _GPU_OUTCOMES.get(result)
    if known is not None:
        return known
    return GpuVerifyOutcome(_unrecognised_summary(result), "CardMeta")
