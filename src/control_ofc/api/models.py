"""Typed data models for daemon API responses.

These are the GUI's internal representations — UI code should only work with
these types, never raw JSON dictionaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConnectionState(Enum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"


class OperationMode(Enum):
    AUTOMATIC = "automatic"
    READ_ONLY = "read_only"
    DEMO = "demo"


class Freshness(Enum):
    FRESH = "fresh"
    STALE = "stale"
    INVALID = "invalid"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@dataclass
class OpenfanCapability:
    present: bool = False
    channels: int = 0
    rpm_support: bool = False
    write_support: bool = False


@dataclass
class HwmonCapability:
    present: bool = False
    pwm_header_count: int = 0
    write_support: bool = False


@dataclass
class KernelWarning:
    """Daemon-emitted kernel-version advisory for the active GPU (DEC-098).

    Mirrors `crate::hwmon::kernel_warnings::KernelWarning` on the daemon.
    Severity is one of ``"info" | "medium" | "high" | "critical"``; the GUI
    surfaces high/critical entries as a one-time popup and logs everything.
    """

    id: str = ""
    severity: str = "info"
    message: str = ""


@dataclass
class AmdGpuCapability:
    present: bool = False
    model_name: str | None = None
    display_label: str = "AMD D-GPU"
    pci_id: str | None = None
    fan_control_method: str = "none"
    pmfw_supported: bool = False
    fan_rpm_available: bool = False
    fan_write_supported: bool = False
    is_discrete: bool = False
    overdrive_enabled: bool = False
    pci_device_id: int | None = None
    pci_revision: int | None = None
    gpu_zero_rpm_available: bool = False
    # DEC-098: list of advisories the daemon detected based on the running
    # kernel + this GPU's identity. Empty when nothing applies; older daemons
    # without the field also yield an empty list (parser-tolerant).
    kernel_warnings: list[KernelWarning] = field(default_factory=list)


@dataclass
class IntelGpuCapability:
    """Intel discrete GPU (Arc) capability (DEC-121).

    Monitoring-only: Intel GPU fan control is firmware-managed with no
    userspace write path, so ``fan_control_method`` is always ``"read_only"``
    (fan present) or ``"none"``, and there is deliberately no
    ``fan_write_supported`` field — Intel GPU fans are never offered as
    writable controls. Mirrors the read-only subset of ``AmdGpuCapability``
    (no PMFW/overdrive/zero-RPM/kernel-warning fields). Older daemons that
    predate the field yield ``present=False`` via the parser's tolerance.
    """

    present: bool = False
    model_name: str | None = None
    display_label: str = "Intel D-GPU"
    pci_id: str | None = None
    pci_device_id: int | None = None
    driver: str | None = None  # "xe" or "i915"
    fan_control_method: str = "none"  # "read_only" | "none" — never writable
    fan_rpm_available: bool = False
    is_discrete: bool = False


@dataclass
class NvidiaGpuCapability:
    """NVIDIA discrete GPU capability (DEC-204).

    Read-only, like the Intel Arc capability (DEC-121) — NVIDIA fan control is
    never offered (nouveau's writable ``pwm1`` is excluded from discovery; the
    NVML backend is telemetry-only), so there is deliberately no
    ``fan_write_supported`` field. ``model_name``/``driver_version`` come only
    from the proprietary NVML driver; the open ``nouveau`` leg yields the
    generic ``"NVIDIA D-GPU"`` label. ``driver`` is the kernel module name
    (``"nouveau"`` or ``"nvidia"``) — not the ``nvml`` userspace library. Older
    daemons that predate the field yield ``present=False`` via parser tolerance.
    """

    present: bool = False
    model_name: str | None = None
    display_label: str = "NVIDIA D-GPU"
    pci_id: str | None = None
    driver: str | None = None  # "nouveau" | "nvidia" (kernel module)
    driver_version: str | None = None
    fan_control_method: str = "none"  # "read_only" | "none" — never writable
    fan_rpm_available: bool = False
    is_discrete: bool = False


@dataclass
class UnsupportedCapability:
    present: bool = False
    status: str = "unsupported"


@dataclass
class AioHwmonCapability:
    """Liquid-cooler (AIO) hwmon capability (daemon >= 1.18.0, DEC-156).

    Backward-compatible superset of :class:`UnsupportedCapability`: ``present``
    and ``status`` are always parseable (pre-1.18.0 daemons send only those),
    while ``pump_writable`` / ``coolant_available`` default to ``False`` against
    an older daemon. ``status`` is one of ``"supported"`` (a writable AIO
    pump/fan header), ``"monitor_only"`` (a cooler/coolant sensor is detected
    but nothing is writable — never offer control), or ``"unsupported"``.
    USB-only coolers are out of scope and reported via ``aio_usb``.
    """

    present: bool = False
    status: str = "unsupported"
    pump_writable: bool = False
    coolant_available: bool = False


@dataclass
class FeatureFlags:
    openfan_write_supported: bool = False
    hwmon_write_supported: bool = False


@dataclass
class ControlCapability:
    """Daemon control-plane capabilities (DEC-159/160).

    Top-level ``control`` block in ``GET /capabilities``. ``profile_storage``
    is True when the daemon exposes the ``/profiles`` CRUD + validate surface
    (daemon ≥ 1.19). ``autonomous_control`` is True only on a 2.0.0+ daemon that
    is the sole authoritative fan writer (DEC-165) — the thin GUI gates all
    runtime control on it: a daemon that omits the flag (pre-2.0) defaults it to
    False here and the GUI refuses to operate. Absent block ⇒ all fields default
    to the old/safe value (AIP-180).
    """

    profile_storage: bool = False
    curve_evaluation: bool = False
    manual_override: bool = False
    fan_identify: bool = False
    autonomous_control: bool = False
    min_supported_gui: str = ""
    # DEC-265: daemon exposes POST /fans/openfan/rescan, which adopts an
    # OpenFanController that appeared after the daemon booted (or that failed its
    # identity handshake once at startup) without a restart. Older daemons omit
    # the field, so it defaults False and the action stays hidden rather than
    # offering a button that can only 404.
    openfan_rescan: bool = False
    # DEC-311 (daemon >= 2.28.0): the daemon classifies PWM headers by role,
    # refuses to STOP a role="pump" header during identify (perturbing it
    # instead), keeps verify above the pump floor, and accepts
    # POST /config/header-role.
    #
    # Gating on this is a TRUTHFULNESS requirement, not a cosmetic one: the
    # wizard's "the pump will briefly change speed" copy is a LIE against an
    # older daemon, which drives the pump to 0. When this is False the wizard
    # keeps its original "the fan will stop" wording, which is the honest
    # description of what that daemon actually does.
    header_roles: bool = False
    # Daemon ≥ 2.23.0 accepts a ``remove`` array on
    # ``POST /config/profile-search-dirs``, so a stale profile search directory
    # can be pruned instead of only ever added.
    #
    # Gating on this flag is mandatory, and for a stronger reason than
    # ``openfan_rescan``'s: an older daemon does not 404 a ``remove``, it parses
    # only ``add`` and **silently ignores the rest**. Probing would read that
    # partial success as a whole one and tell the user a directory had been
    # pruned when it had not.
    profile_search_dir_remove: bool = False
    # AIO-MB Phase 3 (daemon >= 2.29.0): the daemon exposes
    # POST /hwmon/{id}/characterize plus the GET/DELETE
    # /diagnostics/characterization pair — the deeper PWM/RPM response sweep
    # that sits ALONGSIDE the quick "Test PWM Control" verify.
    #
    # Gate on this rather than probing. An older daemon 404s the POST — the same
    # status this route returns for an unknown header id. They differ only in
    # error.code ("not_found" from the route fallback vs "validation_error" from
    # the handler), and keying feature detection on that is exactly the
    # undocumented coupling the capability exists to replace.
    pwm_characterization: bool = False
    # DEC-316 (AIO-MB Phase 4, daemon >= 2.31.0): the daemon exposes
    # `GET /inventory/cooling-devices`, `POST /config/cooling-device` and
    # `DELETE /config/cooling-device/{id}`. Gates those ENDPOINTS only — the
    # additive `HwmonHeader` fields that shipped alongside need no flag, since
    # each is optional and absence already means "fall back".
    cooling_devices: bool = False
    # AIO-MB Phase 5 (daemon >= 2.32.0): the validation-session surface.
    # Gate on this rather than probing — an older daemon 404s these routes from
    # the route fallback, which is indistinguishable from a genuine "no such
    # session" without inspecting `error.code`.
    validation_sessions: bool = False


@dataclass
class Capabilities:
    api_version: int = 1
    daemon_version: str = ""
    ipc_transport: str = ""
    openfan: OpenfanCapability = field(default_factory=OpenfanCapability)
    hwmon: HwmonCapability = field(default_factory=HwmonCapability)
    amd_gpu: AmdGpuCapability = field(default_factory=AmdGpuCapability)
    intel_gpu: IntelGpuCapability = field(default_factory=IntelGpuCapability)
    nvidia_gpu: NvidiaGpuCapability = field(default_factory=NvidiaGpuCapability)
    aio_hwmon: AioHwmonCapability = field(default_factory=AioHwmonCapability)
    aio_usb: UnsupportedCapability = field(default_factory=UnsupportedCapability)
    features: FeatureFlags = field(default_factory=FeatureFlags)
    control: ControlCapability = field(default_factory=ControlCapability)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@dataclass
class SubsystemStatus:
    name: str = ""
    status: str = "unknown"
    age_ms: int | None = None
    reason: str = ""


@dataclass
class OverrideStatusEntry:
    """One active manual override on the daemon's `/status` poll surface
    (DEC-163). Carries no `override_token` — this is an *observation* surface,
    not a control one: an override the GUI did not create cannot be renewed or
    released (no token), only displayed or superseded by a fresh take (DEC-169).
    """

    control_id: str = ""
    pwm_percent: int = 0
    expires_in_secs: int = 0


@dataclass
class IdentifyStatusEntry:
    """One active fan-identify hold on the daemon's `/status` poll surface
    (DEC-166), each with its remaining deadman TTL."""

    fan_id: str = ""
    expires_in_secs: int = 0
    # DEC-311: so a GUI polling into an identify it did not initiate still
    # describes it truthfully. "stop" (pre-2.28.0 daemons omit it) or
    # "pump_perturb".
    mode: str = "stop"
    identify_pwm_percent: int = 0


@dataclass
class UnavailableSensor:
    """A sensor the daemon discovered but currently cannot read (DEC-193).

    Mirrors the daemon's ``UnavailableSensorEntry`` on the ``/status`` +
    ``/poll`` surface. The canonical case is a wireless-radio temperature
    (e.g. an ``ath12k`` WiFi chip) that returns ``ENETDOWN`` whenever the radio
    is soft-blocked. These are surfaced for *display only* (Diagnostics): the
    daemon evicts them from the live ``sensors`` array so a stale value is never
    served, and the GUI does not raise a staleness warning for them.
    """

    id: str = ""
    label: str = ""
    reason: str = ""
    unavailable_for_ms: int = 0


# Every value the daemon's `skipped_controls[].reason` field can take (273-i), in
# no particular order. This is the WIRE vocabulary, so — like
# `THERMAL_STATE_VALUES` above — it belongs with the model rather than with the
# one surface that renders it, and `test_skip_reason_map_covers_the_wire_vocabulary`
# pins the Controls-page presentation map against it. DEC-257 is the reason: a
# presentation map keyed off a wire field drifted silently once already and a live
# thermal recovery rendered as a neutral grey pill.
SKIP_REASON_VALUES: tuple[str, ...] = (
    "curve_not_found",
    "sensor_unavailable",
    "mix_unresolvable",
    "sync_unresolvable",
)


@dataclass
class ControlOutput:
    """One control's applied output from the engine's last evaluating tick (277-k).

    The value the daemon actually applied, whatever produced it — a curve or a
    live manual override — because the question the Controls card renders it to
    answer is "what are the fans doing?".

    **Not a per-fan duty.** A member can sit above this on a role-aware floor or
    below it on a diverging GPU (DEC-119); per-fan duty is ``FanReading.
    last_commanded_pwm``. **Absence is meaningful**: a control the daemon did not
    evaluate — no profile, a listed skip, or the whole of a thermal event — is
    simply not in the list, and the card must fall back to "no value" rather than
    carrying its previous figure forward. Daemon ≥ 2.22.0; older daemons omit the
    array entirely and the GUI defaults it to empty.
    """

    control_id: str = ""
    output_pct: float = 0.0


@dataclass
class SkippedControl:
    """A control the daemon's engine cannot resolve, so is not commanding (273-i).

    Mirrors the daemon's ``SkippedControlEntry`` on the ``/status`` + ``/poll``
    surface. The fans of such a control hold their last commanded duty — a skip
    never lowers a fan (DEC-269) — but nothing is driving them, which is what
    this says.

    ``reason`` is a stable token from `SKIP_REASON_VALUES`, not prose: the daemon
    deliberately sends the token and leaves the wording to the client, so it can
    be styled and localised here. An unrecognised token must still render (a
    newer daemon may add one), which is why the presentation map has a fallback.
    """

    control_id: str = ""
    control_name: str = ""
    reason: str = ""
    skipped_for_ms: int = 0


@dataclass
class ReadinessRollup:
    """Compact hardware-readiness rollup from ``GET /status`` + ``/poll`` (DEC-206).

    A cache-cheap summary the daemon mirrors onto the poll surface so the
    Dashboard can show a single health chip without fetching the full
    ``/inventory/readiness`` list. ``overall`` is the most severe item's
    severity; ``top_summary`` / ``top_code`` name the single most-important next
    step (both ``None`` when ``overall`` is ``"ok"``). The whole object is
    ``None`` on daemons predating DEC-206 (and until the daemon's startup seed
    runs) → the GUI hides the chip.
    """

    overall: str = "ok"  # ok | info | warning | critical
    critical: int = 0
    warning: int = 0
    info: int = 0
    top_summary: str | None = None
    top_code: str | None = None

    @property
    def to_fix_count(self) -> int:
        """Number of items that need attention (critical + warning); the chip's
        ``N to fix``. Info-tier items are advisory and not counted."""
        return self.critical + self.warning


@dataclass
class RuntimeConfigDegraded:
    """The daemon's own ``runtime.toml`` failed to load, so it is running on
    built-in defaults (daemon >= 2.34.0, DEC-321).

    **[SAFETY], and this is why the field is on the wire at all.** The defaults
    carry no ``header_roles``. On a board whose Super-I/O publishes no
    ``pwmN_label`` files — the case the whole AIO-MB programme exists for — a
    user's ``pump`` assignment is the *only* evidence a header drives a pump, so
    a failed **startup** load removes that header's 30% floor, its stop exemption
    and its pump-safe identify. The daemon's only other notification is one
    ``warn!`` in its journal, which no client can see.

    ``None`` when the config loaded cleanly *and* when the daemon predates
    2.34.0. The two are deliberately indistinguishable and both read as "fine" —
    the safe direction, because that is exactly what such a daemon reports today.

    A **missing** ``runtime.toml`` is not a degradation and is never reported:
    that is first boot, and defaults are the correct answer there.
    """

    #: ``unreadable`` (I/O error, or larger than the daemon's 4 MiB read cap) or
    #: ``malformed`` (read, but not valid TOML for this daemon version —
    #: canonically a downgrade, since each section is ``deny_unknown_fields``).
    reason: str = ""
    #: The file that failed to load.
    path: str = ""
    #: The underlying I/O or TOML error, verbatim. Daemon prose, **not** a stable
    #: token — render it, never branch on it.
    detail: str = ""
    #: ``startup`` or ``reload``. These cost different things: only the boot load
    #: seeds every runtime-mutable key, so a ``reload`` failure leaves header
    #: roles as startup established them and is materially narrower.
    phase: str = ""


# Every value the daemon's `thermal_state` field can take (DEC-132/165), in
# severity order. This is the WIRE vocabulary, so it belongs with the model
# rather than with any one surface that renders it.
#
# DEC-257: three separate presentation maps key off this field
# (`ui.status_banner.THERMAL_STATES`, `services.dashboard_view._THERMAL_REASONS`,
# `services.system_state_view._THERMAL_STATE`) and one of them had silently
# drifted — it carried three values the daemon never sends and was missing
# `recovery` and `no_sensor_fallback`, so a live thermal recovery rendered as a
# neutral grey pill. They cannot be collapsed into one map (they map to different
# things, and one lives behind a Qt import while two are deliberately Qt-free),
# so `test_thermal_state_maps_cover_the_wire_vocabulary` pins all of them against
# this tuple instead. Any future map is one line away from the same guarantee.
THERMAL_STATE_VALUES: tuple[str, ...] = (
    "normal",
    "recovery",
    "emergency",
    "no_sensor_fallback",
)


@dataclass
class DaemonStatus:
    api_version: int = 1
    daemon_version: str = ""
    overall_status: str = "unknown"
    subsystems: list[SubsystemStatus] = field(default_factory=list)
    uptime_seconds: int | None = None
    # Daemon thermal safety override state (DEC-132) — one of
    # `THERMAL_STATE_VALUES`. While not "normal" the daemon is
    # forcing OpenFan + writable-hwmon PWM to protect the hardware — the engine
    # is the sole writer since 2.0.0, so there is no GUI loop to stand down
    # (DEC-165). Defaults to "normal" for older daemons that don't send the field.
    thermal_state: str = "normal"
    # Daemon-held live overrides / fan-identify holds (DEC-163/166), each with
    # remaining TTL. Omitted from the wire when empty (daemon skips empty Vecs),
    # so these default to []. Poll-authoritative observation surface consumed by
    # the Controls page (display-only reconcile) and Diagnostics (DEC-169).
    overrides: list[OverrideStatusEntry] = field(default_factory=list)
    fan_identify: list[IdentifyStatusEntry] = field(default_factory=list)
    # DEC-193: sensors discovered but currently unreadable (e.g. an ath12k WiFi
    # temp while the radio is off). Omitted from the wire when empty (daemon
    # skips empty Vecs) → defaults to []. Display-only; surfaced in Diagnostics.
    unavailable_sensors: list[UnavailableSensor] = field(default_factory=list)
    # 273-i: controls the daemon's engine cannot resolve, so is not commanding —
    # e.g. a Mix naming a curve id the profile no longer has. Their fans hold
    # their last commanded duty. Omitted from the wire when empty (daemon skips
    # empty Vecs) and absent entirely from daemons older than 2.21.0 → defaults
    # to []. Display-only; surfaced on the Controls page card for the control.
    skipped_controls: list[SkippedControl] = field(default_factory=list)
    control_outputs: list[ControlOutput] = field(default_factory=list)
    # AIO-MB Phase 5: the current or most recent validation session, in
    # miniature, so a live panel needs no second request. `None` when no session
    # has ever run OR the daemon predates 2.32.0 — absence is not an error, and
    # the two cases are deliberately indistinguishable because nothing should
    # behave differently between them.
    validation_session: ValidationSessionSummary | None = None
    # DEC-194: the daemon's active profile, mirrored onto every /poll status so an
    # external activation (CLI --profile, another client, systemd) shows within
    # ~1 s instead of the slow /profile/active refresh. `None` (not "") when the
    # key is ABSENT — an older daemon that never sends it, or genuinely no active
    # profile — which is how the polling worker knows to leave the /profile/active
    # fallback authoritative instead of clobbering it with a blank.
    active_profile_id: str | None = None
    active_profile_name: str | None = None
    # DEC-206: compact hardware-readiness rollup for the Dashboard health chip,
    # mirrored onto every /poll status. `None` when the key is ABSENT (older
    # daemon, or before the daemon's startup seed runs) → the chip is hidden.
    readiness: ReadinessRollup | None = None
    # DEC-321 / `WIRE-a`: set when the daemon's own runtime.toml failed to load
    # and it fell back to built-in defaults. `None` when it loaded cleanly OR the
    # daemon predates 2.34.0 — both read as "fine", which is the safe direction.
    # STICKY for the daemon's lifetime: a later successful `POST /config/*`
    # repairs the file but does not clear this, because nearly every
    # runtime-mutable key is consumed once at startup. Only a restart clears it.
    runtime_config_degraded: RuntimeConfigDegraded | None = None


# ---------------------------------------------------------------------------
# Sensors and Fans
# ---------------------------------------------------------------------------


@dataclass
class SensorThresholds:
    """Curated hwmon temperature-threshold sysfs attributes (DEC-117).

    Mirrors the daemon's :class:`SensorThresholdsResponse`. Every field is
    optional because driver coverage varies wildly across motherboards —
    k10temp exposes essentially none, coretemp typically exposes
    ``max``/``crit``, amdgpu exposes ``crit``/``emergency``, and the
    nct6775/nct6683 families expose ``max``/``crit``/``alarm``. Alarm flags
    are snapshotted at daemon discovery time, not refreshed every poll.
    """

    max_c: float | None = None
    min_c: float | None = None
    crit_c: float | None = None
    crit_hyst_c: float | None = None
    emergency_c: float | None = None
    emergency_hyst_c: float | None = None
    lcrit_c: float | None = None
    offset_c: float | None = None
    alarm: bool | None = None
    max_alarm: bool | None = None
    crit_alarm: bool | None = None
    fault: bool | None = None

    def is_empty(self) -> bool:
        """True when no attribute was reported by the daemon.

        The daemon omits the entire ``thresholds`` JSON object when no
        attribute was readable, so an instance with this method returning
        True normally means a malformed/partial payload — the GUI treats
        it the same as "no thresholds" for rendering purposes.
        """
        return (
            self.max_c is None
            and self.min_c is None
            and self.crit_c is None
            and self.crit_hyst_c is None
            and self.emergency_c is None
            and self.emergency_hyst_c is None
            and self.lcrit_c is None
            and self.offset_c is None
            and self.alarm is None
            and self.max_alarm is None
            and self.crit_alarm is None
            and self.fault is None
        )


@dataclass
class SensorReading:
    id: str = ""
    kind: str = ""
    label: str = ""
    value_c: float = 0.0
    source: str = ""
    age_ms: int = 0
    rate_c_per_s: float | None = None
    session_min_c: float | None = None
    session_max_c: float | None = None
    chip_name: str = ""
    temp_type: int | None = None
    # DEC-117: curated hwmon threshold attributes. ``None`` when the daemon
    # predates DEC-117 or when the chip exposes no attribute of interest
    # for this sensor.
    thresholds: SensorThresholds | None = None
    # DEC-193: False when this temperature must not be offered as a fan-curve
    # source (currently wireless-radio PHY temps such as ath12k WiFi, which read
    # ENETDOWN whenever the radio is down). The Controls page drops these from
    # the curve sensor picker; display is unaffected. Defaults True so a
    # pre-2.3.0 daemon that omits the field leaves every sensor selectable.
    control_eligible: bool = True

    @property
    def freshness(self) -> Freshness:
        if self.age_ms < 2000:
            return Freshness.FRESH
        if self.age_ms < 10000:
            return Freshness.STALE
        return Freshness.INVALID


@dataclass
class FanReading:
    id: str = ""
    source: str = ""
    rpm: int | None = None
    last_commanded_pwm: int | None = None
    # DEC-204: firmware-reported *measured* current fan duty % (NVIDIA via NVML),
    # distinct from ``last_commanded_pwm`` (daemon-commanded) — never conflate.
    # May exceed 100 (NVML expresses it as a % of max noise tolerance). ``None``
    # for sources without a duty readback (openfan/hwmon/amd/intel/nouveau) and
    # on pre-DEC-204 daemons (absent from the wire).
    duty_pct: int | None = None
    age_ms: int = 0
    stall_detected: bool | None = None
    # AIO-MB Phase 5: the hardware readback of `pwmN`, as a percent.
    #
    # Distinct from ``last_commanded_pwm``, which for an hwmon header carries
    # whichever of the poll's readback and the engine's command wrote last
    # (register row AIO5-a). Phase 5 needs the two as separate columns, and a
    # device-side override is classified from `command low + readback low + RPM
    # high` — neither is expressible while they share a field.
    #
    # hwmon only. ``None`` means "the daemon did not say" — never 0% — and is
    # what a pre-2.32 daemon, an OpenFan channel and a GPU fan all report.
    pwm_readback_pct: int | None = None
    # AIO-MB Phase 6 (DEC-318, daemon >= 2.33.0): the duty the daemon last
    # COMMANDED for an hwmon header, as a percent.
    #
    # The command half of the pair whose readback half is ``pwm_readback_pct``,
    # and the field to read when the value the daemon actually chose matters.
    # Single-producer daemon-side (only the hwmon write path sets it), unlike
    # ``last_commanded_pwm`` — which for an hwmon header reports whichever of the
    # poll's readback and the engine's command wrote last (AIO5-a), so for an
    # *uncontrolled* header it is a readback despite its name.
    #
    # hwmon only. ``None`` means "the daemon has never commanded this header" —
    # never 0% — and is also what a pre-2.33 daemon reports for every fan. An
    # OpenFan channel and a GPU fan keep their own unambiguous command in
    # ``last_commanded_pwm`` and report ``None`` here rather than duplicating it.
    pwm_commanded_pct: int | None = None
    # AIO-MB Phase 4: the driver's own `fanN_alarm` bit, sampled at 1 Hz.
    # `None` means "not known" — either the driver exposes no alarm attribute,
    # or the entry was refreshed by a PWM write without re-reading it. Never
    # treat None as "no alarm".
    fan_alarm: bool | None = None
    # AIO-MB Phase 4: the LIVE `pwmN_enable` mode for an hwmon header. On the
    # poll rather than on the header because the daemon writes this attribute
    # when it takes a header over, so a discovery-time value would report the
    # pre-takeover mode forever — and the field's diagnostic value is answering
    # "is something else controlling this header *now*?". `None` = not known.
    pwm_enable_mode: int | None = None

    @property
    def freshness(self) -> Freshness:
        if self.age_ms < 2000:
            return Freshness.FRESH
        if self.age_ms < 10000:
            return Freshness.STALE
        return Freshness.INVALID


# ---------------------------------------------------------------------------
# Cooling-device topology (AIO-MB Phase 4, DEC-316, daemon >= 2.31.0)
# ---------------------------------------------------------------------------


@dataclass
class DevicePolicySummary:
    """The resolved device policy a cooling device operates under.

    Read-only: these values are compiled into the daemon and selected by id.
    The GUI displays them and never sends them — the daemon rejects a payload
    carrying any of these keys by name.
    """

    id: str = ""
    display_name: str = ""
    # The policy's own declared floor. The floor a given HEADER actually gets is
    # `HwmonHeader.effective_min_pwm_pct`, which also applies the absolute pump
    # backstop — prefer that when showing what a specific fan will do.
    minimum_safe_pwm_pct: int = 0
    supports_stop: bool = False
    startup_override_seconds: int | None = None
    expected_rpm_min: int | None = None
    expected_rpm_max: int | None = None
    internal_control_possible: bool = False


@dataclass
class CoolingDevice:
    """One cooling assembly: a pump, its radiator fans, and a temperature source.

    Metadata. The daemon's profile engine never reads a cooling device, so this
    changes nothing about what a fan does — it exists so the GUI can present a
    cooler as one thing instead of re-inferring it from labels.
    """

    id: str = ""
    name: str = ""
    # Opaque token: "unknown" | "aio_liquid" | "air_cooler" | "custom_loop".
    # Render an unrecognised value rather than dropping the device (273-i).
    kind: str = "unknown"
    pump_member: str | None = None
    radiator_members: list[str] = field(default_factory=list)
    auxiliary_members: list[str] = field(default_factory=list)
    # Advisory only — a curve keeps its own `sensor_id`. Nothing in the control
    # path reads these.
    preferred_sensor: str | None = None
    fallback_sensor: str | None = None
    coolant_sensor: str | None = None
    # "available" | "unavailable". Unavailable is the NORMAL case for a
    # motherboard-connected AIO and is not an error or a readiness item.
    coolant_telemetry: str = "unavailable"
    device_policy: DevicePolicySummary = field(default_factory=DevicePolicySummary)


@dataclass
class CoolingDeviceInventory:
    cooling_devices: list[CoolingDevice] = field(default_factory=list)
    # Every policy the daemon ships, so the GUI offers the real choices rather
    # than a hardcoded list that drifts from the binary.
    available_policies: list[DevicePolicySummary] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Hwmon headers
# ---------------------------------------------------------------------------


@dataclass
class HwmonHeader:
    id: str = ""
    # DEC-229: never empty, and not always real. The daemon's `read_label` tries
    # `pwmN_label`, then `fanN_label`, then *synthesises* `pwm{pwm_index}` — and
    # the it87 driver publishes no label files for most Gigabyte boards, so the
    # synthesised form is common. Resolve through
    # `hwmon_label_resolver.resolve_hwmon_header_label` (or `AppState`) rather
    # than reading this field directly; `is_placeholder_hwmon_label` is the test.
    # Note the label is also embedded in `id` (`hwmon:<chip>:<dev>:pwmN:<label>`).
    label: str = ""
    chip_name: str = ""
    device_id: str = ""
    pwm_index: int = 0
    supports_enable: bool = False
    rpm_available: bool = False
    min_pwm_percent: int = 0
    max_pwm_percent: int = 100
    is_writable: bool = True
    pwm_mode: int | None = None  # 0=DC, 1=PWM, None=not exposed
    is_aio: bool = False  # liquid-cooler header (daemon >= 1.18.0, DEC-156)
    # DEC-311 (AIO-MB Phase 1, daemon >= 2.28.0): what this channel DRIVES.
    # Per-channel, unlike the chip-level `is_aio` — a pump on a motherboard
    # AIO_PUMP header is `role="pump", is_aio=False`, which is the whole point.
    # Already has the user's `POST /config/header-role` assignment applied.
    #
    # Treat as an OPAQUE TOKEN: render an unrecognised value rather than
    # dropping the header (the 273-i rule). Known values are "unknown",
    # "cpu_fan", "pump", "radiator_fan", "chassis_fan"; a pre-2.28.0 daemon
    # omits the field entirely, hence the "unknown" default.
    role: str = "unknown"
    # How `role` was established: "none" | "label" | "chip_mapping" |
    # "user_assigned". Lets the UI distinguish a confident classification from
    # a guess worth asking the user about.
    role_source: str = "none"

    # ── AIO-MB Phase 4 (DEC-316, daemon >= 2.31.0) ───────────────────────────
    # Every field below defaults to None/empty meaning "this daemon did not
    # say", NEVER "zero". That distinction is load-bearing for
    # `effective_min_pwm_pct` in particular: typed `int` with a 0 default, a
    # pre-2.31 daemon (which omits the key) would make the GUI believe a 0%
    # floor on a pump. `None` routes callers to the client-side reconstruction
    # in `services/pump_protection.py` instead.
    #
    # The daemon-enforced duty floor for this header, in percent. Prefer this
    # over re-deriving the floor from labels and chip names.
    effective_min_pwm_pct: int | None = None
    # Whether this header may be driven to 0 at all. False wherever the daemon's
    # pump-protection union holds. `None` = unknown, fall back.
    stop_permitted: bool | None = None
    # The cooling device that claims this header, if any.
    cooling_device_id: str | None = None
    # ── Header capability audit (read-only driver introspection) ──
    # PWM base frequency in Hz from `pwmN_freq`.
    pwm_freq_hz: int | None = None
    # NOTE: the header carries no `pwm_enable_mode`. The header's CURRENT mode is
    # state, not a capability — the daemon writes `pwmN_enable` itself when it
    # takes a header over — so it rides the 1 Hz poll as `FanReading.pwm_enable_mode`.
    # `supports_enable` above is the static half: whether the attribute exists.
    #
    # The `pwmN_enable` values this chip's driver accepts. EMPTY MEANS UNKNOWN:
    # nothing in sysfs reports this, so the daemon derives it from driver
    # knowledge and has none for unrecognised chips. Never render an empty list
    # as "no modes supported".
    supported_pwm_enable_modes: list[int] = field(default_factory=list)
    # Low RPM alarm threshold from `fanN_min`.
    rpm_min_threshold: int | None = None
    # High RPM threshold from `fanN_max`. Absent on most Super-I/O chips.
    rpm_max_threshold: int | None = None
    # Tach pulses per revolution from `fanN_pulses`. Absent on it87 — the
    # validation board — so `None` is the common case, not an anomaly.
    tach_pulses_per_rev: int | None = None


# ---------------------------------------------------------------------------
# Write responses
# ---------------------------------------------------------------------------


@dataclass
class GpuFanResetResult:
    gpu_id: str = ""
    reset: bool = False


@dataclass
class ProfileActivateResult:
    """Response from POST /profile/activate."""

    activated: bool = False
    profile_id: str = ""
    profile_name: str = ""


@dataclass
class OverrideGrant:
    """Response from POST /control/{id}/override (DEC-163)."""

    control_id: str = ""
    override_token: int = 0
    pwm_percent: int = 0
    ttl_secs: int = 0
    renew_secs: int = 0
    expires_in_secs: int = 0


@dataclass
class OverrideRenewResult:
    """Response from POST /control/{id}/override/renew (DEC-163)."""

    control_id: str = ""
    override_token: int = 0
    ttl_secs: int = 0
    expires_in_secs: int = 0


@dataclass
class OverrideReleaseResult:
    """Response from DELETE /control/{id}/override (DEC-163)."""

    control_id: str = ""
    released: bool = False


@dataclass
class IdentifyResult:
    """Response from POST /fans/{id}/identify (DEC-166)."""

    fan_id: str = ""
    action: str = ""
    expires_in_secs: int | None = None
    # DEC-311: which behaviour the DAEMON chose. The client always asks for
    # "stop"; the daemon substitutes a safe perturbation for a pump-role header
    # and reports back what it did. "stop" | "pump_perturb"; None on restore,
    # and None from a pre-2.28.0 daemon (which always stops).
    mode: str | None = None
    # The duty the fan is held at: 0 for a stop, >= 30 for a pump perturbation.
    identify_pwm_percent: int | None = None
    # What it was running at beforehand, so the UI can say "60% -> 85%".
    baseline_pwm_percent: int | None = None


@dataclass
class FieldViolation:
    """One validation finding from ``error.details.field_violations`` (DEC-160).

    ``severity`` is the daemon's lowercase ``"error"`` | ``"warning"`` tier
    (DEC-160). It defaults to ``"error"`` for older daemons that omit it — a
    violation surfaced on a 400 rejection is an error unless told otherwise.
    """

    field: str = ""
    reason: str = ""
    description: str = ""
    severity: str = "error"


@dataclass
class ActiveProfileInfo:
    """Response from GET /profile/active."""

    active: bool = False
    profile_id: str = ""
    profile_name: str = ""


@dataclass
class ProfileDeactivateResult:
    """Response from POST /profile/deactivate."""

    deactivated: bool = False
    previous_profile_id: str | None = None
    previous_profile_name: str | None = None


# ---------------------------------------------------------------------------
# Sensor history
# ---------------------------------------------------------------------------


# NOTE: deferred-feature scaffolding. CalPoint / CalibrationResult /
# parse_calibration_result model `POST /fans/openfan/{ch}/calibrate`, whose
# built-in UI flow is deferred (docs/08_API_Integration_Contract.md §
# calibration) — no DaemonClient method or widget consumes them yet. Kept
# (with tests) so the calibration UI can land against a parsed contract.


@dataclass
class CalPoint:
    """A single calibration sweep data point."""

    pwm_percent: int = 0
    rpm: int = 0


@dataclass
class CalibrationResult:
    """Result of a fan calibration sweep."""

    fan_id: str = ""
    points: list[CalPoint] = field(default_factory=list)
    start_pwm: int | None = None
    stop_pwm: int | None = None
    min_rpm: int = 0
    max_rpm: int = 0


@dataclass
class ProfileSearchDirsResult:
    """Response from POST /config/profile-search-dirs.

    ``search_dirs`` is the daemon's resulting list — render *that*, never a
    locally-predicted one: an edit can be partly idempotent (removing an entry
    that was never registered, re-adding one already present).
    """

    updated: bool = False
    search_dirs: list[str] = field(default_factory=list)


@dataclass
class HistoryPoint:
    """A single point from the daemon's sensor history ring buffer."""

    ts: int = 0  # Unix timestamp in milliseconds
    v: float = 0.0  # Value (°C, RPM, etc.)


