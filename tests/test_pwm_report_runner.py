"""The PWM Test Report runner: a pure state machine over the daemon's own
diagnostics (DEC-404 Stage 4).

These drive :class:`ReportRunner` with scripted daemon answers and assert on
the calls it asks for and the document it writes. Nothing here needs Qt, a
worker or a daemon — which is the point of keeping the runner pure.
"""

from __future__ import annotations

import pytest

from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report.catalog import (
    SWEEP_POINTS_PCT,
    TEST_PAIRING,
    TEST_PROBE,
    TEST_SWEEP,
    TEST_VERIFY,
)
from control_ofc.services.pwm_report.runner import (
    CALL_CANCEL,
    CALL_POLL,
    CALL_PREFLIGHT,
    CALL_SNAPSHOT,
    CALL_START_PROBE,
    CALL_START_SWEEP,
    CALL_VERIFY,
    HANDBACK_WAIT_S,
    MAX_POLL_FAILURES,
    REASON_CANCELLED,
    REASON_CONNECTION_LOST,
    REASON_NOT_CONFIRMED,
    build_plan,
    start_refusals,
)
from tests.pwm_report_fixtures import (
    CPU,
    SYS,
    UNAVAILABLE,
    bundle,
    channel,
    fan,
    header,
    ok,
    one,
    probe_run,
    refused,
    runner_for,
    sweep_run,
    verify_body,
)

READY = {"verdict": "ready", "checks": [], "blocking": []}


def _baseline() -> dict:
    return bundle(fans=[fan(CPU), fan(SYS)], headers=[header(CPU), header(SYS)])


def _start(runner, now: float = 0.0):
    snap = one(runner.start(now), CALL_SNAPSHOT)
    assert snap.args["tag"] == "baseline"
    return runner.on_outcome(snap.req_id, ok(_baseline()), now)


def _through_preflight(runner, calls, now: float):
    pre = one(calls, CALL_PREFLIGHT)
    return runner.on_outcome(pre.req_id, ok(READY), now)


# ── Plan and refusals ────────────────────────────────────────────────────────


def test_plan_orders_headers_by_stable_id_and_tests_by_the_fixed_order():
    plan = build_plan({SYS: {TEST_PROBE, TEST_VERIFY}, CPU: {TEST_SWEEP, TEST_PAIRING}})
    assert [(p.channel_id, p.test) for p in plan] == [
        (CPU, TEST_PAIRING),
        (CPU, TEST_SWEEP),
        (SYS, TEST_VERIFY),
        (SYS, TEST_PROBE),
    ]
    assert [p.step_id for p in plan] == ["s1", "s2", "s3", "s4"]


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"demo_mode": True}, "Demo mode"),
        ({"connected": False}, "not connected"),
        ({"thermal_state": "emergency"}, "thermal protection"),
        ({"diagnostic_running": True}, "Another diagnostic"),
        ({"session_recording": True}, "validation session"),
        ({"local_verify_running": True}, "System State page"),
    ],
)
def test_each_start_refusal_names_its_state(kwargs, needle):
    base = {
        "connected": True,
        "thermal_state": "normal",
        "diagnostic_running": False,
        "session_recording": False,
        "local_verify_running": False,
        "demo_mode": False,
    }
    assert start_refusals(**base) == []
    reasons = start_refusals(**{**base, **kwargs})
    assert len(reasons) == 1 and needle in reasons[0]


# ── The happy path ───────────────────────────────────────────────────────────


