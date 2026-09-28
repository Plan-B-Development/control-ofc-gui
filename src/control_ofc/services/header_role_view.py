"""View-model for setting a header's role, and for nct6687d's MSI labels (DEC-444).

Qt-free, in the ``services/*_view.py`` pattern. The Hardware page's header card
and the PWM Test Report's scope page both offer the same role picker; this
module is the one place that decides what each choice means, whether a change
is a no-op, whether it removes pump protection, and what to tell the user
afterwards. The dialog and both callers decide nothing but layout.

Three rules from DEC-311/312 shape everything here:

* **A role write can only ever change the user's ASSIGNMENT.** A clear drops the
  stored assignment and the daemon falls back to its own inference (label or
  chip), so a role the hardware gave cannot be removed — and the picker's
  "Not set" says exactly that rather than promising "unclassified".
* **Protection is a union, the wire ``role`` is display only.** A header the
  hardware labels ``PUMP`` stays protected whatever the user assigns. So the
  outcome is read back from the daemon (``effective_role`` and the re-read
  header's ``stop_permitted``), never predicted from the choice.
* **Only ``pump`` feeds a floor.** An assigned ``cpu_fan`` adds none — the CPU
  floor comes from a label that names the CPU fan (daemon
  ``profile.rs::assigned_role_is_pump`` is the only floor term an assignment
  feeds). The copy says so instead of implying a protection that is not there.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..api.models import Capabilities, HeaderRoleResult, HwmonHeader
from ..knowledge.hwmon_label_resolver import is_placeholder_hwmon_label
from .daemon_features import daemon_supports, unsupported_feature_message
from .header_inspector_view import role_label, role_source_label
from .pump_protection import header_is_pump_protected

ROLE_PUMP = "pump"
ROLE_CHASSIS = "chassis_fan"
ROLE_RADIATOR = "radiator_fan"
ROLE_CPU = "cpu_fan"


@dataclass(frozen=True)
class RoleChoice:
    """One option in the picker. ``token`` is ``None`` for "Not set" (a clear)."""

    token: str | None
    label: str
    effect: str


#: Picker order: the protective role first, then the two that unlock the stall
#: probe, then the display-only one, then the clear. Never an explicit
#: ``unknown`` — the user chose (Q3) that removing means falling back to what the
#: hardware reports, and an explicit ``unknown`` would instead *override* a
#: label-derived role for display.
ROLE_CHOICES: tuple[RoleChoice, ...] = (
    RoleChoice(
        ROLE_PUMP,
        "Pump",
        "The daemon never stops it and holds it at or above the pump safety floor.",
    ),
    RoleChoice(
        ROLE_CHASSIS,
        "Chassis fan",
        "A case fan. Adds no floor. Makes the PWM Test Report's stall/restart "
        "probe available on this header.",
    ),
    RoleChoice(
        ROLE_RADIATOR,
        "Radiator fan",
        "A fan on a liquid cooler's radiator. Adds no floor. Makes the stall/restart "
        "probe available on this header.",
    ),
    RoleChoice(
        ROLE_CPU,
        "CPU fan",
        "Changes the name shown only: an assigned CPU-fan role adds no floor (a "
        "header whose hardware label names the CPU fan is floored by that label). "
        "The stall/restart probe is not offered on a CPU fan.",
    ),
    RoleChoice(
        None,
        "Not set",
        "Removes your assignment. The header goes back to the role its hardware "
        "label or chip gives it, or to Unclassified if it has none.",
    ),
)


def current_choice(header: HwmonHeader) -> str | None:
    """The picker's pre-selection: the user's own assignment, else "Not set".

    A role the daemon inferred is *not* pre-selected as that role, because the
    picker edits the assignment and there is none — choosing that role again
    would store one, which is a change, not a no-op.
    """
    return header.role if header.role_source == "user_assigned" else None


@dataclass(frozen=True)
class RolePlan:
    """What choosing ``new_role`` on a header will write."""

    header_id: str
    new_role: str | None
    #: True when nothing would change daemon-side, so nothing is sent.
    noop: bool
    #: True when this removes a pump role the USER assigned — the only role write
    #: that can lower protection, and so the only one that asks first.
    removes_user_pump: bool


def plan_role_change(header: HwmonHeader, new_role: str | None) -> RolePlan:
    user_assigned = header.role_source == "user_assigned"
    # A clear changes something only if there is an assignment to drop; a set
    # is a no-op only when it restates the user's own assignment.
    noop = not user_assigned if new_role is None else (user_assigned and header.role == new_role)
    removes_user_pump = (
        not noop and user_assigned and header.role == ROLE_PUMP and new_role != ROLE_PUMP
    )
    return RolePlan(header.id, new_role, noop, removes_user_pump)


def role_editable(header: HwmonHeader, capabilities: Capabilities | None) -> tuple[bool, str]:
    """Whether the picker is offered on this header, and why not when it is not.

    Read-only headers are refused (Q4): the daemon never drives one, so a role
    there buys neither a floor nor a probe. A running PWM Test Report does NOT
    disable it — the daemon re-checks the role at every step of a run.
    """
    if daemon_supports("pump_protection", capabilities) is not True:
        return False, unsupported_feature_message("pump_protection")
    if not header.is_writable:
        return (
            False,
            "This header is read-only, so the daemon never drives it and a role changes nothing.",
        )
    return True, ""


def outcome_message(
    name: str,
    result: HeaderRoleResult,
    refreshed: HwmonHeader | None,
    capabilities: Capabilities | None,
) -> str:
    """What happened, from the daemon's answer — never from the request.

    ``effective_role`` is what the daemon resolved; the re-read header's
    ``stop_permitted`` (via ``header_is_pump_protected``) is whether it is
    protected now. A header the daemon still protects after a non-pump choice
    says so, because the union keeps a label- or profile-derived pump protected.
    """
    effective = role_label(result.effective_role)
    if result.role is None:
        text = (
            f"Your role assignment on {name} was removed. The daemon now reads it as {effective}."
        )
    else:
        text = f"{name} is now set to {effective}."
    if result.role == ROLE_CPU:
        text += " An assigned CPU-fan role adds no floor."
    if refreshed is not None and result.effective_role != ROLE_PUMP:
        if header_is_pump_protected(refreshed, capabilities):
            text += (
                " The daemon still protects it as a pump — from its hardware label, or "
                "because the active profile names it a pump — so it is never stopped."
            )
    elif refreshed is None:
        text += (
            " The header list could not be re-read, so the cards may lag until the next refresh."
        )
    return text


def failure_message(name: str, role: str | None, error: object, *, reread: bool = True) -> str:
    """Why a role write failed — claiming "nothing changed" only when it is known.

    A daemon error envelope (an HTTP status) is a refusal: the daemon persists
    first, so a 400 or 503 changed nothing. A timeout or a dropped connection
    proves nothing either way — the write may have landed — so the message says
    so, and the caller re-reads the headers to show what the daemon now reports.
    ``reread`` is whether that re-read succeeded; when it did not, the card may
    still show the old role, and the message must not claim otherwise.
    """
    what = "remove the role from" if role is None else f"set {role_label(role)} on"
    if isinstance(getattr(error, "status", 0), int) and getattr(error, "status", 0) > 0:
        return f"Could not {what} {name}: {error}. Nothing was changed."
    if not reread:
        return (
            f"Could not {what} {name}: {error}. The daemon did not confirm the change, "
            "and the header list could not be re-read, so the card may not show the "
            "role the daemon now has."
        )
    return (
        f"Could not {what} {name}: {error}. The daemon did not confirm the change; "
        "the header now shows what the daemon reports."
    )


# ── BRD-h: nct6687d's MSI fan labels on other vendors' boards ────────────────
#
# The out-of-tree nct6687d (Fred78290) publishes MSI's fan names as `fanN_label`
# on every board it binds, whatever the vendor (verified against `nct6687.c`,
# 2026-09-28: `nct6687_fan_config_default` + the MSI alt map's "Pump Fan #2"). It
# registers its hwmon device as nct6683, nct6686 or nct6687 by chip. The
# in-kernel nct6683 driver publishes NO fan or pwm labels, so these exact
# strings on an nct668x chip identify nct6687d without knowing which driver is
# bound. The daemon reads `fanN_label` when `pwmN_label` is absent, so "Pump
# Fan" becomes a label-derived pump — on an ASRock Taichi that is a chassis
# header, while the real AIO_PUMP sits unlabelled on the nct6799 chip
# (nct6687d #155).

NCT6687D_FAN_LABELS = frozenset(
    {
        "CPU Fan",
        "Pump Fan",
        "Pump Fan #2",
        *(f"System Fan #{n}" for n in range(1, 7)),
    }
)
_NCT668X_PREFIX = "nct668"
_MSI_VENDOR_HINT = "micro-star"


def nct6687_label_unverified(header: HwmonHeader, board_vendor: str) -> bool:
    """True when this header's name is nct6687d's MSI label on a non-MSI board.

    An unknown vendor is NOT flagged: without the DMI string there is no evidence
    the board is anything but MSI, and a caveat on every nct668x header of an
    MSI board would be noise.
    """
    vendor = (board_vendor or "").strip().lower()
    if not vendor or _MSI_VENDOR_HINT in vendor:
        return False
    if not (header.chip_name or "").lower().startswith(_NCT668X_PREFIX):
        return False
    label = header.label or ""
    if is_placeholder_hwmon_label(label, header.pwm_index):
        return False
    return label in NCT6687D_FAN_LABELS


def nct6687_label_note(header: HwmonHeader, board_vendor: str) -> str:
    """The card's caveat for a flagged header, or ``""``."""
    if not nct6687_label_unverified(header, board_vendor):
        return ""
    if header.label.startswith("Pump Fan"):
        return (
            f"Label unverified: nct6687d names this “{header.label}” on every board. "
            "On this board it may be a case-fan header, and your real pump may be on "
            "a header with no name — use Set role… on the header the pump is plugged into."
        )
    return (
        f"Label unverified: nct6687d uses MSI's header names on every board, so "
        f"“{header.label}” may not match what is printed on this board."
    )


