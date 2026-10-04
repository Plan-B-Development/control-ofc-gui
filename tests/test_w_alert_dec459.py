"""W-ALERT Run 2 (DEC-459): what becomes an alert, and the stall hold.

Three changes, each tested at its rule AND at every call site that reads it:

* ``TS-bg`` — a stall is held through a missing ``stall_detected`` for up to
  ``HOLD_S`` (ending early on RPM above 0), and the alert, the Dashboard onset
  annotation, the fan card and the Hardware page's header status all read the
  held set (``AppState.stalled_fan_ids``), never the raw flag.
* ``WIRE-o`` (Q2-C) — the driver's ``fan_alarm`` is a warning-tone fan-card
  state (DRIVER_ALARM, below LOW_RPM), never an alert.
* ``DC-cz`` (Q5-A) — a non-normal ``thermal_state`` is an alert while the daemon
  is reachable: an emergency is an error naming its causes, the no-sensor floor,
  an older daemon's recovery hold and an unknown token are warnings.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    ConnectionState,
    DaemonStatus,
    FanReading,
    HwmonHeader,
    OverrideStatusEntry,
)
from control_ofc.services import cooling_watch
from control_ofc.services.alerts_view import next_action_for_warning
from control_ofc.services.app_state import AppState
from control_ofc.services.fan_cards_view import FanState, build_fan_card_vms
from control_ofc.services.header_inspector_view import (
    STATUS_DRIVER_ALARM,
    STATUS_NEEDS_ATTENTION,
    STATUS_NORMAL,
    build_header_inspector_views,
)
from control_ofc.services.profile_service import ControlMember, LogicalControl, Profile
from control_ofc.services.stall_hold import HOLD_S, StallHold

FAN = "openfan:ch00"


def _fan(stall: bool | None, rpm: int | None = 0, fan_id: str = FAN) -> FanReading:
    return FanReading(
        id=fan_id,
        source="openfan",
        rpm=rpm,
        last_commanded_pwm=60,
        age_ms=100,
        stall_detected=stall,
    )


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _state(clock: _Clock) -> AppState:
    state = AppState()
    state._clock = clock
    state.set_connection(ConnectionState.CONNECTED)
    return state


# ── TS-bg: the hold itself ───────────────────────────────────────────────────


class TestStallHold:
    def test_a_missing_flag_holds_a_stall_seen_before(self):
        hold = StallHold()
        assert hold.update([_fan(True)], 0.0) == {FAN}
        assert hold.update([_fan(None)], 1.0) == {FAN}

    def test_the_hold_ends_after_hold_s(self):
        hold = StallHold()
        hold.update([_fan(True)], 0.0)
        assert hold.update([_fan(None)], HOLD_S - 0.1) == {FAN}
        assert hold.update([_fan(None)], HOLD_S) == frozenset()

    def test_rpm_above_zero_ends_the_hold_at_once(self):
        hold = StallHold()
        hold.update([_fan(True)], 0.0)
        assert hold.update([_fan(None, rpm=800)], 1.0) == frozenset()

    def test_an_explicit_false_clears_it(self):
        hold = StallHold()
        hold.update([_fan(True)], 0.0)
        assert hold.update([_fan(False)], 1.0) == frozenset()

    def test_a_fan_never_seen_stalled_is_never_held(self):
        assert StallHold().update([_fan(None)], 0.0) == frozenset()

    def test_a_fan_missing_from_the_poll_is_dropped(self):
        hold = StallHold()
        hold.update([_fan(True)], 0.0)
        assert hold.update([], 1.0) == frozenset()

    def test_a_new_true_restarts_the_hold(self):
        hold = StallHold()
        hold.update([_fan(True)], 0.0)
        hold.update([_fan(True)], 4.0)
        # 4.5 s after the first True, but only 0.5 s after the second.
        assert hold.update([_fan(None)], 4.0 + HOLD_S - 0.5) == {FAN}


# ── TS-bg: every consumer reads the held set ─────────────────────────────────


def _stall_events(transitions: list, kind: str) -> list:
    return [t for t in transitions if t.kind == kind and t.occurrence.key == f"fan_stall:{FAN}"]


class TestAlertReadsTheHold:
    def test_a_one_poll_gap_is_one_occurrence(self):
        """``True, None, True`` used to be onset, recovery, onset — two alerts and
        a recovery for one stalled fan."""
        clock = _Clock()
        state = _state(clock)
        seen: list = []
        state.alert_transitions.connect(seen.extend)
        for flag in (True, None, True):
            state.set_fans([_fan(flag)])
            clock.now += 1.0
        assert len(_stall_events(seen, "onset")) == 1
        assert _stall_events(seen, "recovered") == []
        assert FAN in state.stalled_fan_ids

    def test_the_alert_ends_when_the_hold_does(self):
        """Presence first, then the absence the hold's bound promises."""
        clock = _Clock()
        state = _state(clock)
        state.set_fans([_fan(True)])
        assert state.alerts.active_count() == 1
        clock.now += HOLD_S
        state.set_fans([_fan(None)])
        assert state.alerts.active_count() == 0


