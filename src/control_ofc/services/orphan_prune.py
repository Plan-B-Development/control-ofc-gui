"""Find chart settings that refer to hardware the daemon no longer reports.

Qt-free by design — the view-model half of the shared "view-model + thin
renderer" pattern, so the rule is testable without a widget.

**Scope is deliberately narrow: `hidden_chart_series` and `series_colors` only.**

`fan_aliases` is *not* pruned, though it accumulates orphans the same way. Two
reasons, both load-bearing:

- DEC-237's Fan Names card lists an alias whose fan is absent precisely so the
  user can clear it ("otherwise a stale name for unplugged hardware can never be
  cleared", ``settings_page._refresh_fan_aliases``). Pruning automatically would
  delete the rows that card exists to show.
- A fan alias becomes ``ControlMember.member_label``, which selects the DEC-095 /
  DEC-162 30% CPU/pump floor. It is a safety input, not decoration.

The prune is user-triggered rather than automatic for a reason a single poll
cannot resolve: "unplugged for good" and "asleep right now" look identical. A
device that is merely off would silently lose its colour and hide state.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrphanReport:
    """Chart-settings keys that match no entity the daemon currently knows."""

    hidden_series: list[str] = field(default_factory=list)
    series_colors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.hidden_series) + len(self.series_colors)

    def __bool__(self) -> bool:
        return self.total > 0


def live_series_keys(
    fan_ids: Iterable[str],
    sensor_ids: Iterable[str],
    unavailable_sensor_ids: Iterable[str] = (),
) -> set[str]:
    """Every chart series key the current hardware can produce.

    ``unavailable_sensor_ids`` is not optional in practice: DEC-193 *evicts* a
    sensor that fails every read from the live ``sensors`` list and reports it
    separately, so a set built from ``sensors`` alone would call a quarantined
    WiFi temperature an orphan and drop its colour every time the radio is off.

    Key shapes mirror ``SeriesSelectionModel`` exactly (``sensor:{id}`` and
    ``fan:{id}:rpm``); a mismatch here would report live hardware as orphaned.
    """
    keys = {f"fan:{fan_id}:rpm" for fan_id in fan_ids}
    keys |= {f"sensor:{sid}" for sid in sensor_ids}
    keys |= {f"sensor:{sid}" for sid in unavailable_sensor_ids}
    return keys


def find_orphans(
    hidden_chart_series: Iterable[str],
    series_colors: Iterable[str],
    known_keys: set[str],
) -> OrphanReport:
    """Keys in the saved settings that no live key accounts for.

    Returns an **empty** report when ``known_keys`` is empty. That is the single
    most important line here: a disconnected GUI, or one asked before its first
    poll, knows of no hardware at all — and "prune everything the daemon did not
    mention" would then mean "delete the user's entire chart configuration".
    """
    if not known_keys:
        return OrphanReport()
    return OrphanReport(
        hidden_series=sorted(k for k in hidden_chart_series if k not in known_keys),
        series_colors=sorted(k for k in series_colors if k not in known_keys),
    )
