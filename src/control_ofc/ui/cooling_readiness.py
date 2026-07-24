"""Pure mapping for the merged Cooling Hardware Readiness page (DEC-207).

Maps the daemon's structured readiness items (from ``GET
/inventory/hardware-readiness``) into the reusable
:class:`~control_ofc.ui.readiness_merge.MergedReadinessItem` model, adding two
GUI-owned things the daemon list does not carry:

* a **page-specific action** per finding — a deep-link to Settings ▸ Preferred
  sensors, an in-surface jump to the on-page Super-I/O details section, a switch
  to the existing PWM-verify / Sensors tabs — never an inert string; and
* a **"Learn how" documentation link** into the project's readiness guide.

Daemon-supplied strings stay in ``plain_detail`` (the view renders them as
PlainText); the doc links are GUI-authored (trusted). It also assigns each finding
to one of the four **hardware-checks groups** the compact checklist renders.

No Qt here, so this is trivially unit-testable. This is the Readiness ⊕ Super-I/O
composition; the separate Readiness ⊕ Troubleshooting ``merge_readiness()`` engine
(built for a different, deferred merge) was retired in the 2026-07-21 audit
dead-code sweep (DEC-224) — ``readiness_merge`` now carries only the shared
model types this module builds.
"""

from __future__ import annotations

from control_ofc.api.models import HardwareReadiness, ReadinessItem
from control_ofc.ui.readiness_merge import (
    ACTION_DEEP_LINK,
    ACTION_IN_SURFACE,
    ACTION_TAB_SWITCH,
    ActionSpec,
    MergedReadinessItem,
)

# Severity → sort rank (higher = more severe). Kept local so this module does not
# depend on readiness_merge internals; matches the daemon vocabulary.
_SEV_RANK = {"ok": 0, "info": 1, "warning": 2, "critical": 3}


def _normalise(severity: str) -> str:
    """Normalise a severity to the daemon vocabulary (``ok|info|warning|critical``)."""
    s = (severity or "info").lower()
    return "warning" if s == "warn" else s


# ── Documentation links (the existing doc_url/doc_title mechanism) ──────────
# The project's own readiness guide on GitHub; the view renders these as a themed
# <a href> "Learn how" link. Anchors match the headings in
# docs/24_Cooling_Hardware_Readiness_Guide.md.
_DOC_BASE = (
    "https://github.com/Plan-B-Development/control-ofc-gui/blob/main/docs/"
    "24_Cooling_Hardware_Readiness_Guide.md"
)


def _doc(anchor: str, title: str) -> tuple[str, str]:
    return (f"{_DOC_BASE}#{anchor}", title)


# code → ("Learn how" url, link title). Codes not listed get no doc link (e.g. the
# positive "ok" checks, which need no remediation guidance).
_DOC: dict[str, tuple[str, str]] = {
    "cpu_sensor_missing": _doc(
        "no-usable-cpu-temperature-source", "No usable CPU temperature source"
    ),
    "cpu_default_low_confidence": _doc(
        "selecting-a-preferred-sensor", "Selecting a preferred sensor"
    ),
    "selected_cpu_sensor_missing": _doc(
        "selecting-a-preferred-sensor", "Selecting a preferred sensor"
    ),
    "selected_mb_sensor_missing": _doc(
        "selecting-a-preferred-sensor", "Selecting a preferred sensor"
    ),
    "no_pwm_controls": _doc("loading-an-in-kernel-super-io-driver", "Loading a Super-I/O driver"),
    "pwm_read_only": _doc("pwm-detected-but-not-verified", "PWM detected but not verified"),
    "pwm_control_unverified": _doc("fan-control-verification", "Fan-control verification"),
    "superio_driver_unloaded": _doc(
        "loading-an-in-kernel-super-io-driver", "Loading a Super-I/O driver"
    ),
    "superio_acpi_conflict": _doc("acpi-io-port-conflicts", "ACPI I/O-port conflicts"),
    "sensors_unavailable": _doc("quarantined-or-unclassified-sensors", "Quarantined sensors"),
    "unknown_sensors_present": _doc("quarantined-or-unclassified-sensors", "Unclassified sensors"),
}


