"""Fan Role card — compact, information-dense card for the controls grid.

Shows: role name, members, assigned curve, output + sensor context, apply status.
Editing members/curve/overrides happens in a dialog, not on the card.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from onlyfans.services.profile_service import ControlMode, CurveConfig, LogicalControl
from onlyfans.ui.widgets.card_metrics import CARD_HEIGHT, CARD_WIDTH


class ControlCard(QFrame):
    """Compact fan role card — dense rows, no dead space."""

    selected = Signal(str)
    delete_requested = Signal(str)
    edit_role_requested = Signal(str)

    def __init__(self, control: LogicalControl, curves: list[CurveConfig], parent=None) -> None:
        super().__init__(parent)
        self.setProperty("class", "Card")
        self._control = control
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        # Row 1: Name + status chip
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self._name_label = QLabel(control.name or "Unnamed")
        self._name_label.setStyleSheet("font-weight: bold; background: transparent;")
        self._name_label.setObjectName(f"ControlCard_Label_{control.id}")
        row1.addWidget(self._name_label)
        row1.addStretch()
        self._status_chip = QLabel("")
        self._status_chip.setObjectName(f"ControlCard_Label_status_{control.id}")
        self._status_chip.setStyleSheet("background: transparent;")
        row1.addWidget(self._status_chip)
        layout.addLayout(row1)

        # Row 2: Members (compact, truncated)
        self._members_label = QLabel(self._members_text(control))
        self._members_label.setProperty("class", "CardMeta")
        self._members_label.setStyleSheet("background: transparent;")
        self._members_label.setObjectName(f"ControlCard_Label_members_{control.id}")
        layout.addWidget(self._members_label)

        # Row 3: Curve assignment
        curve_row = QHBoxLayout()
        curve_row.setSpacing(4)
        curve_name = self._curve_name(curves, control.curve_id)
        mode_text = "Manual" if control.mode == ControlMode.MANUAL else curve_name
        self._curve_label = QLabel(f"Curve: {mode_text}")
        self._curve_label.setProperty("class", "CardMeta")
        self._curve_label.setStyleSheet("background: transparent;")
        self._curve_label.setObjectName(f"ControlCard_Label_curve_{control.id}")
        curve_row.addWidget(self._curve_label)
        curve_row.addStretch()
        layout.addLayout(curve_row)

        # Row 4: Output + sensor context
        self._output_label = QLabel("\u2014")
        self._output_label.setObjectName(f"ControlCard_Label_output_{control.id}")
        self._output_label.setStyleSheet("background: transparent;")
        self._output_label.setProperty("class", "CardMeta")
        layout.addWidget(self._output_label)

        # Row 5: Bottom row — RPM left, Delete + Edit right
        actions = QHBoxLayout()
        actions.setSpacing(4)

        self._rpm_label = QLabel("")
        self._rpm_label.setProperty("class", "CardMeta")
        self._rpm_label.setStyleSheet("background: transparent;")
        self._rpm_label.setObjectName(f"ControlCard_Label_rpm_{control.id}")
        actions.addWidget(self._rpm_label)

        actions.addStretch()

        del_btn = QPushButton("Delete")
        del_btn.setObjectName(f"ControlCard_Btn_delete_{control.id}")
        del_btn.setToolTip("Delete this fan role")
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._control.id))
        actions.addWidget(del_btn)

        edit_btn = QPushButton("Edit\u2026")
        edit_btn.setObjectName(f"ControlCard_Btn_edit_{control.id}")
        edit_btn.setToolTip("Edit fan role: members, curve, overrides")
        edit_btn.clicked.connect(lambda: self.edit_role_requested.emit(self._control.id))
        actions.addWidget(edit_btn)

        layout.addLayout(actions)

        self._update_no_members_state(control)

    # ─── Public API ──────────────────────────────────────────────────

    @property
    def control(self) -> LogicalControl:
        return self._control

    def mousePressEvent(self, event) -> None:
        self.selected.emit(self._control.id)
        super().mousePressEvent(event)

    def set_output(
        self, output_pct: float, sensor_name: str = "", sensor_value: float | None = None
    ) -> None:
        if not self._control.members:
            return
        if self._control.mode == ControlMode.MANUAL:
            self._output_label.setText(f"Now: {output_pct:.0f}% (Manual)")
        elif sensor_name and sensor_value is not None:
            self._output_label.setText(
                f"Now: {output_pct:.0f}% \u2022 {sensor_name} {sensor_value:.1f}\u00b0C"
            )
        else:
            self._output_label.setText(f"Now: {output_pct:.0f}%")
        self._status_chip.setText("Applied")
        self._status_chip.setProperty("class", "SuccessChip")
        self._status_chip.style().unpolish(self._status_chip)
        self._status_chip.style().polish(self._status_chip)

    def set_rpm(self, rpm_text: str) -> None:
        self._rpm_label.setText(rpm_text)

    def update_control(self, control: LogicalControl, curves: list[CurveConfig]) -> None:
        self._control = control
        self._name_label.setText(control.name or "Unnamed")
        self._members_label.setText(self._members_text(control))
        curve_name = self._curve_name(curves, control.curve_id)
        mode_text = "Manual" if control.mode == ControlMode.MANUAL else curve_name
        self._curve_label.setText(f"Curve: {mode_text}")
        self._update_no_members_state(control)

    def update_output_preview(
        self, curve_name: str, sensor_name: str, sensor_value: float, output_pct: float
    ) -> None:
        """Update the output line from a curve edit without a full control loop cycle."""
        self._output_label.setText(
            f"Preview: {output_pct:.0f}% \u2022 {sensor_name} {sensor_value:.1f}\u00b0C"
        )

    # ─── Internals ───────────────────────────────────────────────────

    def _members_text(self, control: LogicalControl) -> str:
        if not control.members:
            return "No outputs assigned"
        labels = [m.member_label or m.member_id for m in control.members]
        text = ", ".join(labels[:3])
        if len(labels) > 3:
            text += f" +{len(labels) - 3} more"
        return f"Members: {text}"

    def _curve_name(self, curves: list[CurveConfig], curve_id: str) -> str:
        for c in curves:
            if c.id == curve_id:
                return f"{c.name} ({c.type.value})"
        return "None"

    def _update_no_members_state(self, control: LogicalControl) -> None:
        if not control.members:
            self._output_label.setText("Assign outputs to enable")
            self._status_chip.setText("No members")
            self._status_chip.setProperty("class", "PageSubtitle")
            self._status_chip.style().unpolish(self._status_chip)
            self._status_chip.style().polish(self._status_chip)
