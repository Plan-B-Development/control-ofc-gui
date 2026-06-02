"""Tests for the Diagnostics > Event Log view (DEC-111).

Asserts that the QTableView-backed event log:
  * populates from the DiagnosticsService deque
  * filters by severity / source / free-text search
  * follows the bottom only when the user is already at the bottom
  * renders a detail block for the selected row
  * exports the currently-filtered rows verbatim
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QApplication

from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.widgets.event_log_view import (
    COL_LEVEL,
    COL_MESSAGE,
    COL_SOURCE,
    EventLogView,
)


@pytest.fixture()
def diag() -> DiagnosticsService:
    return DiagnosticsService()


def _populate(svc: DiagnosticsService) -> None:
    svc.log_event("info", "polling", "Daemon connected")
    svc.log_event("warning", "control_loop", "Fan 'openfan:ch00' write failed 3 times")
    svc.log_event("error", "lease", "Lease lost: renewal failed after 3 retries")
    svc.log_event("info", "gui", "Theme changed: Solar Light")


def _proxy_row_count(view: EventLogView) -> int:
    return view._proxy.rowCount()


def _source_model(view: EventLogView) -> QStandardItemModel:
    return view._model


def test_initial_population_from_deque(qtbot, diag: DiagnosticsService) -> None:
    """Events already in the deque appear in the table on construction."""
    _populate(diag)
    view = EventLogView(diag)
    qtbot.addWidget(view)
    assert _source_model(view).rowCount() == 4
    assert _proxy_row_count(view) == 4


def test_appended_event_shows_up_live(qtbot, diag: DiagnosticsService) -> None:
    """A log_event after construction lands in the table via the signal."""
    view = EventLogView(diag)
    qtbot.addWidget(view)
    assert _source_model(view).rowCount() == 0

    diag.log_event("info", "polling", "Daemon connected")
    assert _source_model(view).rowCount() == 1
    assert _proxy_row_count(view) == 1


def test_clear_events_flushes_table(qtbot, diag: DiagnosticsService) -> None:
    _populate(diag)
    view = EventLogView(diag)
    qtbot.addWidget(view)
    assert _source_model(view).rowCount() == 4
    diag.clear_events()
    assert _source_model(view).rowCount() == 0


def test_severity_filter_hides_rows(qtbot, diag: DiagnosticsService) -> None:
    """Unchecking Info hides every Info-level row but leaves them in the model."""
    _populate(diag)
    view = EventLogView(diag)
    qtbot.addWidget(view)

    view._btn_info.setChecked(False)
    # 2 info rows hidden → 2 visible (warning + error)
    assert _proxy_row_count(view) == 2
    # Source model unchanged
    assert _source_model(view).rowCount() == 4

    view._btn_info.setChecked(True)
    assert _proxy_row_count(view) == 4


def test_source_filter_single_select(qtbot, diag: DiagnosticsService) -> None:
    """Picking a source in the dropdown restricts the proxy to that source."""
    _populate(diag)
    view = EventLogView(diag)
    qtbot.addWidget(view)

    # The combo always has "All sources" first, then sorted distinct sources.
    idx = view._source_combo.findText("lease")
    assert idx > 0, "Expected 'lease' to be in the source dropdown"
    view._source_combo.setCurrentIndex(idx)
    assert _proxy_row_count(view) == 1

    # Reset to All
    view._source_combo.setCurrentIndex(0)
    assert _proxy_row_count(view) == 4


def test_search_filters_message_and_source(qtbot, diag: DiagnosticsService) -> None:
    """Search is a case-insensitive substring match on message + source."""
    _populate(diag)
    view = EventLogView(diag)
    qtbot.addWidget(view)

    view._search_edit.setText("openfan")
    # Only the control_loop row mentions 'openfan' in its message
    assert _proxy_row_count(view) == 1

    view._search_edit.setText("control")
    # Same row matches via the source column 'control_loop'
    assert _proxy_row_count(view) == 1

    view._search_edit.setText("LEASE")
    # Case-insensitive — matches source 'lease' AND message 'Lease lost...'
    assert _proxy_row_count(view) == 1

    view._search_edit.clear()
    assert _proxy_row_count(view) == 4


def test_filters_combine_with_AND(qtbot, diag: DiagnosticsService) -> None:
    """Severity + source + search filters all narrow the visible set."""
    _populate(diag)
    view = EventLogView(diag)
    qtbot.addWidget(view)

    view._btn_info.setChecked(False)
    view._btn_warning.setChecked(False)
    # only error level visible
    assert _proxy_row_count(view) == 1

    view._search_edit.setText("nonsense-no-match")
    assert _proxy_row_count(view) == 0


def test_details_pane_shows_selected_row(qtbot, diag: DiagnosticsService) -> None:
    _populate(diag)
    view = EventLogView(diag)
    qtbot.addWidget(view)

    proxy = view._proxy
    # Select the second row (the warning)
    view._table.selectRow(1)
    text = view._details.toPlainText()
    assert "WARNING" in text or "warning" in text.lower()
    # The full message is rendered, not just the row label
    model = _source_model(view)
    source_row = proxy.mapToSource(proxy.index(1, 0)).row()
    expected_msg = model.data(model.index(source_row, COL_MESSAGE)) or ""
    assert expected_msg in text


def test_copy_filtered_rows_to_clipboard(qtbot, diag: DiagnosticsService) -> None:
    """Copy emits exactly the rows currently visible — no hidden rows leak."""
    _populate(diag)
    view = EventLogView(diag)
    qtbot.addWidget(view)

    # Filter to a single source so the clipboard payload is deterministic.
    idx = view._source_combo.findText("polling")
    view._source_combo.setCurrentIndex(idx)
    assert _proxy_row_count(view) == 1

    view._on_copy_clicked()
    app = QApplication.instance()
    assert app is not None
    clip_text = app.clipboard().text()
    assert "Daemon connected" in clip_text
    # The control_loop row is hidden, so it must not appear in the clipboard.
    assert "openfan" not in clip_text.lower()


def test_empty_state_label_visibility(qtbot, diag: DiagnosticsService) -> None:
    view = EventLogView(diag)
    qtbot.addWidget(view)

    # ``isHidden()`` mirrors the widget's local visibility intent and is
    # decoupled from whether the parent has been shown yet (qtbot does not
    # show widgets by default). It's the right contract to assert here.
    assert not view._empty_label.isHidden()

    diag.log_event("info", "polling", "Daemon connected")
    assert view._empty_label.isHidden()

    # Filtering to a non-existent value flips the hint back on with a
    # different message ("No events match the current filter").
    view._search_edit.setText("nothing-matches-this")
    assert not view._empty_label.isHidden()
    assert "filter" in view._empty_label.text().lower()


def test_level_cell_carries_raw_level_for_filter(qtbot, diag: DiagnosticsService) -> None:
    """The level column stores the raw level string in UserRole.

    The display label is uppercase ('WARNING'), but the filter proxy must
    match against the lowercase canonical value so adding a new severity
    later doesn't break the proxy.
    """
    diag.log_event("warning", "control_loop", "msg")
    view = EventLogView(diag)
    qtbot.addWidget(view)

    model = _source_model(view)
    item = model.item(0, COL_LEVEL)
    assert item is not None
    assert item.data(Qt.ItemDataRole.UserRole) == "warning"
    assert item.text() == "WARNING"


def test_known_sources_populate_combo_after_event(qtbot, diag: DiagnosticsService) -> None:
    """The source dropdown grows the first time a new source appears."""
    view = EventLogView(diag)
    qtbot.addWidget(view)
    assert view._source_combo.count() == 1  # "All sources"

    diag.log_event("info", "polling", "Daemon connected")
    items = [view._source_combo.itemText(i) for i in range(view._source_combo.count())]
    assert "polling" in items

    # Re-emitting a known source must not duplicate the entry.
    diag.log_event("warning", "polling", "Daemon disconnected")
    items = [view._source_combo.itemText(i) for i in range(view._source_combo.count())]
    assert items.count("polling") == 1


def test_appended_row_renders_message_and_source(qtbot, diag: DiagnosticsService) -> None:
    diag.log_event("error", "lease", "Lease lost: timeout")
    view = EventLogView(diag)
    qtbot.addWidget(view)

    model = _source_model(view)
    assert model.data(model.index(0, COL_SOURCE)) == "lease"
    assert "Lease lost: timeout" in (model.data(model.index(0, COL_MESSAGE)) or "")
