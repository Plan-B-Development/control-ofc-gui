"""Qt-free view-model for the Dashboard page (DEC-219, Phase 7.2).

Pure computation carved out of :class:`DashboardPage`: fan tooltips, capability
chips / banners, and the thermal-safety detail text. No Qt imports — the page
renders these plain dataclasses onto its (already-decomposed) widget components.

DEC-222 removed the summary-card and fans-card faces with the cards themselves,
and moved chart-series curation to :func:`series_selection.default_series_keys`
(it was chart logic wearing card-era names).

Keeping this layer Qt-free makes the dashboard's display logic unit-testable
without constructing widgets, mirroring the S2-S5 ``services/*_view`` modules.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from control_ofc.api.models import (
    Capabilities,
)
from control_ofc.constants import EXPECTED_API_VERSION

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
        # DEC-269: "reachable" was true when the only trigger was a sensor that
        # had vanished. Since DEC-267 a sensor that is still listed but has
        # STOPPED UPDATING also reaches this state — so the old wording appeared
        # directly above a "Hottest CPU sensor: 62.0°C" line drawn from the very
        # list it denied. Phrased to be true of both triggers without needing a
        # daemon-version gate.
        "No current CPU temperature reading. The daemon has forced a safe fallback fan "
        "speed because it cannot confirm the system is cool — a reading may still be "
        "listed, but it has stopped updating."
    ),
}


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

    ``hwmon_banner``
    of ``None`` hides the banner; ``api_skew_message`` of ``None`` means no skew
    (the page hides that banner and clears the warning)."""

    openfan: SubsystemChipVM
    hwmon: SubsystemChipVM
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
        hwmon_banner=banner,
        api_skew_message=api_skew,
    )


def cpu_values_for_display(
    readings: Iterable[tuple[float, bool]],
) -> tuple[list[float], bool]:
    """Split CPU readings into the tier the detail text should describe.

    ``readings`` is ``(value_c, is_fresh)`` per CPU sensor. Returns the values
    the label will describe, plus whether they are stale.

    Mirrors the daemon's ``hottest_cpu_reading`` (DEC-269): fresh readings win
    outright, and stale ones stand in only when *nothing* is fresh. Resolving
    the value and the staleness flag together is the point — computing them
    separately let ``max()`` range over stale and fresh sensors while the flag
    required *all* of them to be stale, so on a multi-CCD Ryzen a stale hottest
    die printed under the confident "Hottest CPU sensor" label.
    """
    # Materialise first: this scans `readings` twice, and a generator caller
    # would otherwise find it empty on the second pass and report every sensor
    # stale.
    pairs = list(readings)
    fresh = [v for v, is_fresh in pairs if is_fresh]
    if fresh:
        return fresh, False
    stale = [v for v, is_fresh in pairs if not is_fresh]
    return stale, bool(stale)


def safety_detail_text(
    thermal: str,
    state_label: str,
    cpu_values: list[float],
    override_count: int,
    *,
    cpu_reading_is_stale: bool,
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
        # DEC-269: hedge on the READING'S OWN AGE, not on `thermal_state`.
        #
        # Keying it on `no_sensor_fallback` was wrong in both directions. The
        # daemon reports `emergency` for a latched emergency running on a stale
        # reading — so the un-hedged label appeared in exactly the state where
        # the value is guaranteed stale — while `no_sensor_fallback` can also be
        # reached with the sensor genuinely gone, where there is no value to
        # print at all. Age is the fact that actually decides it, the GUI already
        # has it, and it needs no daemon-version gate.
        label = "Last known CPU sensor" if cpu_reading_is_stale else "Hottest CPU sensor"
        lines += ["", f"{label}: {max(cpu_values):.1f}°C"]
    if override_count:
        lines += [
            "",
            f"{override_count} manual override{'s' if override_count != 1 else ''} active.",
        ]
    return "\n".join(lines)
