"""Duty drift, as the System State page reports it (DEC-404 S4-3, S4-12).

Daemon 2.53.0+ (DEC-406, capability ``control.duty_reconciliation``) reads back
an hwmon write it would coalesce and rewrites a duty something else moved. After
three corrections the next tick contradicts, it stops, and flags the header
``duty_not_holding``. Both facts ride every hwmon ``/fans``/``/poll`` entry.

What the page does with them — the user's choices, S4-3 and S4-12:

* **a condition card only while a header is not holding**, one per header, one
  per episode. The episode's identity is the daemon's correction count at the
  give-up, because the count cannot move while the daemon has stopped
  correcting: a new episode needs new corrections, so a fresh give-up is a new
  occurrence and a dismissal of the last one cannot hide it;
* **corrections that held are a reading**, a count in the Interference Monitor,
  never an alert;
* **the card counts everywhere the page's conditions count** — the "N ACTION
  REQUIRED" pill and the pop-out report — so the two cannot disagree (DEC-379).
  Every builder therefore takes :class:`DutyDriftState` as a REQUIRED keyword:
  a caller that forgot it would fail loudly instead of quietly reporting "no
  drift", which is the default-argument trap DEC-379 names.

Qt-free.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass

#: Condition keys start with this; the rest is a digest of the header's stable
#: id, because an id carries ':' and may carry '#', which a silence token
#: cannot hold (`health_ack.parse_token`).
DRIFT_KEY_PREFIX = "duty_not_holding_"


@dataclass(frozen=True)
class DutyDrift:
    """One header the daemon has stopped correcting."""

    header_id: str
    name: str
    #: The daemon's count when it gave up — the episode's identity. ``None``
    #: when the daemon flagged the header without a count (never expected).
    corrections: int | None


@dataclass(frozen=True)
class DutyDriftState:
    """Everything the page says about drift, from one ``/fans`` poll."""

    #: The daemon publishes the fields at all (it has DEC-406). Without them
    #: the page says nothing — absence is "this daemon does not reconcile",
    #: never "no drift".
    reported: bool
    not_holding: tuple[DutyDrift, ...]
    #: ``(header id, display name, corrections since the daemon started)`` for
    #: every hwmon header with at least one correction.
    corrections: tuple[tuple[str, str, int], ...]


NO_DRIFT = DutyDriftState(reported=False, not_holding=(), corrections=())


def duty_drift_state(fans: Iterable[object], name_of: Callable[[str], str]) -> DutyDriftState:
    """Read the drift fields off the app's parsed ``FanReading`` list."""
    reported = False
    not_holding: list[DutyDrift] = []
    corrections: list[tuple[str, str, int]] = []
    for fan in sorted(fans, key=lambda f: getattr(f, "id", "")):
        if getattr(fan, "source", "") != "hwmon":
            continue
        count = getattr(fan, "duty_corrections", None)
        flag = getattr(fan, "duty_not_holding", None)
        if count is None and flag is None:
            continue
        reported = True
        fid = str(getattr(fan, "id", ""))
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            corrections.append((fid, name_of(fid), count))
        if flag is True:
            not_holding.append(
                DutyDrift(
                    header_id=fid,
                    name=name_of(fid),
                    corrections=count if isinstance(count, int) else None,
                )
            )
    return DutyDriftState(reported, tuple(not_holding), tuple(corrections))


def drift_key(header_id: str) -> str:
    return DRIFT_KEY_PREFIX + hashlib.sha256(header_id.encode("utf-8")).hexdigest()[:12]


def is_drift_key(key: str) -> bool:
    return key.startswith(DRIFT_KEY_PREFIX)


def drift_problems(state: DutyDriftState, *, doc_url: str, doc_title: str) -> list[dict]:
    """Condition dicts in the readiness shape, one per header not holding.

    The guide link is the caller's (``readiness_report``, beside the BIOS-reclaim
    condition — the same class of problem, another writer on the header), so the
    URL has one definition.

    ``label`` and ``fix`` are GUI-authored only, per that shape's contract —
    they reach rich text unescaped. The fan's name is daemon- or user-supplied,
    so it travels separately as ``subject`` and every consumer escapes it.
    """
    out: list[dict] = []
    for drift in state.not_holding:
        out.append(
            {
                "key": drift_key(drift.header_id),
                "label": "Fan duty is not holding",
                "fix": (
                    "Something other than Control-OFC keeps changing this fan's duty. "
                    "The daemon corrected it three times, the change came back each "
                    "time, so it has stopped rewriting it; it corrects it again once the "
                    "duty agrees or the command changes. Look for the motherboard's own "
                    "fan control (BIOS/EC) or another fan-control program acting on this "
                    "header, and turn it off for it."
                ),
                "doc_url": doc_url,
                "doc_title": doc_title,
                "severity": "warn",
                "subject": drift.name,
                "header_id": drift.header_id,
                "corrections": drift.corrections,
                # The episode: see the module docstring.
                "fingerprint": "" if drift.corrections is None else f"c{drift.corrections}",
            }
        )
    return out


def corrections_line(state: DutyDriftState) -> str:
    """The Interference Monitor's reading — "" when there is nothing to say."""
    if not state.reported or not state.corrections:
        return ""
    parts = ", ".join(f"{name} ({count})" for _hid, name, count in state.corrections)
    return (
        f"Duty corrections since the daemon started: {parts}. Each time, something "
        "else had changed the fan's duty and the daemon put it back."
    )
