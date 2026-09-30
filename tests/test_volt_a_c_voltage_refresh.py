"""`VOLT-a` + `VOLT-c` — the Voltages panel refreshes, says when it was read, and
has data in demo.

`VOLT-a`: the panel rendered the shared ``/diagnostics/hardware`` snapshot, said
it was "measured when the GUI connected" (false since System State's refresh and
rescan could replace it), and had no refresh of its own. Now Re-scan re-reads
it, the panel says when the readings were taken, and it redraws whenever any
writer lands a new result.

`VOLT-c`: demo seeded no rails and nothing loaded the demo diagnostics, so the
panel (and System State) were empty in every demo surface.

Also the `VOLT-b` footnote correction: an ``/etc/sensors.d`` file does not name
these channels, because the kernel driver never reads it.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLabel, QTableWidget

from control_ofc.api.errors import DaemonTimeout
from control_ofc.api.models import (
    ConnectionState,
    HardwareDiagnosticsResult,
    VoltageRail,
)
from control_ofc.services import diagnostics_service as diag_module
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_service import DemoService
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.hardware_view import build_voltage_panel
from control_ofc.ui.pages import hardware_page as hw_module
from control_ofc.ui.pages.diagnostics_workers import _HwDiagWorker
from control_ofc.ui.pages.hardware_page import _VOLT_NAME, _VOLT_VALUE, HardwarePage


def _rail(channel: int, value_v: float, label: str = "") -> VoltageRail:
    return VoltageRail(
        id=f"hwmon:it8696:it87.2624:in{channel}",
        chip_name="it8696",
        channel=channel,
        label=label or f"in{channel}",
        value_v=value_v,
        identified=bool(label),
    )


_OLD = [_rail(0, 1.236), _rail(7, 3.288, "3VSB")]
_NEW = [_rail(0, 1.250), _rail(7, 3.300, "3VSB")]


def _local(y: int, mo: int, d: int, h: int, mi: int) -> float:
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


# ── View-model: the read time ────────────────────────────────────────────


def test_a_reading_from_today_says_the_clock_time_it_was_read():
    read_at = _local(2026, 9, 30, 14, 3)
    panel = build_voltage_panel(_OLD, read_at=read_at, now=read_at + 3600, refresh_error="")
    assert panel.provenance_text.startswith("Read at 14:03. ")
    assert "not on the live poll" in panel.provenance_text
    assert "Re-scan" in panel.provenance_text


def test_a_reading_from_another_day_carries_its_date():
    """A session can outlive a day, and "Read at 23:58" seen the next morning
    would read as last night's or this morning's."""
    read_at = _local(2026, 9, 29, 23, 58)
    panel = build_voltage_panel(
        _OLD, read_at=read_at, now=_local(2026, 9, 30, 9, 0), refresh_error=""
    )
    month = time.strftime("%b", time.localtime(read_at))  # locale-dependent word
    assert panel.provenance_text.startswith(f"Read on 29 {month} at 23:58. ")


def test_the_same_day_of_a_different_year_is_another_day():
    """The date check compares the year too, not only the day of the year."""
    read_at = _local(2025, 9, 30, 14, 3)
    panel = build_voltage_panel(
        _OLD, read_at=read_at, now=_local(2026, 9, 30, 14, 3), refresh_error=""
    )
    assert panel.provenance_text.startswith("Read on 30 ")


def test_an_unknown_read_time_says_nothing_about_age():
    """Nothing stamped the snapshot (a direct cache assignment): no invented time,
    but the not-live half still holds."""
    with_time = build_voltage_panel(
        _OLD, read_at=_local(2026, 9, 30, 14, 3), now=0.0, refresh_error=""
    )
    without = build_voltage_panel(_OLD, read_at=None, now=0.0, refresh_error="")
    assert "Read" in with_time.provenance_text  # precondition: the other arm says it
    assert "Read" not in without.provenance_text
    assert "not on the live poll" in without.provenance_text


def test_the_refresh_error_is_carried_with_or_without_rails():
    failed = build_voltage_panel(_OLD, read_at=None, now=0.0, refresh_error="timed out")
    empty_failed = build_voltage_panel([], read_at=None, now=0.0, refresh_error="timed out")
    fine = build_voltage_panel(_OLD, read_at=None, now=0.0, refresh_error="")
    assert failed.refresh_error_text == "Could not re-read voltages: timed out"
    assert empty_failed.refresh_error_text == failed.refresh_error_text
    assert fine.refresh_error_text == ""


def test_the_footnote_no_longer_says_sensors_d_names_the_channels():
    """`VOLT-b`'s false claim. libsensors reads /etc/sensors.d; the kernel driver
    does not, so the labels this table shows cannot change because of it."""
    footnote = build_voltage_panel(_OLD, read_at=None, now=0.0, refresh_error="").footnote
    assert footnote  # precondition: an unnamed channel is present
    assert "is what names them" not in footnote
    assert "for the sensors command, not here" in footnote
    assert "kernel driver" in footnote


# ── DiagnosticsService: the single writer stamps and announces ──────────


def test_set_hw_diagnostics_stamps_the_arrival_time_and_announces_it(qtbot, monkeypatch):
    diag = DiagnosticsService(AppState())
    assert diag.last_hw_diagnostics_at is None
    seen: list[tuple] = []
    # The listener reads the cache and its time INSIDE the slot, so this also
    # proves the signal goes out after both are written.
    diag.hw_diagnostics_changed.connect(
        lambda: seen.append((diag.last_hw_diagnostics, diag.last_hw_diagnostics_at))
    )
    monkeypatch.setattr(diag_module.time, "time", lambda: 1234.5)
    result = HardwareDiagnosticsResult(voltages=_OLD)
    diag.set_hw_diagnostics(result)
    assert diag.last_hw_diagnostics_at == 1234.5
    assert seen == [(result, 1234.5)]


def test_the_signal_fires_without_an_app_state_too():
    """``state`` is optional; the old early return for it must not skip the emit."""
    diag = DiagnosticsService(None)
    seen: list[int] = []
    diag.hw_diagnostics_changed.connect(lambda: seen.append(1))
    diag.set_hw_diagnostics(HardwareDiagnosticsResult())
    assert seen == [1]
    assert diag.last_hw_diagnostics_at is not None


# ── The Hardware page ────────────────────────────────────────────────────


class _FakeClient:
    """Enough of ``DaemonClient`` for ``_ensure_worker``: a socket path."""

    socket_path = "/nonexistent/control-ofc-test.sock"


def _worker_answering(outcome):
    """A real ``_HwDiagWorker`` whose daemon call returns or raises ``outcome``,
    so the page's worker wiring and the worker's own error mapping both run."""

    class _Client:
        def hardware_diagnostics(self):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def close(self):
            pass

    class _Worker(_HwDiagWorker):
        def _ensure_client(self):
            return _Client()

    return _Worker


def _page(qtbot, monkeypatch, *, outcome=None, client=None, cached=True):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    diag = DiagnosticsService(state)
    if cached:
        diag.set_hw_diagnostics(HardwareDiagnosticsResult(voltages=_OLD))
    if outcome is not None:
        monkeypatch.setattr(hw_module, "_HwDiagWorker", _worker_answering(outcome))
    page = HardwarePage(state=state, diagnostics_service=diag, client=client)
    # Join the worker thread even when an assertion fails first: a QThread
    # destroyed while running aborts the whole test process.
    qtbot.addWidget(page, before_close_func=lambda w: w.cleanup())
    # The readiness half of Re-scan is not under test; keep it off the socket.
    page._ensure_readiness_worker = lambda: False
    page._render_voltages()
    return page, diag


def _settle() -> None:
    """A redraw ``deleteLater``s the old table and notes, so until the deferred
    deletes run ``findChild`` can still return the previous render's widgets."""
    QApplication.instance().sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _values(page) -> dict[str, str]:
    _settle()
    table = page.findChild(QTableWidget, "Hardware_Table_voltages")
    assert table is not None
    return {
        table.item(i, _VOLT_NAME).text(): table.item(i, _VOLT_VALUE).text()
        for i in range(table.rowCount())
    }


