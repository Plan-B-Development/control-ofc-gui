"""DEC-423: a pump/CPU control's minimum is raised to the enforced floor on load.

Adding the Kraken 2024 Elite to the cooler list made its fan channel pump/CPU
class, and the daemon's validate() rejects a pump/CPU control below 30%
(FLOOR_TOO_LOW). A profile stored before the change would therefore be refused
on activation, because activation saves the in-memory profile first. The loader
now raises such a control to that floor, as it already sanitised a pump's
stop_pct. Only the ENFORCED floor: the chassis 20% is a default a user may keep a
control below, and the loader leaves it alone.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import ProfileActivateResult
from control_ofc.services.profile_service import (
    PROFILE_SCHEMA_VERSION,
    ControlMember,
    Profile,
    ProfileService,
    control_minimum_pct,
)

# A USB HID device id carries colons of its own; the chip is still the second
# field, which is what both sides read.
KRAKEN_FAN = "hwmon:kraken2024elite:0003:1E71:3012.0001:pwm2:Fan speed"


def _control(cid: str, member_id: str, label: str, minimum: float) -> dict:
    return {
        "id": cid,
        "name": cid,
        "mode": "curve",
        "curve_id": "c",
        "members": [{"source": "hwmon", "member_id": member_id, "member_label": label}],
        "minimum_pct": minimum,
    }


def _stored(*controls: dict) -> dict:
    return {
        "id": "k24",
        "name": "Kraken",
        "version": PROFILE_SCHEMA_VERSION,
        "controls": list(controls),
        "curves": [],
    }


def _floor(control) -> float:
    return control_minimum_pct(control.members)


def test_load_raises_a_stored_minimum_to_the_enforced_floor():
    profile = Profile.from_dict(_stored(_control("rad", KRAKEN_FAN, "Fan speed", 20.0)))
    rad = profile.controls[0]
    # Relationship, not a literal: the minimum becomes the pump/CPU role's floor.
    assert rad.minimum_pct == _floor(rad)
    assert rad.minimum_pct > 20.0, "precondition: the enforced floor is above the stored 20"


def test_load_never_lowers_and_never_imposes_the_chassis_default():
    profile = Profile.from_dict(
        _stored(
            # A chassis control a user keeps BELOW the 20% default: kept. The
            # chassis floor is a default, and the daemon enforces none.
            _control("case", "hwmon:it8696:it87.2624:pwm3:pwm3", "SYS_FAN2", 15.0),
            # A pump control a user raised above the floor: kept.
            _control("pump", "hwmon:nct6798:x:pwm1:AIO_PUMP", "AIO_PUMP", 45.0),
        )
    )
    case, pump = profile.controls
    assert case.minimum_pct == 15.0 < _floor(case)
    assert pump.minimum_pct == 45.0 > _floor(pump)


def test_activation_publishes_the_healed_floor(tmp_path, monkeypatch):
    """The call site: activation saves first, so the document the daemon
    receives must already carry the raised minimum, or validate() refuses it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    client = MagicMock()
    client.list_profiles.return_value = [{"id": "k24", "name": "Kraken"}]
    client.get_profile.return_value = _stored(_control("rad", KRAKEN_FAN, "Fan speed", 20.0))
    client.activate_profile.return_value = ProfileActivateResult(activated=True)

    svc = ProfileService(client=client)
    assert svc.load() == []
    outcome = svc.activate("k24", client=client)
    assert outcome.activated, outcome.error

    sent = client.update_profile.call_args.args[1]
    sent_ctrl = sent["controls"][0]
    members = [ControlMember.from_dict(m) for m in sent_ctrl["members"]]
    assert sent_ctrl["minimum_pct"] == control_minimum_pct(members)
    assert sent_ctrl["minimum_pct"] > 20.0, "the stored 20 must not reach the daemon"