def test_verify_then_sweep_runs_to_a_complete_report():
    runner = runner_for({CPU: {TEST_VERIFY, TEST_SWEEP}}, [channel(CPU)])
    calls = _start(runner)
    assert runner.doc["environment"]["daemon"]["kernel_release"] == "6.18.2-1-cachyos"
    assert runner.doc["environment"]["board"]["bios_date"] == "08/14/2025"

    verify = one(_through_preflight(runner, calls, 0.0), CALL_VERIFY)
    assert verify.header_id == CPU
    assert runner.on_outcome(verify.req_id, ok(verify_body()), 1.0) == []
    assert runner.step_phase == "handback"
    # The hand-back wait holds the next test off (DEC-382's next-tick hand-back).
    assert runner.on_tick(1.0 + HANDBACK_WAIT_S - 0.01) == []
    calls = runner.on_tick(1.0 + HANDBACK_WAIT_S)

    start = one(_through_preflight(runner, calls, 5.0), CALL_START_SWEEP)
    # S4-1: the points and the walk are asked for; the settle is NOT — the
    # daemon owns the timing and the pump floor.
    assert dict(start.args) == {
        "points_pct": list(SWEEP_POINTS_PCT),
        "bidirectional": True,
        "stability_seconds": 20,
    }
    assert "settle_seconds" not in start.args
    assert runner.on_outcome(start.req_id, ok(sweep_run("running")), 5.0) == []
    poll = one(runner.on_tick(6.0), CALL_POLL)
    assert poll.args["slot"] == "characterization"
    assert runner.on_outcome(poll.req_id, ok(sweep_run("running")), 6.0) == []
    poll = one(runner.on_tick(7.0), CALL_POLL)
    assert runner.on_outcome(poll.req_id, ok(sweep_run("complete")), 7.0) == []

    final = one(runner.on_tick(7.0 + HANDBACK_WAIT_S), CALL_SNAPSHOT)
    assert final.args["tag"] == "final"
    runner.on_outcome(final.req_id, ok(_baseline()), 11.0)

    doc = runner.doc
    assert runner.finished and doc["state"] == d.STATE_COMPLETE
    assert [s["status"] for s in doc["steps"]] == [d.STEP_COMPLETE, d.STEP_COMPLETE]
    # The daemon's answers are kept verbatim.
    assert doc["steps"][0]["result"] == verify_body()
    assert doc["steps"][1]["result"]["run_id"] == "char-1"
    assert doc["steps"][1]["request"]["points_pct"] == list(SWEEP_POINTS_PCT)
    assert doc["findings"], "a finished run always has findings"


def test_polls_are_paced_at_one_per_second_and_never_stacked():
    runner = runner_for({CPU: {TEST_SWEEP}}, [channel(CPU)])
    start = one(_through_preflight(runner, _start(runner), 0.0), CALL_START_SWEEP)
    runner.on_outcome(start.req_id, ok(sweep_run("running")), 0.0)
    assert runner.on_tick(0.5) == []
    poll = one(runner.on_tick(1.0), CALL_POLL)
    # A second tick while that poll is in flight asks for nothing.
    assert runner.on_tick(2.5) == []
    runner.on_outcome(poll.req_id, ok(sweep_run("running")), 2.6)
    one(runner.on_tick(3.6), CALL_POLL)


# ── Preflight, consent and refusals ─────────────────────────────────────────


def test_a_blocked_preflight_is_not_tested_with_the_reason_and_starts_nothing():
    runner = runner_for({CPU: {TEST_SWEEP}}, [channel(CPU)])
    pre = one(_start(runner), CALL_PREFLIGHT)
    blocked = {
        "verdict": "blocked",
        "checks": [{"check_id": "thermal", "state": "fail", "detail": "too hot"}],
        "blocking": ["thermal"],
    }
    calls = runner.on_outcome(pre.req_id, ok(blocked), 0.0)
    assert one(calls, CALL_SNAPSHOT).args["tag"] == "final"
    step = runner.doc["steps"][0]
    assert step["status"] == d.STEP_NOT_TESTED
    assert "preflight blocked" in step["reason"]
    assert step["preflight"] == blocked  # the daemon's rows, verbatim


def test_a_probe_without_its_confirmation_is_never_started():
    runner = runner_for({SYS: {TEST_PROBE}}, [channel(SYS)], consent=frozenset())
    calls = _start(runner)
    # Straight to the final snapshot: no preflight, no start.
    assert one(calls, CALL_SNAPSHOT).args["tag"] == "final"
    step = runner.doc["steps"][0]
    assert step["status"] == d.STEP_NOT_TESTED
    assert step["reason"] == REASON_NOT_CONFIRMED


def test_a_confirmed_probe_sends_the_acknowledgement():
    runner = runner_for({SYS: {TEST_PROBE}}, [channel(SYS)], consent={SYS})
    start = one(_through_preflight(runner, _start(runner), 0.0), CALL_START_PROBE)
    assert dict(start.args) == {"acknowledge_below_floor": True}
    runner.on_outcome(start.req_id, ok(probe_run(None, state="running")), 0.0)
    poll = one(runner.on_tick(1.0), CALL_POLL)
    assert poll.args["slot"] == "stall_probe"


