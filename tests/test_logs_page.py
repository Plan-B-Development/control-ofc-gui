"""DEC-210: LogsPage — event table, filters, inspector, snapshot cards, journal.

Constructs the page directly (like the Diagnostics-tab / Overview tests) on the
shared ``DiagnosticsService`` feed and drives its handlers. Avoids spinning the
event loop for the journal thread — the worker is unit-tested in isolation and
the page handler is driven directly.
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QPushButton, QWidget

from control_ofc.api.models import ConnectionState
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.pages.logs_page import LogsPage, _JournalWorker
from control_ofc.ui.theme import active_theme


def _diag() -> DiagnosticsService:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    return DiagnosticsService(s)


def _page(qtbot, diag=None):
    diag = diag or _diag()
    page = LogsPage(diagnostics_service=diag)
    qtbot.addWidget(page)
    return page, diag


# ── Table population + live feed ──────────────────────────────────────────


def test_backfill_populates_table_from_deque(qtbot):
    diag = _diag()
    diag.log_event("info", "gui", "started")
    diag.log_event("warning", "sensor", "warn msg")
    page, _ = _page(qtbot, diag)
    assert page._table.rowCount() == 2


def test_live_append_grows_table(qtbot):
    page, diag = _page(qtbot)
    assert page._table.rowCount() == 0
    diag.log_event("info", "gui", "hello")
    assert page._table.rowCount() == 1
    assert page._table.item(0, 3).text() == "hello"


def test_level_cell_is_status_pill_with_state(qtbot):
    diag = _diag()
    diag.log_event("error", "gpu_ctrl", "boom")
    page, _ = _page(qtbot, diag)
    holder = page._table.cellWidget(0, 1)
    pill = holder.findChild(StatusPill)
    assert pill is not None
    assert pill.state() == "crit"
    assert pill.text() == "ERR"


def test_message_foreground_tracks_level(qtbot):
    diag = _diag()
    diag.log_event("error", "gpu", "failure")
    page, _ = _page(qtbot, diag)
    item = page._table.item(0, 3)
    assert item.foreground().color().name() == QColor(active_theme().status_crit).name()


# ── Filtering ──────────────────────────────────────────────────────────────


def test_toggles_filter_by_level(qtbot):
    diag = _diag()
    for lvl in ("info", "warning", "error"):
        diag.log_event(lvl, "s", f"{lvl} msg")
    page, _ = _page(qtbot, diag)
    assert page._table.rowCount() == 3
    page._toggle_error.setChecked(False)
    assert page._table.rowCount() == 2
    page._toggle_info.setChecked(False)
    page._toggle_warn.setChecked(False)
    assert page._table.rowCount() == 0  # all toggles off → empty


def test_search_filters_over_message_and_source(qtbot):
    diag = _diag()
    diag.log_event("info", "gui", "alpha")
    diag.log_event("info", "polling", "beta gamma")
    page, _ = _page(qtbot, diag)
    page._search_edit.setText("beta")
    assert page._table.rowCount() == 1
    assert page._rows[0].message == "beta gamma"
    page._search_edit.setText("polling")  # matches the source field
    assert page._table.rowCount() == 1


def test_clear_empties_table(qtbot):
    diag = _diag()
    diag.log_event("info", "g", "x")
    page, _ = _page(qtbot, diag)
    assert page._table.rowCount() == 1
    page._clear_btn.click()
    assert page._table.rowCount() == 0


# ── Log Inspector ──────────────────────────────────────────────────────────


def test_row_select_populates_inspector(qtbot):
    diag = _diag()
    diag.log_event("info", "polling", "detecting sensors")
    page, _ = _page(qtbot, diag)
    page._table.selectRow(0)
    assert not page._inspector_detail.isHidden()
    assert page._insp_source.text() == "polling"
    assert page._insp_pill.state() == "ok"
    assert page._insp_message.toPlainText() == "detecting sensors"
    assert page._insp_timestamp.text() == page._rows[0].detail_time_str


def test_selection_survives_live_append(qtbot):
    diag = _diag()
    diag.log_event("info", "polling", "first")
    diag.log_event("warning", "sensor", "second")
    page, _ = _page(qtbot, diag)
    page._table.selectRow(0)
    assert page._selected_vm.message == "first"
    diag.log_event("error", "gpu", "third")  # live append rebuilds the table
    assert page._table.rowCount() == 3
    assert page._selected_vm.message == "first"  # inspector selection preserved
    assert page._insp_message.toPlainText() == "first"


def test_no_context_data_field(qtbot):
    # DEC-210: DiagEvent carries no structured context, so the mockup's
    # "Context Data" JSON pane is dropped — no fabricated values.
    page, _ = _page(qtbot)
    for child in page.findChildren(QWidget):
        assert "context" not in child.objectName().lower()


def test_empty_inspector_before_selection(qtbot):
    page, _ = _page(qtbot)
    assert not page._inspector_empty.isHidden()
    assert page._inspector_detail.isHidden()


# ── Snapshot cards ─────────────────────────────────────────────────────────


def test_sync_snapshot_cards_fill_from_format(qtbot):
    page, diag = _page(qtbot)
    cases = [
        ("Logs_Btn_daemonStatus", page._daemon_preview, diag.format_daemon_status),
        ("Logs_Btn_controllerStatus", page._controller_preview, diag.format_controller_status),
        ("Logs_Btn_gpuStatus", page._gpu_preview, diag.format_gpu_status),
    ]
    for object_name, preview, fmt in cases:
        btn = page.findChild(QPushButton, object_name)
        assert btn is not None
        btn.click()
        assert preview.toPlainText() == fmt()
        assert preview.toPlainText()  # non-empty


def test_journal_worker_emits_fetched(qtbot):
    diag = _diag()
    diag.fetch_journal_entries = lambda: "CANNED JOURNAL"  # type: ignore[method-assign]
    worker = _JournalWorker(diag)
    with qtbot.waitSignal(worker.fetched, timeout=1000) as blocker:
        worker.do_fetch()
    assert blocker.args == ["CANNED JOURNAL"]


def test_journal_handler_updates_preview_and_reenables(qtbot):
    page, _ = _page(qtbot)
    page._journal_btn.setEnabled(False)
    page._on_journal_fetched("HELLO JOURNAL")
    assert page._journal_preview.toPlainText() == "HELLO JOURNAL"
    assert page._journal_btn.isEnabled()


def test_fetch_journal_shows_fetching_and_disables(qtbot):
    page, _ = _page(qtbot)
    page._ensure_journal_worker = lambda: None  # type: ignore[method-assign]  # no real thread
    page._fetch_journal()
    assert not page._journal_btn.isEnabled()
    assert page._journal_preview.toPlainText() == "Fetching…"


# ── Copy / theme / no-leak / teardown ──────────────────────────────────────


def test_copy_visible_rows_to_clipboard(qtbot):
    from PySide6.QtWidgets import QApplication

    diag = _diag()
    diag.log_event("info", "gui", "one")
    diag.log_event("warning", "sensor", "two")
    page, _ = _page(qtbot, diag)
    page._copy_btn.click()
    text = QApplication.clipboard().text()
    assert "[gui] one" in text
    assert "[sensor] two" in text
    assert "[INFO]" in text and "[WARN]" in text


def test_set_theme_rerenders(qtbot):
    diag = _diag()
    diag.log_event("info", "g", "x")
    page, _ = _page(qtbot, diag)
    page.set_theme(None)
    assert page._table.rowCount() == 1


def test_no_diagnostics_or_eventlog_objectnames_leak(qtbot):
    diag = _diag()
    diag.log_event("info", "g", "x")
    page, _ = _page(qtbot, diag)
    for child in page.findChildren(QWidget):
        name = child.objectName()
        assert not name.startswith("Diagnostics_"), name
        assert not name.startswith("EventLog_"), name


def test_cleanup_is_safe_without_worker(qtbot):
    page, _ = _page(qtbot)
    page.cleanup()  # never fetched → no worker/thread; must not raise
    assert page._journal_worker is None
    assert page._journal_thread is None


def test_cleanup_tears_down_active_worker(qtbot):
    diag = _diag()
    diag.fetch_journal_entries = lambda: "quick"  # type: ignore[method-assign]  # no subprocess
    page, _ = _page(qtbot, diag)
    page._fetch_journal()  # creates + starts the real worker thread
    assert page._journal_thread is not None
    page.cleanup()  # disconnect-first teardown must join the thread and not hang
    assert page._journal_worker is None
    assert page._journal_thread is None


def test_export_bundle_invokes_service(qtbot, monkeypatch, tmp_path):
    from pathlib import Path

    from PySide6.QtWidgets import QFileDialog

    diag = _diag()
    target = tmp_path / "bundle.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    called: list = []
    diag.export_support_bundle = lambda p: called.append(p)  # type: ignore[method-assign]
    page, _ = _page(qtbot, diag)
    page._export_btn.click()
    assert called == [Path(str(target))]


def test_export_bundle_cancel_is_noop(qtbot, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    diag = _diag()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    called: list = []
    diag.export_support_bundle = lambda p: called.append(p)  # type: ignore[method-assign]
    page, _ = _page(qtbot, diag)
    page._export_btn.click()
    assert called == []


def test_export_bundle_failure_surfaces_into_feed(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    diag = _diag()
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "b.json"), "")
    )

    def boom(_p):
        raise OSError("disk full")

    diag.export_support_bundle = boom  # type: ignore[method-assign]
    page, _ = _page(qtbot, diag)
    page._export_btn.click()
    assert page._table.rowCount() == 1  # the failure is logged as a visible event
    assert "export failed" in page._rows[0].message.lower()
