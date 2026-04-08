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


@dataclass
class UnsupportedCapability:
    present: bool = False
    status: str = "unsupported"


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
class Capabilities:
    api_version: int = 1
    daemon_version: str = ""
    ipc_transport: str = ""
    openfan: OpenfanCapability = field(default_factory=OpenfanCapability)
    hwmon: HwmonCapability = field(default_factory=HwmonCapability)
    amd_gpu: AmdGpuCapability = field(default_factory=AmdGpuCapability)
    aio_hwmon: UnsupportedCapability = field(default_factory=UnsupportedCapability)
    aio_usb: UnsupportedCapability = field(default_factory=UnsupportedCapability)
    features: FeatureFlags = field(default_factory=FeatureFlags)
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


# ---------------------------------------------------------------------------
# Sensors and Fans
# ---------------------------------------------------------------------------


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
    pwm_index: int = 0
    supports_enable: bool = False
    rpm_available: bool = False
    min_pwm_percent: int = 0
    max_pwm_percent: int = 100
    is_writable: bool = True
    pwm_mode: int | None = None  # 0=DC, 1=PWM, None=not exposed


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
class SetPwmAllResult:
    pwm_percent: int = 0
    channels_affected: int = 0


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
class ActiveProfileInfo:
    """Response from GET /profile/active."""

    active: bool = False
    profile_id: str = ""
    profile_name: str = ""


# ---------------------------------------------------------------------------
# Sensor history
# ---------------------------------------------------------------------------


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
# Parsing helpers
# ---------------------------------------------------------------------------


def _filter_fields(cls: type, data: dict) -> dict:
    """Filter a dict to only keys that match dataclass field names.

    Prevents TypeError from ``**`` unpacking when the daemon sends new fields
    that the GUI's dataclass doesn't know about yet (forward compatibility).
    """
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in known}


def parse_capabilities(data: dict) -> Capabilities:
    devices = data.get("devices", {})
    features = data.get("features", {})
    limits = data.get("limits", {})

    return Capabilities(
        api_version=data.get("api_version", 1),
        daemon_version=data.get("daemon_version", ""),
        ipc_transport=data.get("ipc_transport", ""),
        openfan=OpenfanCapability(**_filter_fields(OpenfanCapability, devices.get("openfan", {}))),
        hwmon=HwmonCapability(**_filter_fields(HwmonCapability, devices.get("hwmon", {}))),
        amd_gpu=AmdGpuCapability(**_filter_fields(AmdGpuCapability, devices.get("amd_gpu", {}))),
        aio_hwmon=UnsupportedCapability(
            **_filter_fields(UnsupportedCapability, devices.get("aio_hwmon", {}))
        ),
        aio_usb=UnsupportedCapability(
            **_filter_fields(UnsupportedCapability, devices.get("aio_usb", {}))
        ),
        features=FeatureFlags(**_filter_fields(FeatureFlags, features)),
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
    )


def parse_sensors(data: dict) -> list[SensorReading]:
    return [SensorReading(**_filter_fields(SensorReading, s)) for s in data.get("sensors", [])]


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


def parse_set_pwm_all(data: dict) -> SetPwmAllResult:
    return SetPwmAllResult(
        pwm_percent=data.get("pwm_percent", 0),
        channels_affected=data.get("channels_affected", 0),
    )


def parse_lease_result(data: dict) -> LeaseResult:
    return LeaseResult(
        lease_id=data.get("lease_id", ""),
        owner_hint=data.get("owner_hint", ""),
        ttl_seconds=data.get("ttl_seconds", 0),
    )


def parse_lease_released(data: dict) -> LeaseReleasedResult:
    return LeaseReleasedResult(released=data.get("released", False))


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