def test_a_busy_slot_is_not_tested_and_moves_straight_on():
    runner = runner_for({CPU: {TEST_SWEEP}, SYS: {TEST_VERIFY}}, [channel(CPU), channel(SYS)])
    start = one(_through_preflight(runner, _start(runner), 0.0), CALL_START_SWEEP)
    calls = runner.on_outcome(start.req_id, refused(409, "conflict", "busy"), 0.0)
    # No hand-back wait: the daemon wrote nothing.
    assert one(calls, CALL_PREFLIGHT).header_id == SYS
    step = runner.doc["steps"][0]
    assert step["status"] == d.STEP_NOT_TESTED
    assert "already running" in step["reason"]
    assert step["error"]["status"] == 409


def test_a_thermal_refusal_is_recorded_as_protection_not_failure():
    runner = runner_for({CPU: {TEST_VERIFY}}, [channel(CPU)])
    verify = one(_through_preflight(runner, _start(runner), 0.0), CALL_VERIFY)
    runner.on_outcome(verify.req_id, refused(409, "thermal_abort", "85 C"), 0.0)
    step = runner.doc["steps"][0]
    assert step["status"] == d.STEP_NOT_TESTED


def test_a_retryable_validation_refusal_is_protection_too():
    runner = runner_for({CPU: {TEST_VERIFY}}, [channel(CPU)])
    verify = one(_through_preflight(runner, _start(runner), 0.0), CALL_VERIFY)
    runner.on_outcome(
        verify.req_id, refused(400, "validation_error", "forcing", retryable=True), 0.0
    )
    step = runner.doc["steps"][0]
    assert step["status"] == d.STEP_NOT_TESTED
    assert "declined for safety" in step["reason"]


def test_a_verify_timeout_waits_for_the_handback_before_moving_on():
    runner = runner_for({CPU: {TEST_VERIFY}}, [channel(CPU)])
    verify = one(_through_preflight(runner, _start(runner), 0.0), CALL_VERIFY)
    assert runner.on_outcome(verify.req_id, UNAVAILABLE, 12.0) == []
    assert runner.step_phase == "handback"
    assert runner.doc["steps"][0]["status"] == d.STEP_ERROR


# ── Cancel, thermal, connection ─────────────────────────────────────────────


def _running_sweep(selection=None):
    runner = runner_for(selection or {CPU: {TEST_SWEEP}, SYS: {TEST_VERIFY}}, [channel(CPU)])
    start = one(_through_preflight(runner, _start(runner), 0.0), CALL_START_SWEEP)
    runner.on_outcome(start.req_id, ok(sweep_run("running")), 0.0)
    return runner


def test_cancel_sends_one_delete_and_marks_the_rest_cancelled_by_you():
    runner = _running_sweep()
    cancel = one(runner.request_cancel(1.0), CALL_CANCEL)
    assert cancel.args["slot"] == "characterization"
    # Asking twice does not send a second DELETE.
    assert runner.request_cancel(1.1) == []
    poll = one(runner.on_tick(2.0), CALL_POLL)
    runner.on_outcome(poll.req_id, ok(sweep_run("cancelled")), 2.0)
    final = one(runner.on_tick(2.0 + HANDBACK_WAIT_S), CALL_SNAPSHOT)
    runner.on_outcome(final.req_id, ok(_baseline()), 6.0)
    doc = runner.doc
    assert doc["state"] == d.STATE_CANCELLED
    assert doc["steps"][0]["status"] == d.STEP_CANCELLED
    assert doc["steps"][1] == {**doc["steps"][1], "status": d.STEP_NOT_TESTED}
    assert doc["steps"][1]["reason"] == REASON_CANCELLED


def test_a_cancel_during_a_start_is_sent_when_the_run_appears():
    runner = runner_for({CPU: {TEST_SWEEP}}, [channel(CPU)])
    start = one(_through_preflight(runner, _start(runner), 0.0), CALL_START_SWEEP)
    # The start is still in flight: nothing to DELETE yet.
    assert runner.request_cancel(0.5) == []
    calls = runner.on_outcome(start.req_id, ok(sweep_run("running")), 0.6)
    assert one(calls, CALL_CANCEL).args["slot"] == "characterization"


