"""Re-key saved fan names when a driver reshapes a hwmon fan id (DEC-247).

Qt-free; the view-model half of the house pattern.

A hwmon fan id embeds the sysfs label and the device-symlink shape —
``hwmon:{chip}:{device_id}:pwm{N}:{label}`` (``pwm_discovery.rs``). A kernel or
driver update that starts or stops publishing ``fanN_label``, or that changes what
``device`` points at, therefore renames every id on that chip, and the user's saved
names silently stop matching anything. Kernels ride the same ``pacman -Syu`` as the
daemon, so this is genuinely update-sensitive.

**Only hwmon fans are affected.** ``openfan:ch07`` is a channel number and
``amd_gpu:0000:03:00.0`` is a PCI BDF — both stable by construction, so neither can
be reshaped this way. Narrowing to hwmon is not a simplification, it is the whole
population.

**Why re-keying cannot lower a safety floor.** A fan alias becomes
``ControlMember.member_label``, which selects the DEC-095/162 30% CPU/pump floor, so
a wrong match would be a safety question if two things were not already true:

* ``infer_member_role`` grants the 30% tier only to ``source == "hwmon"`` members —
  and a re-key never changes a member's source; and
* ``role_preserving_label`` **never lowers the inferred role**: where the hardware's
  resolved label carries "cpu"/"pump" and the alias does not, the hardware label is
  what gets persisted.

So the worst a wrong match can do is put a confusing *name* on a fan. It is still
kept deliberately conservative below, because a confusing name on a cooler is its own
kind of harm.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# The ``pwmN`` segment is the anchor for parsing. Splitting on ":" alone is
# ambiguous — a device id can itself contain colons (``0000:2d:00.0``) — but the
# pwm index is unmistakable and always sits immediately before the label.
_PWM_SEGMENT = re.compile(r"^pwm(\d+)$")


def parse_hwmon_fan_id(fan_id: str) -> tuple[str, int] | None:
    """``(chip, pwm_index)`` for a hwmon fan id, else ``None``.

    The pair is deliberately everything *except* the two volatile segments: the
    device id and the label are exactly what a driver update rewrites, while the
    chip name and the pwm index identify the same physical header across the change.
    """
    parts = fan_id.split(":")
    if len(parts) < 4 or parts[0] != "hwmon":
        return None
    for part in parts[2:]:
        m = _PWM_SEGMENT.match(part)
        if m:
            return parts[1], int(m.group(1))
    return None


def find_realias_moves(
    fan_aliases: dict[str, str],
    live_fan_ids: Iterable[str],
) -> dict[str, str]:
    """``{old_id: new_id}`` for saved names whose header came back under a new id.

    Every condition here exists to refuse an uncertain match rather than guess:

    * the saved id must name **no** live fan — a name still pointing at present
      hardware is not orphaned and must never be moved;
    * both sides must parse as hwmon fans with the same ``(chip, pwm index)``;
    * exactly **one** live candidate may match. Two identical chips on one board
      would otherwise make the choice a coin flip;
    * the candidate must not already carry an alias of its own — the user's
      existing name always wins over a resurrected one;
    * no two orphans may claim the same candidate.

    Returns an empty mapping when ``live_fan_ids`` is empty: nothing is known yet
    (disconnected, or before the first poll), so every saved name would look
    orphaned and there would be nothing sound to match against.
    """
    live = set(live_fan_ids)
    if not live:
        return {}

    by_slot: dict[tuple[str, int], list[str]] = {}
    for fan_id in live:
        slot = parse_hwmon_fan_id(fan_id)
        if slot is not None:
            by_slot.setdefault(slot, []).append(fan_id)

    moves: dict[str, str] = {}
    claimed: set[str] = set()
    for old_id in sorted(fan_aliases):
        if old_id in live:
            continue
        slot = parse_hwmon_fan_id(old_id)
        if slot is None:
            continue
        candidates = [c for c in by_slot.get(slot, []) if c not in fan_aliases]
        if len(candidates) != 1 or candidates[0] in claimed:
            continue
        moves[old_id] = candidates[0]
        claimed.add(candidates[0])
    return moves


def apply_realias_moves(fan_aliases: dict[str, str], moves: dict[str, str]) -> dict[str, str]:
    """``fan_aliases`` with each move applied. The old key is dropped, not kept.

    Keeping both would leave the stale entry to surface as a phantom row in the
    Fan Names card and as an orphan in the DEC-246 prune — the name has moved, it
    has not been copied.
    """
    updated = dict(fan_aliases)
    for old_id, new_id in moves.items():
        name = updated.pop(old_id, None)
        if name is not None:
            updated[new_id] = name
    return updated