@dataclass
class SensorHistory:
    """Response from GET /sensors/history."""

    entity_id: str = ""
    points: list[HistoryPoint] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Hardware diagnostics
# ---------------------------------------------------------------------------


@dataclass
class HwmonChipInfo:
    chip_name: str = ""
    device_id: str = ""
    expected_driver: str = ""
    in_mainline_kernel: bool = False
    header_count: int = 0


@dataclass
class HwmonDiagnostics:
    chips_detected: list[HwmonChipInfo] = field(default_factory=list)
    total_headers: int = 0
    writable_headers: int = 0
    enable_revert_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class GpuDiagnosticsInfo:
    pci_bdf: str = ""
    pci_device_id: int = 0
    pci_revision: int = 0
    model_name: str | None = None
    fan_control_method: str = "none"
    overdrive_enabled: bool = False
    ppfeaturemask: str | None = None
    ppfeaturemask_bit14_set: bool = False
    zero_rpm_available: bool = False
    # DEC-119: PMFW OD_RANGE fan-speed bounds (percent). The firmware-enforced
    # minimum (~15% on RDNA3+) is the real reason a PMFW GPU fan cannot be
    # driven to 0% via the curve. None for non-PMFW GPUs / older daemons.
    fan_speed_min_pct: int | None = None
    fan_speed_max_pct: int | None = None
    # DEC-119: best-effort PMFW ``fan_minimum_pwm`` setting (percent). None
    # when absent/unparseable or the daemon predates the field.
    fan_minimum_pwm: int | None = None
    # DEC-119: whether the amdgpu driver is bound to this GPU's PCI device.
    # Defaults True (an hwmon node implies a bound driver) for forward-compat.
    amdgpu_driver_bound: bool = True
    # DEC-119: kernel-regression advisories for this GPU, mirroring
    # ``/capabilities.amd_gpu.kernel_warnings``. Hand-parsed in
    # ``parse_hardware_diagnostics`` (nested dataclasses can't round-trip via
    # ``**``). Empty when none apply or the daemon predates the field.
    kernel_warnings: list[KernelWarning] = field(default_factory=list)