def test_thermal_protection_stops_the_run_as_aborted():
    runner = _running_sweep()
    assert runner.observe(1.0, connected=True, thermal_state="normal") == []
    cancel = one(runner.observe(2.0, connected=True, thermal_state="emergency"), CALL_CANCEL)
    assert cancel.args["slot"] == "characterization"
    poll = one(runner.on_tick(3.0), CALL_POLL)
    runner.on_outcome(poll.req_id, ok(sweep_run("aborted")), 3.0)
    final = one(runner.on_tick(3.0 + HANDBACK_WAIT_S), CALL_SNAPSHOT)
    runner.on_outcome(final.req_id, ok(_baseline()), 7.0)
    doc = runner.doc
    assert doc["state"] == d.STATE_ABORTED
    assert "emergency" in doc["state_reason"]
    assert doc["steps"][1]["status"] == d.STEP_NOT_TESTED
    assert "thermal protection" in doc["steps"][1]["reason"]


def test_losing_the_daemon_interrupts_and_still_asks_for_the_final_state():
    runner = _running_sweep()
    final = one(runner.observe(1.0, connected=False, thermal_state="normal"), CALL_SNAPSHOT)
    assert final.args["tag"] == "final"
    assert runner.doc["steps"][0]["status"] == d.STEP_INTERRUPTED
    assert runner.doc["steps"][1]["reason"] == REASON_CONNECTION_LOST
    runner.on_outcome(final.req_id, UNAVAILABLE, 1.5)
    doc = runner.doc
    assert doc["state"] == d.STATE_INTERRUPTED
    assert doc["snapshots"]["final"] is None
    assert any(f["rule"] == "final.unavailable" for f in doc["findings"])


def test_repeated_poll_failures_are_treated_as_a_lost_daemon():
    runner = _running_sweep()
    now = 1.0
    calls: list = []
    for _ in range(MAX_POLL_FAILURES):
        poll = one(runner.on_tick(now), CALL_POLL)
        calls = runner.on_outcome(poll.req_id, UNAVAILABLE, now)
        now += 1.0
    assert one(calls, CALL_SNAPSHOT).args["tag"] == "final"
    assert runner.doc["steps"][0]["status"] == d.STEP_INTERRUPTED


def test_another_run_in_the_slot_interrupts_the_step():
    runner = _running_sweep({CPU: {TEST_SWEEP}})
    poll = one(runner.on_tick(1.0), CALL_POLL)
    runner.on_outcome(poll.req_id, ok(sweep_run("complete", run_id="someone-else")), 1.0)
    step = runner.doc["steps"][0]
    assert step["status"] == d.STEP_INTERRUPTED
    assert step["result"] is None  # someone else's result is never recorded as ours


def test_an_unrecognised_terminal_state_is_kept_verbatim():
    runner = _running_sweep({CPU: {TEST_SWEEP}})
    poll = one(runner.on_tick(1.0), CALL_POLL)
    runner.on_outcome(poll.req_id, ok(sweep_run("paused_by_future_daemon")), 1.0)
    assert runner.doc["steps"][0]["status"] == "paused_by_future_daemon"


def test_a_stale_answer_is_ignored():
    runner = _running_sweep({CPU: {TEST_SWEEP}})
    before = runner.revision
    assert runner.on_outcome(9999, ok(sweep_run("complete")), 1.0) == []
    assert runner.revision == before


def test_a_failed_baseline_tests_nothing():
    runner = runner_for({CPU: {TEST_VERIFY}}, [channel(CPU)])
    snap = one(runner.start(0.0), CALL_SNAPSHOT)
    assert runner.on_outcome(snap.req_id, UNAVAILABLE, 0.0) == []
    doc = runner.doc
    assert runner.finished and doc["state"] == d.STATE_INTERRUPTED
    assert doc["steps"][0]["status"] == d.STEP_NOT_TESTED


def test_abandon_saves_an_interrupted_record_without_a_final_read():
    runner = _running_sweep()
    runner.abandon(5.0)
    doc = runner.doc
    assert runner.finished and doc["state"] == d.STATE_INTERRUPTED
    assert doc["steps"][0]["status"] == d.STEP_INTERRUPTED
    assert doc["steps"][1]["status"] == d.STEP_NOT_TESTED
    assert doc["snapshots"]["final"] is None
    assert "not read" in doc["final_note"]
    assert runner.active_slot is None


def test_active_slot_names_the_diagnostic_to_cancel_on_exit():
    runner = _running_sweep()
    assert runner.active_slot == "characterization"
