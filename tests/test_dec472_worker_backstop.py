"""DEC-472: every diagnostics worker slot ends in one shared backstop.

`PTA-p` gave `_VerifyWorker` a final ``except Exception``; DEC-266 gave the
rescan one. Every other slot still let an exception outside the daemon-error
family escape, and Qt prints and swallows an exception escaping a slot — so
neither result signal fired and the consumer's in-flight state never cleared
(`PTA-q`: *Test GPU Fan Control* disabled until restart; `ROLE-g`: a status poll
in flight when teardown closed the client).

Each case drives the slot with a client whose call raises httpx's closed-client
``RuntimeError`` — the exception `ROLE-g` saw — and asserts the slot still
answers, exactly once, on its error signal. The quiet-after-shutdown half
asserts the same slot says nothing once the worker has been shut down.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication

from control_ofc.services.app_state import AppState
from control_ofc.ui.pages.diagnostics_workers import (
    UNEXPECTED_VERIFY_ERROR,
    _CharacterizationWorker,
    _ControlPathWorker,
    _GpuVerifyWorker,
    _HardwareReadinessWorker,
    _HwDiagWorker,
    _OpenFanCalibrationWorker,
    _OpenFanFirmwareWorker,
    _PwmReportWorker,
    _ValidationWorker,
    _VerifyWorker,
    unexpected_error_message,
)
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.pages.pwm_report_controller import PwmReportController
from control_ofc.ui.pages.system_state_page import SystemStatePage

CLOSED = RuntimeError("Cannot send a request, as the client has been closed.")

# (id, worker class, slot name, slot args, client method that raises,
#  error signal, ok signal, the message the error signal must carry)
CASES = [
    ("verify", _VerifyWorker, "do_verify", ("h",), "verify_hwmon_pwm",
     "verify_error", "verify_ok", UNEXPECTED_VERIFY_ERROR),
    ("gpu-verify", _GpuVerifyWorker, "do_verify", ("g",), "verify_gpu_fan",
     "verify_error", "verify_ok", unexpected_error_message("the test")),
    ("gpu-reset", _GpuVerifyWorker, "do_reset", ("g",), "reset_gpu_fan",
     "reset_error", "reset_ok", unexpected_error_message("the restore")),
    ("hw-diag", _HwDiagWorker, "do_fetch", (), "hardware_diagnostics",
     "fetch_error", "fetch_ok", unexpected_error_message("the diagnostics fetch")),
    ("rescan", _HwDiagWorker, "do_rescan", (), "hwmon_rescan",
     "rescan_error", "rescan_ok", unexpected_error_message("the hardware rescan")),
    ("readiness", _HardwareReadinessWorker, "do_fetch", (), "hardware_readiness",
     "fetch_error", "fetch_ok", unexpected_error_message("the hardware readiness fetch")),
    ("readiness-refresh", _HardwareReadinessWorker, "do_refresh", (), "hardware_readiness",
     "fetch_error", "fetch_ok", unexpected_error_message("the hardware readiness fetch")),
    # Shown verbatim and unprefixed by the page, so it starts as a sentence.
    ("probe", _HardwareReadinessWorker, "do_probe", (), "superio_probe",
     "probe_error", "probe_ok", "The Super-I/O port probe ended with an unexpected error "
     "(details in the application log)"),
    ("char-preflight", _CharacterizationWorker, "do_preflight", ("h", "characterization"),
     "diagnostic_preflight", "preflight_error", "preflight_ready",
     "The safety preflight ended with an unexpected error (details in the application log)"),
    ("char-start", _CharacterizationWorker, "do_start", ("h", None, None, None, None),
     "start_characterization", "run_error", "run_updated",
     unexpected_error_message("the characterisation start")),
    ("char-poll", _CharacterizationWorker, "do_poll", (), "characterization_status",
     "run_error", "run_updated", unexpected_error_message("the status poll")),
    ("char-cancel", _CharacterizationWorker, "do_cancel", (), "cancel_characterization",
     "run_error", "run_updated", unexpected_error_message("the cancellation")),
    ("path-preflight", _ControlPathWorker, "do_preflight", ("h", "control_path"),
     "diagnostic_preflight", "preflight_error", "preflight_ready",
     "The safety preflight ended with an unexpected error (details in the application log)"),
    ("path-start", _ControlPathWorker, "do_start", ("h",), "start_control_path_discovery",
     "run_error", "run_updated", unexpected_error_message("the discovery start")),
    # `ROLE-g`'s own case.
    ("path-poll", _ControlPathWorker, "do_poll", (), "control_path_status",
     "run_error", "run_updated", unexpected_error_message("the status poll")),
    ("path-cancel", _ControlPathWorker, "do_cancel", (), "cancel_control_path_discovery",
     "run_error", "run_updated", unexpected_error_message("the cancellation")),
    ("ofan-start", _OpenFanCalibrationWorker, "do_start", (3,), "start_openfan_calibration",
     "run_error", "run_updated", unexpected_error_message("the calibration start")),
    ("ofan-poll", _OpenFanCalibrationWorker, "do_poll", (), "openfan_calibration_status",
     "run_error", "run_updated", unexpected_error_message("the status poll")),
    ("ofan-cancel", _OpenFanCalibrationWorker, "do_cancel", (), "cancel_openfan_calibration",
     "run_error", "run_updated", unexpected_error_message("the cancellation")),
    ("ofw-device", _OpenFanFirmwareWorker, "do_device", (), "openfan_device",
     "device_error", "device_ready", unexpected_error_message("the controller read")),
    # An unexpected failure of the POST answers on `start_unconfirmed`: it may
    # have reached the daemon, so the window asks rather than says it failed.
    ("ofw-start", _OpenFanFirmwareWorker, "do_start", ("S", {}, None, "connected"),
     "start_openfan_maintenance", "start_unconfirmed", "run_updated",
     unexpected_error_message("the update start")),
    # DEC-483: the upload before a daemon write fails before anything is sent
    # that could start a run, so it answers on `start_failed`.
    ("ofw-start-write", _OpenFanFirmwareWorker, "do_start", ("S", {}, b"uf2", "connected"),
     "stage_openfan_firmware", "start_failed", "run_updated",
     unexpected_error_message("the update start")),
    ("ofw-stage", _OpenFanFirmwareWorker, "do_stage", (b"uf2",), "stage_openfan_firmware",
     "stage_failed", "staged", unexpected_error_message("the file upload")),
    ("ofw-poll", _OpenFanFirmwareWorker, "do_poll", (), "openfan_maintenance_status",
     "run_error", "run_updated", unexpected_error_message("the status read")),
    ("ofw-cancel", _OpenFanFirmwareWorker, "do_cancel", (), "cancel_openfan_maintenance",
     "run_error", "run_updated", unexpected_error_message("the cancellation")),
    ("val-start", _ValidationWorker, "do_start", ("d", "", [], [], {}, False),
     "start_validation_session", "session_error", "session_updated",
     unexpected_error_message("starting the session")),
    ("val-poll", _ValidationWorker, "do_poll", (), "validation_session",
     "session_error", "session_updated", unexpected_error_message("reading the session")),
    ("val-stop", _ValidationWorker, "do_stop", (), "stop_validation_session",
     "session_error", "session_updated", unexpected_error_message("stopping the session")),
    ("val-cancel", _ValidationWorker, "do_cancel", (), "cancel_validation_session",
     "session_error", "session_updated", unexpected_error_message("cancelling the session")),
    ("val-marker", _ValidationWorker, "do_marker", ("x", ""), "add_validation_marker",
     "session_error", "action_ok", unexpected_error_message("marking an event")),
    ("val-measure", _ValidationWorker, "do_measurement", ("k", 1.0, "u", "", ""),
     "add_validation_measurement", "session_error", "action_ok",
     unexpected_error_message("recording a measurement")),
]  # fmt: skip


def _armed(cls, method: str):
    worker = cls("/tmp/x.sock")
    client = MagicMock()
    getattr(client, method).side_effect = CLOSED
    # `_ensure_client` is replaced rather than `_client` set, so the client
    # survives `shutdown()` (which drops `_client`) for the teardown cases.
    worker._ensure_client = MagicMock(return_value=client)
    return worker


def _capture(worker, error_signal: str, ok_signal: str) -> tuple[list, list]:
    errors: list[tuple] = []
    oks: list[tuple] = []
    getattr(worker, error_signal).connect(lambda *a: errors.append(a))
    getattr(worker, ok_signal).connect(lambda *a: oks.append(a))
    return errors, oks


@pytest.mark.parametrize(
    ("cls", "slot", "args", "method", "error_signal", "ok_signal", "message"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_an_unexpected_exception_still_answers_on_the_error_signal(
    qapp, cls, slot, args, method, error_signal, ok_signal, message
):
    worker = _armed(cls, method)
    errors, oks = _capture(worker, error_signal, ok_signal)

    getattr(worker, slot)(*args)

    assert oks == []
    assert len(errors) == 1, f"{cls.__name__}.{slot} must answer exactly once, got {errors}"
    assert errors[0][:2] == ("error", message)
    assert "client has been closed" not in errors[0][1], "interpreter text is for the log"


@pytest.mark.parametrize(
    ("cls", "slot", "args", "method", "error_signal", "ok_signal", "message"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_after_shutdown_the_backstop_is_quiet(
    qapp, caplog, cls, slot, args, method, error_signal, ok_signal, message
):
    """Teardown closes the client first on purpose; nobody is listening then."""
    worker = _armed(cls, method)
    errors, oks = _capture(worker, error_signal, ok_signal)
    worker.shutdown()

    with caplog.at_level(logging.DEBUG, logger="control_ofc.ui.pages.diagnostics_workers"):
        getattr(worker, slot)(*args)

    assert errors == [] and oks == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("after shutdown" in r.getMessage() for r in caplog.records), (
        "the race is still recorded, at debug"
    )


def test_outside_teardown_the_failure_is_logged_with_its_traceback(qapp, caplog):
    worker = _armed(_ControlPathWorker, "control_path_status")
    with caplog.at_level(logging.ERROR, logger="control_ofc.ui.pages.diagnostics_workers"):
        worker.do_poll()
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert record.exc_info is not None and record.exc_info[1] is CLOSED


# ── After shutdown nothing builds a new client (review P2) ────────────────────


def test_a_slot_after_shutdown_opens_no_new_client(qapp):
    """`shutdown` clears `_client`; a queued slot must not rebuild one."""
    worker = _ControlPathWorker("/tmp/does-not-exist.sock")
    errors, oks = _capture(worker, "run_error", "run_updated")
    worker.shutdown()

    worker.do_poll()

    assert worker._client is None
    assert errors == [] and oks == []


def test_a_report_snapshot_after_shutdown_stops_talking_to_the_daemon(qapp):
    """The backstop lets `_snapshot`'s read loop continue past a failed read, so
    without the refusal each later read opened a fresh client during teardown."""
    worker = _PwmReportWorker("/tmp/does-not-exist.sock")
    done: list[tuple] = []
    worker.call_done.connect(lambda req, outcome: done.append((req, outcome)))
    worker.shutdown()

    worker.do_call(9, "snapshot", {})

    assert worker._client is None, "a client was built after shutdown"
    [(req, outcome)] = done
    assert req == 9 and outcome.ok is False


# ── The PWM Test Report worker: its runner waits on `call_done` ───────────────


def test_a_report_call_that_raises_unexpectedly_still_completes(qapp):
    """With no body seen, the existing tail reports "no answer", not a verdict."""
    worker = _PwmReportWorker("/tmp/x.sock")
    client = MagicMock()
    client.verify_hwmon_pwm.side_effect = CLOSED
    worker._client = client
    done: list[tuple] = []
    worker.call_done.connect(lambda req, outcome: done.append((req, outcome)))

    worker.do_call(7, "verify", {"header_id": "h"})

    [(req, outcome)] = done
    assert req == 7
    assert outcome.ok is False and outcome.category == "unavailable"


def test_a_report_dispatch_failure_still_completes(qapp):
    worker = _PwmReportWorker("/tmp/x.sock")
    worker._execute = MagicMock(side_effect=KeyError("slot"))  # type: ignore[method-assign]
    done: list[tuple] = []
    worker.call_done.connect(lambda req, outcome: done.append((req, outcome)))

    worker.do_call(8, "poll", {})

    [(req, outcome)] = done
    assert req == 8
    assert outcome.ok is False and outcome.category == "error"
    assert outcome.error_message == unexpected_error_message("the call")


# ── Teardown: the page's requests go before the client closes (`ROLE-g`) ──────


class _RequestWorker(QObject):
    """A real QObject whose request slot records whether it was reached."""

    def __init__(self, order: list[str], page_signal) -> None:
        super().__init__()
        self._order = order
        self._page_signal = page_signal
        self._args: tuple = ()

    @Slot()
    def do_request(self) -> None:
        self._order.append("request")

    def shutdown(self) -> None:
        self._order.append("shutdown")
        # Still connected here would mean a request could reach the worker
        # after its client closed — the window this ordering closes.
        self._page_signal.emit(*self._args)


# Each page's own request signal: the disconnect under test is the page's.
PAGES = [
    (SystemStatePage, "_verify_request", ("h",)),
    (HardwarePage, "_discover_poll_request", ()),
]


@pytest.mark.parametrize(("page_cls", "signal_name", "args"), PAGES)
def test_teardown_drops_the_pages_requests_before_closing_the_client(
    qtbot, page_cls, signal_name, args
):
    page = page_cls(state=AppState())
    qtbot.addWidget(page)
    signal = getattr(page, signal_name)
    order: list[str] = []
    worker = _RequestWorker(order, signal)
    worker._args = args
    signal.connect(worker.do_request)
    other: list[str] = []
    signal.connect(lambda *_: other.append("other"))  # a link that is not to the worker
    thread = MagicMock()
    thread.quit.side_effect = lambda: order.append("quit")
    thread.wait.side_effect = lambda *_: order.append("wait") or True

    signal.emit(*args)
    assert order == ["request"], "precondition: the request reaches the worker before teardown"
    order.clear()

    page._teardown_worker(worker, thread, "t")

    assert order == ["shutdown", "quit", "wait"], order
    signal.emit(*args)
    assert order == ["shutdown", "quit", "wait"], "a request reached the worker after teardown"
    assert len(other) == 3, "teardown must drop only the page's links to this worker"


class _CallWorker(QObject):
    """The PWM report worker's shape: a ``do_call`` slot and ``call_done``."""

    call_done = Signal(int, object)

    def __init__(self, order: list[str], controller_signal) -> None:
        super().__init__()
        self._order = order
        self._controller_signal = controller_signal

    @Slot(int, str, object)
    def do_call(self, req: int, method: str, payload: object) -> None:
        self._order.append("call")

    def shutdown(self) -> None:
        self._order.append("shutdown")
        # Still connected here would queue a call against a closing client.
        self._controller_signal.emit(3, "poll", {})


