"""Carry saved memory-sensor ids across the DEC-492 re-key.

Qt-free; the view-model half of the house pattern, beside ``id_migration``
(DEC-247), whose shape it follows: match against the **live** set, accept a single
candidate only, and let the caller announce the moves.

A DEC-492 daemon names a memory-module sensor by its SMBus controller, port and
SPD address instead of the kernel's dynamic bus number, so every id saved against
the old form stops matching. Unlike the DEC-442 chip-name clean-up this cannot be
a pure string function — the old id does not contain the topology — so the rule is
:func:`~control_ofc.knowledge.memory_sensor_id.resolve_memory_sensor_id`, applied
on a sensor poll: cross-form only (legacy ↔ stable, both directions), and any second
module with the same chip, address and label — live or quarantined, either form —
refuses the move.

Profiles are re-saved only where that cannot publish something the user did not:
the profile the Controls page holds with unsaved edits, and an unpublished local
draft (``ProfileService`` promises no background sync), are re-keyed in memory
only. A pre-DEC-492 daemon does not hot-reload the active profile on a save, so
after a daemon downgrade the active profile needs re-activating (docs/20).

**No safety input moves.** A memory module is ``kind: mb_temp``; it never feeds
the thermal ladder, and the ids re-keyed here are display settings and curve
``sensor_id`` references the daemon resolves by the same rule anyway. No fan,
header or member id parses as a memory-sensor id, so no role or floor carrier can
be touched.

Stores re-keyed: chart colours and hidden chart series (``sensor:<id>`` keys),
sensor class overrides, Overview-hidden sensors, and profile ``curves[].sensor_id``.
Not re-keyed: the daemon's preferred CPU/motherboard sensor and cooling-device
sensors (advisory daemon-side metadata — re-picked by the user), and
``hardware_notes`` (keyed by header id, never a sensor).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from control_ofc.knowledge.memory_sensor_id import (
    parse_memory_sensor_id,
    resolve_memory_sensor_id,
)

SENSOR_SERIES_PREFIX = "sensor:"


def find_memory_id_moves(
    saved: Iterable[str], live: Iterable[str], unavailable: Iterable[str]
) -> dict[str, str]:
    """``{saved id: live id}`` for every saved memory-sensor id that is not live
    and resolves by the shared rule. ``unavailable`` (the daemon's quarantined
    ids) is required: a quarantined twin must block a move, so no caller may
    leave it out."""
    live = list(live)
    unavailable = list(unavailable)
    live_set = set(live)
    moves: dict[str, str] = {}
    for sid in set(saved):
        if sid in live_set or parse_memory_sensor_id(sid) is None:
            continue
        target = resolve_memory_sensor_id(sid, live, unavailable)
        if target is not None:
            moves[sid] = target
    return moves


def series_sensor_ids(keys: Iterable[str]) -> list[str]:
    """The sensor ids inside ``sensor:<id>`` chart-series keys."""
    return [k[len(SENSOR_SERIES_PREFIX) :] for k in keys if k.startswith(SENSOR_SERIES_PREFIX)]


def rekey_series_key(key: str, moves: Mapping[str, str]) -> str:
    if key.startswith(SENSOR_SERIES_PREFIX):
        sid = key[len(SENSOR_SERIES_PREFIX) :]
        if sid in moves:
            return SENSOR_SERIES_PREFIX + moves[sid]
    return key


def rekey_list(items: Iterable[str], moves: Mapping[str, str], *, series: bool) -> list[str]:
    """Re-key a list of ids (or ``sensor:<id>`` series keys), order kept, deduped."""
    out: list[str] = []
    for item in items:
        new = rekey_series_key(item, moves) if series else moves.get(item, item)
        if new not in out:
            out.append(new)
    return out


def rekey_mapping(mapping: Mapping[str, object], moves: Mapping[str, str], *, series: bool) -> dict:
    """Re-key a dict's keys. An entry already at the live key wins over a moved
    one: it can only have been written against the live id."""
    out = {
        k: v
        for k, v in mapping.items()
        if (rekey_series_key(k, moves) if series else moves.get(k, k)) == k
    }
    for k, v in mapping.items():
        new = rekey_series_key(k, moves) if series else moves.get(k, k)
        if new != k:
            out.setdefault(new, v)
    return out


def rekey_profile_curves(profile, moves: Mapping[str, str]) -> int:
    """Point every curve of ``profile`` whose ``sensor_id`` moved at its live id.
    Returns how many curves changed."""
    changed = 0
    for curve in profile.curves:
        new = moves.get(curve.sensor_id)
        if new is not None:
            curve.sensor_id = new
            changed += 1
    return changed
