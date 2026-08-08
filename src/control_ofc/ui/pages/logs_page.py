"""Logs page — the event feed, a Log Inspector, and the active-warnings surface.

A thin renderer over the Qt-free ``services.logs_view`` view-models, styled with
the Stage-1 component library. Migrates the former Event Log tab (event stream +
Diagnostic Snapshots) into its own page and adds a right-hand Log Inspector for
the selected event. Fed by the shared ``DiagnosticsService`` event feed
(``event_appended`` / ``events_cleared``) — the same deque every emitter writes
to, so events logged anywhere appear here.

DEC-222 made this the single **active-warnings** surface too. The two are
different things and are shown as such: the event feed on the left is *history*,
while the Active Warnings panel on the right is ``AppState.active_warnings`` —
the dedup-keyed, dismissable set of what is wrong *right now*. It previously
opened as a dialog from the Dashboard status strip, which the Dashboard rebuild
removed.

Presentation-only (DEC-210/DEC-222): no daemon/API/schema change. The only
behavioural improvement is running the existing ``journalctl`` fetch on a
background thread (``_JournalWorker``) so the 5 s subprocess no longer freezes
the UI thread.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.diagnostics_service import JOURNAL_TIMEOUT_S, DiagnosticsService
from control_ofc.services.logs_view import LogRowVM, build_log_row, build_log_rows, filter_log_rows
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.qt_util import block_signals, style_splitter
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.warnings_view import WarningsView

if TYPE_CHECKING:
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.app_state import AppState

log = logging.getLogger(__name__)

_LOG_COLS = ["Time", "Level", "Source", "Message"]
_COL_TIME = 0
_COL_LEVEL = 1
_COL_SOURCE = 2
_COL_MESSAGE = 3


class _JournalWorker(QObject):
    """Runs the (blocking) journalctl subprocess off the UI thread.

    ``fetch_journal_entries`` only spawns a subprocess and formats strings — it
    touches no Qt objects or shared GUI state — so calling it from a worker
    thread is safe. The result comes back via a queued ``fetched`` signal.
    """

    fetched = Signal(str)

    def __init__(self, diag: DiagnosticsService) -> None:
        super().__init__()
        self._diag = diag

    @Slot()
    def do_fetch(self) -> None:
        self.fetched.emit(self._diag.fetch_journal_entries())


class LogsPage(QWidget):
    """The Logs page: filter toolbar, event table, snapshot cards, inspector."""

    # Main-thread → worker-thread request (queued). Class attribute so PySide6
    # binds it as a bound signal per instance.
    _journal_request = Signal()

    def __init__(
        self,
        diagnostics_service: DiagnosticsService,
        parent: QWidget | None = None,
        *,
        state: AppState | None = None,
        settings_service: AppSettingsService | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Logs_Root")
        self._diag = diagnostics_service
        self._state = state
        self._settings_service = settings_service

        # Poll/feed-driven state.
        self._all_rows: list[LogRowVM] = []
        self._rows: list[LogRowVM] = []  # current filtered view (1:1 with table rows)
        self._selected_vm: LogRowVM | None = None
        self._suppress_selection = False

        # Journal worker (lazy).
        self._journal_thread: QThread | None = None
        self._journal_worker: _JournalWorker | None = None

        # DEC-245: coalesce a typed search phrase into one settings write.
        self._filter_write_timer = QTimer(self)
        self._filter_write_timer.setObjectName("Logs_Timer_filterWrite")
        self._filter_write_timer.setSingleShot(True)
        self._filter_write_timer.setInterval(500)
        self._filter_write_timer.timeout.connect(self._persist_log_filters)

        self._build_ui()
        self._connect_signals()
        self._restore_log_filters()

        # Backfill from any events already in the deque (startup breadcrumbs).
        self._all_rows = build_log_rows(self._diag.events)
        self._rebuild_table()
        self._render_inspector()

    # ── Filter persistence (DEC-245) ─────────────────────────────────

    def _level_toggles(self) -> dict[str, QPushButton]:
        return {
            "info": self._toggle_info,
            "warn": self._toggle_warn,
            "error": self._toggle_error,
        }

    def _restore_log_filters(self) -> None:
        """Re-apply the saved filter, signals blocked so it does not write itself back."""
        if self._settings_service is None:
            return
        s = self._settings_service.settings
        enabled = set(s.logs_level_filters)
        for level, btn in self._level_toggles().items():
            with block_signals(btn):
                btn.setChecked(level in enabled)
        if s.logs_search_text:
            with block_signals(self._search_edit):
                self._search_edit.setText(s.logs_search_text)

    def _persist_log_filters(self) -> None:
        if self._settings_service is None:
            return
        self._settings_service.update(
            logs_level_filters=[lv for lv, b in self._level_toggles().items() if b.isChecked()],
            logs_search_text=self._search_edit.text(),
        )

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)
        outer.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("Logs_Splitter")
        splitter.addWidget(self._build_left_pane())
        splitter.addWidget(self._build_right_column())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([820, 320])
        style_splitter(splitter)
        outer.addWidget(splitter, 1)

    def _build_right_column(self) -> QWidget:
        """Active warnings over the log inspector (DEC-222).

        A vertical splitter rather than a fixed stack: the warnings list and the
        selected-event detail both want height, and which one matters depends on
        whether anything is currently wrong. The two are deliberately separate —
        warnings are current state, the inspector is one historical event.
        """
        column = QSplitter(Qt.Orientation.Vertical)
        column.setObjectName("Logs_Splitter_rightColumn")

        warnings_panel = QWidget()
        warnings_panel.setObjectName("Logs_Panel_warnings")
        warnings_layout = QVBoxLayout(warnings_panel)
        warnings_layout.setContentsMargins(12, 4, 4, 4)
        warnings_layout.setSpacing(8)
        warnings_layout.addWidget(
            SectionHeader("Active Warnings", object_name="Logs_SectionHeader_warnings")
        )
        self._warnings_view = WarningsView(self._state)
        self._warnings_view.setObjectName("Logs_View_warnings")
        warnings_layout.addWidget(self._warnings_view, 1)

        column.addWidget(warnings_panel)
        column.addWidget(self._build_inspector())
        column.setStretchFactor(0, 1)
        column.setStretchFactor(1, 1)
        column.setSizes([300, 380])
        style_splitter(column)
        return column

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("Logs_Edit_search")
        self._search_edit.setPlaceholderText("Filter messages…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(280)
        row.addWidget(self._search_edit)

        self._toggle_info = self._make_toggle("INFO", "Logs_Toggle_info")
        self._toggle_warn = self._make_toggle("WARN", "Logs_Toggle_warn")
        self._toggle_error = self._make_toggle("ERR", "Logs_Toggle_error")
        row.addWidget(self._toggle_info)
        row.addWidget(self._toggle_warn)
        row.addWidget(self._toggle_error)

        row.addStretch(1)

        self._clear_btn = make_button("Clear Logs", "secondary", object_name="Logs_Btn_clear")
        self._copy_btn = make_button("Copy", "secondary", object_name="Logs_Btn_copy")
        self._export_btn = make_button("Export Bundle", "primary", object_name="Logs_Btn_export")
        row.addWidget(self._clear_btn)
        row.addWidget(self._copy_btn)
        row.addWidget(self._export_btn)
        return row

    @staticmethod
    def _make_toggle(label: str, object_name: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName(object_name)
        btn.setCheckable(True)
        btn.setChecked(True)
        btn.setProperty("variant", "ghost")
        return btn

    def _build_left_pane(self) -> QWidget:
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Event table ↕ diagnostic-snapshot cards share this column's height
        # through a drag handle (DEC-234): pull it down to shrink the table and
        # grow the cards so more of a snapshot / journal is readable at once.
        # Both panes scroll internally, so the split can fill the column.
        column = QSplitter(Qt.Orientation.Vertical)
        column.setObjectName("Logs_Splitter_leftColumn")
        column.setChildrenCollapsible(False)

        self._table = QTableWidget(0, len(_LOG_COLS))
        self._table.setObjectName("Logs_Table_events")
        self._table.setHorizontalHeaderLabels(_LOG_COLS)
        apply_dense_table(self._table)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setMinimumHeight(150)
        _mono(self._table)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_TIME, QHeaderView.ResizeMode.ResizeToContents)
        # Level holds a pill cell-widget: ResizeToContents measures the (empty)
        # item, not the widget, so pin a fixed width wide enough for the pill.
        header.setSectionResizeMode(_COL_LEVEL, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(_COL_SOURCE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_MESSAGE, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(_COL_LEVEL, 92)
        column.addWidget(self._table)

        snapshots = QWidget()
        snapshots.setObjectName("Logs_Pane_snapshots")
        snapshots.setMinimumHeight(140)
        snap_layout = QVBoxLayout(snapshots)
        snap_layout.setContentsMargins(0, 8, 0, 0)
        snap_layout.setSpacing(10)
        snap_layout.addWidget(
            SectionHeader("Diagnostic Snapshots", object_name="Logs_SectionHeader_snapshots")
        )
        # Stretch the cards row so growing this pane grows the previews (the goal:
        # read more of a snapshot without opening the full bundle).
        snap_layout.addLayout(self._build_snapshot_cards(), 1)
        column.addWidget(snapshots)

        column.setStretchFactor(0, 1)
        column.setStretchFactor(1, 0)
        column.setSizes([420, 190])
        style_splitter(column)
        layout.addWidget(column, 1)
        return pane

    def _build_snapshot_cards(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        # Three cheap AppState-derived snapshots render synchronously; the
        # System Journal shells out to journalctl on a background thread.
        self._daemon_preview = self._add_snapshot_card(
            row,
            "Daemon Status",
            "daemonStatus",
            "Refresh",
            lambda: self._fill_sync_card(self._daemon_preview, self._diag.format_daemon_status),
        )
        self._controller_preview = self._add_snapshot_card(
            row,
            "Controller (OpenFan)",
            "controllerStatus",
            "Refresh",
            lambda: self._fill_sync_card(
                self._controller_preview, self._diag.format_controller_status
            ),
        )
        self._gpu_preview = self._add_snapshot_card(
            row,
            "GPU State",
            "gpuStatus",
            "Refresh",
            lambda: self._fill_sync_card(self._gpu_preview, self._diag.format_gpu_status),
        )
        self._journal_preview = self._add_snapshot_card(
            row,
            "System Journal",
            "systemJournal",
            "Fetch",
            self._fetch_journal,
        )
        return row

    def _add_snapshot_card(
        self, row: QHBoxLayout, title: str, slug: str, button_label: str, handler
    ) -> QPlainTextEdit:
        card = Card()
        card.setObjectName(f"Logs_Card_{slug}")
        v = QVBoxLayout(card)
        v.setSpacing(6)
        heading = QLabel(title)
        heading.setProperty("class", "CardSubtitle")
        v.addWidget(heading)

        preview = QPlainTextEdit()
        preview.setObjectName(f"Logs_Text_{slug}")
        preview.setReadOnly(True)
        preview.setMinimumHeight(90)
        preview.setMaximumBlockCount(2000)
        preview.setPlaceholderText("Not fetched yet.")
        _mono(preview)
        v.addWidget(preview, 1)

        btn = make_button(button_label, "ghost", object_name=f"Logs_Btn_{slug}")
        btn.clicked.connect(handler)
        v.addWidget(btn)
        # Keep the journal button so the fetch handler can disable it while busy.
        if slug == "systemJournal":
            self._journal_btn = btn
        row.addWidget(card, 1)
        return preview

    def _build_inspector(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("Logs_Panel_inspector")
        panel.setMinimumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 4, 4, 4)
        layout.setSpacing(8)

        title = QLabel("Log Inspector")
        title.setProperty("class", "PageSubtitle")
        layout.addWidget(title)

        self._inspector_empty = QLabel("Select an event to inspect its full detail.")
        self._inspector_empty.setObjectName("Logs_Label_inspectorEmpty")
        self._inspector_empty.setProperty("class", "CardMeta")
        self._inspector_empty.setWordWrap(True)
        layout.addWidget(self._inspector_empty)

        self._inspector_detail = QWidget()
        detail = QVBoxLayout(self._inspector_detail)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(6)

        detail.addWidget(_caption("Timestamp"))
        self._insp_timestamp = QLabel("—")
        self._insp_timestamp.setObjectName("Logs_Label_inspectorTimestamp")
        _mono(self._insp_timestamp)
        self._insp_timestamp.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail.addWidget(self._insp_timestamp)

        detail.addWidget(_caption("Level & Source"))
        ls_row = QHBoxLayout()
        ls_row.setSpacing(8)
        self._insp_pill = StatusPill("—", "neutral")
        self._insp_pill.setObjectName("Logs_Pill_inspectorLevel")
        ls_row.addWidget(self._insp_pill)
        self._insp_source = QLabel("—")
        self._insp_source.setObjectName("Logs_Label_inspectorSource")
        _mono(self._insp_source)
        ls_row.addWidget(self._insp_source)
        ls_row.addStretch(1)
        detail.addLayout(ls_row)

        detail.addWidget(_caption("Raw Message"))
        self._insp_message = QPlainTextEdit()
        self._insp_message.setObjectName("Logs_Text_inspectorMessage")
        self._insp_message.setReadOnly(True)
        self._insp_message.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        _mono(self._insp_message)
        detail.addWidget(self._insp_message, 1)

        layout.addWidget(self._inspector_detail, 1)
        return panel

    def _connect_signals(self) -> None:
        self._search_edit.textChanged.connect(self._rebuild_table)
        self._toggle_info.toggled.connect(self._rebuild_table)
        self._toggle_warn.toggled.connect(self._rebuild_table)
        self._toggle_error.toggled.connect(self._rebuild_table)

        # DEC-245: an ERR-only filter used to revert to all-levels on every launch.
        # Toggles write immediately; the search box is debounced so a typed phrase
        # is one write rather than one per keystroke.
        self._search_edit.textChanged.connect(lambda _t: self._filter_write_timer.start())
        self._toggle_info.toggled.connect(self._persist_log_filters)
        self._toggle_warn.toggled.connect(self._persist_log_filters)
        self._toggle_error.toggled.connect(self._persist_log_filters)

        self._clear_btn.clicked.connect(self._diag.clear_events)
        self._copy_btn.clicked.connect(self._copy_visible)
        self._export_btn.clicked.connect(self.export_bundle)

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self._diag.event_appended.connect(self._on_event_appended)
        self._diag.events_cleared.connect(self._on_events_cleared)

    # ── Feed handlers ────────────────────────────────────────────────

    def _on_event_appended(self, ev) -> None:
        was_at_bottom = self._is_at_bottom()
        self._all_rows.append(build_log_row(ev))
        self._rebuild_table()
        if was_at_bottom:
            self._scroll_to_bottom()

    def _on_events_cleared(self) -> None:
        self._all_rows = []
        self._selected_vm = None
        self._rebuild_table()
        self._render_inspector()

    # ── Table rendering ──────────────────────────────────────────────

    def _active_levels(self) -> set[str]:
        levels: set[str] = set()
        if self._toggle_info.isChecked():
            levels.add("info")
        if self._toggle_warn.isChecked():
            levels.add("warning")
        if self._toggle_error.isChecked():
            levels.add("error")
        return levels

    def _rebuild_table(self, *_args) -> None:
        prev = self._selected_vm
        self._rows = filter_log_rows(
            self._all_rows,
            levels=self._active_levels(),
            source="",
            search=self._search_edit.text(),
        )
        self._suppress_selection = True
        try:
            self._clear_cell_widgets(self._table, _COL_LEVEL)
            self._table.setRowCount(len(self._rows))
            theme = active_theme()
            muted = QColor(theme.text_secondary)
            for r, vm in enumerate(self._rows):
                _ensure_items(self._table, r, len(_LOG_COLS))
                time_item = self._table.item(r, _COL_TIME)
                time_item.setText(vm.time_str)
                time_item.setForeground(muted)
                src_item = self._table.item(r, _COL_SOURCE)
                src_item.setText(vm.source)
                src_item.setForeground(muted)
                msg_item = self._table.item(r, _COL_MESSAGE)
                msg_item.setText(vm.message)
                msg_item.setForeground(QColor(_message_color(theme, vm.level_state)))
                for col in (_COL_TIME, _COL_SOURCE, _COL_MESSAGE):
                    self._table.item(r, col).setToolTip(vm.message)
                self._set_pill(self._table, r, _COL_LEVEL, vm.level_label, vm.level_state)
            # Restore selection by frozen-VM equality so a live append / refilter
            # never clears the inspector (DEC-210). A prev row filtered out keeps
            # the inspector showing its detail, only without a highlighted row.
            if prev is not None and prev in self._rows:
                self._table.selectRow(self._rows.index(prev))
        finally:
            self._suppress_selection = False

    def _on_selection_changed(self, *_args) -> None:
        if self._suppress_selection:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self._rows):
            self._selected_vm = self._rows[idx]
            self._render_inspector()

    def _render_inspector(self) -> None:
        vm = self._selected_vm
        if vm is None:
            self._inspector_empty.setVisible(True)
            self._inspector_detail.setVisible(False)
            return
        self._inspector_empty.setVisible(False)
        self._inspector_detail.setVisible(True)
        self._insp_timestamp.setText(vm.detail_time_str)
        self._insp_pill.set_text(vm.level_label)
        self._insp_pill.set_state(vm.level_state)
        self._insp_source.setText(vm.source)
        self._insp_message.setPlainText(vm.message)

    # ── Auto-scroll (follow the tail only when already at the bottom) ─

    def _is_at_bottom(self) -> bool:
        bar = self._table.verticalScrollBar()
        if bar is None:
            return True
        return bar.value() >= bar.maximum() - 2

    def _scroll_to_bottom(self) -> None:
        bar = self._table.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    # ── Toolbar actions ──────────────────────────────────────────────

    def _copy_visible(self) -> None:
        if not self._rows:
            return
        text = "\n".join(
            f"[{r.time_str}] [{r.level_label}] [{r.source}] {r.message}" for r in self._rows
        )
        clip = QApplication.clipboard()
        if clip is not None:
            clip.setText(text)

    def export_bundle(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Support Bundle",
            "control_ofc_support_bundle.json",
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            self._diag.export_support_bundle(Path(path))
        except Exception as e:  # surface any failure into the visible event feed
            self._diag.log_event("error", "gui", f"Support bundle export failed: {str(e)[:80]}")

    # ── Snapshot cards ───────────────────────────────────────────────

    @staticmethod
    def _fill_sync_card(preview: QPlainTextEdit, fetch_fn) -> None:
        preview.setPlainText(fetch_fn())

    def _fetch_journal(self) -> None:
        self._journal_btn.setEnabled(False)
        self._journal_preview.setPlainText("Fetching…")
        self._ensure_journal_worker()
        self._journal_request.emit()

    def _ensure_journal_worker(self) -> None:
        if self._journal_worker is not None:
            return
        self._journal_thread = QThread(self)
        self._journal_worker = _JournalWorker(self._diag)
        self._journal_worker.moveToThread(self._journal_thread)
        # Main → worker and worker → main are both queued so nothing blocks the
        # UI thread and the result lands back on it.
        self._journal_request.connect(
            self._journal_worker.do_fetch, Qt.ConnectionType.QueuedConnection
        )
        self._journal_worker.fetched.connect(
            self._on_journal_fetched, Qt.ConnectionType.QueuedConnection
        )
        self._journal_thread.start()

    @Slot(str)
    def _on_journal_fetched(self, text: str) -> None:
        self._journal_preview.setPlainText(text)
        self._journal_btn.setEnabled(True)

    # ── Theme + teardown ─────────────────────────────────────────────

    def set_theme(self, _tokens) -> None:
        """Re-render the table (message colours + pills read the live theme) and
        the inspector so a theme switch is picked up on the next paint."""
        self._rebuild_table()
        self._render_inspector()

    def cleanup(self) -> None:
        """Flush a pending filter write, then tear the journal worker + thread
        down race-free (mirrors the Diagnostics ``_teardown_worker``
        disconnect-first ordering).

        The flush is DEC-245: a filter typed within the 500 ms debounce window is
        otherwise lost on close. No teardown hazard — the timer is parented to
        this page and a child QTimer of a destroyed parent never fires — so this
        is purely a lost-write fix, and it must precede the blocking
        ``QThread.wait`` below rather than follow it.
        """
        if getattr(self, "_filter_write_timer", None) is not None and (
            self._filter_write_timer.isActive()
        ):
            self._filter_write_timer.stop()
            self._persist_log_filters()

        if self._journal_worker is not None:
            QObject.disconnect(self._journal_worker, None, None, None)
        if self._journal_thread is not None:
            self._journal_thread.quit()
            # A fetch in flight blocks in subprocess.run for up to
            # JOURNAL_TIMEOUT_S (which then kills the journalctl child); the
            # queued quit() is only processed once do_fetch returns. Waiting the
            # full subprocess budget + margin lets the worker join cleanly on the
            # normal path — the old 2 s wait fell short of the 5 s timeout and
            # forced terminate(), orphaning the child. terminate() stays only as
            # a last-resort backstop.
            join_ms = int(JOURNAL_TIMEOUT_S * 1000) + 1500
            if not self._journal_thread.wait(join_ms):
                log.warning(
                    "Logs journal thread did not stop within %.1fs, terminating",
                    join_ms / 1000,
                )
                self._journal_thread.terminate()
                self._journal_thread.wait(1000)
        self._journal_worker = None
        self._journal_thread = None

    # ── Small helpers ────────────────────────────────────────────────

    @staticmethod
    def _clear_cell_widgets(table: QTableWidget, col: int) -> None:
        for row in range(table.rowCount()):
            table.removeCellWidget(row, col)

    @staticmethod
    def _set_pill(table: QTableWidget, row: int, col: int, text: str, state: str) -> None:
        # Left-align a compact pill in the column; both the holder and the pill
        # are mouse-transparent so row selection still resolves via indexAt()
        # (mirrors OverviewPage._set_pill).
        pill = StatusPill(text, state)
        pill.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        holder = QWidget()
        holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(0)
        lay.addWidget(pill)
        lay.addStretch(1)
        table.setCellWidget(row, col, holder)


def _message_color(theme, level_state: str) -> str:
    if level_state == "crit":
        return theme.status_crit
    if level_state == "warn":
        return theme.status_warn
    return theme.text_primary


def _caption(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setProperty("class", "CardMeta")
    return label


def _mono(widget) -> None:
    font = widget.font()
    font.setFamily("monospace")
    widget.setFont(font)


def _ensure_items(table: QTableWidget, row: int, ncols: int) -> None:
    for col in range(ncols):
        if table.item(row, col) is None:
            table.setItem(row, col, QTableWidgetItem())
