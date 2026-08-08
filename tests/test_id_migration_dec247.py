"""Saved fan names follow their header when a driver reshapes its id (DEC-247).

The dangerous direction is matching too eagerly — putting the user's "CPU" name on
the wrong fan — so most of these pin cases the matcher must refuse.
"""

from __future__ import annotations

import pytest

from control_ofc.services.id_migration import (
    apply_realias_moves,
    find_realias_moves,
    parse_hwmon_fan_id,
)

# The reporter's real board, before and after a driver relabel. Same chip, same
# pwm index; the device id and the label are what moved.
OLD = "hwmon:it8696:pci0:pwm1:CHA_FAN1"
NEW = "hwmon:it8696:it87.2624:pwm1:pwm1"


class TestParse:
    @pytest.mark.parametrize(
        ("fan_id", "expected"),
        [
            (OLD, ("it8696", 1)),
            (NEW, ("it8696", 1)),
            ("hwmon:it8696:it87.2624:pwm3:pwm3", ("it8696", 3)),
            # A device id containing colons is why the pwm segment is the anchor
            # and a bare split(":") would not do.
            ("hwmon:nct6687:0000:2d:00.0:pwm2:CPU_FAN", ("nct6687", 2)),
        ],
    )
    def test_reads_chip_and_index(self, fan_id, expected):
        assert parse_hwmon_fan_id(fan_id) == expected

    @pytest.mark.parametrize(
        "fan_id",
        [
            "openfan:ch07",  # channel number — stable by construction
            "amd_gpu:0000:03:00.0",  # PCI BDF — stable by construction
            "hwmon:it8696:it87.2624:temp1",  # a sensor, not a fan
            "hwmon:short",
            "",
        ],
    )
    def test_returns_none_for_ids_that_cannot_be_reshaped(self, fan_id):
        assert parse_hwmon_fan_id(fan_id) is None


class TestFindMoves:
    def test_follows_a_relabelled_header(self):
        assert find_realias_moves({OLD: "CPU"}, {NEW, "openfan:ch00"}) == {OLD: NEW}

    def test_never_moves_a_name_whose_fan_is_still_present(self):
        """A name pointing at live hardware is not orphaned. Moving it would be
        the one unambiguous way to get this wrong."""
        assert find_realias_moves({OLD: "CPU"}, {OLD, NEW}) == {}

    def test_refuses_an_ambiguous_match(self):
        """Two identical chips on one board would make the choice a coin flip."""
        twin_a = "hwmon:it8696:it87.2624:pwm1:pwm1"
        twin_b = "hwmon:it8696:it87.656:pwm1:pwm1"
        assert find_realias_moves({OLD: "CPU"}, {twin_a, twin_b}) == {}

    def test_will_not_overwrite_an_existing_name(self):
        """The user's current name for the live fan always wins over a
        resurrected one."""
        assert find_realias_moves({OLD: "CPU", NEW: "Already Named"}, {NEW}) == {}

    def test_two_orphans_cannot_claim_the_same_fan(self):
        other = "hwmon:it8696:usb0:pwm1:SYS_FAN"
        moves = find_realias_moves({OLD: "CPU", other: "Rear"}, {NEW})
        assert len(moves) == 1

    def test_different_index_is_not_a_match(self):
        assert find_realias_moves({OLD: "CPU"}, {"hwmon:it8696:it87.2624:pwm4:pwm4"}) == {}

    def test_different_chip_is_not_a_match(self):
        assert find_realias_moves({OLD: "CPU"}, {"hwmon:nct6687:it87.2624:pwm1:pwm1"}) == {}

    def test_no_live_fans_means_no_moves(self):
        """Disconnected or pre-first-poll: everything looks orphaned and there is
        nothing sound to match against."""
        assert find_realias_moves({OLD: "CPU"}, set()) == {}

    def test_stable_id_schemes_are_left_alone(self):
        """openfan channels and GPU BDFs embed no label, so they cannot be
        reshaped this way and must never be re-keyed."""
        aliases = {"openfan:ch99": "Gone", "amd_gpu:0000:2d:00.0": "Old GPU"}
        assert find_realias_moves(aliases, {"openfan:ch00", "amd_gpu:0000:03:00.0"}) == {}


