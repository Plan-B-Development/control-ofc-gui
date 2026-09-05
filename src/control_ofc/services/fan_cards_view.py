"""Dashboard fan-card view-model (DEC-222) — pure, no Qt.

One card per **logical control**, not per fan. That granularity is not a
presentation preference: the daemon's live-intent API is control-keyed
(``POST /control/{id}/override``, DEC-163) and ``fan_identify`` is stop/restore
only, so there is no per-fan speed surface to render a per-fan card against.
A control's card therefore aggregates its members' telemetry and names how many
fans it covers.

Supersedes the zone/bucket grouping in the retired ``fan_grouping`` module: the
Fan Zone and Fan Array sections were removed with the Dashboard rebuild, and the
per-tile state derivation that was worth keeping moved here intact.

Fans that belong to no control — including *every* controllable fan when no
profile is active — collect in a single "Unassigned" pseudo-control so the
Dashboard can still answer "what are the fans doing?" in exactly the state where
the user most needs an answer. Read-only fans are kept out of that bucket but
get a card each, so a GPU whose only speed signal is a firmware-reported
measured duty (DEC-204) still has one place that shows it; a read-only fan that
a hand-edited profile placed inside a real control renders there instead,
because the control genuinely exists.

**No PySide import of its own**, and every function here is exercisable without a
``QApplication`` — that is the property the tests rely on, and the one to keep.
Note it is *not* the same as "pulls no Qt transitively": ``profile_service``
imports ``QtCore`` for ``ProfileService``'s signals, so importing this module
does load PySide. (The retired ``fan_grouping`` module claimed the stronger
property in its docstring; that claim had silently become false.)

Honesty notes (GUI-derived data; daemon fields are never invented):
- ``temp_c`` is the control's curve sensor temperature, or ``None`` when the
  curve is absent, unresolved, or a composite Mix/Sync with no single sensor.
- ``overridden`` mirrors ``DaemonStatus.overrides`` — the daemon is authoritative.
- A control member with no live reading contributes ``OFFLINE``; it is never
  hidden to make a card look healthier than it is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from control_ofc.api.models import (
    Capabilities,
    FanReading,
    Freshness,
    HwmonHeader,
    OverrideStatusEntry,
)
from control_ofc.services.overview_view import fan_control_method
from control_ofc.services.profile_service import (
    CurveConfig,
    CurveType,
    Profile,
    member_minimum_pct,
)

# The pseudo-control id for fans belonging to no real control.
UNASSIGNED_ID = ""
UNASSIGNED_LABEL = "Unassigned"

# Card-key prefix for a read-only fan's own card.
READ_ONLY_PREFIX = "readonly:"

# GUI-generated control ids are uuid4 hex, so they never collide with the two
# pseudo-card namespaces above — but a hand-edited or shared profile can carry
# any string, including "" or a "readonly:"-prefixed one, and nothing upstream
# rejects it (neither LogicalControl.from_dict nor the daemon's profile
# validation checks id shape or uniqueness). Cards are therefore keyed by a
# de-duplicated ``card_key`` while ``control_id`` stays the truthful value the
# Edit deep-link needs.

_GPU_SOURCES = ("amd_gpu", "intel_gpu", "nvidia_gpu")

# Curve types that combine or mirror other curves/controls rather than reading one
# sensor. They keep whatever sensor_id they last had, so it must not be trusted.
_COMPOSITE_CURVE_TYPES = (CurveType.MIX, CurveType.SYNC)

# Control methods that mean "this fan has no write path". Mirrors the strings
# returned by :func:`overview_view.fan_control_method` (DEC-102 / DEC-204).
_READ_ONLY_METHODS = ("read-only", "no fan control", "unknown")


class FanState(Enum):
    """Per-fan / per-card state. Text labels — never colour-only (WCAG 1.4.1);

    the renderer pairs each with a glyph. Worst-of precedence for a card chip is
    highest rank first: OFFLINE > STALL > STALE > LOW_RPM > OVERRIDE > NORMAL
    (a fault always beats the informational OVERRIDE; OFFLINE = no reading at
    all). Preserved verbatim from the retired ``fan_grouping`` module so the
    state vocabulary did not silently change with the Dashboard rebuild.
    """

    NORMAL = "Normal"
    OVERRIDE = "Override"  # manually pinned via a daemon override on its control
    LOW_RPM = "Low RPM"  # rpm==0 while commanded above the floor (non-GPU only)
    STALE = "Stale"  # reading older than the FRESH window
    # Daemon-confirmed stall: rpm==0 while the header reported a duty above the
    # daemon's stall threshold. Deliberately not worded "while PWM commanded" —
    # the daemon derives it from `last_commanded_pwm`, which for an uncontrolled
    # hwmon header is the readback (`AIO5-a` / `WIRE-j`).
    STALL = "Stall"
    OFFLINE = "Offline"  # expected fan with no live reading


# Worst-of ranking (higher = worse) used for the card state chip.
_STATE_RANK: dict[FanState, int] = {
    FanState.NORMAL: 0,
    FanState.OVERRIDE: 1,
    FanState.LOW_RPM: 2,
    FanState.STALE: 3,
    FanState.STALL: 4,
    FanState.OFFLINE: 5,
}


@dataclass(frozen=True)
class FanCardVM:
    """One logical control rendered as a Dashboard card.

    ``rpm``/``pwm_pct``/``duty_pct`` are means across the members that reported a
    value, and are ``None`` when no member did. ``curve`` is handed to the card's
    preview painter as-is — the card never resolves another curve's or control's
    fields (the enduring curve-ownership rule).

    ``card_key`` is what the page keys its reconcile dict and objectNames on. It
    equals ``control_id`` in every normal case; it differs only when a malformed
    profile would otherwise make two cards collide and silently drop one.

    ``duty_pct`` is the firmware-reported *measured* duty (NVIDIA via NVML,
    DEC-204) and is distinct from the daemon-commanded ``pwm_pct``. Where both
    exist the commanded value wins on display; duty is the fallback that gives
    read-only GPU fans a speed readout at all.
    """

    control_id: str
    card_key: str  # unique per card; == control_id unless a profile forced a clash
    label: str
    is_unassigned: bool
    is_read_only: bool
    fan_count: int
    member_fan_ids: tuple[str, ...]
    rpm: int | None
    pwm_pct: int | None
    duty_pct: int | None
    temp_c: float | None
    state: FanState
    overridden: bool
    curve: CurveConfig | None


def is_fan_controllable(
    fan: FanReading, headers: list[HwmonHeader], caps: Capabilities | None
) -> bool:
    """True when the fan has a real PWM write path.

    Derived from :func:`overview_view.fan_control_method` so the Dashboard and
    the Overview page can never disagree about what "controllable" means. An
    ``unknown`` method counts as not controllable — we do not claim a write path
    we cannot evidence (the GPU truthfulness rule).
    """
    return fan_control_method(fan, headers, caps) not in _READ_ONLY_METHODS


def _derive_state(fan: FanReading, *, overridden: bool, floor: float) -> FanState:
    """State for a *present* fan, following the pinned precedence order."""
    if fan.stall_detected is True:
        return FanState.STALL
    if fan.freshness != Freshness.FRESH:
        return FanState.STALE
    # LOW_RPM is a soft heuristic and is suppressed for GPU fans: a zero-RPM idle
    # is normal for them (DEC-047), and intel GPU fans report no commanded PWM.
    #
    # `requested_duty`, not `last_commanded_pwm` (`WIRE-j`): for an hwmon header
    # the latter carries the poll's *readback* whenever nothing has been
    # commanded (`AIO5-a`), so an uncontrolled header sitting at a BIOS-set duty
    # with a dead tach would be flagged "low RPM against a command" that never
    # happened. DEC-318 shipped the unambiguous field; this derivation and the
    # Dashboard's stall alert were the two that never moved to it.
    commanded, _approximate = fan.requested_duty()
    if (
        fan.source not in _GPU_SOURCES
        and fan.rpm == 0
        and commanded is not None
        and commanded > floor
    ):
        return FanState.LOW_RPM
    if overridden:
        return FanState.OVERRIDE
    return FanState.NORMAL


def _avg(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _worst(states: list[FanState]) -> FanState:
    """Worst-of the member states.

    An empty list means the control has no members at all — which is how a
    freshly-created fan role looks before any fan is assigned. That is not a
    fault, so it must NOT report OFFLINE: doing so paints a red critical chip on
    a control the user is still configuring, and makes a genuine OFFLINE (an
    expected fan reporting nothing) indistinguishable from an unfinished one.
    """
    return max(states, key=lambda s: _STATE_RANK[s]) if states else FanState.NORMAL


def _unique_key(preferred: str, taken: set[str]) -> str:
    """A card key not already in ``taken``, suffixing only on a real clash."""
    key = preferred
    n = 2
    while key in taken:
        key = f"{preferred}#{n}"
        n += 1
    taken.add(key)
    return key


def build_fan_card_vms(
    fans: list[FanReading],
    *,
    active_profile: Profile | None,
    overrides: list[OverrideStatusEntry],
    headers: list[HwmonHeader] | None = None,
    caps: Capabilities | None = None,
    sensor_values: dict[str, float] | None = None,
    display_name: Callable[[str], str] | None = None,
) -> list[FanCardVM]:
    """Build one card VM per logical control, plus the Unassigned bucket.

    Args:
        fans: displayable live readings from the daemon poll.
        active_profile: the active profile, or ``None``. With no profile there
            are no controls, so every controllable fan lands in Unassigned.
        overrides: ``DaemonStatus.overrides`` — active manual overrides keyed by
            ``control_id`` (DEC-163). Drives the read-only "Override active" chip.
        headers: hwmon headers, used to decide writability for the Unassigned
            bucket. Empty/None → hwmon fans are treated as not controllable
            (we cannot evidence a write path without the header).
        caps: daemon capabilities, used for the GPU write-path decision.
        sensor_values: ``sensor_id -> value_c`` snapshot (page-resolved), used to
            fill each card's ``temp_c`` from its curve's sensor. Keeps this
            module Qt-free and free of any daemon call.
        display_name: resolves a fan id to its best display name
            (``AppState.fan_display_name``), used to label read-only fan cards.
            Falls back to the raw id.

    Returns:
        Controls in profile order, then Unassigned last (omitted when empty).
        Pure and deterministic — no Qt, no I/O, no clock.
    """
    by_id = {f.id: f for f in fans}
    sv = sensor_values or {}
    hdrs = headers or []
    override_control_ids = {o.control_id for o in overrides}

    cards: list[FanCardVM] = []
    claimed: set[str] = set()
    keys: set[str] = set()

    for control in active_profile.controls if active_profile else []:
        overridden = control.id in override_control_ids
        curve = active_profile.get_curve(control.curve_id) if active_profile else None
        rpms: list[int] = []
        pwms: list[int] = []
        duties: list[int] = []
        states: list[FanState] = []
        member_ids: list[str] = []

        for member in control.members:
            member_ids.append(member.member_id)
            claimed.add(member.member_id)
            fan = by_id.get(member.member_id)
            if fan is None:
                # A profile member with no live reading is OFFLINE, never hidden.
                states.append(FanState.OFFLINE)
                continue
            floor = member_minimum_pct(control, member)
            states.append(_derive_state(fan, overridden=overridden, floor=floor))
            if fan.rpm is not None:
                rpms.append(fan.rpm)
            if fan.last_commanded_pwm is not None:
                pwms.append(fan.last_commanded_pwm)
            if fan.duty_pct is not None:
                duties.append(fan.duty_pct)

        # A composite Mix/Sync curve has no single sensor, so it has no single
        # temperature to show — "—" is the honest render, not a borrowed value.
        # The type check is load-bearing: the curve editor writes sensor_id
        # unconditionally, so a Mix/Sync curve routinely carries a stale one left
        # over from whatever it was before. Trusting sensor_id alone would show
        # that stale sensor's reading as if it drove the control.
        temp = None
        if curve is not None and curve.sensor_id and curve.type not in _COMPOSITE_CURVE_TYPES:
            temp = sv.get(curve.sensor_id)

        cards.append(
            FanCardVM(
                control_id=control.id,
                card_key=_unique_key(control.id, keys),
                label=control.name or control.id,
                is_unassigned=False,
                is_read_only=False,
                fan_count=len(control.members),
                member_fan_ids=tuple(member_ids),
                rpm=_avg(rpms),
                pwm_pct=_avg(pwms),
                duty_pct=_avg(duties),
                temp_c=temp,
                state=_worst(states),
                overridden=overridden,
                curve=curve,
            )
        )

    # Unassigned: controllable fans no control claims. With no profile active
    # that is every controllable fan — the Dashboard still shows the hardware.
    unclaimed = [f for f in fans if f.id not in claimed]
    loose = [f for f in unclaimed if is_fan_controllable(f, hdrs, caps)]
    if loose:
        rpms = [f.rpm for f in loose if f.rpm is not None]
        pwms = [f.last_commanded_pwm for f in loose if f.last_commanded_pwm is not None]
        duties = [f.duty_pct for f in loose if f.duty_pct is not None]
        states = [_derive_state(f, overridden=False, floor=0.0) for f in loose]
        cards.append(
            FanCardVM(
                control_id=UNASSIGNED_ID,
                card_key=_unique_key(UNASSIGNED_ID, keys),
                label=UNASSIGNED_LABEL,
                is_unassigned=True,
                is_read_only=False,
                fan_count=len(loose),
                member_fan_ids=tuple(sorted(f.id for f in loose)),
                rpm=_avg(rpms),
                pwm_pct=_avg(pwms),
                duty_pct=_avg(duties),
                temp_c=None,  # no control, so no curve, so no driving sensor
                state=_worst(states),
                overridden=False,
                curve=None,
            )
        )

    # Read-only fans get one card each rather than a shared bucket. They cannot
    # be assigned to a control (the member picker refuses them, DEC-102), so a
    # per-fan card is the only way their reading stays attributable — pooling a
    # GPU's measured duty into a shared card would destroy the very number the
    # card exists to show (DEC-204).
    for fan in sorted(
        (f for f in unclaimed if not is_fan_controllable(f, hdrs, caps)),
        key=lambda f: f.id,
    ):
        cards.append(
            FanCardVM(
                control_id=f"{READ_ONLY_PREFIX}{fan.id}",
                card_key=_unique_key(f"{READ_ONLY_PREFIX}{fan.id}", keys),
                label=display_name(fan.id) if display_name else fan.id,
                is_unassigned=False,
                is_read_only=True,
                fan_count=1,
                member_fan_ids=(fan.id,),
                rpm=fan.rpm,
                pwm_pct=fan.last_commanded_pwm,
                duty_pct=fan.duty_pct,
                temp_c=None,
                state=_derive_state(fan, overridden=False, floor=0.0),
                overridden=False,
                curve=None,
            )
        )

    return cards
