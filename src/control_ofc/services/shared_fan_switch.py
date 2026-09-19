"""A Dell machine's one BIOS fan switch: a profile controls all of its fans or none.

`TS-bb`, DEC-403. On many Dell machines the mainline ``dell_smm`` driver has ONE
switch for the BIOS's fan control, shared by every fan: it sits on the first
header as a write-only ``pwm1_enable``, and no other fan has an enable file.
Taking ``pwm1`` turns the BIOS off for every fan, and giving it back turns it on
for every fan. So a profile that controls only some of those fans leaves the
rest with nothing controlling them, or hands a fan it still names to the BIOS.

The user chose to enforce the rule here, in the GUI, when a profile is SAVED
(which activation does first), rather than in the daemon. Every profile saved
through the GUI from then on complies, so the tray and the daemon's boot
activation start only compliant ones; a profile saved earlier, or imported, is
flagged on the Controls page until it is fixed.

Only machines with the shared switch are affected. The same driver can instead
give every fan its own ``pwmN_enable``, and then nothing couples the fans. The
two are told apart from ``/hwmon/headers`` alone: ``supports_enable`` is whether
the ``pwmN_enable`` file exists, and on the shared kind only ``pwm1`` has one.

Qt-free on purpose (the view-model convention): the rule is tested here, and the
service and the page only call it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from control_ofc.api.models import HwmonHeader
    from control_ofc.services.profile_service import Profile

DELL_SMM_CHIP = "dell_smm"


@dataclass(frozen=True)
class SharedSwitchViolation:
    """One shared switch a profile controls only part of.

    ``named`` are the fans the profile controls and ``missing`` the ones it does
    not; both non-empty, both header ids in ``pwm`` order.
    """

    named: tuple[str, ...]
    missing: tuple[str, ...]


class SharedSwitchRuleError(Exception):
    """Raised instead of saving a profile that breaks the rule (DEC-403).

    Carries the violations for callers that want the ids, and a message that
    already names the fans the way the rest of the GUI does.
    """

    def __init__(self, violations: tuple[SharedSwitchViolation, ...], message: str) -> None:
        super().__init__(message)
        self.violations = violations
        self.message = message


def shared_switch_groups(headers: Iterable[HwmonHeader]) -> tuple[tuple[str, ...], ...]:
    """Every writable fan behind each shared ``dell_smm`` switch, per device.

    A device qualifies when its writable ``pwm1`` has an enable file and at least
    one of its other writable fans has none. Read-only headers are left out: a
    profile cannot name them, so "all of them" cannot include them.
    """
    by_device: dict[str, list[HwmonHeader]] = {}
    for h in headers:
        if h.chip_name == DELL_SMM_CHIP and h.is_writable:
            by_device.setdefault(h.device_id, []).append(h)
    groups: list[tuple[str, ...]] = []
    for fans in by_device.values():
        fans.sort(key=lambda h: h.pwm_index)
        switch = next((h for h in fans if h.pwm_index == 1), None)
        if switch is None or not switch.supports_enable:
            continue
        if all(h.supports_enable for h in fans):
            continue  # a per-fan switch on every fan: nothing is coupled
        groups.append(tuple(h.id for h in fans))
    return tuple(groups)


def shared_switch_violations(
    profile: Profile, headers: Iterable[HwmonHeader]
) -> tuple[SharedSwitchViolation, ...]:
    """The shared switches ``profile`` controls some but not all of the fans of."""
    named_ids = {m.member_id for c in profile.controls for m in c.members if m.source == "hwmon"}
    violations: list[SharedSwitchViolation] = []
    for group in shared_switch_groups(headers):
        named = tuple(i for i in group if i in named_ids)
        if named and len(named) < len(group):
            missing = tuple(i for i in group if i not in named_ids)
            violations.append(SharedSwitchViolation(named=named, missing=missing))
    return tuple(violations)


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def describe_shared_switch_violations(
    violations: Iterable[SharedSwitchViolation], name_of: Callable[[str], str]
) -> str:
    """The user-facing explanation, naming each fan through ``name_of``."""
    parts = [
        "On this Dell, one BIOS switch controls all of its fans, so a profile must "
        "control all of them or none."
    ]
    for v in violations:
        missing = _join([name_of(i) for i in v.missing])
        named = _join([name_of(i) for i in v.named])
        parts.append(f"Add {missing} to a fan role, or remove {named}.")
    return " ".join(parts)
