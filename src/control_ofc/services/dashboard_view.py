"""Qt-free view-model for the Dashboard page (DEC-219, Phase 7.2).

Pure computation carved out of :class:`DashboardPage`: the summary-card faces,
the fans-card face, chart-series curation, fan tooltips, capability chips /
banners, and the thermal-safety detail text. No Qt imports — the page renders
these plain dataclasses onto its (already-decomposed) widget components.

Keeping this layer Qt-free makes the dashboard's display logic unit-testable
without constructing widgets, mirroring the S2-S5 ``services/*_view`` modules.
"""

from __future__ import annotations

from dataclasses import dataclass

from control_ofc.api.models import (
    Capabilities,
    FanReading,
    Freshness,
    HwmonHeader,
    SensorReading,
)
from control_ofc.constants import EXPECTED_API_VERSION
from control_ofc.services.session_stats import SessionStatsTracker
from control_ofc.ui.hwmon_guidance import lookup_chip_guidance

# Trend deadband: a |rate| below this reads as "flat" so a near-steady
# temperature doesn't flicker the glyph between rising/falling.
_TREND_DEADBAND_C_PER_S = 0.05

# Plain-language reason per daemon thermal_state, for the Safety detail. Kept
# qualitative (no hardcoded thresholds) so it can't drift from the daemon.
_THERMAL_REASONS: dict[str, str] = {
    "normal": "Cooling is operating normally; the daemon is following the active profile.",
    "recovery": (
        "Temperature exceeded the safety threshold. The daemon forced fans up and is holding "
        "a recovery speed until the system cools further."
    ),
    "emergency": (
        "A critical temperature was reached. The daemon has forced all controllable fans to "
        "100% to protect the hardware until temperatures fall."
    ),
    "no_sensor_fallback": (
        "No CPU temperature sensor is reachable. The daemon has forced a safe fallback fan "
        "speed because it cannot confirm the system is cool."
    ),
}

# The curated default chart series (refinement §7.3 / B-fork DEC-181): one CPU
# temp, one GPU temp, one mobo/case temp — kind-aware, so it can't be derived
# from the pure series keys alone (the model can't tell a CPU temp from a GPU
# temp by key). Daemon sends snake_case kinds; demo sends PascalCase.
_CURATED_CARD_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cpu_temp", ("CpuTemp", "cpu_temp")),
    ("gpu_temp", ("GpuTemp", "gpu_temp")),
    ("mobo_temp", ("MbTemp", "mb_temp")),
)


def trend_from_rate(rate: float | None) -> str:
    """Map a °C/s rate to a trend direction ("up"/"down"/"flat"/"").

    ``None`` (no rate yet) yields "" (no glyph). Pure/testable; mirrors the
    deadband so the Summary card's arrow is stable."""
    if rate is None:
        return ""
    if rate > _TREND_DEADBAND_C_PER_S:
        return "up"
    if rate < -_TREND_DEADBAND_C_PER_S:
        return "down"
    return "flat"


def resolve_card_sensor(
    category: str,
    kinds: tuple[str, ...],
    sensors: list[SensorReading],
    sensor_by_id: dict[str, SensorReading],
    bindings: dict[str, str],
) -> SensorReading | None:
    """The sensor that represents a card category: the card's binding if set and
    present, else the first sensor whose ``kind`` matches. Shared by the
    summary-card face (:func:`build_summary_card_vm`) and the curated-chart
    subset (:func:`curated_sensor_id`) so the chart's default line matches the
    card."""
    binding = bindings.get(category, "")
    if binding and binding in sensor_by_id:
        return sensor_by_id[binding]
    for s in sensors:
        if s.kind in kinds:
            return s
    return None


def curated_sensor_id(
    category: str,
    kinds: tuple[str, ...],
    sensors: list[SensorReading],
    bindings: dict[str, str],
) -> str | None:
    """The one sensor id that represents a card category in the curated chart
    subset (see :func:`resolve_card_sensor`)."""
    sensor = resolve_card_sensor(category, kinds, sensors, {s.id: s for s in sensors}, bindings)
    return sensor.id if sensor else None


def curated_chart_keys(sensors: list[SensorReading], bindings: dict[str, str]) -> set[str]:
    """The curated default series: CPU temp, GPU temp, and one mobo/case temp.
    Non-existent slots are simply dropped (``set_only_visible`` later intersects
    with known keys, so a filtered/absent sensor is harmless)."""
    keys: set[str] = set()
    for category, kinds in _CURATED_CARD_KINDS:
        sid = curated_sensor_id(category, kinds, sensors, bindings)
        if sid:
            keys.add(f"sensor:{sid}")
    return keys


