"""OpenFan calibration dialog — W-OFAN Run 2 (DEC-452 daemon, DEC-453 GUI).

The project's standing test rules apply: a call-site test asserts a
RELATIONSHIP whose right-hand side the defect cannot satisfy (DEC-324);
``isVisibleTo(parent)``, never ``isVisible()``, under offscreen; presence before
absence; ``.click()`` rather than the handler.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    CalPoint,
    Capabilities,
    ConnectionState,
    ControlCapability,
    FanReading,
    OpenFanCalibrationRun,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.daemon_features import daemon_supports, unsupported_feature_message
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.openfan_calibration_view import (
    DEMO_REFUSAL,
    advice_text,
    build_calibration_view,
    build_channel_options,
    parse_channel,
    restore_note,
    run_has_ended,
)
from control_ofc.ui.pages.diagnostics_workers import _OpenFanCalibrationWorker
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.pages.pwm_report_controller import RUN_ACTIVE_REASON
from control_ofc.ui.widgets import openfan_calibration_dialog as dlg_mod
from control_ofc.ui.widgets.openfan_calibration_dialog import OpenFanCalibrationDialog

# ── fixtures ─────────────────────────────────────────────────────────────────


def _fan(ch: int, rpm: int | None = 900, source: str = "openfan") -> FanReading:
    return FanReading(id=f"openfan:ch{ch:02d}", source=source, rpm=rpm)


def _names(fan_id: str) -> str:
    return {"openfan:ch03": "Rear Exhaust"}.get(fan_id, f"OpenFan CH{parse_channel(fan_id)}")


def _run(**kw) -> OpenFanCalibrationRun:
    base = OpenFanCalibrationRun(
        run_id="ofcal-1",
        fan_id="openfan:ch03",
        channel=3,
        state="complete",
        outcome="stall_and_restart_found",
        stall_duty_pct=12,
        restart_duty_pct=18,
        hysteresis_pct=6,
        min_rpm=310,
        max_rpm=1650,
        rise_limit_c=5.0,
        hold_ms=5000,
        points=[
            CalPoint(100, 1650, "descent", "spinning"),
            CalPoint(12, 0, "descent", "stopped"),
            CalPoint(18, 310, "ascent", "spinning"),
            CalPoint(100, 1600, "kick", "spinning"),
        ],
        original_pct=40,
        restore_outcome="restored",
        completed_unix_ms=180_000,
    )
    return replace(base, **kw)


def _running(**kw) -> OpenFanCalibrationRun:
    kw.setdefault("state", "running")
    kw.setdefault("outcome", None)
    kw.setdefault("phase", "descent")
    kw.setdefault("current_pct", 40)
    kw.setdefault("restore_outcome", "pending")
    kw.setdefault("completed_unix_ms", None)
    return _run(**kw)


# ── view-model: channel picker ───────────────────────────────────────────────


class TestChannelOptions:
    def test_every_openfan_channel_is_offered_including_a_stopped_one(self):
        """DEC-453: a 0 rpm channel is offered — a curve may be parking it."""
        fans = [_fan(3, 0), _fan(0, 850), _fan(1, None), _fan(9, 700, source="hwmon")]
        opts = build_channel_options(fans, _names)
        assert [o.channel for o in opts] == [0, 1, 3]  # sorted, hwmon excluded
        by_ch = {o.channel: o for o in opts}
        assert by_ch[3].text == "Rear Exhaust (CH3) — 0 rpm now"
        assert by_ch[0].text == "OpenFan CH0 — 850 rpm now"  # not "OpenFan CH0 (CH0)"
        assert by_ch[1].text.endswith("no RPM reading")

    def test_an_unparseable_id_is_skipped(self):
        assert parse_channel("openfan:ch07") == 7
        assert parse_channel("openfan:7") is None
        fans = [FanReading(id="openfan:weird", source="openfan", rpm=1)]
        assert build_channel_options(fans, _names) == []


# ── view-model: advice, end detection, notes ─────────────────────────────────


class TestAdvice:
    def test_the_advice_names_the_restart_duty_not_the_stall_duty(self):
        """DEC-453 rule 1. Values chosen apart so a swap is visible."""
        run = _run(stall_duty_pct=12, restart_duty_pct=26)
        text, tone = advice_text(run)
        assert f"{run.restart_duty_pct}%" in text
        assert f"{run.stall_duty_pct}%" not in text
        assert tone == "ok"

    def test_a_fan_that_never_stopped_gets_no_figure(self):
        text, _ = advice_text(_run(outcome="no_stall_down_to_0", stall_duty_pct=None))
        assert "never stopped" in text
        assert "%" not in text.replace("0 %", "")

    def test_did_not_restart_refuses_a_figure_and_says_how_high_it_went(self):
        run = _run(
            outcome="did_not_restart",
            restart_duty_pct=None,
            points=[CalPoint(14, 0, "descent", "stopped"), CalPoint(30, 0, "ascent", "stopped")],
            stall_duty_pct=14,
        )
        text, tone = advice_text(run)
        assert text.startswith("No minimum can be recommended")
        assert "by 30%" in text and "14%" in text
        assert tone == "warn"

    @pytest.mark.parametrize("outcome", ["aborted", "cancelled", "no_fan_detected", "future"])
    def test_other_outcomes_carry_no_advice(self, outcome):
        assert advice_text(_run(outcome=outcome)) == ("", "neutral")


class TestRunEnd:
    @pytest.mark.parametrize(("phase", "words"), [("kick", "Kicking"), ("restore", "Restoring")])
    def test_the_kick_and_restore_are_in_progress_and_cannot_be_cancelled(self, phase, words):
        """The wire's shape during the kick and the restore: `state` still
        `running` (the handler blanks the walk's own state until the terminal
        publish), no stamp, restore pending. A DELETE would answer 202 and stop
        nothing, so Cancel is not offered."""
        view = build_calibration_view(_running(phase=phase), channel_label="CH3")
        assert view.finished is False
        assert view.running is True
        assert view.can_cancel is False
        assert view.notes == []
        assert words in view.status_text

    @pytest.mark.parametrize("phase", ["descent", "ascent"])
    def test_the_walk_itself_can_be_cancelled(self, phase):
        assert build_calibration_view(_running(phase=phase), channel_label="c").can_cancel is True

    def test_the_end_is_the_stamp_the_contract_names(self):
        """docs/08 names `completed_unix_ms` as the end. No current daemon sends
        a terminal `state` without it; if one did, polling must go on rather
        than report a pending restore as unrecorded."""
        early = _run(phase="restore", restore_outcome="pending", completed_unix_ms=None)
        assert early.is_running is False  # precondition: `state` alone says done
        assert run_has_ended(early) is False
        assert build_calibration_view(early, channel_label="c").notes == []
        assert run_has_ended(_run()) is True

    def test_a_running_walk_can_be_cancelled(self):
        view = build_calibration_view(_running(), channel_label="CH3")
        assert view.can_cancel is True
        assert "40%" in view.status_text
        assert view.advice == ""


class TestNotesAndTokens:
    def test_restored_full_speed_explains_itself_only_when_it_moved_the_fan(self):
        moved = _run(restore_outcome="restored_full_speed", restore_failed=True, original_pct=40)
        note = restore_note(moved)
        assert "100 %" in note and "40%" in note
        assert restore_note(replace(moved, restore_failed=False, original_pct=100)) == ""

    def test_a_terminal_pending_restore_is_reported_unrecorded(self):
        run = _run(outcome="aborted", abort_reason="task_failed", restore_outcome="pending")
        assert "did not record" in restore_note(run)
        view = build_calibration_view(run, channel_label="CH3")
        assert "daemon defect" in view.status_text

    @pytest.mark.parametrize(
        ("token", "needle"),
        [
            ("write_failed", "Re-activate your profile"),
            ("skipped_thermal_force", "Thermal safety"),
            ("from_the_future", "From the future"),
        ],
    )
    def test_each_restore_outcome_is_worded_and_unknown_ones_rendered(self, token, needle):
        assert needle in restore_note(_run(restore_outcome=token, restore_failed=True))

    def test_the_rise_limit_is_the_reported_one_not_a_literal(self):
        run = _run(outcome="aborted", abort_reason="thermal_rise", rise_limit_c=7.5)
        assert "7.5 °C" in build_calibration_view(run, channel_label="CH3").status_text

    def test_unknown_tokens_are_rendered_not_dropped(self):
        run = _run(outcome="aborted", abort_reason="cosmic_ray")
        assert "Cosmic ray" in build_calibration_view(run, channel_label="c").status_text
        assert (
            "brand new"
            in build_calibration_view(
                _run(outcome="brand_new"), channel_label="c"
            ).status_text.lower()
        )
        running = build_calibration_view(_running(phase="warp"), channel_label="c")
        assert running.status_text.startswith("Warp at 40%")
        odd = build_calibration_view(
            _run(points=[CalPoint(50, 900, "sideways", "wobbling")]), channel_label="c"
        )
        assert (odd.rows[0].phase, odd.rows[0].observation) == ("Sideways", "Wobbling")

    def test_a_failed_kick_is_flagged(self):
        view = build_calibration_view(_run(restart_failed_at_full=True), channel_label="c")
        assert any("stuck or disconnected" in n for n in view.notes)


class TestCurve:
    def test_descent_is_falling_ascent_is_rising_and_the_kick_is_not_plotted(self):
        curve = build_calibration_view(_run(), channel_label="c").curve
        assert [(p.duty_pct, p.rpm) for p in curve.falling] == [(12, 0), (100, 1650)]
        assert [(p.duty_pct, p.rpm) for p in curve.rising] == [(18, 310)]
        assert curve.has_data is True


# ── dialog ───────────────────────────────────────────────────────────────────


def _dialog(qtbot, *, demo=False, channels=(3, 5)) -> OpenFanCalibrationDialog:
    opts = build_channel_options([_fan(c) for c in channels], _names)
    dialog = OpenFanCalibrationDialog(opts, demo=demo)
    qtbot.addWidget(dialog)
    return dialog


def _widgets(dialog):
    return (
        dialog.findChild(QPushButton, "OfanCal_Btn_start"),
        dialog.findChild(QCheckBox, "OfanCal_Check_notPump"),
        dialog.findChild(QComboBox, "OfanCal_Combo_channel"),
    )


class TestDialogConsent:
    def test_start_needs_the_pump_confirmation_and_sends_the_selected_channel(self, qtbot):
        dialog = _dialog(qtbot)
        start, check, combo = _widgets(dialog)
        assert start.isEnabled() is False
        combo.setCurrentIndex(1)
        check.setChecked(True)
        assert start.isEnabled() is True
        with qtbot.waitSignal(dialog.start_requested) as sig:
            start.click()
        assert sig.args == [dialog.selected_option().channel]
        assert sig.args == [5]
        dialog.stop_polling()

    def test_a_consent_does_not_carry_over_to_another_channel(self, qtbot):
        dialog = _dialog(qtbot)
        start, check, combo = _widgets(dialog)
        check.setChecked(True)
        assert "Rear Exhaust (CH3)" in check.text()
        combo.setCurrentIndex(1)
        assert check.isChecked() is False
        assert start.isEnabled() is False
        assert "OpenFan CH5" in check.text()

    def test_a_live_rpm_refresh_keeps_the_consent_but_a_moved_selection_drops_it(self, qtbot):
        dialog = _dialog(qtbot)
        _start, check, combo = _widgets(dialog)
        check.setChecked(True)
        dialog.set_channels(build_channel_options([_fan(3, 0), _fan(5, 400)], _names))
        assert check.isChecked() is True
        assert "0 rpm now" in combo.itemText(0)  # the text did refresh
        dialog.set_channels(build_channel_options([_fan(5, 400)], _names))  # CH3 vanished
        assert check.isChecked() is False

    def test_demo_mode_refuses_start(self, qtbot):
        dialog = _dialog(qtbot, demo=True)
        start, check, _ = _widgets(dialog)
        check.setChecked(True)  # even forced, Start stays refused
        dialog._refresh_start()
        assert start.isEnabled() is False
        assert check.isEnabled() is False
        blocked = dialog.findChild(QLabel, "OfanCal_Label_blocked")
        assert blocked.text() == DEMO_REFUSAL
        assert blocked.isVisibleTo(dialog) is True
        with qtbot.assertNotEmitted(dialog.start_requested):
            dialog._on_start()

    def test_a_same_set_refresh_rewrites_text_without_rebuilding(self, qtbot):
        """F6: every 1 Hz poll refreshes the RPM. Rebuilding the items each time
        resets an open popup's highlight, so only a changed set rebuilds."""
        dialog = _dialog(qtbot)
        _start, _check, combo = _widgets(dialog)
        removed = []
        combo.model().rowsRemoved.connect(lambda *_: removed.append(1))
        dialog.set_channels(build_channel_options([_fan(3, 111), _fan(5, 222)], _names))
        assert removed == []
        assert combo.itemText(0).endswith("111 rpm now")
        dialog.set_channels(build_channel_options([_fan(3, 111)], _names))
        assert removed  # presence: a changed set does rebuild
        assert combo.count() == 1

    def test_no_channel_means_no_start(self, qtbot):
        dialog = _dialog(qtbot, channels=())
        start, check, _ = _widgets(dialog)
        assert start.isEnabled() is False
        assert check.isVisibleTo(dialog) is False