@dataclass
class IntelGpuDiagnosticsInfo:
    """Intel discrete GPU diagnostics (DEC-121).

    Read-only by nature — no ppfeaturemask/overdrive/PMFW/kernel-warning
    fields. ``fan_control_note`` is a daemon-supplied, user-facing explanation
    of why fan control is unavailable (firmware-managed).
    """

    pci_bdf: str = ""
    pci_device_id: int = 0
    pci_revision: int = 0
    model_name: str | None = None
    driver: str = ""  # "xe" or "i915"
    fan_control_method: str = "none"
    fan_rpm_available: bool = False
    fan_control_note: str = ""


@dataclass
class NvidiaGpuDiagnosticsInfo:
    """NVIDIA discrete GPU diagnostics (DEC-204).

    Read-only by nature — no fan write path (nouveau's ``pwm1`` is excluded from
    discovery; the NVML backend is telemetry-only). ``fan_control_note`` is a
    daemon-supplied, user-facing explanation of why fan control is unavailable.
    ``driver`` is the kernel module name (``"nouveau"``/``"nvidia"``). No
    ``pci_device_id``/``pci_revision`` — the daemon has no NVIDIA device-id →
    model table. ``model_name``/``driver_version`` are NVML-only.
    """

    pci_bdf: str = ""
    model_name: str | None = None
    driver: str = ""  # "nouveau" | "nvidia" (kernel module)
    driver_version: str | None = None
    fan_control_method: str = "none"
    fan_rpm_available: bool = False
    fan_control_note: str = ""


@dataclass
class AmdPciDeviceInfo:
    """An AMD VGA-class PCI device and its bound driver (DEC-119).

    Mirrors the daemon's ``AmdPciDeviceInfo``. Detected by scanning PCI space
    independently of hwmon, so a GPU whose amdgpu driver failed to bind
    (blacklist, KMS failure, vfio-pci passthrough) is still reported — that
    case produces no hwmon node and an absent ``GpuDiagnosticsInfo``.
    """

    pci_bdf: str = ""
    pci_device_id: int = 0
    driver: str | None = None
    amdgpu_bound: bool = False
    hwmon_present: bool = False