def absent_member_ids(profile, present_ids: set[str]) -> set[str]:
    """Active-profile member fan ids that are *expected* but currently absent
    from the readings — these become OFFLINE tiles.

    Pure/testable. A present-but-idle member fan (one filtered out of the calm
    card view) stays in ``present_ids`` and is therefore simply omitted, never
    mislabelled OFFLINE — truthfulness over completeness (refinement §4.2)."""
    if profile is None:
        return set()
    member_ids = {m.member_id for c in profile.controls for m in c.members}
    return member_ids - present_ids


def fan_tooltip(fan: FanReading, headers: list[HwmonHeader]) -> str:
    """Build a tooltip for a fan row, including hwmon chip/driver context."""
    parts = [f"ID: {fan.id}"]
    if fan.source == "hwmon":
        header = next((h for h in headers if h.id == fan.id), None)
        if header and header.chip_name:
            parts.append(f"Chip: {header.chip_name}")
            g = lookup_chip_guidance(header.chip_name)
            if g:
                status = "mainline" if g.in_mainline else g.driver_package
                parts.append(f"Driver: {g.driver_name} ({status})")
            mode = {0: "DC", 1: "PWM"}.get(header.pwm_mode if header.pwm_mode is not None else -1)
            if mode:
                parts.append(f"Mode: {mode}")
            if not header.is_writable:
                parts.append("Status: read-only")
    return "\n".join(parts)


@dataclass(frozen=True)
class SummaryCardVM:
    """The face of a temperature summary card. ``status_class`` of ``None`` means
    "leave the card's status class untouched" (the binding-missing branch does
    not reclassify)."""

    value_text: str
    trend: str
    tooltip: str
    status_class: str | None
    range_min: float | None
    range_max: float | None


def build_summary_card_vm(
    category: str,
    kinds: tuple[str, ...],
    sensors: list[SensorReading],
    sensor_by_id: dict[str, SensorReading],
    bindings: dict[str, str],
    session_stats: SessionStatsTracker,
    warn: float = 0.0,
    crit: float = 0.0,
) -> SummaryCardVM | None:
    """Compute a summary card face from binding or auto-match by kind.

    Returns ``None`` when neither a bound nor a kind-matched sensor exists (the
    card is then left exactly as it was — never blanked to a stale value)."""
    binding = bindings.get(category, "")
    sensor = resolve_card_sensor(category, kinds, sensors, sensor_by_id, bindings)
    if sensor:
        freshness = sensor.freshness
        # Trend glyph only while the reading is live — a stale rate is not
        # trustworthy.
        trend = trend_from_rate(sensor.rate_c_per_s) if freshness == Freshness.FRESH else ""
        if freshness == Freshness.INVALID:
            value = f"{sensor.value_c:.1f}°C ⚠"
            tooltip = f"Stale reading ({sensor.age_ms / 1000:.0f}s old)"
            status: str | None = "CriticalChip"
        elif freshness == Freshness.STALE:
            value = f"{sensor.value_c:.1f}°C ⏱"
            tooltip = f"Aging reading ({sensor.age_ms / 1000:.1f}s old)"
            status = "WarningChip"
        else:
            value = f"{sensor.value_c:.1f}°C"
            tooltip = ""
            if crit and sensor.value_c > crit:
                status = "CriticalChip"
            elif warn and sensor.value_c > warn:
                status = "WarningChip"
            else:
                status = ""
        stats = session_stats.get(sensor.id)
        return SummaryCardVM(
            value_text=value,
            trend=trend,
            tooltip=tooltip,
            status_class=status,
            range_min=stats.min_c if stats else None,
            range_max=stats.max_c if stats else None,
        )
    if binding:
        return SummaryCardVM(
            value_text="—",
            trend="",
            tooltip="Bound sensor not available",
            status_class=None,
            range_min=None,
            range_max=None,
        )
    return None


@dataclass(frozen=True)
class FansCardVM:
    """The face of the Fans summary card."""

    value_text: str
    status_class: str
    detail_text: str


def build_fans_card_vm(display_fans: list[FanReading]) -> FansCardVM:
    """Fans card face: online/expected + average PWM/RPM. "Online" = a FRESH
    reading; a shortfall flags a warning."""
    total = len(display_fans)
    online = sum(1 for f in display_fans if f.freshness == Freshness.FRESH)
    rpms = [f.rpm for f in display_fans if f.rpm is not None]
    pwms = [f.last_commanded_pwm for f in display_fans if f.last_commanded_pwm is not None]
    parts = []
    if pwms:
        parts.append(f"avg {round(sum(pwms) / len(pwms))}% PWM")
    if rpms:
        parts.append(f"{round(sum(rpms) / len(rpms))} rpm")
    return FansCardVM(
        value_text=f"{online}/{total}",
        status_class="WarningChip" if total and online < total else "",
        detail_text=" · ".join(parts),
    )