class TestDashboardReadsTheHold:
    def _labels(self, page) -> list[str]:
        return [lbl for _, _, lbl in page._chart._annotations]

    def test_one_stall_annotation_across_a_missing_flag(self, qtbot):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        clock = _Clock()
        state = _state(clock)
        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        for flag in (True, None, True):
            state.set_fans([_fan(flag)])
            clock.now += 1.0
        stall_lines = [lbl for lbl in self._labels(page) if lbl.startswith("Stall:")]
        assert len(stall_lines) == 1, stall_lines

    def test_the_card_stays_stalled_through_a_missing_flag(self, qtbot, profile_service):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        profile_service.active_profile.controls = [
            LogicalControl(
                id="c1", name="Chassis", members=[ControlMember(source="openfan", member_id=FAN)]
            )
        ]
        clock = _Clock()
        state = _state(clock)
        page = DashboardPage(state=state, profile_service=profile_service)
        qtbot.addWidget(page)
        state.set_fans([_fan(True)])
        clock.now += 1.0
        state.set_fans([_fan(None)])
        assert page._fan_cards["c1"]._state_chip.text() == "Stall"


def test_the_card_builder_reads_the_set_not_the_flag():
    """The relationship, both ways: a held id with no flag is STALL; a raw True
    the set does not hold is not."""
    control = LogicalControl(
        id="c1", name="C", members=[ControlMember(source="openfan", member_id=FAN)]
    )
    profile = Profile(id="p", name="P", controls=[control])
    held = build_fan_card_vms(
        [_fan(None)], active_profile=profile, overrides=[], stalled_ids={FAN}
    )[0]
    assert held.state is FanState.STALL
    unheld = build_fan_card_vms(
        [_fan(True)], active_profile=profile, overrides=[], stalled_ids=frozenset()
    )[0]
    assert unheld.state is not FanState.STALL


HEADER = HwmonHeader(
    id="hwmon:nct6799:isa-0a20:pwm2:SYS_FAN1",
    label="SYS_FAN1",
    chip_name="nct6799",
    pwm_index=2,
    rpm_available=True,
    is_writable=True,
    rpm_min_threshold=600,
)


def _hwmon_reading(stall: bool | None, alarm: bool | None = None, rpm: int = 0) -> FanReading:
    return FanReading(
        id=HEADER.id,
        source="hwmon",
        rpm=rpm,
        pwm_commanded_pct=60,
        age_ms=100,
        stall_detected=stall,
        fan_alarm=alarm,
    )


def _status(view) -> str:
    return next(r.value for r in view.live_rows if r.label == "Status")


