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
from .daemon_features import requires_daemon

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


def daemon_protects_pumps(capabilities: Capabilities | None) -> bool:
    """Whether this daemon has a pump-protection model at all (DEC-311).

    The wizard-level question, as distinct from `header_is_pump_protected`'s
    per-header one: *can* this daemon decline to stop a pump? A pre-2.28.0
    daemon has no role model, so it drives every identified fan to 0 — pumps
    included — and any copy promising otherwise is a lie.
    """
    return bool(capabilities is not None and getattr(capabilities.control, "header_roles", False))


def pump_identify_warning(capabilities: Capabilities | None) -> str:
    """The Fan Wizard's up-front line about what happens to a pump (`UDOC-i`).

    Rich-text, one bullet, gated on the capability — because the guarantee it
    describes is the daemon's, not the GUI's.

    This lives here rather than in the wizard because it is the same rule as
    `daemon_protects_pumps`, one sentence further on, and the wizard is not its
    only possible consumer. It exists at all because the intro page stated the
    protected outcome **unconditionally**, in a static label built once in
    `__init__`, while every per-fan string a few hundred lines below correctly
    went through the gate. A user on an older daemon was reassured on page one
    that their pump would keep running, and then it was stopped.
    """
    if daemon_protects_pumps(capabilities):
        return (
            "• A <b>pump is never stopped</b> — its speed is shifted instead, so "
            "coolant keeps flowing. Watch the RPM reading or listen for the change."
        )
    return (
        "• <b>This daemon cannot protect a pump</b> "
        f"{requires_daemon('pump_protection')}: every fan it identifies is "
        "stopped briefly, <b>including a pump</b>. If you run a liquid cooler, "
        "update the daemon before using this wizard."
    )


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

    **Since DEC-316 this reconstruction is the FALLBACK, not the primary answer.**
    A daemon >= 2.31.0 reports ``stop_permitted`` per header, computed from the
    same union on the side that actually enforces it, and that is authoritative
    when present. The reconstruction below still runs for older daemons and
    whenever the field is absent — which is why it is kept rather than deleted,
    and why ``None`` must never be read as ``False``: a defaulted "stoppable"
    would offer to stop a real pump.
    """
    if header is None:
        return False
    # Prefer what the daemon says it will do over what we can infer it will do,
    # and check it BEFORE the capability gate.
    #
    # `stop_permitted` is self-describing — absent means "this daemon did not
    # say" — which is exactly why `docs/08` states it needs no capability flag.
    # Gating it behind `header_roles` would contradict that, and would fail in
    # the unsafe direction: a header the daemon reports as unstoppable would be
    # read as stoppable whenever that flag was absent. Today both flags ship
    # together, so this is unreachable; nothing enforces that pairing, and the
    # cost of not relying on it is one reordered branch.
    #
    # `None` falls through to the reconstruction below — it is NOT a "no".
    if header.stop_permitted is not None:
        return not header.stop_permitted
    # The reconstruction DOES need the gate: a pre-2.28.0 daemon has no role
    # model at all, so there is nothing to reconstruct from.
    if not daemon_protects_pumps(capabilities):
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


def header_effective_floor_pct(
    header: HwmonHeader | None,
    capabilities: Capabilities | None,
) -> int | None:
    """The duty floor the daemon will actually enforce for this header, or None.

    Daemon-first (DEC-316). A daemon >= 2.31.0 computes this from the resolved
    device policy clamped by the absolute pump backstop and publishes it as
    ``effective_min_pwm_pct``; that is the number to show, because it is the one
    the engine will enforce. Re-deriving it client-side is what this phase
    exists to stop: the GUI had no way to know about a policy at all, so any
    number it computed would silently diverge the moment a validated device
    policy shipped.

    ``None`` means "not known" and callers must render it as such rather than
    substituting 0 — that is the whole reason the wire field is optional.

    **Daemons 2.31.0 to 2.35.3 over-claim for one case (`WIRE-b`, fixed in
    2.35.4).** A radiator or auxiliary member of a cooling device resolved that
    *device's* policy — ``generic_pump`` by default — and so reported 30, while
    no enforcement site applies a floor to it. Such a header reports a non-zero
    floor **and** ``stop_permitted: true`` together, a pairing a 2.35.4+ daemon
    cannot produce. This function deliberately does **not** compensate for it:
    second-guessing a self-describing safety field client-side is the failure
    direction this whole module exists to avoid, and suppressing a floor the GUI
    merely believes to be decorative is exactly how a real one would get hidden.

    The fallback keeps older daemons honest: with no reported value, a
    pump-protected header still shows the hard 30% floor those daemons enforce.
    An ordinary header gets ``None`` rather than 0, because the daemon applies
    no per-role floor of its own there — the control's own ``minimum_pct``
    governs, and claiming 0 would misrepresent that as "no floor at all".
    """
    if header is None:
        return None
    if header.effective_min_pwm_pct is not None:
        return header.effective_min_pwm_pct
    if header_is_pump_protected(header, capabilities):
        # Deferred import: `profile_service` is heavy and imports widely, and
        # this is the only value needed from it. Referenced rather than
        # restated so the two cannot drift — CLAUDE.md's "a threshold spelled
        # into a name drifts every time the threshold moves".
        from .profile_service import CONTROL_ROLE_CPU_PUMP, ROLE_MINIMUM_PCT

        return int(ROLE_MINIMUM_PCT[CONTROL_ROLE_CPU_PUMP])
    return None
