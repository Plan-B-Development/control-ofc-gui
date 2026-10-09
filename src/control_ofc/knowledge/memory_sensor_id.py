"""Memory-module sensor ids in both forms, and the cross-form re-key rule (DEC-492).

Mirrors the daemon's ``hwmon::memory_id``; both are pinned to the shared oracle
``tests/fixtures/memory_sensor_ids.json``.

A memory-module sensor (``spd5118``, ``jc42``) is an i2c client. Its id used to
embed the kernel's dynamic bus number (``hwmon:spd5118:21-0051:temp1``, the
*legacy* form); a DEC-492 daemon names it by SMBus controller, port or mux channel
and SPD address instead (``hwmon:spd5118:0000:00:14.0-p0-0051:temp1``, the
*stable* form), falling back to the legacy form where the topology cannot be named.

:func:`resolve_memory_sensor_id` is the one rule both sides use to carry a saved id
across: a live id is itself. Otherwise every other id with the same chip, SPD
address and label is a candidate — live or quarantined (``unavailable``, which the
daemon evicts from the live set), in either form — and the id moves only when there
is exactly one, it is live, and it is of the **other** form. A quarantined or
same-form twin therefore blocks the move rather than letting the remaining module
inherit the saved one's settings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# DEC-491: memory-module temperature sensors, mirrored from the daemon's
# `classify::MEMORY_MODULE_CHIPS`. `spd5118` is the DDR5 SPD hub (kernel 6.11+),
# `jc42` the JEDEC JC-42.4 sensor on a DDR4/DDR3 module. Both sit on the board's
# SMBus, one device per module, and both publish a bare `temp1`. Defined here, not
# in `sensor_knowledge`, which imports this module.
MEMORY_MODULE_CHIPS = frozenset({"spd5118", "jc42"})

# ASCII digits only (`\d` also matches non-ASCII digits the daemon never emits),
# always with `fullmatch`: `$` also matches before a trailing newline, which the
# daemon's byte checks reject.
_ADDR4 = re.compile(r"[0-9a-f]{4}")
_DIGITS = re.compile(r"[0-9]+")
_SEGMENT = re.compile(r"(?:p|ch)[0-9]+")


@dataclass(frozen=True)
class MemorySensorId:
    chip: str
    legacy: bool
    controller: str
    segments: tuple[str, ...]
    address: int
    label: str


def parse_memory_sensor_id(sensor_id: str) -> MemorySensorId | None:
    """Parse ``hwmon:<memory chip>:<device>:<label>``; ``None`` for anything else.

    The label is the last ``:``-separated part and the device everything between
    the chip and it (a PCI controller has colons of its own).
    """
    if not sensor_id.startswith("hwmon:"):
        return None
    chip, sep, rest = sensor_id[len("hwmon:") :].partition(":")
    if not sep or chip not in MEMORY_MODULE_CHIPS:
        return None
    device, sep, label = rest.rpartition(":")
    if not sep or not device or not label:
        return None
    head, sep, addr = device.rpartition("-")
    if not sep or not head or not _ADDR4.fullmatch(addr):
        return None
    address = int(addr, 16)
    if _DIGITS.fullmatch(head):
        return MemorySensorId(chip, True, "", (), address, label)
    controller = head
    segments: list[str] = []
    while True:
        before, sep, last = controller.rpartition("-")
        if not sep or not before or not _SEGMENT.fullmatch(last):
            break
        segments.append(last)
        controller = before
    segments.reverse()
    return MemorySensorId(chip, False, controller, tuple(segments), address, label)


def resolve_memory_sensor_id(
    saved: str, live: Iterable[str], unavailable: Iterable[str]
) -> str | None:
    """The live id ``saved`` refers to, or ``None`` (see the module docstring).

    ``unavailable`` are the ids the daemon reports quarantined
    (``unavailable_sensors[]``); they can only block a move, never receive one.
    Required, so no caller can silently drop them.
    """
    live = list(live)
    if saved in live:
        return saved
    wanted = parse_memory_sensor_id(saved)
    if wanted is None:
        return None
    live_set = set(live)
    candidates = []
    for sid in dict.fromkeys([*live, *unavailable]):
        if sid == saved:
            continue
        p = parse_memory_sensor_id(sid)
        if (
            p is not None
            and p.chip == wanted.chip
            and p.address == wanted.address
            and p.label == wanted.label
        ):
            candidates.append((sid, p))
    if len(candidates) != 1:
        return None
    sid, p = candidates[0]
    return sid if sid in live_set and p.legacy != wanted.legacy else None
