"""`ROLE-c`: the one role-write path (`services/header_role_writes.py`).

The ordering is the safety property (DEC-312): every assign before any clear,
the first failed assign stops everything, and a pump role the user assigned is
removed only after every assign landed AND the confirmation agreed. The
surfaces' own call-site tests live beside each surface (`test_aio_mb_phase2`,
`test_aio_mb_phase7`, `test_header_role_picker`).
"""

from __future__ import annotations

import pytest

from control_ofc.api.errors import DaemonError, DaemonTimeout
from control_ofc.api.models import HeaderRoleResult, HwmonHeader
from control_ofc.services.header_role_writes import (
    KIND_ASSIGN,
    KIND_CLEAR,
    KIND_REMOVE_PUMP,
    apply_role_writes,
    plan_role_writes,
)

NEW = "hwmon:it8696:it87.2624:pwm2:pwm2"
OLD_PUMP = "hwmon:it8696:it87.2624:pwm5:pwm5"
USER_RAD = "hwmon:it8696:it87.2624:pwm3:pwm3"
LABEL_PUMP = "hwmon:nct6799:isa-0290:pwm7:AIO_PUMP"


def _hdr(hid: str, role: str = "unknown", source: str = "none") -> HwmonHeader:
    return HwmonHeader(
        id=hid,
        label=hid.rsplit(":", 1)[-1],
        chip_name=hid.split(":")[1],
        pwm_index=int(hid.split(":pwm")[1].split(":")[0]),
        is_writable=True,
        role=role,
        role_source=source,
    )


HEADERS = [
    _hdr(NEW),
    _hdr(OLD_PUMP, "pump", "user_assigned"),
    _hdr(USER_RAD, "radiator_fan", "user_assigned"),
    _hdr(LABEL_PUMP, "pump", "label"),
]


class _Client:
    """Records every call in one log, so the order across kinds is visible."""

    def __init__(self, *, fail: dict[str, Exception] | None = None, reread_error=None):
        self.log: list[tuple] = []
        self._fail = fail or {}
        self._reread_error = reread_error

    def set_header_role(self, header_id, role):
        self.log.append(("set", header_id, role))
        if header_id in self._fail:
            raise self._fail[header_id]
        return HeaderRoleResult(
            updated=True, header_id=header_id, role=role, effective_role=role or "unknown"
        )

    def hwmon_headers(self):
        self.log.append(("reread",))
        if self._reread_error is not None:
            raise self._reread_error
        return list(HEADERS)


class _Confirm:
    def __init__(self, answer: bool, client: _Client | None = None):
        self.answer = answer
        self.asked: list[list[str]] = []
        self._client = client

    def __call__(self, ids):
        self.asked.append(list(ids))
        if self._client is not None:
            self._client.log.append(("confirm", *ids))
        return self.answer


def _refusal() -> DaemonError:
    return DaemonError(code="persistence_failed", message="read-only fs", status=503)


class TestPlan:
    def test_each_write_is_sorted_by_what_it_does_to_protection(self):
        writes, skipped = plan_role_writes(
            HEADERS,
            [
                (OLD_PUMP, None),  # removes a pump the user named
                (USER_RAD, None),  # drops a role with no floor
                (NEW, "pump"),  # adds protection
                (LABEL_PUMP, "chassis_fan"),  # the label keeps it protected (union)
            ],
        )
        assert [(w.header_id, w.kind) for w in writes] == [
            (NEW, KIND_ASSIGN),
            (LABEL_PUMP, KIND_ASSIGN),
            (USER_RAD, KIND_CLEAR),
            (OLD_PUMP, KIND_REMOVE_PUMP),
        ]
        assert skipped == []

    def test_a_user_pump_downgraded_to_a_radiator_fan_is_a_pump_removal(self):
        writes, _ = plan_role_writes(HEADERS, [(OLD_PUMP, "radiator_fan")])
        assert [w.kind for w in writes] == [KIND_REMOVE_PUMP]

    def test_a_write_that_changes_nothing_is_skipped(self):
        writes, skipped = plan_role_writes(
            HEADERS, [(OLD_PUMP, "pump"), (NEW, None), (USER_RAD, "radiator_fan")]
        )
        assert writes == []
        assert skipped == [OLD_PUMP, NEW, USER_RAD]

    def test_a_clear_on_an_unknown_header_asks_first(self):
        writes, _ = plan_role_writes(HEADERS, [("hwmon:gone:x:pwm9:pwm9", None)])
        assert [w.kind for w in writes] == [KIND_REMOVE_PUMP]


