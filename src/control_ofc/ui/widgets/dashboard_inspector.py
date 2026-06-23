"""Dashboard inspector — Sensors side panel (DEC-184, was DEC-182).

A thin container for the dashboard's right pane: a "Sensors" heading over the
existing :class:`SensorSeriesPanel` (passed in). The dashboard page toggles the
whole pane's visibility from the status strip so the chart can reclaim width on
narrow windows.

DEC-184 reduced this from the former tabbed Sensors/Events/Warnings panel: the
Events breadcrumb now lives only in Diagnostics, and the active-warnings detail
(``AppState.active_warnings`` — the dedup-keyed, dismissable set: stale sensor/fan,
fan stall, API skew, …) re-homed to a dialog opened from the status-strip warning
chip. :class:`WarningsView` (still defined here) renders that set in the dialog.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

if TYPE_CHECKING:
    from control_ofc.services.app_state import AppState

# Severity → (glyph, chip QSS class). Glyph pairs with the level word so the row
# never leans on colour alone (WCAG 1.4.1).
_LEVEL_GLYPH: dict[str, str] = {"info": "ⓘ", "warning": "⚠", "error": "✖"}
_LEVEL_CHIP: dict[str, str] = {
    "info": "InfoChip",
    "warning": "WarningChip",
    "error": "CriticalChip",
}


def next_action_for_warning(warning: dict) -> str | None:
    """Suggested next step for an ``active_warnings`` entry, or ``None``.

    Pure + unit-tested. Keyed on the warning's ``_key`` prefix first (the most
    specific signal — e.g. a stall vs a stale fan share ``source == "fan"``), then
    on ``source``. The taxonomy is the bounded set produced by
    ``AppState._update_warnings`` + ``add_warning`` callers.
    """
    key = warning.get("_key", "") or ""
    source = warning.get("source", "") or ""
    if key.startswith("fan_stall"):
        return (
            "Check the fan is spinning and properly connected — 0 RPM while a PWM is "
            "commanded usually means a stalled or unplugged fan."
        )
    if key.startswith("sensor_stale") or source == "sensor":
        return (
            "Sensor data is stale. Check the daemon connection and the sensor's driver "
            "in Diagnostics → Troubleshooting."
        )
    if key.startswith("fan_stale") or source == "fan":
        return "Fan telemetry is stale. Check the fan/header connection and the daemon status."
    if source == "api":
        return "Align your control-ofc-daemon and control-ofc-gui package versions."
    return None


class WarningsView(QWidget):
    """Scrollable list of active warnings with per-row detail + Clear all.

    Renders ``state.active_warnings`` (NOT the diagnostics event log). Each row
    shows severity, summary, component, timestamp and a suggested next action, with
    the raw detail behind a focusable expander (so no critical info is hover-only,
    WCAG 1.4.13). Rebuilt on ``warning_count_changed`` / ``warnings_cleared`` —
    low-frequency, so a full rebuild is simpler than reconciliation and avoids
    stale rows.
    """

    def __init__(self, state: AppState | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WarningsView_Root")
        self._state = state
        self._entry_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("WarningsView_Scroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._container = QWidget()
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(0, 0, 0, 0)
        self._vbox.setSpacing(8)
        self._vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

        self._clear_btn = QPushButton("Clear all warnings")
        self._clear_btn.setObjectName("WarningsView_Btn_clearAll")
        self._clear_btn.clicked.connect(self._on_clear)
        layout.addWidget(self._clear_btn)

        if self._state is not None:
            self._state.warning_count_changed.connect(self._on_count_changed)
            self._state.warnings_cleared.connect(self.refresh)

        self.refresh()

    def entry_count(self) -> int:
        """Number of warning rows currently rendered (0 = empty state)."""
        return self._entry_count

    def _on_count_changed(self, _count: int) -> None:
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list from ``state.active_warnings``."""
        # Tear down old rows.
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        warnings = list(self._state.active_warnings) if self._state is not None else []
        self._entry_count = len(warnings)

        if not warnings:
            empty = QLabel("No active warnings.")
            empty.setObjectName("WarningsView_Label_empty")
            empty.setProperty("class", "PageSubtitle")
            self._vbox.addWidget(empty)
            self._clear_btn.setEnabled(False)
            return

        self._clear_btn.setEnabled(True)
        for i, w in enumerate(warnings):
            self._vbox.addWidget(self._build_entry(i, w))
        self._vbox.addStretch(1)

    def _build_entry(self, idx: int, w: dict) -> QWidget:
        frame = QFrame()
        frame.setObjectName(f"WarningsView_Entry_{idx}")
        frame.setProperty("class", "Card")
        v = QVBoxLayout(frame)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(4)

        level = w.get("level", "warning")
        head = QHBoxLayout()
        sev = QLabel(f"{_LEVEL_GLYPH.get(level, '⚠')}  {level.upper()}")
        sev.setObjectName(f"WarningsView_Entry_{idx}_severity")
        sev.setProperty("class", _LEVEL_CHIP.get(level, "WarningChip"))
        head.addWidget(sev)
        head.addStretch(1)
        ts = QLabel(time.strftime("%H:%M:%S", time.localtime(w.get("timestamp", time.time()))))
        ts.setObjectName(f"WarningsView_Entry_{idx}_time")
        ts.setProperty("class", "CardMeta")
        head.addWidget(ts)
        v.addLayout(head)

        # Warning strings are daemon-derived (sensor labels, fan ids) — render
        # them as plain text so stray markup can never be reinterpreted as rich
        # text (the old WarningsDialog used plain table items; match that).
        summary = QLabel(w.get("message", ""))
        summary.setObjectName(f"WarningsView_Entry_{idx}_summary")
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        v.addWidget(summary)

        component = QLabel(f"Component: {w.get('source', '') or '—'}")
        component.setObjectName(f"WarningsView_Entry_{idx}_component")
        component.setTextFormat(Qt.TextFormat.PlainText)
        component.setProperty("class", "CardMeta")
        v.addWidget(component)

        action = next_action_for_warning(w)
        if action:
            act = QLabel(f"→ {action}")
            act.setObjectName(f"WarningsView_Entry_{idx}_action")
            act.setWordWrap(True)
            act.setProperty("class", "CardMeta")
            v.addWidget(act)

        detail = CollapsibleSection(
            "Raw detail", f"WarningsView_Entry_{idx}_detail", expanded=False
        )
        raw = QLabel(
            f"key: {w.get('_key', '') or '—'}\nlevel: {level}\nsource: {w.get('source', '') or '—'}"
        )
        raw.setTextFormat(Qt.TextFormat.PlainText)
        raw.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        raw.setWordWrap(True)
        detail.add_widget(raw)
        v.addWidget(detail)

        return frame

    def _on_clear(self) -> None:
        if self._state is not None:
            self._state.clear_warnings()


class DashboardInspector(QWidget):
    """Right-pane inspector: a titled Sensors panel (DEC-184)."""

    def __init__(self, sensors_widget: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Inspector_Root")
        self.setMinimumWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 2)
        layout.setSpacing(4)

        self._heading = QLabel("Sensors")
        self._heading.setObjectName("Inspector_Heading")
        self._heading.setProperty("class", "SectionTitle")
        layout.addWidget(self._heading)

        # The inspector owns the panel objectName so naming stays consistent
        # regardless of what the page named the composed widget.
        sensors_widget.setObjectName("Inspector_Panel_sensors")
        self._sensors_widget = sensors_widget
        layout.addWidget(sensors_widget, 1)

    def sensors_widget(self) -> QWidget:
        """The hosted sensor panel (exposed for tests/wiring)."""
        return self._sensors_widget
