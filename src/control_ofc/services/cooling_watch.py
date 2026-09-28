"""Wording for the daemon's cooling-failure watch (DEC-443, `W-SAFE`). Qt-free.

The daemon publishes three things on ``/status`` and owns none of their words:
``emergency_causes`` (why ``thermal_state`` is ``emergency``), ``pump_stalls``
(pumps under the stall response) and ``advisories`` (a cooling problem the CPU
emergency will not see). The client owns the wording, and every map here is
pinned against the wire vocabulary in ``api.models`` by
``tests/test_cooling_watch.py`` — the DEC-257 rule for a presentation map keyed
off a wire field.

**An unrecognised token renders**, at the severity its field implies, rather
than disappearing: a daemon that gains a new state must not go quiet here.

No daemon timing constant appears in any string — the stall window, the
response window and the advisory hold are daemon-side and not on the wire, so a
number here would be a copy that drifts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from control_ofc.api.models import CoolingAdvisory, PumpStall

#: ``pump_stalls[].state`` → (title suffix, detail after the pump's name).
PUMP_STALL_TEXT: dict[str, tuple[str, str]] = {
    "stall_response": (
        "stalled",
        "reads 0 RPM while it is commanded to run, so the daemon is running it at "
        "full speed to restart it.",
    ),
    "not_turning": (
        "not turning",
        "still reads 0 RPM at full speed. Check the pump's power and cable now — the "
        "CPU may have no liquid cooling.",
    ),
    "held": (
        "held at full speed",
        "has stalled twice since the daemon started, so the daemon holds it at full "
        "speed until the daemon restarts or a profile is activated.",
    ),
}

#: ``advisories[].code`` → title. The detail is built from the record's figures.
ADVISORY_TITLES: dict[str, str] = {
    "cpu_at_ceiling_low_cooling": "CPU at its limit with low cooling",
}

#: ``emergency_causes[]`` token → the clause that opens the emergency reason.
EMERGENCY_CAUSE_PHRASES: dict[str, str] = {
    "cpu": "A critical CPU temperature was reached",
    "coolant": "The coolant reached its configured limit",
}


#: ``emergency_causes[]`` token → what has to cool before the emergency ends —
#: the Dashboard banner's "your profile resumes fully once …" clause.
EMERGENCY_CAUSE_SUBJECTS: dict[str, str] = {
    "cpu": "the CPU",
    "coolant": "the coolant",
}


@dataclass(frozen=True)
class CoolingAlert:
    """One alert-ledger condition, Qt-free."""

    key: str
    level: str
    title: str
    detail: str


def pump_stall_alert(name: str, stall: PumpStall) -> CoolingAlert:
    """The error-level alert for one pump under the stall response (the user's Q7).

    Every state is an error: the pump is not moving coolant, or was not moments
    ago. ``name`` is the pump's resolved display name.
    """
    suffix, detail = PUMP_STALL_TEXT.get(
        stall.state,
        (
            stall.state or "unknown state",
            f"is in a stall state this client does not know ({stall.state}).",
        ),
    )
    return CoolingAlert(
        key=f"pump_stall:{stall.header_id}",
        level="error",
        title=f"Pump '{name}' {suffix}",
        detail=f"Pump '{name}' {detail}",
    )


def advisory_alert(advisory: CoolingAdvisory) -> CoolingAlert:
    """The warning-level alert for a cooling advisory. It forces nothing, and says so."""
    title = ADVISORY_TITLES.get(advisory.code, f"Cooling advisory: {advisory.code}")
    if advisory.code == "cpu_at_ceiling_low_cooling":
        detail = (
            f"The CPU has been at or above its {advisory.ceiling_c:.0f} °C limit "
            f"({advisory.cpu_temp_c:.1f} °C) for over a minute while no fan or pump is "
            f"commanded above {advisory.max_duty_pct}%. Check that the pump and fans are "
            f"running — the daemon does not force anything for this."
        )
    else:
        detail = (
            f"The daemon raised a cooling advisory this client does not know ({advisory.code})."
        )
    return CoolingAlert(
        key=f"cooling_advisory:{advisory.code}",
        level="warning",
        title=title,
        detail=detail,
    )


def emergency_opening(causes: Sequence[str]) -> str:
    """The clause naming what tripped the emergency, e.g. "The coolant reached its
    configured limit". Empty ``causes`` — a daemon before 3.0.0, whose only
    emergency is the CPU one — reads as the CPU clause, which is true of it."""
    if not causes:
        return EMERGENCY_CAUSE_PHRASES["cpu"]
    phrases = [
        EMERGENCY_CAUSE_PHRASES.get(
            c, f"A safety limit this client does not know ({c}) was reached"
        )
        for c in causes
    ]
    if len(phrases) == 1:
        return phrases[0]
    return phrases[0] + " and " + " and ".join(p[0].lower() + p[1:] for p in phrases[1:])


def emergency_resume(causes: Sequence[str]) -> str:
    """When a profile resumes after an emergency, e.g. "once the coolant cools".

    Empty ``causes`` is a daemon before 3.0.0, whose only emergency is the CPU
    one. A cause this client does not know makes the clause generic rather than
    naming only the causes it does know — it must not point the user at the
    wrong component.
    """
    known = list(dict.fromkeys(causes or ("cpu",)))
    if any(c not in EMERGENCY_CAUSE_SUBJECTS for c in known):
        return "once temperatures recover"
    subjects = [EMERGENCY_CAUSE_SUBJECTS[c] for c in known]
    if len(subjects) == 1:
        return f"once {subjects[0]} cools"
    return f"once {' and '.join(subjects)} cool"
