"""Alert lifecycle (DEC-282) — the ledger, its wiring into AppState, and the
event-log observer.

The four tests marked RC-1/RC-2/RC-3 below were verified to FAIL against the code
immediately before DEC-282; they are the regression guards for the three root causes
of the disappearing-alert behaviour.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import FanReading, SensorReading
from control_ofc.services.alerts import (
    AlertCondition,
    AlertLedger,
    AlertState,
    AlertTransition,
    transition_to_log,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.widgets.alert_status_bar import AlertStatusBar


def cond(key: str, level: str = "warning", detail: str = "d", title: str = "t") -> AlertCondition:
    return AlertCondition(
        key=key, level=level, source="fan", component="c", title=title, detail=detail
    )


STALL = FanReading(id="cpu_fan", rpm=0, last_commanded_pwm=60, age_ms=100, stall_detected=True)
SPINNING = FanReading(
    id="cpu_fan", rpm=900, last_commanded_pwm=60, age_ms=100, stall_detected=False
)


def stale_sensor(sid: str) -> SensorReading:
    return SensorReading(id=sid, label=sid, age_ms=5000)


def fresh_sensor(sid: str) -> SensorReading:
    return SensorReading(id=sid, label=sid, age_ms=100)


class TestLedgerStateMachine:
    def test_onset_then_recovery_traverses_isa_states(self):
        led = AlertLedger()
        trs = led.reconcile([cond("k")], now=100.0)
        assert [t.kind for t in trs] == ["onset"]
        assert led.present()[0].state is AlertState.ACTIVE

        trs = led.reconcile([], now=101.5)
        assert [t.kind for t in trs] == ["recovered"]
        # Cleared but never seen — the state a linear active/acknowledged/recovered
        # model cannot express, and the one that stops an alert vanishing unnoticed.
        assert led.recovered()[0].state is AlertState.RECOVERED_UNSEEN
        assert led.unacknowledged_count() == 1
        assert led.active_count() == 0

        led.acknowledge("k", now=102.0)
        assert led.recovered()[0].state is AlertState.RECOVERED
        assert led.unacknowledged_count() == 0

    def test_acknowledging_a_present_condition_does_not_clear_it(self):
        """RC-3/§2: acknowledgement means "seen", never "resolved"."""
        led = AlertLedger()
        led.reconcile([cond("k")], now=100.0)
        led.acknowledge("k", now=101.0)

        occ = led.present()[0]
        assert occ.state is AlertState.ACKNOWLEDGED
        assert occ.is_present is True
        assert led.active_count() == 1, "the condition is still happening"
        assert led.unacknowledged_count() == 0, "but nothing is unseen"

    def test_recurrence_after_recovery_mints_a_new_occurrence(self):
        """RC-3: the transition ISA-18.2 calls "Re-Alarm Unack"."""
        led = AlertLedger()
        led.reconcile([cond("k")], now=100.0)
        led.acknowledge("k", now=101.0)
        led.reconcile([], now=102.0)
        assert led.unacknowledged_count() == 0, "acknowledged and recovered — quiet"

        trs = led.reconcile([cond("k")], now=200.0)

        assert [t.kind for t in trs] == ["onset"]
        occ = led.present()[0]
        assert occ.activation_epoch == 200.0, "a new occurrence, not the acknowledged one"
        assert occ.state is AlertState.ACTIVE
        assert led.unacknowledged_count() == 1

    def test_continuous_condition_is_one_occurrence_across_many_polls(self):
        """§4: no poll-cycle spam. The event feed is capped, so a per-poll onset would
        flush every other diagnostic out of it within a couple of minutes."""
        led = AlertLedger()
        onsets = 0
        for tick in range(200):
            onsets += sum(
                1 for t in led.reconcile([cond("k")], now=float(tick)) if t.kind == "onset"
            )

        assert onsets == 1
        assert led.present()[0].activation_epoch == 0.0, "onset time is not bumped by re-sighting"
        assert led.present()[0].last_detected == 199.0

    def test_same_count_swap_is_visible_to_the_ledger(self):
        """RC-1 at the ledger level: A out, B in, count unchanged at 1."""
        led = AlertLedger()
        led.reconcile([cond("A")], now=100.0)
        trs = led.reconcile([cond("B")], now=101.0)

        assert sorted(t.kind for t in trs) == ["onset", "recovered"]
        assert led.active_count() == 1
        assert [o.key for o in led.present()] == ["B"]

    def test_recovered_history_is_bounded(self):
        led = AlertLedger(max_recovered=3)
        for i in range(6):
            led.reconcile([cond(f"k{i}")], now=float(i))
            led.reconcile([], now=float(i) + 0.5)

        assert len(led.recovered()) == 3
        assert [o.key for o in led.recovered()] == ["k5", "k4", "k3"], "newest first"

    def test_transition_snapshot_is_independent_of_later_acknowledgement(self):
        led = AlertLedger()
        trs = led.reconcile([cond("k")], now=100.0)
        led.acknowledge("k", now=101.0)

        assert trs[0].occurrence.acknowledged is False, "the snapshot records what was true"
        assert led.present()[0].acknowledged is True

    def test_present_orders_most_severe_first(self):
        led = AlertLedger()
        led.reconcile([cond("warn", level="warning"), cond("err", level="error")], now=100.0)
        assert [o.key for o in led.present()] == ["err", "warn"]


class TestTransitionToLog:
    def test_onset_carries_the_condition_sentence(self):
        led = AlertLedger()
        trs = led.reconcile([cond("k", level="error", detail="Fan 'cpu_fan' stall detected")], 1.0)
        assert transition_to_log(trs[0]) == ("error", "fan", "Fan 'cpu_fan' stall detected")

    def test_recovery_reads_back_the_title_with_a_duration(self):
        """§4's worked example: "CPU_FAN stall recovered after 1.2 seconds"."""
        led = AlertLedger()
        led.reconcile([cond("k", title="CPU_FAN stall")], now=100.0)
        trs = led.reconcile([], now=101.2)

        level, source, message = transition_to_log(trs[0])
        assert level == "info", "a recovery is good news, not a warning"
        assert source == "fan"
        assert message == "CPU_FAN stall recovered after 1.2s"

    def test_recovery_of_an_unknown_kind_is_not_reachable_as_an_onset(self):
        led = AlertLedger()
        trs = led.reconcile([cond("k")], now=1.0)
        forged = AlertTransition("recovered", trs[0].occurrence)
        assert transition_to_log(forged)[0] == "info"


