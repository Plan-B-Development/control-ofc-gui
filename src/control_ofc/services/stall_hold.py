"""Which fans are stalled right now, holding through a missing flag (`TS-bg`). Qt-free.

The daemon's ``stall_detected`` is tri-state and ``None`` means *not evaluated*,
never *not stalled* (``docs/08``). Since DEC-412 an OpenFan channel's flag goes
missing for about one poll after a failed reply, so reading ``None`` as "not
stalled" made a stalled fan's alert clear and re-raise and posted a second
Dashboard "Stall:" annotation.

The fix is the alarm-management *off-delay* (ISA-18.2): a stall seen once is held
while the flag is missing, for at most :data:`HOLD_S` after the last ``True``,
and ends at once on evidence the fan turns (RPM above 0) or on an explicit
``False``. The hold is bounded because an unbounded one would keep an alert up
for ever on a channel the daemon has stopped evaluating — a resumed machine
whose channel nothing commands, say.

:class:`StallHold` is the ONE way the GUI answers "is this fan stalled?": the
alert, the Dashboard onset annotation, the fan-card state and the Hardware
page's header status all read the set it produces, through
``AppState.stalled_fan_ids``. The clock is passed in, so every rule here is
testable without waiting.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from control_ofc.api.models import FanReading

#: Seconds a stall is held after its last explicit ``True`` while the flag is
#: missing. About five polls; the missing-flag window it covers is one.
HOLD_S = 5.0


@dataclass(frozen=True)
class _Held:
    last_true_at: float


class StallHold:
    """Stall state per fan id, held through a missing ``stall_detected``."""

    def __init__(self) -> None:
        self._held: dict[str, _Held] = {}

    @property
    def stalled(self) -> frozenset[str]:
        """The fan ids stalled as of the last :meth:`update`."""
        return frozenset(self._held)

    def update(self, fans: Iterable[FanReading], now: float) -> frozenset[str]:
        """Fold one poll's fans in and return the stalled set.

        Per fan: ``True`` stalls it (and restarts the hold); ``False`` clears it;
        ``None`` keeps a stall seen before, until :data:`HOLD_S` has passed since
        its last ``True`` or the fan reads RPM above 0. A fan missing from the
        poll is dropped, and a fan never seen stalled is never held.
        """
        held: dict[str, _Held] = {}
        for fan in fans:
            if fan.stall_detected is True:
                held[fan.id] = _Held(last_true_at=now)
                continue
            if fan.stall_detected is False:
                continue
            prior = self._held.get(fan.id)
            if prior is None:
                continue
            turning = fan.rpm is not None and fan.rpm > 0
            if not turning and now - prior.last_true_at < HOLD_S:
                held[fan.id] = prior
        self._held = held
        return self.stalled