@dataclass
class ThermalSafetyInfo:
    """Thermal-emergency state and thresholds, as reported by the daemon.

    The two threshold defaults are **fallbacks for a daemon that omits the
    fields**, not a source of truth — the daemon owns these values and DEC-292
    made it report them from one place rather than restating them. Read
    ``emergency_threshold_c`` (the Overview/System State surfaces already do);
    never hardcode the number beside it, or the GUI becomes a sixth copy of a
    threshold that has just been reduced to one.
    """

    state: str = "normal"
    cpu_sensor_found: bool = False
    # DEC-308: PER-MACHINE, not a constant. The daemon derives the trip point
    # from the CPU's own reported design ceiling and reports what it actually
    # acted on, so this default is only what a daemon that reports nothing
    # implies — it is the daemon's floor, never an upper bound. Render the
    # reported value; do not compare it to a literal.
    emergency_threshold_c: float = 105.0
    release_threshold_c: float = 80.0


@dataclass
class KernelModuleInfo:
    name: str = ""
    loaded: bool = False
    in_mainline: bool = False


@dataclass
class AcpiConflictInfo:
    io_range: str = ""
    claimed_by: str = ""
    conflicts_with_driver: str = ""


@dataclass
class ModuleCollisionInfo:
    """Pair of loaded driver modules that race for the same chip (DEC-105).

    Distinct from `AcpiConflictInfo` (about I/O port ranges) and the
    GUI-side `CONFLICTING_MODULE_SETS` (a static name-pair fallback used
    when the daemon doesn't report this field). When the daemon reports a
    collision the GUI must render a CRITICAL banner and discourage PWM
    writes until the user resolves the load ordering.
    """

    module_a: str = ""
    module_b: str = ""
    # `severity` defaults to "info" deliberately — the conservative
    # direction. The daemon always serializes the field on every entry it
    # emits (no `skip_serializing_if`), so the default only applies when a
    # malformed entry is missing the field. In that case we never want to
    # misclassify a lower-severity future entry as CRITICAL. Mirrors the
    # `KernelWarning.severity` default convention.
    severity: str = "info"
    summary: str = ""
    remediation: str = ""


@dataclass
class BoardInfo:
    vendor: str = ""
    name: str = ""
    bios_version: str = ""


@dataclass
class HwmonVerifyState:
    pwm_enable: int | None = None
    pwm_raw: int | None = None
    pwm_percent: int | None = None
    rpm: int | None = None


@dataclass
class HwmonVerifyResult:
    header_id: str = ""
    result: str = ""
    initial_state: HwmonVerifyState = field(default_factory=HwmonVerifyState)
    final_state: HwmonVerifyState = field(default_factory=HwmonVerifyState)
    test_pwm_percent: int = 0
    wait_seconds: int = 0
    details: str = ""
    # True if the daemon's post-verify restore-to-original-PWM failed. The
    # daemon serializes this only when true (skip_serializing_if), so older
    # daemons that lack the field appear here as the default ``False``.
    restore_failed: bool = False


@dataclass
class GpuVerifyState:
    """Snapshot of GPU fan state during a verify (DEC-120). Fields are
    path-dependent: ``zero_rpm_enabled`` is set on the PMFW path,
    ``pwm_enable`` on the legacy ``pwm1`` path. ``applied_speed_pct`` is the
    read-back commanded speed (flat curve value for PMFW, ``pwm1`` percent for
    legacy)."""

    applied_speed_pct: int | None = None
    rpm: int | None = None
    pwm_enable: int | None = None
    zero_rpm_enabled: bool | None = None


@dataclass
class GpuVerifyResult:
    """Result of ``POST /gpu/{gpu_id}/fan/verify`` (DEC-120). ``result`` is one
    of: ``effective``, ``curve_not_applied``, ``no_rpm_effect``,
    ``zero_rpm_suppressed``, ``rpm_unavailable``, ``write_failed``, or
    ``pwm_enable_reverted`` (legacy path)."""

    gpu_id: str = ""
    result: str = ""
    initial_state: GpuVerifyState = field(default_factory=GpuVerifyState)
    final_state: GpuVerifyState = field(default_factory=GpuVerifyState)
    test_speed_pct: int = 0
    wait_seconds: int = 0
    fan_control_method: str = ""
    details: str = ""
    restore_failed: bool = False


@dataclass
class HardwareDiagnosticsResult:
    api_version: int = 1
    hwmon: HwmonDiagnostics = field(default_factory=HwmonDiagnostics)
    gpu: GpuDiagnosticsInfo | None = None
    # DEC-121: Intel discrete GPU diagnostics. None when no Intel GPU present
    # or the daemon predates the field.
    intel_gpu: IntelGpuDiagnosticsInfo | None = None
    # DEC-204: NVIDIA discrete GPU diagnostics. None when no NVIDIA GPU present
    # or the daemon predates the field.
    nvidia_gpu: NvidiaGpuDiagnosticsInfo | None = None
    thermal_safety: ThermalSafetyInfo = field(default_factory=ThermalSafetyInfo)
    kernel_modules: list[KernelModuleInfo] = field(default_factory=list)
    acpi_conflicts: list[AcpiConflictInfo] = field(default_factory=list)
    board: BoardInfo = field(default_factory=BoardInfo)
    # DEC-101: chip names this DMI board is expected to expose, sourced
    # from the daemon's curated dual-chip board table. Empty when the
    # board is unknown or the daemon predates DEC-101 (the field is
    # `skip_serializing_if = "Vec::is_empty"` on the wire). The
    # diagnostics page compares this against `hwmon.chips_detected[]
    # .chip_name` to render a missing-chip warning banner with the
    # dual-chip remediation steps (driver update first; `mmio=on`
    # modprobe.d line on pre-2026-03 builds — DEC-144).
    expected_chips: list[str] = field(default_factory=list)
    # DEC-101: best-effort kernel-level chip detection (parsed from
    # /dev/kmsg by the daemon). Populated when the kernel ring buffer
    # is readable; empty otherwise. Useful for surfacing the
    # "kernel found chip but driver did not bind" diagnostic; not
    # authoritative for "what works".
    kernel_detected_chips: list[str] = field(default_factory=list)
    # DEC-105: simultaneous-load collisions detected by the daemon. Empty
    # when the daemon predates DEC-105 (skip_serializing_if = "Vec::is_empty"
    # on the wire). When present, the GUI renders a CRITICAL banner and
    # discourages PWM writes until the user resolves the load ordering.
    module_collisions: list[ModuleCollisionInfo] = field(default_factory=list)
    # DEC-110: CPU vendor string from `/proc/cpuinfo` vendor_id, normalised
    # by the daemon to ``"Intel"`` / ``"AMD"`` / ``""`` (empty when unknown
    # or the daemon predates DEC-110; `skip_serializing_if = "String::is_empty"`
    # on the wire). Used by the diagnostics page to scope platform-specific
    # vendor quirks (e.g. MSI Z890 vs MSI X870E) without inferring platform
    # from board name.
    cpu_vendor: str = ""
    # DEC-119: AMD VGA-class PCI devices and their driver binding, detected
    # independently of hwmon. Lets the diagnostics page distinguish "no AMD
    # GPU" from "AMD GPU present but amdgpu not bound". Empty when no AMD VGA
    # device exists or the daemon predates the field.
    amd_pci_devices: list[AmdPciDeviceInfo] = field(default_factory=list)
    # DEC-119: whether the amdgpu kernel module is loaded. Paired with
    # amd_pci_devices to distinguish a blacklisted module from a bind failure.
    amdgpu_module_loaded: bool = False


# ---------------------------------------------------------------------------
# Hwmon inventory + readiness (Phase 4 — DEC-200)
#
# Daemon-authoritative, additive: GET /inventory/hwmon and /inventory/readiness.
# Distinct from the GUI-authored ``readiness_report`` (derived from
# /diagnostics/hardware) — these are the daemon's own structured views.
# ---------------------------------------------------------------------------


@dataclass
class InventoryTempSensor:
    """A temperature sensor from GET /inventory/hwmon, carrying the daemon's
    fine-grained classification refinement (advisory; the daemon's ``kind`` and
    thermal safety are unchanged)."""

    id: str = ""
    kind: str = ""
    label: str = ""
    value_c: float = 0.0
    source: str = ""
    chip_name: str = ""
    classification: str = ""  # cpu_tctl / cpu_package / vrm_temp / motherboard_temp / ...
    confidence: str = ""  # high | medium | low | unknown
    rationale: str = ""
    # DEC-193: default True so a pre-field daemon leaves sensors selectable.
    control_eligible: bool = True


@dataclass
class DefaultCpuSensor:
    """The daemon's default-CPU recommendation from GET /inventory/hwmon.

    ``source`` is ``"user"`` when it echoes the persisted preferred CPU sensor,
    else ``"auto"`` for the deterministic auto-pick.
    """

    sensor_id: str = ""
    confidence: str = ""
    rationale: str = ""
    source: str = "auto"


@dataclass
class InventoryPreferences:
    """The user's persisted preferred sensors, echoed on GET /inventory/hwmon.

    Each id may be stale (check the sensor list / readiness items).
    """

    cpu_sensor_id: str | None = None
    mb_sensor_id: str | None = None


@dataclass
class InventoryPwmControl:
    """One controllable PWM header from GET /inventory/hwmon ``pwm_controls``
    (mirrors the daemon's ``responses.rs::PwmHeaderEntry`` field-for-field;
    CONTR-1, 2026-07-21 audit)."""

    id: str = ""
    label: str = ""
    chip_name: str = ""
    device_id: str = ""
    pwm_index: int = 0
    supports_enable: bool = False
    rpm_available: bool = False
    min_pwm_percent: int = 0
    max_pwm_percent: int = 100
    is_writable: bool = False
    # 0 = DC, 1 = PWM; None when the pwmN_mode file is not exposed (the daemon
    # omits the key).
    pwm_mode: int | None = None
    # Daemon-authoritative AIO hint (Kraken/Aquacomputer). Always present from
    # any daemon that serves this endpoint (≥ 2.6.0, DEC-200 — later than the
    # field's 1.18.0 introduction); the False default is a forward-compat
    # safety net only.
    is_aio: bool = False
    # DEC-311: per-channel role, with the user's assignment applied. Same
    # opaque-token rule as `HwmonHeader.role` — render what you do not
    # recognise, never drop the row.
    role: str = "unknown"
    role_source: str = "none"


@dataclass
class InventoryFanInput:
    """One monitor-only fan line from GET /inventory/hwmon
    ``monitor_only_fans`` (daemon ``FanInputEntry``): a ``fanN_input``
    tachometer with no matching ``pwmN`` — visible, never controllable."""

    id: str = ""
    source: str = "hwmon"
    chip_name: str = ""
    label: str = ""
    fan_index: int = 0


@dataclass
class HwmonInventory:
    """Response from GET /inventory/hwmon: classified temp sensors, the
    controllable PWM headers, monitor-only fan tachometers, the default-CPU
    recommendation, and persisted sensor preferences."""

    api_version: int = 1
    temp_sensors: list[InventoryTempSensor] = field(default_factory=list)
    pwm_controls: list[InventoryPwmControl] = field(default_factory=list)
    # The daemon omits the key when empty (additive) → default [].
    monitor_only_fans: list[InventoryFanInput] = field(default_factory=list)
    default_cpu: DefaultCpuSensor | None = None
    preferences: InventoryPreferences | None = None


@dataclass
class ReadinessItem:
    """One structured readiness finding from GET /inventory/readiness."""

    code: str = ""
    # Conservative default: an entry missing its severity is treated as ``info``
    # (never falsely "ok"/healthy nor over-alarming as "critical"). Mirrors the
    # ``ModuleCollisionInfo.severity`` convention.
    severity: str = "info"  # ok | info | warning | critical
    component: str = ""  # cpu | pwm | hwmon | sensor
    summary: str = ""
    detail: str = ""
    recommended_action: str = ""
    can_automate: bool = False
    blocks_monitoring: bool = False
    blocks_control: bool = False
    affects_safety: bool = False
    reboot_may_be_required: bool = False


@dataclass
class InventoryReadiness:
    """Response from GET /inventory/readiness — the daemon's structured,
    read-only hardware-readiness list plus an ``overall`` rollup severity."""

    api_version: int = 1
    overall: str = "ok"  # ok | info | warning | critical
    items: list[ReadinessItem] = field(default_factory=list)


@dataclass
class PreferredSensorResult:
    """Response from POST /config/preferred-{cpu,mb}-sensor."""

    updated: bool = False
    role: str = ""  # "cpu" | "mb"
    preferred_sensor: str | None = None


@dataclass
class HeaderRoleResult:
    """Response from POST /config/header-role (DEC-311/312).

    role echoes the assignment and is None after a clear; effective_role
    is what the daemon actually resolved, which is NOT the same thing — a cleared
    header falls back to the daemon's own inference, and an assignment on a header
    whose label already says "pump" resolves to the assignment. Read
    effective_role to confirm what happened; role only says what was stored.

    effective_role is an OPAQUE TOKEN (the 273-i rule): render an unrecognised
    value rather than dropping it, and never grant it pump semantics.
    """

    updated: bool = False
    header_id: str = ""
    role: str | None = None
    effective_role: str = "unknown"


