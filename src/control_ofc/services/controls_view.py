"""Qt-free view helpers for the Controls page (DEC-214).

Pure derivations kept out of the Qt page so they stay headless-testable: which
fans are unassigned (the Assign-Roles "Unassigned Fans" count) and the per-member
live-RPM map (the compact role-card member rows). No value is fabricated.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def unassigned_fan_ids(fans: Iterable, controls: Sequence) -> list[str]:
    """Fan ids that are not a member of any logical control.

    Feeds the "Unassigned Fans (N)" dropzone in the Assign-Roles pane.
    """
    assigned = {m.member_id for c in controls for m in c.members}
    return [f.id for f in fans if f.id not in assigned]


def member_rpm_map(control, fan_readings: dict) -> dict[str, int | None]:
    """Map each of a control's member ids to its live RPM (``None`` when unknown).

    ``fan_readings`` is an id → reading mapping. A member with no reading (or a
    reading without an RPM) maps to ``None`` so the card leaves that RPM column
    blank rather than inventing a value.
    """
    result: dict[str, int | None] = {}
    for member in control.members:
        reading = fan_readings.get(member.member_id)
        result[member.member_id] = getattr(reading, "rpm", None) if reading is not None else None
    return result
