"""The "Show test evidence" disclosure under a fan-control test result (`WIRE-f`).

A thin renderer over :class:`control_ofc.services.verify_evidence.VerifyEvidence`:
the before/after table, collapsed by default, beneath a result whose one-line
summary is already on screen. Shared by System State (hwmon and GPU results) and
the Hardware page, so one test reads the same way wherever it was started.

Built on :class:`CollapsibleSection`, which already carries the themed header,
its focus ring and its keyboard toggle. The table is a grid of labels rather than
a ``QTableView`` — three short rows, and a label grid sizes to its content
without the view's row-height and column-width traps (DEC-314, DEC-363).
"""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from control_ofc.services.verify_evidence import VerifyEvidence
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

TITLE_SHOW = "Show test evidence"
TITLE_HIDE = "Hide test evidence"
COLUMN_BEFORE = "Before"
COLUMN_AFTER = "After"


class VerifyEvidencePanel(CollapsibleSection):
    """Collapsed before/after table; hidden entirely when there is no evidence."""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(TITLE_SHOW, object_name, expanded=False, parent=parent)
        self._table = QWidget()
        self._table.setObjectName(f"{object_name}_Table")
        self._grid = QGridLayout(self._table)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(4)
        self.add_widget(self._table)
        self._object_name = object_name
        self._cells: list[list[str]] = []
        self.toggled.connect(self._on_toggled)
        self.setVisible(False)

    # ── Public API ───────────────────────────────────────────────────

    def set_evidence(self, evidence: VerifyEvidence | None) -> None:
        """Show *evidence*'s rows, or hide the panel when there are none.

        A new result always starts collapsed: the summary above it has changed,
        and an open table from the previous result would sit under a verdict it
        does not describe until it repainted.
        """
        self._clear()
        rows = evidence.rows if evidence is not None else ()
        if not rows:
            self.setVisible(False)
            return
        self._add_row(0, ("", COLUMN_BEFORE, COLUMN_AFTER), header=True)
        for i, row in enumerate(rows, start=1):
            self._add_row(i, (row.label, row.before, row.after))
        self.set_expanded(False)
        self.set_title(TITLE_SHOW)
        self.setVisible(True)

    def cells(self) -> list[list[str]]:
        """The rendered table, header row first — what a reader of the panel sees."""
        return [list(r) for r in self._cells]

    # ── Internals ────────────────────────────────────────────────────

    def _on_toggled(self, expanded: bool) -> None:
        self.set_title(TITLE_HIDE if expanded else TITLE_SHOW)

    def _add_row(self, row: int, texts: tuple[str, str, str], *, header: bool = False) -> None:
        for col, text in enumerate(texts):
            label = QLabel(text)
            label.setObjectName(f"{self._object_name}_Cell_{row}_{col}")
            if header or col == 0:
                label.setProperty("class", "CardMeta")
            self._grid.addWidget(label, row, col)
        self._grid.setColumnStretch(2, 1)
        self._cells.append(list(texts))

    def _clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cells = []