class TestHeaderInspectorReadsTheHold:
    def test_the_builder_reads_the_set_not_the_flag(self):
        [held] = build_header_inspector_views(
            [HEADER], readings=[_hwmon_reading(None)], stalled_ids={HEADER.id}
        )
        assert _status(held) == STATUS_NEEDS_ATTENTION
        [unheld] = build_header_inspector_views(
            [HEADER], readings=[_hwmon_reading(True)], stalled_ids=frozenset()
        )
        assert _status(unheld) == STATUS_NORMAL

    def test_the_hardware_page_passes_the_held_set(self, qtbot):
        from control_ofc.ui.pages.hardware_page import HardwarePage

        clock = _Clock()
        state = _state(clock)
        state.set_hwmon_headers([HEADER])
        page = HardwarePage(state=state)
        qtbot.addWidget(page)
        state.set_fans([_hwmon_reading(True)])
        clock.now += 1.0
        state.set_fans([_hwmon_reading(None)])
        assert HEADER.id in state.stalled_fan_ids, "precondition: the hold is on"
        view = page._header_cards[HEADER.id]._view
        assert _status(view) == STATUS_NEEDS_ATTENTION


def _status_row(view):
    return next(r for r in view.live_rows if r.label == "Status")


class TestHeaderInspectorDriverAlarm:
    """`ALERT-a` (U12): the inspector agrees with the fan card on a driver alarm."""

    def test_an_alarm_alone_is_the_cards_warning_state(self):
        reading = _hwmon_reading(False, alarm=True, rpm=400)
        [view] = build_header_inspector_views([HEADER], readings=[reading], stalled_ids=frozenset())
        [card] = _alarm_cards(reading)
        assert card.state is FanState.DRIVER_ALARM, "precondition: the card calls it an alarm"
        row = _status_row(view)
        assert row.value == STATUS_DRIVER_ALARM == card.state.value
        assert row.state == "warn"

    def test_a_stall_stays_critical_with_or_without_the_alarm(self):
        for alarm in (True, False):
            [view] = build_header_inspector_views(
                [HEADER], readings=[_hwmon_reading(True, alarm=alarm)], stalled_ids={HEADER.id}
            )
            row = _status_row(view)
            assert (row.value, row.state) == (STATUS_NEEDS_ATTENTION, "critical"), alarm

    @pytest.mark.parametrize("alarm", [False, None])
    def test_no_alarm_is_normal(self, alarm):
        [view] = build_header_inspector_views(
            [HEADER], readings=[_hwmon_reading(False, alarm=alarm, rpm=400)], stalled_ids=()
        )
        assert _status_row(view).value == STATUS_NORMAL


# ── WIRE-o (Q2-C): the driver alarm is a card state, never an alert ──────────


def _alarm_cards(*fans: FanReading, headers=(HEADER,), overrides=()):
    control = LogicalControl(
        id="c1",
        name="C",
        members=[ControlMember(source="hwmon", member_id=f.id) for f in fans],
    )
    return build_fan_card_vms(
        list(fans),
        active_profile=Profile(id="p", name="P", controls=[control]),
        overrides=list(overrides),
        headers=list(headers),
        stalled_ids=frozenset(),
        display_name=lambda fid: "Rear fan" if fid == HEADER.id else fid,
    )


