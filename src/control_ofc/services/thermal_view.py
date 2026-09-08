"""Rendering for thermal observations, steady state and startup fingerprints.

AIO Phase 8 Batch 3a (DEC-335), §1 / §2 / §3 / §9.2. Qt-free and total: every
function here takes plain models and returns frozen dataclasses, so the wording
and tone decisions are testable headlessly and the widgets stay thin.

# Two rules this module exists to enforce

**Unknown is not zero.** A `None` power, temperature or RPM renders as
[`UNKNOWN_TEXT`], never as `0 W`. On the reference host `k10temp` publishes no
power attribute at all, so "not known" is the *common* case for package power,
and a dash that means "we cannot see this" is the honest answer where a `0 W`
would be a claim about an idle machine.

**A startup override is a device behaviour, not a fault.** §1 is explicit: do
not diagnose a temporary high RPM as failed PWM control while command and
readback remain valid. The daemon decides that and sends a token; this module
renders the token and the duty evidence beside it. It never re-derives a verdict
from the RPM, and nothing here can produce a failure tone from a fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from control_ofc.api.models import (
    STARTUP_COMMAND_MISMATCH,
    STARTUP_DEVICE_OVERRIDE,
    STARTUP_NONE_OBSERVED,
    STARTUP_UNRESOLVED,
    STEADY_STATE_DETECTED,
    STEADY_STATE_INSUFFICIENT,
    STEADY_STATE_NOT_ESTABLISHED,
    ValidationSample,
    ValidationSession,
    ValidationStartupFingerprint,
    ValidationSteadyState,
)

#: What every unreadable value renders as. One constant so a `None` cannot
#: appear as a dash in one table and "n/a" in another.
UNKNOWN_TEXT = "—"

#: Tone tokens, matching the ones the rest of the GUI's view-models emit.
TONE_NEUTRAL = "neutral"
TONE_GOOD = "good"
TONE_INFO = "info"
TONE_WARN = "warn"

STEADY_STATE_LABELS = {
    STEADY_STATE_DETECTED: "Steady",
    STEADY_STATE_NOT_ESTABLISHED: "Not established",
    STEADY_STATE_INSUFFICIENT: "Insufficient data",
}

#: **No token maps to a failure tone, and that is deliberate.** §3 forbids
#: turning "did not settle" into a verdict about the cooler: a run stopped early
#: and a loop that genuinely cannot stabilise produce the same token, and the
#: data cannot tell them apart.
STEADY_STATE_TONES = {
    STEADY_STATE_DETECTED: TONE_GOOD,
    STEADY_STATE_NOT_ESTABLISHED: TONE_INFO,
    STEADY_STATE_INSUFFICIENT: TONE_NEUTRAL,
}

STARTUP_LABELS = {
    STARTUP_DEVICE_OVERRIDE: "Device startup override",
    STARTUP_UNRESOLVED: "Override did not resolve",
    STARTUP_NONE_OBSERVED: "No startup override",
    STARTUP_COMMAND_MISMATCH: "Command/readback mismatch",
}

#: Again, no failure tone. `STARTUP_COMMAND_MISMATCH` is the strongest reading
#: available and it is only `warn`: it says the benign explanation is
#: unsupported, which is a weaker claim than asserting a fault.
STARTUP_TONES = {
    STARTUP_DEVICE_OVERRIDE: TONE_INFO,
    STARTUP_UNRESOLVED: TONE_INFO,
    STARTUP_NONE_OBSERVED: TONE_NEUTRAL,
    STARTUP_COMMAND_MISMATCH: TONE_WARN,
}


def _titleise(token: str) -> str:
    """Fallback wording for a token this build does not know.

    273-i: an unrecognised token is **rendered**, never dropped. A newer daemon
    naming a state this GUI predates must still put something on screen.
    """
    return token.replace("_", " ").strip().capitalize() or UNKNOWN_TEXT


def steady_state_label(verdict: str) -> str:
    return STEADY_STATE_LABELS.get(verdict) or _titleise(verdict)


def steady_state_tone(verdict: str) -> str:
    return STEADY_STATE_TONES.get(verdict, TONE_NEUTRAL)


def startup_label(token: str) -> str:
    return STARTUP_LABELS.get(token) or _titleise(token)


def startup_tone(token: str) -> str:
    return STARTUP_TONES.get(token, TONE_NEUTRAL)


def watts_text(value: float | None) -> str:
    """Format a power reading. ``None`` is unknown, never ``0 W``."""
    return UNKNOWN_TEXT if value is None else f"{value:.0f} W"


def celsius_text(value: float | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value:.1f} °C"


def slope_text(value: float | None) -> str:
    """Signed, because the sign is the information — is it still climbing?"""
    return UNKNOWN_TEXT if value is None else f"{value:+.2f} °C/min"


def duration_text(ms: int | None) -> str:
    if ms is None:
        return UNKNOWN_TEXT
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


@dataclass(frozen=True)
class InfoRow:
    label: str
    value: str
    tone: str = TONE_NEUTRAL


@dataclass(frozen=True)
class SteadyStateView:
    verdict: str
    verdict_label: str
    tone: str
    rows: list[InfoRow] = field(default_factory=list)
    #: The daemon's own criterion string, rendered verbatim (§3 requires the
    #: rule be reported). Empty when the daemon sent none.
    criterion: str = ""


@dataclass(frozen=True)
class StartupFingerprintView:
    member_id: str
    role: str
    interpretation: str
    interpretation_label: str
    tone: str
    rows: list[InfoRow] = field(default_factory=list)


@dataclass(frozen=True)
class LiveSummaryView:
    """§9.2's live block: what the user watches while the workload runs."""

    elapsed_text: str
    temperature_text: str
    package_power_text: str
    gpu_power_text: str
    pump_text: str
    radiator_text: str
    slope_text: str
    steady_text: str
    #: Always present, and always the same sentence. §2 requires the UI make
    #: clear that Control-OFC is not launching the workload.
    workload_note: str = (
        "Control-OFC is not starting or controlling your workload — "
        "run it yourself, then watch the cooling response here."
    )