class TestApply:
    def test_assigns_then_clears_then_confirm_then_pump_removals(self):
        client = _Client()
        confirm = _Confirm(True, client)
        published = []
        outcome = apply_role_writes(
            client,
            HEADERS,
            [(OLD_PUMP, None), (USER_RAD, None), (NEW, "pump")],
            confirm=confirm,
            publish_headers=published.append,
        )
        assert client.log == [
            ("set", NEW, "pump"),
            ("set", USER_RAD, None),
            ("confirm", OLD_PUMP),
            ("set", OLD_PUMP, None),
            ("reread",),
        ]
        assert outcome.landed_ids() == {NEW, USER_RAD, OLD_PUMP}
        assert outcome.result_for(NEW).effective_role == "pump"
        assert outcome.headers == HEADERS and published == [HEADERS]

    def test_a_failed_assign_stops_everything_and_asks_nothing(self):
        client = _Client(fail={NEW: _refusal()})
        confirm = _Confirm(True)
        outcome = apply_role_writes(
            client,
            HEADERS,
            [(OLD_PUMP, None), (USER_RAD, None), (LABEL_PUMP, "chassis_fan"), (NEW, "pump")],
            confirm=confirm,
        )
        assert client.log == [
            ("set", LABEL_PUMP, "chassis_fan"),
            ("set", NEW, "pump"),
            ("reread",),
        ], "no clear and no pump removal after a failed assign"
        assert confirm.asked == []
        assert outcome.failed_assign.write.header_id == NEW
        assert outcome.landed_ids() == {LABEL_PUMP}, "the earlier assign did land"

    def test_declining_keeps_the_pump_and_still_rereads_what_landed(self):
        client = _Client()
        outcome = apply_role_writes(
            client, HEADERS, [(NEW, "pump"), (OLD_PUMP, None)], confirm=_Confirm(False)
        )
        assert ("set", OLD_PUMP, None) not in client.log
        assert [w.header_id for w in outcome.declined] == [OLD_PUMP]
        assert client.log[-1] == ("reread",)

    def test_failed_clears_and_removals_are_tolerated_and_reported(self):
        client = _Client(fail={USER_RAD: _refusal(), OLD_PUMP: _refusal()})
        outcome = apply_role_writes(
            client,
            HEADERS,
            [(USER_RAD, None), (OLD_PUMP, None), (NEW, "pump")],
            confirm=_Confirm(True),
        )
        assert outcome.failed_assign is None
        assert [(f.write.header_id, f.write.kind) for f in outcome.failed] == [
            (USER_RAD, KIND_CLEAR),
            (OLD_PUMP, KIND_REMOVE_PUMP),
        ]
        assert outcome.landed_ids() == {NEW}

    def test_the_confirmation_is_asked_once_with_every_removal(self):
        second = "hwmon:it8696:it87.2624:pwm6:pwm6"
        headers = [*HEADERS, _hdr(second, "pump", "user_assigned")]
        confirm = _Confirm(True)
        apply_role_writes(
            _Client(), headers, [(OLD_PUMP, None), (second, "chassis_fan")], confirm=confirm
        )
        assert confirm.asked == [[OLD_PUMP, second]]

    def test_nothing_to_send_sends_nothing_and_rereads_nothing(self):
        client = _Client()
        confirm = _Confirm(True)
        outcome = apply_role_writes(client, HEADERS, [(OLD_PUMP, "pump")], confirm=confirm)
        assert client.log == [] and confirm.asked == []
        assert outcome.skipped == (OLD_PUMP,) and not outcome.attempted

    @pytest.mark.parametrize(
        "error", [DaemonTimeout(message="timed out"), ConnectionError("gone"), KeyError("role")]
    )
    def test_an_unconfirmed_write_is_reported_and_reread(self, error):
        """A timeout or a malformed answer proves nothing either way — the write
        may have landed — so the re-read is what shows the truth."""
        client = _Client(fail={NEW: error})
        outcome = apply_role_writes(client, HEADERS, [(NEW, "pump")], confirm=_Confirm(True))
        assert outcome.failed_assign.error is error
        assert outcome.headers == HEADERS

    def test_a_failed_reread_publishes_nothing(self):
        client = _Client(reread_error=ConnectionError("gone"))
        published = []
        outcome = apply_role_writes(
            client,
            HEADERS,
            [(NEW, "chassis_fan")],
            confirm=_Confirm(True),
            publish_headers=published.append,
        )
        assert outcome.landed_ids() == {NEW}, "precondition: the write landed"
        assert outcome.headers is None and published == []
