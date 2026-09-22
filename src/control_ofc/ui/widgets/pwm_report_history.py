"""The PWM Test Report window's "Reports" page (DEC-404 Stage 5, S5-1).

A list of saved reports — newest first — with Open, Export, Delete and Compare,
plus "Open a report file…" for a report from elsewhere.

Two kinds of row, and the difference is the point:

* **Saved** — a file in the reports folder. Delete removes it (D-b: only on the
  user's request, after a confirmation).
* **Opened from a file** (S5-6) — loaded for viewing and comparing only, and
  **never copied into the folder**, so another machine's report cannot be
  mistaken for one of this machine's. It stays in the list until the app exits
  and cannot be deleted from here — it is not ours to delete.

The report a run is writing right now is listed "In progress" and cannot be
opened, exported, compared or deleted until the run ends.

A thin renderer over ``services/pwm_report/store.py``; files are read with the
report's own reopen limit and schema check.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.paths import export_default_dir, reports_dir
from control_ofc.services.pwm_report import store
from control_ofc.services.pwm_report.view import REPORT_STATE_LABELS
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import SectionHeader
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.widgets.pwm_report_export import REPORT_FORMATS, attach_export_menu

_COLUMNS = ("Started (UTC)", "Machine", "Tests", "State", "Where")

SOURCE_SAVED = "This computer"
SOURCE_FILE = "A file (not saved here)"


@dataclass
class HistoryRow:
    entry: store.HistoryEntry
    imported: bool = False
    doc: dict | None = None


def _plain(text: str, object_name: str, *, meta: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if meta:
        label.setProperty("class", "CardMeta")
    return label


class PwmReportHistoryPage(QWidget):
    """The history list. Emits what the user asked for; the window acts."""

    #: ``(document, path, imported)``
    open_requested = Signal(object, object, bool)
    #: ``(document, document)``
    compare_requested = Signal(object, object)
    #: ``(document, format, imported)``
    export_requested = Signal(object, str, bool)

    def __init__(
        self,
        *,
        directory: Path | None = None,
        running_report_id: Callable[[], str] = str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PwmReport_Page_history")
        self._directory = directory
        self._running_report_id = running_report_id
        self._rows: list[HistoryRow] = []
        self._imported: list[HistoryRow] = []

        v = QVBoxLayout(self)
        v.setSpacing(10)
        v.addWidget(SectionHeader("Reports", object_name="PwmReport_Header_history"))
        v.addWidget(
            _plain(
                "Every report this computer has saved, newest first. Select one to open or "
                "export it, or two to compare them. Reports are never deleted "
                "automatically.",
                "PwmReport_Label_historyIntro",
            )
        )
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("PwmReport_Table_history")
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        apply_dense_table(self._table)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setMinimumHeight(260)
        self._table.itemSelectionChanged.connect(self._refresh_buttons)
        self._table.itemDoubleClicked.connect(lambda _item: self._open_selected())
        v.addWidget(self._table, 1)
        self._empty = _plain(
            "No reports yet. Start one with “New report”.", "PwmReport_Label_historyEmpty"
        )
        v.addWidget(self._empty)
        self._hint = _plain("", "PwmReport_Label_historyHint", meta=True)
        v.addWidget(self._hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.open_btn = make_button("Open", "primary", object_name="PwmReport_Btn_historyOpen")
        self.export_btn = make_button("Export", object_name="PwmReport_Btn_historyExport")
        attach_export_menu(
            self.export_btn,
            REPORT_FORMATS,
            self._export_selected,
            "PwmReport_Action_historyExport",
        )
        self.compare_btn = make_button("Compare", object_name="PwmReport_Btn_historyCompare")
        self.delete_btn = make_button(
            "Delete…", "danger", object_name="PwmReport_Btn_historyDelete"
        )
        self.import_btn = make_button(
            "Open a report file…", object_name="PwmReport_Btn_historyImport"
        )
        self.open_btn.clicked.connect(self._open_selected)
        self.compare_btn.clicked.connect(self._compare_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.import_btn.clicked.connect(self._import_file)
        for button in (self.open_btn, self.export_btn, self.compare_btn, self.delete_btn):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.import_btn)
        v.addLayout(row)
        self.refresh()

    # ── Rows ────────────────────────────────────────────────────────────────

    def rows(self) -> list[HistoryRow]:
        return list(self._rows)

    def refresh(self) -> None:
        saved = [HistoryRow(e) for e in store.list_reports(self._directory)]
        self._rows = [*self._imported, *saved]
        running = self._running_report_id()
        table = self._table
        table.clearSelection()
        table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            e = row.entry
            if e.error:
                state = "Unreadable"
            elif running and e.report_id == running and not row.imported:
                state = "In progress (running now)"
            else:
                state = REPORT_STATE_LABELS.get(e.state, (e.state or "—", ""))[0]
            values = (
                e.started_at or e.path.name,
                e.machine or "—",
                e.tests or "—",
                state,
                SOURCE_FILE if row.imported else SOURCE_SAVED,
            )
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if c == 0:
                    item.setToolTip(str(e.path))
                elif c == 3 and e.error:
                    item.setToolTip(e.error)
                table.setItem(r, c, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._empty.setVisible(not self._rows)
        self._table.setVisible(bool(self._rows))
        self._refresh_buttons()

    def select_rows(self, indexes: list[int]) -> None:
        """Select rows by index, as a user's Ctrl-click would."""
        self._table.clearSelection()
        mode = self._table.selectionMode()
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for i in indexes:
            self._table.selectRow(i)
        self._table.setSelectionMode(mode)

    def _selected(self) -> list[HistoryRow]:
        indexes = sorted({i.row() for i in self._table.selectionModel().selectedRows()})
        return [self._rows[i] for i in indexes if 0 <= i < len(self._rows)]

    def _is_running(self, row: HistoryRow) -> bool:
        running = self._running_report_id()
        return bool(running) and not row.imported and row.entry.report_id == running

    def _usable(self, row: HistoryRow) -> bool:
        return not row.entry.error and not self._is_running(row)

    def _refresh_buttons(self) -> None:
        sel = self._selected()
        one = len(sel) == 1 and self._usable(sel[0])
        self.open_btn.setEnabled(one)
        self.export_btn.setEnabled(one)
        self.compare_btn.setEnabled(len(sel) == 2 and all(self._usable(r) for r in sel))
        self.delete_btn.setEnabled(
            bool(sel) and all(not r.imported and not self._is_running(r) for r in sel)
        )
        hint = ""
        if any(r.imported for r in sel):
            hint = "A report opened from a file is not saved here, so it cannot be deleted here."
        elif any(self._is_running(r) for r in sel):
            hint = "That report is being written by the run in progress."
        elif len(sel) > 2:
            hint = "Compare takes exactly two reports."
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))

    def _load(self, row: HistoryRow) -> dict | None:
        if row.doc is not None:
            return row.doc
        try:
            doc, _repaired = store.load_report(row.entry.path)
        except Exception as e:  # repair re-derives findings over the file's content
            QMessageBox.warning(self, "Cannot open the report", f"{row.entry.path}: {e}")
            self.refresh()
            return None
        return doc

    # ── Actions ─────────────────────────────────────────────────────────────

    def _open_selected(self) -> None:
        sel = self._selected()
        if len(sel) != 1 or not self._usable(sel[0]):
            return
        doc = self._load(sel[0])
        if doc is not None:
            self.open_requested.emit(doc, sel[0].entry.path, sel[0].imported)

    def _export_selected(self, fmt: str) -> None:
        sel = self._selected()
        if len(sel) != 1 or not self._usable(sel[0]):
            return
        doc = self._load(sel[0])
        if doc is not None:
            self.export_requested.emit(doc, fmt, sel[0].imported)

    def _compare_selected(self) -> None:
        sel = self._selected()
        if len(sel) != 2 or not all(self._usable(r) for r in sel):
            return
        docs = [self._load(r) for r in sel]
        if docs[0] is not None and docs[1] is not None:
            self.compare_requested.emit(docs[0], docs[1])

    def _delete_selected(self) -> None:
        sel = [r for r in self._selected() if not r.imported and not self._is_running(r)]
        if not sel:
            return
        names = "\n".join(r.entry.path.name for r in sel)
        answer = QMessageBox.question(
            self,
            "Delete reports?",
            f"Permanently delete {len(sel)} saved report(s)? This cannot be undone.\n\n{names}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failed: list[str] = []
        for row in sel:
            try:
                store.delete_report(row.entry.path, self._directory)
            except (OSError, ValueError) as e:
                failed.append(f"{row.entry.path.name}: {e}")
        if failed:
            QMessageBox.warning(self, "Some reports were not deleted", "\n".join(failed))
        self.refresh()

    def _import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a PWM Test Report", str(export_default_dir()), "PWM Test Report (*.json)"
        )
        if not path:
            return
        self.import_path(Path(path))

    def import_path(self, path: Path) -> bool:
        """Load *path* for viewing (S5-6). Never writes, never copies."""
        try:
            doc, _repaired = store.load_report(path)
        except Exception as e:  # untrusted: repair re-derives findings over its content
            QMessageBox.warning(self, "Cannot open the file", f"{path}: {e}")
            return False
        folder = self._directory or reports_dir()
        if path.parent.resolve() == folder.resolve():
            # One of this computer's own saved reports, picked by file: it is
            # already in the list, so open it as the saved report it is.
            self.open_requested.emit(doc, path, False)
            return True
        self._imported = [r for r in self._imported if r.entry.path != path]
        self._imported.insert(0, HistoryRow(store.history_entry(path, doc), imported=True, doc=doc))
        self.refresh()
        self.open_requested.emit(doc, path, True)
        return True
