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


#: `pwmN_enable` values every hwmon driver agrees on. Anything above 1 is
#: driver-specific automatic control, which is what makes it evidence of
#: interference — the daemon writes 1 when it takes a header over, so a point
#: reporting 2 during a sweep means something else wrote it back.
_PWM_ENABLE_LABELS = {0: "no control", 1: "manual", 2: "automatic"}


def _fmt_pwm_enable(mode: int | None) -> str:
    """Render a point's `pwmN_enable` (`WIRE-u`).

    ``None`` means the daemon did not report it — never "no control", which is
    the *value 0* and a materially different statement. An unrecognised value is
    rendered as itself rather than dropped: modes above 2 are driver-specific
    and a newer driver may use one (the 273-i rule).
    """
    if mode is None:
        return UNKNOWN_TEXT
    known = _PWM_ENABLE_LABELS.get(mode)
    return known if known is not None else f"mode {mode}"


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
        return _with_original_duty(known, run.original_pct)
    if not run.restore_outcome:
        # Pre-2.30.0: `restore_failed: true` meant the restore write failed, and
        # nothing else could set it.
        return _with_original_duty(_RESTORE_NOTE["write_failed"], run.original_pct)
    return _with_original_duty(
        _RESTORE_NOTE_FALLBACK.format(reason=_humanise_token(run.restore_outcome)),
        run.original_pct,
    )


def _with_original_duty(note: str, original_pct: int | None) -> str:
    """Name the duty the header should be back at (`WIRE-u`).

    `CharacterizationRun.original_pct` was parsed and never read, so every
    restore-failure note told the user their fan had been left somewhere without
    being able to say where it *should* be — which is the one fact needed to put
    it back by hand. Appended rather than woven into each note so the per-reason
    advice, including the thermal-force case where "re-activate your profile" is
    the wrong instruction (`AUD2-c`), is unchanged.

    Omitted when the daemon did not report one — which is exactly the
    `no_original_duty` case, where claiming a figure would contradict the note
    it is appended to.
    """
    if original_pct is None:
        return note
    return f"{note} It was at {original_pct}% before the sweep."


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
    # WIRE-u: the per-point `pwmN_enable` mode — the direct evidence behind the
    # sweep-level `interference_detected` verdict the dialog already shows. A
    # verdict without the observation that produced it is something the user has
    # to take on trust, and this is the row where it becomes checkable.
    control_mode: str = UNKNOWN_TEXT
    response: str = UNKNOWN_TEXT
    settling: str = UNKNOWN_TEXT
    # ── AIO Phase 8 Batch 2 (DEC-334) ────────────────────────────────────────
    # Which leg of the walk produced this reading. A bidirectional sweep visits
    # most duties twice, and a table that did not say which was which would show
    # two contradictory rows for the same duty with no way to tell them apart.
    direction: str = UNKNOWN_TEXT
    # §4's per-point classification, worded. Never "fault" — §4 forbids
    # inferring cavitation or an electrical failure from tach variability.
    stability: str = UNKNOWN_TEXT


@dataclass(frozen=True)
class SeriesPoint:
    """One plotted (duty, RPM) reading."""

    duty_pct: int
    rpm: int


@dataclass(frozen=True)
class ResponseCurve:
    """§8.3's plot data, already separated by leg.

    Kept Qt-free so the chart is a thin renderer over a testable view model —
    the project's view-model + renderer standard. ``has_data`` exists so the
    dialog can hide the chart rather than draw empty axes, which would imply a
    measurement that was never taken.
    """

    rising: list[SeriesPoint] = field(default_factory=list)
    falling: list[SeriesPoint] = field(default_factory=list)
    plateaus: list[tuple[int, int]] = field(default_factory=list)
    low_plateau_to_pct: int | None = None
    saturation_from_pct: int | None = None
    #: §8.3's "optional expected/learned band" is not plotted: the daemon
    #: publishes whether a reading fell outside the learned range, not the band
    #: itself, and drawing a band from this run's own points would be a
    #: fabricated reference. The verdict is shown as text instead.
    has_data: bool = False


@dataclass(frozen=True)
class SummaryRow:
    """One label/value line in the §8.2 compact block or §8.4 detail block."""

    label: str
    value: str
    state: str = "neutral"


@dataclass(frozen=True)
class ProvenanceRow:
    """§8.6. A value with the classification it was published under."""

    label: str
    value: str
    provenance: str


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
    # ── AIO Phase 8 Batch 2 (DEC-334) ────────────────────────────────────────
    #: Derived from the run, not from what was requested: a sweep that aborted
    #: before its second leg is honestly unidirectional (DEC-325).
    bidirectional: bool = False
    #: §8.2's compact block, in the spec's order.
    summary_rows: list[SummaryRow] = field(default_factory=list)
    #: §8.4's expandable engineering detail.
    detail_rows: list[SummaryRow] = field(default_factory=list)
    curve: ResponseCurve = field(default_factory=ResponseCurve)
    #: §8.5's cautious wording, or "" when the condition does not hold. Shown as
    #: a caution, **never** as a generic red "hardware failed".
    override_warning: str = ""
    #: §8.6.
    provenance_rows: list[ProvenanceRow] = field(default_factory=list)


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


