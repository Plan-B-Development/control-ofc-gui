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
    CoolingDevice,
    CoolingDeviceInventory,
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


def _hardware_page(qtbot, tmp_path, monkeypatch, settings_service, *, devices=()):
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.hardware_page import HardwarePage
    from tests.conftest import FakeDaemonClient

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    state = _state(cooling_devices=True, validation_sessions=True)
    if devices:
        state.set_cooling_devices(CoolingDeviceInventory(cooling_devices=list(devices)))
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


def _aio(device_id: str = "aio0") -> CoolingDevice:
    return CoolingDevice(
        id=device_id, name="AIO", kind="aio_liquid", pump_member=CPU, radiator_members=[SYS]
    )


def _device_buttons(page, device_id: str = "aio0") -> dict[str, QPushButton]:
    card = page._device_cards[device_id]
    return {
        name: card.findChild(QPushButton, f"CoolingDeviceCard_Btn_{name}_{device_id}")
        for name in ("charPump", "validate", "viewHeaders", "edit", "forget")
    }


def test_a_run_stands_down_the_cooling_device_cards_diagnostics(
    qtbot, tmp_path, monkeypatch, settings_service
):
    """`PTA-b`: *Characterise Pump* and *Start Validation* share the report's slot."""
    page, state = _hardware_page(qtbot, tmp_path, monkeypatch, settings_service, devices=[_aio()])
    page.findChild(QPushButton, "Hardware_Btn_pwmReport").click()
    buttons = _device_buttons(page)
    assert all(b is not None and b.isEnabled() for b in buttons.values()), "precondition"
    page._report_controller.run_active_changed.emit(True)
    for name in ("charPump", "validate"):
        assert not buttons[name].isEnabled(), name
        assert buttons[name].toolTip() == RUN_ACTIVE_REASON, name
    # The actions that run nothing stay live.
    assert all(buttons[n].isEnabled() for n in ("viewHeaders", "edit", "forget"))
    # A poll re-rendering the card must not re-enable it mid-run...
    state.set_fans(parse_fans({"fans": [fan(CPU, rpm=950), fan(SYS)]}))
    assert not buttons["charPump"].isEnabled() and not buttons["validate"].isEnabled()
    # ...and a device configured mid-run stands down from its first render.
    state.set_cooling_devices(CoolingDeviceInventory(cooling_devices=[_aio(), _aio("aio1")]))
    late = _device_buttons(page, "aio1")
    assert not late["charPump"].isEnabled() and not late["validate"].isEnabled()
    page._report_controller.run_active_changed.emit(False)
    assert buttons["charPump"].isEnabled() and buttons["validate"].isEnabled()
    assert buttons["charPump"].toolTip() == ""
    page.cleanup()


def test_a_run_refuses_every_entry_point_the_device_card_reaches(
    qtbot, tmp_path, monkeypatch, settings_service
):
    """`PTA-b`: the buttons are one gate; the methods they call are the other."""
    from unittest.mock import MagicMock

    from control_ofc.ui.pages import hardware_page as hw

    page, _state_unused = _hardware_page(
        qtbot, tmp_path, monkeypatch, settings_service, devices=[_aio()]
    )
    opened: list[str] = []
    monkeypatch.setattr(
        hw, "PwmCharacterizationDialog", lambda hid, *a, **k: opened.append(hid) or MagicMock()
    )
    monkeypatch.setattr(page, "_ensure_char_worker", lambda: True)
    monkeypatch.setattr(page, "_ensure_validation_worker", lambda: True)
    # Precondition: outside a run, the same calls really do open a dialog.
    page._open_characterization(CPU)
    assert opened == [CPU], "precondition: characterisation opens outside a run"
    opened.clear()
    page._open_validation(kind="validation", device_id="aio0")
    dialog = page._validation_dialog
    assert dialog is not None, "precondition: a session window opens outside a run"
    dialog.reject()  # through `finished`, the teardown production uses
    assert page._validation_dialog is None, "precondition: the window is gone again"
    page.findChild(QPushButton, "Hardware_Btn_pwmReport").click()
    page._report_controller.run_active_changed.emit(True)
    page._open_characterization(CPU)
    page._open_validation(kind="validation", device_id="aio0")
    assert opened == [], "no characterisation dialog mid-run"
    assert page._validation_dialog is None, "no session dialog mid-run"
    page.cleanup()


def test_a_session_window_opened_before_the_run_cannot_start_during_it(
    qtbot, tmp_path, monkeypatch, settings_service
):
    """`PTA-b`: the session dialog is modeless, so it outlives the stand-down."""
    page, _state_unused = _hardware_page(
        qtbot, tmp_path, monkeypatch, settings_service, devices=[_aio()]
    )
    monkeypatch.setattr(page, "_ensure_validation_worker", lambda: True)
    starts: list[tuple] = []
    page._validation_start_request.connect(lambda *args: starts.append(args))
    _device_buttons(page)["validate"].click()
    dialog = page._validation_dialog
    assert dialog is not None, "precondition: the card opened a session window"
    start = dialog.findChild(QPushButton, "Validation_Btn_start")
    assert start.isEnabled(), "precondition"
    page.findChild(QPushButton, "Hardware_Btn_pwmReport").click()
    page._report_controller.run_active_changed.emit(True)
    start.click()
    assert starts == [], "a session start reached the daemon mid-run"
    assert dialog.findChild(QLabel, "Validation_Label_status").text() == RUN_ACTIVE_REASON
    assert start.isEnabled(), "a refused start re-arms, as every refused start does"
    page._report_controller.run_active_changed.emit(False)
    start.click()
    assert len(starts) == 1, "after the run the same click starts a session"
    dialog.close()
    page.cleanup()