def _started(qtbot, dialog) -> None:
    start, check, _ = _widgets(dialog)
    check.setChecked(True)
    start.click()


class TestDialogRun:
    def test_a_snapshot_of_someone_elses_run_is_ignored(self, qtbot):
        dialog = _dialog(qtbot)
        _started(qtbot, dialog)
        status = dialog.findChild(QLabel, "OfanCal_Label_status")
        dialog.apply_run(_running(channel=5, fan_id="openfan:ch05", run_id="other"))
        assert status.text() == "Starting…"
        dialog.apply_run(_running())  # the 202: ours, learns the run id
        assert "Walking down" in status.text()
        dialog.apply_run(_running(run_id="stale", current_pct=90))
        assert "40%" in status.text()
        dialog.stop_polling()

    def test_polling_continues_through_the_restore_and_ends_on_the_terminal_publish(self, qtbot):
        dialog = _dialog(qtbot)
        _started(qtbot, dialog)
        dialog.apply_run(_running())
        dialog.apply_run(_running(phase="restore"))
        assert dialog._timer.isActive() is True
        assert dialog.is_running is True
        dialog.apply_run(_run())
        assert dialog._timer.isActive() is False
        start, check, _ = _widgets(dialog)
        assert start.text() == "Run again"
        assert check.isChecked() is False  # a consent is for one run
        advice = dialog.findChild(QLabel, "OfanCal_Label_advice")
        assert advice.isVisibleTo(dialog) is True
        assert "18%" in advice.text()

    def test_a_vanished_run_is_terminal(self, qtbot):
        dialog = _dialog(qtbot)
        _started(qtbot, dialog)
        dialog.apply_run(None)
        assert dialog._timer.isActive() is False
        assert "no longer has this run" in dialog.findChild(QLabel, "OfanCal_Label_status").text()

    def test_an_error_after_the_202_keeps_tracking_the_run(self, qtbot):
        """F1: a poll timeout, or a cancel that lost the race with the run's own
        end (409), says nothing about the walk — polling carries on, the close
        question stays, and the result still arrives."""
        dialog = _dialog(qtbot)
        _started(qtbot, dialog)
        dialog.apply_run(_running())
        dialog.apply_error("error", "no OpenFan calibration is running")
        status = dialog.findChild(QLabel, "OfanCal_Label_status")
        assert "no OpenFan calibration is running" in status.text()
        assert dialog.is_running is True
        assert dialog._timer.isActive() is True
        dialog.apply_run(_run())
        assert dialog.is_running is False
        assert "18%" in dialog.findChild(QLabel, "OfanCal_Label_advice").text()

    def test_a_reply_after_the_end_does_not_repaint(self, qtbot):
        dialog = _dialog(qtbot)
        _started(qtbot, dialog)
        dialog.apply_run(_running())
        dialog.apply_run(_run())
        status = dialog.findChild(QLabel, "OfanCal_Label_status")
        final = status.text()
        dialog.apply_run(_running())  # a poll queued before the end
        assert status.text() == final

    def test_a_refusal_is_shown_in_the_daemons_words(self, qtbot):
        dialog = _dialog(qtbot)
        _started(qtbot, dialog)
        dialog.apply_error("unavailable", "No fresh CPU temperature reading.")
        assert dialog.findChild(QLabel, "OfanCal_Label_status").text() == (
            "No fresh CPU temperature reading."
        )
        assert dialog.is_running is False


