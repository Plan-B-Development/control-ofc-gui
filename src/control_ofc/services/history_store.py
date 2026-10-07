"""Rolling time-series buffer for chart data.

Stores the last 2 hours of sensor and fan readings in memory.
"""

from __future__ import annotations

import bisect
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from control_ofc.api.models import FanReading, HistoryPoint, SensorReading
from control_ofc.constants import HISTORY_DURATION_S, POLL_INTERVAL_MS

# Two stamps of one key closer than this are one daemon sample: seen by two GUI
# polls (its back-stamped time moves only by transport jitter), or by a poll and
# the daemon's history ring. Half a GUI poll keeps distinct 1 Hz samples apart.
_SAME_SAMPLE_S = POLL_INTERVAL_MS / 1000 / 2


def boottime_s() -> float:
    """The chart's time base: ``CLOCK_BOOTTIME`` seconds (GSA-j).

    Unlike ``time.monotonic()`` it keeps counting through suspend, so a reading
    taken before a sleep is drawn as old as it really is, ages out of the
    retention on time, and lands where the daemon's wall-clock history (prefill
    after a resume) puts the same sample. Every chart-time stamp — the store,
    the chart's ``now``, the dashboard's annotations — goes through
    :meth:`HistoryStore.now`, so they cannot drift onto different clocks.
    """
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def _wall_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class TimestampedReading:
    timestamp: float  # boottime seconds (HistoryStore.now)
    value: float


class HistoryStore:
    """In-memory ring buffer for chart time-series data.

    Keyed by entity id (sensor or fan). Each series is a deque bounded
    by time (2 hours). Oldest entries are pruned on each append.
    """

    def __init__(
        self,
        max_age_s: float = HISTORY_DURATION_S,
        *,
        clock: Callable[[], float] = boottime_s,
        wall_ms: Callable[[], int] = _wall_ms,
    ) -> None:
        self._max_age_s = max_age_s
        self._clock = clock
        self._wall_ms = wall_ms
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

    def now(self) -> float:
        """The current chart time — the one clock every chart stamp uses."""
        return self._clock()

    def record_sensors(self, sensors: list[SensorReading]) -> None:
        """Record each reading at the time it was sampled, not received (GSA-i).

        A reading is ``age_ms`` old when it arrives, so it is stamped
        ``now - age``. A sensor the daemon has stopped refreshing keeps the same
        sample time poll after poll, so it adds no point — its line stops where
        the data stopped instead of running on at the last value.
        """
        now = self._clock()
        with self._lock:
            for s in sensors:
                self._record_sample(f"sensor:{s.id}", now - s.age_ms / 1000, s.value_c)

    def record_fans(self, fans: list[FanReading]) -> None:
        """Record measured RPM at its sample time — see :meth:`record_sensors`."""
        now = self._clock()
        with self._lock:
            for f in fans:
                if f.rpm is not None:
                    self._record_sample(f"fan:{f.id}:rpm", now - f.age_ms / 1000, float(f.rpm))

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
        across calls — guaranteed today (a live sample is appended only when it
        is newer than the series' last point, see ``_record_sample``; prefill
        bumps the generation anyway)."""
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

        Converts daemon wall-clock timestamps (ms since epoch) to chart time
        (boottime) offsets relative to now, then MERGES into any existing live
        series, sorted ascending (DEC-146 P2-1). A plain append corrupted
        reconnects — daemon history (older timestamps) landed after newer live
        readings, drawing zigzag chart artifacts and breaking the hover lookup,
        which uses ``np.searchsorted`` and requires sorted input. Live appends
        are monotonic already, so the sorted invariant holds permanently after
        this merge.

        A daemon point within ``_SAME_SAMPLE_S`` of a point already held is the
        same sample (live points are stamped at their sample time) and is
        dropped, so a prefill fills only the gaps — a disconnect, or the time
        before the GUI started — and a re-prefill adds nothing it already has.
        Wall time and boottime both run through suspend, so the conversion
        holds across one (GSA-j).
        """
        self._merge_prefill(f"sensor:{sensor_id}", points)

    def prefill_fan(self, fan_id: str, points: list[HistoryPoint], *, metric: str = "rpm") -> None:
        """Pre-fill a fan series, the counterpart to :meth:`prefill_sensor`.

        Same merge semantics, same guarantees — it shares the implementation
        rather than restating it, so the sorted/deduped/generation-bumped
        invariant cannot drift between the two paths.

        ``metric`` selects the series: ``"rpm"`` (measured) or ``"pwm"``
        (commanded). These are never conflated — see ``CLAUDE.md``.

        Added for DEC-248: the screenshot pipeline had no public way to seed
        fan history, so it used the private ``_append``, which neither sorts nor
        bumps the generation. The result was an RPM series the chart never
        rendered *and* a deque that violated the ascending-timestamp invariant
        ``np.searchsorted`` depends on.
        """
        self._merge_prefill(f"fan:{fan_id}:{metric}", points)

    def _merge_prefill(self, key: str, points: list[HistoryPoint]) -> None:
        """Merge wall-clock history into *key*, preserving the sorted invariant.

        Acquires the lock itself, unlike ``_append``/``_prune`` which require it
        to be held already.
        """
        if not points:
            return
        now = self._clock()
        now_wall_ms = self._wall_ms()
        incoming = sorted(
            (
                TimestampedReading(timestamp=now - ((now_wall_ms - p.ts) / 1000.0), value=p.v)
                for p in points
            ),
            key=lambda r: r.timestamp,
        )
        with self._lock:
            existing = self._series.get(key)
            merged: list[TimestampedReading] = list(existing) if existing else []
            held = [r.timestamp for r in merged]
            for r in incoming:
                if not _near_any(held, r.timestamp):
                    merged.append(r)
            merged.sort(key=lambda r: r.timestamp)
            self._series[key] = deque(merged)
            self._generation[key] = self._generation.get(key, 0) + 1
            self._prune(key)

    def clear(self) -> None:
        with self._lock:
            for key in self._series:
                self._generation[key] = self._generation.get(key, 0) + 1
            self._series.clear()

    def _record_sample(self, key: str, timestamp: float, value: float) -> None:
        """Append a live sample unless the series already holds it (or a later
        one). Keeps live appends strictly increasing, which ``readings_since``
        and the chart's ``searchsorted`` rely on."""
        series = self._series.get(key)
        if series and timestamp < series[-1].timestamp + _SAME_SAMPLE_S:
            return
        self._append(key, timestamp, value)

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
        cutoff = self._clock() - self._max_age_s
        while series and series[0].timestamp < cutoff:
            series.popleft()
        if not series:
            self._series.pop(key, None)


def _near_any(sorted_ts: list[float], ts: float) -> bool:
    """Whether *ts* lies within ``_SAME_SAMPLE_S`` of any value in *sorted_ts*."""
    i = bisect.bisect_left(sorted_ts, ts)
    return (i < len(sorted_ts) and sorted_ts[i] - ts < _SAME_SAMPLE_S) or (
        i > 0 and ts - sorted_ts[i - 1] < _SAME_SAMPLE_S
    )
