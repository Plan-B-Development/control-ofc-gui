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


#: Shown wherever a measurement is genuinely absent. Never substitute 0.
UNKNOWN_TEXT = "—"

#: The daemon sub-samples RPM every 500 ms during a sweep
#: (`CHARACTERIZATION_SAMPLE_INTERVAL`), so a latency is only ever accurate to
#: about half a second. One decimal place and a "~" is the honest presentation;
#: milliseconds would invent precision the measurement does not have (§9).
_SECONDS_DECIMALS = 1


def _fmt_pct(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value}%"


def _fmt_seconds(ms: int | None) -> str:
    """A duration in seconds, or an em dash when it was never measured.

    ``None`` is the common and legitimate case: a header with no tach, or a fan
    whose RPM never moved past the noise floor, yields no first-change time at
    all. Rendering that as "0.0 s" would report an instant response where in
    fact there was no response to time.
    """
    if ms is None:
        return UNKNOWN_TEXT
    return f"{ms / 1000:.{_SECONDS_DECIMALS}f} s"


def _typical_seconds(values: object) -> str:
    """The median of the measured durations, as "~N.N s", or "" if none were.

    Median rather than mean: a single startup outlier — exactly what a pump does
    on its first point — would drag a mean far from what every other point
    showed. Empty string, not an em dash, because the caller shows a whole
    summary LINE only when there is something to say (§9: "use measured values
    only").
    """
    measured = sorted(v for v in values if v is not None)
    if not measured:
        return ""
    mid = len(measured) // 2
    median = measured[mid] if len(measured) % 2 else (measured[mid - 1] + measured[mid]) / 2
    return f"~{median / 1000:.{_SECONDS_DECIMALS}f} s"


def _fmt_rpm(value: int | None) -> str:
    return "—" if value is None else f"{value}"


def _humanise_token(token: str) -> str:
    """Render a token this build does not recognise, rather than dropping it."""
    return token.replace("_", " ").strip().capitalize() or "Unknown"


#: What to tell the user for each `restore_outcome`, keyed by the daemon's token.
#: The advice differs per reason, which is the whole point of the token: under a
#: thermal force "re-activate your profile" is the one thing the user must not
#: do, and that is exactly what the single "restore failed" note used to say
#: (`AUD2-c`).
_RESTORE_NOTE = {
    "write_failed": (
        "Restoring the original speed failed, so the header is still at the "
        "last tested duty. Re-activate your profile to take control back."
    ),
    "skipped_thermal_force": (
        "Thermal safety is forcing fan output, so the original speed was not "
        "restored — the header is being held above the tested duty on purpose. "
        "It is released automatically once temperatures fall."
    ),
    "skipped_shutting_down": (
        "The daemon was shutting down, so the original speed was not restored. "
        "The header is handed back to the motherboard as part of shutdown."
    ),
    "no_original_duty": (
        "This header's speed could not be read before the sweep, so there was "
        "nothing to restore it to and it is still at the last tested duty. "
        "Re-activate your profile to take control back."
    ),
}

#: Said when the daemon reports the header was left moved but names a reason this
#: build does not know — 273-i: render the unrecognised token, never drop it.
_RESTORE_NOTE_FALLBACK = (
    "The original speed was not restored ({reason}), so the header is still at "
    "the last tested duty."
)


#: Tokens that mean the header IS back where the sweep found it. Everything else
#: — including one this build has never seen — means it is not.
_RESTORE_OK = frozenset({"", "pending", "restored"})


def restore_note(run: CharacterizationRun) -> str:
    """What to say about the pre-sweep duty, or ``""`` when it was put back.

    Derived from BOTH fields rather than from the boolean alone. The daemon
    computes one from the other so they cannot disagree — but taking a remote
    field's word for a truthfulness decision is exactly what `AUD2-c` was, and a
    version-skewed or partial response that named a skip while saying
    ``restore_failed: false`` would fall silent in precisely the old way. Same
    reconstruct-don't-trust discipline as DEC-312's pump predicate.
    """
    if not run.restore_failed and run.restore_outcome in _RESTORE_OK:
        return ""
    known = _RESTORE_NOTE.get(run.restore_outcome)
    if known:
        return known
    if not run.restore_outcome:
        # Pre-2.30.0: `restore_failed: true` meant the restore write failed, and
        # nothing else could set it.
        return _RESTORE_NOTE["write_failed"]
    return _RESTORE_NOTE_FALLBACK.format(reason=_humanise_token(run.restore_outcome))


@dataclass(frozen=True)
class CharRow:
    """One table row: the duty asked for, what came back, and a verdict."""

    pwm: str
    readback: str
    rpm: str
    result: str
    state: str
    # ── Timing analysis (AIO-MB Phase 6 §9) ──────────────────────────────────
    # Both figures have been on the wire and parsed since Phase 3 and were
    # rendered nowhere. `first_change_ms` is how long the fan took to react at
    # all; `settle_ms` is how long the daemon held the point. Absent means the
    # daemon could not measure it — commonly no tach, or an RPM that never moved
    # — and renders as an em dash, never as 0.
    response: str = UNKNOWN_TEXT
    settling: str = UNKNOWN_TEXT


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
    # ── Timing summary (AIO-MB Phase 6 §9) ───────────────────────────────────
    # "~0.4 s" / "~1.9 s", or "" when nothing was measurable. Deliberately
    # one-decimal and prefixed "~": the daemon sub-samples RPM at 500 ms, so
    # any further precision would be invented. §9: "avoid over-precision if
    # sampling resolution does not justify it".
    response_latency: str = ""
    settling_time: str = ""


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
        response=_fmt_seconds(point.first_change_ms),
        settling=_fmt_seconds(point.settle_ms),
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

    note = restore_note(run)
    if note:
        notes.append(note)

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
        response_latency=_typical_seconds(p.first_change_ms for p in run.points),
        settling_time=_typical_seconds(p.settle_ms for p in run.points),
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
