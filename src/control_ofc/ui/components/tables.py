"""Dense data-table styling helper (DEC-208)."""

from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView

from control_ofc.ui.qt_util import repolish


def apply_dense_table(table: QAbstractItemView) -> None:
    """Apply the dense ``.DenseTable`` look (sticky small header, tight rows).

    Works on QTableWidget / QTableView. Cosmetic only — the column model, sizing,
    and selection semantics remain the caller's responsibility.
    """
    table.setProperty("class", "DenseTable")
    table.setAlternatingRowColors(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    if hasattr(table, "setShowGrid"):
        table.setShowGrid(False)
    v_header = getattr(table, "verticalHeader", lambda: None)()
    if v_header is not None:
        v_header.setVisible(False)
    h_header = getattr(table, "horizontalHeader", lambda: None)()
    if h_header is not None:
        h_header.setHighlightSections(False)
    repolish(table)
