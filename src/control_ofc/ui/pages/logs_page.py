"""Logs page — a List + Inspector event workflow (DEC-314).

Three regions: a compact **activity strip** summarising volume over the retained feed,
a dense **event list**, and a tabbed **inspector** for the selected event. Find an event
on the left, understand it on the right.

**What changed from DEC-282, and why.** The page previously put a four-column table
across the full width with a "Diagnostic tools" section collapsed underneath and an
inspector that appeared only once a row was selected. Diagnostics were therefore a
bottom strip competing with the log, the four columns spent most of their width on
short fixed-size fields, and there was nowhere to show an event's structured metadata.
Now the diagnostics probes are inspector tabs (they are per-*session* context, not
per-event, so they live beside the event rather than under the list), the row is
painted as two lines with a severity edge, and the inspector is permanent because it
hosts Diagnostics and Journal as well as event detail. Its close button remains, so the
list can still be given the full width.

**Rendering is a thin pass over ``services.logs_view``.** Repeat collapsing, filtering,
facet counts, histogram bucketing and related-event correlation are all pure functions
there; this module decides only what to draw and when. The single derivation entry
point is :meth:`_refresh_view`, and there is exactly one — the previous page kept a
bulk rebuild *and* a hand-written incremental append, which is why it needed a seeded
fuzz test to prove the two agreed.

No daemon/API/schema change. The ``journalctl`` fetch still runs on a background thread
(``_JournalWorker``) so the 5 s subprocess cannot freeze the UI thread; its
disconnect-first teardown ordering in ``cleanup()`` is load-bearing and must not be
reordered.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from control_ofc.constants import PAGE_CONTROLS, PAGE_SYSTEM_STATE
from control_ofc.services.diagnostics_service import (
    JOURNAL_TIMEOUT_S,
    MAX_EVENTS,
    DiagnosticsService,
)
from control_ofc.services.logs_view import (
    LEVEL_ORDER,
    LogRowVM,
    RelatedEvents,
    bucket_window,
    build_log_row,
    build_log_rows,
    collapse_repeats,
    filter_log_rows,
    format_event_with_context,
    format_raw_record,
    format_row_line,
    histogram_buckets,
    index_for_window,
    level_counts,
    newest_first,
    related_rows,
    source_names,
    time_span,
)
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.qt_util import block_signals, style_splitter
from control_ofc.ui.widgets.activity_histogram import ActivityHistogram
from control_ofc.ui.widgets.alert_center_dialog import AlertCenterDialog
from control_ofc.ui.widgets.alert_status_bar import AlertStatusBar
from control_ofc.ui.widgets.log_event_model import LogEventModel
from control_ofc.ui.widgets.log_row_delegate import LogRowDelegate

if TYPE_CHECKING:
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.app_state import AppState

log = logging.getLogger(__name__)

# Dropdown entry meaning "no source restriction" — `filter_log_rows` wants "".
_ALL_SOURCES = "All sources"

# Inspector tab positions. Named because two of them load lazily and the indices are
# compared in `_on_tab_changed`; a bare 2 there would be unreadable and fragile.
_TAB_DETAILS = 0
_TAB_RAW = 1
_TAB_DIAGNOSTICS = 2
_TAB_JOURNAL = 3

# Sources whose events have a known, safe follow-up action (brief §8). Deliberately a
# small explicit map keyed on the emitter tag, not a heuristic over message text — the
# brief rules out building a parser to make buttons resemble the mock-up.
_CONTEXT_ACTIONS: dict[str, tuple[str, int]] = {
    "hwmon": ("Open Hardware", PAGE_SYSTEM_STATE),
    "openfan": ("Open Hardware", PAGE_SYSTEM_STATE),
    "gpu": ("Open Hardware", PAGE_SYSTEM_STATE),
    "profile": ("Open Controls", PAGE_CONTROLS),
}


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
    """Activity strip, event list, and a tabbed inspector."""

    # Main-thread → worker-thread request (queued). Class attribute so PySide6
    # binds it as a bound signal per instance.
    _journal_request = Signal()
    #: A contextual action asked to leave the page; MainWindow owns the stack.
    navigate_requested = Signal(int)

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

        # The uncollapsed feed, mirroring `DiagnosticsService._events` and bounded the
        # same way and to the same depth — a deque, so over-append is impossible by
        # construction rather than by a trim call nothing checks. Built once per event
        # rather than re-derived per keystroke: everything downstream of it is pure and
        # cheap, but `strftime` over 200 rows on every character typed is not.
        self._all_rows: deque[LogRowVM] = deque(maxlen=MAX_EVENTS)
        # Selection is keyed by stable event id, never by row position (brief §4).
        # `_selected_row` is kept even when the row is filtered out of view, so the
        # pane you are reading survives a filter change or a live append (DEC-210).
        self._selected_event_id: int | None = None
        self._selected_row: LogRowVM | None = None
        self._suppress_selection = False
        self._window: tuple[float, float] | None = None
        self._related: RelatedEvents | None = None
        # The collapsed feed, cached by `_refresh_view` for the inspector's correlation.
        self._collapsed: list[LogRowVM] = []
        self._pending_new = 0
        self._diag_loaded = False
        self._journal_loaded = False

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
        self._refresh_view()

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
        """Alert bar → activity strip → splitter(list | inspector) (brief §2)."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        self._alert_bar = AlertStatusBar(self._state, object_name="Logs_AlertBar")
        self._alert_bar.view_alerts_clicked.connect(self.open_alert_center)
        outer.addWidget(self._alert_bar)

        outer.addLayout(self._build_activity_strip())

        # Deliberately NOT a QStackedLayout anywhere on this page: that lays out only
        # its current page, and reading geometry from a page never navigated to has
        # twice nearly shipped as data loss here.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("Logs_Splitter")
        splitter.addWidget(self._build_left_pane())
        self._inspector = self._build_inspector()
        splitter.addWidget(self._inspector)
        # The list dominates and the inspector is substantial but secondary (brief
        # §1.2) — a *ratio*, never a fixed inspector width copied from the mock.
        #
        # **Stretch factors alone do not produce it, and silently did not.** A
        # QSplitter seeds each pane from its size hint, clamped to its minimum, and
        # then preserves that ratio on every resize; the stretch factor only governs
        # surplus. Left to itself the split seeded at the pane minimums — measured
        # 755:192 — and the inspector held 18-20% at *every* window size, less than the
        # 28% the pre-DEC-314 page shipped (`splitter_persistence` records the old
        # default as 820:320) on a redesign that makes the inspector more important,
        # not less. `setSizes` with values above both minimums is what actually sets
        # the ratio; the numbers are proportions, which QSplitter scales to the real
        # width, and DEC-245 then captures the laid-out result as this page's default
        # for "Reset layout". Same pattern as `controls_page.py` (setStretchFactor +
        # setSizes together).
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setCollapsible(0, False)
        # A floor so a drag cannot crush the inspector below what its own tabs ask
        # for. `max(...)` with the panel's own hint, deliberately: `setMinimumWidth`
        # *replaces* the computed minimum rather than raising a floor under it
        # (DEC-281), so taking the larger of the two is what makes this a raise.
        # The floor has to clear the **tab bar**, not the tab widget's sizeHint: the
        # latter measured 264px while the four tabs needed 310, so Qt fell back to
        # scroll arrows and rendered "Journal" as "Jo". A tab the user cannot see is
        # not a tab. `max(...)` over all three so this stays a raise, never a DEC-281
        # cap, and the layout's own left margin is included because the tabs sit
        # inside it.
        left_margin = self._inspector.layout().contentsMargins().left()
        self._inspector.setMinimumWidth(
            max(
                self._inspector.minimumSizeHint().width(),
                self._tabs.sizeHint().width(),
                self._tabs.tabBar().sizeHint().width() + left_margin,
            )
        )
        splitter.setSizes([900, 560])
        style_splitter(splitter)
        self._splitter = splitter
        outer.addWidget(splitter, 1)

    def _build_activity_strip(self) -> QVBoxLayout:
        """The activity overview and its time-window readout (brief §3)."""
        box = QVBoxLayout()
        box.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        caption = QLabel("ACTIVITY")
        caption.setProperty("class", "CardMeta")
        head.addWidget(caption)
        self._window_label = QLabel("All retained events")
        self._window_label.setObjectName("Logs_Label_window")
        self._window_label.setProperty("class", "SmallLabel")
        head.addWidget(self._window_label)
        head.addStretch(1)
        self._clear_window_btn = make_button(
            "Clear time filter", "ghost", object_name="Logs_Btn_clearWindow"
        )
        self._clear_window_btn.setVisible(False)
        head.addWidget(self._clear_window_btn)
        box.addLayout(head)

        self._histogram = ActivityHistogram(object_name="Logs_Histogram_activity")
        box.addWidget(self._histogram)
        return box

    def _build_left_pane(self) -> QWidget:
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(self._build_toolbar())

        self._model = LogEventModel(self)
        self._table = QTableView()
        self._table.setObjectName("Logs_Table_events")
        self._table.setModel(self._model)
        self._delegate = LogRowDelegate(self._table)
        self._table.setItemDelegate(self._delegate)
        apply_dense_table(self._table)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.setMouseTracking(True)  # the delegate paints a hover state
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        v_header = self._table.verticalHeader()
        v_header.setVisible(False)
        # **Load-bearing.** With the default `Interactive` mode a QTableView sizes every
        # row from `defaultSectionSize` and never asks the delegate at all — measured:
        # delegate sizeHint 45px, actual rowHeight 30px, so the two-line row rendered
        # with its meta line sliced in half. `ResizeToContents` is what makes the view
        # ask. Chosen over computing `setDefaultSectionSize` once because that value
        # would have to be recomputed on every theme change, and a number that must be
        # refreshed is the pinning-mechanism-nothing-checks trap; this cannot go stale.
        # Cost is bounded by MAX_EVENTS (200 rows) and measured below the noise floor.
        v_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header = self._table.horizontalHeader()
        # One column painted whole by the delegate — the row's internal layout is the
        # delegate's business, not a header's.
        header.setVisible(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # `name_value_control` deliberately covers only value-shaped controls; an item
        # view is not one, so it silently does nothing here. Qt's QAccessibleTable does
        # not fall through to the current item the way a non-editable QComboBox does
        # (DEC-269), so a plain accessible name is both sufficient and correct.
        self._table.setAccessibleName("Event list")
        layout.addWidget(self._table, 1)

        # Empty states (brief §12). A sibling of the table in the same box layout —
        # both are laid out, so neither ever carries phantom geometry.
        self._empty_label = QLabel("Waiting for events…")
        self._empty_label.setObjectName("Logs_Label_empty")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setProperty("class", "SmallLabel")
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label, 1)
        return pane

    def _build_toolbar(self) -> QHBoxLayout:
        """Search · severity chips · source · follow · actions (brief §5)."""
        row = QHBoxLayout()
        row.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("Logs_Edit_search")
        self._search_edit.setPlaceholderText("Search logs…")
        # Placeholder text is NOT an accessible name — Qt exposes it as a description
        # at best, and it vanishes the moment anything is typed, so the field goes
        # anonymous exactly when it holds state (273-g).
        name_value_control(self._search_edit, "Search logs")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(280)
        # A maximum without a minimum let the toolbar squeeze the page's primary
        # control to 108px inside a real window — it rendered as "Sea…".
        #
        # `Policy.Minimum` means "sizeHint is the floor, may grow", and QLineEdit's own
        # sizeHint is computed from the *current* font. So the floor tracks the theme's
        # font size with no number to recompute and nothing to go stale — which matters
        # here more than usual, because the first attempt at this WAS a font metric and
        # was still wrong: it was measured at construction, before the theme's font had
        # been applied, so it floored the field at the fallback font's width (DEC-303's
        # trap, in a timing coat rather than a portability one).
        self._search_edit.setSizePolicy(
            QSizePolicy.Policy.Minimum, self._search_edit.sizePolicy().verticalPolicy()
        )
        row.addWidget(self._search_edit)

        # "All" plus three INDEPENDENT severity toggles, not the mock's mutually
        # exclusive All|WARN|ERR triple. Three toggles can express "INFO only" and
        # "WARN+ERR", which the exclusive version cannot, and brief §15 forbids
        # regressing the existing INFO/WARN/ERR filtering. "All" is a shortcut that
        # checks all three, so the mock's one-click behaviour is preserved as well.
        self._all_btn = make_button("All", "ghost", object_name="Logs_Btn_levelAll")
        row.addWidget(self._all_btn)
        self._toggle_info = self._make_toggle("INFO", "Logs_Toggle_info")
        self._toggle_warn = self._make_toggle("WARN", "Logs_Toggle_warn")
        self._toggle_error = self._make_toggle("ERR", "Logs_Toggle_error")
        row.addWidget(self._toggle_info)
        row.addWidget(self._toggle_warn)
        row.addWidget(self._toggle_error)

        # Populated from the sources actually present in the feed rather than a
        # hardcoded vocabulary, so a new emitter appears here without anyone
        # remembering to add it.
        self._source_combo = QComboBox()
        self._source_combo.setObjectName("Logs_Combo_source")
        self._source_combo.addItem(_ALL_SOURCES)
        name_value_control(self._source_combo, "Filter by source")
        row.addWidget(self._source_combo)

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

    # ── Inspector ────────────────────────────────────────────────────

    def _build_inspector(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("Logs_Panel_inspector")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("Inspector")
        title.setProperty("class", "PageSubtitle")
        head.addWidget(title)
        head.addStretch(1)
        self._inspector_close = make_button(
            "✕", "ghost", object_name="Logs_Btn_inspectorClose", accessible_name="Close inspector"
        )
        head.addWidget(self._inspector_close)
        layout.addLayout(head)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("Logs_Tabs_inspector")
        self._tabs.addTab(self._build_details_tab(), "Details")
        self._tabs.addTab(self._build_raw_tab(), "Raw")
        self._tabs.addTab(self._build_diagnostics_tab(), "Diagnostics")
        self._tabs.addTab(self._build_journal_tab(), "Journal")
        layout.addWidget(self._tabs, 1)
        return panel

    @staticmethod
    def _scroller(inner: QWidget, object_name: str) -> QScrollArea:
        """Wrap a tab body so it scrolls independently when it outgrows the pane."""
        area = QScrollArea()
        area.setObjectName(object_name)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(inner)
        return area

    def _build_details_tab(self) -> QWidget:
        body = QWidget()
        detail = QVBoxLayout(body)
        detail.setContentsMargins(2, 8, 2, 2)
        detail.setSpacing(8)

        ls_row = QHBoxLayout()
        ls_row.setSpacing(8)
        self._insp_pill = StatusPill("—", "neutral", object_name="Logs_Pill_inspectorLevel")
        ls_row.addWidget(self._insp_pill)
        self._insp_source = QLabel("—")
        self._insp_source.setObjectName("Logs_Label_inspectorSource")
        ls_row.addWidget(self._insp_source)
        ls_row.addStretch(1)
        detail.addLayout(ls_row)

        self._timestamp_caption = _caption("Timestamp")
        detail.addWidget(self._timestamp_caption)
        self._insp_timestamp = QLabel("—")
        self._insp_timestamp.setObjectName("Logs_Label_inspectorTimestamp")
        self._insp_timestamp.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail.addWidget(self._insp_timestamp)

        # Repeat accounting (brief §6): count, first and most recent occurrence. Shown
        # only for a run — a single event has nothing to say here and a permanent
        # single-occurrence row would be noise.
        self._insp_repeat = QLabel("")
        self._insp_repeat.setObjectName("Logs_Label_inspectorRepeat")
        self._insp_repeat.setWordWrap(True)
        self._insp_repeat.setVisible(False)
        detail.addWidget(self._insp_repeat)

        self._message_caption = _caption("Message")
        detail.addWidget(self._message_caption)
        self._insp_message = QPlainTextEdit()
        self._insp_message.setObjectName("Logs_Text_inspectorMessage")
        name_value_control(self._insp_message, "Full message")
        self._insp_message.setReadOnly(True)
        self._insp_message.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        detail.addWidget(self._insp_message, 1)

        # Structured fields, rendered only when the event actually carries some
        # (brief §7.1 forbids empty placeholder rows).
        self._fields_caption = _caption("Fields")
        detail.addWidget(self._fields_caption)
        self._fields_panel = QWidget()
        self._fields_panel.setObjectName("Logs_Panel_inspectorFields")
        self._fields_grid = QGridLayout(self._fields_panel)
        self._fields_grid.setContentsMargins(0, 0, 0, 0)
        self._fields_grid.setHorizontalSpacing(10)
        self._fields_grid.setVerticalSpacing(2)
        self._fields_grid.setColumnStretch(1, 1)
        detail.addWidget(self._fields_panel)

        self._related_caption = _caption("Related")
        detail.addWidget(self._related_caption)
        self._related_label = QLabel("")
        self._related_label.setObjectName("Logs_Label_relatedTitle")
        self._related_label.setProperty("class", "SmallLabel")
        self._related_label.setWordWrap(True)
        detail.addWidget(self._related_label)
        self._related_list = QListWidget()
        self._related_list.setObjectName("Logs_List_related")
        self._related_list.setAccessibleName("Related events")
        self._related_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        detail.addWidget(self._related_list)
        self._filter_related_btn = make_button(
            "Filter to these", "ghost", object_name="Logs_Btn_filterRelated"
        )
        detail.addWidget(self._filter_related_btn)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._copy_event_btn = make_button(
            "Copy event + context", "secondary", object_name="Logs_Btn_copyEvent"
        )
        actions.addWidget(self._copy_event_btn)
        # Starts label-less and is filled only for a source with a known follow-up, so
        # it needs an explicit name for the moment before that (DEC-268). The name is
        # re-set alongside the text below: Qt's QAccessibleButton prefers an
        # accessibleName over the visible text, so a static generic one would
        # *replace* the good specific label rather than back it up.
        self._action_btn = make_button(
            "",
            "ghost",
            object_name="Logs_Btn_action",
            accessible_name="Open the page related to this event",
        )
        self._action_btn.setVisible(False)
        actions.addWidget(self._action_btn)
        actions.addStretch(1)
        detail.addLayout(actions)

        self._insp_empty = QLabel("Select an event to inspect")
        self._insp_empty.setObjectName("Logs_Label_inspectorEmpty")
        self._insp_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._insp_empty.setProperty("class", "SmallLabel")
        detail.addWidget(self._insp_empty, 1)

        # Everything that describes an event — **including its captions**. The two
        # section captions below were anonymous locals and so were never hidden with
        # the values they label, leaving a bare "TIMESTAMP / MESSAGE" stack above
        # "Select an event to inspect": exactly the empty placeholder rows brief §7.1
        # forbids.
        self._detail_widgets = [
            self._insp_pill,
            self._insp_source,
            self._timestamp_caption,
            self._insp_timestamp,
            self._message_caption,
            self._insp_message,
            self._fields_caption,
            self._fields_panel,
            self._related_caption,
            self._related_label,
            self._related_list,
            self._filter_related_btn,
            self._copy_event_btn,
        ]
        return self._scroller(body, "Logs_Scroll_details")

    def _build_raw_tab(self) -> QWidget:
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(2, 8, 2, 2)
        v.setSpacing(8)
        v.addWidget(
            _caption("Stored event record"),
        )
        self._raw_text = QPlainTextEdit()
        self._raw_text.setObjectName("Logs_Text_raw")
        name_value_control(self._raw_text, "Raw event record")
        self._raw_text.setReadOnly(True)
        self._raw_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._raw_text.setPlaceholderText("Select an event to inspect")
        _mono(self._raw_text)
        v.addWidget(self._raw_text, 1)
        self._copy_raw_btn = make_button("Copy", "secondary", object_name="Logs_Btn_copyRaw")
        v.addWidget(self._copy_raw_btn)
        return body

    def _build_diagnostics_tab(self) -> QWidget:
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(2, 8, 2, 2)
        v.setSpacing(10)
        self._daemon_preview = self._add_probe(
            v,
            "Daemon Status",
            "daemonStatus",
            lambda: self._fill_sync_probe(self._daemon_preview, self._diag.format_daemon_status),
        )
        self._controller_preview = self._add_probe(
            v,
            "Controller (OpenFan)",
            "controllerStatus",
            lambda: self._fill_sync_probe(
                self._controller_preview, self._diag.format_controller_status
            ),
        )
        self._gpu_preview = self._add_probe(
            v,
            "GPU State",
            "gpuStatus",
            lambda: self._fill_sync_probe(self._gpu_preview, self._diag.format_gpu_status),
        )
        return self._scroller(body, "Logs_Scroll_diagnostics")

    def _add_probe(self, box: QVBoxLayout, title: str, slug: str, handler) -> QPlainTextEdit:
        head = QHBoxLayout()
        heading = QLabel(title)
        heading.setProperty("class", "CardSubtitle")
        head.addWidget(heading)
        head.addStretch(1)
        btn = make_button("Refresh", "ghost", object_name=f"Logs_Btn_{slug}")
        btn.clicked.connect(handler)
        head.addWidget(btn)
        box.addLayout(head)

        preview = QPlainTextEdit()
        preview.setObjectName(f"Logs_Text_{slug}")
        # Per-probe name — `heading` carries which probe this is, so the name
        # distinguishes the several panes in this tab (273-g).
        name_value_control(preview, heading)
        preview.setReadOnly(True)
        preview.setMaximumBlockCount(2000)
        preview.setPlaceholderText("Not fetched yet.")
        _mono(preview)
        box.addWidget(preview, 1)
        return preview

    def _build_journal_tab(self) -> QWidget:
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(2, 8, 2, 2)
        v.setSpacing(8)
        head = QHBoxLayout()
        heading = QLabel("System Journal")
        heading.setProperty("class", "CardSubtitle")
        head.addWidget(heading)
        head.addStretch(1)
        self._journal_btn = make_button("Fetch", "ghost", object_name="Logs_Btn_systemJournal")
        head.addWidget(self._journal_btn)
        v.addLayout(head)

        self._journal_preview = QPlainTextEdit()
        self._journal_preview.setObjectName("Logs_Text_systemJournal")
        name_value_control(self._journal_preview, heading)
        self._journal_preview.setReadOnly(True)
        self._journal_preview.setMaximumBlockCount(2000)
        self._journal_preview.setPlaceholderText("Not fetched yet.")
        _mono(self._journal_preview)
        v.addWidget(self._journal_preview, 1)
        return body

    # ── Signals ──────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        self._search_edit.textChanged.connect(self._refresh_view)
        for btn in self._level_toggles().values():
            btn.toggled.connect(self._refresh_view)
            btn.toggled.connect(self._persist_log_filters)
        self._all_btn.clicked.connect(self._enable_all_levels)
        self._source_combo.currentTextChanged.connect(self._refresh_view)
        self._source_combo.currentTextChanged.connect(lambda _t: self._persist_log_filters())
        self._follow_btn.toggled.connect(self._on_follow_toggled)
        self._new_events_btn.clicked.connect(self._resume_following)
        self._inspector_close.clicked.connect(self._close_inspector)

        self._histogram.bucket_clicked.connect(self._on_bucket_clicked)
        self._histogram.capacity_changed.connect(self._refresh_view)
        self._clear_window_btn.clicked.connect(lambda: self._on_bucket_clicked(-1))

        self._copy_event_btn.clicked.connect(self._copy_event_with_context)
        self._copy_raw_btn.clicked.connect(self._copy_raw)
        self._filter_related_btn.clicked.connect(self._filter_to_related)
        self._related_list.itemActivated.connect(self._on_related_activated)
        self._related_list.itemClicked.connect(self._on_related_activated)
        self._action_btn.clicked.connect(self._run_context_action)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._journal_btn.clicked.connect(self._fetch_journal)

        # DEC-245: an ERR-only filter used to revert to all-levels on every launch.
        # Toggles write immediately; the search box is debounced so a typed phrase
        # is one write rather than one per keystroke.
        self._search_edit.textChanged.connect(lambda _t: self._filter_write_timer.start())

        self._clear_btn.clicked.connect(self._diag.clear_events)
        self._copy_btn.clicked.connect(self._copy_visible)

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        bar = self._table.verticalScrollBar()
        if bar is not None:
            bar.valueChanged.connect(self._on_table_scrolled)

        self._diag.event_appended.connect(self._on_event_appended)
        self._diag.events_cleared.connect(self._on_events_cleared)
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        """Brief §9, scoped to the event list rather than the page.

        ``WidgetShortcut`` on the table, not ``WidgetWithChildrenShortcut`` on the
        page: a page-wide single-letter shortcut fires while the user is typing in
        the search box, so "f" would toggle Follow instead of entering an "f". Bound
        to the list, the keys are live exactly when the list has focus, which is
        when they mean anything.
        """
        for key, slot in (
            ("/", self._focus_search),
            ("f", self._follow_btn.click),
            ("Esc", self._close_inspector),
        ):
            sc = QShortcut(QKeySequence(key), self._table)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(slot)

    def _focus_search(self) -> None:
        self._search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search_edit.selectAll()

    def _enable_all_levels(self) -> None:
        """The "All" chip: check every severity (brief §5)."""
        for btn in self._level_toggles().values():
            btn.setChecked(True)

    # ── Feed handlers ────────────────────────────────────────────────

    def _on_event_appended(self, ev) -> None:
        """Record the event and re-derive the view.

        Following is an explicit, visible state. The rule underneath is unchanged and
        deliberately kept: never yank someone back to the newest row while they are
        reading older ones. Entries arriving while paused are counted so the user
        knows there is something to come back to.
        """
        self._all_rows.append(build_log_row(ev))
        if not self._is_following():
            self._pending_new += 1
        self._refresh_view()

    def _on_events_cleared(self) -> None:
        self._all_rows.clear()
        self._selected_event_id = None
        self._selected_row = None
        self._pending_new = 0
        self._window = None
        self._histogram.set_selected_index(None)
        self._refresh_view()

    # ── The single derivation path ───────────────────────────────────

    def _refresh_view(self, *_args) -> None:
        """Re-derive every visible surface from the feed. The one entry point.

        Ordering matters and is the pipeline documented in ``services.logs_view``:
        collapse runs before filtering, and the display order is flipped exactly once,
        at the end.

        The three consumers are fed deliberately different slices:

        - the **chips** count rows that pass every filter *except* severity, or each
          chip's own count would drop to zero the moment it was unchecked;
        - the **histogram** buckets rows that pass every filter *except* the time
          window, so the selected window stays visible against the whole span, and it
          uses the **uncollapsed** rows so the bars show real event volume;
        - the **list** gets everything applied.
        """
        rows = list(self._all_rows)
        collapsed = collapse_repeats(rows)
        source, search = self._selected_source(), self._search_edit.text()
        levels = self._active_levels()
        all_levels = set(LEVEL_ORDER)

        self._sync_source_choices(collapsed)

        facet_rows = filter_log_rows(
            collapsed, levels=all_levels, source=source, search=search, window=self._window
        )
        self._render_level_counts(level_counts(facet_rows), len(facet_rows))

        hist_rows = filter_log_rows(rows, levels=levels, source=source, search=search, window=None)
        span = time_span(hist_rows)
        buckets = histogram_buckets(
            hist_rows,
            span=span,
            bucket_count=self._histogram.preferred_bucket_count(len(hist_rows)),
        )
        self._histogram.set_buckets(buckets)
        # Re-derived, not remembered: the boundaries move with the span (see
        # `index_for_window`).
        self._histogram.set_selected_index(index_for_window(buckets, self._window))

        visible = newest_first(
            filter_log_rows(
                collapsed, levels=levels, source=source, search=search, window=self._window
            )
        )
        self._collapsed = collapsed
        self._apply_rows(visible, collapsed, has_any=bool(rows))
        self._refresh_follow_indicator()

    def _apply_rows(
        self, visible: list[LogRowVM], collapsed: list[LogRowVM], *, has_any: bool
    ) -> None:
        """Push rows into the model, then re-anchor selection and scroll by identity.

        A model reset drops the view's selection and its scroll offset. Both are
        restored by **event id**, never by row number: under newest-first ordering
        every existing row's index changes whenever an event arrives, so an index is
        meaningless a moment after it is read.
        """
        anchor = None if self._is_following() else self._top_visible_event_id()

        self._suppress_selection = True
        try:
            self._model.set_rows(visible)
            # Keep the selected row's own data current — a collapsed run the user is
            # inspecting grows underneath them, and the inspector should say so.
            if self._selected_event_id is not None:
                fresh = next((r for r in collapsed if r.event_id == self._selected_event_id), None)
                if fresh is not None:
                    self._selected_row = fresh
            pos = (
                self._model.index_of_event(self._selected_event_id)
                if self._selected_event_id is not None
                else -1
            )
            if pos >= 0:
                self._table.selectRow(pos)
        finally:
            self._suppress_selection = False

        if self._is_following():
            self._table.scrollToTop()
        elif anchor is not None:
            at = self._model.index_of_event(anchor)
            if at >= 0:
                self._table.scrollTo(
                    self._model.index(at, 0), QAbstractItemView.ScrollHint.PositionAtTop
                )

        empty = not visible
        self._table.setVisible(not empty)
        self._empty_label.setVisible(empty)
        if empty:
            self._empty_label.setText(
                "No events match this filter" if has_any else "Waiting for events…"
            )
        self._render_inspector()

    def _render_level_counts(self, counts: dict[str, int], total: int) -> None:
        self._all_btn.setText(f"All  {total}")
        for level, btn in zip(LEVEL_ORDER, self._level_toggles().values(), strict=True):
            btn.setText(f"{_LEVEL_CHIP[level]}  {counts.get(level, 0)}")

    # ── Filter state ─────────────────────────────────────────────────

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

    def _sync_source_choices(self, rows: list[LogRowVM]) -> None:
        """Keep the dropdown's options equal to the sources present in the feed.

        The current selection is **always retained as an option even when no row
        carries it**. Two things make that necessary rather than merely tidy: the feed
        is a capped deque, so a source can age out of it while the user is still
        filtering on it; and a filter restored from settings at startup is applied
        before any matching event has been logged. Dropping it in either case would
        silently reset a setting the user chose — the DEC-245 failure.
        """
        sources = source_names(rows)
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

    def _on_bucket_clicked(self, index: int) -> None:
        """Apply (or clear) the histogram's time window (brief §3)."""
        if index < 0:
            self._window = None
            self._histogram.set_selected_index(None)
        else:
            self._window = bucket_window(self._histogram.buckets(), index)
            self._histogram.set_selected_index(index if self._window else None)
        self._clear_window_btn.setVisible(self._window is not None)
        self._window_label.setText(
            "All retained events" if self._window is None else _window_text(self._window)
        )
        self._refresh_view()

    # ── Follow / live tail (newest first: the tail is the TOP) ────────

    def _is_at_top(self) -> bool:
        bar = self._table.verticalScrollBar()
        return True if bar is None else bar.value() <= bar.minimum() + 2

    def _is_following(self) -> bool:
        return self._follow_btn.isChecked() and self._is_at_top()

    def _top_visible_event_id(self) -> int | None:
        idx = self._table.indexAt(self._table.viewport().rect().topLeft())
        row = self._model.row_at(idx.row()) if idx.isValid() else None
        return row.event_id if row is not None else None

    def _on_follow_toggled(self, checked: bool) -> None:
        if checked:
            self._resume_following()
        else:
            self._refresh_follow_indicator()

    def _resume_following(self) -> None:
        self._follow_btn.setChecked(True)
        self._pending_new = 0
        self._table.scrollToTop()
        self._refresh_follow_indicator()

    def _on_table_scrolled(self, _value: int) -> None:
        """Scrolling away pauses following; scrolling back to the tail resumes it."""
        if self._is_at_top():
            self._pending_new = 0
        self._refresh_follow_indicator()

    def _refresh_follow_indicator(self) -> None:
        paused = not self._is_following()
        self._follow_btn.setText("○ Follow paused" if paused else "● Follow")
        show_pending = paused and self._pending_new > 0
        if show_pending:
            noun = "event" if self._pending_new == 1 else "events"
            self._new_events_btn.setText(f"{self._pending_new} new {noun} ↑")
        self._new_events_btn.setVisible(show_pending)

    # ── Alert surfaces ───────────────────────────────────────────────

    def open_alert_center(self) -> None:
        """Open the on-demand Alert Centre."""
        if self._state is None:
            return
        dialog = AlertCenterDialog(self._state, self)
        dialog.show_related_logs.connect(self.show_related_logs)
        dialog.exec()

    def show_related_logs(self, source: str, component: str) -> None:
        """Narrow the list to an alert's context — the Alert Centre's entry point.

        Also used by the inspector's "Filter to these". Filters to the source and
        searches for the component, which is what the available data supports; it is
        a narrowing, not a guarantee that every row shown is related.
        """
        for btn in self._level_toggles().values():
            with block_signals(btn):
                btn.setChecked(True)
        idx = self._source_combo.findText(source)
        with block_signals(self._source_combo):
            self._source_combo.setCurrentIndex(idx if idx >= 0 else 0)
        with block_signals(self._search_edit):
            self._search_edit.setText(component if component and component != source else "")
        self._refresh_view()
        self._persist_log_filters()

    # ── Selection + inspector ────────────────────────────────────────

    def _on_selection_changed(self, *_args) -> None:
        if self._suppress_selection:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        row = self._model.row_at(rows[0].row())
        if row is not None:
            self._selected_event_id = row.event_id
            self._selected_row = row
            self._render_inspector()

    def _close_inspector(self) -> None:
        """Clear the selection and give the width back to the list."""
        self._selected_event_id = None
        self._selected_row = None
        self._suppress_selection = True
        try:
            self._table.clearSelection()
        finally:
            self._suppress_selection = False
        self._render_inspector()

    def _render_inspector(self) -> None:
        row = self._selected_row
        for w in self._detail_widgets:
            w.setVisible(row is not None)
        self._insp_empty.setVisible(row is None)
        if row is None:
            self._insp_repeat.setVisible(False)
            self._action_btn.setVisible(False)
            self._raw_text.setPlainText("")
            self._related = None
            return

        self._insp_pill.set_text(row.level_label)
        self._insp_pill.set_state(row.level_state)
        self._insp_source.setText(row.source or "—")
        self._insp_timestamp.setText(row.detail_time_str)
        self._insp_message.setPlainText(row.message)

        repeated = row.repeat_count > 1
        self._insp_repeat.setVisible(repeated)
        if repeated:
            self._insp_repeat.setText(
                f"Repeated {row.repeat_count} times — "
                f"first {row.first_time_str}, most recent {row.detail_time_str}"
            )

        self._render_fields(row)
        self._render_related(row)
        self._raw_text.setPlainText(format_raw_record(row))

        label_page = _CONTEXT_ACTIONS.get(row.source)
        self._action_btn.setVisible(label_page is not None)
        if label_page is not None:
            self._action_btn.setText(label_page[0])
            self._action_btn.setAccessibleName(label_page[0])

    def _render_fields(self, row: LogRowVM) -> None:
        _clear_grid(self._fields_grid)
        self._fields_caption.setVisible(bool(row.fields))
        self._fields_panel.setVisible(bool(row.fields))
        for r, (key, value) in enumerate(row.fields):
            name = QLabel(key)
            name.setProperty("class", "CardMeta")
            val = QLabel(value)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._fields_grid.addWidget(name, r, 0, Qt.AlignmentFlag.AlignTop)
            self._fields_grid.addWidget(val, r, 1)

    def _render_related(self, row: LogRowVM) -> None:
        # The collapsed feed `_refresh_view` already derived — correlating over a
        # second, independently recomputed collapse would be both wasted work and a
        # chance for the two to disagree about what a run is.
        self._related = related_rows(self._collapsed, row)
        self._related_list.clear()
        has = self._related is not None and bool(self._related.rows)
        self._related_caption.setVisible(self._related is not None)
        self._related_label.setVisible(self._related is not None)
        self._related_list.setVisible(has)
        self._filter_related_btn.setVisible(has)
        if self._related is None:
            return
        if not has:
            self._related_label.setText(f"{self._related.label} — no other events")
            return
        self._related_label.setText(self._related.label)
        for r in self._related.rows:
            item = QListWidgetItem(format_row_line(r))
            item.setData(Qt.ItemDataRole.UserRole, r.event_id)
            self._related_list.addItem(item)

    def _on_related_activated(self, item: QListWidgetItem) -> None:
        """Jump the list to a related event, if it is currently visible."""
        event_id = item.data(Qt.ItemDataRole.UserRole)
        pos = self._model.index_of_event(event_id)
        if pos >= 0:
            self._table.selectRow(pos)
            self._table.scrollTo(self._model.index(pos, 0))

    def _filter_to_related(self) -> None:
        if self._related is not None:
            self.show_related_logs(self._related.source, self._related.component)

    def _run_context_action(self) -> None:
        row = self._selected_row
        if row is None:
            return
        target = _CONTEXT_ACTIONS.get(row.source)
        if target is not None:
            self.navigate_requested.emit(target[1])

    # ── Copy actions ─────────────────────────────────────────────────

    @staticmethod
    def _to_clipboard(text: str) -> None:
        clip = QApplication.clipboard()
        if clip is not None:
            clip.setText(text)

    def _copy_event_with_context(self) -> None:
        if self._selected_row is not None:
            self._to_clipboard(format_event_with_context(self._selected_row, self._related))

    def _copy_raw(self) -> None:
        if self._selected_row is not None:
            self._to_clipboard(format_raw_record(self._selected_row))

    def _copy_visible(self) -> None:
        rows = self._model.rows()
        if rows:
            self._to_clipboard("\n".join(format_row_line(r) for r in rows))

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

    # ── Diagnostics + Journal tabs (lazy) ────────────────────────────

    def _on_tab_changed(self, index: int) -> None:
        """Fetch a probe tab's content on first activation only (brief §7.4/§7.5).

        Neither tab polls. Opening Logs must not spawn a ``journalctl`` subprocess or
        walk ``AppState`` for probes the user has not asked to see (brief §11), and
        re-selecting a tab must not silently re-run them either — the Refresh/Fetch
        buttons are how a stale probe is renewed.
        """
        if index == _TAB_DIAGNOSTICS and not self._diag_loaded:
            self._diag_loaded = True
            self._fill_sync_probe(self._daemon_preview, self._diag.format_daemon_status)
            self._fill_sync_probe(self._controller_preview, self._diag.format_controller_status)
            self._fill_sync_probe(self._gpu_preview, self._diag.format_gpu_status)
        elif index == _TAB_JOURNAL and not self._journal_loaded:
            self._journal_loaded = True
            self._fetch_journal()

    @staticmethod
    def _fill_sync_probe(preview: QPlainTextEdit, fetch_fn) -> None:
        preview.setPlainText(fetch_fn())

    def _fetch_journal(self) -> None:
        self._journal_loaded = True
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
        self._journal_btn.setText("Refresh")

    # ── Theme + teardown ─────────────────────────────────────────────

    def set_theme(self, _tokens) -> None:
        """Repaint the delegate-drawn surfaces; both read the live theme at paint time,
        so nothing needs re-plumbing — only a repaint request. The search field's floor
        needs nothing here either: it is Qt's own sizeHint, which already tracks the
        font."""
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.update()
        self._histogram.update()

    def cleanup(self) -> None:
        """Flush a pending filter write, then tear the journal worker + thread
        down race-free.

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


# Chip captions, keyed by raw level so the toggle text and the filter key cannot
# drift apart.
_LEVEL_CHIP: dict[str, str] = {"info": "INFO", "warning": "WARN", "error": "ERR"}


def _window_text(window: tuple[float, float]) -> str:
    start = time.strftime("%H:%M:%S", time.localtime(window[0]))
    end = time.strftime("%H:%M:%S", time.localtime(window[1]))
    return f"Filtered to {start} to {end}"


def _clear_grid(grid: QGridLayout) -> None:
    while grid.count():
        item = grid.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def _caption(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setProperty("class", "CardMeta")
    return label


def _mono(widget) -> None:
    font = widget.font()
    font.setFamily("monospace")
    widget.setFont(font)