class TestCloseMidRun:
    @pytest.mark.parametrize(
        ("answer", "cancels", "closes"),
        [
            (dlg_mod.CLOSE_STOP, True, True),
            (dlg_mod.CLOSE_KEEP, False, True),
            ("stay", False, False),
        ],
    )
    def test_closing_mid_run_asks(self, qtbot, monkeypatch, answer, cancels, closes):
        dialog = _dialog(qtbot)
        _started(qtbot, dialog)
        dialog.apply_run(_running())
        asked = []
        monkeypatch.setattr(dialog, "_ask_close_mid_run", lambda: asked.append(1) or answer)
        cancelled = []
        dialog.cancel_requested.connect(lambda: cancelled.append(1))
        rejected = []
        dialog.rejected.connect(lambda: rejected.append(1))
        dialog.findChild(QPushButton, "OfanCal_Btn_close").click()
        assert asked == [1]
        assert bool(cancelled) is cancels
        assert bool(rejected) is closes
        dialog.stop_polling()

    def test_closing_when_nothing_can_be_cancelled_does_not_ask(self, qtbot, monkeypatch):
        dialog = _dialog(qtbot)
        _started(qtbot, dialog)
        dialog.apply_run(_running(phase="kick"))
        monkeypatch.setattr(dialog, "_ask_close_mid_run", lambda: pytest.fail("asked"))
        rejected = []
        dialog.rejected.connect(lambda: rejected.append(1))
        dialog.findChild(QPushButton, "OfanCal_Btn_close").click()
        assert rejected == [1]