def build_steady_state_view(steady: ValidationSteadyState | None) -> SteadyStateView | None:
    """Render §3's result. ``None`` in, ``None`` out — no temperature recorded."""
    if steady is None:
        return None
    rows = [
        InfoRow("Mean temperature", celsius_text(steady.mean_c)),
        InfoRow("Peak temperature", celsius_text(steady.peak_c)),
        InfoRow("Temperature trend", slope_text(steady.slope_c_per_min)),
        InfoRow(
            "Variation",
            UNKNOWN_TEXT if steady.stddev_c is None else f"±{steady.stddev_c:.2f} °C",
        ),
        InfoRow("Warm-up", duration_text(steady.warmup_ms)),
        InfoRow("Confidence", steady.confidence.capitalize() or UNKNOWN_TEXT),
    ]
    return SteadyStateView(
        verdict=steady.verdict,
        verdict_label=steady_state_label(steady.verdict),
        tone=steady_state_tone(steady.verdict),
        rows=rows,
        criterion=steady.criterion or "",
    )


def build_startup_views(
    fingerprints: list[ValidationStartupFingerprint],
    *,
    display_name=None,
) -> list[StartupFingerprintView]:
    """Render §1's per-member fingerprints.

    ``display_name`` resolves a member id to the user's name for it, following
    the same injection the other view-models use rather than reaching for the
    alias store from a Qt-free module.
    """
    out: list[StartupFingerprintView] = []
    for f in fingerprints:
        name = display_name(f.member_id) if display_name else f.member_id
        rows = [
            InfoRow("Member", name or f.member_id or UNKNOWN_TEXT),
            InfoRow(
                "Peak RPM",
                UNKNOWN_TEXT if f.peak_rpm is None else f"{f.peak_rpm} RPM",
            ),
            InfoRow(
                "Settled RPM",
                UNKNOWN_TEXT if f.post_override_rpm is None else f"{f.post_override_rpm} RPM",
            ),
            InfoRow("Override duration", duration_text(f.override_duration_ms)),
            InfoRow("Transition", duration_text(f.transition_ms)),
            # The duty pair is what makes the benign reading legible. §1's whole
            # point is that these two agreeing is what rules out a control
            # fault, so they are shown together and never separately.
            InfoRow(
                "Duty during override",
                _duty_pair_text(f.requested_pct_during, f.readback_pct_during),
            ),
        ]
        out.append(
            StartupFingerprintView(
                member_id=f.member_id,
                role=f.role,
                interpretation=f.interpretation,
                interpretation_label=startup_label(f.interpretation),
                tone=startup_tone(f.interpretation),
                rows=rows,
            )
        )
    return out


def _duty_pair_text(requested: int | None, readback: int | None) -> str:
    if requested is None and readback is None:
        return UNKNOWN_TEXT
    req = UNKNOWN_TEXT if requested is None else f"{requested}%"
    rb = UNKNOWN_TEXT if readback is None else f"{readback}%"
    return f"commanded {req} · readback {rb}"


