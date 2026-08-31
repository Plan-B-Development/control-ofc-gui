"""Logs page — the event feed, with alerts and detail available on demand.

A thin renderer over the Qt-free ``services.logs_view`` and ``services.alerts_view``
view-models, styled with the Stage-1 component library. Fed by the shared
``DiagnosticsService`` event feed (``event_appended`` / ``events_cleared``) — the same
deque every emitter writes to, so events logged anywhere appear here.

**Layout (DEC-282).** Compact alert bar → filter toolbar → full-width log table →
collapsed "Diagnostic tools". DEC-222 had put a permanent Active Warnings panel here
and DEC-210 a permanent Log Inspector beside the table; with the four DEC-234 snapshot
previews below it, seven areas competed for the page and the log table itself got about
half of it — while two of those areas were usually empty, one of them reserving a
quarter of the width to say "No active warnings."

Both permanent panels are gone. Alert detail opens in the scrim-backed
:class:`AlertCenterDialog`, restoring substantially the pre-DEC-222 arrangement where
warnings lived in a dialog; the inspector is a splitter child hidden until a row is
selected. DEC-210's selection-restore-by-frozen-VM-equality is **preserved** — a live
append must not clear the pane you are reading.

No daemon/API/schema change. The ``journalctl`` fetch still runs on a background thread
(``_JournalWorker``) so the 5 s subprocess cannot freeze the UI thread; its
disconnect-first teardown ordering in ``cleanup()`` is load-bearing and must not be
reordered.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.diagnostics_service import (
    JOURNAL_TIMEOUT_S,
    MAX_EVENTS,
    DiagnosticsService,
)
from control_ofc.services.logs_view import LogRowVM, build_log_row, build_log_rows, filter_log_rows
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.qt_util import block_signals, style_splitter
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.alert_center_dialog import AlertCenterDialog
from control_ofc.ui.widgets.alert_status_bar import AlertStatusBar
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

if TYPE_CHECKING:
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.app_state import AppState

log = logging.getLogger(__name__)

# Dropdown entry meaning "no source restriction" — `filter_log_rows` wants "".
_ALL_SOURCES = "All sources"

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

        # Poll/feed-driven state. `_all_rows` mirrors the service's own capped feed
        # (`DiagnosticsService._events`) and is bounded the same way and to the same
        # depth: a deque, so over-append is impossible by construction rather than by
        # a trim call nothing checks. Unbounded, it made every append quadratic in
        # session length — 145 ms per event at 1000 rows — because `_rebuild_table`
        # rebuilds every row.
        self._all_rows: deque[LogRowVM] = deque(maxlen=MAX_EVENTS)
        self._rows: list[LogRowVM] = []  # current filtered view (1:1 with table rows)
        self._selected_vm: LogRowVM | None = None
        self._suppress_selection = False
        # Entries that arrived while the user was reading older rows.
        self._pending_new = 0

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
        self._all_rows = deque(build_log_rows(self._diag.events), maxlen=MAX_EVENTS)
        self._rebuild_table()
        self._render_inspector()
        self._refresh_follow_indicator()

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
        if s.logs_source_filter:
            # The feed is rebuilt from a capped deque at startup, so the saved source
            # may not be among the current rows yet. Add it rather than dropping the
            # filter — _sync_source_choices keeps the selection if it is still valid.
            with block_signals(self._source_combo):
                if self._source_combo.findText(s.logs_source_filter) < 0:
                    self._source_combo.addItem(s.logs_source_filter)
                self._source_combo.setCurrentText(s.logs_source_filter)

    def _persist_log_filters(self) -> None:
        if self._settings_service is None:
            return
        self._settings_service.update(
            logs_level_filters=[lv for lv, b in self._level_toggles().items() if b.isChecked()],
            logs_search_text=self._search_edit.text(),
            logs_source_filter=self._selected_source(),
        )

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Alert bar → toolbar → full-width table → collapsible diagnostics (DEC-282).

        The page used to give permanent space to seven areas at once: the table, an
        Active Warnings panel, a Log Inspector, and four snapshot cards. Between them
        the log table — the reason the page exists — got about half the content area,
        and two of the panels were usually empty. Everything that is not the table is
        now either one line tall or opened on demand.
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        self._alert_bar = AlertStatusBar(self._state, object_name="Logs_AlertBar")
        self._alert_bar.view_alerts_clicked.connect(self.open_alert_center)
        outer.addWidget(self._alert_bar)

        outer.addLayout(self._build_toolbar())

        # The inspector is a splitter child that is hidden until a row is selected, so
        # closing it returns the full width to the table rather than leaving a reserved
        # empty column. Deliberately NOT a QStackedLayout: that lays out only its
        # current page, and reading geometry from a page never navigated to has twice
        # nearly shipped as data loss here.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("Logs_Splitter")
        splitter.addWidget(self._build_left_pane())
        self._inspector = self._build_inspector()
        splitter.addWidget(self._inspector)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setCollapsible(0, False)
        style_splitter(splitter)
        self._splitter = splitter
        self._inspector.setVisible(False)
        outer.addWidget(splitter, 1)

    def _build_toolbar(self) -> QHBoxLayout:
        """Search · severity · source · follow · actions (brief §10, §11).

        The Export Bundle button that used to sit here is gone. It called exactly the
        same method as the global footer's "Export Support Bundle" (still wired at
        ``MainWindow``), so the page carried a duplicate of an action already reachable
        from every page — and the toolbar needed the room for the source filter and the
        follow control. The export itself is unchanged.
        """
        row = QHBoxLayout()
        row.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("Logs_Edit_search")
        self._search_edit.setPlaceholderText("Search logs…")
        # Placeholder text is NOT an accessible name — Qt exposes it as a
        # description at best, and it vanishes the moment anything is typed,
        # so the field goes anonymous exactly when it holds state (273-g).
        name_value_control(self._search_edit, "Search logs")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(280)
        row.addWidget(self._search_edit)

        self._toggle_info = self._make_toggle("INFO", "Logs_Toggle_info")
        self._toggle_warn = self._make_toggle("WARN", "Logs_Toggle_warn")
        self._toggle_error = self._make_toggle("ERR", "Logs_Toggle_error")
        row.addWidget(self._toggle_info)
        row.addWidget(self._toggle_warn)
        row.addWidget(self._toggle_error)

        # Source filter. `filter_log_rows` already implemented exact-source matching;
        # the page simply never offered a way to set it. Populated from the sources
        # actually present in the feed rather than a hardcoded vocabulary, so a new
        # emitter appears here without anyone remembering to add it.
        self._source_combo = QComboBox()
        self._source_combo.setObjectName("Logs_Combo_source")
        self._source_combo.addItem(_ALL_SOURCES)
        name_value_control(self._source_combo, "Filter by source")
        row.addWidget(self._source_combo)

        # Follow. The auto-scroll behaviour already existed but was invisible: the tail
        # was followed only while the view happened to be at the bottom, with nothing
        # telling the user that was the rule. Now it is a state they can see and set.
        self._follow_btn = self._make_toggle("● Follow", "Logs_Toggle_follow")
        self._follow_btn.setChecked(True)
        name_value_control(self._follow_btn, "Follow new log entries")
        row.addWidget(self._follow_btn)

        self._new_events_btn = make_button(
            "", "ghost", object_name="Logs_Btn_newEvents", accessible_name="Jump to newest entries"
        )
        self._new_events_btn.setVisible(False)
        row.addWidget(self._new_events_btn)

        row.addStretch(1)

        self._clear_btn = make_button("Clear Logs", "secondary", object_name="Logs_Btn_clear")
        self._copy_btn = make_button("Copy", "secondary", object_name="Logs_Btn_copy")
        row.addWidget(self._clear_btn)
        row.addWidget(self._copy_btn)
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

        # DEC-282 replaces DEC-234's table↕snapshots drag handle with a collapsed
        # section. The handle let you trade table height for snapshot height, but it
        # started with the snapshots already taking about a third of the column — four
        # narrow monospace panes side by side, permanently, on a page whose job is the
        # log. Collapsed by default, the table simply gets the column.
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
        layout.addWidget(self._table, 1)

        self._diag_section = CollapsibleSection(
            "Diagnostic tools", "Logs_Section_diagnostics", expanded=False
        )
        self._diag_section.add_layout(self._build_snapshot_cards())
        layout.addWidget(self._diag_section)
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
        # Per-log preview — `heading` carries which log this is, so the name
        # distinguishes the several previews on this page (273-g).
        name_value_control(preview, heading)
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
        panel.setMinimumWidth(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 4, 4, 4)
        layout.setSpacing(8)

        # Header with a close affordance: the panel is on-demand now, so there has to
        # be a way back to the full-width table.
        head = QHBoxLayout()
        title = QLabel("Log detail")
        title.setProperty("class", "PageSubtitle")
        head.addWidget(title)
        head.addStretch(1)
        self._inspector_close = make_button(
            "✕", "ghost", object_name="Logs_Btn_inspectorClose", accessible_name="Close log detail"
        )
        head.addWidget(self._inspector_close)
        layout.addLayout(head)

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
        name_value_control(self._insp_message, "Raw message")
        self._insp_message.setReadOnly(True)
        self._insp_message.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        _mono(self._insp_message)
        detail.addWidget(self._insp_message, 1)

        self._insp_copy = make_button("Copy", "secondary", object_name="Logs_Btn_inspectorCopy")
        detail.addWidget(self._insp_copy)

        layout.addWidget(self._inspector_detail, 1)
        return panel

    def _connect_signals(self) -> None:
        self._search_edit.textChanged.connect(self._rebuild_table)
        self._toggle_info.toggled.connect(self._rebuild_table)
        self._toggle_warn.toggled.connect(self._rebuild_table)
        self._toggle_error.toggled.connect(self._rebuild_table)
        self._source_combo.currentTextChanged.connect(self._rebuild_table)
        self._source_combo.currentTextChanged.connect(lambda _t: self._persist_log_filters())
        self._follow_btn.toggled.connect(self._on_follow_toggled)
        self._new_events_btn.clicked.connect(self._resume_following)
        self._inspector_close.clicked.connect(self._close_inspector)
        self._insp_copy.clicked.connect(self._copy_selected)
        bar = self._table.verticalScrollBar()
        if bar is not None:
            bar.valueChanged.connect(self._on_table_scrolled)

        # DEC-245: an ERR-only filter used to revert to all-levels on every launch.
        # Toggles write immediately; the search box is debounced so a typed phrase
        # is one write rather than one per keystroke.
        self._search_edit.textChanged.connect(lambda _t: self._filter_write_timer.start())
        self._toggle_info.toggled.connect(self._persist_log_filters)
        self._toggle_warn.toggled.connect(self._persist_log_filters)
        self._toggle_error.toggled.connect(self._persist_log_filters)

        self._clear_btn.clicked.connect(self._diag.clear_events)
        self._copy_btn.clicked.connect(self._copy_visible)

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self._diag.event_appended.connect(self._on_event_appended)
        self._diag.events_cleared.connect(self._on_events_cleared)

    # ── Feed handlers ────────────────────────────────────────────────

    def _on_event_appended(self, ev) -> None:
        """Append, and follow the tail only if the user has not scrolled away.

        Following is now an explicit, visible state rather than an emergent one. The
        rule underneath is the same and deliberately kept: never yank someone back to
        the newest row while they are reading older ones. What changed is that the
        state is named, can be turned off, and that entries arriving while paused are
        counted so the user knows there is something to come back to.

        The table is updated **incrementally** (`_append_row`), never rebuilt. A full
        rebuild per event is O(rows) over a list that grows with events, i.e. quadratic
        in session length; it is reserved for a filter/search/theme change, where the
        whole view genuinely changes.
        """
        # The head about to be pushed out by `maxlen`, captured before the append
        # because afterwards it is unrecoverable. The bound is read off the deque
        # rather than restated as `== MAX_EVENTS`, so the two cannot disagree about
        # when an eviction happens (an unbounded deque has `maxlen is None`, which
        # compares unequal to any length and correctly evicts nothing).
        evicted = self._all_rows[0] if len(self._all_rows) == self._all_rows.maxlen else None
        vm = build_log_row(ev)
        self._all_rows.append(vm)
        following = self._follow_btn.isChecked() and self._is_at_bottom()
        self._append_row(vm, evicted)
        if following:
            self._scroll_to_bottom()
        else:
            self._pending_new += 1
        self._refresh_follow_indicator()

    def _on_events_cleared(self) -> None:
        self._all_rows.clear()
        self._selected_vm = None
        self._pending_new = 0
        self._rebuild_table()
        self._render_inspector()
        self._refresh_follow_indicator()

    # ── Follow / live tail ───────────────────────────────────────────

    def _on_follow_toggled(self, checked: bool) -> None:
        if checked:
            self._resume_following()
        else:
            self._refresh_follow_indicator()

    def _resume_following(self) -> None:
        self._follow_btn.setChecked(True)
        self._pending_new = 0
        self._scroll_to_bottom()
        self._refresh_follow_indicator()

    def _on_table_scrolled(self, _value: int) -> None:
        """Scrolling away pauses following; scrolling back to the bottom resumes it."""
        if self._is_at_bottom():
            self._pending_new = 0
        self._refresh_follow_indicator()

    def _refresh_follow_indicator(self) -> None:
        paused = not (self._follow_btn.isChecked() and self._is_at_bottom())
        self._follow_btn.setText("○ Follow paused" if paused else "● Follow")
        show_pending = paused and self._pending_new > 0
        if show_pending:
            noun = "event" if self._pending_new == 1 else "events"
            self._new_events_btn.setText(f"{self._pending_new} new {noun} ↓")
        self._new_events_btn.setVisible(show_pending)

    # ── Alert surfaces ───────────────────────────────────────────────

    def open_alert_center(self) -> None:
        """Open the on-demand Alert Centre (brief §8)."""
        if self._state is None:
            return
        dialog = AlertCenterDialog(self._state, self)
        dialog.show_related_logs.connect(self.show_related_logs)
        dialog.exec()

    def show_related_logs(self, source: str, component: str) -> None:
        """Narrow the table to an alert's context (brief §14).

        Deliberately simple. ``DiagEvent`` carries only a timestamp, level, source and
        message — there is no alert id to correlate on — so this filters to the alert's
        source and searches for its component, which is what the available data
        supports. It is a narrowing, not a guarantee that every row shown is related.
        """
        for btn in self._level_toggles().values():
            with block_signals(btn):
                btn.setChecked(True)
        idx = self._source_combo.findText(source)
        with block_signals(self._source_combo):
            self._source_combo.setCurrentIndex(idx if idx >= 0 else 0)
        with block_signals(self._search_edit):
            self._search_edit.setText(component if component and component != source else "")
        self._rebuild_table()
        self._persist_log_filters()

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

    def _selected_source(self) -> str:
        """The source filter, or "" for no restriction (what ``filter_log_rows`` wants)."""
        text = self._source_combo.currentText()
        return "" if text == _ALL_SOURCES else text

    def _sync_source_choices(self) -> None:
        """Keep the dropdown's options equal to the sources present in the feed.

        Rebuilt from the rows rather than a hardcoded list so a new emitter shows up
        without anyone having to remember this widget.

        The current selection is **always retained as an option even when no row
        carries it**. Two things make that necessary rather than merely tidy: the feed
        is a capped deque, so a source can age out of it while the user is still
        filtering on it; and a filter restored from settings at startup is applied
        before any matching event has been logged. Dropping it in either case would
        silently reset a setting the user chose — the DEC-245 failure — and the
        alternative it protects against (an unexplained empty table) does not arise,
        because the dropdown is right there still reading "fan".
        """
        sources = sorted({r.source for r in self._all_rows if r.source})
        current = self._source_combo.currentText()
        if current and current != _ALL_SOURCES and current not in sources:
            sources = sorted([*sources, current])
        if [self._source_combo.itemText(i) for i in range(self._source_combo.count())] == [
            _ALL_SOURCES,
            *sources,
        ]:
            return
        with block_signals(self._source_combo):
            self._source_combo.clear()
            self._source_combo.addItem(_ALL_SOURCES)
            self._source_combo.addItems(sources)
            idx = self._source_combo.findText(current)
            self._source_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _rebuild_table(self, *_args) -> None:
        """Re-derive the whole filtered view. For a filter/search/theme change only —
        a live append takes the O(1) `_append_row` path instead."""
        prev = self._selected_vm
        self._sync_source_choices()
        self._rows = filter_log_rows(
            self._all_rows,
            levels=self._active_levels(),
            source=self._selected_source(),
            search=self._search_edit.text(),
        )
        self._suppress_selection = True
        try:
            self._clear_cell_widgets(self._table, _COL_LEVEL)
            self._table.setRowCount(len(self._rows))
            theme = active_theme()
            muted = QColor(theme.text_secondary)
            for r, vm in enumerate(self._rows):
                self._paint_row(r, vm, theme, muted)
            # Restore selection by frozen-VM equality so a live append / refilter
            # never clears the inspector (DEC-210). A prev row filtered out keeps
            # the inspector showing its detail, only without a highlighted row.
            if prev is not None and prev in self._rows:
                self._table.selectRow(self._rows.index(prev))
        finally:
            self._suppress_selection = False

    def _append_row(self, vm: LogRowVM, evicted: LogRowVM | None) -> None:
        """Add one row and drop the evicted head, without rebuilding the table.

        Three things make this safe to do incrementally rather than by rebuild:

        - **The filter verdict comes from `filter_log_rows` itself**, over a
          one-element sequence, so the incremental path and the bulk path cannot
          drift into disagreeing about what is visible.
        - **`_suppress_selection` is load-bearing, and its absence is silent.**
          `removeRow(0)` emits `selectionChanged` *during* the removal carrying the
          pre-shift index (measured: selected row 3 → the signal reports 3, and only
          afterwards does Qt shift the highlight to row 2). `_on_selection_changed`
          would therefore re-read `self._rows` — already trimmed — at a stale index
          and silently move `_selected_vm` and the inspector one event forward while
          the highlight stayed put, breaking DEC-210's promise that the pane you are
          reading survives a live append.
        - **No explicit `removeCellWidget` is needed**: `removeRow` destroys the
          level column's pill holder with the row (measured after flushing
          `DeferredDelete` — without that flush the backlog looks like a leak).

        Qt shifts the highlight with the item for every row *except* the one being
        removed, where it re-anchors onto whatever slides into that index instead —
        so the boundary case is handled explicitly below rather than assumed.
        """
        self._sync_source_choices()
        matched = filter_log_rows(
            [vm],
            levels=self._active_levels(),
            source=self._selected_source(),
            search=self._search_edit.text(),
        )
        self._suppress_selection = True
        try:
            # `self._rows` preserves feed order, so an evicted row that is visible at
            # all is visible at index 0. Identity, not equality: two identical
            # messages in the same second produce equal VMs.
            if evicted is not None and self._rows and self._rows[0] is evicted:
                # Qt does not drop the highlight when the highlighted row is removed
                # — it re-anchors it onto the row that slides into that index. Left
                # alone, the highlight would move to the *next* event while
                # `_selected_vm` and the inspector (correctly) stay on the evicted
                # one, so the two would describe different events. Dropping the
                # highlight and keeping the detail is DEC-210's rule for a row that
                # leaves the view, and is what `_rebuild_table` does when the
                # selected row is filtered out.
                losing_highlight = self._selected_table_row() == 0
                del self._rows[0]
                self._table.removeRow(0)
                if losing_highlight:
                    self._table.clearSelection()
            if matched:
                r = self._table.rowCount()
                self._rows.append(vm)
                self._table.insertRow(r)
                theme = active_theme()
                self._paint_row(r, vm, theme, QColor(theme.text_secondary))
        finally:
            self._suppress_selection = False

    def _paint_row(self, r: int, vm: LogRowVM, theme, muted: QColor) -> None:
        """Render one row's cells. Shared by `_rebuild_table` and `_append_row` so an
        incrementally added row cannot be painted differently from a rebuilt one."""
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

    def _selected_table_row(self) -> int | None:
        """The highlighted table row, or None when nothing is highlighted."""
        rows = self._table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

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

    def _close_inspector(self) -> None:
        """Dismiss the detail pane and give the width back to the table."""
        self._selected_vm = None
        self._suppress_selection = True
        try:
            self._table.clearSelection()
        finally:
            self._suppress_selection = False
        self._render_inspector()

    def _copy_selected(self) -> None:
        vm = self._selected_vm
        if vm is None:
            return
        clip = QApplication.clipboard()
        if clip is not None:
            clip.setText(f"[{vm.detail_time_str}] [{vm.level_label}] [{vm.source}] {vm.message}")

    def _render_inspector(self) -> None:
        """Show the pane only while a row is selected (brief §12).

        Hiding the whole splitter child, rather than emptying it, is what returns the
        space: an empty-but-present panel is exactly the reserved dead width this
        redesign set out to remove.
        """
        vm = self._selected_vm
        self._inspector.setVisible(vm is not None)
        if vm is None:
            return
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