class TestDriverAlarm:
    def test_an_alarm_is_a_card_state_naming_the_fan_and_its_limit(self):
        [card] = _alarm_cards(_hwmon_reading(False, alarm=True, rpm=400))
        assert card.state is FanState.DRIVER_ALARM
        assert "Rear fan" in card.state_detail
        assert f"{HEADER.rpm_min_threshold} RPM" in card.state_detail

    def test_no_limit_is_not_invented(self):
        header = HwmonHeader(id=HEADER.id, label="SYS_FAN1", pwm_index=2)
        [card] = _alarm_cards(_hwmon_reading(False, alarm=True, rpm=400), headers=(header,))
        assert card.state is FanState.DRIVER_ALARM
        assert "RPM" not in card.state_detail

    def test_an_unknown_alarm_is_not_an_alarm(self):
        [card] = _alarm_cards(_hwmon_reading(False, alarm=None, rpm=400))
        assert card.state is FanState.NORMAL
        assert card.state_detail == ""

    def test_low_rpm_outranks_it_and_it_outranks_an_override(self):
        """The user's order (2026-09-30): … > LOW_RPM > DRIVER_ALARM > OVERRIDE."""
        low = FanReading(id="hwmon:x:pwm3", source="hwmon", rpm=0, pwm_commanded_pct=60, age_ms=1)
        [card] = _alarm_cards(_hwmon_reading(False, alarm=True, rpm=400), low)
        assert card.state is FanState.LOW_RPM
        assert card.state_detail == "", "the tooltip is for the alarm state only"
        [card] = _alarm_cards(
            _hwmon_reading(False, alarm=True, rpm=400),
            overrides=(OverrideStatusEntry(control_id="c1", pwm_percent=70),),
        )
        assert card.state is FanState.DRIVER_ALARM

    def test_it_raises_no_alert(self):
        state = _state(_Clock())
        state.set_fans([_hwmon_reading(False, alarm=True, rpm=400)])
        assert state.alerts.active_count() == 0

    def test_the_card_renders_the_chip_and_its_tooltip(self, qtbot):
        from control_ofc.ui.widgets.fan_control_card import FanControlCard

        [vm] = _alarm_cards(_hwmon_reading(False, alarm=True, rpm=400))
        card = FanControlCard(vm)
        qtbot.addWidget(card)
        assert card._state_chip.text() == "Driver alarm"
        assert card._state_chip.property("class") == "WarningChip"
        assert "Rear fan" in card._state_chip.toolTip()
        card.update_vm(_alarm_cards(_hwmon_reading(False, alarm=False, rpm=400))[0])
        assert card._state_chip.toolTip() == "", "a cleared alarm leaves no stale tooltip"

    def test_the_chip_describes_the_alarm_to_a_screen_reader(self, qtbot):
        """`ALERT-b`: the detail was reachable only by hovering the chip."""
        from control_ofc.ui.widgets.fan_control_card import FanControlCard

        [vm] = _alarm_cards(_hwmon_reading(False, alarm=True, rpm=400))
        assert vm.state_detail, "precondition: an alarm card carries a detail"
        card = FanControlCard(vm)
        qtbot.addWidget(card)
        assert card._state_chip.accessibleDescription() == vm.state_detail
        card.update_vm(_alarm_cards(_hwmon_reading(False, alarm=False, rpm=400))[0])
        assert card._state_chip.accessibleDescription() == "", "cleared with the alarm"

    @pytest.mark.parametrize("with_limit", [True, False])
    def test_each_detail_line_is_one_short_fact(self, with_limit):
        """`ALERT-c`: ``safe_tooltip`` never wraps, so the lines must be short
        where they are built. The name line carries a user alias, whose length
        the GUI does not choose, so it is checked for its wording instead."""
        headers = (HEADER,) if with_limit else (HwmonHeader(id=HEADER.id, pwm_index=2),)
        [card] = _alarm_cards(_hwmon_reading(False, alarm=True, rpm=400), headers=headers)
        name_line, *facts = card.state_detail.splitlines()
        assert name_line == "Rear fan: the fan chip reports an alarm."
        assert len(facts) == 4
        assert max(len(line) for line in facts) <= 60, facts
        assert (f"{HEADER.rpm_min_threshold} RPM" in card.state_detail) is with_limit


# ── DC-cz (Q5-A): a thermal state is an alert ────────────────────────────────


# The two new maps are pinned where every other map on the same field is:
# `test_thermal_state_maps_cover_the_wire_vocabulary` and
# `test_every_wording_map_covers_the_wire_vocabulary`.


class TestThermalAlertWording:
    def test_normal_raises_nothing(self):
        assert cooling_watch.thermal_alert("normal", []) is None
        assert cooling_watch.thermal_alert("", []) is None

    def test_an_emergency_is_an_error_naming_its_causes(self):
        alert = cooling_watch.thermal_alert("emergency", ["coolant"])
        assert alert.level == "error"
        assert "coolant" in alert.title
        assert "CPU" not in alert.title
        assert cooling_watch.EMERGENCY_CAUSE_PHRASES["coolant"] in alert.detail

    def test_an_older_daemons_emergency_is_the_cpu_one(self):
        assert "CPU" in cooling_watch.thermal_alert("emergency", []).title

    @pytest.mark.parametrize("state", ["no_sensor_fallback", "recovery"])
    def test_the_forcing_states_are_warnings(self, state):
        assert cooling_watch.thermal_alert(state, []).level == "warning"

    def test_an_unknown_state_is_a_warning_naming_the_token(self):
        alert = cooling_watch.thermal_alert("overheat_v9", [])
        assert alert.level == "warning"
        assert "overheat_v9" in alert.title

    def test_advice_for_each(self):
        assert "Hardware page" in next_action_for_warning({"_key": "thermal:no_sensor_fallback"})
        assert "fans and pump" in next_action_for_warning({"_key": "thermal:emergency"})


