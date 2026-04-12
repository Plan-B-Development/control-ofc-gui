"""Warnings dialog — shows active warnings with clear action."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from control_ofc.services.app_state import AppState


class WarningsDialog(QDialog):
    """Modal dialog showing active warnings with a Clear All action."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Active Warnings")
        self.setMinimumSize(500, 300)
        self._state = state

        layout = QVBoxLayout(self)

        warnings = state.active_warnings
        if not warnings:
            empty = QLabel("No active warnings.")
            empty.setObjectName("WarningsDialog_Label_empty")
            empty.setProperty("class", "PageSubtitle")
            layout.addWidget(empty)
        else:
            self._table = QTableWidget(len(warnings), 4)
            self._table.setObjectName("WarningsDialog_Table_warnings")
            self._table.setHorizontalHeaderLabels(["Time", "Level", "Source", "Message"])
            self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            self._table.verticalHeader().setVisible(False)
            self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

            for i, w in enumerate(warnings):
                time_str = time.strftime("%H:%M:%S", time.localtime(w["timestamp"]))
                self._table.setItem(i, 0, QTableWidgetItem(time_str))
                self._table.setItem(i, 1, QTableWidgetItem(w.get("level", "warning")))
                self._table.setItem(i, 2, QTableWidgetItem(w.get("source", "")))
                self._table.setItem(i, 3, QTableWidgetItem(w.get("message", "")))

            layout.addWidget(self._table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        if warnings:
            clear_btn = QPushButton("Clear All Warnings")
            clear_btn.setObjectName("WarningsDialog_Btn_clearAll")
            clear_btn.clicked.connect(self._on_clear)
            btn_row.addWidget(clear_btn)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("WarningsDialog_Btn_close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _on_clear(self) -> None:
        self._state.clear_warnings()
        self.accept()
