"""View-model for the PWM/RPM characterisation dialog (AIO-MB Phase 3).

Qt-free by design, like its siblings in this package: every decision about what
the dialog *says* is made here and unit-tested headlessly, and the widget is a
thin renderer over :class:`CharacterizationView`.

Two rules in here are correctness requirements rather than presentation taste,
and both come straight from ``AIO-Phase3.md``:

1. **The three axes stay separate.** Command acceptance, PWM readback and
   physical RPM response are reported as three verdicts. Collapsing them into one
   pass/fail is the defect the brief calls out by name.
2. **A device that does not follow PWM is not automatically faulty.** A
   non-monotonic curve, or RPM that never moves while the readback is perfect, is
   reported as an observation — never as "PWM writes failed". A pump in its
   startup/self-bleeding period produces exactly that signature.

Unknown tokens are rendered, never dropped (the 273-i rule): a newer daemon may
add a verdict this build has not heard of, and silently dropping the row would
shorten the sweep without saying so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..api.models import CharacterizationRun, HwmonHeader

# The brief's mandated pre-run wording. Generic on purpose: the validation
# cooler's startup override runs ~50 s, but the duration is a property of the
# individual pump and no OS-visible signal announces it, so hardcoding a wait for
# every device is explicitly forbidden. See register row ``AIO3-a``.
PUMP_STARTUP_WARNING = (
    "Some pumps temporarily override PWM during startup or internal "
    "thermal-protection behaviour. If RPM does not follow PWM, allow that "
    "behaviour to finish before concluding that control is unavailable."
)

ENGINE_PAUSE_WARNING = (
    "Curve control for every fan is paused while this runs, and each fan holds "
    "its last duty. Thermal safety is unaffected and still overrides everything."
)


def _fmt_pct(value: int | None) -> str:
    return "—" if value is None else f"{value}%"


def _fmt_rpm(value: int | None) -> str:
    return "—" if value is None else f"{value}"


def _humanise_token(token: str) -> str:
    """Render a token this build does not recognise, rather than dropping it."""
    return token.replace("_", " ").strip().capitalize() or "Unknown"


@dataclass(frozen=True)
class CharRow:
    """One table row: the duty asked for, what came back, and a verdict."""

    pwm: str
    readback: str
    rpm: str
    result: str
    state: str


@dataclass(frozen=True)
class VerdictChip:
    label: str
    value: str
    state: str


@dataclass(frozen=True)
class CharacterizationView:
    header_label: str
    rows: list[CharRow] = field(default_factory=list)
    progress_text: str = ""
    status_text: str = ""
    running: bool = False
    can_cancel: bool = False
    verdicts: list[VerdictChip] = field(default_factory=list)
    observed_range: str = ""
    notes: list[str] = field(default_factory=list)


# Per-point result wording. A row's verdict combines the two axes, but never
# reports an RPM observation as a write failure.
_READBACK_ROW = {
    "match": ("OK", "ok"),
    "clamped": ("PWM clamped", "warn"),
    "reverted": ("Control reclaimed", "critical"),
    "unavailable": ("Readback unavailable", "neutral"),
}

_COMMAND_CHIP = {
    "pass": ("Accepted", "ok"),
    "partial": ("Partly accepted", "warn"),
    "fail": ("Rejected", "critical"),
}
_READBACK_CHIP = {
    "pass": ("Correct", "ok"),
    "clamped": ("Clamped", "warn"),
    "reverted": ("Reclaimed", "critical"),
    "unavailable": ("Unavailable", "neutral"),
}
_RPM_CHIP = {
    "responsive": ("Responds", "ok"),
    "no_response": ("No response", "warn"),
    "unavailable": ("No tachometer", "neutral"),
}

_TERMINAL_STATUS = {
    "complete": "Finished.",
    "cancelled": "Cancelled — the header was restored to its original speed.",
    "aborted": "Stopped early.",
    "failed": "Stopped — a PWM write failed.",
}


def _row_for(point) -> CharRow:
    """Build one row. A write failure outranks any readback wording, because the
    write is the thing that did not happen."""
    if not point.command_accepted:
        result, state = "Write failed", "critical"
    else:
        result, state = _READBACK_ROW.get(
            point.readback_verdict,
            (_humanise_token(point.readback_verdict), "neutral"),
        )
        # A correct readback with a motionless fan is worth surfacing on the row,
        # but as an observation — never as a failed write.
        if result == "OK" and point.rpm_verdict == "unchanged":
            result, state = "No RPM change", "warn"
    return CharRow(
        pwm=_fmt_pct(point.requested_pct),
        readback=_fmt_pct(point.readback_pct),
        rpm=_fmt_rpm(point.rpm_after),
        result=result,
        state=state,
    )


def build_characterization_view(
    run: CharacterizationRun | None,
    *,
    header_label: str,
) -> CharacterizationView:
    """Render-ready state for the dialog. ``None`` means nothing has started."""
    if run is None:
        return CharacterizationView(
            header_label=header_label,
            status_text="Ready to start.",
            progress_text="",
        )

    rows = [_row_for(p) for p in run.points]
    total = len(run.requested_points_pct) or len(run.points)
    running = run.is_running
    progress_text = f"{len(run.points)} of {total} points"

    if running:
        status_text = f"Measuring… holding {run.settle_seconds}s per step."
    else:
        status_text = _TERMINAL_STATUS.get(run.state, _humanise_token(run.state))
        if run.detail:
            status_text = f"{status_text} {run.detail}"

    verdicts: list[VerdictChip] = []
    observed_range = ""
    notes: list[str] = []
    summary = run.summary
    if summary is not None:
        for label, token, table in (
            ("PWM command", summary.command_acceptance, _COMMAND_CHIP),
            ("PWM readback", summary.pwm_readback, _READBACK_CHIP),
            ("RPM response", summary.rpm_response, _RPM_CHIP),
        ):
            value, state = table.get(token, (_humanise_token(token), "neutral"))
            verdicts.append(VerdictChip(label=label, value=value, state=state))

        if summary.min_rpm is not None and summary.max_rpm is not None:
            observed_range = f"{summary.min_rpm}-{summary.max_rpm} RPM"
            if summary.min_tested_pct is not None and summary.max_tested_pct is not None:
                observed_range += f" across {summary.min_tested_pct}-{summary.max_tested_pct}% PWM"

        if summary.possible_device_override:
            notes.append(
                "PWM was accepted and read back correctly, but the fan speed did "
                "not follow it. " + PUMP_STARTUP_WARNING
            )
        if summary.interference_detected:
            notes.append(
                "Another controller (BIOS, EC, or the board's own firmware) took "
                "the header back during the test, so these readings are not a "
                "clean measurement of what the daemon commanded."
            )
        if summary.monotonic is False:
            notes.append(
                "Fan speed did not rise steadily with PWM. That is an observation, "
                "not a fault — many pumps and fans have a non-linear or hysteretic "
                "response."
            )
        if summary.dead_zone_upper_pct is not None:
            notes.append(
                f"Speed was flat up to {summary.dead_zone_upper_pct}% before it "
                "started rising, which suggests a dead zone at the low end."
            )
        if summary.clamp_pct is not None:
            notes.append(
                f"The header reported {summary.clamp_pct}% back for more than one "
                "requested duty, which suggests the hardware pins PWM there."
            )

    if run.restore_failed:
        notes.append(
            "Restoring the original speed failed, so the header is still at the "
            "last tested duty. Re-activate your profile to take control back."
        )

    return CharacterizationView(
        header_label=header_label,
        rows=rows,
        progress_text=progress_text,
        status_text=status_text,
        running=running,
        can_cancel=running,
        verdicts=verdicts,
        observed_range=observed_range,
        notes=notes,
    )


def pre_run_warnings(header: HwmonHeader | None, *, is_pump: bool) -> list[str]:
    """Warnings shown before a sweep starts.

    ``is_pump`` must be the reconstructed UNION — the wire ``role``, the daemon's
    own label, and any liquid-cooler evidence — never the display ``role`` alone
    (DEC-312). Reading ``role == "pump"`` would drop the warning for a header the
    user has re-labelled ``chassis_fan`` while the daemon still protects it as a
    pump, which is precisely the case where the warning matters most.
    """
    warnings = [ENGINE_PAUSE_WARNING]
    if is_pump:
        warnings.append(
            "This header is protected as a pump: it will never be driven below "
            "30%, and never stopped."
        )
        warnings.append(PUMP_STARTUP_WARNING)
    if header is not None and not header.is_writable:
        warnings.append("This header is read-only, so the test cannot drive it.")
    return warnings