# ── Actions (deep-link / in-surface / tab-switch — never an inert string) ────
# Opaque targets the Hardware page routes on:
#   "preferred_cpu" | "preferred_mb" → cross-page deep-link to Settings
#   "superio"                        → in-surface scroll to the Super-I/O section
#   "pwm_verify"                     → switch to the existing PWM-verify workflow
#   "sensors"                        → switch to the Overview page (sensor table)
def _action_for(code: str) -> ActionSpec:
    if code in ("cpu_sensor_missing", "cpu_default_low_confidence", "selected_cpu_sensor_missing"):
        return ActionSpec(ACTION_DEEP_LINK, "Pick a CPU sensor", "preferred_cpu")
    if code == "selected_mb_sensor_missing":
        return ActionSpec(ACTION_DEEP_LINK, "Pick a motherboard sensor", "preferred_mb")
    if code in ("pwm_control_unverified", "pwm_read_only"):
        return ActionSpec(ACTION_TAB_SWITCH, "Test PWM control", "pwm_verify")
    if code in ("no_pwm_controls", "superio_driver_unloaded", "superio_acpi_conflict"):
        return ActionSpec(ACTION_IN_SURFACE, "View Super-I/O details", "superio")
    if code in ("monitor_only_fans_present", "unknown_sensors_present", "sensors_unavailable"):
        return ActionSpec(ACTION_TAB_SWITCH, "View sensors", "sensors")
    return ActionSpec()  # ok items (cpu_sensor_present / pwm_controls_present) etc.


# ── Hardware-checks groups (the compact checklist, §3 of the page) ──────────
GROUP_TEMP = "Temperature monitoring"
GROUP_FANS = "Fan monitoring and control"
GROUP_SUPERIO = "Super-I/O and kernel support"
GROUP_SENSORS = "Sensor configuration"
# Stable render order for the grouped checklist.
GROUP_ORDER = [GROUP_TEMP, GROUP_FANS, GROUP_SUPERIO, GROUP_SENSORS]

_GROUP: dict[str, str] = {
    "cpu_sensor_missing": GROUP_TEMP,
    "cpu_sensor_present": GROUP_TEMP,
    "cpu_default_low_confidence": GROUP_TEMP,
    "selected_cpu_sensor_missing": GROUP_TEMP,
    "no_pwm_controls": GROUP_FANS,
    "pwm_controls_present": GROUP_FANS,
    "pwm_read_only": GROUP_FANS,
    "pwm_control_unverified": GROUP_FANS,
    "monitor_only_fans_present": GROUP_FANS,
    "superio_driver_unloaded": GROUP_SUPERIO,
    "superio_acpi_conflict": GROUP_SUPERIO,
    "sensors_unavailable": GROUP_SENSORS,
    "unknown_sensors_present": GROUP_SENSORS,
    "selected_mb_sensor_missing": GROUP_SENSORS,
}


def group_for(code: str) -> str:
    """The hardware-checks group a readiness code belongs to (an unrecognised code
    falls into Sensor configuration rather than being dropped)."""
    return _GROUP.get(code, GROUP_SENSORS)


def _item_from(it: ReadinessItem) -> MergedReadinessItem:
    sev = _normalise(it.severity)
    plain = it.detail or ""
    if it.recommended_action:
        plain = f"{plain}\n→ {it.recommended_action}".strip()
    doc_url, doc_title = _DOC.get(it.code, ("", ""))
    return MergedReadinessItem(
        code=it.code,
        severity=sev,
        rank=_SEV_RANK.get(sev, 1),
        headline=it.summary or it.code,
        component=it.component or "",
        plain_detail=plain,
        doc_url=doc_url,
        doc_title=doc_title,
        action=_action_for(it.code),
        affects_safety=it.affects_safety,
        blocks_control=it.blocks_control,
        blocks_monitoring=it.blocks_monitoring,
        reboot_may_be_required=it.reboot_may_be_required,
        source="daemon",
        is_ok=(sev == "ok"),
    )


def build_readiness_items(hw: HardwareReadiness | None) -> list[MergedReadinessItem]:
    """Map the daemon readiness items into the reusable actionable-item model,
    sorted most-severe-first (``ok`` items last, stable within a tier). Pure."""
    items = [_item_from(it) for it in (hw.items if hw else [])]
    items.sort(key=lambda m: (m.is_ok, -m.rank))
    return items
