"""PTA-v (`U11`): a failed request mid-run must not end the dialog's view of it.

Both diagnostic dialogs used to stop polling and disable Cancel on ANY error, so
one timed-out poll left the user unable to cancel a sweep the daemon was still
running. Now a run the daemon has shown as ours and running keeps its poll timer
and its Cancel button; the wording escalates from "connection problem" to "lost
contact" after `LOST_CONTACT_AFTER` consecutive failures; and a refused start
still ends the run as before.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import CharacterizationRun, ControlPathRun, ControlPathStatus
from control_ofc.services.run_contact_view import LOST_CONTACT_AFTER, live_run_error_text
from control_ofc.ui.widgets.control_path_dialog import ControlPathDiscoveryDialog
from control_ofc.ui.widgets.pwm_characterization_dialog import PwmCharacterizationDialog


def _char(qtbot):
    dlg = PwmCharacterizationDialog("h1", "AIO_PUMP", is_pump=True)
    qtbot.addWidget(dlg)
    return dlg


def _char_snapshot(state: str):
    return CharacterizationRun(run_id="r1", header_id="h1", state=state)


def _path(qtbot):
    dlg = ControlPathDiscoveryDialog("h1", "AIO_PUMP", is_pump=True)
    qtbot.addWidget(dlg)
    return dlg


def _path_snapshot(state: str):
    return ControlPathStatus(run=ControlPathRun(run_id="r1", header_id="h1", state=state))


DIALOGS = [
    pytest.param(_char, _char_snapshot, id="characterisation"),
    pytest.param(_path, _path_snapshot, id="control-path"),
]


def _live(qtbot, make, snapshot):
    dlg = make(qtbot)
    dlg._start_btn.click()
    dlg.apply_run(snapshot("running"))
    assert dlg._timer.isActive(), "precondition: the run is being polled"
    return dlg


@pytest.mark.parametrize(("make", "snapshot"), DIALOGS)
class TestAFailureDuringALiveRun:
    def test_keeps_polling_and_cancel(self, qtbot, make, snapshot):
        dlg = _live(qtbot, make, snapshot)
        dlg.apply_error("unavailable", "The daemon did not answer the status poll in time.")
        assert dlg._timer.isActive(), "one failed poll must not stop watching a live run"
        assert dlg._cancel_btn.isEnabled(), "the user must still be able to cancel"
        assert not dlg._start_btn.isEnabled(), "a second start would race the live run"
        assert dlg._status_lbl.text().startswith("Connection problem")
        assert "did not answer the status poll" in dlg._status_lbl.text()

    def test_a_failed_cancel_gives_cancel_back(self, qtbot, make, snapshot):
        dlg = _live(qtbot, make, snapshot)
        dlg._cancel_btn.click()
        assert not dlg._cancel_btn.isEnabled()
        dlg.apply_error("unavailable", "Connection lost during cancellation")
        assert dlg._cancel_btn.isEnabled(), "a cancel that failed must be retryable"
        assert dlg._timer.isActive()

    def test_escalates_to_lost_contact_after_consecutive_failures(self, qtbot, make, snapshot):
        dlg = _live(qtbot, make, snapshot)
        for _ in range(LOST_CONTACT_AFTER - 1):
            dlg.apply_error("unavailable", "Daemon unavailable during status poll")
        assert dlg._status_lbl.text().startswith("Connection problem")
        dlg.apply_error("unavailable", "Daemon unavailable during status poll")
        assert dlg._status_lbl.text().startswith("Lost contact")
        assert dlg._timer.isActive(), "lost contact still keeps trying"
        assert dlg._cancel_btn.isEnabled()

    def test_a_snapshot_between_failures_resets_the_count(self, qtbot, make, snapshot):
        dlg = _live(qtbot, make, snapshot)
        for _ in range(LOST_CONTACT_AFTER - 1):
            dlg.apply_error("unavailable", "busy")
        dlg.apply_run(snapshot("running"))
        dlg.apply_error("unavailable", "busy")
        assert dlg._status_lbl.text().startswith("Connection problem"), (
            "failures are counted consecutively, not over the run"
        )

    def test_a_terminal_snapshot_after_failures_still_ends_the_run(self, qtbot, make, snapshot):
        dlg = _live(qtbot, make, snapshot)
        dlg.apply_error("unavailable", "busy")
        dlg.apply_run(snapshot("complete"))
        assert not dlg._timer.isActive()
        assert not dlg._cancel_btn.isEnabled()
        assert dlg._start_btn.isEnabled()

    def test_a_vanished_run_after_failures_still_ends_it(self, qtbot, make, snapshot):
        dlg = _live(qtbot, make, snapshot)
        dlg.apply_error("unavailable", "busy")
        dlg.apply_run(None)
        assert not dlg._timer.isActive()
        assert "no longer has this run" in dlg._status_lbl.text()
        dlg.apply_error("unavailable", "busy")
        assert dlg._start_btn.isEnabled(), "an error after the run ended does not revive it"


@pytest.mark.parametrize(("make", "snapshot"), DIALOGS)
class TestAFailureBeforeTheRunIsLive:
    def test_a_refused_start_ends_the_run(self, qtbot, make, snapshot):
        del snapshot
        dlg = make(qtbot)
        dlg._start_btn.click()
        dlg.apply_error("unavailable", "Cannot run while hot")
        assert not dlg._timer.isActive()
        assert not dlg._cancel_btn.isEnabled()
        assert dlg._start_btn.isEnabled()
        assert dlg._status_lbl.text() == "Cannot run while hot"

    def test_an_error_after_the_run_finished_does_not_reopen_it(self, qtbot, make, snapshot):
        dlg = _live(qtbot, make, snapshot)
        dlg.apply_run(snapshot("complete"))
        dlg.apply_error("error", "boom")
        assert not dlg._timer.isActive()
        assert not dlg._cancel_btn.isEnabled()


def test_the_wording_escalates_at_the_threshold_and_keeps_the_cause():
    assert live_run_error_text(LOST_CONTACT_AFTER - 1, "why").startswith("Connection problem")
    lost = live_run_error_text(LOST_CONTACT_AFTER, "why")
    assert lost.startswith("Lost contact")
    assert lost.endswith("Last error: why")
