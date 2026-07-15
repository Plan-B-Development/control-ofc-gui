"""DEC-214: pure Controls view helpers (unassigned fans + per-member RPM map)."""

from __future__ import annotations

from dataclasses import dataclass

from control_ofc.services.controls_view import member_rpm_map, unassigned_fan_ids
from control_ofc.services.profile_service import ControlMember, LogicalControl


@dataclass
class _Fan:
    id: str
    rpm: int | None = None


def test_unassigned_fan_ids_excludes_assigned():
    fans = [_Fan("openfan:0"), _Fan("openfan:1"), _Fan("hwmon:pwm1")]
    controls = [
        LogicalControl(
            id="r1",
            name="CPU",
            members=[ControlMember(source="openfan", member_id="openfan:0")],
        )
    ]
    assert unassigned_fan_ids(fans, controls) == ["openfan:1", "hwmon:pwm1"]


def test_unassigned_fan_ids_all_when_no_controls():
    fans = [_Fan("a"), _Fan("b")]
    assert unassigned_fan_ids(fans, []) == ["a", "b"]


def test_member_rpm_map_resolves_and_blanks_unknown():
    control = LogicalControl(
        id="r1",
        name="Intake",
        members=[
            ControlMember(source="openfan", member_id="openfan:0"),
            ControlMember(source="openfan", member_id="openfan:1"),  # no reading
        ],
    )
    fan_map = {"openfan:0": _Fan("openfan:0", rpm=1151)}
    result = member_rpm_map(control, fan_map)
    assert result == {"openfan:0": 1151, "openfan:1": None}


def test_member_rpm_map_none_when_reading_has_no_rpm():
    control = LogicalControl(
        id="r1",
        name="X",
        members=[ControlMember(source="hwmon", member_id="hwmon:pwm1")],
    )
    fan_map = {"hwmon:pwm1": _Fan("hwmon:pwm1", rpm=None)}
    assert member_rpm_map(control, fan_map) == {"hwmon:pwm1": None}
