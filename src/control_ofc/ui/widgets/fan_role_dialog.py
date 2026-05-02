"""Edit Fan Role dialog — members, curve assignment, mode, manual speed, and overrides."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    CurveConfig,
    LogicalControl,
)
from control_ofc.ui.qt_util import block_signals


class FanRoleDialog(QDialog):
    """Dialog for editing a fan role's name, curve, mode, manual speed, and members."""

    def __init__(
        self,
        control: LogicalControl,
        curves: list[CurveConfig],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Fan Role: {control.name}")
        self.setMinimumWidth(420)
        self._control = control

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit(control.name)
        self._name_edit.setObjectName("FanRoleDialog_Edit_name")
        name_row.addWidget(self._name_edit, 1)
        layout.addLayout(name_row)

        # Mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.setObjectName("FanRoleDialog_Combo_mode")
        self._mode_combo.addItem("Curve", ControlMode.CURVE.value)
        self._mode_combo.addItem("Manual", ControlMode.MANUAL.value)
        idx = 0 if control.mode == ControlMode.CURVE else 1
        self._mode_combo.setCurrentIndex(idx)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_combo, 1)
        layout.addLayout(mode_row)

        # Curve selector (visible in Curve mode)
        self._curve_widget = QWidget()
        curve_layout = QHBoxLayout(self._curve_widget)
        curve_layout.setContentsMargins(0, 0, 0, 0)
        curve_layout.addWidget(QLabel("Curve:"))
        self._curve_combo = QComboBox()
        self._curve_combo.setObjectName("FanRoleDialog_Combo_curve")
        for c in curves:
            self._curve_combo.addItem(f"{c.name} ({c.type.value})", c.id)
        cidx = self._curve_combo.findData(control.curve_id)
        if cidx >= 0:
            self._curve_combo.setCurrentIndex(cidx)
        curve_layout.addWidget(self._curve_combo, 1)
        layout.addWidget(self._curve_widget)

        # Manual speed controls (visible in Manual mode)
        self._manual_widget = QWidget()
        manual_layout = QVBoxLayout(self._manual_widget)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(6)

        manual_label = QLabel("Manual Output:")
        manual_label.setStyleSheet("font-weight: bold;")
        manual_layout.addWidget(manual_label)

        speed_row = QHBoxLayout()
        self._manual_slider = QSlider()
        self._manual_slider.setOrientation(Qt.Orientation.Horizontal)
        self._manual_slider.setRange(0, 100)
        self._manual_slider.setValue(round(control.manual_output_pct))
        self._manual_slider.setObjectName("FanRoleDialog_Slider_manual")
        self._manual_slider.valueChanged.connect(self._on_slider_changed)
        speed_row.addWidget(self._manual_slider, 1)

        self._manual_spin = QSpinBox()
        self._manual_spin.setRange(0, 100)
        self._manual_spin.setValue(round(control.manual_output_pct))
        self._manual_spin.setSuffix("%")
        self._manual_spin.setObjectName("FanRoleDialog_Spin_manual")
        self._manual_spin.valueChanged.connect(self._on_spin_changed)
        speed_row.addWidget(self._manual_spin)

        manual_layout.addLayout(speed_row)
        layout.addWidget(self._manual_widget)

        # Members summary
        members_text = (
            ", ".join(m.member_label or m.member_id for m in control.members) or "None assigned"
        )
        members_label = QLabel(f"Members: {members_text}")
        members_label.setWordWrap(True)
        members_label.setProperty("class", "PageSubtitle")
        layout.addWidget(members_label)

        edit_members_btn = QPushButton("Edit Members\u2026")
        edit_members_btn.setObjectName("FanRoleDialog_Btn_editMembers")
        edit_members_btn.setToolTip("Open member assignment dialog")
        edit_members_btn.clicked.connect(self._on_edit_members)
        layout.addWidget(edit_members_btn)

        # Per-GPU-member zero-RPM toggle (v4). Only renders when the role has
        # at least one ``amd_gpu`` member; the daemon ignores the flag for
        # non-GPU sources. Each toggle binds to the member's ``fan_zero_rpm``.
        self._gpu_zero_rpm_checks: dict[str, QCheckBox] = {}
        self._gpu_section = self._build_gpu_zero_rpm_section(control.members)
        if self._gpu_section is not None:
            layout.addWidget(self._gpu_section)

        layout.addStretch()

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Save")
        ok_btn.setObjectName("FanRoleDialog_Btn_save")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._edit_members_callback = None
        # Apply initial mode visibility
        self._apply_mode_visibility()

    def set_edit_members_callback(self, callback):
        self._edit_members_callback = callback

    def _on_edit_members(self) -> None:
        if self._edit_members_callback:
            self._edit_members_callback(self._control.id)

    def _on_mode_changed(self, _index: int) -> None:
        self._apply_mode_visibility()

    def _apply_mode_visibility(self) -> None:
        is_manual = self._mode_combo.currentData() == ControlMode.MANUAL.value
        self._curve_widget.setVisible(not is_manual)
        self._manual_widget.setVisible(is_manual)

    def _on_slider_changed(self, value: int) -> None:
        with block_signals(self._manual_spin):
            self._manual_spin.setValue(value)

    def _on_spin_changed(self, value: int) -> None:
        with block_signals(self._manual_slider):
            self._manual_slider.setValue(value)

    def get_result(self) -> dict:
        return {
            "name": self._name_edit.text().strip() or self._control.name,
            "mode": ControlMode(self._mode_combo.currentData()),
            "curve_id": self._curve_combo.currentData() or "",
            "manual_output_pct": float(self._manual_spin.value()),
            "gpu_fan_zero_rpm": self._collect_gpu_zero_rpm(),
        }

    # ─── GPU zero-RPM section ────────────────────────────────────────

    def _build_gpu_zero_rpm_section(self, members: list[ControlMember]) -> QWidget | None:
        """Build the per-GPU-member zero-RPM toggle group.

        Returns ``None`` when no GPU members are present so the dialog stays
        compact for chassis-only roles.
        """
        gpu_members = [m for m in members if m.source == "amd_gpu"]
        if not gpu_members:
            return None

        frame = QFrame()
        frame.setObjectName("FanRoleDialog_Frame_gpuZeroRpm")
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setSpacing(4)

        title = QLabel("GPU fan idle behaviour")
        title.setStyleSheet("font-weight: bold;")
        frame_layout.addWidget(title)

        info = QLabel(
            "When the GPU is below the firmware's idle threshold, choose "
            "whether the fan stops (zero-RPM) or keeps spinning at the "
            "curve's minimum."
        )
        info.setWordWrap(True)
        info.setProperty("class", "PageSubtitle")
        frame_layout.addWidget(info)

        for m in gpu_members:
            row = QHBoxLayout()
            label = QLabel(m.member_label or m.member_id)
            row.addWidget(label, 1)
            check = QCheckBox("Allow zero-RPM idle")
            check.setObjectName(f"FanRoleDialog_Check_zeroRpm_{m.member_id}")
            check.setToolTip(
                "Keep the GPU's firmware zero-RPM mode enabled while this "
                "profile is active — the fan stops at idle and spins up "
                "with the curve. When unchecked, the daemon disables "
                "zero-RPM so the fan tracks the curve continuously."
            )
            check.setChecked(bool(m.fan_zero_rpm))
            self._gpu_zero_rpm_checks[m.member_id] = check
            row.addWidget(check)
            frame_layout.addLayout(row)

        return frame

    def _collect_gpu_zero_rpm(self) -> dict[str, bool]:
        """Return ``{member_id: fan_zero_rpm}`` for the GPU members shown."""
        return {mid: cb.isChecked() for mid, cb in self._gpu_zero_rpm_checks.items()}
