"""View-model for a cooling device's topology (AIO-MB Phase 4, DEC-316).

Qt-free by design — the widget that renders this is a thin renderer over
``CoolingDeviceView``, in the ``services/*_view.py`` pattern this project uses
for every page derivation. Phase 4 built the model; **Phase 6 (DEC-318) added the
card** — ``ui/widgets/cooling_device_card.py`` — so nothing here decides spacing,
colour or layout. Phase 6 also added the live-telemetry and pump-strategy fields.

What it does decide is the part that is not presentation taste:

* **A floor is shown only where it is known.** ``effective_min_pwm_pct`` is
  optional on the wire precisely so that "this daemon did not say" is
  distinguishable from "zero", and a pump rendered as ``0%`` would be the exact
  truthfulness bug the field exists to prevent. Unknown renders as an em dash.
* **Stoppability comes from the daemon where the daemon offers it.** Both go
  through ``services/pump_protection`` rather than being re-derived here —
  there is one rule and one place it lives (DEC-276's lesson, applied to a
  safety predicate).
* **Unrecognised tokens render.** A ``kind`` this GUI does not know is shown
  humanised rather than dropped (the 273-i rule), because a newer daemon adding
  a device type must not make a user's cooler vanish from their own screen.
* **Absent coolant telemetry is normal, not a fault.** A motherboard-connected
  AIO has none; the brief calls that out explicitly. It is reported in a neutral
  state, never as a warning.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..api.models import Capabilities, CoolingDevice, FanReading, HwmonHeader
from .header_inspector_view import requested_pct
from .pump_protection import header_effective_floor_pct, header_is_pump_protected

#: Shown wherever a value is genuinely unknown. Never substitute 0 or "None".
UNKNOWN_TEXT = "—"

#: Copy for the coolant row when the machine exposes no coolant sensor. This is
#: the normal case for a motherboard-connected AIO and the wording must not
#: imply a fault or a missing step.
NO_COOLANT_NOTE = "No coolant sensor on this machine — CPU temperature is used instead."

_KIND_LABELS = {
    "aio_liquid": "Liquid cooler (AIO)",
    "air_cooler": "Air cooler",
    "custom_loop": "Custom loop",
    "unknown": "Cooling device",
}

_ROLE_LABELS = {
    "pump": "Pump",
    "radiator": "Radiator fan",
    "auxiliary": "Auxiliary fan",
}


def _humanise_token(token: str) -> str:
    """Render an unrecognised wire token readably rather than dropping it."""
    return token.replace("_", " ").strip().capitalize() or UNKNOWN_TEXT


def _fmt_pct(value: int | None) -> str:
    return UNKNOWN_TEXT if value is None else f"{value}%"


@dataclass(frozen=True)
class CoolingMemberRow:
    """One PWM header belonging to a cooling device."""

    member_id: str
    label: str
    #: "Pump" | "Radiator fan" | "Auxiliary fan"
    role_label: str
    #: The daemon-enforced floor, or ``UNKNOWN_TEXT`` when it did not say.
    floor_text: str
    #: True only when the daemon (or the fallback reconstruction) says this
    #: header may be driven to 0. A pump is never stoppable.
    stop_permitted: bool
    #: True when this member id matched no discovered header — a stale device
    #: describing hardware that has since moved or gone.
    missing: bool = False
    #: "ok" | "warn" | "critical" | "neutral", the shared chip vocabulary.
    state: str = "ok"
    # ── Live telemetry (AIO-MB Phase 6) ──────────────────────────────────────
    # Joined here rather than in the widget so the card stays a thin renderer
    # over ONE view-model, per the house "view-model + thin renderer" rule.
    #: e.g. "1,284 RPM", or "—" when the header has no tach.
    rpm_text: str = UNKNOWN_TEXT
    #: The duty the daemon COMMANDED, never the readback (§6).
    requested_text: str = UNKNOWN_TEXT
    #: The hardware readback. Kept separate from ``requested_text`` on purpose:
    #: collapsing them hides a write failure, a BIOS reclaim and a device-side
    #: override behind one number.
    readback_text: str = UNKNOWN_TEXT


@dataclass(frozen=True)
class CoolingDeviceView:
    """Render-ready topology for one cooling device."""

    device_id: str
    name: str
    kind_label: str
    pump: CoolingMemberRow | None = None
    radiators: list[CoolingMemberRow] = field(default_factory=list)
    auxiliaries: list[CoolingMemberRow] = field(default_factory=list)
    #: Advisory control sensor, resolved to a display label where possible.
    sensor_label: str = UNKNOWN_TEXT
    coolant_available: bool = False
    coolant_note: str = NO_COOLANT_NOTE
    policy_label: str = ""
    #: Rows describing hardware this daemon cannot find, if any.
    missing_members: list[str] = field(default_factory=list)
    state: str = "ok"
    # ── AIO-MB Phase 6 ───────────────────────────────────────────────────────
    #: Live temperature of the control sensor, e.g. "54°C".
    sensor_temp_text: str = UNKNOWN_TEXT
    #: Live coolant temperature where genuinely available.
    coolant_temp_text: str = UNKNOWN_TEXT
    #: "Automatic · CPU Package" | "Fixed" | "Not controlled". DERIVED from the
    #: active profile's curve shape, never stored — see ``pump_strategy_text``.
    strategy_text: str = UNKNOWN_TEXT

    @property
    def all_rows(self) -> list[CoolingMemberRow]:
        """Every member row in pump -> radiator -> auxiliary order."""
        rows: list[CoolingMemberRow] = []
        if self.pump is not None:
            rows.append(self.pump)
        rows.extend(self.radiators)
        rows.extend(self.auxiliaries)
        return rows


def _member_row(
    member_id: str,
    role: str,
    *,
    headers: dict[str, HwmonHeader],
    capabilities: Capabilities | None,
    display_name: Callable[[str], str] | None,
    readings: dict[str, FanReading] | None = None,
) -> CoolingMemberRow:
    header = headers.get(member_id)
    # Name resolution ladder: the caller's resolver (a user alias), then the
    # header's own label, then the raw member id. The middle rung is what stops
    # a user with no alias seeing `hwmon:it8696:isa-0a40:pwm5:PUMP` where the
    # board says `PUMP` — a resolver returning "" means "no alias", not
    # "no name available".
    resolved = display_name(member_id) if display_name is not None else ""
    label = resolved or (header.label if header else "") or member_id

    floor = header_effective_floor_pct(header, capabilities)
    protected = header_is_pump_protected(header, capabilities)

    # A member the daemon cannot find is the one genuinely wrong state here: the
    # device describes hardware that has moved or gone, so nothing will control
    # it. Everything else — an unknown floor, no coolant — is normal.
    missing = header is None

    reading = (readings or {}).get(member_id)
    requested, _approximate = requested_pct(reading)
    return CoolingMemberRow(
        member_id=member_id,
        label=label,
        role_label=_ROLE_LABELS.get(role, _humanise_token(role)),
        floor_text=_fmt_pct(floor),
        stop_permitted=not protected,
        missing=missing,
        state="warn" if missing else "ok",
        rpm_text=(
            UNKNOWN_TEXT if reading is None or reading.rpm is None else f"{reading.rpm:,} RPM"
        ),
        requested_text=_fmt_pct(requested),
        readback_text=_fmt_pct(reading.pwm_readback_pct if reading else None),
    )


#: Shown when no control in the active profile drives the pump member.
NOT_CONTROLLED_TEXT = "Not controlled"


def pump_strategy_text(
    pump_member: str,
    profile: object | None,
    *,
    sensor_labels: dict[str, str] | None = None,
) -> str:
    """Derive the pump's control strategy for DISPLAY (AIO-MB Phase 6 §2).

    **Derived, never stored** — deliberately. ``AioConfigDialog`` collects an
    Automatic/Fixed choice and bakes it into the curve it seeds, persisting no
    strategy field of its own, so the curve *is* the record. Adding a stored
    ``pump_strategy`` would create a second source of truth that a later curve
    edit would silently falsify, and Phase 4 Decision 6 already settled that
    topology is advisory while curves keep their own sensor.

    Returns "Fixed", "Automatic · <sensor>", or ``NOT_CONTROLLED_TEXT``. A
    manual-mode control and a Flat curve are both genuinely fixed output; every
    other curve type varies with temperature.

    Duck-typed on ``profile`` so this module does not import the heavy
    ``profile_service`` at module scope (the same deferred-import reasoning as
    ``pump_protection.header_effective_floor_pct``).
    """
    if not pump_member or profile is None:
        return NOT_CONTROLLED_TEXT
    labels = sensor_labels or {}
    for control in getattr(profile, "controls", []) or []:
        members = getattr(control, "members", []) or []
        if not any(getattr(m, "member_id", "") == pump_member for m in members):
            continue
        mode = getattr(getattr(control, "mode", None), "value", "")
        if mode == "manual":
            return "Fixed"
        curve = None
        getter = getattr(profile, "get_curve", None)
        if callable(getter):
            curve = getter(getattr(control, "curve_id", ""))
        if curve is None:
            # A control whose curve will not resolve is *skipped* by the daemon
            # (273-i) — it commands nothing, so claiming a strategy would be a
            # lie in exactly the state the user most needs the truth.
            return NOT_CONTROLLED_TEXT
        curve_type = getattr(getattr(curve, "type", None), "value", "")
        if curve_type == "flat":
            return "Fixed"
        sensor_id = getattr(curve, "sensor_id", "") or ""
        sensor = labels.get(sensor_id, sensor_id)
        return f"Automatic · {sensor}" if sensor else "Automatic"
    return NOT_CONTROLLED_TEXT


def build_cooling_device_view(
    device: CoolingDevice,
    *,
    headers: list[HwmonHeader] | None = None,
    capabilities: Capabilities | None = None,
    display_name: Callable[[str], str] | None = None,
    sensor_labels: dict[str, str] | None = None,
    readings: list[FanReading] | None = None,
    sensor_values: dict[str, float] | None = None,
    profile: object | None = None,
) -> CoolingDeviceView:
    """Build the render-ready topology for one cooling device.

    ``display_name`` is the caller's name resolver (``AppState.member_display_name``
    in the app, absent in tests) so this module stays Qt-free and never reaches
    into application state for a label.

    ``readings``, ``sensor_values`` and ``profile`` are the AIO-MB Phase 6
    additions: they let the card render live values and the derived pump
    strategy from ONE view-model instead of joining three sources in the widget.
    All optional — with none of them the view degrades to the Phase 4 topology
    it has always produced, which is what a pre-2.31 daemon and every existing
    test see.
    """
    by_id = {h.id: h for h in (headers or [])}
    labels = sensor_labels or {}
    by_reading = {r.id: r for r in (readings or [])}
    temps = sensor_values or {}

    def row(member_id: str, role: str) -> CoolingMemberRow:
        return _member_row(
            member_id,
            role,
            headers=by_id,
            capabilities=capabilities,
            display_name=display_name,
            readings=by_reading,
        )

    pump = row(device.pump_member, "pump") if device.pump_member else None
    radiators = [row(m, "radiator") for m in device.radiator_members]
    auxiliaries = [row(m, "auxiliary") for m in device.auxiliary_members]

    missing = [r.member_id for r in [pump, *radiators, *auxiliaries] if r and r.missing]

    # The advisory sensor. `preferred` then `fallback`, matching the daemon's
    # own ordering; neither participates in control, so this is presentation.
    sensor_id = device.preferred_sensor or device.fallback_sensor or ""
    sensor_label = labels.get(sensor_id, sensor_id) if sensor_id else UNKNOWN_TEXT

    coolant_available = device.coolant_telemetry == "available"
    if coolant_available:
        coolant_id = device.coolant_sensor or ""
        coolant_note = labels.get(coolant_id, coolant_id) or "Coolant temperature available"
    else:
        coolant_note = NO_COOLANT_NOTE

    kind_label = _KIND_LABELS.get(device.kind) or _humanise_token(device.kind)

    def temp_text(sid: str) -> str:
        value = temps.get(sid)
        return UNKNOWN_TEXT if value is None else f"{value:.0f}°C"

    return CoolingDeviceView(
        device_id=device.id,
        name=device.name or kind_label,
        kind_label=kind_label,
        pump=pump,
        radiators=radiators,
        auxiliaries=auxiliaries,
        sensor_label=sensor_label,
        coolant_available=coolant_available,
        coolant_note=coolant_note,
        policy_label=device.device_policy.display_name or device.device_policy.id,
        missing_members=missing,
        state="warn" if missing else "ok",
        sensor_temp_text=temp_text(sensor_id),
        coolant_temp_text=(
            temp_text(device.coolant_sensor or "") if coolant_available else UNKNOWN_TEXT
        ),
        strategy_text=pump_strategy_text(device.pump_member or "", profile, sensor_labels=labels),
    )


def build_cooling_device_views(
    devices: list[CoolingDevice],
    *,
    headers: list[HwmonHeader] | None = None,
    capabilities: Capabilities | None = None,
    display_name: Callable[[str], str] | None = None,
    sensor_labels: dict[str, str] | None = None,
    readings: list[FanReading] | None = None,
    sensor_values: dict[str, float] | None = None,
    profile: object | None = None,
) -> list[CoolingDeviceView]:
    """Build views for every configured device, preserving daemon order."""
    return [
        build_cooling_device_view(
            d,
            headers=headers,
            capabilities=capabilities,
            display_name=display_name,
            sensor_labels=sensor_labels,
            readings=readings,
            sensor_values=sensor_values,
            profile=profile,
        )
        for d in devices
    ]


# ── AIO-MB Phase 7 (DEC-319): membership index and topology merge ────────────
#
# Two surfaces now create cooling devices — ``AioConfigDialog`` and the fan
# wizard — and two more need to know which fans a device already claims. Both
# facts therefore live here rather than inside either widget: a rule that lives
# inside one consumer is a rule the other consumers cannot follow (CLAUDE.md;
# DEC-276's precedent, applied again).

#: Default name for a cooling device this GUI creates. Moved out of
#: ``ui/widgets/aio_config_dialog`` in Phase 7, when the wizard became a second
#: creator.
DEFAULT_COOLING_DEVICE_NAME = "AIO Cooling System"

#: Wire token for a liquid cooler (``CoolingDeviceKind::AioLiquid``).
COOLING_DEVICE_KIND_AIO = "aio_liquid"

#: The single device id this GUI creates and upserts. Both writing surfaces use
#: it, which is what makes Configure AIO and the wizard idempotent against each
#: other rather than accumulating duplicate devices.
DEFAULT_COOLING_DEVICE_ID = "aio-1"

#: Header roles that imply cooling-stack membership when no device is configured.
#: Maps the wire ``HwmonHeader.role`` token to this module's own role vocabulary.
_ROLE_DERIVED_MEMBERSHIP = {"pump": "pump", "radiator_fan": "radiator"}


@dataclass(frozen=True)
class CoolingMembership:
    """Why one fan id is claimed by the cooling stack.

    ``device_id``/``device_name`` are empty when the claim came from a header
    *role* with no configured device (AIO-MB Phase 7, Decision 4). That
    distinction is load-bearing for the copy: there is no device to name, so
    saying "Part of: AIO Cooling System" would assert a device that does not
    exist.
    """

    member_id: str
    #: "pump" | "radiator" | "auxiliary", this module's vocabulary.
    role: str
    role_label: str
    device_id: str = ""
    device_name: str = ""

    @property
    def from_device(self) -> bool:
        """True when a configured cooling device claims this member."""
        return bool(self.device_id)


def cooling_member_index(
    devices: list[CoolingDevice] | None,
    headers: list[HwmonHeader] | None = None,
    capabilities: Capabilities | None = None,
) -> dict[str, CoolingMembership]:
    """Every fan id the cooling stack claims, mapped to why.

    **Built from the device inventory first, headers only as a fallback.** A
    device's ``radiator_members`` may contain OpenFan channel ids, which have no
    ``HwmonHeader`` at all — so an index derived from ``header.cooling_device_id``
    would silently miss exactly the members a user with an OpenFan-driven
    radiator cares about. The inventory is the complete source; the header pass
    exists only to catch a role the user assigned without completing a device.

    A configured device always outranks a bare role for the same id: it carries
    a name, and the name is what the user is shown.

    No capability gate is needed. ``role`` defaults to ``"unknown"`` and the
    inventory is ``None`` against a daemon that does not serve it, so a pre-2.28
    daemon yields an empty index by construction rather than by permission.
    """
    index: dict[str, CoolingMembership] = {}

    for device in devices or []:
        pairs: list[tuple[str, str]] = []
        if device.pump_member:
            pairs.append((device.pump_member, "pump"))
        pairs.extend((m, "radiator") for m in device.radiator_members if m)
        pairs.extend((m, "auxiliary") for m in device.auxiliary_members if m)
        for member_id, role in pairs:
            # First device wins; a member named twice keeps its stronger role,
            # which is the order above (pump before radiator before auxiliary).
            if member_id in index:
                continue
            index[member_id] = CoolingMembership(
                member_id=member_id,
                role=role,
                role_label=_ROLE_LABELS.get(role, _humanise_token(role)),
                device_id=device.id,
                device_name=device.name or DEFAULT_COOLING_DEVICE_NAME,
            )

    for header in headers or []:
        if header.id in index:
            continue
        # **The pump term is the UNION predicate, never the bare `role` token.**
        # `role` is the DISPLAY role and a user assignment fully substitutes for
        # inference there, downgrades included (DEC-312): assign `chassis_fan` to
        # a header the hardware labels `PUMP` and `role` reads `chassis_fan`
        # while the daemon still refuses to stop it. Reading `role == "pump"`
        # here would leave that header un-excluded in the wizard and unreserved
        # in the Controls picker while the daemon protects it — the GUI
        # disagreeing with the daemon about the same header, which is exactly
        # the bug DEC-312 records. There is no equivalent union for a radiator:
        # the daemon infers `RadiatorFan` only for a known cooler chip's
        # non-pump channels and never guesses it on a motherboard header, so the
        # resolved token is the only evidence there is.
        # A UNION of the two, never a swap. Gating on the predicate ALONE loses
        # the plain `role == "pump"` claim whenever `capabilities` is absent —
        # the predicate needs it for its reconstruction path — and that loses it
        # in the unsafe direction: a known pump would stop being excluded from
        # identification. Caught by this module's own existing tests during the
        # DEC-319 review remediation, which is the whole reason they are there.
        if (header.role or "") == "pump" or header_is_pump_protected(header, capabilities):
            role = "pump"
        elif _ROLE_DERIVED_MEMBERSHIP.get(header.role or "") == "radiator":
            role = "radiator"
        else:
            continue
        index[header.id] = CoolingMembership(
            member_id=header.id,
            role=role,
            role_label=_ROLE_LABELS.get(role, _humanise_token(role)),
        )

    return index


def merge_cooling_device_payload(
    existing: CoolingDevice | None,
    *,
    pump_member: str | None,
    radiator_members: Iterable[str] | None = None,
    auxiliary_members: Iterable[str] | None = None,
) -> dict:
    """Overlay a new topology onto an existing device, preserving everything else.

    **``POST /config/cooling-device`` is create-or-*replace* by id, not a merge**
    — the daemon builds a fresh record from the payload
    (``api/handlers/config.rs:1324``) and swaps it in (``:1370``). So a caller
    that knows only the topology and posts only the topology **erases** the
    name, the advisory sensors and the policy that an earlier
    ``AioConfigDialog`` run stored. This function is what stops that, and it is
    the reason the wizard reads the inventory before it writes (AIO-MB Phase 7,
    Decision 6).

    Returns keyword arguments for ``DaemonClient.set_cooling_device`` **without**
    the id, so the caller stays in charge of which device it is writing. Keys
    whose value is empty are omitted, matching that method's own payload
    construction — an omitted key and an empty one mean the same thing to it.

    ``existing`` of ``None`` is a create: defaults are supplied for name and
    kind, and there is nothing else to preserve.

    **``None`` and ``[]`` mean different things for the member lists, and the
    difference is the whole safety of this function.** ``None`` is "the caller
    has no opinion" and PRESERVES what the device already had; ``[]`` is "the
    caller says empty" and clears it. Defaulting ``None`` to ``[]`` is what a
    first version of this did, and it silently zeroed ``auxiliary_members`` —
    a list no GUI surface can even display, so the caller could not have had an
    opinion about it — on the first wizard Apply.
    """

    def _statement(given: Iterable[str] | None, current: list[str] | None) -> list[str]:
        source = list(current or []) if given is None else list(given)
        return [m for m in source if m]

    radiators = _statement(radiator_members, existing.radiator_members if existing else None)
    auxiliaries = _statement(auxiliary_members, existing.auxiliary_members if existing else None)

    payload: dict = {
        "name": (existing.name if existing else "") or DEFAULT_COOLING_DEVICE_NAME,
        "kind": (existing.kind if existing else "") or COOLING_DEVICE_KIND_AIO,
        "pump_member": pump_member or None,
        "radiator_members": radiators,
        "auxiliary_members": auxiliaries,
    }

    if existing is not None:
        # The fields the wizard has no opinion about, and therefore must not
        # destroy. `kind` is preserved above rather than forced to AIO: a user
        # who described an air cooler or a custom loop does not get it silently
        # relabelled because they later ran the wizard.
        payload["preferred_sensor"] = existing.preferred_sensor or None
        payload["fallback_sensor"] = existing.fallback_sensor or None
        payload["coolant_sensor"] = existing.coolant_sensor or None
        policy_id = existing.device_policy.id if existing.device_policy else ""
        if policy_id:
            payload["device_policy_id"] = policy_id

    return payload


def find_cooling_device(
    devices: list[CoolingDevice] | None, device_id: str
) -> CoolingDevice | None:
    """The device with this id, or ``None``. The read half of read-modify-write."""
    return next((d for d in (devices or []) if d.id == device_id), None)
