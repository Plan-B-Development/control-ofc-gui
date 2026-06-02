"""Event Log table view with severity / source filters, search, details pane.

DEC-111: replaces the original ``QPlainTextEdit`` event view with a structured
table backed by a ``QStandardItemModel`` + ``QSortFilterProxyModel``. Lets the
user narrow ~200 in-process breadcrumbs by severity, source, and free-text
without re-rendering the underlying deque.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.diagnostics_service import DiagEvent, DiagnosticsService
from control_ofc.ui.theme import active_theme

if TYPE_CHECKING:
    from PySide6.QtCore import QObject

# Column indices for the event-log model. Kept as module-level constants so
# both the model build and the filter proxy reference one source of truth.
COL_TIME = 0
COL_LEVEL = 1
COL_SOURCE = 2
COL_MESSAGE = 3
COLUMN_COUNT = 4

# Display labels for the three severity levels. Matches the
# ``DiagEvent.level`` vocabulary exactly so filter membership tests can use
# the level string verbatim.
_LEVEL_LABEL: dict[str, str] = {
    "info": "INFO",
    "warning": "WARNING",
    "error": "ERROR",
}

_ALL_SOURCES_LABEL = "All sources"


def _level_color(level: str) -> str:
    """Return the foreground colour for a severity level.

    Reads ``active_theme`` on every call so a theme switch picks up the new
    status colours on the next repaint — matches the diagnostics page
    sensor-freshness pattern.
    """
    theme = active_theme()
    if level == "error":
        return theme.status_crit
    if level == "warning":
        return theme.status_warn
    if level == "info":
        return theme.status_ok
    return theme.text_primary


class _EventFilterProxy(QSortFilterProxyModel):
    """Custom proxy that filters rows by selected levels, sources, and search.

    A plain ``setFilterFixedString`` only filters one column at a time; we
    need to AND three independent filters (severity multi-select, source
    single-select, free-text substring) across two columns, so a custom
    ``filterAcceptsRow`` is required.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # All three levels active by default — the view shows everything
        # until the user opts out of a level.
        self._levels: set[str] = {"info", "warning", "error"}
        # Empty source filter = no source restriction (show every source).
        self._source_filter: str = ""
        self._search: str = ""

    def set_levels(self, levels: set[str]) -> None:
        self._levels = set(levels)
        self.invalidate()

    def set_source(self, source: str) -> None:
        # Empty string ⇒ no source restriction. Anything else must match
        # a row's source column exactly.
        self._source_filter = source
        self.invalidate()

    def set_search(self, search: str) -> None:
        self._search = search.strip().lower()
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return True
        level_idx = model.index(source_row, COL_LEVEL, source_parent)
        # DiagEvent.level is stored on the level cell as UserRole so the
        # filter doesn't have to lowercase the display label every time.
        level = model.data(level_idx, Qt.ItemDataRole.UserRole) or ""
        if level not in self._levels:
            return False

        if self._source_filter:
            src_idx = model.index(source_row, COL_SOURCE, source_parent)
            source = model.data(src_idx, Qt.ItemDataRole.DisplayRole) or ""
            if source != self._source_filter:
                return False

        if self._search:
            msg_idx = model.index(source_row, COL_MESSAGE, source_parent)
            src_idx = model.index(source_row, COL_SOURCE, source_parent)
            msg = model.data(msg_idx, Qt.ItemDataRole.DisplayRole) or ""
            source = model.data(src_idx, Qt.ItemDataRole.DisplayRole) or ""
            hay = f"{msg} {source}".lower()
            if self._search not in hay:
                return False

        return True


