"""Rolling time-series buffer for chart data.

Stores the last 2 hours of sensor and fan readings in memory.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from control_ofc.api.models import FanReading, HistoryPoint, SensorReading
from control_ofc.constants import HISTORY_DURATION_S


@dataclass
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

    def record_sensors(self, sensors: list[SensorReading]) -> None:
        now = time.monotonic()
        for s in sensors:
            self._append(f"sensor:{s.id}", now, s.value_c)

    def record_fans(self, fans: list[FanReading]) -> None:
        now = time.monotonic()
        for f in fans:
            if f.rpm is not None:
                self._append(f"fan:{f.id}:rpm", now, float(f.rpm))
            if f.last_commanded_pwm is not None:
                self._append(f"fan:{f.id}:pwm", now, float(f.last_commanded_pwm))

    def get_series(self, key: str) -> list[TimestampedReading]:
        """Return the time series for a given key, pruned to max_age."""
        if key not in self._series:
            return []
        self._prune(key)
        return list(self._series[key])

    def series_keys(self) -> list[str]:
        return list(self._series.keys())

    def prefill_sensor(self, sensor_id: str, points: list[HistoryPoint]) -> None:
        """Pre-fill history from daemon's ring buffer (e.g. on first connect).

        Converts daemon wall-clock timestamps (ms since epoch) to monotonic
        offsets relative to now, so they integrate with the existing time-based
        pruning.
        """
        if not points:
            return
        key = f"sensor:{sensor_id}"
        now_mono = time.monotonic()
        now_wall_ms = int(time.time() * 1000)
        series = self._series.get(key)
        if series is None:
            series = deque()
            self._series[key] = series
        for p in points:
            age_ms = now_wall_ms - p.ts
            mono_ts = now_mono - (age_ms / 1000.0)
            series.append(TimestampedReading(timestamp=mono_ts, value=p.v))
        self._prune(key)

    def clear(self) -> None:
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