@dataclass(frozen=True)
class LabelPromptView:
    """The Hardware page's prompt to name the real pump (BRD-h, Q6-A)."""

    #: The dismissal key: the flagged chips' device ids, so new hardware asks again.
    key: str
    text: str


def nct6687_label_prompt(
    headers: list[HwmonHeader], board_vendor: str, confirmed: frozenset[str] | set[str]
) -> LabelPromptView | None:
    """The prompt, or ``None`` when there is nothing to ask.

    Hidden once any header carries a pump role the user assigned — the question
    has been answered — and once the user said the labels are correct for these
    chips on this machine.
    """
    flagged = [h for h in headers if nct6687_label_unverified(h, board_vendor)]
    if not flagged:
        return None
    if any(h.role == ROLE_PUMP and h.role_source == "user_assigned" for h in headers):
        return None
    key = "nct6687-labels:" + ",".join(sorted({h.device_id or h.chip_name for h in flagged}))
    if key in confirmed:
        return None
    return LabelPromptView(
        key=key,
        text=(
            "Some headers here are named by the nct6687d driver, which uses MSI's fan "
            "names on every board. On this board its “Pump Fan” may be a case-fan "
            "header, and a real pump on a header with no name gets no pump protection "
            "until you name it. If you have a liquid cooler, use Set role… on the "
            "header its pump is plugged into. If the names match your board, or there "
            "is no pump, choose “Labels are correct”."
        ),
    )


def role_cell_text(header_role: str, role_source: str) -> str:
    """The PWM Test Report's Role cell: readable role, and who set it."""
    return f"{role_label(header_role)} · {role_source_label(role_source)}"
