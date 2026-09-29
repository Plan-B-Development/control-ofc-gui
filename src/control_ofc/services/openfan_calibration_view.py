"""View-model for the OpenFan calibration dialog (DEC-452 / DEC-453).

Qt-free, like its siblings: every decision about what the dialog *says* is made
here and unit-tested headlessly, and the widget is a thin renderer over
:class:`CalibrationView`.

The run itself is the daemon's (``POST /fans/openfan/{ch}/calibration`` →
``202``, then ``GET /diagnostics/openfan-calibration``). It walks one channel
down from 100 % until the fan is confirmed stopped (the **stall duty**), then
back up until it is confirmed spinning (the **restart duty**). Two rules in here
are correctness requirements rather than presentation taste:

1. **The advice names the restart duty, never the stall duty (DEC-453).** A
   minimum just above the stall duty keeps a *spinning* fan spinning, but a fan
   that is already stopped — at boot, or after a 0 % curve point — starts again
   only at the restart duty. Only that figure makes "keeps this fan running"
   true in both cases.
2. **Every token is opaque (273-i).** A newer daemon may add an outcome, abort
   reason, phase or restore outcome this build has never seen; each is rendered
   humanised, never dropped.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..api.models import CalPoint, OpenFanCalibrationRun
from .characterization_view import ResponseCurve, SeriesPoint, SummaryRow

#: Shown wherever a measurement is genuinely absent. Never substitute 0.
UNKNOWN_TEXT = "—"

_CHANNEL_RE = re.compile(r"^openfan:ch(\d+)$")

#: Shown before a run. The pause is the same one every hardware diagnostic
#: claims; the 0 % is what makes this one different from every other test on
#: the Hardware page.
PRE_RUN_WARNINGS = (
    "Walks the channel you pick down from 100 % until the fan stops, then back "
    "up until it starts again, and reports both duties. It takes about 1.5 to 2 "
    "minutes, up to about 3.",
    "The fan on this channel will be stopped during the test. The daemon cannot "
    "tell whether an OpenFan channel powers a pump, so it asks you to confirm "
    "that it does not.",
    "Curve control for every fan is paused while this runs, and each fan holds "
    "its last duty. Thermal safety is unaffected and still overrides everything; "
    "the test stops itself if the CPU warms noticeably.",
)

DEMO_REFUSAL = (
    "Demo mode has no OpenFan controller to calibrate. Connect to the daemon to run this test."
)

NO_CHANNELS_TEXT = "No OpenFan channel is reporting. Connect an OpenFan controller first."


def pump_confirmation_text(channel_label: str) -> str:
    """The consent line for one channel — unticked again on every channel change."""
    return (
        f"{channel_label} does not power a pump. I understand its fan will be "
        "stopped during this test."
    )


def parse_channel(fan_id: str) -> int | None:
    """``openfan:ch07`` → ``7``; anything else → ``None``."""
    match = _CHANNEL_RE.match(fan_id or "")
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class ChannelOption:
    """One entry of the channel picker."""

    fan_id: str
    channel: int
    #: The channel's name alone — what every other line of the dialog calls it.
    label: str
    #: The picker text: the name plus the live RPM.
    text: str


def _channel_label(channel: int, display_name: str) -> str:
    """The name, and the channel number where the name does not already say it."""
    fallback = f"OpenFan CH{channel}"
    if not display_name or display_name == fallback:
        return fallback
    return f"{display_name} (CH{channel})"


def build_channel_options(
    fans: Iterable[object], display_name: Callable[[str], str]
) -> list[ChannelOption]:
    """Every OpenFan channel ``/fans`` reports, with its current RPM (DEC-453).

    A channel reading 0 rpm is offered too: a curve may be holding its fan at
    0 %, and an empty channel simply ends as "no fan detected". Hiding it would
    hide exactly the fan someone parked and now wants to measure.
    """
    options: list[ChannelOption] = []
    for fan in fans:
        if getattr(fan, "source", "") != "openfan":
            continue
        fan_id = getattr(fan, "id", "")
        channel = parse_channel(fan_id)
        if channel is None:
            continue
        label = _channel_label(channel, display_name(fan_id))
        rpm = getattr(fan, "rpm", None)
        rpm_text = "no RPM reading" if rpm is None else f"{rpm} rpm now"
        options.append(ChannelOption(fan_id, channel, label, f"{label} — {rpm_text}"))
    options.sort(key=lambda o: o.channel)
    return options


def _humanise_token(token: str) -> str:
    """Render a token this build does not recognise, rather than dropping it."""
    return token.replace("_", " ").strip().capitalize() or "Unknown"


def _pct(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value}%"


_PHASE_ROW = {"descent": "Down", "ascent": "Up", "kick": "Kick"}

_OBSERVATION = {
    "spinning": "Spinning",
    "stopped": "Stopped",
    "unconfirmed": "Unconfirmed",
    "interrupted": "Interrupted",
}

#: What the run is doing now. ``{pct}`` is the duty it last commanded.
_PHASE_STATUS = {
    "descent": "Walking down — holding {pct} until the fan's reading settles",
    "ascent": "Walking back up — holding {pct} until the fan starts again",
    "kick": "Kicking the fan at 100 % so it is not left stopped",
    "restore": "Restoring the channel's original speed",
}

#: Phases a DELETE no longer stops.
_UNCANCELLABLE = frozenset({"kick", "restore"})

_OUTCOME_STATUS = {
    "stall_and_restart_found": "Finished — found where this fan stops and starts.",
    "no_stall_down_to_0": "Finished — the fan kept spinning even at 0 %.",
    "did_not_restart": "Finished — the fan did not start again on its own.",
    "no_fan_detected": "Finished — no fan was detected on this channel.",
    "cancelled": "Cancelled.",
}

#: Why a run stopped early. ``{rise}`` is the daemon's own reported limit —
#: interpolated, never restated as a literal (a threshold spelled into prose
#: drifts the moment the constant moves).
_ABORT_WORDS = {
    "thermal_limit": "a temperature reached the diagnostics limit",
    "thermal_force": "thermal safety took over the fans",
    "stale_temperature": "the temperature readings went stale, so the test could not tell "
    "how hot the machine was",
    "thermal_rise": "the CPU warmed by more than {rise} during the test",
    "no_cpu_temperature": "there was no fresh CPU temperature reading",
    "write_failed": "a write to the controller failed",
    "rpm_unreadable": "no RPM reading arrived after a write",
    "shutting_down": "the daemon was shutting down",
    "superseded": "the daemon's diagnostic pause was lost to another test",
    "task_failed": "the daemon's calibration task ended unexpectedly — a daemon defect worth "
    "reporting",
}

#: Tokens that mean the channel is back where the run found it.
_RESTORE_OK = frozenset({"restored", "not_needed"})


def _fmt_temp_delta(value: float) -> str:
    return f"{value:g} °C"


def _abort_text(run: OpenFanCalibrationRun) -> str:
    token = run.abort_reason or ""
    words = _ABORT_WORDS.get(token)
    if words is None:
        return _humanise_token(token) if token else "no reason was given"
    return words.format(rise=_fmt_temp_delta(run.rise_limit_c))


def restore_note(run: OpenFanCalibrationRun) -> str:
    """What to say about the channel's speed after the run, or ``""`` when it is back.

    Derived from BOTH fields, as characterisation's is: a version-skewed
    response naming a skip while saying ``restore_failed: false`` must not fall
    silent.
    """
    token = run.restore_outcome
    if token in _RESTORE_OK and not run.restore_failed:
        return ""
    original = (
        "" if run.original_pct is None else f" It was at {run.original_pct}% before the test."
    )
    if token == "restored_full_speed":
        if not run.restore_failed:
            return ""  # its original duty was 100 % itself
        return (
            "The channel was left at 100 %, not its original speed: the daemon was "
            "shutting down before it could confirm the fan had started again, and a "
            "stopped fan is never left stopped." + original
        )
    if token == "skipped_thermal_force":
        return (
            "Thermal safety is forcing fan output, so the original speed was not "
            "restored — the channel is held at the forced duty on purpose and is "
            "released once temperatures fall."
        )
    if token == "write_failed":
        return (
            "Restoring the original speed failed, so the channel may still be at the "
            "last tested duty. Re-activate your profile to take control back." + original
        )
    if token in ("pending", ""):
        # Only reachable on a terminal run: `task_failed` leaves it pending.
        return (
            "The daemon tried to restore the channel's speed but did not record the "
            "result. Re-activate your profile to be sure it is back under control." + original
        )
    return (
        f"The original speed was not restored ({_humanise_token(token)}). "
        "Re-activate your profile to take control back." + original
    )


def advice_text(run: OpenFanCalibrationRun) -> tuple[str, str]:
    """The one-line recommendation and its tone, or ``("", "neutral")``.

    Only a measured restart duty earns a figure (DEC-453, rule 1 above).
    """
    outcome = run.outcome or ""
    if outcome == "stall_and_restart_found" and run.restart_duty_pct is not None:
        return (
            f"Keep this fan's minimum at {run.restart_duty_pct}% or more to keep it "
            "running: that is the lowest duty that started it again from a stop.",
            "ok",
        )
    if outcome == "no_stall_down_to_0":
        return ("This fan never stopped, so any minimum keeps it running.", "ok")
    if outcome == "did_not_restart":
        top = max((p.pwm_percent for p in run.points if p.phase == "ascent"), default=None)
        stall = _pct(run.stall_duty_pct)
        by = f" by {top}%" if top is not None else ""
        return (
            f"No minimum can be recommended from this run: the fan stopped at {stall} "
            f"and did not start again{by}.",
            "warn",
        )
    return ("", "neutral")


@dataclass(frozen=True)
class CalRow:
    duty: str
    phase: str
    rpm: str
    observation: str


@dataclass(frozen=True)
class CalibrationView:
    channel_label: str
    rows: list[CalRow] = field(default_factory=list)
    status_text: str = ""
    running: bool = False
    can_cancel: bool = False
    finished: bool = False
    summary_rows: list[SummaryRow] = field(default_factory=list)
    advice: str = ""
    advice_state: str = "neutral"
    notes: list[str] = field(default_factory=list)
    curve: ResponseCurve = field(default_factory=ResponseCurve)


def _row(point: CalPoint) -> CalRow:
    observation = _OBSERVATION.get(point.observation, _humanise_token(point.observation))
    return CalRow(
        duty=f"{point.pwm_percent}%",
        phase=_PHASE_ROW.get(point.phase, _humanise_token(point.phase)),
        rpm=str(point.rpm),
        observation=observation,
    )


def _curve(run: OpenFanCalibrationRun) -> ResponseCurve:
    """The descent as the falling series and the ascent as the rising one.

    The kick is left out: it is a recovery write at 100 %, not a point on
    either leg, and plotting it would draw a leg that never happened.
    """
    falling = sorted(
        (SeriesPoint(p.pwm_percent, p.rpm) for p in run.points if p.phase == "descent"),
        key=lambda s: s.duty_pct,
    )
    rising = sorted(
        (SeriesPoint(p.pwm_percent, p.rpm) for p in run.points if p.phase == "ascent"),
        key=lambda s: s.duty_pct,
    )
    return ResponseCurve(rising=rising, falling=falling, has_data=bool(rising or falling))


def _summary(run: OpenFanCalibrationRun) -> list[SummaryRow]:
    rows: list[SummaryRow] = []
    if run.stall_duty_pct is not None:
        rows.append(SummaryRow("Stops at", f"{run.stall_duty_pct}% (on the way down)"))
    if run.restart_duty_pct is not None:
        rows.append(SummaryRow("Starts again at", f"{run.restart_duty_pct}% (on the way up)"))
    if run.hysteresis_pct is not None:
        rows.append(SummaryRow("Gap between the two", f"{run.hysteresis_pct} points of duty"))
    if run.min_rpm is not None and run.max_rpm is not None:
        rows.append(SummaryRow("Speed range seen", f"{run.min_rpm}-{run.max_rpm} rpm"))
    if run.start_cpu_temp_c is not None:
        peak = f", peak {run.max_cpu_temp_c:.1f} °C" if run.max_cpu_temp_c is not None else ""
        rows.append(
            SummaryRow(
                "CPU temperature",
                f"{run.start_cpu_temp_c:.1f} °C at start{peak} "
                f"(the test stops above +{_fmt_temp_delta(run.rise_limit_c)})",
            )
        )
    if run.hold_ms:
        rows.append(SummaryRow("Hold per step", f"{run.hold_ms / 1000:g} s"))
    return rows


def run_has_ended(run: OpenFanCalibrationRun) -> bool:
    """Has the daemon published this run's end?

    **Keyed on ``completed_unix_ms``, docs/08's rule for this route.** Only the
    terminal publish sets it — the same publish that makes ``state`` terminal
    and fills ``outcome`` and ``restore_outcome``; while the kick and the
    restore run, the wire still reads ``running``. The stamp is the field the
    contract names as the end, so a daemon that ever published a terminal
    ``state`` earlier could not end polling mid-restore and report a restore
    that was merely unfinished as one it failed to record.
    """
    return run.completed_unix_ms is not None


def _status(run: OpenFanCalibrationRun) -> str:
    if not run_has_ended(run):
        if not run.phase:
            return "Starting…" if run.is_running else "Finishing…"
        template = _PHASE_STATUS.get(run.phase)
        pct = _pct(run.current_pct)
        if template is None:
            text = f"{_humanise_token(run.phase)} at {pct}"
        else:
            text = template.format(pct=pct)
        return f"{text}…"
    outcome = run.outcome or ""
    if outcome == "aborted" or (not outcome and run.state == "aborted"):
        text = f"Stopped early: {_abort_text(run)}."
    elif outcome in _OUTCOME_STATUS:
        text = _OUTCOME_STATUS[outcome]
    elif outcome:
        text = f"Finished — {_humanise_token(outcome).lower()}."
    else:
        text = f"{_humanise_token(run.state)}."
    if run.detail:
        text = f"{text} {run.detail}"
    return text


def build_calibration_view(
    run: OpenFanCalibrationRun | None, *, channel_label: str
) -> CalibrationView:
    """Render-ready state for the dialog. ``None`` means nothing has started."""
    if run is None:
        return CalibrationView(channel_label=channel_label, status_text="Ready to start.")

    status_text = _status(run)
    ended = run_has_ended(run)
    notes: list[str] = []
    advice, advice_state = ("", "neutral")
    if ended:
        advice, advice_state = advice_text(run)
        if run.restart_failed_at_full:
            notes.append(
                "A 100 % kick did not get the fan spinning within its window. It may "
                "be stuck or disconnected — check it before relying on it."
            )
        note = restore_note(run)
        if note:
            notes.append(note)
    return CalibrationView(
        channel_label=channel_label,
        rows=[_row(p) for p in run.points],
        status_text=status_text,
        running=not ended,
        # A DELETE acts only during the walk itself. The kick and the restore
        # run to their end — the daemon still answers 202, because the run
        # still reads `running`, but nothing stops (docs/08).
        can_cancel=not ended and run.is_running and run.phase not in _UNCANCELLABLE,
        finished=ended,
        summary_rows=_summary(run),
        advice=advice,
        advice_state=advice_state,
        notes=notes,
        curve=_curve(run),
    )
