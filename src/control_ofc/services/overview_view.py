"""Qt-free view-model layer for the Overview page (DEC-209).

Pure builders that turn poll-driven models (`Capabilities`, `DaemonStatus`,
`FanReading`, `SensorReading`, `HwmonHeader`) into frozen `…VM` dataclasses the
thin `OverviewPage` renderer consumes. All display derivation (control method,
presence, confidence, freshness, tooltips) lives here so it is unit-testable
without a `QApplication`.

This module also owns the eight display helpers **extracted verbatim** from
`ui/pages/diagnostics_page.py` (they were always Qt-free); the page re-exports
them under their old private names so its tests keep importing them. The builders
mirror the current `_on_capabilities` / `_on_status` / `_on_fans` / `_set_sensor_row`
/ `_recompute_sensor_summary` behaviour exactly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html import escape

from control_ofc.api.models import (
    Capabilities,
    DaemonStatus,
    FanReading,
    Freshness,
    HwmonCapability,
    HwmonHeader,
    SensorReading,
)
from control_ofc.knowledge.hwmon_label_resolver import is_placeholder_hwmon_label
from control_ofc.knowledge.sensor_knowledge import (
    classify_sensor,
    classify_sensor_with_overrides,
    format_sensor_tooltip,
)
from control_ofc.services.diagnostics_service import format_uptime
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.fan_presence import (
    PRESENCE_BADGE,
    PRESENCE_TOOLTIP,
    FanPresence,
    classify_fan_presence,
)
from control_ofc.ui.hwmon_guidance import lookup_chip_guidance

# ─── Display maps (extracted from diagnostics_page.py) ─────────────────────

CONTROL_METHOD_TOOLTIPS: dict[str, str] = {
    "OpenFan USB": "OpenFan Controller connected via USB serial.",
    "hwmon PWM": "Motherboard fan controlled via hwmon PWM (the daemon engine owns all writes).",
    "hwmon PWM — no RPM": (
        "Motherboard PWM output without a tachometer input. Writable but no RPM feedback."
    ),
    "hwmon PWM (legacy)": ("Pre-RDNA3 GPU fan controlled via the legacy pwm1 sysfs interface."),
    "PMFW curve": ("GPU fan controlled via the AMD PMFW fan_curve sysfs interface."),
    "read-only": (
        "BIOS/EC owns this fan; PWM writes will be reverted. Run Test PWM Control to confirm."
    ),
    "no fan control": "GPU has no writable fan control path exposed to the OS.",
    "unknown": "Daemon did not report a classification for this fan.",
}

CONFIDENCE_DISPLAY: dict[str, str] = {
    "high": "High",
    "medium_high": "Medium-High",
    "medium": "Medium",
    "low": "Low",
}

SOURCE_CLASS_DISPLAY: dict[str, str] = {
    "cpu_die": "CPU die",
    "cpu_control": "CPU control",
    "cpu_ccd": "CPU CCD",
    "cpu_peci": "CPU (PECI)",
    "cpu_board_side": "CPU (board-side)",
    "cpu_internal": "CPU internal",
    "cpu_package": "CPU package",
    "amd_tsi": "AMD TSI",
    "gpu_edge": "GPU edge",
    "gpu_junction": "GPU junction",
    "gpu_memory": "GPU memory",
    "gpu_other": "GPU",
    "vrm": "VRM",
    "chipset": "Chipset",
    "external_probe": "External probe",
    "coolant_in": "Coolant in",
    "coolant_out": "Coolant out",
    "coolant": "Coolant",
    "board_ambient": "Board ambient",
    "board_system": "Board (SYSTIN)",
    "board_auxiliary": "Board (aux)",
    "board_thermistor": "Board thermistor",
    "thermal_diode": "Thermal diode",
    "memory_dimm": "DIMM",
    "smbus_device": "SMBus",
    "virtual": "Virtual",
    "chip_local": "Chip local",
    "disk_composite": "Disk",
    "super_io_channel": "Super-I/O ch.",
    "vendor_wmi_unlabeled": "Vendor WMI",
    "vendor_labeled": "Vendor",
    "bogus": "Bogus",
    "unknown": "Unknown",
}

# Semantic-state maps for the StatusPill badges the Overview page renders.
_FRESHNESS_STATE: dict[Freshness, str] = {
    Freshness.FRESH: "ok",
    Freshness.STALE: "warn",
    Freshness.INVALID: "crit",
}
_CONFIDENCE_STATE: dict[str, str] = {
    "high": "ok",
    "medium_high": "info",
    "medium": "neutral",
    "low": "warn",
}
# Daemon `overall_status` → pill state.
#
# The daemon emits exactly three values, from `HealthStatus::Display`:
# "ok" | "warn" | "crit". "crit" was missing here, so a critical daemon fell
# through `.get(..., "neutral")` and painted a **grey** pill — visually calmer
# than "warn", which *was* mapped. A severity inversion, and it silently
# nullified DEC-249's whole point: a stalled engine escalates overall_status to
# "crit" precisely so it is impossible to miss.
#
# The other keys are legacy/defensive spellings this GUI has carried for
# daemons that never emitted them; harmless, kept so an older or third-party
# daemon still colours sensibly. The three the daemon actually sends are pinned
# by test_overview_view.
_STATUS_STATE: dict[str, str] = {
    "ok": "ok",
    "warn": "warn",
    "crit": "crit",
    # Legacy / defensive spellings — not emitted by any current daemon.
    "degraded": "warn",
    "warning": "warn",
    "error": "crit",
    "critical": "crit",
}


# ─── Extracted pure helpers (decomposed off AppState) ──────────────────────


def fan_control_method(
    fan: FanReading, headers: list[HwmonHeader], caps: Capabilities | None
) -> str:
    """Control-method display string for a fan (typed daemon data only)."""
    if fan.source == "openfan":
        return "OpenFan USB"
    if fan.source == "amd_gpu":
        if not caps or not caps.amd_gpu.present:
            return "unknown"
        method = caps.amd_gpu.fan_control_method
        return {
            "pmfw_curve": "PMFW curve",
            "hwmon_pwm": "hwmon PWM (legacy)",
            "read_only": "read-only",
            "none": "no fan control",
        }.get(method, "unknown")
    if fan.source == "intel_gpu":
        if caps and caps.intel_gpu.present:
            method = caps.intel_gpu.fan_control_method
            return {"read_only": "read-only", "none": "no fan control"}.get(method, "read-only")
        return "read-only"
    if fan.source == "nvidia_gpu":
        if caps and caps.nvidia_gpu.present:
            method = caps.nvidia_gpu.fan_control_method
            return {"read_only": "read-only", "none": "no fan control"}.get(method, "read-only")
        return "read-only"
    if fan.source == "hwmon":
        header = next((h for h in headers if h.id == fan.id), None)
        if header is None:
            return "unknown"
        return "read-only" if not header.is_writable else "hwmon PWM"
    return "unknown"


def pwm_only_control_method(header: HwmonHeader) -> str:
    """Control-method display string for a PWM-only hwmon header (no tachometer)."""
    return "read-only" if not header.is_writable else "hwmon PWM — no RPM"


def hwmon_overview_text(
    hwmon_cap: HwmonCapability, writable_headers: int | None
) -> tuple[str, bool]:
    """Overview hwmon line + whether it should be warn-styled (ALL read-only)."""
    if not hwmon_cap.present:
        return "hwmon: Not present", False
    count = hwmon_cap.pwm_header_count
    if count > 0 and writable_headers == 0:
        return f"hwmon: Present ({count} headers — ALL read-only)", True
    parts: list[str] = []
    if hwmon_cap.write_support:
        parts.append("write")
    suffix = (", " + ", ".join(parts) + ")") if parts else ")"
    return f"hwmon: Present ({count} headers{suffix}", False


def features_line_text(caps: Capabilities, writable_headers: int | None) -> str:
    """Overview Features line (annotates hwmon-writes when 0 writable headers)."""
    f = caps.features
    features: list[str] = []
    if f.openfan_write_supported:
        features.append("OpenFan writes")
    if f.hwmon_write_supported:
        if writable_headers == 0 and caps.hwmon.present and caps.hwmon.pwm_header_count > 0:
            features.append("hwmon writes (daemon-supported; 0 writable headers on this system)")
        else:
            features.append("hwmon writes")
    return f"Features: {', '.join(features) or 'none'}"


def is_alarm_active(s: SensorReading) -> bool:
    """True when the daemon reported an asserted crit_alarm or value ≥ crit (DEC-117)."""
    t = s.thresholds
    if t is None:
        return False
    if t.crit_alarm is True:
        return True
    return t.crit_c is not None and s.value_c >= t.crit_c


def fan_row_tooltip(
    fan: FanReading,
    headers: list[HwmonHeader],
    caps: Capabilities | None,
    presence: FanPresence | None = None,
) -> str:
    """Multi-line fan-row tooltip (chip/driver + control-method context).

    Daemon-supplied fields (``id``/``source``/``chip_name``/GPU labels) are
    ``html.escape``-d because Qt tooltips auto-detect rich text
    (``Qt.mightBeRichText``): a stray ``'<...>'`` in a daemon string would
    otherwise be reinterpreted as markup. ``quote=False`` keeps apostrophes
    literal so plain-text tooltips read normally (DEC-106).
    """
    parts = [f"ID: {escape(fan.id, quote=False)}", f"Source: {escape(fan.source, quote=False)}"]
    parts.append(f"Control method: {fan_control_method(fan, headers, caps)}")
    if presence is not None and presence not in (FanPresence.PRESENT, FanPresence.UNKNOWN):
        parts.append(f"Presence: {PRESENCE_BADGE[presence]}")
        parts.append(PRESENCE_TOOLTIP[presence])
    if fan.age_ms is not None:
        parts.append(f"Data age: {fan.age_ms} ms")
    if fan.source == "hwmon":
        header = next((h for h in headers if h.id == fan.id), None)
        if header:
            if header.chip_name:
                parts.append(f"Chip: {escape(header.chip_name, quote=False)}")
            g = lookup_chip_guidance(header.chip_name) if header.chip_name else None
            if g:
                status = "mainline" if g.in_mainline else g.driver_package
                parts.append(f"Driver: {g.driver_name} ({status})")
            mode = {0: "DC", 1: "PWM"}.get(header.pwm_mode if header.pwm_mode is not None else -1)
            if mode:
                parts.append(f"PWM mode: {mode}")
    elif fan.source == "amd_gpu":
        if caps and caps.amd_gpu.present:
            gpu = caps.amd_gpu
            parts.append(f"GPU: {escape(gpu.display_label, quote=False)}")
            if gpu.pci_id:
                parts.append(f"PCI: {escape(gpu.pci_id, quote=False)}")
    elif fan.source == "intel_gpu":
        if caps and caps.intel_gpu.present:
            igpu = caps.intel_gpu
            parts.append(f"GPU: {escape(igpu.display_label, quote=False)}")
            if igpu.pci_id:
                parts.append(f"PCI: {escape(igpu.pci_id, quote=False)}")
            parts.append("Fan: read-only (firmware-managed)")
    elif fan.source == "nvidia_gpu":
        if caps and caps.nvidia_gpu.present:
            ngpu = caps.nvidia_gpu
            parts.append(f"GPU: {escape(ngpu.display_label, quote=False)}")
            if ngpu.pci_id:
                parts.append(f"PCI: {escape(ngpu.pci_id, quote=False)}")
            ngpu_fan = {"read_only": "read-only", "none": "no fan control"}.get(
                ngpu.fan_control_method, "read-only"
            )
            parts.append(f"Fan: {ngpu_fan}")
    return "\n".join(parts)


def _format_rpm_cell(fan: FanReading, presence: FanPresence) -> str:
    rpm_text = str(fan.rpm) if fan.rpm is not None else "—"
    badge = PRESENCE_BADGE.get(presence, "")
    return f"{rpm_text} — {badge}" if badge else rpm_text


# ─── View-model dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class DaemonHealthVM:
    version_text: str
    status_text: str
    status_state: str  # ok | warn | crit | neutral
    uptime_text: str
    subsystems_text: str
    overrides_text: str
    age_note: str


@dataclass(frozen=True)
class DeviceDiscoveryVM:
    openfan: str
    hwmon: str
    hwmon_warn: bool
    amd_gpu: str
    intel_gpu: str
    nvidia_gpu: str
    aio: str
    features: str


@dataclass(frozen=True)
class FanRowVM:
    name: str
    # DEC-227: stable fan/header id behind the row. The table shows no ID column,
    # so without this the page has no way to map a right-clicked row back to the
    # fan it names. Required, not defaulted — a row that cannot be identified
    # should fail loudly at construction rather than silently refuse to rename.
    fan_id: str
    source: str
    control_method: str
    control_method_tooltip: str
    rpm_text: str
    pwm_text: str
    freshness_label: str
    freshness_state: str  # ok | warn | crit | neutral
    row_tooltip: str
    is_pwm_only: bool


@dataclass(frozen=True)
class SensorRowVM:
    label: str
    sensor_id: str
    source_class_text: str
    chip: str
    value_text: str
    age_text: str
    confidence_label: str
    confidence_state: str  # ok | info | neutral | warn
    tooltip: str
    is_quirky: bool
    is_low_confidence: bool
    is_alarm: bool


# ─── Builders (mirror the current renderers exactly) ───────────────────────

# DEC-302: NOT "time since the daemon last polled it". On daemon >= 2.24.2 the
# openfan/hwmon entries report the worse of poll liveness and data freshness, so
# when a poll is running but not covering every channel/sensor, age_ms is the
# OLDEST READING's age rather than the poll's. The old wording described exactly
# the number that let a 3-of-10 frame read as "readings fresh".
_AGE_NOTE = "Age = how long ago this subsystem's data was last refreshed"


def build_daemon_health_vm(
    caps: Capabilities | None, status: DaemonStatus | None
) -> DaemonHealthVM:
    version_text = (
        f"Daemon: v{caps.daemon_version} (API v{caps.api_version})" if caps else "Daemon: —"
    )
    if status is None:
        return DaemonHealthVM(
            version_text=version_text,
            status_text="Status: —",
            status_state="neutral",
            uptime_text="Uptime: —",
            subsystems_text="Subsystems: —",
            overrides_text="Overrides: —",
            age_note=_AGE_NOTE,
        )
    status_text = f"Status: {status.overall_status}"
    status_state = _STATUS_STATE.get(status.overall_status, "neutral")
    uptime_text = (
        f"Uptime: {format_uptime(status.uptime_seconds)}"
        if status.uptime_seconds is not None
        else "Uptime: —"
    )
    subsystem_lines = []
    for s in status.subsystems:
        age = f" (age {s.age_ms}ms)" if s.age_ms is not None else ""
        reason = f" — {s.reason}" if s.reason else ""
        subsystem_lines.append(f"{s.name}: {s.status}{age}{reason}")
    subsystems_text = (
        "Subsystems:\n" + "\n".join(subsystem_lines) if subsystem_lines else "Subsystems: —"
    )
    ov_lines = [f"{o.control_id} {o.pwm_percent}% ({o.expires_in_secs}s)" for o in status.overrides]
    id_lines = [
        f"{i.fan_id} {i.describe_hold()} ({i.expires_in_secs}s)" for i in status.fan_identify
    ]
    active = []
    if ov_lines:
        active.append("Overrides: " + ", ".join(ov_lines))
    if id_lines:
        active.append("Identify: " + ", ".join(id_lines))
    overrides_text = "\n".join(active) if active else "Overrides: —"
    return DaemonHealthVM(
        version_text=version_text,
        status_text=status_text,
        status_state=status_state,
        uptime_text=uptime_text,
        subsystems_text=subsystems_text,
        overrides_text=overrides_text,
        age_note=_AGE_NOTE,
    )


def build_device_discovery_vm(
    caps: Capabilities | None, writable_headers: int | None
) -> DeviceDiscoveryVM:
    if caps is None:
        return DeviceDiscoveryVM(
            openfan="OpenFan: —",
            hwmon="hwmon: —",
            hwmon_warn=False,
            amd_gpu="AMD GPU: —",
            intel_gpu="Intel GPU: —",
            nvidia_gpu="NVIDIA GPU: —",
            aio="Liquid cooling: —",
            features="Features: —",
        )
    of = caps.openfan
    of_status = f"Present ({of.channels} ch" if of.present else "Not present"
    if of.present:
        parts = []
        if of.write_support:
            parts.append("write")
        if of.rpm_support:
            parts.append("RPM")
        of_status += ", " + "+".join(parts) + ")" if parts else ")"
    openfan = f"OpenFan: {of_status}"

    hwmon_text, hwmon_warn = hwmon_overview_text(caps.hwmon, writable_headers)

    gpu = caps.amd_gpu
    if gpu.present:
        gpu_parts = [gpu.display_label]
        if gpu.pci_id:
            gpu_parts.append(f"PCI {gpu.pci_id}")
        gpu_parts.append(f"fan: {gpu.fan_control_method}")
        amd_gpu = f"AMD GPU: {', '.join(gpu_parts)}"
    else:
        amd_gpu = "AMD GPU: Not detected"

    igpu = caps.intel_gpu
    if igpu.present:
        igpu_parts = [igpu.display_label]
        if igpu.pci_id:
            igpu_parts.append(f"PCI {igpu.pci_id}")
        igpu_parts.append(f"fan: {igpu.fan_control_method} (firmware-managed)")
        intel_gpu = f"Intel GPU: {', '.join(igpu_parts)}"
    else:
        intel_gpu = "Intel GPU: Not detected"

    ngpu = caps.nvidia_gpu
    if ngpu.present:
        ngpu_parts = [ngpu.display_label]
        if ngpu.pci_id:
            ngpu_parts.append(f"PCI {ngpu.pci_id}")
        ngpu_fan = {"read_only": "read-only", "none": "no fan control"}.get(
            ngpu.fan_control_method, "read-only"
        )
        ngpu_parts.append(f"fan: {ngpu_fan}")
        nvidia_gpu = f"NVIDIA GPU: {', '.join(ngpu_parts)}"
    else:
        nvidia_gpu = "NVIDIA GPU: Not detected"

    # `status` is the daemon's own three-way verdict and was parsed and never
    # read; this re-derived the identical answer from `present` + `pump_writable`
    # (`WIRE-s`). Equivalent today, but it is a rule the daemon owns and either
    # side could move. An unrecognised token is described rather than dropped
    # (273-i) — a newer daemon may add one, and "Not detected" would be a lie.
    aio_cap = caps.aio_hwmon
    if aio_cap.status == "supported":
        detail = "pump/fan writable"
    elif aio_cap.status == "monitor_only":
        detail = "monitor-only (read-only driver — use vendor tooling)"
    elif aio_cap.present:
        detail = aio_cap.status.replace("_", " ") or "status not reported"
    else:
        detail = ""
    if detail:
        if aio_cap.coolant_available:
            detail += ", coolant sensed"
        aio = f"Liquid cooling: Detected (hwmon) — {detail}"
    else:
        aio = "Liquid cooling: Not detected"

    return DeviceDiscoveryVM(
        openfan=openfan,
        hwmon=hwmon_text,
        hwmon_warn=hwmon_warn,
        amd_gpu=amd_gpu,
        intel_gpu=intel_gpu,
        nvidia_gpu=nvidia_gpu,
        aio=aio,
        features=features_line_text(caps, writable_headers),
    )


def build_fan_rows(
    fans: list[FanReading],
    headers: list[HwmonHeader],
    caps: Capabilities | None,
    display_name: Callable[[str], str],
) -> list[FanRowVM]:
    """Mirror of `_on_fans`: displayable fans + synthesized PWM-only header rows."""
    fan_ids = {f.id for f in fans}
    pwm_only = [h for h in headers if h.id not in fan_ids]
    header_by_id = {h.id: h for h in headers}
    display_fans = filter_displayable_fans(fans, {}, hide_unused=False)

    rows: list[FanRowVM] = []
    for f in display_fans:
        control_method = fan_control_method(f, headers, caps)
        presence = classify_fan_presence(f, header_by_id.get(f.id))
        rows.append(
            FanRowVM(
                name=display_name(f.id),
                fan_id=f.id,
                source=f.source,
                control_method=control_method,
                control_method_tooltip=CONTROL_METHOD_TOOLTIPS.get(
                    control_method, CONTROL_METHOD_TOOLTIPS["unknown"]
                ),
                rpm_text=_format_rpm_cell(f, presence),
                pwm_text=str(f.last_commanded_pwm) if f.last_commanded_pwm is not None else "—",
                freshness_label=f.freshness.value,
                freshness_state=_FRESHNESS_STATE.get(f.freshness, "neutral"),
                row_tooltip=fan_row_tooltip(f, headers, caps, presence),
                is_pwm_only=False,
            )
        )

    for h in pwm_only:
        control_method = pwm_only_control_method(h)
        tip_parts = [
            f"ID: {h.id}",
            "Source: hwmon (PWM output only — no RPM tachometer)",
            f"Control method: {control_method}",
        ]
        # DEC-229: the daemon synthesises "pwmN" when the chip publishes no
        # label file. Repeating that under "Label:" would contradict the
        # resolved row name directly above it and pass off an invented value
        # as sysfs truth, so the line is dropped rather than filled in.
        if h.label and not is_placeholder_hwmon_label(h.label, h.pwm_index):
            tip_parts.append(f"Label: {h.label}")
        if h.chip_name:
            tip_parts.append(f"Chip: {h.chip_name}")
        rows.append(
            FanRowVM(
                # Resolved like any other fan row (DEC-227) rather than raw
                # h.label: these headers are renamable too, and a rename that the
                # row then refused to show would be the very bug this fixes.
                name=display_name(h.id),
                fan_id=h.id,
                source="hwmon (PWM-only)",
                control_method=control_method,
                control_method_tooltip=CONTROL_METHOD_TOOLTIPS.get(
                    control_method, CONTROL_METHOD_TOOLTIPS["unknown"]
                ),
                rpm_text="—",
                pwm_text="—",
                freshness_label="N/A",
                freshness_state="neutral",
                row_tooltip="\n".join(tip_parts),
                is_pwm_only=True,
            )
        )
    return rows


def build_sensor_rows(
    sensors: list[SensorReading], *, overrides: dict[str, str], board_vendor: str
) -> list[SensorRowVM]:
    """Mirror of `_set_sensor_row` (minus Qt): 8-column data + hover tooltip.

    The tooltip appends the daemon `Source:` line — the one datum the mockup
    moves out of the table into hover (Session min/max is already in the tooltip).
    """
    rows: list[SensorRowVM] = []
    for s in sensors:
        classification = classify_sensor_with_overrides(
            s.id,
            chip_name=s.chip_name,
            label=s.label,
            temp_type=s.temp_type,
            board_vendor=board_vendor,
            overrides=overrides,
        )
        is_quirky = classification.source_class == "bogus"
        is_low_confidence = classification.confidence == "low"
        prefix = "⚠ " if is_quirky else ("? " if is_low_confidence else "")
        value_text = f"{s.value_c:.1f}"
        alarm = is_alarm_active(s)
        if alarm:
            value_text += "  ⚠ ALARM"
        tooltip = format_sensor_tooltip(
            classification,
            sensor_id=s.id,
            chip_name=s.chip_name,
            session_min=s.session_min_c,
            session_max=s.session_max_c,
            rate_c_per_s=s.rate_c_per_s,
        )
        tooltip += f"\nSource: {escape(s.source, quote=False) if s.source else '—'}"
        rows.append(
            SensorRowVM(
                label=prefix + (s.label or s.id),
                sensor_id=s.id or "—",
                source_class_text=SOURCE_CLASS_DISPLAY.get(
                    classification.source_class, classification.source_class
                ),
                chip=s.chip_name or "—",
                value_text=value_text,
                age_text=str(s.age_ms),
                confidence_label=CONFIDENCE_DISPLAY.get(
                    classification.confidence, classification.confidence
                ),
                confidence_state=_CONFIDENCE_STATE.get(classification.confidence, "neutral"),
                tooltip=tooltip,
                is_quirky=is_quirky,
                is_low_confidence=is_low_confidence,
                is_alarm=alarm,
            )
        )
    return rows


def build_sensor_summary(
    all_sensors: list[SensorReading],
    *,
    hidden_count: int,
    unavailable_count: int,
    board_vendor: str,
) -> str:
    """Mirror of `_recompute_sensor_summary`: the 'Sensors: N total · …' line."""
    n = len(all_sensors)
    if n == 0 and unavailable_count == 0:
        return "Sensors: —"
    cpu = sum(1 for s in all_sensors if s.kind == "cpu_temp")
    board = sum(1 for s in all_sensors if s.kind == "mb_temp")
    gpu = sum(1 for s in all_sensors if s.kind == "gpu_temp")
    disk = sum(1 for s in all_sensors if s.kind == "disk_temp")
    # DEC-156's fifth kind, missing since it shipped (`WIRE-c`). On an AIO
    # machine the coolant temperature is arguably the headline number, and it
    # was the one kind with no line in the breakdown.
    coolant = sum(1 for s in all_sensors if s.kind == "coolant_temp")
    stale = sum(1 for s in all_sensors if s.freshness != Freshness.FRESH)
    low_conf = 0
    for s in all_sensors:
        c = classify_sensor(
            chip_name=s.chip_name, label=s.label, temp_type=s.temp_type, board_vendor=board_vendor
        )
        if c.confidence == "low":
            low_conf += 1
    parts = [f"{n} total"]
    if cpu:
        parts.append(f"{cpu} CPU")
    if board:
        parts.append(f"{board} board")
    if gpu:
        parts.append(f"{gpu} GPU")
    if coolant:
        parts.append(f"{coolant} liquid")
    if disk:
        parts.append(f"{disk} disk")
    if stale:
        parts.append(f"{stale} stale")
    if low_conf:
        parts.append(f"{low_conf} low-confidence")
    if unavailable_count:
        parts.append(f"{unavailable_count} unavailable")
    if hidden_count:
        parts.append(f"{hidden_count} hidden")
    return "Sensors: " + " · ".join(parts)