# ---------------------------------------------------------------------------
# Daemon configuration (DEC-243) — GET /config + the extended POST /config/*
#
# The daemon is authoritative for every value here. Before this endpoint the
# writable knobs were write-only, so the GUI kept a local mirror and pushed it on
# save: a fresh GUI against a daemon set to 10 s displayed 0 s. `value` is what
# the files say (what a restart would give); `running_value` is what the daemon
# process actually started with. Never conflate them — `restart_pending` is the
# daemon's own verdict and must not be re-derived GUI-side.
# ---------------------------------------------------------------------------


@dataclass
class DaemonConfigKey:
    """One configuration key as reported by GET /config."""

    key: str = ""
    value: object = None
    running_value: object = None
    source: str = "default"  # "runtime" | "admin" | "default"
    mutable: bool = False
    requires_restart: bool = False
    restart_pending: bool = False
    # Set when a config write alone cannot enable the feature — currently the two
    # [detection] opt-ins, each of which also needs a root systemd drop-in. The
    # UI must not present such a key as "on" merely because the flag is set.
    requires_privilege: str | None = None

    @property
    def running_display(self) -> str:
        """Human-readable rendering of what the daemon is actually running.

        ``running_value`` is always sent by the daemon and ``None`` means the key
        is genuinely unset (only ``serial.port`` can be null today). It must NOT
        fall back to ``value`` on ``None`` — that is precisely the bug this
        replaced, where a null running value read as "same as the file" and the
        card claimed the daemon was running a port it had never been given.
        """
        return "not set" if self.running_value is None else str(self.running_value)


@dataclass
class DaemonConfig:
    """Response from GET /config."""

    api_version: int = 0
    admin_config_path: str = ""
    runtime_config_path: str = ""
    restart_pending: bool = False
    keys: list[DaemonConfigKey] = field(default_factory=list)

    def get(self, key: str) -> DaemonConfigKey | None:
        for k in self.keys:
            if k.key == key:
                return k
        return None


@dataclass
class ConfigWriteResult:
    """Response from the DEC-243 POST /config/* routes."""

    updated: bool = False
    key: str = ""
    value: object = None
    note: str = ""
    requires_privilege: str | None = None


# ---------------------------------------------------------------------------
# Super-I/O detection (Phase 3 — DEC-202)
#
# Daemon-authoritative, additive, read-only: GET /inventory/superio. The daemon
# owns every string here (chip names, recommendations, caveats), so the view
# renders them as PlainText — never markup.
# ---------------------------------------------------------------------------


@dataclass
class SuperIoRecommendation:
    """A "load this driver" recommendation for an unbound Super-I/O chip."""

    module: str = ""
    in_mainline: bool = False
    load_hint: str = ""
    reason: str = ""
    risk_notes: list[str] = field(default_factory=list)


@dataclass
class SuperIoChip:
    """One detected Super-I/O chip from GET /inventory/superio."""

    chip_name: str = ""
    vendor: str = ""  # ite|nuvoton|winbond|smsc|national|fintek|unknown
    evidence: list[str] = field(
        default_factory=list
    )  # dmi_board_table|kernel_log|bound_hwmon|port_probe (opaque; DEC-203 added port_probe)
    confidence: str = ""  # high|medium|low|unknown
    bound_driver: str | None = None
    expected_module: str = ""
    module_loaded: bool = False
    hwmon_present: bool = False
    recommendation: SuperIoRecommendation | None = None
    caveats: list[str] = field(default_factory=list)


@dataclass
class SuperIoReport:
    """Response from GET /inventory/superio — passive Super-I/O detection.

    ``arch_supported`` is False on non-x86 daemons (with an empty ``chips``).
    Read-only: detection proves a chip is present, never that fan control works.
    """

    api_version: int = 1
    arch_supported: bool = True
    chips: list[SuperIoChip] = field(default_factory=list)
    acpi_conflict_drivers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # DEC-203: whether the opt-in active /dev/port probe can run right now, and
    # (when it can't) why. Default False/"" so an older daemon degrades safely.
    port_probe_available: bool = False
    port_probe_reason: str = ""


@dataclass
class HardwareReadiness:
    """Response from GET /inventory/hardware-readiness (DEC-207) — the combined
    readiness + Super-I/O snapshot the merged "Cooling Hardware Readiness" page
    fetches in ONE atomic request, all from a single shared daemon scan.

    Every field defaults so a pre-field daemon (or a malformed response) degrades
    safely. Daemon-authored strings (readiness items + Super-I/O) render as
    PlainText; only GUI-authored guidance is trusted rich text.
    """

    api_version: int = 1
    overall: str = "ok"  # ok | info | warning | critical
    rollup: ReadinessRollup = field(default_factory=ReadinessRollup)
    items: list[ReadinessItem] = field(default_factory=list)
    superio: SuperIoReport = field(default_factory=SuperIoReport)
    scanned_age_ms: int = 0
    generation: int = 0


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


#: Wire keys the GUI uses as an *identity* — hashed into a dict, or compared for
#: equality against a card/control id. Every one of them is declared ``str`` on
#: every dataclass that carries it, so this set is the runtime half of a
#: declaration the type annotations already make.
_ID_KEYS = frozenset(
    {"id", "control_id", "fan_id", "member_id", "curve_id", "sensor_id", "profile_id"}
)

#: Wire keys the GUI formats as a number. `output_pct` reaches an
#: ``f"{v:.0f}%"`` in `ControlCard.set_output`, which raises on a str or a list.
_FLOAT_KEYS = frozenset({"output_pct"})


def _filter_fields(cls: type, data: dict) -> dict:
    """Filter a dict to only keys that match dataclass field names, and coerce
    identity fields to ``str``.

    Filtering prevents TypeError from ``**`` unpacking when the daemon sends new
    fields that the GUI's dataclass doesn't know about yet (forward
    compatibility).

    **The coercion is register row 277-h, decided once here rather than per
    site.** The GUI hashes daemon-supplied ids in about half a dozen places
    (``skipped_controls``, ``overrides``, ``fan_identify``, ``unavailable_sensors``,
    ``services/session_stats.py``); a non-string id — ``"control_id": []`` — makes
    ``{e.control_id: ...}`` raise ``TypeError: unhashable type`` inside a slot on
    the 1 Hz poll path. 273-i patched exactly one of those comprehensions with an
    ``isinstance`` guard, which closed one door of N and made the asymmetry *read*
    as deliberate.

    Coercing rather than dropping is the deliberate choice: a malformed id
    becomes a string that cannot match any real control, so the entry is ignored
    and the surface degrades quietly, where dropping the key would substitute the
    dataclass default (``""``) and could collide with a genuinely empty id.

    Unreachable against the shipping daemon — those fields are Rust ``String`` on
    ``#[derive(Serialize)]``, so serde cannot emit anything else — and the blast
    radius was overstated in an earlier comment: measured on PySide6 6.11.1, an
    exception in a slot does **not** escape ``emit()``. This is about not leaving
    a trap that reads as intentional, not about a live crash.
    """
    known = {f.name for f in fields(cls)}
    return {k: _coerce_wire_value(k, v) for k, v in data.items() if k in known}


def _coerce_wire_value(key: str, value: object) -> object:
    """Normalise one wire field to the type its dataclass declares.

    Identity fields become ``str``; ``output_pct`` becomes ``float``. Both are a
    runtime restatement of the annotations already on those dataclasses, and both
    exist because the value flows somewhere that a wrong type breaks: an id into a
    dict key, ``output_pct`` into an ``f"{v:.0f}%"`` format.

    Covering `output_pct` alongside the ids is the point. Coercing one and not the
    other — on the *same new dataclass* — would recreate exactly the per-field
    asymmetry 277-h existed to remove, where a guard on one comprehension made the
    absence of a guard on its sibling read as deliberate.

    A value that cannot be coerced is left alone rather than defaulted: the
    dataclass default would be a plausible-looking number, where the original
    keeps the fault visible at the point it is used.
    """
    if key in _ID_KEYS and not isinstance(value, str):
        return str(value)
    if key in _FLOAT_KEYS and not isinstance(value, float):
        # `bool` is deliberately excluded. It is an `int` subclass, so `float(True)`
        # is a perfectly ordinary 1.0 — which would silently turn a malformed
        # `"output_pct": true` into a card confidently reporting 1%. Left as a
        # bool, it fails the numeric check at the parse site and the entry is
        # dropped, so it reads as "no value" instead.
        if isinstance(value, bool):
            return value
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return value
    return value


def _coalesce_pci_bdf(raw: dict) -> dict:
    """Accept both ``pci_id`` and ``pci_bdf`` input keys on the same payload.

    The daemon historically emitted ``pci_id`` in ``/capabilities`` and
    ``pci_bdf`` in ``/diagnostics/hardware`` despite both fields carrying the
    same PCI BDF address. Daemon is transitioning to emit both names on both
    endpoints with the legacy name deprecated (M11). Normalising here lets
    GUI code use either dataclass field indiscriminately during the
    transition.
    """
    if not isinstance(raw, dict):
        return raw
    result = dict(raw)
    if "pci_bdf" in result and "pci_id" not in result:
        result["pci_id"] = result["pci_bdf"]
    elif "pci_id" in result and "pci_bdf" not in result:
        result["pci_bdf"] = result["pci_id"]
    return result


def parse_capabilities(data: dict) -> Capabilities:
    devices = data.get("devices", {})
    features = data.get("features", {})

    # DEC-098: kernel_warnings is a list of dicts on the wire; the
    # `_filter_fields` helper would drop it if it landed here as a list of
    # dicts (the dataclass-from-kwargs pattern can't construct nested
    # dataclasses). Hand-parse it so each entry becomes a `KernelWarning`.
    amd_gpu_raw = _coalesce_pci_bdf(devices.get("amd_gpu", {}))
    kernel_warnings_raw = amd_gpu_raw.pop("kernel_warnings", []) or []
    kernel_warnings = [
        KernelWarning(**_filter_fields(KernelWarning, kw))
        for kw in kernel_warnings_raw
        if isinstance(kw, dict)
    ]
    amd_gpu = AmdGpuCapability(**_filter_fields(AmdGpuCapability, amd_gpu_raw))
    amd_gpu.kernel_warnings = kernel_warnings

    # DEC-121: Intel discrete GPU — additive, read-only. No nested lists to
    # hand-parse; `_coalesce_pci_bdf` normalises pci_bdf↔pci_id like amd_gpu.
    intel_gpu = IntelGpuCapability(
        **_filter_fields(IntelGpuCapability, _coalesce_pci_bdf(devices.get("intel_gpu", {})))
    )
    # DEC-204: NVIDIA discrete GPU capability (additive, read-only). Same
    # tolerance as intel_gpu — absent key / old daemon → present=False.
    nvidia_gpu = NvidiaGpuCapability(
        **_filter_fields(NvidiaGpuCapability, _coalesce_pci_bdf(devices.get("nvidia_gpu", {})))
    )

    return Capabilities(
        api_version=data.get("api_version", 1),
        daemon_version=data.get("daemon_version", ""),
        ipc_transport=data.get("ipc_transport", ""),
        openfan=OpenfanCapability(**_filter_fields(OpenfanCapability, devices.get("openfan", {}))),
        hwmon=HwmonCapability(**_filter_fields(HwmonCapability, devices.get("hwmon", {}))),
        amd_gpu=amd_gpu,
        intel_gpu=intel_gpu,
        nvidia_gpu=nvidia_gpu,
        aio_hwmon=AioHwmonCapability(
            **_filter_fields(AioHwmonCapability, devices.get("aio_hwmon", {}))
        ),
        aio_usb=UnsupportedCapability(
            **_filter_fields(UnsupportedCapability, devices.get("aio_usb", {}))
        ),
        features=FeatureFlags(**_filter_fields(FeatureFlags, features)),
        # DEC-160: top-level ``control`` block; absent on pre-1.19 daemons →
        # all-default (profile_storage=False), which disables the import offer.
        control=ControlCapability(**_filter_fields(ControlCapability, data.get("control", {}))),
    )


def parse_status(data: dict) -> DaemonStatus:
    return DaemonStatus(
        api_version=data.get("api_version", 1),
        daemon_version=data.get("daemon_version", ""),
        overall_status=data.get("overall_status", "unknown"),
        subsystems=[
            SubsystemStatus(**_filter_fields(SubsystemStatus, s))
            for s in data.get("subsystems", [])
            if isinstance(s, dict)
        ],
        uptime_seconds=data.get("uptime_seconds"),
        # DEC-132: absent on pre-1.13 daemons — treat as "normal".
        thermal_state=data.get("thermal_state", "normal"),
        # DEC-163/166/169: omitted from the wire when empty — default to [].
        overrides=[
            OverrideStatusEntry(**_filter_fields(OverrideStatusEntry, e))
            for e in data.get("overrides", [])
            if isinstance(e, dict)
        ],
        fan_identify=[
            IdentifyStatusEntry(**_filter_fields(IdentifyStatusEntry, e))
            for e in data.get("fan_identify", [])
            if isinstance(e, dict)
        ],
        # DEC-193: omitted from the wire when empty — default to [].
        unavailable_sensors=[
            UnavailableSensor(**_filter_fields(UnavailableSensor, e))
            for e in data.get("unavailable_sensors", [])
            if isinstance(e, dict)
        ],
        # 273-i: omitted when empty, and absent entirely before daemon 2.21.0 —
        # default to [] either way, so an older daemon simply reports nothing
        # skipped rather than the GUI having to know its version.
        skipped_controls=[
            SkippedControl(**_filter_fields(SkippedControl, e))
            for e in data.get("skipped_controls", [])
            if isinstance(e, dict)
        ],
        # 277-k: omitted when empty, and absent entirely before daemon 2.22.0 —
        # default to [] either way, so an older daemon simply reports no live
        # output and the cards keep showing "—", exactly as they did before the
        # field existed.
        control_outputs=[
            entry
            for entry in (
                ControlOutput(**_filter_fields(ControlOutput, e))
                for e in data.get("control_outputs", [])
                if isinstance(e, dict)
            )
            # An output that will not coerce to a number is an output we do not
            # know, so drop the entry and let it read as ABSENT — which the
            # contract already defines as "—". Keeping it would carry a value
            # that raises inside `f"{v:.0f}%"` on the 1 Hz slot, and defaulting it
            # to 0.0 would invent a duty the daemon never reported.
            if isinstance(entry.output_pct, (int, float)) and not isinstance(entry.output_pct, bool)
        ],
        # AIO-MB Phase 5: omitted when no session has ever run, and absent
        # entirely before daemon 2.32.0 — `None` either way.
        validation_session=parse_validation_session_summary(data),
        # DEC-194: absent key (older daemon, or no active profile) → None, so the
        # polling fast-path leaves the /profile/active fallback authoritative. A
        # present value updates the active profile every poll.
        active_profile_id=data.get("active_profile_id"),
        active_profile_name=data.get("active_profile_name"),
        # DEC-206: the compact readiness rollup for the Dashboard chip. Absent
        # key (old daemon / pre-seed) or a malformed object → None ⇒ chip hidden.
        readiness=_parse_readiness_rollup(data.get("readiness")),
        # DEC-321 / `WIRE-a`: absent when the config loaded cleanly and on every
        # daemon before 2.34.0 → None, which the Dashboard reads as "fine".
        runtime_config_degraded=_parse_runtime_config_degraded(data.get("runtime_config_degraded")),
    )


