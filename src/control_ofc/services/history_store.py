"""Rolling time-series buffer for chart data.

Stores the last 2 hours of sensor and fan readings in memory.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from control_ofc.api.models import FanReading, HistoryPoint, SensorReading
from control_ofc.constants import HISTORY_DURATION_S


@dataclass(slots=True)
class TimestampedReading:
    timestamp: float  # monotonic seconds
    value: float


class HistoryStore:
    """In-memory ring buffer for chart time-series data.

    Keyed by entity id (sensor or fan). Each series is a deque bounded
    by time (2 hours). Oldest entries are pruned on each append.
    """

    def __init__(self, max_age_s: float = HISTORY_DURATION_S) -> None:
        self._max_age_s = max_age_s
        self._series: dict[str, deque[TimestampedReading]] = {}
        # Per-key counter bumped by non-append-only mutations (see generation()).
        self._generation: dict[str, int] = {}
        # `prefill_sensor` runs on the polling worker thread (polling.py) while
        # `record_sensors`/`record_fans` run on the GUI thread (main.py) — an
        # unsynchronised compound read-modify-write on `_series` could drop a
        # startup chart point or tear a deque mid-iteration. Every public method
        # that touches `_series`/`_generation` holds this lock; the private
        # `_append`/`_prune` helpers assume it is already held (non-reentrant, so
        # they must never re-acquire it).
        self._lock = threading.Lock()

    def record_sensors(self, sensors: list[SensorReading]) -> None:
        now = time.monotonic()
        with self._lock:
            for s in sensors:
                self._append(f"sensor:{s.id}", now, s.value_c)

    def record_fans(self, fans: list[FanReading]) -> None:
        now = time.monotonic()
        with self._lock:
            for f in fans:
                if f.rpm is not None:
                    self._append(f"fan:{f.id}:rpm", now, float(f.rpm))

    def get_series(self, key: str) -> list[TimestampedReading]:
        """Return the time series for a given key, pruned to max_age."""
        with self._lock:
            if key not in self._series:
                return []
            self._prune(key)
            return list(self._series.get(key, ()))

    def series_keys(self) -> list[str]:
        with self._lock:
            return list(self._series.keys())

    def generation(self, key: str) -> int:
        """Monotonic per-key counter bumped by any NON-append-only mutation
        (the :meth:`prefill_sensor` merge, :meth:`clear`).

        Lets a consumer keep an incremental cache of the append-only tail
        (EFF-1, 2026-07-21 audit) and fall back to a full rebuild only when
        the series was restructured. Left-side age pruning deliberately does
        NOT bump it: pruned entries are strictly older than any cached tail,
        so a windowed consumer never misses data from them.
        """
        with self._lock:
            return self._generation.get(key, 0)

    def readings_since(self, key: str, after_ts: float) -> list[TimestampedReading]:
        """Readings with ``timestamp`` strictly greater than *after_ts*,
        ascending — O(new), scanning from the right of the deque. The
        incremental-read half of the :meth:`generation` contract (valid only
        while the generation is unchanged; live appends are monotonic).

        Strict ``>`` assumes no two readings of one key share a timestamp
        across calls — guaranteed today (one ``time.monotonic()`` stamp per
        1 Hz record pass; prefill dedupes exact timestamps and bumps the
        generation anyway)."""
        with self._lock:
            series = self._series.get(key)
            if not series:
                return []
            out: list[TimestampedReading] = []
            for r in reversed(series):
                if r.timestamp <= after_ts:
                    break
                out.append(r)
        out.reverse()
        return out

    def prefill_sensor(self, sensor_id: str, points: list[HistoryPoint]) -> None:
        """Pre-fill history from the daemon's ring buffer (first connect and
        every reconnect).

        Converts daemon wall-clock timestamps (ms since epoch) to monotonic
        offsets relative to now, then MERGES into any existing live series:
        the combined points are sorted ascending and exact-timestamp
        duplicates dropped (DEC-146 P2-1). A plain append corrupted reconnects
        — daemon history (older timestamps) landed after newer live readings,
        drawing zigzag chart artifacts and breaking the hover lookup, which
        uses ``np.searchsorted`` and requires sorted input. Live appends are
        monotonic already, so the sorted invariant holds permanently after
        this merge. Overlapping re-prefills may add near-duplicate points
        (conversion jitter defeats exact-ts dedupe); they are bounded
        (≤ one daemon ring per reconnect), sorted, and age out with pruning.
        """
        if not points:
            return
        key = f"sensor:{sensor_id}"
        now_mono = time.monotonic()
        now_wall_ms = int(time.time() * 1000)
        incoming = [
            TimestampedReading(timestamp=now_mono - ((now_wall_ms - p.ts) / 1000.0), value=p.v)
            for p in points
        ]
        with self._lock:
            existing = self._series.get(key)
            merged: list[TimestampedReading] = list(existing) if existing else []
            merged.extend(incoming)
            merged.sort(key=lambda r: r.timestamp)
            deduped: deque[TimestampedReading] = deque()
            for r in merged:
                if deduped and r.timestamp == deduped[-1].timestamp:
                    continue
                deduped.append(r)
            self._series[key] = deduped
            self._generation[key] = self._generation.get(key, 0) + 1
            self._prune(key)

    def clear(self) -> None:
        with self._lock:
            for key in self._series:
                self._generation[key] = self._generation.get(key, 0) + 1
            self._series.clear()

    def _append(self, key: str, timestamp: float, value: float) -> None:
        if key not in self._series:
            self._series[key] = deque()
        self._series[key].append(TimestampedReading(timestamp=timestamp, value=value))
        self._prune(key)

    def _prune(self, key: str) -> None:
        series = self._series.get(key)
        if not series:
            self._series.pop(key, None)
            return
        cutoff = time.monotonic() - self._max_age_s
        while series and series[0].timestamp < cutoff:
            series.popleft()
        if not series:
            self._series.pop(key, None)