def _emergency(state: AppState, causes=("cpu",)) -> None:
    state.set_status(DaemonStatus(thermal_state="emergency", emergency_causes=list(causes)))
    state.set_fans([])


class TestThermalAlertInTheLedger:
    def test_an_emergency_is_an_active_error(self):
        state = _state(_Clock())
        counts: list[int] = []
        state.warning_count_changed.connect(counts.append)
        _emergency(state)
        [occ] = state.alerts.present()
        assert (occ.key, occ.level, occ.source) == ("thermal:emergency", "error", "thermal")
        # The footer's health rollup is driven by this count, so it cannot read
        # "All systems nominal" during an emergency.
        assert counts == [1]

    def test_acknowledging_quiets_the_badge_but_not_the_health_count(self):
        state = _state(_Clock())
        _emergency(state)
        state.acknowledge_all()
        assert state.unacknowledged_count == 0
        assert state.warning_count == 1

    def test_a_state_change_closes_one_alert_and_raises_the_next(self):
        state = _state(_Clock())
        seen: list = []
        state.alert_transitions.connect(seen.extend)
        state.set_status(DaemonStatus(thermal_state="no_sensor_fallback"))
        state.set_fans([])
        _emergency(state)
        kinds = [(t.kind, t.occurrence.key) for t in seen]
        assert ("recovered", "thermal:no_sensor_fallback") in kinds
        assert ("onset", "thermal:emergency") in kinds

    def test_it_clears_once_the_daemon_stays_unreachable(self):
        """Presence first; then only the connection moves and the clear timer
        fires. No poll arrives while disconnected, so the timer's reconcile is the
        only one that can clear it."""
        from control_ofc.services.app_state import THERMAL_UNREACHABLE_CLEAR_MS

        state = _state(_Clock())
        _emergency(state)
        state.set_connection(ConnectionState.DISCONNECTED)
        assert state.warning_count == 1, "not cleared by the disconnect alone"
        timer = state._thermal_clear_timer
        assert timer.isActive() and timer.interval() == THERMAL_UNREACHABLE_CLEAR_MS
        timer.timeout.emit()
        assert state.warning_count == 0

    def test_one_failed_poll_is_not_a_recovery(self):
        """The poll reports a disconnect on its FIRST failed cycle. A timeout
        inside an emergency must not log a recovery, nor mint a fresh
        unacknowledged alert once the daemon answers again."""
        state = _state(_Clock())
        seen: list = []
        state.alert_transitions.connect(seen.extend)
        _emergency(state)
        state.acknowledge_all()
        state.set_connection(ConnectionState.DISCONNECTED)
        state.set_connection(ConnectionState.CONNECTED)
        assert not state._thermal_clear_timer.isActive(), "reconnect cancels the clear"
        _emergency(state)
        keys = [(t.kind, t.occurrence.key) for t in seen]
        assert keys.count(("onset", "thermal:emergency")) == 1
        assert ("recovered", "thermal:emergency") not in keys
        assert state.unacknowledged_count == 0

    def test_the_footer_is_not_nominal_during_an_emergency(self, qtbot):
        """The wiring MainWindow makes (DEC-282): the footer takes the ACTIVE count."""
        from control_ofc.ui.components.footer import StatusFooter

        state = _state(_Clock())
        footer = StatusFooter()
        qtbot.addWidget(footer)
        state.warning_count_changed.connect(footer.set_warning_count)
        _emergency(state)
        assert footer._health_label.text() != "All systems nominal"