class TestAppStateWiring:
    """CLAUDE.md: extracting a rule into a testable function does NOT test the call
    site — that lesson has recurred five times here. These drive AppState's real
    setters and assert through live signal connections."""

    def test_rc1_same_count_swap_refreshes_the_view_through_the_signal(self, qtbot):
        """RC-1, end to end.

        Verified to fail before DEC-282: ``warning_count_changed`` was gated on the
        list length, so this swap emitted nothing and the view went on rendering S1.
        Asserted through the real connection, never by calling ``refresh()``.
        """
        state = AppState()
        bar = AlertStatusBar(state)
        qtbot.addWidget(bar)
        state.set_sensors([stale_sensor("S1")])
        assert bar.vm.warning_count == 1
        assert "Sensor 'S1' is stale" in bar._headline.text()

        changes: list[int] = []
        state.warnings_changed.connect(lambda: changes.append(1))
        counts: list[int] = []
        state.warning_count_changed.connect(counts.append)

        state.set_sensors([fresh_sensor("S1"), stale_sensor("S2")])

        assert counts == [], "the count genuinely did not move — that was the trap"
        assert changes, "but the content did, and the content signal must fire"
        assert bar._headline.text() == "Sensor 'S2' is stale (age 5000ms)", (
            "the surface must now show the condition that is real, not the resolved one"
        )
        # S1 did not simply vanish either — it recovered before anyone looked at it,
        # so it is retained as history. That is the whole point of DEC-282.
        assert [o.key for o in state.alerts.recovered()] == ["sensor_stale:S1"]
        assert state.unacknowledged_count == 2, "the live one and the unseen recovery"

    def test_rc2_onset_and_recovery_each_log_exactly_once(self, qtbot):
        """RC-2. Verified to fail before DEC-282: transitions wrote zero events."""
        state = AppState()
        diag = DiagnosticsService(state)
        diag.attach_alert_source(state)

        state.set_fans([STALL])
        onset = [e for e in diag.events if "stall detected" in e.message]
        assert len(onset) == 1
        assert onset[0].level == "error"
        assert onset[0].source == "fan"

        state.set_fans([SPINNING])
        recovery = [e for e in diag.events if "recovered after" in e.message]
        assert len(recovery) == 1
        assert recovery[0].level == "info"

    def test_rc2_a_persistent_condition_does_not_spam_the_feed(self, qtbot):
        """§4. The feed is capped at MAX_EVENTS, so this is a correctness concern and
        not merely a tidiness one."""
        state = AppState()
        diag = DiagnosticsService(state)
        diag.attach_alert_source(state)

        for _ in range(50):
            state.set_fans([STALL])

        assert len([e for e in diag.events if "stall detected" in e.message]) == 1

    def test_interleaved_sensor_and_fan_setters_log_one_onset_and_one_recovery(self, qtbot):
        """Correction C — the real cadence, not a simplified one.

        ``_update_warnings`` runs from BOTH ``set_sensors`` and ``set_fans``, and each
        call rebuilds conditions from sensors AND fans. The two calls in a poll
        therefore see *different* inputs (new sensors + the previous poll's fans, then
        new sensors + new fans), so "reconcile is idempotent on a fixed present-set"
        does not by itself establish that a poll cannot double-log or emit a spurious
        recovery for the half of the state that has not been written yet.
        """
        state = AppState()
        diag = DiagnosticsService(state)
        diag.attach_alert_source(state)

        # Three polls with the fan stalled, driven the way PollingService drives it.
        for _ in range(3):
            state.set_sensors([fresh_sensor("S1")])
            state.set_fans([STALL])
        # Fourth poll: the fan recovers. set_sensors still sees the stalled fan.
        state.set_sensors([fresh_sensor("S1")])
        state.set_fans([SPINNING])
        # And two more quiet polls.
        for _ in range(2):
            state.set_sensors([fresh_sensor("S1")])
            state.set_fans([SPINNING])

        assert len([e for e in diag.events if "stall detected" in e.message]) == 1
        assert len([e for e in diag.events if "recovered after" in e.message]) == 1

    def test_a_sensor_condition_survives_a_fan_only_update(self, qtbot):
        """The spurious-recovery case the shared condition builder exists to prevent."""
        state = AppState()
        diag = DiagnosticsService(state)
        diag.attach_alert_source(state)
        state.set_sensors([stale_sensor("S1")])
        assert state.warning_count == 1

        state.set_fans([SPINNING])

        assert state.warning_count == 1, "a fan update must not recover a sensor alert"
        assert [e for e in diag.events if "recovered after" in e.message] == []

    def test_acknowledge_splits_the_two_counts(self, qtbot):
        """§2 + the ribbon/footer split: seen is not solved."""
        state = AppState()
        state.set_fans([STALL])
        assert (state.warning_count, state.unacknowledged_count) == (1, 1)

        state.acknowledge_all()

        assert state.warning_count == 1, "health: the fan is still stalled"
        assert state.unacknowledged_count == 0, "attention: the user has seen it"

    def test_acknowledge_one_key_leaves_the_others(self, qtbot):
        state = AppState()
        state.add_warning("warning", "sensor", "a", key="a")
        state.add_warning("error", "fan", "b", key="b")
        assert state.unacknowledged_count == 2

        state.acknowledge("a")

        assert state.unacknowledged_count == 1
        assert [r["_key"] for r in state.unacknowledged_warnings] == ["b"]

    def test_growing_age_does_not_churn_the_content_signal(self, qtbot):
        """A staleness message restates a ticking age. Treating that as a content
        change would rebuild the list every second and collapse any raw-detail
        expander the user had opened."""
        state = AppState()
        state.set_sensors([SensorReading(id="S1", label="S1", age_ms=5000)])
        changes: list[int] = []
        state.warnings_changed.connect(lambda: changes.append(1))

        state.set_sensors([SensorReading(id="S1", label="S1", age_ms=6000)])

        assert changes == []

    def test_stale_to_invalid_escalation_does_reach_the_view(self, qtbot):
        """...but a real escalation must, or the guard above would be hiding it."""
        state = AppState()
        state.set_sensors([SensorReading(id="S1", label="S1", age_ms=5000)])
        changes: list[int] = []
        state.warnings_changed.connect(lambda: changes.append(1))

        state.set_sensors([SensorReading(id="S1", label="S1", age_ms=20000)])  # INVALID

        assert changes, "stale → invalid is a genuine content change"

    def test_transitions_are_not_emitted_when_nothing_changed(self, qtbot):
        state = AppState()
        state.set_fans([STALL])
        emitted: list[list] = []
        state.alert_transitions.connect(emitted.append)

        state.set_fans([STALL])

        assert emitted == []


@pytest.mark.parametrize("attached", [True, False])
def test_observer_is_opt_in(qtbot, attached):
    """A DiagnosticsService that was never attached must not log alerts — the wiring
    is what makes it happen, so the wiring is what the test must exercise."""
    state = AppState()
    diag = DiagnosticsService(state)
    if attached:
        diag.attach_alert_source(state)

    state.set_fans([STALL])

    logged = [e for e in diag.events if "stall detected" in e.message]
    assert len(logged) == (1 if attached else 0)
