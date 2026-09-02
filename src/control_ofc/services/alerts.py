"""Qt-free alert lifecycle ledger (DEC-282).

The GUI derives conditions (stale sensor, stale fan, fan stall, API skew, …) from
poll data. Before DEC-282 those conditions were a flat list rebuilt from scratch on
every tick, and three things followed from that shape:

* the change signal was gated on the list's **length**, so a condition resolving
  while another activated in the same tick emitted nothing and the view rendered a
  condition that no longer existed;
* a condition that appeared and cleared between two polls left **no trace at all** —
  no event, no timestamp, nothing the user could go back and read;
* acknowledgement added the condition's key to a set that was never pruned, which
  permanently muted every future recurrence of that key for the session.

This module replaces the flat list with **occurrences**. An occurrence is identified
by ``(key, activation_epoch)``: it is minted when a condition appears, spans however
many polls the condition persists, is closed when the condition clears, and a later
recurrence of the same key mints a *new* occurrence. That single change is what makes
the other three problems structural rather than rules to remember — acknowledgement
marks an occurrence, so it cannot suppress a future one; a continuous condition is one
occurrence, so it logs once rather than once per poll.

States follow **ISA-18.2**, the industrial alarm-management standard, because its two-by-two
of (condition present?) by (acknowledged?) expresses the case a linear
active→acknowledged→recovered model cannot: a condition that cleared before anyone
looked at it. That is `RECOVERED_UNSEEN` (the standard's *RTN Unacknowledged*), and it
is the state that stops an alert flashing past unnoticed.

Pure and Qt-free by design (the ``services/*_view.py`` pattern): time is passed in, so
every transition is deterministic under test, and nothing here imports
``DiagnosticsService`` — transitions are *returned*, and a thin observer decides what to
log. ``AppState`` cannot import ``DiagnosticsService`` in any case; the import already
runs the other way.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, replace
from enum import Enum

# Upper bound on retained recovered occurrences. Session-only history: enough to
# answer "what was that thing that flashed up?" without letting a flapping sensor
# grow the list without limit.
MAX_RECOVERED = 50

# Severity ordering for presentation. Mirrors the level vocabulary already used by
# DiagEvent and StatusPill — there is no CRITICAL level in this application.
_LEVEL_RANK: dict[str, int] = {"error": 3, "warning": 2, "info": 1}


class AlertState(Enum):
    """ISA-18.2 alarm states, minus the shelved/suppressed/out-of-service ones this
    application has no concept of."""

    ACTIVE = "active"  # present, unacknowledged      (ISA-18.2 "Unack")
    ACKNOWLEDGED = "acknowledged"  # present, acknowledged        (ISA-18.2 "Acked")
    RECOVERED_UNSEEN = "recovered_unseen"  # cleared, unacknowledged      (ISA-18.2 "RTN Unack")
    RECOVERED = "recovered"  # cleared, acknowledged        (ISA-18.2 "Normal")


@dataclass(frozen=True)
class AlertCondition:
    """One condition present at this instant — the per-tick input to ``reconcile``.

    ``title`` is the short label a recovery line reads back ("CPU_FAN stall");
    ``detail`` is the full sentence the onset logs and the Alert Centre shows.
    """

    key: str
    level: str  # "info" | "warning" | "error"
    source: str
    component: str
    title: str
    detail: str


@dataclass
class AlertOccurrence:
    """One occurrence of one condition, from onset to recovery.

    ``activation_epoch`` is both the occurrence's identity discriminator (paired with
    ``key``) and its first-detected time — a recurrence after recovery necessarily has
    a later one, which is precisely why acknowledging this occurrence cannot silence
    the next.
    """

    key: str
    activation_epoch: float
    level: str
    source: str
    component: str
    title: str
    detail: str
    last_detected: float
    recovered_at: float | None = None
    acknowledged: bool = False

    @property
    def is_present(self) -> bool:
        """Whether the underlying condition still exists. Acknowledgement does not
        change this — that is the whole point of the ACKNOWLEDGED state."""
        return self.recovered_at is None

    @property
    def state(self) -> AlertState:
        if self.recovered_at is None:
            return AlertState.ACKNOWLEDGED if self.acknowledged else AlertState.ACTIVE
        return AlertState.RECOVERED if self.acknowledged else AlertState.RECOVERED_UNSEEN

    @property
    def duration_s(self) -> float:
        """Seconds from onset to recovery, or to last sighting while still present."""
        end = self.recovered_at if self.recovered_at is not None else self.last_detected
        return max(0.0, end - self.activation_epoch)


@dataclass(frozen=True)
class AlertTransition:
    """A state change worth recording. ``occurrence`` is an independent snapshot taken
    at transition time, so a later acknowledgement cannot retroactively alter what a
    consumer was told."""

    kind: str  # "onset" | "recovered"
    occurrence: AlertOccurrence


def _severity_rank(occ: AlertOccurrence) -> int:
    return _LEVEL_RANK.get(occ.level, 0)


def _newest_most_severe(occ: AlertOccurrence) -> tuple[int, float]:
    return (_severity_rank(occ), occ.activation_epoch)


class AlertLedger:
    """Occurrence store + state machine. Pure: every method that needs the time takes
    it as an argument, so tests pin transitions exactly rather than sleeping."""

    def __init__(self, max_recovered: int = MAX_RECOVERED) -> None:
        # key -> the one occurrence currently present for that key (ACTIVE or
        # ACKNOWLEDGED). A key has at most one present occurrence by construction.
        self._present: dict[str, AlertOccurrence] = {}
        # Closed occurrences, newest first (RECOVERED_UNSEEN or RECOVERED).
        self._recovered: deque[AlertOccurrence] = deque(maxlen=max_recovered)

    # ── State machine ────────────────────────────────────────────────

    def reconcile(self, conditions: list[AlertCondition], now: float) -> list[AlertTransition]:
        """Fold the currently-present conditions into the ledger and return what changed.

        A key already present is *refreshed*, not re-minted — that is what keeps a
        condition spanning many polls to a single occurrence, and therefore to a single
        onset log line. Only genuine onsets and recoveries produce transitions.
        """
        transitions: list[AlertTransition] = []
        present_now = {c.key: c for c in conditions}

        # Recoveries first, so a caller reading the returned list sees the close of an
        # occurrence before any re-onset of the same key in a later tick.
        for key in [k for k in self._present if k not in present_now]:
            occ = self._present.pop(key)
            occ.recovered_at = now
            self._recovered.appendleft(occ)
            transitions.append(AlertTransition("recovered", replace(occ)))

        for key, cond in present_now.items():
            occ = self._present.get(key)
            if occ is None:
                occ = AlertOccurrence(
                    key=cond.key,
                    activation_epoch=now,
                    level=cond.level,
                    source=cond.source,
                    component=cond.component,
                    title=cond.title,
                    detail=cond.detail,
                    last_detected=now,
                )
                self._present[key] = occ
                transitions.append(AlertTransition("onset", replace(occ)))
            else:
                # Refresh the fields that legitimately move while a condition persists
                # (a staleness message carries a growing age). Identity, onset time and
                # acknowledgement are untouched — this is the same occurrence.
                occ.last_detected = now
                occ.level = cond.level
                occ.detail = cond.detail
                occ.title = cond.title

        return transitions

    def acknowledge(self, key: str, now: float) -> bool:
        """Mark every unacknowledged occurrence of *key* as seen. Returns whether
        anything changed.

        Acknowledging a **present** condition moves it ACTIVE → ACKNOWLEDGED and
        explicitly does NOT clear it: the fan is still stalled, and
        ``active_count`` still counts it.
        """
        del now  # acknowledgement time is not currently displayed; kept for symmetry
        changed = False
        occ = self._present.get(key)
        if occ is not None and not occ.acknowledged:
            occ.acknowledged = True
            changed = True
        for rec in self._recovered:
            if rec.key == key and not rec.acknowledged:
                rec.acknowledged = True
                changed = True
        return changed

    def acknowledge_all(self, now: float) -> int:
        """Acknowledge every outstanding occurrence. Returns how many changed."""
        del now
        count = 0
        for occ in self._present.values():
            if not occ.acknowledged:
                occ.acknowledged = True
                count += 1
        for rec in self._recovered:
            if not rec.acknowledged:
                rec.acknowledged = True
                count += 1
        return count

    # ── Views ────────────────────────────────────────────────────────

    def present(self) -> list[AlertOccurrence]:
        """Occurrences whose condition still exists (ACTIVE + ACKNOWLEDGED), most
        severe first, then newest."""
        return sorted(self._present.values(), key=_newest_most_severe, reverse=True)

    def unacknowledged(self) -> list[AlertOccurrence]:
        """Everything still demanding attention — ACTIVE plus RECOVERED_UNSEEN. The
        second half is what keeps an alert that cleared before anyone looked at it
        from vanishing silently."""
        live = [o for o in self.present() if not o.acknowledged]
        unseen = [o for o in self._recovered if not o.acknowledged]
        return live + unseen

    def recovered(self) -> list[AlertOccurrence]:
        """Closed occurrences, newest first — the session's alert history."""
        return list(self._recovered)

    def active_count(self) -> int:
        """A **health** number: conditions that genuinely exist right now, whether or
        not they have been acknowledged. Never quietened by a button."""
        return len(self._present)

    def unacknowledged_count(self) -> int:
        """An **attention** number: what the user has not yet looked at, including
        alerts that already recovered unseen."""
        return len(self.unacknowledged())