# ── worker ───────────────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, error: DaemonError | None = None) -> None:
        self.calls: list[tuple] = []
        self._error = error

    def start_openfan_calibration(self, channel, *, acknowledge_below_floor):
        self.calls.append(("start", channel, acknowledge_below_floor))
        if self._error:
            raise self._error
        return _running(channel=channel)

    def openfan_calibration_status(self):
        self.calls.append(("status",))
        return None

    def cancel_openfan_calibration(self):
        self.calls.append(("cancel",))
        return _running(state="cancelled")


class TestWorker:
    def test_start_poll_cancel_reach_the_client(self, qtbot):
        worker = _OpenFanCalibrationWorker("/nonexistent.sock")
        fake = _FakeClient()
        worker._client = fake
        got = []
        worker.run_updated.connect(got.append)
        worker.do_start(4)
        worker.do_poll()
        worker.do_cancel()
        assert fake.calls == [("start", 4, True), ("status",), ("cancel",)]
        assert [getattr(r, "channel", None) for r in got] == [4, None, 3]

    @pytest.mark.parametrize(
        ("error", "category"),
        [
            (DaemonError(code="validation_error", message="no CPU", retryable=True), "unavailable"),
            (DaemonError(code="thermal_abort", message="hot"), "unavailable"),
            (DaemonError(code="validation_error", message="busy slot"), "error"),
        ],
    )
    def test_a_safety_refusal_is_soft(self, qtbot, error, category):
        worker = _OpenFanCalibrationWorker("/nonexistent.sock")
        worker._client = _FakeClient(error)
        got = []
        worker.run_error.connect(lambda c, m: got.append((c, m)))
        worker.do_start(1)
        assert got == [(category, error.message)]