# §8.5, verbatim. The spec dictates cautious wording here, and lists every
# benign explanation, because the alternative — a red "hardware failed" — is the
# conclusion this whole diagnostic exists to avoid jumping to.
OUTSIDE_LEARNED_RANGE_WARNING = (
    "PWM command and motherboard readback are valid, but reported RPM is "
    "outside the characterised response range. The connected device may be "
    "applying internal control, a clamp, startup behaviour, thermal protection, "
    "or may use different tach mapping/scaling."
)

_DIRECTION_LABELS = {
    "ramp": "Start",
    "falling": "Falling",
    "rising": "Rising",
}

# §4's four states, worded. `variable` and `unstable` are OBSERVATIONS: §4 is
# explicit that tach variability alone does not evidence cavitation, an
# electrical fault or bubbles, so none of these is a failure word.
_STABILITY_LABELS = {
    "stable": ("Stable", "ok"),
    "variable": ("Variable", "warn"),
    "unstable": ("Unstable", "warn"),
    "insufficient_data": ("Too few samples", "neutral"),
    "unavailable": ("No tach", "neutral"),
}

_HYSTERESIS_LABELS = {
    "none": ("Not observed", "ok"),
    "present": ("Observed", "warn"),
    "insufficient_data": ("Too little data", "neutral"),
    "not_tested": ("Not tested", "neutral"),
}


def _fmt_float(value: float | None, suffix: str = "") -> str:
    return UNKNOWN_TEXT if value is None else f"{value:.1f}{suffix}"


def _settling_ms(point) -> int | None:
    """§5's settling time for one point, or ``None`` when it was not measured.

    ``settled_ms`` is when reported RPM entered its band. ``settle_ms`` is merely
    how long the daemon *held* the point — and since DEC-334 a stability dwell
    inflates it, so a step with a 20 s dwell reports 26 000 ms for a fan that
    settled in 3 s.

    So ``settle_ms`` is used only as the **pre-DEC-334 fallback**, detected by the
    absence of the per-point stability block: an older daemon sends neither
    field, and there ``settle_ms`` is the only figure there has ever been. On a
    current daemon that measured no settling, the answer is ``None`` — "we did
    not observe it settle" — never the length of the wait, which would report a
    placeholder as a measurement and is precisely what §5 forbids.
    """
    settled = getattr(point, "settled_ms", None)
    if settled is not None:
        return settled
    if getattr(point, "stability", None) is None:
        return point.settle_ms
    return None


def _stability_text(point) -> str:
    stab = getattr(point, "stability", None)
    if stab is None or not stab.verdict:
        return UNKNOWN_TEXT
    label, _tone = _STABILITY_LABELS.get(stab.verdict, (_humanise_token(stab.verdict), "neutral"))
    if stab.cv_pct is not None:
        return f"{label} ({stab.cv_pct:.1f}%)"
    return label


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
        control_mode=_fmt_pwm_enable(point.pwm_enable),
        response=_fmt_seconds(point.first_change_ms),
        settling=_fmt_seconds(_settling_ms(point)),
        direction=_DIRECTION_LABELS.get(
            getattr(point, "direction", "") or "",
            _humanise_token(getattr(point, "direction", "")) or UNKNOWN_TEXT,
        ),
        stability=_stability_text(point),
    )


def _build_curve(run: CharacterizationRun) -> ResponseCurve:
    """§8.3's plot data, split by the leg the daemon says produced it.

    The split reads the point's own ``direction`` rather than inferring one from
    the order the points arrived in: a run that aborted mid-walk, or one from an
    older daemon that sends no direction at all, would otherwise be sliced into
    two series that never existed.
    """
    rising: list[SeriesPoint] = []
    falling: list[SeriesPoint] = []
    for pt in run.points:
        if pt.rpm_after is None:
            continue
        sample = SeriesPoint(duty_pct=pt.requested_pct, rpm=pt.rpm_after)
        if pt.direction == "rising":
            rising.append(sample)
        elif pt.direction == "falling":
            falling.append(sample)
        elif not pt.direction:
            # Pre-DEC-334 daemon: one ascending series, and calling it "rising"
            # is what it actually was.
            rising.append(sample)
    rising.sort(key=lambda s: s.duty_pct)
    falling.sort(key=lambda s: s.duty_pct)
    summary = run.summary
    return ResponseCurve(
        rising=rising,
        falling=falling,
        plateaus=[(pl.from_pct, pl.to_pct) for pl in (summary.plateaus if summary else [])],
        low_plateau_to_pct=summary.low_plateau_to_pct if summary else None,
        saturation_from_pct=summary.saturation_from_pct if summary else None,
        has_data=bool(rising or falling),
    )


