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

from dataclasses import dataclass, field

from ..api.models import HardwareDiagnosticsResult, HwmonHeader, HwmonVerifyResult
from ..ui.hwmon_guidance import dual_chip_verify_hint, verification_guidance

#: Result token -> (one-line summary, chip class). The chip class is the shared
#: vocabulary from ``theme.py``; the summary is the user-facing sentence.
#:
#: `pwm_enable_reverted` is the only Critical: it means something else is
#: actively fighting the daemon for the header. A clamp or an unmoved RPM is a
#: Warning because both have benign explanations (a firmware minimum, a fan
#: behind a splitter), and an unavailable RPM is neutral because a header
#: without a tach is normal hardware, not a fault (§18).
_STATUS_MAP: dict[str, tuple[str, str]] = {
    "effective": ("PWM control is working correctly", "SuccessChip"),
    "pwm_enable_reverted": (
        "BIOS/EC reverted pwm_enable — fan control is being overridden",
        "CriticalChip",
    ),
    "pwm_value_clamped": ("PWM value was clamped or ignored by hardware", "WarningChip"),
    "no_rpm_effect": (
        "PWM accepted but RPM did not change (fan may be disconnected or stalled)",
        "WarningChip",
    ),
    "rpm_unavailable": ("PWM write accepted but RPM readback unavailable", "CardMeta"),
}

#: The compact per-header verdict shown on a Hardware card (§7).
VERDICT_PASS = "PASS"
VERDICT_WARN = "CHECK"
VERDICT_FAIL = "FAIL"

_VERDICTS: dict[str, str] = {
    "effective": VERDICT_PASS,
    "pwm_enable_reverted": VERDICT_FAIL,
    "pwm_value_clamped": VERDICT_WARN,
    "no_rpm_effect": VERDICT_WARN,
    "rpm_unavailable": VERDICT_WARN,
}


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
    summary, chip_class = _STATUS_MAP.get(result.result, (f"Result: {result.result}", "CardMeta"))
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
        verdict=_VERDICTS.get(result.result, VERDICT_WARN),
        lines=lines,
        restore_failed=bool(getattr(result, "restore_failed", False)),
    )