def _parse_readiness_rollup(raw: object) -> ReadinessRollup | None:
    """Parse the DEC-206 ``readiness`` rollup object, or ``None`` when absent or
    malformed (old daemon / pre-seed) so the Dashboard chip stays hidden.

    Defensive like the sibling status parsers: a non-dict (including the common
    absent-key ``None``) yields ``None``; unknown fields are dropped so a newer
    daemon that extends the rollup cannot break an older GUI.
    """
    if not isinstance(raw, dict):
        return None
    return ReadinessRollup(**_filter_fields(ReadinessRollup, raw))


def _parse_runtime_config_degraded(raw: object) -> RuntimeConfigDegraded | None:
    """Parse the DEC-321 ``runtime_config_degraded`` object, or ``None`` when it
    is absent or malformed.

    Defensive like the sibling status parsers: a non-dict (including the common
    absent-key ``None``) yields ``None``; unknown fields are dropped so a newer
    daemon that extends the object cannot break an older GUI.
    """
    if not isinstance(raw, dict):
        return None
    return RuntimeConfigDegraded(**_filter_fields(RuntimeConfigDegraded, raw))


def parse_sensors(data: dict) -> list[SensorReading]:
    sensors = data.get("sensors", [])
    if not isinstance(sensors, list):
        # Preserve the pre-DEC-117 contract: a non-list ``sensors`` field is
        # a malformed daemon payload, not "no sensors". The polling worker
        # wraps this in DaemonError handling so a clear error surfaces to
        # the user rather than an empty list. Tests pin this behaviour.
        raise TypeError(f"expected 'sensors' to be a list, got {type(sensors).__name__}")
    return [_parse_sensor_reading(s) for s in sensors]


def _parse_sensor_reading(raw: dict) -> SensorReading:
    """Parse a single ``SensorEntry`` JSON payload into a ``SensorReading``.

    Handles the DEC-117 nested ``thresholds`` object: the dict-comprehension
    ``_filter_fields`` pattern can't construct a nested dataclass on its own,
    so we hand-parse the threshold sub-payload and inject the result.
    """
    if not isinstance(raw, dict):
        return SensorReading()
    fields_only = _filter_fields(SensorReading, raw)
    thresholds_raw = fields_only.pop("thresholds", None)
    thresholds: SensorThresholds | None = None
    if isinstance(thresholds_raw, dict):
        thresholds = SensorThresholds(**_filter_fields(SensorThresholds, thresholds_raw))
        if thresholds.is_empty():
            thresholds = None
    return SensorReading(thresholds=thresholds, **fields_only)


def parse_fans(data: dict) -> list[FanReading]:
    fans = data.get("fans", [])
    if not isinstance(fans, list):
        # Mirror parse_sensors: a non-list ``fans`` field is a malformed daemon
        # payload, not "no fans". The polling worker wraps this in DaemonError
        # handling so a clear error surfaces rather than an empty fan table.
        raise TypeError(f"expected 'fans' to be a list, got {type(fans).__name__}")
    # Per-element guard mirrors parse_sensors' _parse_sensor_reading: a non-dict
    # element (malformed daemon payload) is skipped, not crashed on — without this
    # `_filter_fields(FanReading, non_dict)` raises AttributeError, which the poll
    # worker's except clause does not catch, freezing the fan table on every poll.
    return [FanReading(**_filter_fields(FanReading, s)) for s in fans if isinstance(s, dict)]


def parse_hwmon_headers(data: dict) -> list[HwmonHeader]:
    return [HwmonHeader(**_filter_fields(HwmonHeader, h)) for h in data.get("headers", [])]


def parse_cooling_devices(data: dict) -> CoolingDeviceInventory:
    """Parse ``GET /inventory/cooling-devices`` (DEC-316).

    A pre-2.31 daemon 404s the route, so the caller gates on
    ``capabilities.control.cooling_devices``; this parser assumes a 200 body.
    """
    devices = data.get("cooling_devices", [])
    policies = data.get("available_policies", [])
    return CoolingDeviceInventory(
        cooling_devices=[_cooling_device_from(d) for d in devices if isinstance(d, dict)],
        available_policies=[
            DevicePolicySummary(**_filter_fields(DevicePolicySummary, p))
            for p in policies
            if isinstance(p, dict)
        ],
    )


def _cooling_device_from(d: dict) -> CoolingDevice:
    dev = CoolingDevice(**_filter_fields(CoolingDevice, d))
    policy = d.get("device_policy")
    if isinstance(policy, dict):
        dev.device_policy = DevicePolicySummary(**_filter_fields(DevicePolicySummary, policy))
    return dev


def parse_override_grant(data: dict) -> OverrideGrant:
    return OverrideGrant(**_filter_fields(OverrideGrant, data))


def parse_override_renew(data: dict) -> OverrideRenewResult:
    return OverrideRenewResult(**_filter_fields(OverrideRenewResult, data))


def parse_override_release(data: dict) -> OverrideReleaseResult:
    return OverrideReleaseResult(**_filter_fields(OverrideReleaseResult, data))


def parse_identify_result(data: dict) -> IdentifyResult:
    return IdentifyResult(**_filter_fields(IdentifyResult, data))


def parse_field_violations(details: object) -> list[FieldViolation]:
    """Extract ``field_violations`` from a ``DaemonError.details`` payload (DEC-160).

    Returns an empty list when ``details`` is not the validation-error shape, so
    callers can render violations uniformly without shape-checking.

    Field values are coerced to ``str``. ``_filter_fields`` screens KEYS only, so
    a daemon answering ``{"field": 1, "reason": null}`` used to build a
    ``FieldViolation`` holding an ``int`` and a ``None``, and the only consumer —
    :func:`control_ofc.services.profile_import_service` — calls ``.strip()`` on
    both, raising ``AttributeError`` from inside a per-profile failure handler
    documented never to raise, which aborted the whole batch import. The envelope
    is not shape-enforced at parse time by design (see
    :class:`control_ofc.api.errors.DaemonError`), so degrading has to happen
    here. ``None`` becomes ``""`` rather than ``"None"``: an absent reason must
    render as absent, not as the word.
    """
    if not isinstance(details, dict):
        return []
    raw = details.get("field_violations", [])
    if not isinstance(raw, list):
        return []

    def _text(value: object) -> str:
        return "" if value is None else str(value)

    return [
        FieldViolation(**{k: _text(v) for k, v in _filter_fields(FieldViolation, item).items()})
        for item in raw
        if isinstance(item, dict)
    ]


def parse_calibration_result(data: dict) -> CalibrationResult:
    return CalibrationResult(
        fan_id=data.get("fan_id", ""),
        points=[CalPoint(**_filter_fields(CalPoint, p)) for p in data.get("points", [])],
        start_pwm=data.get("start_pwm"),
        stop_pwm=data.get("stop_pwm"),
        min_rpm=data.get("min_rpm", 0),
        max_rpm=data.get("max_rpm", 0),
    )


def parse_sensor_history(data: dict) -> SensorHistory:
    return SensorHistory(
        entity_id=data.get("entity_id", ""),
        points=[HistoryPoint(**_filter_fields(HistoryPoint, p)) for p in data.get("points", [])],
    )


def parse_profile_activate(data: dict) -> ProfileActivateResult:
    return ProfileActivateResult(
        activated=data.get("activated", False),
        profile_id=data.get("profile_id", ""),
        profile_name=data.get("profile_name", ""),
    )


def parse_profile_deactivate(data: dict) -> ProfileDeactivateResult:
    return ProfileDeactivateResult(
        deactivated=bool(data.get("deactivated", False)),
        previous_profile_id=data.get("previous_profile_id"),
        previous_profile_name=data.get("previous_profile_name"),
    )


def parse_active_profile(data: dict) -> ActiveProfileInfo | None:
    if not data.get("active", False):
        return None
    return ActiveProfileInfo(
        active=True,
        profile_id=data.get("profile_id", ""),
        profile_name=data.get("profile_name", ""),
    )


def parse_gpu_fan_reset(data: dict) -> GpuFanResetResult:
    return GpuFanResetResult(gpu_id=data.get("gpu_id", ""), reset=data.get("reset", False))


def parse_profile_search_dirs(data: dict) -> ProfileSearchDirsResult:
    return ProfileSearchDirsResult(
        updated=data.get("updated", False),
        search_dirs=data.get("search_dirs", []),
    )


def parse_hardware_diagnostics(data: dict) -> HardwareDiagnosticsResult:
    hwmon_raw = data.get("hwmon", {})
    hwmon = HwmonDiagnostics(
        chips_detected=[
            HwmonChipInfo(**_filter_fields(HwmonChipInfo, c))
            for c in hwmon_raw.get("chips_detected", [])
        ],
        total_headers=hwmon_raw.get("total_headers", 0),
        writable_headers=hwmon_raw.get("writable_headers", 0),
        enable_revert_counts=hwmon_raw.get("enable_revert_counts", {}),
    )

    gpu_raw = data.get("gpu")
    gpu = None
    if isinstance(gpu_raw, dict) and gpu_raw:
        gpu_norm = _coalesce_pci_bdf(gpu_raw)
        # DEC-119: kernel_warnings is a list of dicts on the wire — pop it
        # before `**`-unpacking so it doesn't land as raw dicts, then
        # hand-parse into KernelWarning (mirrors parse_capabilities).
        gpu_kw_raw = gpu_norm.pop("kernel_warnings", []) or []
        gpu = GpuDiagnosticsInfo(**_filter_fields(GpuDiagnosticsInfo, gpu_norm))
        gpu.kernel_warnings = [
            KernelWarning(**_filter_fields(KernelWarning, kw))
            for kw in gpu_kw_raw
            if isinstance(kw, dict)
        ]

    # DEC-121: Intel discrete GPU diagnostics (additive, read-only).
    intel_gpu_raw = data.get("intel_gpu")
    intel_gpu = None
    if isinstance(intel_gpu_raw, dict) and intel_gpu_raw:
        intel_gpu = IntelGpuDiagnosticsInfo(
            **_filter_fields(IntelGpuDiagnosticsInfo, _coalesce_pci_bdf(intel_gpu_raw))
        )

    # DEC-204: NVIDIA discrete GPU diagnostics (additive, read-only).
    nvidia_gpu_raw = data.get("nvidia_gpu")
    nvidia_gpu = None
    if isinstance(nvidia_gpu_raw, dict) and nvidia_gpu_raw:
        nvidia_gpu = NvidiaGpuDiagnosticsInfo(
            **_filter_fields(NvidiaGpuDiagnosticsInfo, _coalesce_pci_bdf(nvidia_gpu_raw))
        )

    thermal_raw = data.get("thermal_safety", {})
    thermal = ThermalSafetyInfo(**_filter_fields(ThermalSafetyInfo, thermal_raw))

    board_raw = data.get("board", {})
    board = BoardInfo(**_filter_fields(BoardInfo, board_raw))

    # DEC-101: dual-chip detection fields — daemon emits them only when
    # non-empty (skip_serializing_if = "Vec::is_empty"), so older daemons
    # that predate the field send no key and we default to []. The list
    # comprehensions also coerce non-string entries to strings as a
    # defensive measure against future shape drift.
    expected_chips_raw = data.get("expected_chips") or []
    expected_chips = [str(c) for c in expected_chips_raw if c]
    kernel_detected_chips_raw = data.get("kernel_detected_chips") or []
    kernel_detected_chips = [str(c) for c in kernel_detected_chips_raw if c]

    # DEC-105: module-collision pairs. Same wire convention — daemons
    # without DEC-105 omit the key, so default to []. Only accept dict
    # entries to avoid `**` unpack failures if the field is present but
    # malformed.
    module_collisions_raw = data.get("module_collisions") or []
    module_collisions = [
        ModuleCollisionInfo(**_filter_fields(ModuleCollisionInfo, mc))
        for mc in module_collisions_raw
        if isinstance(mc, dict)
    ]

    # DEC-119: AMD PCI driver-bound scan. Same wire convention — omitted when
    # empty, so older daemons default to []. Only dict entries are accepted.
    amd_pci_devices_raw = data.get("amd_pci_devices") or []
    amd_pci_devices = [
        AmdPciDeviceInfo(**_filter_fields(AmdPciDeviceInfo, d))
        for d in amd_pci_devices_raw
        if isinstance(d, dict)
    ]

    return HardwareDiagnosticsResult(
        api_version=data.get("api_version", 1),
        hwmon=hwmon,
        gpu=gpu,
        intel_gpu=intel_gpu,
        nvidia_gpu=nvidia_gpu,
        thermal_safety=thermal,
        kernel_modules=[
            KernelModuleInfo(**_filter_fields(KernelModuleInfo, m))
            for m in data.get("kernel_modules", [])
        ],
        acpi_conflicts=[
            AcpiConflictInfo(**_filter_fields(AcpiConflictInfo, c))
            for c in data.get("acpi_conflicts", [])
        ],
        board=board,
        expected_chips=expected_chips,
        kernel_detected_chips=kernel_detected_chips,
        module_collisions=module_collisions,
        cpu_vendor=str(data.get("cpu_vendor") or ""),
        amd_pci_devices=amd_pci_devices,
        amdgpu_module_loaded=bool(data.get("amdgpu_module_loaded", False)),
    )