class TestClient:
    def test_the_requests_hit_dec_452s_routes_with_the_acknowledgement(self, monkeypatch):
        from control_ofc.api.client import DaemonClient

        client = DaemonClient(socket_path="/nonexistent.sock")
        seen = []
        monkeypatch.setattr(
            client, "_post", lambda path, json=None: seen.append((path, json)) or {}
        )
        monkeypatch.setattr(client, "_get", lambda path: seen.append((path, None)) or {})
        monkeypatch.setattr(client, "_delete", lambda path: seen.append((path, None)) or {})
        client.start_openfan_calibration(7, acknowledge_below_floor=True)
        client.openfan_calibration_status()
        client.cancel_openfan_calibration()
        assert seen == [
            ("/fans/openfan/7/calibration", {"acknowledge_below_floor": True}),
            ("/diagnostics/openfan-calibration", None),
            ("/diagnostics/openfan-calibration", None),
        ]
        client.close()

    def test_no_run_yet_is_none(self, monkeypatch):
        from control_ofc.api.client import DaemonClient

        client = DaemonClient(socket_path="/nonexistent.sock")

        def _404(_path):
            raise DaemonError(code="not_found", message="no run", status=404)

        monkeypatch.setattr(client, "_get", _404)
        assert client.openfan_calibration_status() is None
        client.close()


