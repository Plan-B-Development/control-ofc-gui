"""The run trace: every fan and every temperature, once a second, for the length
of a report run (DEC-404, S4-8).

Fed from the application's own 1 Hz poll (``AppState``), never from a poll of
its own — the report adds no load to the daemon to watch it. Stored
**column-wise**: the ids once, then one list per quantity, because a row per
sample repeats every id and every key name and measured ~36 MB an hour on the
development machine (19 fans, 24 sensors) against ~1.5 MB for this shape.

Recording stops at :data:`MAX_SAMPLES` (three hours) and the trace says so,
with the time it stopped. Nothing is dropped silently: a gap in the poll is a
missing sample, not an interpolated one, because ``t_ms`` is recorded per
sample rather than assumed from the cadence.

Values keep their wire meaning: ``None`` is "the daemon did not say", never 0.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

#: S4-8: three hours at 1 Hz.
MAX_SAMPLES = 3 * 60 * 60

#: The per-fan quantities, in their wire names.
FAN_SERIES = ("rpm", "pwm_readback_pct", "pwm_commanded_pct", "pwm_enable_mode")


class TraceRecorder:
    """Accumulates samples; :meth:`to_dict` is the document's ``trace`` value."""

    def __init__(self, *, max_samples: int = MAX_SAMPLES) -> None:
        self._max = max_samples
        self._t_ms: list[int] = []
        self._thermal: list[str | None] = []
        self._fans: dict[str, dict[str, list[object]]] = {}
        self._temps: dict[str, list[float | None]] = {}
        self._truncated_at_ms: int | None = None

    @property
    def sample_count(self) -> int:
        return len(self._t_ms)

    @property
    def truncated(self) -> bool:
        return self._truncated_at_ms is not None

    def add_sample(
        self,
        t_ms: int,
        *,
        thermal_state: str | None,
        fans: Iterable[object],
        sensors: Iterable[object],
    ) -> bool:
        """Record one poll. Returns ``False`` once the cap has been reached.

        ``fans`` and ``sensors`` are the app's parsed ``FanReading`` /
        ``SensorReading`` objects, read by attribute so this module needs no
        model import. A channel first seen mid-run gets a column back-filled
        with ``None`` — it was not reported before, which is what ``None`` means.
        """
        if self.sample_count >= self._max:
            if self._truncated_at_ms is None:
                self._truncated_at_ms = t_ms
            return False
        index = self.sample_count
        self._t_ms.append(int(t_ms))
        self._thermal.append(thermal_state)
        seen_fans: set[str] = set()
        for fan in fans:
            fid = getattr(fan, "id", "")
            if not fid or fid in seen_fans:
                continue
            seen_fans.add(fid)
            cols = self._fans.get(fid)
            if cols is None:
                cols = {name: [None] * index for name in FAN_SERIES}
                self._fans[fid] = cols
            for name in FAN_SERIES:
                cols[name].append(getattr(fan, name, None))
        for fid, cols in self._fans.items():
            if fid not in seen_fans:
                for name in FAN_SERIES:
                    cols[name].append(None)
        seen_temps: set[str] = set()
        for sensor in sensors:
            sid = getattr(sensor, "id", "")
            if not sid or sid in seen_temps:
                continue
            seen_temps.add(sid)
            col = self._temps.get(sid)
            if col is None:
                col = [None] * index
                self._temps[sid] = col
            value = getattr(sensor, "value_c", None)
            finite = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
            # A non-finite reading is recorded as "not reported": the saved file
            # must stay loadable, and `load_json_capped` rejects NaN/Infinity.
            col.append(round(float(value), 1) if finite else None)
        for sid, col in self._temps.items():
            if sid not in seen_temps:
                col.append(None)
        return True

    def to_dict(self) -> dict:
        return {
            "interval_ms": 1000,
            "source": "the app's own 1 Hz poll",
            "max_samples": self._max,
            "samples": self.sample_count,
            "truncated": self.truncated,
            "truncated_at_ms": self._truncated_at_ms,
            "t_ms": list(self._t_ms),
            "thermal_state": list(self._thermal),
            "fans": {
                fid: {k: list(v) for k, v in cols.items()} for fid, cols in self._fans.items()
            },
            "temps_c": {sid: list(col) for sid, col in self._temps.items()},
        }