def _main_window(qtbot, tmp_path, monkeypatch, settings_service, profile_service):
    from control_ofc.ui.main_window import MainWindow
    from tests.conftest import FakeDaemonClient

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    state = _state()
    win = MainWindow(
        state=state,
        profile_service=profile_service,
        settings_service=settings_service,
        client=FakeDaemonClient(),
        demo_mode=False,
    )
    qtbot.addWidget(win)
    # MainWindow's startup marks the state disconnected until its first poll;
    # replay what that poll would have delivered.
    polled = _state()
    state.set_connection(polled.connection)
    state.set_mode(polled.mode)
    state.set_capabilities(polled.capabilities)
    state.set_hwmon_headers(list(polled.hwmon_headers))
    state.set_fans(list(polled.fans))
    state.set_status(polled.daemon_status)
    return win, state


def test_main_window_stands_system_state_down_for_a_run(
    qtbot, tmp_path, monkeypatch, settings_service, profile_service
):
    """`PTA-c`: through MainWindow's real wiring, never a direct
    `set_pwm_report_active` — the connection is the thing that can go missing."""
    win, _state_unused = _main_window(
        qtbot, tmp_path, monkeypatch, settings_service, profile_service
    )
    try:
        verify_all = win.system_state_page.findChild(QPushButton, "SystemState_Btn_verifyAll")
        assert verify_all.isEnabled(), "precondition"
        win.hardware_page.findChild(QPushButton, "Hardware_Btn_pwmReport").click()
        win.hardware_page._report_controller.run_active_changed.emit(True)
        assert not verify_all.isEnabled()
        assert verify_all.toolTip() == RUN_ACTIVE_REASON
        win.hardware_page._report_controller.run_active_changed.emit(False)
        assert verify_all.isEnabled()
    finally:
        win.hardware_page.cleanup()
        win.system_state_page.cleanup()


SWEEP_REFUSAL = "System State page is still running"


def test_a_system_state_sweep_refuses_the_reports_start(
    qtbot, tmp_path, monkeypatch, settings_service, profile_service
):
    """`PTA-d`: the poll's `verify_active` reads false between two of a sweep's
    verifies, so the report must ask System State itself — through MainWindow."""
    win, state = _main_window(qtbot, tmp_path, monkeypatch, settings_service, profile_service)
    system_state = win.system_state_page
    try:
        # No worker, so the sweep's first verify request reaches nothing and the
        # sweep stays in flight for as long as the test needs.
        monkeypatch.setattr(system_state, "_ensure_verify_worker", lambda: True)
        win.hardware_page.findChild(QPushButton, "Hardware_Btn_pwmReport").click()
        window = win.hardware_page._report_window
        started: list[tuple] = []
        monkeypatch.setattr(
            win.hardware_page._report_controller,
            "start",
            lambda *args: started.append(args) or False,
        )
        _btn(window, "PwmReport_Btn_next").click()
        _btn(window, "PwmReport_Btn_next").click()
        window.findChild(QCheckBox, "PwmReport_Check_consent").click()
        start = _btn(window, "PwmReport_Btn_start")
        assert start.isEnabled(), "precondition: nothing is running yet"

        # The sweep starts AFTER the window last refreshed, with the poll saying
        # nothing is running — the gap the row is about.
        system_state.findChild(QPushButton, "SystemState_Btn_verifyAll").click()
        assert system_state.pwm_verify_running(), "precondition: the sweep is running"
        assert start.isEnabled(), "precondition: no poll has refreshed the window"
        start.click()
        assert started == [], "the report started over a running sweep"
        refusals = window.findChild(QLabel, "PwmReport_Label_reviewRefusals")
        assert SWEEP_REFUSAL in refusals.text(), "Start did nothing without saying why"
        # And the next poll keeps it refused, with `verify_active` false.
        state.set_status(DaemonStatus(thermal_state="normal", verify_active=False))
        assert not start.isEnabled()

        # Answer each verify the way the absent worker would, until the sweep ends.
        while (pending := system_state._verify_all_pending) is not None:
            system_state._on_verify_error("error", "not under test", pending)
        assert not system_state.pwm_verify_running(), "precondition: the sweep has ended"
        state.set_status(DaemonStatus(thermal_state="normal"))
        assert start.isEnabled(), "the refusal clears when the sweep does"
        assert SWEEP_REFUSAL not in refusals.text()
    finally:
        win.hardware_page.cleanup()
        system_state.cleanup()


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