# ── Hardware page entry point ────────────────────────────────────────────────


def _caps(**control) -> Capabilities:
    return Capabilities(control=ControlCapability(**control))


def _page(qtbot, *, caps=None, fans=(3,), mode=None) -> HardwarePage:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    if mode is not None:
        state.set_mode(mode)
    if caps is not None:
        state.set_capabilities(caps)
    state.set_fans([_fan(c) for c in fans])
    page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state), client=None)
    qtbot.addWidget(page)
    page._sync_diagnostic_enablement()
    return page


class TestHardwarePageEntry:
    @pytest.mark.parametrize("advertised", [True, False])
    def test_the_button_tracks_the_wire_flag(self, qtbot, advertised):
        """Against the WIRE flag, not `daemon_supports` — deleting the registry
        entry makes both sides of that comparison falsy together (DEC-334)."""
        caps = _caps(openfan_calibration=advertised)
        page = _page(qtbot, caps=caps)
        button = page.findChild(QPushButton, "Hardware_Btn_openfanCalibration")
        assert button is not None
        assert button.isEnabled() == caps.control.openfan_calibration
        if not advertised:
            assert button.toolTip() == unsupported_feature_message("openfan_calibration")

    def test_the_feature_id_resolves_through_the_registry(self):
        assert daemon_supports("openfan_calibration", _caps(openfan_calibration=True)) is True
        assert daemon_supports("openfan_calibration", _caps(openfan_calibration=False)) is False

    def test_no_openfan_channel_disables_it(self, qtbot):
        page = _page(qtbot, caps=_caps(openfan_calibration=True), fans=())
        button = page.findChild(QPushButton, "Hardware_Btn_openfanCalibration")
        assert button.isEnabled() is False
        assert "No OpenFan channel" in button.toolTip()

    def test_a_running_report_stands_it_down(self, qtbot):
        page = _page(qtbot, caps=_caps(openfan_calibration=True))
        button = page.findChild(QPushButton, "Hardware_Btn_openfanCalibration")
        assert button.isEnabled() is True  # presence before absence
        page._on_report_active(True)
        assert button.isEnabled() is False
        assert button.toolTip() == RUN_ACTIVE_REASON

    def test_demo_mode_opens_the_dialog_with_start_refused(self, qtbot, monkeypatch):
        page = _page(qtbot, mode=OperationMode.DEMO)
        button = page.findChild(QPushButton, "Hardware_Btn_openfanCalibration")
        assert button.isEnabled() is True
        opened = []

        def fake_exec(dialog):
            opened.append((dialog._demo, [o.channel for o in dialog._options]))
            return 0

        monkeypatch.setattr(OpenFanCalibrationDialog, "exec", fake_exec)
        button.click()
        assert opened == [(True, [3])]
        assert page._ofancal_dialog is None  # released after exec

    def test_the_intro_no_longer_promises_what_calibration_breaks(self, qtbot):
        page = _page(qtbot)
        intro = page.findChild(QLabel, "Hardware_Label_diagnosticsIntro")
        assert "OpenFan calibration" in intro.text()
        assert "0 %" in intro.text()


