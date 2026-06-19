"""Fan Role card — compact, information-dense card for the controls grid.

Shows: role name, members, assigned curve, output + sensor context, apply status.
Editing members/curve/overrides happens in a dialog, not on the card.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from control_ofc.services.profile_service import (
    CONTROL_ROLE_GPU,
    ControlMode,
    CurveConfig,
    LogicalControl,
    control_minimum_pct,
    infer_control_role,
    infer_member_role,
)
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.card_metrics import (
    DEFAULT_CARD_SIZE,
    MIN_USER_CARD_WIDTH_PX,
    card_dimensions,
)
from control_ofc.ui.widgets.card_resize import CardResizeGrip, snap_size

# QWIDGETSIZE_MAX — not exported by this PySide6 build; used to undo a fixed
# height when a user size override is cleared (DEC-129).
_QWIDGETSIZE_MAX = 16777215


class ControlCard(QFrame):
    """Compact fan role card — dense rows, no dead space."""

    selected = Signal(str)
    delete_requested = Signal(str)
    edit_role_requested = Signal(str)
    # Transient per-card manual override (Decision 1A): toggled carries the
    # active flag + the slider value at toggle time; value_changed fires while
    # dragging. Neither mutates the saved profile.
    manual_toggled = Signal(str, bool, int)  # control_id, active, pct
    manual_value_changed = Signal(str, int)  # control_id, pct
    # DEC-129 per-card user resize: resized fires on grip release with the
    # snapped size actually applied; size_reset fires on grip double-click
    # after the card has restored its theme-derived default.
    resized = Signal(str, int, int)  # control_id, width, height
    size_reset = Signal(str)  # control_id

    def __init__(
        self,
        control: LogicalControl,
        curves: list[CurveConfig],
        card_size: str = DEFAULT_CARD_SIZE,
        user_size: tuple[int, int] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "Card")
        self._control = control
        self._last_output_pct: float | None = None
        # DEC-169: a daemon-held override this GUI session does NOT own (no
        # fencing token — only displayable, never renewable/releasable). Set by
        # the Controls page's /status reconcile; shows a read-only "External"
        # chip. `None` when no foreign override is pinning this control.
        self._external_pct: int | None = None
        # Fixed width keeps the grid columns aligned; height is a floor so the
        # card grows to fit scaled text rather than clipping rows (DEC-128).
        self._card_size_tier = card_size
        # DEC-129: persisted per-card override; None = theme-derived sizing.
        self._user_size: tuple[int, int] | None = None
        # Grip exists before the first resizeEvent (any setFixedWidth below
        # triggers one) so resizeEvent can always reposition it.
        self._grip = CardResizeGrip(self)
        self._grip.setObjectName(f"ControlCard_Grip_{control.id}")
        self._grip.resize_finished.connect(self._on_grip_resized)
        self._grip.reset_requested.connect(self._on_grip_reset)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
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

        # Row 3: Curve assignment + minimum-PWM badge
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
        # Minimum-PWM badge: surfaces the role-derived safety floor so the
        # user can see at a glance why a curve appears clamped at the bottom.
        # See profile_service.role_minimum_pct + DEC-095.
        self._min_pwm_label = QLabel("")
        self._min_pwm_label.setProperty("class", "CardMeta")
        self._min_pwm_label.setStyleSheet("background: transparent;")
        self._min_pwm_label.setObjectName(f"ControlCard_Label_minPwm_{control.id}")
        curve_row.addWidget(self._min_pwm_label)
        layout.addLayout(curve_row)

        # Row 4: Output + sensor context (auto) — morphs into an inline manual
        # slider when the Manual toggle is on. The two share the row; exactly
        # one is visible, so the row height stays constant.
        row4 = QHBoxLayout()
        row4.setSpacing(4)
        self._output_label = QLabel("—")
        self._output_label.setObjectName(f"ControlCard_Label_output_{control.id}")
        self._output_label.setStyleSheet("background: transparent;")
        self._output_label.setProperty("class", "CardMeta")
        row4.addWidget(self._output_label)
        self._manual_slider = QSlider(Qt.Orientation.Horizontal)
        self._manual_slider.setObjectName(f"ControlCard_Slider_manual_{control.id}")
        self._manual_slider.setRange(0, 100)
        self._manual_slider.setValue(50)
        self._manual_slider.setVisible(False)
        self._manual_slider.valueChanged.connect(self._on_manual_slider_changed)
        row4.addWidget(self._manual_slider, 1)
        self._manual_pct_label = QLabel("50%")
        self._manual_pct_label.setObjectName(f"ControlCard_Label_manualPct_{control.id}")
        self._manual_pct_label.setStyleSheet("background: transparent;")
        self._manual_pct_label.setProperty("class", "CardMeta")
        self._manual_pct_label.setVisible(False)
        row4.addWidget(self._manual_pct_label)
        layout.addLayout(row4)

        # Surplus vertical space (card taller than its rows — e.g. the DEC-128
        # floor or a DEC-129 user resize) pools here instead of being
        # distributed between the text rows, which read as bloated line
        # spacing. Rows stay tight at the top; actions stay pinned at the
        # bottom.
        layout.addStretch(1)

        # Row 5: Bottom row — RPM left, Delete + Edit right
        actions = QHBoxLayout()
        actions.setSpacing(4)

        self._rpm_label = QLabel("")
        self._rpm_label.setProperty("class", "CardMeta")
        self._rpm_label.setStyleSheet("background: transparent;")
        self._rpm_label.setObjectName(f"ControlCard_Label_rpm_{control.id}")
        actions.addWidget(self._rpm_label)

        actions.addStretch()

        self._manual_btn = QPushButton("Manual")
        self._manual_btn.setObjectName(f"ControlCard_Btn_manual_{control.id}")
        self._manual_btn.setCheckable(True)
        self._manual_btn.setToolTip(
            "Temporarily set this role's fans to a fixed speed.\n"
            "Not saved to the profile; clears on profile change."
        )
        self._manual_btn.toggled.connect(self._on_manual_toggled)
        actions.addWidget(self._manual_btn)

        del_btn = QPushButton("Delete")
        del_btn.setObjectName(f"ControlCard_Btn_delete_{control.id}")
        del_btn.setToolTip("Delete this fan role")
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._control.id))
        actions.addWidget(del_btn)

        edit_btn = QPushButton("Edit…")
        edit_btn.setObjectName(f"ControlCard_Btn_edit_{control.id}")
        edit_btn.setToolTip("Edit fan role: members, curve, overrides")
        edit_btn.clicked.connect(lambda: self.edit_role_requested.emit(self._control.id))
        actions.addWidget(edit_btn)

        layout.addLayout(actions)

        self._update_no_members_state(control)
        self._update_min_pwm_badge(control)
        self.apply_card_size(active_theme().base_font_size_pt, card_size, user_size)

    # ─── Public API ──────────────────────────────────────────────────

    @property
    def control(self) -> LogicalControl:
        return self._control

    @property
    def user_size(self) -> tuple[int, int] | None:
        """The persisted per-card size override, or None for theme sizing."""
        return self._user_size

    def apply_card_size(
        self,
        base_pt: int,
        tier: str = DEFAULT_CARD_SIZE,
        user_size: tuple[int, int] | None = None,
    ) -> None:
        """Size the card from the theme base font size and a density tier.

        Without a user override: width is fixed so the flow grid stays
        column-aligned; height is a minimum floor (no maximum), so a card
        with scaled-up text or more members grows taller instead of clipping
        rows (DEC-128).

        With a user override (DEC-129): both dimensions are fixed to the
        snapped override, re-clamped to the current content minimum at every
        re-apply — so a theme/tier change or content growth clamps the
        override but never clears it. Passing ``user_size=None`` keeps any
        existing override; clearing is explicit via :meth:`clear_user_size`.
        """
        self._card_size_tier = tier
        if user_size is not None:
            self._user_size = self._snap_to_content(*user_size)
        if self._user_size is not None:
            width, height = self._snap_to_content(*self._user_size)
            self.setFixedWidth(width)
            self.setFixedHeight(height)
        else:
            width, height = card_dimensions(base_pt, tier)
            self.setFixedWidth(width)
            # Undo a previous override's fixed height before re-flooring.
            self.setMaximumHeight(_QWIDGETSIZE_MAX)
            self.setMinimumHeight(height)
        self.updateGeometry()

    def set_user_size(self, width: int, height: int) -> tuple[int, int]:
        """Apply a live user resize (grip drag), snapped and clamped.

        Returns the size actually applied so the grip can report it on
        release.
        """
        applied = self._snap_to_content(width, height)
        self._user_size = applied
        self.setFixedWidth(applied[0])
        self.setFixedHeight(applied[1])
        self.updateGeometry()
        return applied

    def clear_user_size(self) -> None:
        """Drop the per-card override and restore theme-derived sizing."""
        self._user_size = None
        self.apply_card_size(active_theme().base_font_size_pt, self._card_size_tier)

    def _snap_to_content(self, width: int, height: int) -> tuple[int, int]:
        """Snap to the shared lattice, clamped so rows can never clip."""
        return snap_size(
            width,
            height,
            MIN_USER_CARD_WIDTH_PX,
            self.layout().minimumSize().height(),
        )

    def _on_grip_resized(self, width: int, height: int) -> None:
        self.resized.emit(self._control.id, width, height)

    def _on_grip_reset(self) -> None:
        self.clear_user_size()
        self.size_reset.emit(self._control.id)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep the resize grip pinned to the bottom-right corner, above the
        # card content (it floats outside the layout).
        self._grip.move(self.width() - self._grip.width(), self.height() - self._grip.height())
        self._grip.raise_()

    def mousePressEvent(self, event) -> None:
        self.selected.emit(self._control.id)
        super().mousePressEvent(event)

    def set_output(
        self,
        output_pct: float,
        sensor_name: str = "",
        sensor_value: float | None = None,
        gpu_output_pct: float | None = None,
    ) -> None:
        if not self._control.members:
            return
        self._last_output_pct = output_pct
        if self._manual_btn.isChecked():
            # Transient manual mode owns the row (slider) and the status chip.
            return
        # DEC-119: in a mixed control the GPU member can sit below the
        # control-wide value, so surface its real output rather than letting the
        # headline misreport it.
        gpu_suffix = f" (GPU {gpu_output_pct:.0f}%)" if gpu_output_pct is not None else ""
        if self._control.mode == ControlMode.MANUAL:
            self._output_label.setText(f"Now: {output_pct:.0f}% (Manual){gpu_suffix}")
        elif sensor_name and sensor_value is not None:
            self._output_label.setText(
                f"Now: {output_pct:.0f}%{gpu_suffix} • {sensor_name} {sensor_value:.1f}°C"
            )
        else:
            self._output_label.setText(f"Now: {output_pct:.0f}%{gpu_suffix}")
        if self._external_pct is not None:
            # DEC-169: a foreign daemon override owns the chip — keep the
            # read-only "External" badge instead of repainting "Applied" each
            # tick. The output label above still tracks the live value.
            return
        self._apply_chip("Applied", "SuccessChip")

    def set_rpm(self, rpm_text: str) -> None:
        self._rpm_label.setText(rpm_text)

    def _apply_chip(self, text: str, cls: str) -> None:
        """Set the status chip text + style class and repolish."""
        self._status_chip.setText(text)
        self._status_chip.setProperty("class", cls)
        self._status_chip.style().unpolish(self._status_chip)
        self._status_chip.style().polish(self._status_chip)

    def _on_manual_toggled(self, checked: bool) -> None:
        """Reveal/hide the inline slider and signal the transient manual state."""
        if checked:
            # DEC-169: taking manual ownership supersedes any foreign-override
            # display — the user's own (fenced, renewable) override now wins.
            self._external_pct = None
        if checked and self._last_output_pct is not None:
            # Start manual at the current speed so the fan doesn't jump.
            self._manual_slider.blockSignals(True)
            self._manual_slider.setValue(round(self._last_output_pct))
            self._manual_slider.blockSignals(False)
            self._manual_pct_label.setText(f"{self._manual_slider.value()}%")
        self._manual_slider.setVisible(checked)
        self._manual_pct_label.setVisible(checked)
        self._output_label.setVisible(not checked)
        self._apply_chip("Manual", "WarningChip") if checked else self._apply_chip("", "")
        self.manual_toggled.emit(self._control.id, checked, self._manual_slider.value())

    def _on_manual_slider_changed(self, value: int) -> None:
        self._manual_pct_label.setText(f"{value}%")
        if self._manual_btn.isChecked():
            self.manual_value_changed.emit(self._control.id, value)

    def clear_manual(self) -> None:
        """Programmatically exit Manual without emitting ``manual_toggled``.

        Used when a daemon override lapses or is rejected (DEC-163): the card
        must stop showing Manual without re-triggering a release of the
        already-gone override. Mirrors the unchecked branch of
        ``_on_manual_toggled``.
        """
        if not self._manual_btn.isChecked():
            return
        self._manual_btn.blockSignals(True)
        self._manual_btn.setChecked(False)
        self._manual_btn.blockSignals(False)
        self._manual_slider.setVisible(False)
        self._manual_pct_label.setVisible(False)
        self._output_label.setVisible(True)
        self._apply_chip("", "")

    def set_external_override(self, pct: int) -> None:
        """Show a read-only "External" chip for a daemon-held override this GUI
        session does not own (DEC-169).

        Such an override carries no fencing token on the `/status` surface, so it
        can only be *displayed* — never renewed or released. The Manual button is
        left unchecked: clicking it is an explicit *take-over* (a fresh
        ``override_take`` that supersedes via monotonic fencing). Manual state, if
        the user already owns one, wins over this display.
        """
        self._external_pct = pct
        if self._manual_btn.isChecked():
            return
        self._apply_chip(f"External {pct}%", "InfoChip")

    def clear_external_override(self) -> None:
        """Drop the read-only external-override chip (DEC-169) once the daemon no
        longer reports it. Leaves a user-owned Manual state untouched; otherwise
        clears the chip and lets the next ``set_output`` repaint "Applied"."""
        if self._external_pct is None:
            return
        self._external_pct = None
        if self._manual_btn.isChecked():
            return
        self._apply_chip("", "")

    def update_control(self, control: LogicalControl, curves: list[CurveConfig]) -> None:
        self._control = control
        self._name_label.setText(control.name or "Unnamed")
        self._members_label.setText(self._members_text(control))
        curve_name = self._curve_name(curves, control.curve_id)
        mode_text = "Manual" if control.mode == ControlMode.MANUAL else curve_name
        self._curve_label.setText(f"Curve: {mode_text}")
        self._update_no_members_state(control)
        self._update_min_pwm_badge(control)
        if self._user_size is not None:
            # Content may have grown (e.g. more members): re-clamp the user
            # override so a previously-valid size can't start clipping rows.
            self.apply_card_size(active_theme().base_font_size_pt, self._card_size_tier)

    def update_output_preview(
        self, curve_name: str, sensor_name: str, sensor_value: float, output_pct: float
    ) -> None:
        """Update the output line from a curve edit without a full control loop cycle."""
        self._output_label.setText(
            f"Preview: {output_pct:.0f}% • {sensor_name} {sensor_value:.1f}°C"
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
        # No fans assigned -> nothing to drive manually.
        self._manual_btn.setEnabled(bool(control.members))
        if not control.members:
            self._output_label.setText("Assign outputs to enable")
            self._status_chip.setText("No members")
            self._status_chip.setProperty("class", "PageSubtitle")
            self._status_chip.style().unpolish(self._status_chip)
            self._status_chip.style().polish(self._status_chip)

    def _update_min_pwm_badge(self, control: LogicalControl) -> None:
        """Refresh the inline minimum-PWM badge from the control's effective floor."""
        # Show the larger of the user-set floor and the role-derived floor so
        # the user sees the clamp that actually applies. Hide entirely when
        # there is no floor (0%), so chassis-only roles authored before v4
        # don't display a misleading "Min: 0%".
        effective = max(control.minimum_pct, control_minimum_pct(control.members))
        if effective <= 0.0:
            self._min_pwm_label.setText("")
            self._min_pwm_label.setToolTip("")
            return
        self._min_pwm_label.setText(f"Min: {effective:.0f}%")
        role = infer_control_role(control.members)
        if role == "cpu_or_pump":
            tip = (
                "Minimum PWM derived from a CPU or pump member. "
                "30% protects the pump from stalling."
            )
        elif role == "chassis":
            tip = (
                "Minimum PWM for chassis fans. "
                "20% prevents most 4-pin fans from stalling at low duty."
            )
        else:
            tip = "Minimum PWM applied by this control."
        # DEC-119: in a mixed control (GPU grouped with chassis/CPU fans) the
        # floor above applies only to the non-GPU members. GPU members are
        # never floored by the GUI — the GPU firmware owns their idle minimum.
        members = control.members
        has_gpu = any(infer_member_role(m) == CONTROL_ROLE_GPU for m in members)
        has_non_gpu = any(infer_member_role(m) != CONTROL_ROLE_GPU for m in members)
        if has_gpu and has_non_gpu:
            tip += (
                " GPU members in this control are not floored "
                "(the GPU firmware manages their minimum)."
            )
        self._min_pwm_label.setToolTip(tip)