def _expected(rails) -> dict[str, str]:
    return {r.label: f"{r.value_v:.3f} V" for r in rails}


def _error_note(page) -> QLabel | None:
    _settle()
    return page.findChild(QLabel, "Hardware_Label_voltagesRefreshError")


def test_re_scan_re_reads_the_voltages_and_the_panel_shows_the_new_values(qtbot, monkeypatch):
    new = HardwareDiagnosticsResult(voltages=_NEW)
    page, diag = _page(qtbot, monkeypatch, outcome=new, client=_FakeClient())
    assert _values(page) == _expected(_OLD)  # precondition: the old snapshot shows

    page._refresh_btn.click()
    qtbot.waitUntil(lambda: diag.last_hw_diagnostics is new, timeout=3000)

    assert _values(page) == _expected(_NEW)
    _settle()
    provenance = page.findChild(QLabel, "Hardware_Label_voltagesProvenance")
    assert provenance is not None and provenance.text().startswith("Read at ")
    assert _error_note(page) is None
    page.cleanup()
    assert page._hw_diag_worker is None and page._hw_diag_thread is None


def test_a_failed_re_read_keeps_the_old_readings_and_says_why(qtbot, monkeypatch):
    page, diag = _page(qtbot, monkeypatch, outcome=DaemonTimeout(), client=_FakeClient())
    old = diag.last_hw_diagnostics

    page._refresh_btn.click()
    qtbot.waitUntil(lambda: _error_note(page) is not None, timeout=3000)

    # The worker's own message for a timeout, carried through unchanged.
    assert _error_note(page).text() == "Could not re-read voltages: Diagnostics fetch timed out"
    assert diag.last_hw_diagnostics is old
    assert _values(page) == _expected(_OLD)
    assert page._hw_diag_in_flight is False

    # Any writer's next result clears it — here System State's path.
    diag.set_hw_diagnostics(HardwareDiagnosticsResult(voltages=_NEW))
    assert _error_note(page) is None
    assert _values(page) == _expected(_NEW)
    page.cleanup()