class _PageFakeClient:
    """Stands in for the worker's DaemonClient; called on the worker thread."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def start_openfan_calibration(self, channel, *, acknowledge_below_floor):
        self.calls.append(("start", channel, acknowledge_below_floor))
        return _running(channel=channel, fan_id=f"openfan:ch{channel:02d}")

    def openfan_calibration_status(self):
        self.calls.append(("status",))
        return _run()

    def cancel_openfan_calibration(self):
        return _running()

    def close(self) -> None:
        pass


class _SocketOnly:
    socket_path = "/nonexistent/control-ofc.sock"


class TestHardwarePageWiring:
    """F3: the page's own connections, not the dialog's handlers called directly."""

    def test_the_picker_follows_the_poll_while_the_dialog_is_open(self, qtbot, monkeypatch):
        page = _page(qtbot, mode=OperationMode.DEMO, fans=(3,))
        seen = []

        def fake_exec(dialog):
            combo = dialog.findChild(QComboBox, "OfanCal_Combo_channel")
            before = combo.itemText(0)
            page._state.set_fans([_fan(3, 0)])
            seen.append((before, combo.itemText(0)))
            return 0

        monkeypatch.setattr(OpenFanCalibrationDialog, "exec", fake_exec)
        page.findChild(QPushButton, "Hardware_Btn_openfanCalibration").click()
        assert seen == [("OpenFan CH3 — 900 rpm now", "OpenFan CH3 — 0 rpm now")]
        # And the connection is released with the dialog.
        page._state.set_fans([_fan(3, 5)])

    def test_start_and_the_result_travel_through_the_page_and_worker(self, qtbot, monkeypatch):
        fake = _PageFakeClient()
        monkeypatch.setattr(_OpenFanCalibrationWorker, "_ensure_client", lambda self: fake)
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_capabilities(_caps(openfan_calibration=True))
        state.set_fans([_fan(3)])
        page = HardwarePage(
            state=state, diagnostics_service=DiagnosticsService(state), client=_SocketOnly()
        )
        qtbot.addWidget(page)
        page._sync_diagnostic_enablement()
        seen = []

        def fake_exec(dialog):
            _started(qtbot, dialog)
            advice = dialog.findChild(QLabel, "OfanCal_Label_advice")
            qtbot.waitUntil(lambda: "18%" in advice.text(), timeout=5000)
            seen.append(dialog.is_running)
            return 0

        monkeypatch.setattr(OpenFanCalibrationDialog, "exec", fake_exec)
        try:
            page.findChild(QPushButton, "Hardware_Btn_openfanCalibration").click()
        finally:
            page.cleanup()
        assert fake.calls[0] == ("start", 3, True)
        assert ("status",) in fake.calls
        assert seen == [False]

    def test_a_theme_switch_reaches_the_open_dialog(self, qtbot, monkeypatch):
        page = _page(qtbot, mode=OperationMode.DEMO)
        forwarded = []
        monkeypatch.setattr(
            OpenFanCalibrationDialog, "set_theme", lambda self, tokens: forwarded.append(tokens)
        )

        def fake_exec(dialog):
            page.set_theme("TOKENS")
            return 0

        monkeypatch.setattr(OpenFanCalibrationDialog, "exec", fake_exec)
        page.findChild(QPushButton, "Hardware_Btn_openfanCalibration").click()
        assert forwarded == ["TOKENS"]