def parse_hwmon_inventory(data: dict) -> HwmonInventory:
    temp_sensors = [
        InventoryTempSensor(**_filter_fields(InventoryTempSensor, s))
        for s in data.get("temp_sensors", [])
        if isinstance(s, dict)
    ]
    pwm_controls = [
        InventoryPwmControl(**_filter_fields(InventoryPwmControl, p))
        for p in data.get("pwm_controls", [])
        if isinstance(p, dict)
    ]
    monitor_only_fans = [
        InventoryFanInput(**_filter_fields(InventoryFanInput, f))
        for f in data.get("monitor_only_fans", [])
        if isinstance(f, dict)
    ]
    default_cpu = None
    dc_raw = data.get("default_cpu")
    if isinstance(dc_raw, dict) and dc_raw:
        default_cpu = DefaultCpuSensor(**_filter_fields(DefaultCpuSensor, dc_raw))
    preferences = None
    pref_raw = data.get("preferences")
    if isinstance(pref_raw, dict) and pref_raw:
        preferences = InventoryPreferences(**_filter_fields(InventoryPreferences, pref_raw))
    return HwmonInventory(
        api_version=data.get("api_version", 1),
        temp_sensors=temp_sensors,
        pwm_controls=pwm_controls,
        monitor_only_fans=monitor_only_fans,
        default_cpu=default_cpu,
        preferences=preferences,
    )


# NOTE: contract-coverage scaffolding. GET /inventory/readiness remains valid
# daemon API (DEC-200); the GUI's only runtime consumer migrated to the merged
# /inventory/hardware-readiness snapshot (DEC-207) and its client method was
# removed in the 2026-07-21 sweep. The parser + InventoryReadiness model stay
# (with tests) per the wire-mirroring policy, ready for any future consumer.
def parse_inventory_readiness(data: dict) -> InventoryReadiness:
    return InventoryReadiness(
        api_version=data.get("api_version", 1),
        overall=str(data.get("overall", "ok")),
        items=[
            ReadinessItem(**_filter_fields(ReadinessItem, i))
            for i in data.get("items", [])
            if isinstance(i, dict)
        ],
    )


def parse_header_role(data: dict) -> HeaderRoleResult:
    """Parse POST /config/header-role. role stays None when the daemon
    reports a cleared assignment; it is never coerced to a string."""
    raw_role = data.get("role")
    return HeaderRoleResult(
        updated=bool(data.get("updated", False)),
        header_id=str(data.get("header_id", "")),
        role=raw_role if isinstance(raw_role, str) else None,
        effective_role=str(data.get("effective_role", "unknown")),
    )


def parse_preferred_sensor(data: dict) -> PreferredSensorResult:
    return PreferredSensorResult(
        updated=bool(data.get("updated", False)),
        role=str(data.get("role", "")),
        preferred_sensor=data.get("preferred_sensor"),
    )


def parse_daemon_config(data: dict) -> DaemonConfig:
    """Parse GET /config (DEC-243). Tolerant: unknown keys are ignored and a
    malformed entry is dropped rather than failing the whole read."""
    keys: list[DaemonConfigKey] = []
    for raw in data.get("keys", []):
        if not isinstance(raw, dict) or not isinstance(raw.get("key"), str):
            continue
        keys.append(DaemonConfigKey(**_filter_fields(DaemonConfigKey, raw)))
    return DaemonConfig(
        api_version=int(data.get("api_version", 0) or 0),
        admin_config_path=str(data.get("admin_config_path", "")),
        runtime_config_path=str(data.get("runtime_config_path", "")),
        restart_pending=bool(data.get("restart_pending", False)),
        keys=keys,
    )


def parse_config_write(data: dict) -> ConfigWriteResult:
    return ConfigWriteResult(
        updated=bool(data.get("updated", False)),
        key=str(data.get("key", "")),
        value=data.get("value"),
        note=str(data.get("note", "")),
        requires_privilege=data.get("requires_privilege"),
    )


def parse_superio_report(data: dict) -> SuperIoReport:
    chips: list[SuperIoChip] = []
    for c in data.get("chips", []):
        if not isinstance(c, dict):
            continue
        rec = None
        rec_raw = c.get("recommendation")
        if isinstance(rec_raw, dict) and rec_raw:
            rec = SuperIoRecommendation(**_filter_fields(SuperIoRecommendation, rec_raw))
        # Parse the nested recommendation explicitly; keep the rest via _filter_fields.
        chip_fields = {
            k: v for k, v in _filter_fields(SuperIoChip, c).items() if k != "recommendation"
        }
        chips.append(SuperIoChip(**chip_fields, recommendation=rec))
    return SuperIoReport(
        # Safe default: an absent support-flag reads as unsupported (AIP-180,
        # matching capability flags). The daemon always emits it, so this only
        # guards a malformed in-range response from falsely rendering the panel.
        api_version=data.get("api_version", 1),
        arch_supported=bool(data.get("arch_supported", False)),
        chips=chips,
        acpi_conflict_drivers=[
            str(d) for d in data.get("acpi_conflict_drivers", []) if isinstance(d, str)
        ],
        notes=[str(n) for n in data.get("notes", []) if isinstance(n, str)],
        port_probe_available=bool(data.get("port_probe_available", False)),
        port_probe_reason=str(data.get("port_probe_reason", "")),
    )


def parse_hardware_readiness(data: dict) -> HardwareReadiness:
    """Parse GET /inventory/hardware-readiness (DEC-207), reusing the readiness +
    Super-I/O parsers so the nested shapes stay identical to their standalone
    endpoints. Non-dict/absent nested objects degrade to empty defaults."""
    rollup_raw = data.get("rollup")
    rollup = (
        ReadinessRollup(**_filter_fields(ReadinessRollup, rollup_raw))
        if isinstance(rollup_raw, dict)
        else ReadinessRollup()
    )
    superio_raw = data.get("superio")
    superio = (
        parse_superio_report(superio_raw) if isinstance(superio_raw, dict) else SuperIoReport()
    )

    def _int(v: object) -> int:
        return v if isinstance(v, int) and not isinstance(v, bool) else 0

    return HardwareReadiness(
        api_version=data.get("api_version", 1),
        overall=str(data.get("overall", "ok")),
        rollup=rollup,
        items=[
            ReadinessItem(**_filter_fields(ReadinessItem, i))
            for i in data.get("items", [])
            if isinstance(i, dict)
        ],
        superio=superio,
        scanned_age_ms=_int(data.get("scanned_age_ms")),
        generation=_int(data.get("generation")),
    )


@dataclass
class CharPoint:
    """One measured point of a PWM/RPM characterisation sweep (AIO-MB Phase 3).

    The three axes stay separate — ``command_accepted`` (did the write land),
    ``readback_verdict`` (did the header report the duty back), and
    ``rpm_verdict`` (did the fan physically respond). ``AIO-Phase3.md`` is
    explicit that collapsing them into one pass/fail is a defect: a pump whose
    firmware overrides PWM during startup reports a correct readback with RPM
    pinned high, and calling that a write failure is the wrong conclusion.

    ``readback_verdict`` and ``rpm_verdict`` are **opaque tokens**. Render an
    unrecognised value rather than dropping the point (the 273-i rule) — a newer
    daemon may add one, and a dropped row is a silently shortened sweep.
    """

    requested_pct: int = 0
    command_accepted: bool = False
    readback_pct: int | None = None
    readback_raw: int | None = None
    pwm_enable: int | None = None
    rpm_before: int | None = None
    rpm_after: int | None = None
    settle_ms: int = 0
    first_change_ms: int | None = None
    readback_verdict: str = ""
    rpm_verdict: str = ""


@dataclass
class CharSummary:
    """Derived diagnostics over a whole characterisation sweep.

    ``possible_device_override`` is NOT a fault verdict — it means PWM was
    accepted and read back correctly while RPM never moved, which is exactly what
    a pump in its startup/self-bleeding period looks like. The UI must say so
    rather than reporting a broken fan.
    """

    command_acceptance: str = ""
    pwm_readback: str = ""
    rpm_response: str = ""
    min_tested_pct: int | None = None
    max_tested_pct: int | None = None
    min_rpm: int | None = None
    max_rpm: int | None = None
    monotonic: bool | None = None
    dead_zone_upper_pct: int | None = None
    clamp_pct: int | None = None
    possible_device_override: bool = False
    interference_detected: bool = False


@dataclass
class CharacterizationRun:
    """A characterisation run — the body of ``GET /diagnostics/characterization``
    and of the ``202`` from ``POST /hwmon/{id}/characterize``.

    ``state`` is an opaque token (``running``/``complete``/``cancelled``/
    ``aborted``/``failed`` today). ``len(points)`` against
    ``len(requested_points_pct)`` is the progress indicator; ``summary`` is
    ``None`` until the run reaches a terminal state.
    """

    run_id: str = ""
    header_id: str = ""
    state: str = ""
    requested_points_pct: list[int] = field(default_factory=list)
    settle_seconds: int = 0
    points: list[CharPoint] = field(default_factory=list)
    summary: CharSummary | None = None
    original_pct: int | None = None
    #: The header was NOT put back where the sweep found it — a failed restore
    #: write, a deliberately skipped one, or an unreadable pre-sweep duty.
    #: ``restore_outcome`` says which; daemon < 2.30.0 reported ``False`` on the
    #: three non-write exits, so an older daemon under-reports rather than lies
    #: in a new way.
    restore_failed: bool = False
    #: Stable token: ``pending`` | ``restored`` | ``write_failed`` |
    #: ``skipped_shutting_down`` | ``skipped_thermal_force`` |
    #: ``no_original_duty``. Empty from a daemon that predates it. The client
    #: owns the wording and must render an unrecognised token (273-i).
    restore_outcome: str = ""
    detail: str | None = None

    @property
    def is_running(self) -> bool:
        return self.state == "running"


def parse_characterization_run(data: dict) -> CharacterizationRun:
    """Parse a characterisation run, tolerating unknown tokens and new fields."""
    raw_points = data.get("points") or []
    points = [CharPoint(**_filter_fields(CharPoint, p)) for p in raw_points if isinstance(p, dict)]
    raw_summary = data.get("summary")
    summary = (
        CharSummary(**_filter_fields(CharSummary, raw_summary))
        if isinstance(raw_summary, dict)
        else None
    )
    requested = data.get("requested_points_pct") or []
    return CharacterizationRun(
        run_id=str(data.get("run_id", "")),
        header_id=str(data.get("header_id", "")),
        state=str(data.get("state", "")),
        requested_points_pct=[int(p) for p in requested if isinstance(p, (int, float))],
        settle_seconds=int(data.get("settle_seconds") or 0),
        points=points,
        summary=summary,
        original_pct=data.get("original_pct"),
        restore_failed=bool(data.get("restore_failed", False)),
        restore_outcome=str(data.get("restore_outcome", "")),
        detail=data.get("detail"),
    )


def parse_hwmon_verify_result(data: dict) -> HwmonVerifyResult:
    def _parse_state(raw: dict) -> HwmonVerifyState:
        return HwmonVerifyState(**_filter_fields(HwmonVerifyState, raw))

    initial_raw = data.get("initial_state", {})
    final_raw = data.get("final_state", {})
    return HwmonVerifyResult(
        header_id=data.get("header_id", ""),
        result=data.get("result", ""),
        initial_state=_parse_state(initial_raw),
        final_state=_parse_state(final_raw),
        test_pwm_percent=data.get("test_pwm_percent", 0),
        wait_seconds=data.get("wait_seconds", 0),
        details=data.get("details", ""),
        restore_failed=bool(data.get("restore_failed", False)),
    )


def parse_gpu_verify_result(data: dict) -> GpuVerifyResult:
    def _parse_state(raw: dict) -> GpuVerifyState:
        return GpuVerifyState(**_filter_fields(GpuVerifyState, raw or {}))

    return GpuVerifyResult(
        gpu_id=data.get("gpu_id", ""),
        result=data.get("result", ""),
        initial_state=_parse_state(data.get("initial_state") or {}),
        final_state=_parse_state(data.get("final_state") or {}),
        test_speed_pct=data.get("test_speed_pct", 0),
        wait_seconds=data.get("wait_seconds", 0),
        fan_control_method=data.get("fan_control_method", ""),
        details=data.get("details", ""),
        restore_failed=bool(data.get("restore_failed", False)),
    )