class TestApplyMoves:
    def test_moves_the_name_and_drops_the_stale_key(self):
        """Keeping both would leave a phantom row in the Fan Names card and an
        orphan for the DEC-246 prune — the name moved, it was not copied."""
        assert apply_realias_moves({OLD: "CPU", "openfan:ch00": "Front"}, {OLD: NEW}) == {
            NEW: "CPU",
            "openfan:ch00": "Front",
        }


class TestMainWindowIntegration:
    def _fans(self, *ids):
        from control_ofc.api.models import FanReading

        return [FanReading(id=i, source="hwmon", rpm=900, age_ms=10) for i in ids]

    def test_first_poll_re_keys_and_persists(self, qtbot, settings_service):
        from control_ofc.ui.main_window import MainWindow

        settings_service.update(fan_aliases={OLD: "CPU"}, fan_aliases_seeded=True)
        w = MainWindow(settings_service=settings_service)
        qtbot.addWidget(w)
        w._state.set_fans(self._fans(NEW))

        assert settings_service.settings.fan_aliases == {NEW: "CPU"}
        assert w._state.fan_aliases == {NEW: "CPU"}

    def test_the_move_is_announced_not_silent(self, qtbot, settings_service):
        """A name jumping between fans is exactly what someone should be able to
        see and disbelieve."""
        from control_ofc.ui.main_window import MainWindow

        settings_service.update(fan_aliases={OLD: "CPU"}, fan_aliases_seeded=True)
        w = MainWindow(settings_service=settings_service)
        qtbot.addWidget(w)
        w._state.set_fans(self._fans(NEW))

        assert any("re-matched" in e.message for e in w._diag.events)

    def test_demo_mode_never_re_keys(self, qtbot, settings_service):
        """Demo ids collide with real hardware, so a demo session must not touch
        the alias map at all (DEC-244)."""
        from control_ofc.ui.main_window import MainWindow

        settings_service.update(fan_aliases={OLD: "CPU"}, fan_aliases_seeded=True)
        w = MainWindow(settings_service=settings_service, demo_mode=True)
        qtbot.addWidget(w)
        w._state.set_fans(self._fans(NEW))

        assert settings_service.settings.fan_aliases == {OLD: "CPU"}


def test_a_re_key_cannot_lower_the_cpu_pump_floor():
    """The safety argument, pinned rather than left in prose.

    A fan alias becomes ControlMember.member_label, which selects the DEC-095/162
    30% floor. Two existing rules make a wrong match harmless to that floor, and
    this fails if either is ever relaxed:

    1. infer_member_role grants the 30% tier only to source == "hwmon"; a re-key
       never changes a member's source.
    2. role_preserving_label never lowers the inferred role — where the hardware's
       resolved label carries cpu/pump and the alias does not, the hardware label
       is what gets persisted.
    """
    from control_ofc.services.controls_view import role_preserving_label
    from control_ofc.services.profile_service import (
        CONTROL_ROLE_CHASSIS,
        CONTROL_ROLE_CPU_PUMP,
        ControlMember,
        infer_member_role,
    )

    # A name wrongly re-keyed onto the CPU header cannot erase its role.
    assert role_preserving_label("Rear Exhaust", "CPU_FAN", "hwmon") == "CPU_FAN"
    assert (
        infer_member_role(
            ControlMember(source="hwmon", member_id="hwmon:x:y:pwm1:z", member_label="CPU_FAN")
        )
        == CONTROL_ROLE_CPU_PUMP
    )
    # And a non-hwmon member is chassis whatever the alias claims, so re-keying an
    # openfan id could not raise or lower anything either.
    assert (
        infer_member_role(
            ControlMember(source="openfan", member_id="openfan:ch00", member_label="CPU Pump")
        )
        == CONTROL_ROLE_CHASSIS
    )