def _member_text(sample: ValidationSample | None, member_id: str | None) -> str:
    if sample is None or not member_id:
        return UNKNOWN_TEXT
    for m in sample.members:
        if m.member_id == member_id:
            duty = UNKNOWN_TEXT if m.requested_pct is None else f"{m.requested_pct}%"
            rpm = UNKNOWN_TEXT if m.rpm is None else f"{m.rpm} RPM"
            return f"{duty} · {rpm}"
    return UNKNOWN_TEXT


def build_live_summary(
    session: ValidationSession,
    steady: ValidationSteadyState | None = None,
) -> LiveSummaryView:
    """§9.2's live block, from the most recent sample.

    Reads the LAST sample rather than averaging: this is a live readout of what
    the machine is doing now, and a running mean would lag the workload the user
    just started and is watching for.
    """
    last = session.samples[-1] if session.samples else None
    meta = session.metadata
    return LiveSummaryView(
        elapsed_text=duration_text(last.elapsed_ms if last else 0),
        temperature_text=celsius_text(last.temperature_c if last else None),
        package_power_text=watts_text(last.package_power_w if last else None),
        gpu_power_text=watts_text(last.gpu_power_w if last else None),
        pump_text=_member_text(last, getattr(meta, "pump_member", None)),
        radiator_text=_radiator_text(last, meta),
        slope_text=slope_text(steady.slope_c_per_min if steady else None),
        steady_text=steady_state_label(steady.verdict) if steady else UNKNOWN_TEXT,
    )


#: §4/§9.3's component-isolation templates, as GUIDED stage lists.
#:
#: **These instruct; they never drive.** Q3-A: Control-OFC adds no new PWM write
#: path in this batch, so a stage tells the user which duty to set — through the
#: Controls page's existing, floor-clamped manual override — and the session
#: records what happens and drops a marker. There is no daemon-side stepping and
#: no fifth claimant on the verify slot.
#:
#: The supporting-device rule from the Overview is baked into the wording of each
#: stage: while one component is varied the other is held at a known safe level,
#: and never are two unknown cooling components varied at once.
ISOLATION_TEMPLATES: dict[str, tuple[str, tuple[str, ...]]] = {
    "pump_influence": (
        "Pump influence",
        (
            "Hold the radiator fans at a fixed, known-safe duty you choose.",
            "Set the pump to its minimum safe duty and let the temperature settle.",
            "Raise the pump to a mid duty and let the temperature settle.",
            "Raise the pump to its maximum and let the temperature settle.",
            "Return the pump to its original duty.",
        ),
    ),
    "radiator_influence": (
        "Radiator influence",
        (
            "Hold the pump at a fixed, known-safe duty you choose.",
            "Set the radiator fans to a low duty and let the temperature settle.",
            "Raise the radiator fans to a mid duty and let the temperature settle.",
            "Raise the radiator fans to maximum and let the temperature settle.",
            "Return the radiator fans to their original duty.",
        ),
    ),
}


@dataclass(frozen=True)
class StageGate:
    """Whether a guided template may advance to its next stage.

    §3.3 of the agreed scope: under Q3-A there is no daemon-driven stepping, so
    this client-side gate is the only place a guided workflow can still refuse.
    It is a real requirement, not a downgrade — §12 asks that an unsafe rising
    temperature block progression, and this is where that happens.
    """

    can_advance: bool
    reason: str = ""


def isolation_stage_gate(session: ValidationSession | None) -> StageGate:
    """Refuse to advance a template while the machine is not in a safe state.

    Three refusals, each matching a §12 bullet or the global safety rules:

    * not recording — there is nothing to mark;
    * the thermal ladder is active — the Overview forbids reducing cooling while
      temperature is already unsafe, and advancing a stage asks the user to do
      exactly that;
    * no fresh temperature — a stage gate that cannot see the temperature cannot
      claim the temperature is safe, and lack of evidence must not read as a pass.
    """
    if session is None or not session.is_recording:
        return StageGate(False, "Start the observation before stepping through a template.")
    last = session.samples[-1] if session.samples else None
    if last is None:
        return StageGate(False, "Waiting for the first sample.")
    if last.thermal_state and last.thermal_state != "normal":
        return StageGate(
            False,
            f"Thermal protection is active ({last.thermal_state}). "
            "Let the system recover before changing anything.",
        )
    if last.temperature_c is None:
        return StageGate(
            False,
            "No temperature reading — the observation cannot confirm it is safe to continue.",
        )
    return StageGate(True)


