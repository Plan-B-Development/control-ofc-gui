"""Per-sensor session statistics tracker.

Tracks min/max/count per sensor since the GUI session started.
Resets when the GUI reconnects to the daemon.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SensorSessionStats:
    """Session statistics for a single sensor."""

    min_c: float
    max_c: float
    count: int


class SessionStatsTracker:
    """Tracks per-sensor session min/max since GUI connect.

    Thread-safe for single-writer (polling thread updates via signal -> main thread).
    """

    def __init__(self) -> None:
        self._stats: dict[str, SensorSessionStats] = {}

    def update(self, sensor_id: str, value_c: float) -> None:
        """Record a new reading for a sensor."""
        existing = self._stats.get(sensor_id)
        if existing is None:
            self._stats[sensor_id] = SensorSessionStats(min_c=value_c, max_c=value_c, count=1)
        else:
            if value_c < existing.min_c:
                existing.min_c = value_c
            if value_c > existing.max_c:
                existing.max_c = value_c
            existing.count += 1

    def update_batch(self, readings: list[tuple[str, float]]) -> None:
        """Record readings for multiple sensors at once."""
        for sensor_id, value_c in readings:
            self.update(sensor_id, value_c)

    def get(self, sensor_id: str) -> SensorSessionStats | None:
        """Get session stats for a sensor, or None if never seen."""
        return self._stats.get(sensor_id)

    def reset(self) -> None:
        """Clear all stats (call on reconnect)."""
        self._stats.clear()

    @property
    def sensor_count(self) -> int:
        """Number of sensors tracked."""
        return len(self._stats)
