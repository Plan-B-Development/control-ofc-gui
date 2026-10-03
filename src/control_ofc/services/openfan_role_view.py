"""View-model for roles on OpenFan channels (`ROLE-f`, DEC-475).

Qt-free, in the ``services/*_view.py`` pattern. The Hardware page's OpenFan
Channels list and its role picker decide nothing but layout; this module
decides which channels are listed, what each choice means on a channel, and
what to tell the user after a write.

An OpenFan channel has no label and no chip, so the user's ``pump`` assignment
is the only pump evidence the daemon can hold for it. Protection is read back
from the daemon's ``stop_permitted`` (``openfan_channel_is_pump_protected``),
never predicted from the choice. The choice semantics (assignment only, a
clear falls back, a removed pump role asks first) are ``header_role_view``'s,
reused through ``plan_role_change``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..api.models import Capabilities, HeaderRoleResult, OpenFanRole
from .daemon_features import daemon_supports
from .header_inspector_view import role_label
from .header_role_view import (
    ROLE_CHASSIS,
    ROLE_CPU,
    ROLE_NO_FAN,
    ROLE_PUMP,
    ROLE_RADIATOR,
    RoleChoice,
)
from .pump_protection import openfan_channel_is_pump_protected

#: The picker for an OpenFan channel. Same tokens and order as the hwmon
#: picker, with effects that are true on a channel: there is no hardware label
#: to fall back to, and no stall/restart probe.
OPENFAN_ROLE_CHOICES: tuple[RoleChoice, ...] = (
    RoleChoice(
        ROLE_PUMP,
        "Pump",
        "The daemon never stops it, holds it at or above the pump safety floor, "
        "and will not calibrate it.",
    ),
    RoleChoice(ROLE_CHASSIS, "Chassis fan", "A case fan. Changes the name shown only."),
    RoleChoice(
        ROLE_RADIATOR,
        "Radiator fan",
        "A fan on a liquid cooler's radiator. Changes the name shown only.",
    ),
    RoleChoice(
        ROLE_CPU,
        "CPU fan",
        "Changes the name shown only: an assigned CPU-fan role adds no floor.",
    ),
    RoleChoice(
        ROLE_NO_FAN,
        "No fan",
        "Nothing is plugged into this channel. Changes the name shown only.",
    ),
    RoleChoice(
        None,
        "Not set",
        "Removes your assignment. The channel goes back to Unclassified — the "
        "daemon has no other way to tell what an OpenFan channel drives.",
    ),
)

OPENFAN_ROLE_INTRO = (
    "What does {name} drive? An OpenFan channel has no hardware label, so this "
    "assignment is the only way the daemon knows a pump is plugged into it."
)


def openfan_roles_supported(capabilities: Capabilities | None) -> bool:
    """Whether this daemon accepts roles on OpenFan channels."""
    return daemon_supports("openfan_header_roles", capabilities) is True


def openfan_role_choices(capabilities: Capabilities | None) -> tuple[RoleChoice, ...]:
    """The picker's choices for this daemon: "No fan" only where it is accepted."""
    if daemon_supports("header_role_no_fan", capabilities) is True:
        return OPENFAN_ROLE_CHOICES
    return tuple(c for c in OPENFAN_ROLE_CHOICES if c.token != ROLE_NO_FAN)


@dataclass(frozen=True)
class OpenFanRoleRow:
    """One line of the Hardware page's OpenFan Channels list."""

    fan_id: str
    name: str
    #: The pill: the role's name, and "not reporting" for an absent channel.
    role_text: str
    protected: bool
    #: ``StatusPill`` state.
    tone: str


def build_openfan_role_rows(
    fans: Iterable[object],
    roles: Iterable[OpenFanRole],
    display_name: Callable[[str], str],
) -> list[OpenFanRoleRow]:
    """Every OpenFan channel ``/fans`` reports, plus any carrying an assignment.

    The daemon lists every channel the controller can have; showing all ten on
    a machine using three would be noise. A channel with an assignment is kept
    even when it is not reporting (controller unplugged), so the assignment
    stays visible and clearable.
    """
    by_id = {r.fan_id: r for r in roles}
    reporting = {getattr(f, "id", "") for f in fans if getattr(f, "source", "") == "openfan"}
    rows: list[OpenFanRoleRow] = []
    for role in sorted(by_id.values(), key=lambda r: r.channel):
        if role.fan_id not in reporting and role.role_source != "user_assigned":
            continue
        protected = openfan_channel_is_pump_protected(role)
        text = role_label(role.role)
        if role.fan_id not in reporting:
            text += " · not reporting"
        rows.append(
            OpenFanRoleRow(
                fan_id=role.fan_id,
                name=display_name(role.fan_id),
                role_text=text,
                protected=protected,
                tone="success" if protected else "neutral",
            )
        )
    return rows


def openfan_outcome_message(
    name: str, result: HeaderRoleResult, refreshed: OpenFanRole | None
) -> str:
    """What happened, from the daemon's answer — never from the request."""
    effective = role_label(result.effective_role)
    if result.role is None:
        text = f"Your role assignment on {name} was removed. It now reads as {effective}."
    else:
        text = f"{name} is now set to {effective}."
    if refreshed is None:
        return (
            text + " The channel list could not be re-read, so it may lag until the next refresh."
        )
    if openfan_channel_is_pump_protected(refreshed):
        text += " The daemon now protects it as a pump: it is never stopped or calibrated."
    return text