@dataclass(frozen=True)
class TracePoint:
    at_s: float
    value: float


@dataclass(frozen=True)
class TraceMarker:
    at_s: float
    label: str


@dataclass(frozen=True)
class SessionTrace:
    """A session's samples as plottable series (Q9, §9.1's event timeline).

    Qt-free by design, and built from the session's **own** ``samples`` array —
    deliberately not from ``HistoryStore``, which is live-only and keyed on a
    monotonic clock, so it cannot render a session that has already finished.
    That coupling is exactly why Phase 6 shipped no session chart.

    **A missing reading produces no point, never a zero.** Plotting `0` for an
    unknown power would draw the series to the floor and read as "the CPU
    stopped drawing power", which is a claim; a gap is the truth.
    """

    temperature: list[TracePoint] = field(default_factory=list)
    coolant: list[TracePoint] = field(default_factory=list)
    package_power: list[TracePoint] = field(default_factory=list)
    gpu_power: list[TracePoint] = field(default_factory=list)
    pump_rpm: list[TracePoint] = field(default_factory=list)
    radiator_rpm: list[TracePoint] = field(default_factory=list)
    markers: list[TraceMarker] = field(default_factory=list)
    steady_from_s: float | None = None
    steady_to_s: float | None = None

    @property
    def has_data(self) -> bool:
        """Whether anything at all is plottable.

        The chart draws nothing when this is false. Empty axes read as "we
        measured and found zero" — the same rule `PwmResponseChart` follows.
        """
        # `P8-ak`: `gpu_power` belongs here. Its absence meant a trace whose
        # only series was GPU power reported `has_data=False`, so the Timeline
        # section was hidden and the chart returned before plotting — recorded
        # data silently dropped. The sibling `has_power` below already counted
        # it, which is what made the omission look deliberate.
        return bool(
            self.temperature
            or self.coolant
            or self.pump_rpm
            or self.radiator_rpm
            or self.package_power
            or self.gpu_power
        )

    @property
    def has_power(self) -> bool:
        return bool(self.package_power or self.gpu_power)


def _series(samples, pick) -> list[TracePoint]:
    out: list[TracePoint] = []
    for s in samples:
        value = pick(s)
        if value is None:
            continue
        out.append(TracePoint(at_s=s.elapsed_ms / 1000.0, value=float(value)))
    return out


def _member_series(samples, member_id: str | None) -> list[TracePoint]:
    if not member_id:
        return []

    def pick(sample: ValidationSample):
        for m in sample.members:
            if m.member_id == member_id:
                return m.rpm
        return None

    return _series(samples, pick)


def build_session_trace(
    session: ValidationSession,
    steady: ValidationSteadyState | None = None,
    *,
    event_label=None,
) -> SessionTrace:
    """Build the plottable trace for one session.

    ``event_label`` renders an event kind for the marker caption; the caller
    injects `validation_view.event_label` rather than this module importing it,
    keeping the two view-models independent.
    """
    samples = session.samples
    meta = session.metadata
    radiator_members = getattr(meta, "radiator_members", None) or []

    last_s = samples[-1].elapsed_ms / 1000.0 if samples else None
    steady_from = None
    if steady is not None and steady.start_ms is not None:
        steady_from = steady.start_ms / 1000.0

    return SessionTrace(
        temperature=_series(samples, lambda s: s.temperature_c),
        coolant=_series(samples, lambda s: s.coolant_c),
        package_power=_series(samples, lambda s: s.package_power_w),
        gpu_power=_series(samples, lambda s: s.gpu_power_w),
        pump_rpm=_member_series(samples, getattr(meta, "pump_member", None)),
        radiator_rpm=_member_series(samples, radiator_members[0] if radiator_members else None),
        markers=[
            TraceMarker(
                at_s=e.elapsed_ms / 1000.0,
                label=event_label(e.kind) if event_label else e.kind,
            )
            for e in session.events
        ],
        steady_from_s=steady_from,
        # The steady region runs to the end of the recording by construction:
        # the daemon reports where it STARTED, and it held from there or the
        # verdict would not have been `detected`.
        steady_to_s=last_s if steady_from is not None else None,
    )


def _radiator_text(sample: ValidationSample | None, meta) -> str:
    """The first radiator member, or unknown.

    Deliberately not an aggregate across radiator members: §3 of Phase 5 says
    member identity is preserved and never flattened into an invented average,
    and a mean RPM across fans behind different headers would be exactly that.
    """
    members = getattr(meta, "radiator_members", None) or []
    return _member_text(sample, members[0] if members else None)
