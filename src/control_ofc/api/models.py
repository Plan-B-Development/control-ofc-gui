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
    MANUAL_OVERRIDE = "manual_override"
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
    lease_required: bool = True
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
    lease_required_for_hwmon_writes: bool = True


@dataclass
class SafetyLimits:
    pwm_percent_min: int = 0
    pwm_percent_max: int = 100
    openfan_stop_timeout_s: int = 8


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


@dataclass
class Capabilities:
    api_version: int = 1
    daemon_version: str = ""
    ipc_transport: str = ""
    openfan: OpenfanCapability = field(default_factory=OpenfanCapability)
    hwmon: HwmonCapability = field(default_factory=HwmonCapability)
    amd_gpu: AmdGpuCapability = field(default_factory=AmdGpuCapability)
    intel_gpu: IntelGpuCapability = field(default_factory=IntelGpuCapability)
    aio_hwmon: AioHwmonCapability = field(default_factory=AioHwmonCapability)
    aio_usb: UnsupportedCapability = field(default_factory=UnsupportedCapability)
    features: FeatureFlags = field(default_factory=FeatureFlags)
    control: ControlCapability = field(default_factory=ControlCapability)
    limits: SafetyLimits = field(default_factory=SafetyLimits)


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
class StatusCounters:
    last_error_summary: str | None = None


@dataclass
class DaemonStatus:
    api_version: int = 1
    daemon_version: str = ""
    overall_status: str = "unknown"
    subsystems: list[SubsystemStatus] = field(default_factory=list)
    counters: StatusCounters = field(default_factory=StatusCounters)
    uptime_seconds: int | None = None
    gui_last_seen_seconds_ago: int | None = None
    # Daemon thermal safety override state (DEC-132): "normal" | "recovery"
    # | "emergency" | "no_sensor_fallback". While not "normal" the daemon is
    # forcing OpenFan+hwmon PWM and force-taking the hwmon lease, so the
    # control loop stands down. Defaults to "normal" for older daemons that
    # don't send the field.
    thermal_state: str = "normal"


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
    age_ms: int = 0
    stall_detected: bool | None = None

    @property
    def freshness(self) -> Freshness:
        if self.age_ms < 2000:
            return Freshness.FRESH
        if self.age_ms < 10000:
            return Freshness.STALE
        return Freshness.INVALID


# ---------------------------------------------------------------------------
# Hwmon headers and lease
# ---------------------------------------------------------------------------


@dataclass
class HwmonHeader:
    id: str = ""
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


@dataclass
class LeaseState:
    lease_required: bool = True
    held: bool = False
    lease_id: str | None = None
    ttl_seconds_remaining: int | None = None
    owner_hint: str | None = None


# ---------------------------------------------------------------------------
# Write responses
# ---------------------------------------------------------------------------


@dataclass
class SetPwmResult:
    channel: int = 0
    pwm_percent: int = 0
    coalesced: bool = False


@dataclass
class HwmonSetPwmResult:
    header_id: str = ""
    pwm_percent: int = 0
    raw_value: int = 0


@dataclass
class LeaseResult:
    lease_id: str = ""
    owner_hint: str = ""
    ttl_seconds: int = 0


@dataclass
class GpuFanSetResult:
    gpu_id: str = ""
    speed_pct: int = 0


@dataclass
class GpuFanResetResult:
    gpu_id: str = ""
    reset: bool = False


@dataclass
class LeaseReleasedResult:
    released: bool = False


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


@dataclass
class FieldViolation:
    """One validation error from ``error.details.field_violations`` (DEC-160)."""

    field: str = ""
    reason: str = ""
    description: str = ""


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
class StartupDelayResult:
    """Response from POST /config/startup-delay."""

    updated: bool = False
    delay_secs: int = 0


@dataclass
class ProfileSearchDirsResult:
    """Response from POST /config/profile-search-dirs."""

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
    state: str = "normal"
    cpu_sensor_found: bool = False
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
# Parsing helpers
# ---------------------------------------------------------------------------