@dataclass(frozen=True)
class SubsystemChipVM:
    """Text + QSS class for a discovery sub-label (OpenFan / hwmon)."""

    text: str
    css_class: str


@dataclass(frozen=True)
class HwmonBannerVM:
    """A hwmon info/warning banner to show, or ``None`` on the page to hide it."""

    kind: str  # "info" | "warning"
    message: str


@dataclass(frozen=True)
class CapabilitiesVM:
    """Everything the capabilities poll drives on the dashboard, sans side effects.

    ``gpu_title`` of ``None`` leaves the GPU card title unchanged; ``hwmon_banner``
    of ``None`` hides the banner; ``api_skew_message`` of ``None`` means no skew
    (the page hides that banner and clears the warning)."""

    openfan: SubsystemChipVM
    hwmon: SubsystemChipVM
    gpu_title: str | None
    hwmon_banner: HwmonBannerVM | None
    api_skew_message: str | None


def build_capabilities_vm(
    caps: Capabilities, expected_api_version: int = EXPECTED_API_VERSION
) -> CapabilitiesVM:
    """Derive the capabilities-driven dashboard state (chips, GPU title, hwmon
    banner, API-skew message). Pure — the page applies the side effects (repolish,
    warning add/remove, log)."""
    of = caps.openfan
    if of.present:
        openfan = SubsystemChipVM(f"OpenFan: detected ({of.channels} ch)", "SuccessChip")
    else:
        openfan = SubsystemChipVM("OpenFan: not detected", "PageSubtitle")

    hw = caps.hwmon
    if hw.present:
        hwmon = SubsystemChipVM(f"hwmon: detected ({hw.pwm_header_count} headers)", "SuccessChip")
    else:
        hwmon = SubsystemChipVM("hwmon: not detected", "PageSubtitle")

    # AMD takes priority when multiple vendors are present; otherwise Intel
    # (DEC-121) then NVIDIA (DEC-204). No match → leave the title unchanged.
    gpu_title: str | None = None
    if caps.amd_gpu.present:
        gpu_title = f"{caps.amd_gpu.display_label} Temp"
    elif caps.intel_gpu.present:
        gpu_title = f"{caps.intel_gpu.display_label} Temp"
    elif caps.nvidia_gpu.present:
        gpu_title = f"{caps.nvidia_gpu.display_label} Temp"

    if not hw.present:
        banner: HwmonBannerVM | None = HwmonBannerVM(
            "info",
            "No motherboard fan headers detected. "
            "Check the Hardware page for driver and BIOS guidance.",
        )
    elif hw.present and not hw.write_support:
        banner = HwmonBannerVM(
            "warning",
            "Motherboard fan headers detected but all are read-only. "
            "Check BIOS fan settings or driver status on the Hardware page.",
        )
    else:
        banner = None

    # API-version-skew guard: GUI and daemon are independently packaged (AUR), so
    # a user can upgrade one without the other. Re-evaluated on every reconnect.
    if caps.api_version != expected_api_version:
        api_skew: str | None = (
            f"Daemon API v{caps.api_version} differs from this GUI's expected "
            f"v{expected_api_version}. Align your control-ofc-daemon and "
            "control-ofc-gui package versions — some features may misbehave."
        )
    else:
        api_skew = None

    return CapabilitiesVM(
        openfan=openfan,
        hwmon=hwmon,
        gpu_title=gpu_title,
        hwmon_banner=banner,
        api_skew_message=api_skew,
    )


def safety_detail_text(
    thermal: str, state_label: str, cpu_values: list[float], override_count: int
) -> str:
    """Read-only thermal-safety summary for the thermal chip's click detail.

    Surfaces only data we actually have — state, a plain reason, the current
    hottest CPU sensor, and any active manual overrides. It does NOT invent a
    "last safe value" or a persisted transition timestamp (neither is
    daemon-provided). The caller resolves ``state_label`` (a presentation
    constant) so this stays Qt-free."""
    lines = [
        f"State: {state_label}",
        "",
        _THERMAL_REASONS.get(thermal, "Current daemon thermal state."),
    ]
    if cpu_values:
        lines += ["", f"Hottest CPU sensor: {max(cpu_values):.1f}°C"]
    if override_count:
        lines += [
            "",
            f"{override_count} manual override{'s' if override_count != 1 else ''} active.",
        ]
    return "\n".join(lines)