def _build_summary_rows(run: CharacterizationRun) -> list[SummaryRow]:
    """§8.2's compact block, in the spec's order."""
    summary = run.summary
    if summary is None:
        return []
    rows: list[SummaryRow] = []
    if summary.min_tested_pct is not None and summary.max_tested_pct is not None:
        rows.append(
            SummaryRow("Safe tested range", f"{summary.min_tested_pct}-{summary.max_tested_pct}%")
        )
    if summary.min_responsive_pct is not None and summary.max_responsive_pct is not None:
        rows.append(
            SummaryRow(
                "Effective range",
                f"{summary.min_responsive_pct}-{summary.max_responsive_pct}%",
            )
        )
    elif summary.plateaus:
        # §3: a sweep that plateaued end to end has no responsive band. Saying so
        # is an observation about the device, not a fault.
        rows.append(SummaryRow("Effective range", "no responsive band", "warn"))
    if summary.min_rpm is not None and summary.max_rpm is not None:
        rows.append(SummaryRow("Reported RPM range", f"{summary.min_rpm}-{summary.max_rpm}"))
    if summary.hysteresis_verdict:
        label, tone = _HYSTERESIS_LABELS.get(
            summary.hysteresis_verdict,
            (_humanise_token(summary.hysteresis_verdict), "neutral"),
        )
        if summary.hysteresis_pct is not None and summary.hysteresis_verdict == "present":
            label = f"{label} ({summary.hysteresis_pct:.1f}% of span)"
        rows.append(SummaryRow("Hysteresis", label, tone))
    if summary.stability_verdict:
        label, tone = _STABILITY_LABELS.get(
            summary.stability_verdict,
            (_humanise_token(summary.stability_verdict), "neutral"),
        )
        rows.append(SummaryRow("RPM stability", label, tone))
    # `P8-x`: the DAEMON owns this derivation (`docs/08`), and publishes it in
    # `_build_detail_rows` as "Response latency (median)". Rendering both put one
    # measurement on screen twice in two units, in a single dialog — and they
    # disagree on any even sample count, because the daemon takes the upper
    # median while `_typical_seconds` averages the middle pair. The client
    # computation survives only as the pre-2.40.0 fallback, for a daemon that
    # publishes neither field.
    if summary.typical_response_ms is None:
        response = _typical_seconds(p.first_change_ms for p in run.points)
        if response:
            rows.append(SummaryRow("Response time", response))
    if summary.typical_settling_ms is None:
        settling = _typical_seconds(_settling_ms(p) for p in run.points)
        if settling:
            rows.append(SummaryRow("Settling time", settling))
    # §6, three states. "No model yet" must not read as agreement — the
    # Overview: "Do not turn lack of evidence into PASS."
    if summary.outside_learned_range is None:
        rows.append(SummaryRow("Learned range", "not established yet", "neutral"))
    elif summary.outside_learned_range:
        rows.append(SummaryRow("Learned range", "reading outside it", "warn"))
    else:
        rows.append(SummaryRow("Learned range", "reading inside it", "ok"))
    rows.append(
        SummaryRow(
            "Device override",
            "Possible" if summary.possible_device_override else "Not observed",
            "warn" if summary.possible_device_override else "ok",
        )
    )
    return rows


