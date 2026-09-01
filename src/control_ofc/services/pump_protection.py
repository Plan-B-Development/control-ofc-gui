"""Client-side reconstruction of the daemon's pump-protection predicate.

Extracted from ``ui/widgets/fan_wizard.py`` when a second surface needed it
(AIO-MB Phase 3). That extraction is the point, not a tidy-up: CLAUDE.md records
"a rule that lives inside one consumer is a rule the other consumers cannot
follow" as a repeat failure here — the accessible-naming rule was refined across
three ADRs while sitting in a private method on one page, leaving fourteen other
surfaces unfixed. Re-deriving this locally in the characterisation dialog would
have been the same bug in a new coat, on a **safety** predicate this time.

There is exactly one rule and one place it lives. Qt-free, so it is testable
headlessly and usable from services as well as widgets.
"""

from __future__ import annotations

from ..api.models import Capabilities, HwmonHeader
from ..knowledge.hwmon_label_resolver import is_placeholder_hwmon_label

# Mirrors the daemon's `classify_header_role` label branches (`hwmon/roles.rs`).
# A label matching any of these classifies the header BEFORE chip mapping is
# consulted, so the liquid-cooler channel-1 -> pump mapping never runs for it.
CPU_FAN_LABEL_PREFIXES = ("cpu_fan", "cpufan")
CHASSIS_LABEL_HINTS = ("cha_fan", "chafan", "sys_fan", "sysfan", "chassis")


def label_outranks_chip_mapping(lowered_label: str) -> bool:
    """True when the daemon would classify this label without reaching chip mapping."""
    normalised = lowered_label.replace("-", "_").replace(" ", "_").replace(".", "_")
    if normalised.startswith(CPU_FAN_LABEL_PREFIXES):
        return True
    return any(hint in normalised for hint in CHASSIS_LABEL_HINTS)


def header_is_pump_protected(
    header: HwmonHeader | None,
    capabilities: Capabilities | None,
) -> bool:
    """Whether the daemon will protect this header as a pump (DEC-311/312).

    Reconstructs the daemon's ``header_is_pump_protected``, which is a **union**
    of the inferred role and the resolved one — never the wire ``role`` field on
    its own. ``role`` is the *display* role, and a user assignment fully
    substitutes for inference there, downgrades included: assign ``chassis_fan``
    to a header the hardware labels ``PUMP`` and ``role`` reads ``chassis_fan``
    while the daemon still refuses to stop or under-drive it. Reading ``role``
    alone is therefore a bug in any safety or truthfulness decision.

    Three terms, matching the daemon's:

    * the resolved role says ``pump``;
    * the RAW daemon label carries a pump hint. Raw, never the resolved display
      name: a user *alias* of "Pump" on an unlabelled header is invisible to the
      daemon, so trusting it would claim protection the daemon does not apply —
      the unsafe direction. The DEC-229 synthesised ``pwmN`` placeholder is
      skipped for the same reason it always is.
    * the header is a liquid-cooler channel 1, which the daemon maps to a pump —
      but ONLY where no recognised non-pump label outranks that. The daemon
      consults the label first and reaches chip mapping only when the label says
      nothing it knows, so a cooler channel 1 labelled ``CPU_FAN1`` infers
      ``cpu_fan``.

    Gated on ``control.header_roles``: a pre-2.28.0 daemon has no role model at
    all, so nothing is protected and any copy claiming otherwise would lie.

    Only hwmon headers can be pumps — OpenFan channels have no header, and GPU
    fans are never pumps.
    """
    if capabilities is None or not getattr(capabilities.control, "header_roles", False):
        return False
    if header is None:
        return False
    if header.role == "pump":
        return True
    raw_label = header.label or ""
    if is_placeholder_hwmon_label(raw_label, header.pwm_index):
        raw_label = ""
    lowered = raw_label.lower()
    if "pump" in lowered:
        return True
    if label_outranks_chip_mapping(lowered):
        return False
    return bool(header.is_aio) and header.pwm_index == 1
