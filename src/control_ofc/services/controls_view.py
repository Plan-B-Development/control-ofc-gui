"""Qt-free view helpers for the Controls page (DEC-214; DEC-219 Phase 7.3).

Pure derivations kept out of the Qt page so they stay headless-testable — no
widget construction, no ``QApplication``. Some pull in ``profile_service`` role
helpers, which transitively loads ``PySide6.QtCore`` (ProfileService is a
QObject), exactly like :mod:`control_ofc.services.fan_cards_view`; the module
still builds no widgets and every function is unit-testable without a display.
No value is fabricated.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from control_ofc.knowledge.sensor_knowledge import (
    classify_sensor_with_overrides,
    sensor_is_coolant,
)
from control_ofc.services.cooling_device_view import CoolingMembership
from control_ofc.services.profile_service import (
    AIO_PUMP_TAG,
    CONTROL_ROLE_CHASSIS,
    CONTROL_ROLE_CPU_PUMP,
    CONTROL_ROLE_GPU,
    _label_indicates_cpu_or_pump,
    control_minimum_pct,
    infer_control_role,
    infer_member_role,
    pump_role_floor_pct,
)
from control_ofc.ui.fan_presence import (
    PRESENCE_BADGE,
    PRESENCE_TOOLTIP,
    FanPresence,
    classify_fan_presence,
)
from control_ofc.ui.hwmon_guidance import lookup_chip_guidance

# The renew timer never fires slower than this floor, so the daemon deadman is
# honoured even for a grant advising a sub-second cadence.
_RENEW_FLOOR_MS = 1000


def unassigned_fan_ids(fans: Iterable, controls: Sequence) -> list[str]:
    """Fan ids that are not a member of any logical control.

    Feeds the "Unassigned Fans (N)" dropzone in the Assign-Roles pane.
    """
    assigned = {m.member_id for c in controls for m in c.members}
    return [f.id for f in fans if f.id not in assigned]


def member_rpm_map(control, fan_readings: dict) -> dict[str, int | None]:
    """Map each of a control's member ids to its live RPM (``None`` when unknown).

    ``fan_readings`` is an id → reading mapping. A member with no reading (or a
    reading without an RPM) maps to ``None`` so the card leaves that RPM column
    blank rather than inventing a value.
    """
    result: dict[str, int | None] = {}
    for member in control.members:
        reading = fan_readings.get(member.member_id)
        result[member.member_id] = getattr(reading, "rpm", None) if reading is not None else None
    return result


def curve_min_output_floor(profile, curve_id: str) -> float:
    """Highest ``minimum_pct`` of any control referencing this curve (0 when none).

    The editor clamps curve points to this so a shared curve can't be authored
    below the strictest role's safe minimum. A freshly-created control still
    contributes its role-aware floor (derived from members) before migration.
    """
    floor = 0.0
    for ctrl in profile.controls:
        if ctrl.curve_id == curve_id:
            effective = max(ctrl.minimum_pct, control_minimum_pct(ctrl.members))
            floor = max(floor, effective)
    return floor


@dataclass(frozen=True)
class MinPwmBadge:
    """What a control card's "Min: N%" badge shows (DEC-417).

    ``floor_pct`` of 0 means the badge is hidden, so a chassis control authored
    before v4 never reads a misleading "Min: 0%".
    """

    floor_pct: float
    tooltip: str


def min_pwm_badge(control, pump_header_ids: frozenset[str]) -> MinPwmBadge:
    """The Min badge for ``control``: its strictest member floor, and why.

    The label-derived floor (``minimum_pct`` against the role floor, DEC-095/162)
    unioned with the header pump-role term (`TS-w`): the daemon floors a member
    whose header is assigned ``pump`` at 30% on the assignment alone, so a member
    authored before the assignment would otherwise show 20% here.

    Only the BADGE takes that term. The card's manual slider keeps the
    label-derived floor, by the user's choice (DEC-417); the daemon clamps a
    request below 30% on such a member up to 30%, as it always has.

    A pump found by its label raises the whole control's ``minimum_pct``; a pump
    ROLE raises only its own member. So where the role is what lifts the badge and
    the control has other floored fans, the tooltip says whom the figure covers —
    and gives the other fans ``control.minimum_pct``, the number the daemon holds
    them at (``member_effective_floor``'s non-pump branch), never the badge's
    ``base``, which also counts the GUI's 20% role default and so overstates it
    for a profile whose minimum sits below that.
    """
    members = control.members
    base = max(control.minimum_pct, control_minimum_pct(members))
    role_floor = pump_role_floor_pct(members, pump_header_ids)
    floor = max(base, role_floor)
    if floor <= 0.0:
        return MinPwmBadge(0.0, "")
    role = infer_control_role(members)
    if role == CONTROL_ROLE_CPU_PUMP:
        tip = "Minimum PWM derived from a CPU or pump member. 30% protects the pump from stalling."
    elif role_floor > base:
        tip = (
            f"Minimum PWM raised to {role_floor:.0f}% because a member's header is "
            f"assigned the pump role. {role_floor:.0f}% protects the pump from stalling."
        )
        assigned = {m.member_id for m in members if pump_role_floor_pct([m], pump_header_ids)}
        others = [
            m
            for m in members
            if m.member_id not in assigned and infer_member_role(m) != CONTROL_ROLE_GPU
        ]
        if others:
            whom = "member" if len(assigned) == 1 else "members"
            tip += (
                f" It applies to the pump-assigned {whom}; the other fans in this "
                f"control keep {control.minimum_pct:.0f}%."
            )
    elif role == CONTROL_ROLE_CHASSIS:
        tip = (
            "Minimum PWM for chassis fans. 20% prevents most 4-pin fans from stalling at low duty."
        )
    else:
        tip = "Minimum PWM applied by this control."
    # DEC-119: in a mixed control (GPU grouped with chassis/CPU fans) the floor
    # above applies only to the non-GPU members. GPU members are never floored by
    # the GUI — the GPU firmware owns their idle minimum.
    has_gpu = any(infer_member_role(m) == CONTROL_ROLE_GPU for m in members)
    has_non_gpu = any(infer_member_role(m) != CONTROL_ROLE_GPU for m in members)
    if has_gpu and has_non_gpu:
        tip += (
            " GPU members in this control are not floored (the GPU firmware manages their minimum)."
        )
    return MinPwmBadge(floor, tip)


def divergent_gpu_output(control, control_output: float, members: dict) -> float | None:
    """A GPU member's applied output when it diverges from the control-wide value
    (DEC-119), else ``None``.

    Only mixed controls (a GPU grouped with chassis/CPU fans) can diverge: the
    GPU member is re-tuned with a 0% floor and may idle below the control-wide
    floor. A GPU-only control's headline already *is* the GPU value, so it is
    never annotated.
    """
    if not members:
        return None
    if not any(infer_member_role(m) != CONTROL_ROLE_GPU for m in control.members):
        return None
    for m in control.members:
        if infer_member_role(m) == CONTROL_ROLE_GPU:
            gpu_out = members.get(m.target_id)
            if gpu_out is not None and abs(gpu_out - control_output) > 1.0:
                return gpu_out
    return None


def renew_interval_ms(
    renew_secs: dict, fallback_ms: int, floor_ms: int = _RENEW_FLOOR_MS
) -> int | None:
    """The shared renew-timer interval: the MIN cadence across held grants, floored.

    ``None`` when nothing is held (leave the running timer as-is). One timer
    serves every override and ``setInterval`` resets the countdown, so it must
    fire fast enough for the shortest-TTL grant — last-writer-wins on a larger
    ``renew_secs`` could otherwise stretch a renew past an earlier override's
    shorter TTL. Grants missing ``renew_secs`` fall back to ``fallback_ms``.
    """
    if not renew_secs:
        return None
    interval_ms = min((secs * 1000) if secs else fallback_ms for secs in renew_secs.values())
    return max(floor_ms, interval_ms)


def override_rejection_feedback(code: str) -> tuple[str, str] | None:
    """``(message, css_class)`` for a *user-actionable* override rejection, else
    ``None`` (benign races stay a quiet card revert).

    Only two codes tell the user something the card flipping back to auto cannot:
    ``thermal_abort`` (safety is holding the fans) and ``stale_fencing_token``
    (another client superseded this override). Keeping every other code silent is
    exactly what makes a superseded override distinct from a lapsed one (DEC-163).
    """
    if code == "thermal_abort":
        return ("Override blocked — thermal emergency (fans held by safety)", "CriticalChip")
    if code == "stale_fencing_token":
        return ("Override superseded by another client", "WarningChip")
    return None


# Wording for each `skipped_controls[].reason` token the daemon can send (273-i).
# The daemon deliberately sends a stable token and leaves the wording to the
# client; this is where that wording lives, so it stays out of the widget and
# stays testable without a QApplication.
#
# Pinned against `models.SKIP_REASON_VALUES` by
# `test_skip_reason_map_covers_the_wire_vocabulary` — DEC-257's lesson: a
# presentation map keyed off a wire field drifted silently once already, and a
# live thermal recovery rendered as a neutral grey pill for it.
_SKIP_REASONS: dict[str, str] = {
    "curve_not_found": "its curve is missing from this profile",
    "sensor_unavailable": "its sensor is unavailable",
    "mix_unresolvable": "none of its combined inputs could be read",
    "sync_unresolvable": "the control it mirrors is not running",
    # Since daemon 2.55.0 (`OFN-al`) this also covers fans on headers the daemon
    # can see but cannot write (read-only, or not on this board at all), so it
    # must not say "reach": a read-only header is reachable.
    "backend_unavailable": "none of its fans can be controlled by this daemon",
}


def _format_skip_duration(ms: int) -> str:
    """A coarse "how long" for the skipped chip (`WIRE-q`).

    Deliberately coarse — minutes above a minute, seconds below — so the chip
    text changes at most once a minute and the 1 Hz poll does not repaint it
    every tick. `0` renders empty: the daemon lists a control only after three
    consecutive skipped ticks, so a zero here means "not reported", and "for 0s"
    would read as though it had just started.
    """
    if ms <= 0:
        return ""
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def skipped_control_feedback(
    reason: str, skipped_for_ms: int = 0, control_name: str = ""
) -> tuple[str, str]:
    """``(chip_text, tooltip)`` for a control the daemon is not commanding.

    The chip is deliberately terse and the same for every cause — what matters
    at a glance is that this fan is not being driven, not why. The why goes in
    the tooltip.

    An UNRECOGNISED token still renders. A newer daemon may add a reason this
    build has never heard of, and showing "Not controlled" with a vaguer tooltip
    is strictly better than showing nothing at all, which is the very silence
    273-i exists to end.
    """
    # `reason` is whatever the wire sent. `_filter_fields` does no type
    # coercion, so a non-conforming daemon sending `"reason": []` would reach a
    # dict lookup with an unhashable key and raise on the 1 Hz poll path — a
    # crash, not a degraded render. `unavailable_sensors` never keys a dict on a
    # wire value, which is why this asymmetry is ours to handle.
    detail = _SKIP_REASONS.get(reason) if isinstance(reason, str) else None
    # `skipped_for_ms` and `control_name` were parsed and never read (`WIRE-q`):
    # "not controlled" and "not controlled for four minutes" call for different
    # responses from the user, and the name is the DAEMON's — the GUI otherwise
    # resolves it from its own profile, which can be the stale one.
    duration = _format_skip_duration(skipped_for_ms)
    chip = f"Not controlled · {duration}" if duration else "Not controlled"
    subject = f'"{control_name}"' if control_name else "these fans"
    since = f" It has been skipped for {duration}." if duration else ""
    if detail is None:
        return (
            chip,
            f"The daemon is not commanding {subject}. They hold their last speed.{since}",
        )
    # Every other cause is a control the daemon WAS driving and has stopped
    # driving, so its fans hold what they were last told until it clears. A
    # `backend_unavailable` control's fans were never this daemon's to hold: a
    # read-only header runs on its firmware, and nothing here will "resolve" it
    # (DEC-412). Saying they hold their last speed would be false for exactly
    # the case the widened token now covers.
    tail = (
        "Their speed is up to the hardware, not this daemon."
        if reason == "backend_unavailable"
        else "They hold their last speed until it resolves."
    )
    return (
        chip,
        f"The daemon is not commanding {subject} — {detail}. {tail}{since}",
    )


def sensor_combo_label(s, overrides: dict) -> str:
    """Curve-editor sensor-combo label, starring coolant + CPU sensors (★) — the
    recommended bindings for AIO/radiator curves (DEC-157). Selection stays free;
    this only highlights."""
    val_text = f" — {s.value_c:.1f}°C" if s.value_c is not None else ""
    cls = classify_sensor_with_overrides(
        s.id, chip_name=s.chip_name, label=s.label, overrides=overrides
    )
    preferred = cls.source_class in ("coolant", "coolant_in", "coolant_out") or (
        s.kind == "cpu_temp"
    )
    star = "★ " if preferred else ""
    return f"{star}{s.label} ({s.kind}){val_text}"


def role_preserving_label(display_name: str, fallback_label: str, source: str) -> str:
    """Pick what to persist as ``ControlMember.member_label`` (DEC-228).

    ``member_label`` is not only a name: both ``infer_member_role`` and the
    daemon's ``member_is_pump_or_cpu`` match "cpu"/"pump"/"aio" against it to
    apply the DEC-095/162 30% CPU/pump floor. Since DEC-252 the daemon's
    eval-time classifier is a *union* of this label and the one it discovered
    itself (carried in the member id), so it can add a floor this label omits —
    but never remove one. Keeping this hardware-truthful is still what makes the
    two agree, and what `validate()`'s rejection still keys on.

    Neither candidate is reliably the safer one, so the rule is simply **never
    lower the inferred role**:

    * the user's alias can carry the role where the hardware does not — the
      daemon synthesises ``"pwm7"`` for any header with no
      ``pwmN_label``/``fanN_label`` (`read_label`), so where nothing else knows
      the header the alias is the only way to say "this is the pump";
    * the hardware-side label can carry it where the alias does not — a header
      labelled ``CPU_OPT`` that the user renamed to "My Fan".

    ``fallback_label`` is ``AppState.fan_fallback_name`` — the *resolved* name
    (sysfs label, else ``/etc/sensors.d``, else the board table), empty for a
    non-hwmon member. Comparing against the resolved name rather than the raw
    ``HwmonHeader.label`` is what makes this work on boards whose chip publishes
    no labels at all (DEC-229): there the raw label is the placeholder ``pwm1``,
    which carries no role, while the board table answers ``CPU_FAN``, which does.

    Display is unaffected either way: every member surface resolves through
    ``AppState.member_display_name``, which prefers the live alias over this cache.
    """
    if source != "hwmon" or not fallback_label:
        return display_name
    if _label_indicates_cpu_or_pump(fallback_label) and not _label_indicates_cpu_or_pump(
        display_name
    ):
        return fallback_label
    return display_name


def aio_tag_for(label: str, role: str = "") -> str:
    """The ``(AIO pump)``/``(AIO radiator)`` suffix for a liquid-cooler header
    (DEC-157). Role-bearing, so it is appended to the persisted label too.

    ``role`` is the daemon's per-channel ``HwmonHeader.role`` (DEC-311). It wins
    over the label guess, because it is evidence rather than inference: a Kraken
    ``pwm1`` whose chip publishes no label reads as the placeholder ``pwm1`` and
    would otherwise be tagged "(AIO radiator)" — and on a motherboard AIO the
    user's header-role assignment is the ONLY thing that knows.

    Only ``pump`` is honoured. ``radiator_fan`` is behaviourally inert daemon-side
    (nothing branches on it; every safety decision goes through ``is_pump()``), so
    reading it here would imply a guarantee that does not exist.
    """
    if role == "pump":
        return AIO_PUMP_TAG
    return AIO_PUMP_TAG if "pump" in label.lower() else " (AIO radiator)"


def build_member_candidates(
    fans,
    headers,
    *,
    gpu_writable: bool,
    display_name,
    fallback_name,
) -> list[dict]:
    """Rows for the member-picker: every fan output that may be controlled.

    Each row carries a user-facing ``label`` (badges, AIO tag, ``(read-only)``)
    and a separate ``clean_label`` — what would be persisted as
    ``ControlMember.member_label``, which is a **safety input**, not decoration:
    it drives the DEC-095/162 30% CPU/pump floor on both sides of the API. See
    :func:`role_preserving_label`.

    Exclusions, all deliberate:

    * hwmon fans whose header is ``is_writable=False`` (DEC-102) — assigning one
      produced a 1 Hz ``EACCES`` storm. Daemon discovery drops these too; this is
      defence-in-depth for an older daemon mid-upgrade.
    * Intel and NVIDIA discrete GPU fans (DEC-121/DEC-204) — no kernel write path
      at all, and unlike an AMD read-only GPU (a fixable ``ppfeaturemask`` state)
      that is permanent. Their temperature sensors stay available as curve sensors.

    ``display_name``/``fallback_name`` are the ``AppState`` resolvers, injected so
    this stays headless. An AMD GPU is still listed when ``gpu_writable`` is False
    — flagged ``(read-only)`` rather than hidden, because that state is fixable.
    """
    header_by_id = {h.id: h for h in headers}
    available: list[dict] = []

    for fan in fans:
        if fan.source == "hwmon":
            h = header_by_id.get(fan.id)
            if h is not None and not h.is_writable:
                continue
        if fan.source in ("intel_gpu", "nvidia_gpu"):
            continue

        label = display_name(fan.id)
        # Empty for a fan with no header, so a raw daemon id can never be
        # mistaken for a role-bearing name (DEC-229).
        fallback = fallback_name(fan.id) if header_by_id.get(fan.id) is not None else ""
        clean_label = role_preserving_label(label, fallback, fan.source)

        if fan.source == "amd_gpu" and not gpu_writable:
            label = f"{label} (read-only)"
        # Surface "no fan detected" / PWM-only states so users don't assign
        # curves to empty headers.
        presence = classify_fan_presence(fan, header_by_id.get(fan.id))
        badge = PRESENCE_BADGE.get(presence, "")
        if badge and "(read-only)" not in label:
            label = f"{label} ({badge})"

        h_aio = header_by_id.get(fan.id)
        # DEC-312: `role == "pump"` joins `is_aio` as a reason to tag. A pump on a
        # motherboard AIO_PUMP header is `is_aio=False` by construction — the chip
        # is the Super-I/O, not the cooler — so without the role term the tag never
        # fires and the persisted `member_label` loses the 30% pump floor the
        # daemon independently enforces.
        if fan.source == "hwmon" and h_aio is not None:
            h_role = h_aio.role
            if h_aio.is_aio or h_role == "pump":
                aio_tag = aio_tag_for(label, h_role)
                label += aio_tag
                clean_label += aio_tag  # role-bearing — see above

        entry = {
            "id": fan.id,
            "source": fan.source,
            "label": label,
            "clean_label": clean_label,
            "rpm": fan.rpm,  # DEC-214: live RPM (None → "no fan", never invented)
        }
        tip = PRESENCE_TOOLTIP.get(presence, "") if presence != FanPresence.PRESENT else ""
        if tip:
            entry["tooltip"] = tip
        available.append(entry)

    for header in headers:
        # Read-only headers are dropped entirely rather than labelled: the old
        # "(read-only)" suffix still allowed assignment, and profiles that bound
        # them produced 1 Hz 503/EACCES storms (DEC-102). They stay visible on
        # the hardware surfaces for awareness.
        if not header.is_writable:
            continue
        if any(a["id"] == header.id for a in available):
            continue

        label = display_name(header.id) or header.id
        clean_label = role_preserving_label(label, fallback_name(header.id), "hwmon")
        presence = classify_fan_presence(None, header)
        if PRESENCE_BADGE.get(presence):
            label = f"{label} ({PRESENCE_BADGE[presence]})"
        header_role = header.role
        if header.is_aio or header_role == "pump":
            aio_tag = aio_tag_for(label, header_role)
            label += aio_tag
            clean_label += aio_tag  # role-bearing — see above

        tip_parts = [f"ID: {header.id}"]
        if header.chip_name:
            tip_parts.append(f"Chip: {header.chip_name}")
            g = lookup_chip_guidance(header.chip_name)
            if g:
                st = "mainline" if g.in_mainline else g.driver_package
                tip_parts.append(f"Driver: {g.driver_name} ({st})")
        if presence != FanPresence.PRESENT:
            tip_parts.append(PRESENCE_TOOLTIP.get(presence, ""))

        available.append(
            {
                "id": header.id,
                "source": "hwmon",
                "label": label,
                "clean_label": clean_label,
                "rpm": None,  # header with no live fan reading → "no fan"
                "tooltip": "\n".join(p for p in tip_parts if p),
            }
        )

    return available


def assigned_elsewhere_map(controls, exclude_control_id: str) -> dict[str, str]:
    """``member_id`` → owning control name, for every control *except* the one
    being edited. Membership is exclusive, so the picker greys these out."""
    return {
        m.member_id: ctrl.name
        for ctrl in controls
        if ctrl.id != exclude_control_id
        for m in ctrl.members
    }


@dataclass(frozen=True)
class ReservationNote:
    """Why the picker warns before taking a fan out of the cooling stack."""

    #: Appended to the row label, e.g. "(Part of: AIO Cooling System)".
    text: str
    #: Shown on hover, and the body of the confirmation.
    tooltip: str
    #: The confirmation title. Kept alongside the text so the widget renders a
    #: view-model rather than composing safety copy of its own.
    title: str


def cooling_device_reservations(
    index: dict[str, CoolingMembership],
    *,
    exempt_ids: Iterable[str] = (),
) -> dict[str, ReservationNote]:
    """``member_id`` → the note to warn with, for every fan the cooling stack claims.

    The soft counterpart to :func:`assigned_elsewhere_map` (AIO-MB Phase 7,
    Decision 3): that map *disables* a row because membership of a control is
    genuinely exclusive, whereas a cooling device is metadata and taking a fan
    from it is allowed — the user is told what they are doing, not stopped.

    **``exempt_ids`` is required for correctness, not convenience.** Pass the
    members the control being edited *already* has. Without it, a user who
    removes a radiator fan from the AIO control mid-dialog cannot put it back:
    the row returns to the Available side still reserved, and warns again about
    a device the fan is being restored to. Exempting the dialog's own starting
    members makes removal and re-addition symmetrical, which is what a user
    expects from a two-list picker.

    Two shapes of copy, because there are two shapes of claim. A configured
    device has a name and a documented release path. A bare header *role* has
    neither — so it must not borrow the device wording and assert a device that
    was never created.
    """
    exempt = set(exempt_ids)
    notes: dict[str, ReservationNote] = {}
    for member_id, membership in index.items():
        if member_id in exempt:
            continue
        if membership.from_device:
            notes[member_id] = ReservationNote(
                text=f"(Part of: {membership.device_name})",
                tooltip=(
                    f"This fan is part of {membership.device_name} "
                    f"({membership.role_label.lower()}). Assigning it here takes it "
                    "out of that group. To release it permanently, forget the device "
                    "on the Hardware page or re-run Configure AIO."
                ),
                title="Take this fan out of the cooling device?",
            )
        elif membership.role == "pump" and not membership.assigned:
            # A pump the daemon protects on evidence other than an assignment:
            # its label, a cooler's channel 1, or — since DEC-384 — the name the
            # active profile gives it. No role edit releases any of those, and
            # the last follows whichever profile is active, not the one being
            # edited, so this names the reasons and offers no remedy.
            notes[member_id] = ReservationNote(
                text="(Pump)",
                tooltip=(
                    "The daemon currently treats this header as a pump — because "
                    "of its label or hardware, or because the active profile names "
                    "this fan a pump or AIO. Assigning it to an unrelated curve is "
                    "allowed, but it is usually not what you want."
                ),
                title="Assign the pump to this curve?",
            )
        elif membership.role == "radiator":
            # `TS-ai`: no GUI route clears a `radiator_fan` role — Configure AIO
            # and the wizard clear only a user-assigned PUMP — so neither shape
            # offers a remedy, as the pump branch above does not. Keyed on
            # `assigned`: a liquid cooler's non-pump channel is `radiator_fan` by
            # chip mapping with nothing assigned, and must not be told it was.
            if membership.assigned:
                text = f"({membership.role_label} role assigned)"
                why = (
                    f"You assigned this header the {membership.role_label.lower()} "
                    "role in the Fan Wizard"
                )
            else:
                text = f"({membership.role_label})"
                why = (
                    "The daemon treats this header as a liquid cooler's "
                    f"{membership.role_label.lower()} because of its hardware"
                )
            notes[member_id] = ReservationNote(
                text=text,
                tooltip=(
                    f"{why}. Assigning it to an unrelated curve is allowed, but it "
                    "is usually not what you want."
                ),
                title=f"Assign the {membership.role_label.lower()} to this curve?",
            )
        else:
            notes[member_id] = ReservationNote(
                text=f"({membership.role_label} role assigned)",
                tooltip=(
                    f"This header is assigned the {membership.role_label.lower()} "
                    "role, so the daemon treats it as part of the cooling stack. "
                    "Assigning it to an unrelated curve is allowed, but it is "
                    "usually not what you want. Clear the role in Configure AIO to "
                    "release it."
                ),
                title=f"Assign the {membership.role_label.lower()} to this curve?",
            )
    return notes


def build_radiator_candidates(
    fans,
    headers,
    *,
    pump_id: str | None,
    preselect_ids: set,
    display_name,
) -> list[dict]:
    """Rows for the AIO wizard's radiator picker (DEC-157): writable hwmon +
    OpenFan outputs, minus the pump itself.

    ``preselect`` ticks a row that the detector already matched, that the header
    reports as liquid-cooled, or whose name says "radiator". GPU fans of every
    vendor are excluded — a GPU fan is never an AIO radiator fan.
    """
    header_by_id = {h.id: h for h in headers}
    candidates: list[dict] = []
    seen: set[str] = set()

    for fan in fans:
        if fan.source in ("amd_gpu", "intel_gpu", "nvidia_gpu"):
            continue
        if fan.source == "hwmon":
            h = header_by_id.get(fan.id)
            if h is None or not h.is_writable:
                continue
        if fan.id == pump_id or fan.id in seen:
            continue
        seen.add(fan.id)
        label = display_name(fan.id)
        candidates.append(
            {
                "id": fan.id,
                "source": fan.source,
                "label": label,
                "preselect": fan.id in preselect_ids or "radiator" in label.lower(),
            }
        )

    for header in headers:
        if not header.is_writable or header.id == pump_id or header.id in seen:
            continue
        # Resolved, not raw ``header.label`` (DEC-229) — matches the fan branch
        # and keeps a placeholder "pwm1" out of the radiator picker.
        label = display_name(header.id) or header.id
        candidates.append(
            {
                "id": header.id,
                "source": "hwmon",
                "label": label,
                "preselect": header.id in preselect_ids
                or header.is_aio
                or "radiator" in label.lower(),
            }
        )

    return candidates


def build_pump_role_candidates(headers, *, display_name) -> list[dict]:
    """Rows for the Configure-AIO pump picker (DEC-312).

    Every writable hwmon header, because on the boards this exists for nothing
    distinguishes them: an ``it87`` with no ``pwmN_label`` files reports five
    identical ``unknown`` channels, and which one the pump is plugged into is a
    fact only the person who built the machine has. Filtering by any heuristic
    would hide the right answer on exactly the hardware that needs it.

    The current role is rendered as its RAW token rather than a mapped display
    string (the 273-i rule): a token this GUI does not recognise — because it was
    added by a newer daemon — must still be visible, never silently dropped or
    shown as "unknown".
    """
    rows: list[dict] = []
    for h in headers:
        if not h.is_writable:
            continue
        label = display_name(h.id) or h.id
        role = h.role or "unknown"
        if role != "unknown":
            label = f"{label} — {role}"
        rows.append(
            {
                "id": h.id,
                "label": label,
                "role": role,
                "role_source": h.role_source,
            }
        )
    return rows


def build_sensor_choices(sensors, overrides: dict) -> list[dict]:
    """AIO wizard sensor rows, flagging coolant + CPU sensors as ``preferred``
    (the recommended bindings for a radiator curve, DEC-157).

    ``coolant`` is the row's own :func:`sensor_is_coolant` answer, which is what
    the dialog stores as the device's ``coolant_sensor`` (`TS-v`); the page seeds
    the curves' calibration from the same predicate.
    """
    choices: list[dict] = []
    for s in sensors:
        coolant = sensor_is_coolant(s, overrides)
        preferred = coolant or s.kind == "cpu_temp"
        choices.append({"id": s.id, "label": s.label, "preferred": preferred, "coolant": coolant})
    return choices


def parse_stored_card_size(raw) -> tuple[int, int] | None:
    """Validate a persisted ``[w, h]`` card-size override; ``None`` if malformed."""
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            return (int(raw[0]), int(raw[1]))
        except (TypeError, ValueError):
            return None
    return None


def prune_card_sizes(sizes: dict, known_ids: set) -> None:
    """Drop card-size overrides for ids absent from ``known_ids`` (mutates in place).

    ``known_ids`` is keyed across *all* profiles (not just the active one) so
    switching profiles never sheds an inactive profile's card sizes.
    """
    for stale in [card_id for card_id in sizes if card_id not in known_ids]:
        del sizes[stale]