def transition_to_log(tr: AlertTransition) -> tuple[str, str, str]:
    """Map a transition to a ``(level, source, message)`` event-log triple.

    Onset carries the condition's own sentence; recovery reads back the short title
    with how long it lasted, so the pair reads as a story in the log table:

        09:18:35 WARN  fan  Fan 'cpu_fan' stall detected (RPM=0 while PWM commanded)
        09:18:36 INFO  fan  CPU_FAN stall recovered after 1.2s
    """
    occ = tr.occurrence
    if tr.kind == "onset":
        return (occ.level, occ.source, occ.detail)
    return ("info", occ.source, f"{occ.title} recovered after {occ.duration_s:.1f}s")


def transition_to_fields(tr: AlertTransition) -> dict[str, str]:
    """The structured half of a transition, for ``DiagEvent.fields`` (DEC-314).

    Deliberately separate from :func:`transition_to_log` rather than widening its
    tuple: the two answer different questions (what sentence to show vs what the
    occurrence actually was), and the callers of each are different.

    This is the richest structured context the GUI holds and previously threw away.
    An ``AlertOccurrence`` carries ``key``, ``component``, ``title`` and
    ``activation_epoch`` — all of which were flattened into one sentence and lost, so
    the Logs page had nothing to correlate on but the source tag. ``component`` in
    particular is the second tier of the inspector's related-events ordering.

    ``duration_s`` is emitted on recovery only: on an onset it is the time since
    activation, which is ~0 by construction and would read as meaningful data.
    """
    occ = tr.occurrence
    fields = {
        "alert": occ.title,
        "alert_key": occ.key,
        "first_detected": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(occ.activation_epoch)),
    }
    if occ.component:
        fields["component"] = occ.component
    if tr.kind != "onset":
        fields["duration_s"] = f"{occ.duration_s:.1f}"
    return fields
