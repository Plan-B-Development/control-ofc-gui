"""PWM Test Report — the window, its controller and the pages it stands down
(DEC-404 Stage 4).

Driven by ``.click()`` through a fake worker that plays the daemon, with a
hand-advanced clock so hand-back waits and poll cadence cost no real time. The
worker still runs on the controller's own QThread, so every answer crosses the
same queued connection production uses.

Every page/window a helper returns is BOUND and used in an assertion (DASH-h):
``qtbot.addWidget`` does not keep a widget alive, and a dropped one takes its
signal connections with it.
"""

from __future__ import annotations

import json
import threading

import pytest
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QCheckBox, QLabel, QMessageBox, QPushButton

from control_ofc.api.models import (
    ConnectionState,
    DaemonStatus,
    OperationMode,
    parse_capabilities,
    parse_fans,
    parse_hwmon_headers,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report.runner import (
    HANDBACK_WAIT_S,
    REASON_WINDOW_CLOSED,
    RUN_ACTIVE_REASON,
    CallOutcome,
)
from control_ofc.ui.pages.pwm_report_controller import PwmReportController
from control_ofc.ui.widgets.pwm_report_window import (
    PAGE_HISTORY,
    PAGE_REPORT,
    PAGE_REVIEW,
    PAGE_RUN,
    PAGE_SCOPE,
    PAGE_SETUP,
    PwmReportWindow,
)
from tests.pwm_report_fixtures import (
    CPU,
    SYS,
    bundle,
    fan,
    header,
    probe_run,
    sweep_run,
    verify_body,
)

READY = {"verdict": "ready", "checks": [], "blocking": []}


def _caps(**flags):
    control = {
        "control_path_discovery": True,
        "pwm_characterization": True,
        "pwm_behaviour_characterization": True,
        "stall_probe": True,
        "duty_reconciliation": True,
        "header_roles": True,
        "autonomous_control": True,
        **flags,
    }
    return parse_capabilities({"daemon_version": "2.54.0", "api_version": 1, "control": control})


def _state(**cap_flags) -> AppState:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    s.set_capabilities(_caps(**cap_flags))
    s.set_hwmon_headers(
        parse_hwmon_headers(
            {"headers": [header(CPU, role="cpu_fan"), header(SYS, role="radiator_fan")]}
        )
    )
    s.set_status(DaemonStatus(thermal_state="normal"))
    s.set_fans(parse_fans({"fans": [fan(CPU), fan(SYS)]}))
    return s


class FakeDaemon:
    """Answers the worker's calls the way a well-behaved daemon would."""

    def __init__(self, *, polls_until_done: int = 1) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()
        self._polls: dict[str, int] = {}
        self._cancelled: set[str] = set()
        self._running: dict[str, dict] = {}
        self.polls_until_done = polls_until_done
        #: Fans the SECOND snapshot (the final state) reports; None = unchanged.
        self.final_fans: list[dict] | None = None
        self._snapshots = 0

    def answer(self, kind: str, payload: dict) -> CallOutcome:
        with self._lock:
            self.calls.append((kind, payload))
        args = payload.get("args") or {}
        if kind == "snapshot":
            self._snapshots += 1
            fans = [fan(CPU), fan(SYS)]
            if self._snapshots > 1 and self.final_fans is not None:
                fans = self.final_fans
            return CallOutcome(
                ok=True,
                status=200,
                body=bundle(fans=fans, headers=[header(CPU), header(SYS)]),
            )
        if kind == "preflight":
            return CallOutcome(ok=True, status=200, body=READY)
        if kind == "verify":
            return CallOutcome(ok=True, status=200, body=verify_body())
        if kind in ("start_sweep", "start_pairing", "start_probe"):
            slot = {
                "start_sweep": "characterization",
                "start_pairing": "control_path",
                "start_probe": "stall_probe",
            }[kind]
            body = (
                probe_run(None, state="running") if slot == "stall_probe" else sweep_run("running")
            )
            self._running[slot] = body
            self._polls[slot] = 0
            return CallOutcome(ok=True, status=202, body=body)
        if kind == "poll":
            slot = args["slot"]
            self._polls[slot] = self._polls.get(slot, 0) + 1
            if slot in self._cancelled:
                body = (
                    sweep_run("cancelled")
                    if slot != "stall_probe"
                    else probe_run("cancelled", state="cancelled")
                )
            elif self._polls[slot] >= self.polls_until_done:
                body = sweep_run("complete") if slot != "stall_probe" else probe_run()
            else:
                body = self._running[slot]
            if slot == "control_path":
                body = {"run": body, "records": []}
            return CallOutcome(ok=True, status=200, body=body)
        if kind == "cancel":
            self._cancelled.add(args["slot"])
            return CallOutcome(ok=True, status=202, body=self._running.get(args["slot"]))
        if kind == "reapply_profile":
            return CallOutcome(ok=True, status=200, body={"activated": True})
        return CallOutcome(ok=False, category="error", error_message=f"unexpected {kind}")

    def kinds(self) -> list[str]:
        with self._lock:
            return [k for k, _ in self.calls]


def _worker_factory(daemon: FakeDaemon):
    class _FakeWorker(QObject):
        call_done = Signal(int, object)

        def __init__(self, _socket_path: str) -> None:
            super().__init__()

        @Slot(int, str, object)
        def do_call(self, req_id: int, kind: str, payload: object) -> None:
            self.call_done.emit(req_id, daemon.answer(kind, dict(payload or {})))

        def shutdown(self) -> None:
            pass

    return _FakeWorker


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def rig(qtbot, tmp_path, settings_service, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    state = _state()
    daemon = FakeDaemon()
    clock = Clock()
    controller = PwmReportController(
        state,
        "/tmp/fake.sock",
        directory=tmp_path / "reports",
        clock=clock,
        worker_factory=_worker_factory(daemon),
    )
    window = PwmReportWindow(controller, state, settings_service)
    qtbot.addWidget(window)
    # S5-1: the window opens on the Reports page; a new report starts from there.
    assert window.current_page() == PAGE_HISTORY
    _btn(window, "PwmReport_Btn_new").click()
    yield window, controller, state, daemon, clock, settings_service
    controller.shutdown()


def _box(window, test: str, cid: str) -> QCheckBox:
    slug = "".join(c if c.isalnum() else "_" for c in cid)
    box = window.findChild(QCheckBox, f"PwmReport_Check_{test}_{slug}")
    assert box is not None
    return box


def _btn(window, name: str) -> QPushButton:
    button = window.findChild(QPushButton, name)
    assert button is not None
    return button


def _clear(window) -> None:
    """Untick every pre-selected test, so a test chooses exactly what runs."""
    for box in window.findChildren(QCheckBox):
        name = box.objectName()
        if name.startswith("PwmReport_Check_") and "consent" not in name.lower():
            box.setChecked(False)


def _run_to_end(qtbot, controller, clock, *, limit: int = 60) -> None:
    for _ in range(limit):
        qtbot.wait(5)
        if not controller.is_running():
            return
        clock.now += HANDBACK_WAIT_S
        controller._on_tick()
    raise AssertionError("the run did not finish")


# ── Scope defaults and capability gates ─────────────────────────────────────


def test_scope_preselects_verify_and_pairing_only(rig):
    window, *_ = rig
    assert window.current_page() == PAGE_SCOPE
    assert _box(window, "verify", CPU).isChecked()
    assert _box(window, "pairing", CPU).isChecked()
    assert not _box(window, "sweep", CPU).isChecked()
    assert not _box(window, "probe", SYS).isChecked()


def test_the_probe_offer_follows_the_wire_field(qtbot, tmp_path, settings_service, monkeypatch):
    """DEC-334's correction: assert against the WIRE field, never the lookup."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for flag in (True, False):
        state = _state(stall_probe=flag)
        controller = PwmReportController(state, "/tmp/fake.sock", directory=tmp_path / "r")
        window = PwmReportWindow(controller, state, settings_service)
        qtbot.addWidget(window)
        _btn(window, "PwmReport_Btn_new").click()
        box = _box(window, "probe", SYS)
        assert box.isEnabled() is state.capabilities.control.stall_probe
        if not flag:
            assert "requires control-ofc-daemon" in box.toolTip()
        controller.shutdown()


def test_a_cpu_fan_is_never_offered_the_probe(rig):
    window, *_ = rig
    box = _box(window, "probe", CPU)
    assert not box.isEnabled() and "CPU fan" in box.toolTip()


# ── The flow, by click ───────────────────────────────────────────────────────


def test_the_whole_flow_by_click(qtbot, rig):
    window, controller, _unused, daemon, clock, settings = rig
    _clear(window)
    _box(window, "verify", CPU).click()
    _box(window, "sweep", CPU).click()
    _btn(window, "PwmReport_Btn_next").click()
    assert window.current_page() == PAGE_SETUP
    slug = "".join(c if c.isalnum() else "_" for c in CPU)
    from PySide6.QtWidgets import QComboBox

    fans_combo = window.findChild(QComboBox, f"PwmReport_Combo_fans_{slug}")
    fans_combo.setCurrentIndex(fans_combo.findData(3))
    _btn(window, "PwmReport_Btn_next").click()
    assert window.current_page() == PAGE_REVIEW
    # "Your setup" is remembered, keyed by the stable header id (decision 7).
    assert settings.settings.hardware_notes[CPU]["fans_behind"] == 3

    start = _btn(window, "PwmReport_Btn_start")
    assert not start.isEnabled(), "Start waits for consent when a test writes"
    window.findChild(QCheckBox, "PwmReport_Check_consent").click()
    assert start.isEnabled()
    start.click()
    assert window.current_page() == PAGE_RUN
    _run_to_end(qtbot, controller, clock)
    assert window.current_page() == PAGE_REPORT

    doc = controller.document()
    assert doc["state"] == d.STATE_COMPLETE
    assert [s["test"] for s in doc["steps"]] == ["verify", "sweep"]
    assert doc["plan"]["consent_text"], "what the user agreed to is recorded"
    assert doc["user_facts"]["headers"][CPU]["fans_behind"] == 3
    saved = json.loads(controller.last_path.read_text())
    assert saved["report_id"] == doc["report_id"] and saved["state"] == d.STATE_COMPLETE
    assert "start_sweep" in daemon.kinds()


def test_the_probe_needs_its_own_confirmation(qtbot, rig):
    window, controller, _s, daemon, clock, _settings = rig
    _clear(window)
    _box(window, "probe", SYS).click()
    _btn(window, "PwmReport_Btn_next").click()
    _btn(window, "PwmReport_Btn_next").click()
    start = _btn(window, "PwmReport_Btn_start")
    window.findChild(QCheckBox, "PwmReport_Check_consent").click()
    assert not start.isEnabled(), "the general consent alone does not cover the probe"
    slug = "".join(c if c.isalnum() else "_" for c in SYS)
    window.findChild(QCheckBox, f"PwmReport_Check_probeConsent_{slug}").click()
    assert start.isEnabled()
    start.click()
    _run_to_end(qtbot, controller, clock)
    payload = next(p for k, p in daemon.calls if k == "start_probe")
    assert payload["args"] == {"acknowledge_below_floor": True}
    assert controller.document()["plan"]["probe_consent"] == [SYS]


def test_nothing_selected_needs_no_consent(qtbot, rig):
    window, controller, _s, daemon, clock, _settings = rig
    _clear(window)
    _btn(window, "PwmReport_Btn_next").click()
    _btn(window, "PwmReport_Btn_next").click()
    consent = window.findChild(QCheckBox, "PwmReport_Check_consent")
    assert not consent.isVisibleTo(window)
    _btn(window, "PwmReport_Btn_start").click()
    _run_to_end(qtbot, controller, clock)
    assert daemon.kinds() == ["snapshot", "snapshot"], "read-only: two snapshots, no writes"


def test_demo_mode_refuses_to_start(rig):
    window, _c, state, *_ = rig
    state.set_mode(OperationMode.DEMO)
    _btn(window, "PwmReport_Btn_next").click()
    _btn(window, "PwmReport_Btn_next").click()
    window.findChild(QCheckBox, "PwmReport_Check_consent").click()
    assert not _btn(window, "PwmReport_Btn_start").isEnabled()
    assert any("Demo mode" in r for r in window.refusals())


# ── Cancel and close ─────────────────────────────────────────────────────────


def _start_sweep_only(qtbot, window, daemon):
    daemon.polls_until_done = 10_000  # stays running until cancelled
    _clear(window)
    _box(window, "sweep", CPU).click()
    _btn(window, "PwmReport_Btn_next").click()
    _btn(window, "PwmReport_Btn_next").click()
    window.findChild(QCheckBox, "PwmReport_Check_consent").click()
    _btn(window, "PwmReport_Btn_start").click()
    qtbot.waitUntil(lambda: "start_sweep" in daemon.kinds(), timeout=2000)
    qtbot.wait(20)


def test_cancel_issues_a_delete_for_the_running_test(qtbot, rig):
    window, controller, _s, daemon, clock, _settings = rig
    _start_sweep_only(qtbot, window, daemon)
    _btn(window, "PwmReport_Btn_cancel").click()
    qtbot.waitUntil(lambda: "cancel" in daemon.kinds(), timeout=2000)
    (payload,) = [p for k, p in daemon.calls if k == "cancel"]
    assert payload["args"] == {"slot": "characterization"}
    _run_to_end(qtbot, controller, clock)
    assert controller.document()["state"] == d.STATE_CANCELLED


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No])
def test_closing_mid_run_asks_first(qtbot, rig, monkeypatch, answer):
    window, controller, _s, daemon, clock, _settings = rig
    window.show()
    _start_sweep_only(qtbot, window, daemon)
    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[1]) or answer)
    window.close()
    assert asked, "closing during a run must ask"
    if answer == QMessageBox.StandardButton.Yes:
        qtbot.waitUntil(lambda: "cancel" in daemon.kinds(), timeout=2000)
        assert not window.isVisible()
        _run_to_end(qtbot, controller, clock)
        doc = controller.document()
        assert doc["state"] == d.STATE_CANCELLED and doc["state_reason"] == REASON_WINDOW_CLOSED
    else:
        qtbot.wait(20)
        assert "cancel" not in daemon.kinds()
        assert window.isVisible() and controller.is_running()


def test_quitting_mid_run_saves_an_interrupted_report(qtbot, rig, monkeypatch):
    window, controller, _s, daemon, _clock, _settings = rig
    _start_sweep_only(qtbot, window, daemon)
    cancelled: list[str] = []
    monkeypatch.setattr(controller, "_cancel_synchronously", cancelled.append)
    controller.shutdown()
    assert cancelled == ["characterization"], "the running test is cancelled on the way out"
    saved = json.loads(controller.last_path.read_text())
    assert saved["state"] == d.STATE_INTERRUPTED
    assert "closed" in saved["state_reason"]


# ── The Hardware page ───────────────────────────────────────────────────────


def _hardware_page(qtbot, tmp_path, monkeypatch, settings_service):
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.hardware_page import HardwarePage
    from tests.conftest import FakeDaemonClient

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    state = _state()
    page = HardwarePage(
        state=state,
        diagnostics_service=DiagnosticsService(state),
        client=FakeDaemonClient(),
        settings_service=settings_service,
    )
    qtbot.addWidget(page)
    return page, state


def test_the_report_window_is_single_instance(qtbot, tmp_path, monkeypatch, settings_service):
    page, _unused = _hardware_page(qtbot, tmp_path, monkeypatch, settings_service)
    button = page.findChild(QPushButton, "Hardware_Btn_pwmReport")
    button.click()
    first = page._report_window
    assert first is not None and first.isVisible()
    button.click()
    assert page._report_window is first, "a second click raises the same window"
    page.cleanup()


def test_a_run_stands_down_every_diagnostic_sharing_the_slot(
    qtbot, tmp_path, monkeypatch, settings_service
):
    page, state = _hardware_page(qtbot, tmp_path, monkeypatch, settings_service)
    page.findChild(QPushButton, "Hardware_Btn_pwmReport").click()
    cards = list(page._header_cards.values())
    assert cards, "precondition: the header cards exist"
    test_btn = next(b for b in cards[0].findChildren(QPushButton) if "Btn_test_" in b.objectName())
    assert test_btn.isEnabled(), "precondition: enabled before the run"
    page._report_controller.run_active_changed.emit(True)
    assert not test_btn.isEnabled() and test_btn.toolTip() == RUN_ACTIVE_REASON
    # A poll re-rendering the card must not re-enable it mid-run.
    state.set_fans(parse_fans({"fans": [fan(CPU, rpm=950), fan(SYS)]}))
    assert not test_btn.isEnabled()
    for name in ("Hardware_Btn_validation", "Hardware_Btn_lifecycle", "Hardware_Btn_thermal"):
        assert not page.findChild(QPushButton, name).isEnabled()
    page._report_controller.run_active_changed.emit(False)
    assert test_btn.isEnabled()
    page.cleanup()


def test_system_state_stands_its_three_hwmon_tests_down(qtbot):
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.system_state_page import SystemStatePage

    state = _state()
    page = SystemStatePage(state=state, diagnostics_service=DiagnosticsService(state))
    qtbot.addWidget(page)
    page._populate_verify_combo()
    names = (
        "SystemState_Btn_verifyPwm",
        "SystemState_Btn_verifyAll",
        "SystemState_Btn_characterize",
    )
    buttons = [page.findChild(QPushButton, n) for n in names]
    # Precondition, or the stand-down below proves nothing: Characterise is
    # hidden and disabled on a daemon without `control.pwm_characterization`.
    assert all(b.isEnabled() for b in buttons), "precondition"
    page.set_pwm_report_active(True)
    assert not any(b.isEnabled() for b in buttons)
    assert all(b.toolTip() == RUN_ACTIVE_REASON for b in buttons)
    # GPU fan buttons are untouched (S4-13).
    gpu = page.findChild(QPushButton, "SystemState_Btn_verifyGpu")
    assert gpu.toolTip() != RUN_ACTIVE_REASON
    page.set_pwm_report_active(False)
    assert all(b.isEnabled() for b in buttons)


def test_a_sweep_finishing_mid_run_does_not_re_enable_verify_all(qtbot):
    """`_finish_verify_all` used a bare setEnabled(True) — the `ACK-z` shape — which
    would re-enable the button in the middle of a report run (S4-13)."""
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.system_state_page import SystemStatePage

    state = _state()
    page = SystemStatePage(state=state, diagnostics_service=DiagnosticsService(state))
    qtbot.addWidget(page)
    page._populate_verify_combo()
    button = page.findChild(QPushButton, "SystemState_Btn_verifyAll")
    assert button.isEnabled(), "precondition"
    page.set_pwm_report_active(True)
    page._finish_verify_all()
    assert not button.isEnabled()
    page.set_pwm_report_active(False)
    assert button.isEnabled()


@pytest.mark.parametrize("condition", ["disconnected", "thermal", "diagnostic", "session"])
def test_every_start_refusal_is_wired_from_the_live_state(rig, condition):
    """The call site, not just `start_refusals` (CLAUDE.md: extracting a rule
    does not test the call site). Each input comes from where production reads it."""
    from control_ofc.api.models import ValidationSessionSummary

    window, _c, state, *_ = rig
    _btn(window, "PwmReport_Btn_next").click()
    _btn(window, "PwmReport_Btn_next").click()
    window.findChild(QCheckBox, "PwmReport_Check_consent").click()
    start = _btn(window, "PwmReport_Btn_start")
    assert start.isEnabled(), "precondition: a healthy state may start"
    if condition == "disconnected":
        state.set_connection(ConnectionState.DISCONNECTED)
    elif condition == "thermal":
        state.set_status(DaemonStatus(thermal_state="emergency"))
    elif condition == "diagnostic":
        state.set_status(DaemonStatus(thermal_state="normal", verify_active=True))
    else:
        state.set_status(
            DaemonStatus(
                thermal_state="normal",
                validation_session=ValidationSessionSummary(state="recording"),
            )
        )
    assert not start.isEnabled()
    label = window.findChild(QLabel, "PwmReport_Label_reviewRefusals")
    assert label.isVisibleTo(window) and "Cannot start now" in label.text()


def test_a_header_left_changed_offers_reapply_and_it_activates_the_profile(qtbot, rig, monkeypatch):
    window, controller, _s, daemon, clock, _settings = rig
    daemon.final_fans = [fan(CPU, mode=2), fan(SYS)]  # CPU left in firmware mode
    _clear(window)
    _box(window, "verify", CPU).click()
    _btn(window, "PwmReport_Btn_next").click()
    _btn(window, "PwmReport_Btn_next").click()
    window.findChild(QCheckBox, "PwmReport_Check_consent").click()
    _btn(window, "PwmReport_Btn_start").click()
    _run_to_end(qtbot, controller, clock)
    assert controller.document()["actions"]["reapply_profile"] is True
    reapply = _btn(window, "PwmReport_Btn_reapply")
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes,
    )
    reapply.click()
    assert asked and "manual override" in asked[0], "the confirmation names DEC-189's effect"
    qtbot.waitUntil(lambda: "reapply_profile" in daemon.kinds(), timeout=2000)
    payload = next(p for k, p in daemon.calls if k == "reapply_profile")
    assert payload["args"] == {"profile_id": "quiet"}
    message = window.findChild(QLabel, "PwmReport_Label_reapply")
    qtbot.waitUntil(lambda: "re-applied" in message.text(), timeout=2000)