def test_the_report_controllers_teardown_drops_its_requests_first(qtbot, tmp_path):
    """`PTA-u`: the PWM Test Report controller follows the pages' rule."""
    order: list[str] = []
    controller = PwmReportController(
        None,
        "/tmp/fake.sock",
        directory=tmp_path,
        worker_factory=lambda _path: _CallWorker(order, controller._call_request),
    )
    assert controller._ensure_worker(), "precondition: the worker was built"
    worker = controller._worker
    thread = controller._thread
    other: list[str] = []
    controller._call_request.connect(lambda *_: other.append("other"))
    controller._call_request.emit(1, "poll", {})
    qtbot.waitUntil(lambda: order == ["call"])
    order.clear()

    controller._teardown_worker()

    assert order[0] == "shutdown", order
    assert thread.wait(1000)
    controller._call_request.emit(2, "poll", {})
    QApplication.processEvents()
    assert "call" not in order, "a request reached the worker after teardown"
    assert len(other) == 3, "teardown must drop only the controller's links"
    del worker


def test_every_worker_slot_is_covered_by_this_file():
    """Enumerate the population, then subtract: a slot added later without a
    case here fails, rather than shipping without the backstop unseen."""
    import inspect

    from control_ofc.ui.pages import diagnostics_workers as module

    workers = [
        cls
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, module._SocketWorker) and cls is not module._SocketWorker
    ]
    slots = {(cls, name) for cls in workers for name in vars(cls) if name.startswith("do_")}
    covered = {(case[1], case[2]) for case in CASES} | {(_PwmReportWorker, "do_call")}
    assert len(workers) >= 9, "precondition: the worker classes were found"
    assert slots - covered == set(), f"slots with no backstop case: {sorted(slots - covered)}"
