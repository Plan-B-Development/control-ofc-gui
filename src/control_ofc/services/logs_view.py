"""Qt-free view-model layer for the Logs page (DEC-210).

Pure builders that turn the ``DiagnosticsService`` event feed (``DiagEvent``)
into frozen ``LogRowVM`` dataclasses the thin ``LogsPage`` renderer consumes,
plus a pure ``filter_log_rows`` that mirrors ``_EventFilterProxy.filterAcceptsRow``
(``ui/widgets/event_log_view.py``) exactly so the new Logs table filters
identically to the (retiring) Diagnostics ▸ Event Log tab. All display derivation
(level label, pill state, timestamp strings) lives here so it is unit-testable
without a ``QApplication``.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from control_ofc.services.diagnostics_service import DiagEvent

# Level → mockup-faithful abbreviated label (INFO/WARN/ERR) used for the table
# cell + pill text. Distinct from the EventLogView widget's INFO/WARNING/ERROR.
_LEVEL_LABEL: dict[str, str] = {
    "info": "INFO",
    "warning": "WARN",
    "error": "ERR",
}

# Level → StatusPill semantic state. Matches ``_level_color`` (event_log_view.py:
# info→ok/green, warning→warn/amber, error→crit/red) and the ``.Pill_*`` state
# vocabulary in ``components/badges.py``.
_LEVEL_STATE: dict[str, str] = {
    "info": "ok",
    "warning": "warn",
    "error": "crit",
}


@dataclass(frozen=True)
class LogRowVM:
    """One event-log row, fully derived for rendering + filtering."""

    timestamp: float  # raw epoch — the inspector formats a fuller datetime
    time_str: str  # "%H:%M:%S" — the table Time column (== DiagEvent.time_str)
    detail_time_str: str  # "%Y-%m-%d %H:%M:%S" — the inspector Timestamp (real, not mock)
    level: str  # raw "info" | "warning" | "error" — the FILTER key
    level_label: str  # "INFO" | "WARN" | "ERR" — pill / cell text
    level_state: str  # "ok" | "warn" | "crit" — StatusPill state
    source: str
    message: str


def build_log_row(ev: DiagEvent) -> LogRowVM:
    """Derive a ``LogRowVM`` from a single ``DiagEvent``."""
    level = ev.level
    return LogRowVM(
        timestamp=ev.timestamp,
        time_str=ev.time_str,
        detail_time_str=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.timestamp)),
        level=level,
        level_label=_LEVEL_LABEL.get(level, level.upper()),
        level_state=_LEVEL_STATE.get(level, "neutral"),
        source=ev.source,
        message=ev.message,
    )


def build_log_rows(events: Iterable[DiagEvent]) -> list[LogRowVM]:
    """Derive ``LogRowVM``s for a sequence of events (insertion order preserved)."""
    return [build_log_row(ev) for ev in events]


def filter_log_rows(
    rows: Sequence[LogRowVM],
    *,
    levels: set[str],
    source: str = "",
    search: str = "",
) -> list[LogRowVM]:
    """Return rows matching every active filter — a line-for-line mirror of
    ``_EventFilterProxy.filterAcceptsRow`` (``ui/widgets/event_log_view.py``).

    - ``levels`` is a severity **set**: a row survives only if ``row.level`` is a
      member. An **empty** set therefore rejects every row (all toggles off →
      zero rows), exactly like the proxy.
    - ``source`` empty ⇒ no source restriction; otherwise an **exact** match.
    - ``search`` is a case-insensitive substring over ``f"{message} {source}"``.

    Deliberately NOT ``DiagnosticsService.filter_events``, whose empty ``levels``
    means *no* filter (show all) — the opposite of the set-membership semantics
    the retiring tab uses.
    """
    needle = search.strip().lower()
    result: list[LogRowVM] = []
    for row in rows:
        if row.level not in levels:
            continue
        if source and row.source != source:
            continue
        if needle:
            hay = f"{row.message} {row.source}".lower()
            if needle not in hay:
                continue
        result.append(row)
    return result
