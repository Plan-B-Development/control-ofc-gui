"""Compact alert status bar for the Logs page (DEC-282, brief §7).

Replaces the permanent right-hand Active Warnings panel, which reserved roughly a
quarter of the page width to say "No active warnings." for as long as nothing was
wrong. This says the same thing in one line, and grows only when there is something to
grow about.

A thin renderer over ``services/alerts_view.AlertStatusVM``; it subscribes to
``AppState.warnings_changed`` — the content signal, not a count — so a condition
resolving as another activates repaints it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from control_ofc.services.alerts_view import build_status_vm
from control_ofc.ui.components.buttons import make_button

if TYPE_CHECKING:
    from control_ofc.services.app_state import AppState


class AlertStatusBar(QFrame):
    """One-line alert summary with a View alerts affordance."""

    view_alerts_clicked = Signal()

    def __init__(
        self,
        state: AppState | None,
        parent: QWidget | None = None,
        *,
        object_name: str = "AlertStatusBar_Root",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._state = state

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._summary = QLabel()
        self._summary.setObjectName(f"{object_name}_summary")
        self._summary.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self._summary)

        self._headline = QLabel()
        self._headline.setObjectName(f"{object_name}_headline")
        self._headline.setTextFormat(Qt.TextFormat.PlainText)
        self._headline.setProperty("class", "CardMeta")
        # Elide rather than force the page wider: the log table owns the width, and a
        # long sensor label must not be able to take it (brief §22).
        self._headline.setMinimumWidth(0)
        row.addWidget(self._headline, 1)

        self._view_btn = make_button(
            "View alerts", "secondary", object_name=f"{object_name}_viewBtn"
        )
        self._view_btn.clicked.connect(self.view_alerts_clicked)
        row.addWidget(self._view_btn)
        outer.addLayout(row)

        # Shown only when nothing is active but something recently recovered — brief
        # §24's "Recent alert: … recovered at 09:21:06".
        self._recent = QLabel()
        self._recent.setObjectName(f"{object_name}_recent")
        self._recent.setTextFormat(Qt.TextFormat.PlainText)
        self._recent.setProperty("class", "CardMeta")
        outer.addWidget(self._recent)

        if self._state is not None:
            self._state.warnings_changed.connect(self.refresh)

        self.refresh()

    def refresh(self) -> None:
        if self._state is None:
            vm = build_status_vm([], [])
        else:
            vm = build_status_vm(self._state.alerts.present(), self._state.alerts.recovered())
        self._vm = vm

        self._summary.setText(vm.summary)
        self._summary.setProperty("class", _SUMMARY_CLASS.get(vm.state, "CardMeta"))
        _restyle(self._summary)

        self._headline.setText(vm.headline)
        self._headline.setVisible(bool(vm.headline))
        self._recent.setText(vm.recent_note)
        self._recent.setVisible(bool(vm.recent_note))

        # The button is pointless with nothing to show — but keep it available while a
        # recovered alert is still worth reading, which is the whole point of §24.
        has_anything = vm.has_active or bool(
            self._state is not None and self._state.alerts.recovered()
        )
        self._view_btn.setVisible(has_anything)

    @property
    def vm(self):
        return self._vm


# Severity → chip class. Token-driven via QSS; no hardcoded colour here.
_SUMMARY_CLASS: dict[str, str] = {
    "crit": "CriticalChip",
    "warn": "WarningChip",
    "ok": "CardMeta",
}


def _restyle(widget: QWidget) -> None:
    """Re-evaluate the widget's QSS after a dynamic property change."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