# ---------------------------------------------------------------------------
# Validation sessions (AIO-MB Phase 5, daemon >= 2.32.0)
# ---------------------------------------------------------------------------
#
# A validation session records what an already-configured cooler actually did,
# and may orchestrate the existing PWM verify and characterisation to produce
# evidence about it. Phase 5 shipped the model and the serializers; **Phase 6
# (DEC-318) added every pixel**, in `ui/widgets/validation_session_dialog.py`.
# There is still deliberately no page, dialog or button in THIS module.
#
# Two rules run through all of it, and both are the daemon's semantics rather
# than the GUI's to reinterpret:
#
#   * The daemon owns result meaning. A finding arrives pre-decided; the GUI
#     renders wording for a token and never recalculates PASS/FAIL/override.
#   * Every enum-ish value is an opaque token. An unrecognised one is RENDERED
#     humanised, never dropped (273-i) — a newer daemon may add one.

# Session lifecycle tokens.
VALIDATION_STATE_IDLE = "idle"
VALIDATION_STATE_RECORDING = "recording"
VALIDATION_STATE_COMPLETED = "completed"
VALIDATION_STATE_CANCELLED = "cancelled"
VALIDATION_STATE_INTERRUPTED = "interrupted"
VALIDATION_STATE_ERROR = "error"

# Result tokens. `UNAVAILABLE` is NOT a failure — the hardware simply does not
# expose what the finding would need — and absence of a diagnostic is
# `NOT_TESTED`, never `PASS`.
VALIDATION_RESULT_PASS = "pass"
VALIDATION_RESULT_FAIL = "fail"
VALIDATION_RESULT_OBSERVED = "observed"
VALIDATION_RESULT_NOT_OBSERVED = "not_observed"
VALIDATION_RESULT_NOT_TESTED = "not_tested"
VALIDATION_RESULT_UNKNOWN = "unknown"
VALIDATION_RESULT_UNAVAILABLE = "unavailable"
VALIDATION_RESULT_INTERRUPTED = "interrupted"

# Orchestratable diagnostics.
VALIDATION_DIAG_CHARACTERIZATION = "pwm_characterization"
VALIDATION_DIAG_VERIFY = "pwm_verify"

# Session kinds.
VALIDATION_KIND_VALIDATION = "validation"
VALIDATION_KIND_LIFECYCLE = "lifecycle"


@dataclass
class ValidationMemberRole:
    """A member's role and safety posture, snapshotted at session start."""

    member_id: str = ""
    label: str = ""
    # The DISPLAY role. For any safety or truthfulness decision use
    # ``pump_protected`` below, which is the daemon's own union predicate
    # (DEC-312) — a user may assign `chassis_fan` to a header the hardware
    # labels `PUMP`, and the display role then lies about what the daemon does.
    role: str = "unknown"
    # "pump" | "radiator" | "auxiliary" — this member's place in the device.
    member_kind: str = ""
    pump_protected: bool = False
    effective_min_pwm_pct: int | None = None
    stop_permitted: bool | None = None
    writable: bool = False


@dataclass
class ValidationDevicePolicy:
    """The compiled-in device policy in force for a session."""

    id: str = ""
    display_name: str = ""
    minimum_safe_pwm_pct: float = 0.0
    supports_stop: bool = False
    startup_override_seconds: int | None = None
    expected_rpm_min: int | None = None
    expected_rpm_max: int | None = None
    internal_control_possible: bool = False


@dataclass
class ValidationMetadata:
    """Everything fixed at session start — topology, roles, policy, profile."""

    cooling_device_id: str = ""
    device_name: str = ""
    device_kind: str = "unknown"
    pump_member: str | None = None
    radiator_members: list[str] = field(default_factory=list)
    auxiliary_members: list[str] = field(default_factory=list)
    temperature_sensor: str | None = None
    coolant_sensor: str | None = None
    # "available" | "unavailable". Unavailable is the NORMAL case for a
    # motherboard-connected AIO and is never an error.
    coolant_telemetry: str = "unavailable"
    device_policy: ValidationDevicePolicy = field(default_factory=ValidationDevicePolicy)
    members: list[ValidationMemberRole] = field(default_factory=list)
    active_profile_id: str | None = None
    active_profile_name: str | None = None
    daemon_version: str = ""
    # User/test metadata. Metadata only — it reaches no safety decision, and the
    # daemon does not claim to have detected any of it electronically.
    user_metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class ValidationMemberSample:
    """One member's telemetry at one instant.

    ``requested_pct`` and ``readback_pct`` are separate on purpose and must never
    be conflated: the first is what the daemon commanded, the second what the
    hardware reports. A device-side override is exactly the case where they agree
    with each other and disagree with the RPM.
    """

    member_id: str = ""
    role: str = ""
    requested_pct: int | None = None
    readback_pct: int | None = None
    rpm: int | None = None
    pwm_enable_mode: int | None = None
    alarm: bool | None = None
    enable_revert_count: int = 0
    # "daemon" | "external" | "unknown"
    ownership: str = "unknown"


@dataclass
class ValidationSample:
    elapsed_ms: int = 0
    unix_ms: int = 0
    temperature_c: float | None = None
    temperature_sensor: str | None = None
    coolant_c: float | None = None
    thermal_state: str = "normal"
    members: list[ValidationMemberSample] = field(default_factory=list)


@dataclass
class ValidationEvent:
    """A point on the session timeline. Phase 6 places these as chart markers."""

    elapsed_ms: int = 0
    unix_ms: int = 0
    kind: str = ""
    detail: str | None = None
    member_id: str | None = None


@dataclass
class ValidationVerifyEvidence:
    header_id: str = ""
    write_ok: bool = False
    readback_pct: int | None = None
    requested_pct: int | None = None
    rpm_before: int | None = None
    rpm_after: int | None = None
    detail: str | None = None


@dataclass
class ValidationEvidence:
    """One orchestrated diagnostic, carried by reference.

    ``characterization`` is the Phase 3 run verbatim — every verdict on it is the
    daemon's, and the GUI recomputes none of them.
    """

    kind: str = ""
    member_id: str = ""
    run_id: str | None = None
    started_unix_ms: int = 0
    completed_unix_ms: int | None = None
    # How the ORCHESTRATION went, not a verdict on the hardware. A diagnostic the
    # daemon refused is `unavailable`, which never means failure.
    outcome: str = "unknown"
    detail: str | None = None
    characterization: CharacterizationRun | None = None
    verify: ValidationVerifyEvidence | None = None


@dataclass
class ValidationFinding:
    """One line of the evidence summary.

    ``id`` is a stable token and the GUI owns the wording. An unrecognised id
    must be rendered humanised, never dropped.
    """

    id: str = ""
    state: str = "unknown"
    detail: str | None = None
    member_id: str | None = None
    evidence_kind: str | None = None


@dataclass
class ValidationExternalMeasurement:
    """A meter reading typed in by a person. Explicitly untrusted; the daemon
    stores and returns these and no control path consults one."""

    unix_ms: int = 0
    kind: str = ""
    value: float = 0.0
    unit: str = ""
    member_id: str | None = None
    note: str | None = None


@dataclass
class ValidationSession:
    session_id: str = ""
    kind: str = VALIDATION_KIND_VALIDATION
    state: str = VALIDATION_STATE_IDLE
    started_unix_ms: int = 0
    completed_unix_ms: int | None = None
    metadata: ValidationMetadata = field(default_factory=ValidationMetadata)
    requested_diagnostics: list[str] = field(default_factory=list)
    sweep_members: list[str] = field(default_factory=list)
    samples: list[ValidationSample] = field(default_factory=list)
    events: list[ValidationEvent] = field(default_factory=list)
    evidence: list[ValidationEvidence] = field(default_factory=list)
    external_measurements: list[ValidationExternalMeasurement] = field(default_factory=list)
    findings: list[ValidationFinding] = field(default_factory=list)
    sample_limit_reached: bool = False
    interrupted_reason: str | None = None
    truncated_at_unix_ms: int | None = None

    @property
    def is_recording(self) -> bool:
        return self.state == VALIDATION_STATE_RECORDING


@dataclass
class ValidationSessionSummary:
    """The miniature that rides ``/status`` + ``/poll``."""

    session_id: str = ""
    kind: str = VALIDATION_KIND_VALIDATION
    state: str = VALIDATION_STATE_IDLE
    elapsed_ms: int = 0
    sample_count: int = 0
    event_count: int = 0
    sample_limit_reached: bool = False
    cooling_device_id: str = ""

    @property
    def is_recording(self) -> bool:
        return self.state == VALIDATION_STATE_RECORDING


@dataclass
class ValidationSessionIndexEntry:
    """One row of ``GET /validation/sessions`` — enough to pick a session."""

    session_id: str = ""
    kind: str = VALIDATION_KIND_VALIDATION
    state: str = VALIDATION_STATE_IDLE
    started_unix_ms: int = 0
    completed_unix_ms: int | None = None
    cooling_device_id: str = ""
    device_name: str = ""
    sample_count: int = 0
    event_count: int = 0
    sample_limit_reached: bool = False
    interrupted_reason: str | None = None


def parse_validation_session(data: dict) -> ValidationSession:
    """Parse a full session body from ``GET/POST /validation/session``.

    A pre-2.32 daemon 404s the route, so the caller gates on
    ``capabilities.control.validation_sessions``; this assumes a 200 body.
    """
    session = ValidationSession(**_filter_fields(ValidationSession, data))

    # Unconditional, unlike the `isinstance` guards below it. An explicit
    # ``"metadata": null`` on the wire passes straight through ``_filter_fields``
    # and overwrites the default factory with ``None``, which every consumer then
    # dereferences — the view-model raises ``AttributeError`` on the first field
    # it reads. The list fields are already reassigned unconditionally; this one
    # was the exception.
    meta_raw = data.get("metadata")
    session.metadata = (
        _validation_metadata_from(meta_raw) if isinstance(meta_raw, dict) else ValidationMetadata()
    )

    # Same defensiveness for the two token lists: a non-list here would be
    # iterated character-by-character by anything rendering it.
    for name in ("requested_diagnostics", "sweep_members"):
        value = getattr(session, name)
        if not isinstance(value, list):
            setattr(session, name, [])
        else:
            setattr(session, name, [str(v) for v in value])

    session.samples = [
        _validation_sample_from(s) for s in data.get("samples", []) if isinstance(s, dict)
    ]
    session.events = [
        ValidationEvent(**_filter_fields(ValidationEvent, e))
        for e in data.get("events", [])
        if isinstance(e, dict)
    ]
    session.evidence = [
        _validation_evidence_from(e) for e in data.get("evidence", []) if isinstance(e, dict)
    ]
    session.external_measurements = [
        ValidationExternalMeasurement(**_filter_fields(ValidationExternalMeasurement, m))
        for m in data.get("external_measurements", [])
        if isinstance(m, dict)
    ]
    session.findings = [
        ValidationFinding(**_filter_fields(ValidationFinding, f))
        for f in data.get("findings", [])
        if isinstance(f, dict)
    ]
    return session


def _validation_metadata_from(raw: dict) -> ValidationMetadata:
    meta = ValidationMetadata(**_filter_fields(ValidationMetadata, raw))
    policy = raw.get("device_policy")
    if isinstance(policy, dict):
        meta.device_policy = ValidationDevicePolicy(
            **_filter_fields(ValidationDevicePolicy, policy)
        )
    meta.members = [
        ValidationMemberRole(**_filter_fields(ValidationMemberRole, m))
        for m in raw.get("members", [])
        if isinstance(m, dict)
    ]
    user_meta = raw.get("user_metadata")
    meta.user_metadata = (
        {str(k): str(v) for k, v in user_meta.items()} if isinstance(user_meta, dict) else {}
    )
    return meta


def _validation_sample_from(raw: dict) -> ValidationSample:
    sample = ValidationSample(**_filter_fields(ValidationSample, raw))
    sample.members = [
        ValidationMemberSample(**_filter_fields(ValidationMemberSample, m))
        for m in raw.get("members", [])
        if isinstance(m, dict)
    ]
    return sample


def _validation_evidence_from(raw: dict) -> ValidationEvidence:
    ev = ValidationEvidence(**_filter_fields(ValidationEvidence, raw))
    char = raw.get("characterization")
    if isinstance(char, dict):
        # Reuse the Phase 3 parser rather than a second copy — the run is the
        # daemon's verbatim, and parsing it differently here is how the two would
        # drift.
        ev.characterization = parse_characterization_run(char)
    verify = raw.get("verify")
    if isinstance(verify, dict):
        ev.verify = ValidationVerifyEvidence(**_filter_fields(ValidationVerifyEvidence, verify))
    return ev


def parse_validation_session_summary(data: dict) -> ValidationSessionSummary | None:
    """Parse the ``validation_session`` block from ``/status`` or ``/poll``.

    ``None`` when the daemon omitted it — no session has ever run, or the daemon
    predates Phase 5. Absence is not an error.
    """
    raw = data.get("validation_session")
    if not isinstance(raw, dict):
        return None
    return ValidationSessionSummary(**_filter_fields(ValidationSessionSummary, raw))


def parse_validation_session_index(data: dict) -> list[ValidationSessionIndexEntry]:
    """Parse ``GET /validation/sessions``, newest first."""
    return [
        ValidationSessionIndexEntry(**_filter_fields(ValidationSessionIndexEntry, s))
        for s in data.get("sessions", [])
        if isinstance(s, dict)
    ]