def _build_detail_rows(run: CharacterizationRun) -> list[SummaryRow]:
    """§8.4's engineering detail, aggregated across the sweep."""
    summary = run.summary
    stats = [p.stability for p in run.points if p.stability is not None]
    rows: list[SummaryRow] = []
    intervals = {st.sample_interval_ms for st in stats if st.sample_interval_ms}
    if intervals:
        rows.append(SummaryRow("Sample interval", f"{max(intervals)} ms"))
    if summary is not None and summary.measurement_resolution_ms is not None:
        # §5: publish the resolution the timings were measured at, so nothing
        # above implies precision the tach cannot support.
        rows.append(SummaryRow("Measurement resolution", f"{summary.measurement_resolution_ms} ms"))
    if stats:
        rows.append(SummaryRow("Samples", str(sum(st.samples for st in stats))))
        means = [st.mean_rpm for st in stats if st.mean_rpm is not None]
        if means:
            # Both ends, stated plainly. It was "Mean RPM (worst step)" showing
            # `min(means)` — but a low mean at a low duty is the expected
            # reading, not the worst one, so the label misdescribed the number.
            rows.append(
                SummaryRow(
                    "Mean RPM across steps",
                    f"{_fmt_float(min(means))} - {_fmt_float(max(means))}",
                )
            )
        devs = [st.stddev_rpm for st in stats if st.stddev_rpm is not None]
        if devs:
            rows.append(SummaryRow("Std deviation (worst step)", _fmt_float(max(devs))))
    if summary is not None:
        if summary.worst_cv_pct is not None:
            rows.append(
                SummaryRow("Coefficient of variation", _fmt_float(summary.worst_cv_pct, "%"))
            )
        # Absent is not zero: a daemon that measured nothing must not report "0
        # dropouts", which reads as a clean tach.
        if stats:
            rows.append(SummaryRow("Tach dropouts", str(summary.total_dropouts)))
            rows.append(SummaryRow("Outliers", str(summary.total_outliers)))
        if summary.typical_response_ms is not None:
            rows.append(
                SummaryRow("Response latency (median)", f"{summary.typical_response_ms} ms")
            )
        if summary.typical_settling_ms is not None:
            rows.append(SummaryRow("Settling time (median)", f"{summary.typical_settling_ms} ms"))
        if summary.hysteresis_compared_points:
            rows.append(
                SummaryRow("Hysteresis comparisons", f"{summary.hysteresis_compared_points} duties")
            )
        for span in summary.plateaus:
            rows.append(
                SummaryRow(
                    "Plateau",
                    f"{span.from_pct}-{span.to_pct}% at {span.rpm_min}-{span.rpm_max} RPM",
                )
            )
    rows.append(
        SummaryRow(
            "Settling criterion",
            "within a fixed band of the rolling median for several consecutive samples",
        )
    )
    return rows


def _build_provenance_rows(run: CharacterizationRun) -> list[ProvenanceRow]:
    """§8.6. Reported and estimated RPM, each labelled with how it was obtained.

    The estimate is shown **only when the daemon sent one**. There is no shipped
    device policy with a correction factor, so on every machine today this is one
    row saying the reported figure is OBSERVED — which is the honest answer, and
    the reason §7's ``physical_rpm`` stays on the UNVERIFIABLE list.
    """
    from control_ofc.services import provenance as prov

    last = next((p for p in reversed(run.points) if p.rpm_after is not None), None)
    if last is None:
        return []
    rows = [
        ProvenanceRow(
            label="Reported RPM",
            value=str(last.rpm_after),
            provenance=run.provenance.get("rpm_after") or prov.classify("rpm_after"),
        )
    ]
    est = last.estimated_physical_rpm
    if est is not None:
        envelope = prov.from_envelope({"value": est.value, "provenance": est.provenance})
        rows.append(
            ProvenanceRow(
                label="Estimated physical RPM",
                value=str(est.value),
                provenance=envelope.provenance if envelope else est.provenance,
            )
        )
        if est.correction_source:
            rows.append(
                ProvenanceRow(
                    label="Correction",
                    value=f"x{est.correction_factor:.3f} ({est.correction_source})",
                    provenance=run.provenance.get("correction_source")
                    or prov.classify("correction_source"),
                )
            )
    return rows


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
        # `P8-bg`: a point is appended only once its hold COMPLETES
        # (`hwmon_ctl.rs:968-974`), so the worst-case interval below is exactly
        # how long this dialog can legitimately show nothing new. A behaviour run
        # adds a stability dwell on up to three steps of the final leg, taking the
        # real gap to settle + dwell — 26 s at the daemon's defaults. Reporting
        # only `settle_seconds` there understated the silence roughly fourfold,
        # which is what makes a healthy run indistinguishable from a wedged one.
        dwell = run.stability_seconds if run.bidirectional else 0
        status_text = f"Measuring… holding {run.settle_seconds}s per step."
        if dwell:
            status_text += (
                f" Some steps are held a further {dwell}s to measure stability, "
                f"so a reading can be up to {run.settle_seconds + dwell}s apart — "
                "a pause that long is normal, not a stall."
            )
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

    # §8.5. Command AND readback valid, but RPM outside the learned range —
    # exactly the three conditions the spec names, and no more. Checking only
    # `outside_learned_range` would fire on a run whose write never landed, where
    # the honest finding is the failed write, not the device's behaviour.
    override_warning = ""
    if (
        summary is not None
        and summary.outside_learned_range
        and summary.command_acceptance == "pass"
        and summary.pwm_readback == "pass"
    ):
        override_warning = OUTSIDE_LEARNED_RANGE_WARNING
        if summary.interpretation_states:
            override_warning += " Possible explanations: " + ", ".join(
                _humanise_token(t) for t in summary.interpretation_states
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
        settling_time=_typical_seconds(_settling_ms(p) for p in run.points),
        bidirectional=any(p.direction == "falling" for p in run.points),
        summary_rows=_build_summary_rows(run),
        detail_rows=_build_detail_rows(run),
        curve=_build_curve(run),
        override_warning=override_warning,
        provenance_rows=_build_provenance_rows(run),
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
