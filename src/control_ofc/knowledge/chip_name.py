"""Canonical hwmon chip names — the it87 v2.0 board suffix (DEC-442, ``BRD-a``).

it87 v2.0 (frankcrawford/it87 PR #132, merged 2026-09-09) names every ITE chip on
a Gigabyte board after the board's SIV word — ``it8696`` publishes as
``it8696_a008090a`` — and every stable id embeds the chip name. The daemon strips
the suffix where it reads the name (``hwmon::chip_name``), so a current daemon
never publishes a suffixed id. Ids a *pre-DEC-442* daemon published can still sit
in this GUI's own settings and local profile copies, though, and this module is
what the load-time clean-up uses to bring them back to the id the daemon now
publishes.

**Mirrors the daemon's rule exactly** — upstream's own ``install-sensorsd.sh``
pattern ``^it[0-9]+_[0-9A-Fa-f]{8}$``, applied to the chip segment of an
``hwmon:<chip>:<device>:…`` id. The two copies are pinned together by the shared
oracle ``tests/fixtures/chip_name_canonical.json`` (byte-identical in the daemon
repo; ``parity.yml`` in both repos fails on drift): if they disagreed, a fan name
would be re-keyed to an id the daemon never publishes.

Never canonicalise a name used to match ``/etc/sensors.d`` — upstream writes those
blocks against the suffixed name, and matching them against the stripped one
would apply another board's labels. That match uses ``HwmonHeader.sysfs_chip_name``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_IT87_SUFFIXED = re.compile(r"(it[0-9]+)_[0-9A-Fa-f]{8}")


def canonical_chip_name(raw: str) -> str:
    """Strip the it87 v2.0 board suffix from a chip name; any other name is
    returned unchanged."""
    m = _IT87_SUFFIXED.fullmatch(raw)
    return m.group(1) if m else raw


def canonical_hwmon_id(id_: str) -> str:
    """Canonicalise the chip segment of an ``hwmon:<chip>:<device>:…`` id.

    Any other id — OpenFan, GPU, a sensor-series key, or an hwmon id whose chip
    carries no suffix — is returned unchanged.
    """
    parts = id_.split(":", 2)
    if len(parts) != 3 or parts[0] != "hwmon":
        return id_
    base = canonical_chip_name(parts[1])
    if base == parts[1]:
        return id_
    return f"hwmon:{base}:{parts[2]}"


def is_suffixed_hwmon_id(id_: str) -> bool:
    """Whether *id_* is an hwmon id whose chip segment carries the suffix."""
    return canonical_hwmon_id(id_) != id_


def canonical_series_key(key: str) -> str:
    """Canonicalise the id inside a chart-series key (``fan:<id>:rpm``,
    ``sensor:<id>``). Any other key is returned unchanged."""
    for prefix in ("fan:", "sensor:"):
        if key.startswith(prefix):
            return prefix + canonical_hwmon_id(key[len(prefix) :])
    return key


def canonicalize_keyed[V](mapping: dict[str, V], key_fn: Callable[[str], str]) -> dict[str, V]:
    """Re-key *mapping* through *key_fn* (``canonical_hwmon_id`` or
    ``canonical_series_key``).

    Where two keys collapse to one, **the suffixed one wins**: it can only have
    been written after the it87 v2.0 rebuild, so it is the user's latest choice
    (DEC-442). Order is otherwise preserved.
    """
    out: dict[str, V] = {}
    from_suffixed: set[str] = set()
    for key, value in mapping.items():
        canonical = key_fn(key)
        suffixed = canonical != key
        if canonical in out and (canonical in from_suffixed or not suffixed):
            continue
        out[canonical] = value
        if suffixed:
            from_suffixed.add(canonical)
    return out


def canonicalize_list(items: list[str], key_fn: Callable[[str], str]) -> list[str]:
    """Canonicalise every entry through *key_fn*, dropping repeats in order."""
    out: list[str] = []
    for item in items:
        canonical = key_fn(item)
        if canonical not in out:
            out.append(canonical)
    return out
