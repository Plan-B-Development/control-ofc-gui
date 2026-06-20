"""Dashboard fan-grouping view-model (DEC-176/177) — pure, no Qt.

This is the typed adapter the visual dashboard phases bind to. It turns the raw
:class:`FanReading` list (plus the GUI-owned ``fan_zones`` map and the active
profile) into ordered, self-describing groups of fan tiles with a truthful
per-tile and per-group state. Keeping the grouping/state logic here means it is
unit-tested without a ``QApplication`` and the cards/zone-cards become thin
renderers.

**No PySide imports** — only the Qt-free model + profile modules. The two source
modules were verified import-clean (no PySide pulled transitively); keep it that
way (the Phase-1 gate greps this module's import for ``PySide``/``shiboken``).

Honesty notes (the data is GUI-derived; we never invent daemon fields):
- ``role`` is ``None`` when no profile is active — roles only exist relative to a
  profile's control members.
- ``controlled_by_daemon`` is an *approximation*: a profile is active **and** the
  fan is a member of one of its controls. The daemon does not report this.
- ``curve_source`` is the sensor id of the fan's control's curve, or ``None``
  (unresolved, or a composite Mix/Sync curve that has no single sensor).
- A fan in ``expected_fan_ids`` but absent from the live readings is reported as
  ``OFFLINE`` — never hidden (refinement §4.2 truthfulness).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from control_ofc.api.models import FanReading, Freshness, OverrideStatusEntry
from control_ofc.services.profile_service import (
    CONTROL_ROLE_CHASSIS,
    CONTROL_ROLE_CPU_PUMP,
    CONTROL_ROLE_GPU,
    Profile,
    infer_member_role,
    member_minimum_pct,
)


class FanState(Enum):
    """Per-fan / per-group state. Text labels — never colour-only (WCAG 1.4.1);

    the renderer pairs each with an icon/shape. Worst-of precedence for a group
    chip is highest rank first: OFFLINE > STALL > STALE > LOW_RPM > OVERRIDE >
    NORMAL (a fault always beats the informational OVERRIDE; OFFLINE = no reading
    at all). Per-tile derivation follows the same order.
    """

    NORMAL = "Normal"
    OVERRIDE = "Override"  # manually pinned via a daemon override on its control
    LOW_RPM = "Low RPM"  # rpm==0 while commanded above the floor (non-GPU only)
    STALE = "Stale"  # reading older than the FRESH window
    STALL = "Stall"  # daemon-confirmed stall (rpm==0 while PWM commanded)
    OFFLINE = "Offline"  # expected fan with no live reading


# Worst-of ranking (higher = worse) used for the group state chip.
_STATE_RANK: dict[FanState, int] = {
    FanState.NORMAL: 0,
    FanState.OVERRIDE: 1,
    FanState.LOW_RPM: 2,
    FanState.STALE: 3,
    FanState.STALL: 4,
    FanState.OFFLINE: 5,
}

_GPU_SOURCES = ("amd_gpu", "intel_gpu")

# Role-bucket labels (fallback when a fan is unzoned but a profile classifies it).
_ROLE_LABELS: dict[str, str] = {
    CONTROL_ROLE_CPU_PUMP: "CPU / Pump",
    CONTROL_ROLE_GPU: "GPU",
    CONTROL_ROLE_CHASSIS: "Chassis",
}

# Source-bucket labels (fallback when no profile classifies the fan).
_SOURCE_LABELS: dict[str, str] = {
    "openfan": "OpenFan",
    "hwmon": "Motherboard (hwmon)",
    "amd_gpu": "AMD GPU",
    "intel_gpu": "Intel GPU",
}

# Fixed display order for the fallback (non-user-zone) buckets. User zones always
# sort first (alphabetically); unknown fallback labels sort last.
_FALLBACK_ORDER = [
    "CPU / Pump",
    "GPU",
    "Chassis",
    "OpenFan",
    "Motherboard (hwmon)",
    "AMD GPU",
    "Intel GPU",
]


@dataclass(frozen=True)
class FanTileVM:
    """One fan rendered as a tile. ``rpm``/``pwm_pct``/``age_ms`` are ``None`` for
    an OFFLINE tile (an expected fan with no live reading)."""

    fan_id: str
    display_name: str
    source: str
    rpm: int | None
    pwm_pct: int | None  # from FanReading.last_commanded_pwm (already a percent)
    state: FanState
    age_ms: int | None
    role: str | None  # infer_member_role result, or None when no profile/member
    controlled_by_daemon: bool  # approx: profile active AND fan is a member
    curve_source: str | None  # sensor id of the fan's curve, or None


@dataclass(frozen=True)
class FanGroupVM:
    """A zone (user-assigned) or a role/source fallback bucket of fan tiles."""

    key: str  # stable slug for objectName + ordering
    label: str  # zone name (user) OR role/source bucket name (fallback)
    is_user_zone: bool  # True = user-assigned zone; False = role/source fallback
    tiles: tuple[FanTileVM, ...]
    fans_online: int  # tiles with a FRESH live reading ("reporting live")
    fans_expected: int  # len(tiles), present + missing-expected
    avg_rpm: int | None  # mean over tiles with a non-None rpm, else None
    avg_pwm_pct: int | None  # mean over tiles with a non-None pwm, else None
    state: FanState  # worst-of member states by _STATE_RANK


def _slug(prefix: str, label: str) -> str:
    body = "".join(c if c.isalnum() else "_" for c in label.lower())
    while "__" in body:
        body = body.replace("__", "_")
    body = body.strip("_")
    return f"{prefix}_{body}" if body else prefix


def _source_of(fan_id: str) -> str:
    """Source prefix of a fan id (``openfan:ch00`` -> ``openfan``). Used to place
    an OFFLINE fan (which has no reading carrying ``source``)."""
    return fan_id.split(":", 1)[0] if ":" in fan_id else fan_id


def _derive_state(fan: FanReading, *, overridden: bool, floor: float) -> FanState:
    """State for a *present* fan, following the pinned precedence order."""
    if fan.stall_detected is True:
        return FanState.STALL
    if fan.freshness != Freshness.FRESH:
        return FanState.STALE
    # LOW_RPM is a soft heuristic and is suppressed for GPU fans: a zero-RPM idle
    # is normal for them (DEC-047), and intel GPU fans report no commanded PWM.
    if (
        fan.source not in _GPU_SOURCES
        and fan.rpm == 0
        and fan.last_commanded_pwm is not None
        and fan.last_commanded_pwm > floor
    ):
        return FanState.LOW_RPM
    if overridden:
        return FanState.OVERRIDE
    return FanState.NORMAL


@dataclass(frozen=True)
class _Bucket:
    key: str
    label: str
    is_user_zone: bool


def _bucket_for(fan_id: str, source: str, role: str | None, fan_zones: dict[str, str]) -> _Bucket:
    """1B-over-1A: a user zone wins; else the profile role bucket; else source."""
    zone = (fan_zones.get(fan_id) or "").strip()
    if zone:
        return _Bucket(_slug("zone", zone), zone, True)
    if role is not None:
        label = _ROLE_LABELS.get(role, "Chassis")
        return _Bucket(_slug("bucket", label), label, False)
    label = _SOURCE_LABELS.get(source, "Other")
    return _Bucket(_slug("bucket", label), label, False)


def _avg(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def build_fan_groups(
    fans: list[FanReading],
    *,
    fan_zones: dict[str, str],
    display_name: Callable[[str], str],
    active_profile: Profile | None,
    overrides: list[OverrideStatusEntry],
    expected_fan_ids: set[str] | None = None,
) -> list[FanGroupVM]:
    """Group live fans into ordered zones/buckets with truthful state.

    Args:
        fans: live readings from the daemon poll (the present fans).
        fan_zones: GUI-owned ``fan_id -> zone name`` map (DEC-176).
        display_name: resolves a fan id to its best display name
            (``AppState.fan_display_name``).
        active_profile: the active profile, or ``None``. Supplies role,
            controlled-by-daemon, the LOW_RPM floor, override→fan mapping and
            curve_source. With no profile, fans group by source.
        overrides: ``DaemonStatus.overrides`` — active manual overrides keyed by
            ``control_id`` (DEC-169).
        expected_fan_ids: the roster of fans that *should* be present. Any id
            here but absent from ``fans`` becomes an OFFLINE tile. Defaults to
            the present ids (no roster means no OFFLINE). The caller composes it,
            e.g. the present ids plus the active-profile member ids.

    Returns:
        Groups ordered user-zones-first (alphabetical) then fallback buckets in a
        fixed role/source order; tiles within a group ordered by fan id. Pure and
        deterministic — no Qt, no I/O, no clock.
    """
    # member_id -> (owning control, member); first occurrence wins.
    member_index: dict[str, tuple] = {}
    if active_profile is not None:
        for control in active_profile.controls:
            for member in control.members:
                member_index.setdefault(member.member_id, (control, member))
    override_control_ids = {o.control_id for o in overrides}

    present_ids = {f.id for f in fans}
    roster = (set(expected_fan_ids) | present_ids) if expected_fan_ids is not None else present_ids
    missing_ids = roster - present_ids

    def resolve(fan_id: str) -> tuple[str | None, bool, str | None, float, bool]:
        """(role, controlled_by_daemon, curve_source, floor, overridden)."""
        entry = member_index.get(fan_id)
        if entry is None:
            return None, False, None, 0.0, False
        control, member = entry
        role = infer_member_role(member)
        overridden = control.id in override_control_ids
        floor = member_minimum_pct(control, member)
        curve = active_profile.get_curve(control.curve_id) if active_profile else None
        curve_source = curve.sensor_id if (curve and curve.sensor_id) else None
        return role, True, curve_source, floor, overridden

    # (bucket, tile, is_online) entries for both present and missing fans.
    entries: list[tuple[_Bucket, FanTileVM, bool]] = []

    for fan in fans:
        role, controlled, curve_source, floor, overridden = resolve(fan.id)
        state = _derive_state(fan, overridden=overridden, floor=floor)
        tile = FanTileVM(
            fan_id=fan.id,
            display_name=display_name(fan.id),
            source=fan.source,
            rpm=fan.rpm,
            pwm_pct=fan.last_commanded_pwm,
            state=state,
            age_ms=fan.age_ms,
            role=role,
            controlled_by_daemon=controlled,
            curve_source=curve_source,
        )
        is_online = fan.freshness == Freshness.FRESH
        entries.append((_bucket_for(fan.id, fan.source, role, fan_zones), tile, is_online))

    for fan_id in sorted(missing_ids):
        role, controlled, curve_source, _floor, _ovr = resolve(fan_id)
        source = _source_of(fan_id)
        tile = FanTileVM(
            fan_id=fan_id,
            display_name=display_name(fan_id),
            source=source,
            rpm=None,
            pwm_pct=None,
            state=FanState.OFFLINE,
            age_ms=None,
            role=role,
            controlled_by_daemon=controlled,
            curve_source=curve_source,
        )
        entries.append((_bucket_for(fan_id, source, role, fan_zones), tile, False))

    # Group by (is_user_zone, label); key/label come from the first entry seen.
    grouped: dict[tuple[bool, str], list[tuple[_Bucket, FanTileVM, bool]]] = {}
    for bucket, tile, online in entries:
        grouped.setdefault((bucket.is_user_zone, bucket.label), []).append((bucket, tile, online))

    groups: list[FanGroupVM] = []
    for (is_user_zone, label), members in grouped.items():
        tiles = tuple(sorted((t for _b, t, _o in members), key=lambda t: t.fan_id))
        online = sum(1 for _b, _t, o in members if o)
        rpms = [t.rpm for t in tiles if t.rpm is not None]
        pwms = [t.pwm_pct for t in tiles if t.pwm_pct is not None]
        group_state = max((t.state for t in tiles), key=lambda s: _STATE_RANK[s])
        groups.append(
            FanGroupVM(
                key=members[0][0].key,
                label=label,
                is_user_zone=is_user_zone,
                tiles=tiles,
                fans_online=online,
                fans_expected=len(tiles),
                avg_rpm=_avg(rpms),
                avg_pwm_pct=_avg(pwms),
                state=group_state,
            )
        )

    def order_key(g: FanGroupVM) -> tuple:
        if g.is_user_zone:
            return (0, 0, g.label.lower())
        idx = _FALLBACK_ORDER.index(g.label) if g.label in _FALLBACK_ORDER else len(_FALLBACK_ORDER)
        return (1, idx, g.label.lower())

    groups.sort(key=order_key)

    # Guarantee unique objectName keys (CLAUDE.md) even if two labels slug-collide.
    seen: dict[str, int] = {}
    deduped: list[FanGroupVM] = []
    for g in groups:
        n = seen.get(g.key, 0)
        seen[g.key] = n + 1
        deduped.append(g if n == 0 else replace(g, key=f"{g.key}_{n + 1}"))
    return deduped
