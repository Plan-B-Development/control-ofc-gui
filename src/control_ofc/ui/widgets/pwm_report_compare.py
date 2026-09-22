"""The PWM Test Report window's comparison page (DEC-404 Stage 5).

A thin renderer over :class:`~control_ofc.services.pwm_report.compare.Comparison`:
the two runs side by side, the start conditions (shown, never judged — S5-5),
how the channels paired, then one table per category. Every string is plain
text (DEC-106) — labels and notes come from daemons and users.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.pwm_report.compare import (
    CATEGORY_NOT_COMPARABLE,
    CATEGORY_ORDER,
    CATEGORY_TITLES,
    Comparison,
    Difference,
)
from control_ofc.ui.components.cards import SectionHeader
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

_DIFF_COLUMNS = ("What", "Earlier", "Later", "Difference", "Note")


def _plain(text: str, object_name: str, *, meta: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if meta:
        label.setProperty("class", "CardMeta")
    return label


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


def _table(object_name: str, header: tuple[str, ...], rows: list[tuple[str, ...]]) -> QTableWidget:
    table = QTableWidget(len(rows), len(header))
    table.setObjectName(object_name)
    table.setHorizontalHeaderLabels(list(header))
    apply_dense_table(table)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            table.setItem(r, c, item)
    table.resizeColumnsToContents()
    table.horizontalHeader().setSectionResizeMode(len(header) - 1, QHeaderView.ResizeMode.Stretch)
    fit_height(table)
    return table


#: Above this a table scrolls inside the page instead of growing further.
MAX_TABLE_HEIGHT = 360


def fit_height(table: QTableWidget) -> None:
    """Make *table* exactly as tall as its rows (up to :data:`MAX_TABLE_HEIGHT`).

    A table inside a scrolling page otherwise keeps its default height and pads
    a short list. Called again once the page is shown, because the header and
    row heights Qt realises under the theme are not the ones it reports before
    (measured: one pixel short, which shows a scrollbar).
    """
    table.resizeRowsToContents()
    header = table.horizontalHeader()
    height = max(header.height(), header.sizeHint().height()) + 2 * table.frameWidth()
    height += sum(table.rowHeight(r) for r in range(table.rowCount()))
    fits = height <= MAX_TABLE_HEIGHT
    table.setVerticalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff if fits else Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    table.setFixedHeight(min(MAX_TABLE_HEIGHT, height))


def _diff_rows(rows: list[Difference]) -> list[tuple[str, ...]]:
    return [(r.subject, r.earlier, r.later, r.delta, r.note) for r in rows]


class PwmReportComparePage(QWidget):
    """Shows one comparison at a time; :meth:`show_comparison` replaces it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PwmReport_Page_compare")
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(10)
        self.comparison: Comparison | None = None

    def show_comparison(self, cmp: Comparison) -> None:
        self.comparison = cmp
        layout = self._layout
        _clear(layout)
        layout.addWidget(SectionHeader("Comparison", object_name="PwmReport_Header_compare"))
        e, lt = cmp.earlier, cmp.later
        layout.addWidget(
            _table(
                "PwmReport_Table_compareRuns",
                ("", "Earlier", "Later"),
                [
                    ("Report", e.report_id, lt.report_id),
                    ("Started (UTC)", e.started_at, lt.started_at),
                    ("Machine", e.machine, lt.machine),
                    ("State", e.state_label, lt.state_label),
                    (
                        "GUI / daemon",
                        f"{e.gui_version} / {e.daemon_version}",
                        f"{lt.gui_version} / {lt.daemon_version}",
                    ),
                ],
            )
        )
        layout.addWidget(
            _plain(
                "Differences are later minus earlier. No difference is judged significant or "
                "not: each measured value is shown beside the spread its own run recorded.",
                "PwmReport_Label_compareIntro",
                meta=True,
            )
        )
        layout.addWidget(_plain("Start conditions", "PwmReport_Label_compareStart"))
        layout.addWidget(
            _table("PwmReport_Table_compareStart", _DIFF_COLUMNS, _diff_rows(cmp.start_conditions))
        )
        lines = [f"Paired by stable id: {len(cmp.paired)} channel(s)."]
        if cmp.only_earlier:
            lines.append(
                "Only in the earlier report: "
                + ", ".join(f"{name} ({cid})" for cid, name in cmp.only_earlier)
            )
        if cmp.only_later:
            lines.append(
                "Only in the later report: "
                + ", ".join(f"{name} ({cid})" for cid, name in cmp.only_later)
            )
        layout.addWidget(_plain("\n".join(lines), "PwmReport_Label_comparePairing"))
        if cmp.possible_renames:
            layout.addWidget(
                _plain(
                    "Possibly renamed — same chip, device and PWM index, different label. "
                    "This is an inference, so these are not compared:\n"
                    + "\n".join(f"{a} → {b}" for a, b in cmp.possible_renames),
                    "PwmReport_Label_compareRenames",
                )
            )
        for category in CATEGORY_ORDER:
            rows = cmp.in_category(category)
            section = CollapsibleSection(
                f"{CATEGORY_TITLES[category]} ({len(rows)})",
                f"PwmReport_Section_compare_{category}",
                expanded=bool(rows),
            )
            same = cmp.same_counts.get(category, 0)
            if same and category != CATEGORY_NOT_COMPARABLE:
                section.add_widget(
                    _plain(
                        f"{same} other compared value(s) are the same in both reports.",
                        f"PwmReport_Label_compareSame_{category}",
                        meta=True,
                    )
                )
            if rows:
                section.add_widget(
                    _table(f"PwmReport_Table_compare_{category}", _DIFF_COLUMNS, _diff_rows(rows))
                )
            else:
                section.add_widget(
                    _plain("Nothing here.", f"PwmReport_Label_compareEmpty_{category}", meta=True)
                )
            layout.addWidget(section)
        layout.addStretch(1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for table in self.findChildren(QTableWidget):
            fit_height(table)