def _filter_fields(cls: type, data: dict) -> dict:
    """Filter a dict to only keys that match dataclass field names.

    Prevents TypeError from ``**`` unpacking when the daemon sends new fields
    that the GUI's dataclass doesn't know about yet (forward compatibility).
    """
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in known}


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
    limits = data.get("limits", {})

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

    return Capabilities(
        api_version=data.get("api_version", 1),
        daemon_version=data.get("daemon_version", ""),
        ipc_transport=data.get("ipc_transport", ""),
        openfan=OpenfanCapability(**_filter_fields(OpenfanCapability, devices.get("openfan", {}))),
        hwmon=HwmonCapability(**_filter_fields(HwmonCapability, devices.get("hwmon", {}))),
        amd_gpu=amd_gpu,
        intel_gpu=intel_gpu,
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
        limits=SafetyLimits(
            pwm_percent_min=limits.get("pwm_percent_min", 0),
            pwm_percent_max=limits.get("pwm_percent_max", 100),
            openfan_stop_timeout_s=limits.get("openfan_stop_timeout_s", 8),
        ),
    )


def parse_status(data: dict) -> DaemonStatus:
    return DaemonStatus(
        api_version=data.get("api_version", 1),
        daemon_version=data.get("daemon_version", ""),
        overall_status=data.get("overall_status", "unknown"),
        subsystems=[
            SubsystemStatus(**_filter_fields(SubsystemStatus, s))
            for s in data.get("subsystems", [])
        ],
        counters=StatusCounters(**_filter_fields(StatusCounters, data.get("counters", {}))),
        uptime_seconds=data.get("uptime_seconds"),
        gui_last_seen_seconds_ago=data.get("gui_last_seen_seconds_ago"),
        # DEC-132: absent on pre-1.13 daemons — treat as "normal".
        thermal_state=data.get("thermal_state", "normal"),
    )


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
    return [FanReading(**_filter_fields(FanReading, s)) for s in data.get("fans", [])]


def parse_hwmon_headers(data: dict) -> list[HwmonHeader]:
    return [HwmonHeader(**_filter_fields(HwmonHeader, h)) for h in data.get("headers", [])]


def parse_lease_status(data: dict) -> LeaseState:
    return LeaseState(
        lease_required=data.get("lease_required", True),
        held=data.get("held", False),
        lease_id=data.get("lease_id"),
        ttl_seconds_remaining=data.get("ttl_seconds_remaining"),
        owner_hint=data.get("owner_hint"),
    )


def parse_set_pwm(data: dict) -> SetPwmResult:
    return SetPwmResult(
        channel=data.get("channel", 0),
        pwm_percent=data.get("pwm_percent", 0),
        coalesced=data.get("coalesced", False),
    )


def parse_lease_result(data: dict) -> LeaseResult:
    return LeaseResult(
        lease_id=data.get("lease_id", ""),
        owner_hint=data.get("owner_hint", ""),
        ttl_seconds=data.get("ttl_seconds", 0),
    )


def parse_lease_released(data: dict) -> LeaseReleasedResult:
    return LeaseReleasedResult(released=data.get("released", False))


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
    """
    if not isinstance(details, dict):
        return []
    raw = details.get("field_violations", [])
    if not isinstance(raw, list):
        return []
    return [FieldViolation(**_filter_fields(FieldViolation, v)) for v in raw if isinstance(v, dict)]


def parse_hwmon_set_pwm(data: dict) -> HwmonSetPwmResult:
    return HwmonSetPwmResult(
        header_id=data.get("header_id", ""),
        pwm_percent=data.get("pwm_percent", 0),
        raw_value=data.get("raw_value", 0),
    )


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


def parse_gpu_fan_set(data: dict) -> GpuFanSetResult:
    return GpuFanSetResult(gpu_id=data.get("gpu_id", ""), speed_pct=data.get("speed_pct", 0))


def parse_gpu_fan_reset(data: dict) -> GpuFanResetResult:
    return GpuFanResetResult(gpu_id=data.get("gpu_id", ""), reset=data.get("reset", False))


def parse_startup_delay(data: dict) -> StartupDelayResult:
    return StartupDelayResult(
        updated=data.get("updated", False),
        delay_secs=int(data.get("delay_secs", 0)),
    )


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
