"""Audit 2026-07-29 F-1 (re-assessed): worker-thread teardown closes the httpx
client BEFORE joining the thread — deliberately.

F-1 read this as a bug ("close the client only after quit/wait, like ControlsPage")
but that is a **false positive** for these workers: the poll / verify calls are
*synchronous blocking* calls, and closing the client is the only way to interrupt
an in-flight one so ``quit()``/``wait()`` can join promptly. Reordering to
close-after-join hangs the join (the blocking call never yields until its own
timeout) → ``wait(2000)`` times out → ``terminate()``. The verify path already has
a live regression test (``test_v1_2_diagnostics`` ``_BlockingVerifyClient``); these
pin the intentional close-first order for the polling service + the page
``_teardown_worker`` helpers so a future "reorder to match ControlsPage" is caught.
ControlsPage legitimately closes-after-join because its override calls are short
enough to join without the interrupt.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QObject, Signal

from control_ofc.services.app_state import AppState
from control_ofc.services.polling import PollingService
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.pages.system_state_page import SystemStatePage


def test_polling_shutdown_closes_client_before_joining_thread():
    order: list[str] = []
    svc = PollingService.__new__(PollingService)  # bypass __init__ / a real thread
    svc.stop = MagicMock(side_effect=lambda: order.append("stop"))
    svc._thread = MagicMock()
    svc._thread.quit.side_effect = lambda: order.append("quit")
    svc._thread.wait.side_effect = lambda *_: order.append("wait") or True
    svc._worker = MagicMock()
    svc._worker.shutdown.side_effect = lambda: order.append("shutdown")

    svc.shutdown()

    # client close (worker.shutdown) BEFORE the join — it interrupts an in-flight
    # blocking poll so wait() can return. See module docstring.
    assert order == ["stop", "shutdown", "quit", "wait"], order


class _OrderWorker(QObject):
    """A real QObject (so ``QObject.disconnect`` works) that records its close."""

    ping = Signal()

    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self._order = order

    def shutdown(self) -> None:
        self._order.append("shutdown")


def _assert_teardown_order(page) -> None:
    order: list[str] = []
    worker = _OrderWorker(order)
    worker.ping.connect(lambda: None)  # so disconnect(all) has a connection to drop
    thread = MagicMock()
    thread.quit.side_effect = lambda: order.append("quit")
    thread.wait.side_effect = lambda *_: order.append("wait") or True

    page._teardown_worker(worker, thread, "t")

    # shutdown (client close) BEFORE quit/wait — the interrupt-then-join order.
    assert order == ["shutdown", "quit", "wait"], order


def test_system_state_teardown_closes_client_before_join(qtbot):
    page = SystemStatePage(state=AppState())
    qtbot.addWidget(page)
    _assert_teardown_order(page)


def test_hardware_teardown_closes_client_before_join(qtbot):
    page = HardwarePage(state=AppState())
    qtbot.addWidget(page)
    _assert_teardown_order(page)
