"""DEC-210: LogsPage — event table, filters, inspector, snapshot cards, journal.

Constructs the page directly (like the Diagnostics-tab / Overview tests) on the
shared ``DiagnosticsService`` feed and drives its handlers. Avoids spinning the
event loop for the journal thread — the worker is unit-tested in isolation and
the page handler is driven directly.
"""

from __future__ import annotations

import re

import pytest
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


def test_snapshot_previews_cap_block_count(qtbot):
    """The diagnostic-snapshot previews cap at 2000 blocks so a large daemon
    snapshot can't grow the widget unbounded (audit 2026-07-15 Phase 5)."""
    page, _ = _page(qtbot)
    assert page._daemon_preview.maximumBlockCount() == 2000
    assert page._controller_preview.maximumBlockCount() == 2000


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


def test_inspector_is_absent_until_a_row_is_selected(qtbot):
    """DEC-282: the pane is hidden, not merely empty.

    It used to be a permanent column showing "Select an event to inspect its full
    detail." — roughly a fifth of the page width reserved to say nothing. Hiding the
    splitter child is what actually returns the width to the table, so that is what
    this asserts; an empty-but-present panel would pass an "is the detail blank?"
    check while still occupying the space.
    """
    page, _ = _page(qtbot)
    assert page._inspector.isHidden()


def test_selecting_a_row_opens_the_inspector_and_closing_returns_the_width(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("warning", "fan", "CPU_FAN stall detected")
    page._table.selectRow(0)
    assert not page._inspector.isHidden()
    assert page._insp_message.toPlainText() == "CPU_FAN stall detected"

    page._inspector_close.click()

    assert page._inspector.isHidden()
    assert page._selected_vm is None


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
    page.export_bundle()
    assert called == [Path(str(target))]


def test_export_bundle_cancel_is_noop(qtbot, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    diag = _diag()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    called: list = []
    diag.export_support_bundle = lambda p: called.append(p)  # type: ignore[method-assign]
    page, _ = _page(qtbot, diag)
    page.export_bundle()
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
    page.export_bundle()
    assert page._table.rowCount() == 1  # the failure is logged as a visible event
    assert "export failed" in page._rows[0].message.lower()


# ── Accessibility of the DEC-282 controls ──────────────────────────────────
# Two kinds of test are needed and this is the second one. An AST lint proves the
# `name_value_control` call was written; only a runtime sweep proves Qt actually
# announces a name — DEC-269 established that `setAccessibleName` alone is discarded
# on a non-editable QComboBox, and the helper written to encode that rule has already
# reintroduced the bug once for a case its unit tests did not cover.


def _announced_name(page, widget) -> str:
    """What a screen reader would actually read for *widget*.

    Three sources, in Qt's own order of precedence: an explicit accessible name, a
    buddy label (how ``name_value_control`` names a QComboBox, whose own
    ``setAccessibleName`` Qt discards when it is not editable — DEC-269), or, for a
    button, its visible text.

    A button's text only counts if it contains an actual word. A bare glyph announces
    as the glyph, which is DEC-268's point: "✕" is not a name.
    """
    from PySide6.QtWidgets import QAbstractButton, QLabel

    direct = widget.accessibleName()
    if direct:
        return direct
    for label in page.findChildren(QLabel):
        if label.buddy() is widget:
            return label.text() or label.accessibleName()
    if isinstance(widget, QAbstractButton) and re.search(r"[A-Za-z]{2,}", widget.text()):
        return widget.text()
    return ""


@pytest.mark.parametrize(
    "object_name",
    [
        "Logs_Edit_search",
        "Logs_Combo_source",
        "Logs_Toggle_follow",
        "Logs_Btn_newEvents",
        "Logs_Btn_inspectorClose",
    ],
)
def test_every_new_control_announces_a_name(qtbot, object_name):
    page, _ = _page(qtbot)
    widget = page.findChild(QWidget, object_name)
    assert widget is not None, f"{object_name} is missing from the page"
    name = _announced_name(page, widget)
    assert name.strip(), f"{object_name} announces as an anonymous control"


def test_glyph_only_controls_do_not_rely_on_their_glyph(qtbot):
    """A bare ✕ or a dynamically-filled label is not a name (DEC-268)."""
    page, _ = _page(qtbot)
    close = page.findChild(QWidget, "Logs_Btn_inspectorClose")
    assert close.text() == "✕"
    assert _announced_name(page, close) == "Close log detail"

    jump = page.findChild(QWidget, "Logs_Btn_newEvents")
    assert jump.text() == "", "starts empty — its label is filled only when paused"
    assert _announced_name(page, jump) == "Jump to newest entries"


def test_new_controls_have_unique_object_names(qtbot):
    page, _ = _page(qtbot)
    # Qt names its own internals (qt_scrollarea_viewport and friends) and reuses
    # those names freely; only our own objectNames are ours to keep unique.
    names = [
        w.objectName()
        for w in page.findChildren(QWidget)
        if w.objectName() and not w.objectName().startswith("qt_")
    ]
    duplicated = {n for n in names if names.count(n) > 1}
    assert not duplicated, f"objectName collisions break findChild/click tests: {duplicated}"


def _settings_service_with(**overrides):
    """A settings service seeded with *overrides* and unable to touch the real file.

    Follows the pattern in test_overview_page.py rather than constructing one and
    calling ``update()``: a default-constructed service still points ``save()`` at the
    user's actual config, and three tests once wiped the developer's settings that way
    (see AppSettingsService's docstring). Neutralising ``save`` is the guard.
    """
    from control_ofc.services.app_settings_service import AppSettings, AppSettingsService

    svc = AppSettingsService()
    svc._settings = AppSettings(**overrides)
    svc.save = lambda: None  # type: ignore[method-assign]
    return svc


# ── Source filter (DEC-282) ────────────────────────────────────────────────


def test_source_dropdown_is_built_from_the_sources_actually_present(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "polling", "connected")
    diag.log_event("warning", "fan", "stall")
    diag.log_event("info", "polling", "again")

    options = [page._source_combo.itemText(i) for i in range(page._source_combo.count())]

    assert options == ["All sources", "fan", "polling"], "deduplicated and sorted"


def test_selecting_a_source_narrows_the_table(qtbot):
    page, diag = _page(qtbot)
    diag.log_event("info", "polling", "connected")
    diag.log_event("warning", "fan", "stall")
    assert len(page._rows) == 2

    page._source_combo.setCurrentText("fan")

    assert [r.source for r in page._rows] == ["fan"]


def test_a_restored_source_filter_survives_having_no_matching_rows_yet(qtbot):
    """Regression: the filter used to be silently reset to "All sources".

    `_sync_source_choices` rebuilds the options from the rows in the feed, and the feed
    is a capped deque that at startup holds only a handful of breadcrumbs. A filter
    restored from settings therefore names a source with no rows behind it — and
    dropping it discards a setting the user deliberately chose (the DEC-245 failure).
    """
    settings = _settings_service_with(logs_source_filter="fan")
    page = LogsPage(diagnostics_service=DiagnosticsService(), settings_service=settings)
    qtbot.addWidget(page)

    assert page._source_combo.currentText() == "fan"
    assert page._selected_source() == "fan"
    assert page._rows == [], "nothing matches yet, and the dropdown says why"

    # And it still narrows correctly once a matching row does arrive.
    page._diag.log_event("warning", "fan", "stall")
    assert [r.source for r in page._rows] == ["fan"]
    assert page._source_combo.currentText() == "fan"