def test_a_failed_first_read_says_why_under_the_empty_note(qtbot, monkeypatch):
    """No snapshot yet (the connect-time fetch failed too): the empty note alone
    would read as "this board has no rails"."""
    page, diag = _page(
        qtbot, monkeypatch, outcome=DaemonTimeout(), client=_FakeClient(), cached=False
    )
    assert diag.last_hw_diagnostics is None
    page._refresh_btn.click()
    qtbot.waitUntil(lambda: _error_note(page) is not None, timeout=3000)
    assert page.findChild(QLabel, "Hardware_Label_voltagesEmpty") is not None
    assert page.findChild(QTableWidget, "Hardware_Table_voltages") is None
    assert "Diagnostics fetch timed out" in _error_note(page).text()


def test_the_panel_redraws_when_another_writer_lands_a_result(qtbot, monkeypatch):
    """Not only on this page's show: the poll worker, System State's refresh and
    its rescan all replace the cache while this page may be on screen."""
    page, diag = _page(qtbot, monkeypatch)
    assert _values(page) == _expected(_OLD)
    diag.set_hw_diagnostics(HardwareDiagnosticsResult(voltages=_NEW))
    assert _values(page) == _expected(_NEW)


def test_re_scan_sends_one_voltage_request_at_a_time(qtbot, monkeypatch):
    page, _diag = _page(qtbot, monkeypatch, client=_FakeClient())
    page._ensure_hw_diag_worker = lambda: True
    sent: list[int] = []
    page._hw_diag_request.connect(lambda: sent.append(1))

    page._rescan()
    page._rescan()  # a second press while the first is outstanding
    assert sent == [1]

    page._on_hw_diag_ok(HardwareDiagnosticsResult(voltages=_NEW))
    page._rescan()
    assert sent == [1, 1]


def test_re_scan_also_forces_the_readiness_assessment(qtbot, monkeypatch):
    """The button still does what it did before `VOLT-a`."""
    page, _diag = _page(qtbot, monkeypatch, client=_FakeClient())
    calls: list = []
    page._fetch_readiness = lambda *a, **k: calls.append(k.get("force"))  # type: ignore[method-assign]
    page._fetch_voltages = lambda: calls.append("voltages")  # type: ignore[method-assign]
    page._refresh_btn.click()
    assert calls == ["voltages", True]


def test_without_a_daemon_re_scan_sends_no_voltage_request(qtbot, monkeypatch):
    """Demo, or not connected: the readiness half already says so."""
    page, _diag = _page(qtbot, monkeypatch, client=None)
    sent: list[int] = []
    page._hw_diag_request.connect(lambda: sent.append(1))
    page._refresh_btn.click()
    assert sent == []
    assert _error_note(page) is None


# ── Demo (`VOLT-c`) ──────────────────────────────────────────────────────


def test_demo_diagnostics_carry_both_kinds_of_rail():
    """The panel exists to draw the identified/unnamed distinction, so the demo
    must show both kinds, not only one."""
    rails = DemoService().hardware_diagnostics().voltages
    assert rails
    kinds = {r.identified for r in rails}
    assert kinds == {True, False}
    for r in rails:
        # A labelled rail carries its label; an unnamed one its channel name.
        assert (r.label != f"in{r.channel}") == r.identified


def test_demo_mode_loads_the_demo_diagnostics_into_the_shared_cache(qtbot, settings_service):
    from control_ofc.ui.main_window import MainWindow

    # settings_service is explicit (DEC-244): omitting it points MainWindow at
    # the real user config file.
    window = MainWindow(settings_service=settings_service, demo_mode=True)
    qtbot.addWidget(window)

    cached = window._diag.last_hw_diagnostics
    assert cached is not None
    assert cached.voltages == DemoService().hardware_diagnostics().voltages
    assert window._diag.last_hw_diagnostics_at is not None
    # And the Voltages panel renders it, both kinds included.
    window.hardware_page._render_voltages()
    assert set(_values(window.hardware_page)) == {r.label for r in cached.voltages}
    window.dashboard_page.cleanup()
