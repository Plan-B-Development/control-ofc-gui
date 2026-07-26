"""Edit Fan Role dialog — members, curve assignment, mode, manual speed, and overrides.

DEC-214 restyle: built over the shared ``ModalDialog`` frame (header/body/footer +
scrim) with the mockup's Name / Mode / Curve fields, a Role-Members grid, and a
Delete / Discard / Save Changes footer. Every attr, objectName, and ``get_result``
key from the pre-redesign dialog is preserved so the ~15 tests + the page's
``_on_edit_role`` consumer keep working.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
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
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.qt_util import block_signals


class FanRoleDialog(ModalDialog):
    """Dialog for editing a fan role's name, curve, mode, manual speed, and members."""

    def __init__(
        self,
        control: LogicalControl,
        curves: list[CurveConfig],
        parent=None,
        display_name: Callable[[str, str], str] | None = None,
    ) -> None:
        super().__init__(f"Edit Fan Role: {control.name}", parent)
        self.setMinimumWidth(460)
        self._control = control
        self._delete_requested = False
        # DEC-228: (member_id, cached member_label) -> name to show, so a rename
        # made on any surface reaches these chips.
        self._display_name = display_name or (lambda mid, label: label or mid)

        layout = self.body_layout()
        layout.setSpacing(10)

        # Mode pill (the mockup's AUTO badge) — reflects Curve vs Manual mode.
        pill_row = QHBoxLayout()
        pill_row.addStretch(1)
        self._mode_pill = StatusPill("", "neutral")
        self._mode_pill.setObjectName("FanRoleDialog_Pill_mode")
        pill_row.addWidget(self._mode_pill)
        layout.addLayout(pill_row)

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
        self._mode_combo.addItem("Curve-based", ControlMode.CURVE.value)
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

        # Role Members header + "Edit Members…"
        members_header = QHBoxLayout()
        members_title = QLabel(f"Role Members ({len(control.members)})")
        members_title.setProperty("class", "PageSubtitle")
        members_header.addWidget(members_title, 1)
        edit_members_btn = QPushButton("Edit Members…")
        edit_members_btn.setObjectName("FanRoleDialog_Btn_editMembers")
        edit_members_btn.setToolTip("Open member assignment dialog")
        edit_members_btn.clicked.connect(self._on_edit_members)
        members_header.addWidget(edit_members_btn)
        layout.addLayout(members_header)

        # Member grid (2 columns of name chips). RPM is not passed to this dialog,
        # so — per "do not invent" — only member names are shown here; the member
        # editor (which is given live readings) is where RPM appears.
        layout.addWidget(self._build_member_grid(control.members))

        # Per-GPU-member zero-RPM toggle (v4). Only renders when the role has
        # at least one ``amd_gpu`` member; the daemon ignores the flag for
        # non-GPU sources. Each toggle binds to the member's ``fan_zero_rpm``.
        self._gpu_zero_rpm_checks: dict[str, QCheckBox] = {}
        self._gpu_section = self._build_gpu_zero_rpm_section(control.members)
        if self._gpu_section is not None:
            layout.addWidget(self._gpu_section)

        layout.addStretch()

        # Footer: Delete Role (left) | Discard | Save Changes
        delete_btn = make_button("Delete Role", "danger", object_name="FanRoleDialog_Btn_delete")
        delete_btn.setToolTip("Delete this fan role")
        delete_btn.clicked.connect(self._on_delete)
        self._footer_layout.insertWidget(0, delete_btn)
        discard_btn = self.add_footer_button("Discard", "ghost")
        discard_btn.clicked.connect(self.reject)
        save_btn = self.add_footer_button(
            "Save Changes", "primary", object_name="FanRoleDialog_Btn_save"
        )
        save_btn.clicked.connect(self.accept)

        self._edit_members_callback = None
        # Apply initial mode visibility + pill
        self._apply_mode_visibility()

    def set_edit_members_callback(self, callback):
        self._edit_members_callback = callback

    def _on_edit_members(self) -> None:
        if self._edit_members_callback:
            self._edit_members_callback(self._control.id)

    def _on_delete(self) -> None:
        """Delete Role: record the intent and accept so the page's ``_on_edit_role``
        routes it to the existing card-delete path (no new delete capability)."""
        self._delete_requested = True
        self.accept()

    def _on_mode_changed(self, _index: int) -> None:
        self._apply_mode_visibility()

    def _apply_mode_visibility(self) -> None:
        is_manual = self._mode_combo.currentData() == ControlMode.MANUAL.value
        self._curve_widget.setVisible(not is_manual)
        self._manual_widget.setVisible(is_manual)
        if is_manual:
            self._mode_pill.set_text("MANUAL")
            self._mode_pill.set_state("warn")
        else:
            self._mode_pill.set_text("AUTO")
            self._mode_pill.set_state("ok")

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
            "delete": self._delete_requested,
        }

    # ─── Member grid ─────────────────────────────────────────────────

    def _build_member_grid(self, members: list[ControlMember]) -> QWidget:
        """A compact 2-column grid of member name chips (mockup Role-Members)."""
        holder = QWidget()
        holder.setObjectName("FanRoleDialog_Grid_members")
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        if not members:
            empty = QLabel("None assigned")
            empty.setProperty("class", "PageSubtitle")
            grid.addWidget(empty, 0, 0)
            return holder
        for i, member in enumerate(members):
            chip = QLabel(self._display_name(member.member_id, member.member_label))
            chip.setTextFormat(Qt.TextFormat.PlainText)  # DEC-231: untrusted alias/label
            chip.setObjectName(f"FanRoleDialog_Chip_member_{member.member_id}")
            chip.setProperty("class", "CardMeta")
            chip.setToolTip(member.member_id)
            grid.addWidget(chip, i // 2, i % 2)
        return holder

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
            label = QLabel(self._display_name(m.member_id, m.member_label))
            label.setTextFormat(Qt.TextFormat.PlainText)  # DEC-231: untrusted alias/label
            label.setObjectName(f"FanRoleDialog_Label_gpuMember_{m.member_id}")
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
