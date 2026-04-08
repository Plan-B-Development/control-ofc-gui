"""Curve card — compact, information-dense card for the curve library grid.

Shows: name, type badge, sensor+value, used-by, status, mini preview.
Actions via setMenu dropdown. Preview renders at actual widget size.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from control_ofc.services.profile_service import CurveConfig, CurveType
from control_ofc.ui.theme import default_dark_theme
from control_ofc.ui.widgets.card_metrics import CARD_HEIGHT, CARD_WIDTH


class CurveCard(QFrame):
    """Compact curve card with preview, metadata, and actions."""

    edit_requested = Signal(str)
    delete_requested = Signal(str)
    rename_requested = Signal(str)
    duplicate_requested = Signal(str)

    def __init__(self, curve: CurveConfig, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("class", "Card")
        self._curve = curve
        self._theme = default_dark_theme()
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        # Row 1: Name + type badge + actions
        header = QHBoxLayout()
        header.setSpacing(4)
        self._name_label = QLabel(curve.name or "Unnamed")
        self._name_label.setStyleSheet("font-weight: bold; background: transparent;")
        self._name_label.setObjectName(f"CurveCard_Label_{curve.id}")
        self._name_label.setToolTip(curve.name or "Unnamed")
        header.addWidget(self._name_label, 1)

        type_label = QLabel(curve.type.value)
        type_label.setProperty("class", "CardMeta")
        type_label.setStyleSheet("background: transparent;")
        header.addWidget(type_label)

        actions_btn = QPushButton("Actions")
        actions_btn.setObjectName(f"CurveCard_Btn_actions_{curve.id}")
        actions_btn.setStyleSheet("padding: 2px 6px; background: transparent;")
        actions_menu = QMenu(actions_btn)
        actions_menu.addAction("Edit", lambda: self.edit_requested.emit(self._curve.id))
        actions_menu.addAction("Rename", lambda: self.rename_requested.emit(self._curve.id))
        actions_menu.addAction("Duplicate", lambda: self.duplicate_requested.emit(self._curve.id))
        actions_menu.addSeparator()
        actions_menu.addAction("Delete", lambda: self.delete_requested.emit(self._curve.id))
        actions_btn.setMenu(actions_menu)
        header.addWidget(actions_btn)
        layout.addLayout(header)

        # Row 2: Sensor + live value
        self._sensor_label = QLabel("No sensor")
        self._sensor_label.setProperty("class", "CardMeta")
        self._sensor_label.setStyleSheet("background: transparent;")
        self._sensor_label.setObjectName(f"CurveCard_Label_sensor_{curve.id}")
        if curve.sensor_id:
            self._sensor_label.setToolTip(f"Raw ID: {curve.sensor_id}")
        layout.addWidget(self._sensor_label)

        # Row 3: Preview (graph sparkline or text summary)
        self._preview = QLabel()
        self._preview.setMinimumHeight(30)
        self._preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview.setObjectName(f"CurveCard_Preview_{curve.id}")
        self._render_preview(curve)
        layout.addWidget(self._preview)

        # Row 4: Used by + status
        footer = QHBoxLayout()
        footer.setSpacing(4)
        self._used_by_label = QLabel("Not assigned")
        self._used_by_label.setProperty("class", "CardMeta")
        self._used_by_label.setStyleSheet("background: transparent;")
        self._used_by_label.setObjectName(f"CurveCard_Label_usedBy_{curve.id}")
        footer.addWidget(self._used_by_label, 1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("background: transparent;")
        self._status_label.setObjectName(f"CurveCard_Label_status_{curve.id}")
        footer.addWidget(self._status_label)
        layout.addLayout(footer)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._curve.type == CurveType.GRAPH and len(self._curve.points) >= 2:
            self._render_graph_preview(self._curve)

    @property
    def curve(self) -> CurveConfig:
        return self._curve

    def set_theme(self, tokens) -> None:
        """Update theme and re-render the curve preview."""
        self._theme = tokens
        self._render_graph_preview(self._curve)

    def update_sensor_display(self, label: str, value_c: float | None = None) -> None:
        if value_c is not None:
            self._sensor_label.setText(f"{label} \u2014 {value_c:.1f}\u00b0C")
        else:
            self._sensor_label.setText(label if label else "No sensor")

    def set_used_by(self, role_names: list[str]) -> None:
        if role_names:
            text = ", ".join(role_names[:3])
            if len(role_names) > 3:
                text += f" +{len(role_names) - 3}"
            self._used_by_label.setText(f"Used by: {text}")
            self._used_by_label.setToolTip(", ".join(role_names))
            self._status_label.setText("Assigned")
            self._status_label.setProperty("class", "SuccessChip")
        else:
            self._used_by_label.setText("Not assigned")
            self._status_label.setText("Unassigned")
            self._status_label.setProperty("class", "PageSubtitle")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)

    def update_curve(self, curve: CurveConfig) -> None:
        self._curve = curve
        self._name_label.setText(curve.name or "Unnamed")
        self._name_label.setToolTip(curve.name or "Unnamed")
        if curve.sensor_id:
            self._sensor_label.setToolTip(f"Raw ID: {curve.sensor_id}")
        else:
            self._sensor_label.setText("No sensor")
        self._render_preview(curve)

    def _render_preview(self, curve: CurveConfig) -> None:
        if curve.type == CurveType.LINEAR:
            self._preview.setText(
                f"{curve.start_temp_c:.0f}\u00b0C\u2192{curve.end_temp_c:.0f}\u00b0C: "
                f"{curve.start_output_pct:.0f}%\u2192{curve.end_output_pct:.0f}%"
            )
            self._preview.setStyleSheet("background: transparent;")
            self._preview.setProperty("class", "PageSubtitle")
            self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        elif curve.type == CurveType.FLAT:
            self._preview.setText(f"Flat: {curve.flat_output_pct:.0f}%")
            self._preview.setStyleSheet("background: transparent;")
            self._preview.setProperty("class", "PageSubtitle")
            self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self._render_graph_preview(curve)

    def _render_graph_preview(self, curve: CurveConfig) -> None:
        w = max(self._preview.width(), 120)
        h = max(self._preview.height(), 30)
        pixmap = QPixmap(w, h)
        pixmap.fill(QColor(0, 0, 0, 0))

        if len(curve.points) < 2:
            self._preview.setPixmap(pixmap)
            return

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(self._theme.accent_primary), 2))

        points = curve.points
        x_min = points[0].temp_c
        x_max = points[-1].temp_c
        x_range = max(x_max - x_min, 1.0)
        pad = 3

        path = QPainterPath()
        for i, p in enumerate(points):
            px = ((p.temp_c - x_min) / x_range) * (w - 2 * pad) + pad
            py = h - pad - (p.output_pct / 100.0) * (h - 2 * pad)
            if i == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)

        painter.drawPath(path)
        painter.end()
        self._preview.setPixmap(pixmap)