class EventLogView(QWidget):
    """Table-based event log with severity / source filters, search, details pane.

    The view is a thin wrapper around a ``QStandardItemModel`` fed by the
    ``DiagnosticsService`` deque. The widget never mutates the deque — it
    only displays it. ``DiagnosticsService.event_appended`` and
    ``events_cleared`` keep the table in sync.

    Auto-scroll follows the bottom only when the user is already at the
    bottom — scrolling up to read history freezes the view so new arrivals
    don't snap it back down (D4).
    """

    def __init__(
        self,
        diag: DiagnosticsService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._diag = diag

        self._model = QStandardItemModel(0, COLUMN_COUNT, self)
        self._model.setHorizontalHeaderLabels(["Time", "Level", "Source", "Message"])

        self._proxy = _EventFilterProxy(self)
        self._proxy.setSourceModel(self._model)

        self._build_ui()
        self._connect_signals()

        # Initial population from any events already in the deque (e.g.
        # startup event logged before the page was constructed).
        for ev in self._diag.events:
            self._append_row(ev)
        self._refresh_source_combo()

    # ─── UI construction ─────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Filter row 1 — severity toggles
        sev_row = QHBoxLayout()
        sev_row.setSpacing(8)
        sev_label = QLabel("Show:")
        sev_label.setProperty("class", "CardMeta")
        sev_row.addWidget(sev_label)

        self._btn_info = QPushButton("Info")
        self._btn_info.setObjectName("EventLog_Toggle_info")
        self._btn_info.setCheckable(True)
        self._btn_info.setChecked(True)
        sev_row.addWidget(self._btn_info)

        self._btn_warning = QPushButton("Warning")
        self._btn_warning.setObjectName("EventLog_Toggle_warning")
        self._btn_warning.setCheckable(True)
        self._btn_warning.setChecked(True)
        sev_row.addWidget(self._btn_warning)

        self._btn_error = QPushButton("Error")
        self._btn_error.setObjectName("EventLog_Toggle_error")
        self._btn_error.setCheckable(True)
        self._btn_error.setChecked(True)
        sev_row.addWidget(self._btn_error)

        sev_row.addStretch()
        layout.addLayout(sev_row)

        # Filter row 2 — source dropdown, search, auto-scroll, export, copy
        ctl_row = QHBoxLayout()
        ctl_row.setSpacing(8)

        src_label = QLabel("Source:")
        src_label.setProperty("class", "CardMeta")
        ctl_row.addWidget(src_label)

        self._source_combo = QComboBox()
        self._source_combo.setObjectName("EventLog_Combo_source")
        self._source_combo.setMinimumWidth(160)
        self._source_combo.addItem(_ALL_SOURCES_LABEL)
        ctl_row.addWidget(self._source_combo)

        search_label = QLabel("Search:")
        search_label.setProperty("class", "CardMeta")
        ctl_row.addWidget(search_label)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("EventLog_Edit_search")
        self._search_edit.setPlaceholderText("Filter messages and sources...")
        self._search_edit.setClearButtonEnabled(True)
        ctl_row.addWidget(self._search_edit, 1)

        self._auto_scroll_btn = QPushButton("Auto-scroll")
        self._auto_scroll_btn.setObjectName("EventLog_Toggle_autoScroll")
        self._auto_scroll_btn.setCheckable(True)
        self._auto_scroll_btn.setChecked(True)
        self._auto_scroll_btn.setToolTip(
            "When on, the view follows new events while you are at the bottom. "
            "Scroll up to pause; scroll back to the bottom to resume."
        )
        ctl_row.addWidget(self._auto_scroll_btn)

        self._export_btn = QPushButton("Export view...")
        self._export_btn.setObjectName("EventLog_Btn_export")
        self._export_btn.setToolTip("Save the currently-filtered events to a text file")
        ctl_row.addWidget(self._export_btn)

        self._copy_btn = QPushButton("Copy view")
        self._copy_btn.setObjectName("EventLog_Btn_copy")
        self._copy_btn.setToolTip("Copy the currently-filtered events to the clipboard")
        ctl_row.addWidget(self._copy_btn)

        layout.addLayout(ctl_row)

        # Table
        self._table = QTableView()
        self._table.setObjectName("EventLog_Table_events")
        self._table.setModel(self._proxy)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COL_TIME, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_LEVEL, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_MESSAGE, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, 1)

        # Details pane — short, read-only, monospace
        details_label = QLabel("Details")
        details_label.setProperty("class", "CardMeta")
        layout.addWidget(details_label)

        self._details = QPlainTextEdit()
        self._details.setObjectName("EventLog_Text_details")
        self._details.setReadOnly(True)
        self._details.setMaximumBlockCount(20)
        self._details.setFixedHeight(72)
        font = self._details.font()
        font.setFamily("monospace")
        self._details.setFont(font)
        self._details.setPlaceholderText("Select an event to see its full message here.")
        layout.addWidget(self._details)

        # Empty-state hint shown when the table has zero rows. Kept as a
        # second label rather than a placeholder so it survives theme
        # changes and is removable in tests via objectName.
        self._empty_label = QLabel("No events yet — GUI activity will appear here.")
        self._empty_label.setObjectName("EventLog_Label_empty")
        self._empty_label.setProperty("class", "CardMeta")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_label)
        self._empty_label.setVisible(self._model.rowCount() == 0)

    def _connect_signals(self) -> None:
        # Filter widgets
        self._btn_info.toggled.connect(self._on_filter_changed)
        self._btn_warning.toggled.connect(self._on_filter_changed)
        self._btn_error.toggled.connect(self._on_filter_changed)
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        self._search_edit.textChanged.connect(self._on_search_changed)

        # Action widgets
        self._export_btn.clicked.connect(self._on_export_clicked)
        self._copy_btn.clicked.connect(self._on_copy_clicked)

        # Selection → details pane
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # DiagnosticsService → table
        self._diag.event_appended.connect(self._on_event_appended)
        self._diag.events_cleared.connect(self._on_events_cleared)

    # ─── Model maintenance ───────────────────────────────────────────

    def _append_row(self, ev: DiagEvent) -> None:
        """Append a DiagEvent to the underlying model.

        The level column carries the raw level string in ``UserRole`` so the
        filter proxy can match without re-lowercasing the display label.
        """
        time_item = QStandardItem(ev.time_str)
        time_item.setData(ev.timestamp, Qt.ItemDataRole.UserRole)

        level_item = QStandardItem(_LEVEL_LABEL.get(ev.level, ev.level.upper()))
        level_item.setData(ev.level, Qt.ItemDataRole.UserRole)
        level_item.setForeground(QColor(_level_color(ev.level)))

        source_item = QStandardItem(ev.source)
        message_item = QStandardItem(ev.message)
        # Show full message text on hover for rows whose Message column is
        # clipped by the column width.
        message_item.setToolTip(ev.message)

        row = [time_item, level_item, source_item, message_item]
        for item in row:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._model.appendRow(row)

        # Enforce MAX_EVENTS — the deque drops the oldest entry first, so
        # the view mirrors that by dropping its top row when it grows past
        # the deque's cap. Without this the view could outgrow the deque
        # after exactly MAX_EVENTS+1 events (the deque drops one but the
        # view appends a fresh row).
        from control_ofc.services.diagnostics_service import MAX_EVENTS

        while self._model.rowCount() > MAX_EVENTS:
            self._model.removeRow(0)

    def _on_event_appended(self, ev: DiagEvent) -> None:
        was_at_bottom = self._is_at_bottom()
        before_known = set(self._known_sources_in_model())

        self._append_row(ev)

        # Source combo refresh — only when the dropdown gains a new value.
        # Avoid unconditional repopulation to keep the user's selection.
        if ev.source not in before_known:
            self._refresh_source_combo()

        self._update_empty_state()

        if self._auto_scroll_btn.isChecked() and was_at_bottom:
            self._scroll_to_bottom()

    def _on_events_cleared(self) -> None:
        self._model.removeRows(0, self._model.rowCount())
        self._details.clear()
        self._refresh_source_combo()
        self._update_empty_state()

    def _known_sources_in_model(self) -> list[str]:
        sources: set[str] = set()
        for row in range(self._model.rowCount()):
            idx = self._model.index(row, COL_SOURCE)
            value = self._model.data(idx, Qt.ItemDataRole.DisplayRole)
            if value:
                sources.add(str(value))
        return sorted(sources)

    def _refresh_source_combo(self) -> None:
        current = self._source_combo.currentText()
        self._source_combo.blockSignals(True)
        try:
            self._source_combo.clear()
            self._source_combo.addItem(_ALL_SOURCES_LABEL)
            for source in self._known_sources_in_model():
                self._source_combo.addItem(source)
            # Restore selection if still present, else fall back to "All".
            idx = self._source_combo.findText(current) if current else 0
            self._source_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._source_combo.blockSignals(False)

    # ─── Filter handlers ─────────────────────────────────────────────

    def _on_filter_changed(self) -> None:
        levels: set[str] = set()
        if self._btn_info.isChecked():
            levels.add("info")
        if self._btn_warning.isChecked():
            levels.add("warning")
        if self._btn_error.isChecked():
            levels.add("error")
        self._proxy.set_levels(levels)
        self._update_empty_state()

    def _on_source_changed(self, index: int) -> None:
        if index <= 0:
            self._proxy.set_source("")
        else:
            self._proxy.set_source(self._source_combo.itemText(index))
        self._update_empty_state()

    def _on_search_changed(self, text: str) -> None:
        self._proxy.set_search(text)
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        total_rows = self._model.rowCount()
        if total_rows == 0:
            self._empty_label.setText("No events yet — GUI activity will appear here.")
            self._empty_label.setVisible(True)
            return
        visible = self._proxy.rowCount()
        if visible == 0:
            self._empty_label.setText("No events match the current filter.")
            self._empty_label.setVisible(True)
        else:
            self._empty_label.setVisible(False)

    # ─── Selection / details ─────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            self._details.clear()
            return
        proxy_idx = indexes[0]
        source_idx = self._proxy.mapToSource(proxy_idx)
        row = source_idx.row()
        time_str = self._model.data(self._model.index(row, COL_TIME)) or ""
        level = self._model.data(self._model.index(row, COL_LEVEL)) or ""
        source = self._model.data(self._model.index(row, COL_SOURCE)) or ""
        message = self._model.data(self._model.index(row, COL_MESSAGE)) or ""
        text = f"[{time_str}] [{level}] [{source}]\n{message}"
        self._details.setPlainText(text)

    # ─── Auto-scroll ─────────────────────────────────────────────────

    def _is_at_bottom(self) -> bool:
        bar = self._table.verticalScrollBar()
        if bar is None:
            return True
        # A 2-pixel slack covers the case where the scrollbar hasn't quite
        # reached the maximum due to row-height rounding.
        return bar.value() >= bar.maximum() - 2

    def _scroll_to_bottom(self) -> None:
        bar = self._table.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    # ─── Export / copy ───────────────────────────────────────────────

    def _filtered_rows_as_text(self) -> str:
        lines: list[str] = []
        for proxy_row in range(self._proxy.rowCount()):
            source_idx = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
            row = source_idx.row()
            t = self._model.data(self._model.index(row, COL_TIME)) or ""
            lvl = self._model.data(self._model.index(row, COL_LEVEL)) or ""
            src = self._model.data(self._model.index(row, COL_SOURCE)) or ""
            msg = self._model.data(self._model.index(row, COL_MESSAGE)) or ""
            lines.append(f"[{t}] [{lvl:>7}] [{src}] {msg}")
        return "\n".join(lines)

    def _on_copy_clicked(self) -> None:
        text = self._filtered_rows_as_text()
        if not text:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    def _on_export_clicked(self) -> None:
        text = self._filtered_rows_as_text()
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Event Log",
            "control_ofc_event_log.txt",
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            # Match the rest of Diagnostics' file-error UX (no popup; the
            # caller can surface a status string). Silently ignoring is
            # consistent with the existing _export_bundle error handling
            # at the page level, which is the layer that owns user
            # messaging.
            pass

    # ─── Theme refresh ───────────────────────────────────────────────

    def refresh_theme(self) -> None:
        """Repaint level-cell foregrounds from the current active theme.

        Called by ``DiagnosticsPage.set_theme`` so a theme switch updates
        the severity colours without rebuilding the table.
        """
        for row in range(self._model.rowCount()):
            level_item = self._model.item(row, COL_LEVEL)
            if level_item is None:
                continue
            level = level_item.data(Qt.ItemDataRole.UserRole) or ""
            level_item.setForeground(QColor(_level_color(str(level))))
