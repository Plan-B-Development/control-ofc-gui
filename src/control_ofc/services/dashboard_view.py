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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from control_ofc.api.models import (
    Capabilities,
    RuntimeConfigDegraded,
)
from control_ofc.constants import EXPECTED_API_VERSION
from control_ofc.services.cooling_watch import (
    EMERGENCY_REACH,
    NO_SENSOR_REASON,
    RECOVERY_REASON,
    emergency_opening,
)

# Plain-language rendering of the DEC-321 `runtime_config_degraded.reason`
# token. An unrecognised token renders as itself rather than being dropped —
# the daemon owns the vocabulary and may extend it, and "the config failed to
# load for a reason this GUI has not heard of" is still worth saying.
_RUNTIME_CONFIG_REASONS: dict[str, str] = {
    "unreadable": "the file could not be read",
    "malformed": "the file is not valid for this daemon version",
}


def runtime_config_degraded_message(degraded: RuntimeConfigDegraded | None) -> str | None:
    """The Dashboard banner text for a daemon running on fallback settings, or
    ``None`` when the config loaded cleanly (`WIRE-a`, DEC-321).

    **[SAFETY].** ``phase`` decides what the user has actually lost, and the two
    cases must not share one message. Only the boot load seeds every
    runtime-mutable key, so a ``startup`` failure drops ``header_roles`` — a pump
    role the user assigned by hand is gone, and with it that header's 30% floor,
    its stop exemption and its pump-safe identify. A ``reload`` failure commits
    only ``profile_search_dirs``, so roles survive as startup established them;
    saying otherwise would be a false alarm about pump protection.

    An ``update`` record (daemon ≥ 2.51.0, `TS-r`) means a ``POST /config/*``
    setter found the file unreadable, kept a copy of it and replaced it. That
    daemon writes the replacement — carrying the header roles and cooling devices
    it is running with — before publishing the record, so this is the one phase
    that may say the roles were kept — truthfully,
    because only a daemon that keeps the more severe record emits it, so an
    ``update`` record also proves boot loaded cleanly. What it lost is every
    other setting that existed only in the old file.

    An **unknown** phase gets the cautious wording: it names the possibility
    without asserting the loss, because over-warning erodes the banner and
    under-warning hides a real one.

    ``kept_as`` (daemon ≥ 3.6.0, `TS-at`) names the copy a setter kept. On a
    ``startup`` record it changes the remedy: a setter since the failed boot
    replaced the file with one holding no roles, so "repair the file and restart"
    would send the user to a healthy file, and a restart would then clear the
    banner with the roles still only in the copy. The copy is the file to repair.

    ``detail`` is deliberately absent from the message. It is verbatim daemon
    prose and can be a multi-line TOML parse error; the page logs it instead, the
    same split the API-skew guard uses.
    """
    if degraded is None:
        return None

    reason = _RUNTIME_CONFIG_REASONS.get(degraded.reason, degraded.reason.strip())
    cause = f" ({reason})" if reason else ""
    where = f" — {degraded.path}" if degraded.path.strip() else ""
    remedy = "Repair the file and restart control-ofc-daemon."
    kept = degraded.kept_as.strip()

    if degraded.phase == "reload":
        # **Deliberately does NOT say "header roles are unaffected".** On daemons
        # 2.34.0-2.35.x `phase` is latest-wins — `apply_config_reload` overwrites
        # the slot unconditionally — so a FAILED reload replaces an earlier
        # startup record and the roles may already be gone while the record reads
        # `reload`, reachable by editing a broken file wrongly and reloading.
        #
        # Daemon 2.36.0 fixed that at source (`WIRE-ao`, DEC-330): a `startup`
        # record now survives a failed reload. **This message does not change**,
        # because nothing in this payload says which daemon sent it, and the safe
        # reading costs nothing. It still says something narrower than the startup
        # case: a reload never *restores* roles.
        return (
            f"Daemon settings failed to reload{cause}{where}. Reloading does not restore "
            f"fan header roles, so if the file was already bad at startup, roles you "
            f"assigned by hand are still not in effect and those headers have no 30% "
            f"pump floor. {remedy}"
        )
    if degraded.phase == "update":
        kept_where = f"as {kept}" if kept else "beside it (named with .invalid- and a timestamp)"
        return (
            f"A setting was saved while the daemon's settings file could not be "
            f"read{cause}{where}. control-ofc-daemon kept a copy of the unreadable "
            f"file {kept_where} and replaced it with a new one. The fan header roles "
            f"and cooling devices it was running with were kept, and with them any 30% "
            f"pump floor a role gives — but every other setting that was only in the old "
            f"file is not in the new one. Copy what you need back from the kept copy, "
            f"then restart control-ofc-daemon."
        )
    if degraded.phase == "startup" and kept:
        # The file at `path` is now the daemon's own replacement; restarting on it
        # would load cleanly and clear this with the roles still lost. Stop first,
        # so no setter rewrites the file between the repair and the restart.
        target = degraded.path.strip() or "the settings file"
        return (
            f"Daemon settings failed to load{cause}{where}. control-ofc-daemon is "
            f"running on built-in defaults, so any fan header roles you assigned by "
            f"hand are NOT in effect — those headers lose their 30% pump floor and "
            f"can be stopped by fan identify. A setting saved since then replaced that "
            f"file with a new one, so your old settings, roles included, are now only "
            f"in the copy it kept: {kept}. Restarting alone would not bring them back, "
            f"and saving settings will not clear this. Stop control-ofc-daemon, repair "
            f"{kept} and move it back over {target}, then start it."
        )
    if degraded.phase == "startup":
        return (
            f"Daemon settings failed to load{cause}{where}. control-ofc-daemon is "
            f"running on built-in defaults, so any fan header roles you assigned by "
            f"hand are NOT in effect — those headers lose their 30% pump floor and "
            f"can be stopped by fan identify. Saving settings will not clear this. "
            f"{remedy}"
        )
    # Unknown or absent phase: state the degradation and flag the risk without
    # claiming a loss that may not have happened.
    return (
        f"Daemon settings failed to load{cause}{where}. control-ofc-daemon is running "
        f"on built-in defaults, so settings you changed — possibly including fan "
        f"header roles you assigned by hand, and their 30% pump floor — may not be in "
        f"effect. Saving settings will not clear this. {remedy}"
    )


# Plain-language reason per daemon thermal_state, for the Safety detail. Kept
# qualitative (no hardcoded thresholds) so it can't drift from the daemon.
_THERMAL_REASONS: dict[str, str] = {
    "normal": "Cooling is operating normally; the daemon is following the active profile.",
    "recovery": RECOVERY_REASON,
    # G159 (`DC-j`): "has forced all controllable fans to 100%" said more than
    # the daemon does — GPU fans are outside the force (DEC-130) and keep their
    # curve (DEC-399), and DEC-371 says a thermal state is never proof a fan
    # was written. This names the state and its reach, not an outcome. The reach
    # is every OpenFan channel and writable hwmon header, AIO/USB devices included.
    # DEC-443: the opening clause names the trigger and follows
    # `emergency_causes` (see `_emergency_reason`); this entry is the CPU one,
    # which is also what a daemon before 3.0.0 means by "emergency".
    "emergency": emergency_opening(()) + EMERGENCY_REACH,
    # DEC-269/DEC-382: the wording and its reasons live in `cooling_watch`, which
    # the thermal alert reads too (DEC-459).
    "no_sensor_fallback": NO_SENSOR_REASON,
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
    (the page hides that banner and clears the warning). ``openfan`` of ``None``
    hides the chip the same way (`OFN-g`) — the controller is optional hardware,
    so its absence is not a subsystem status worth a permanent chip."""

    openfan: SubsystemChipVM | None
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
    openfan = (
        SubsystemChipVM(f"OpenFan: detected ({of.channels} ch)", "SuccessChip")
        if of.present
        else None
    )

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


def _emergency_reason(causes: Sequence[str]) -> str:
    """The emergency reason with its trigger named (DEC-443): a coolant
    emergency must not be explained as a CPU one."""
    return emergency_opening(causes) + EMERGENCY_REACH


def safety_detail_text(
    thermal: str,
    state_label: str,
    cpu_values: list[float],
    override_count: int,
    *,
    cpu_reading_is_stale: bool,
    emergency_causes: Sequence[str] = (),
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
        _emergency_reason(emergency_causes)
        if thermal == "emergency"
        else _THERMAL_REASONS.get(thermal, "Current daemon thermal state."),
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
