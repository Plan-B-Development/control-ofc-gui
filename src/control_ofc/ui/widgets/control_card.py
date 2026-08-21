"""Fan Role card — compact, information-dense card for the controls grid.

Shows: role name, members, assigned curve, output + sensor context, apply status.
Editing members/curve/overrides happens in a dialog, not on the card.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.controls_view import skipped_control_feedback
from control_ofc.services.profile_service import (
    CONTROL_ROLE_GPU,
    ControlMode,
    CurveConfig,
    LogicalControl,
    control_minimum_pct,
    infer_control_role,
    infer_member_role,
)
from control_ofc.ui.components.labels import ElidedLabel
from control_ofc.ui.qt_util import repolish, set_chip_class
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.card_metrics import DEFAULT_CARD_SIZE
from control_ofc.ui.widgets.resizable_grid_card import ResizableGridCard


class ControlCard(ResizableGridCard):
    """Compact fan role card — dense rows, no dead space."""

    selected = Signal(str)
    delete_requested = Signal(str)
    edit_role_requested = Signal(str)
    # Transient per-card manual override (Decision 1A): toggled carries the
    # active flag + the slider value at toggle time; value_changed fires while
    # dragging. Neither mutates the saved profile.
    manual_toggled = Signal(str, bool, int)  # control_id, active, pct
    manual_value_changed = Signal(str, int)  # control_id, pct
    # resized / size_reset (per-card user resize, DEC-129) are inherited from
    # ResizableGridCard.

    def __init__(
        self,
        control: LogicalControl,
        curves: list[CurveConfig],
        card_size: str = DEFAULT_CARD_SIZE,
        user_size: tuple[int, int] | None = None,
        parent=None,
        display_name: Callable[[str, str], str] | None = None,
    ) -> None:
        super().__init__(parent)
        # DEC-228: resolves (member_id, cached member_label) -> the name to show,
        # so a rename made anywhere reaches these rows. Defaults to the old
        # cached-label behaviour when no resolver is supplied (tests, previews).
        self._display_name = display_name or (lambda mid, label: label or mid)
        self._control = control
        self._last_output_pct: float | None = None
        # DEC-169: a daemon-held override this GUI session does NOT own (no
        # fencing token — only displayable, never renewable/releasable). Set by
        # the Controls page's /status reconcile; shows a read-only "External"
        # chip. `None` when no foreign override is pinning this control.
        self._external_pct: int | None = None
        # 273-i: the daemon reports this control as one it cannot resolve, so it
        # is commanding nothing and these fans hold their last speed. `None` when
        # the control is being commanded normally. Set by the Controls page's
        # /status reconcile, exactly like `_external_pct` above.
        self._skipped_reason: str | None = None
        # Sizing (DEC-128 floor) + the DEC-129 resize grip live in the base.
        self._init_grid_card(control.id, f"ControlCard_Grip_{control.id}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        # Row 1: role icon + name + "N Fans" pill + status chip
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self._role_icon = QLabel("●")  # a role-coloured dot (mockup icon stand-in)
        self._role_icon.setObjectName(f"ControlCard_Icon_role_{control.id}")
        self._role_icon.setStyleSheet(
            f"color: {self._role_color(control)}; background: transparent;"
        )
        row1.addWidget(self._role_icon)
        self._name_label = QLabel(control.name or "Unnamed")
        # DEC-231: control names come from the profile (untrusted) — render
        # verbatim so stray markup can never be reinterpreted as rich text
        # (matches fan_control_card + warnings_view).
        self._name_label.setTextFormat(Qt.TextFormat.PlainText)
        self._name_label.setStyleSheet("font-weight: bold; background: transparent;")
        self._name_label.setObjectName(f"ControlCard_Label_{control.id}")
        row1.addWidget(self._name_label)
        row1.addStretch()
        self._fan_count_label = QLabel("")
        self._fan_count_label.setObjectName(f"ControlCard_Label_fanCount_{control.id}")
        self._fan_count_label.setProperty("class", "Pill_neutral")
        row1.addWidget(self._fan_count_label)
        self._status_chip = QLabel("")
        self._status_chip.setObjectName(f"ControlCard_Label_status_{control.id}")
        self._status_chip.setStyleSheet("background: transparent;")
        row1.addWidget(self._status_chip)
        layout.addLayout(row1)

        # Compact members summary (kept for callers/tests) hidden behind the
        # per-member RPM rows (the mockup treatment); set_member_rpms fills the
        # live RPM column, never fabricating an unknown reading.
        self._members_label = QLabel(self._members_text(control))
        self._members_label.setProperty("class", "CardMeta")
        self._members_label.setStyleSheet("background: transparent;")
        self._members_label.setObjectName(f"ControlCard_Label_members_{control.id}")
        self._members_label.setVisible(False)
        layout.addWidget(self._members_label)

        self._member_rows = QWidget()
        self._member_rows.setObjectName(f"ControlCard_Rows_members_{control.id}")
        self._member_rows_layout = QVBoxLayout(self._member_rows)
        self._member_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._member_rows_layout.setSpacing(1)
        self._member_row_rpm: dict[str, QLabel] = {}
        self._member_row_name: dict[str, QLabel] = {}
        layout.addWidget(self._member_rows)
        self._rebuild_member_rows(control)

        # Details (revealed on select, DEC-214): assigned curve + min-PWM badge,
        # and the auto output / inline manual-slider row. Default-expanded so a
        # standalone card shows every widget (the card tests need no selection);
        # the page collapses non-selected cards to the compact mockup form.
        self._expanded = True
        self._details = QWidget()
        self._details.setObjectName(f"ControlCard_Details_{control.id}")
        details_layout = QVBoxLayout(self._details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(2)

        # Curve assignment + minimum-PWM badge
        curve_row = QHBoxLayout()
        curve_row.setSpacing(4)
        curve_name = self._curve_name(curves, control.curve_id)
        mode_text = "Manual" if control.mode == ControlMode.MANUAL else curve_name
        # DEC-258: an ElidedLabel, not a QLabel. The curve name is
        # profile-authored and arbitrary-length, so this row's width demand is
        # unbounded — it alone wanted 286px of a 304px content area at 16pt, and
        # a longer name clips at ANY font size or card tier. Widening the card
        # (see `card_metrics._WIDTH_PER_PT`) fixes the typical case; only elision
        # bounds the worst one.
        #
        # DEC-231 still holds: ElidedLabel renders plain text unconditionally, so
        # stray markup in a profile-authored name cannot become formatting.
        self._curve_label = ElidedLabel(f"Curve: {mode_text}")
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
        details_layout.addLayout(curve_row)

        # Output + sensor context (auto) — morphs into an inline manual slider
        # when the Manual toggle is on. The two share the row; exactly one is
        # visible, so the row height stays constant.
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
        details_layout.addLayout(row4)
        layout.addWidget(self._details)

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

        # Link-indicator nub (DEC-214): a small accent stub on the right edge,
        # shown for the selected card — the visual wire toward the curve column.
        self._link_nub = QFrame(self)
        self._link_nub.setObjectName(f"ControlCard_Nub_link_{control.id}")
        self._link_nub.setFixedSize(3, 18)
        self._link_nub.setStyleSheet(
            f"background: {active_theme().accent_primary}; border-radius: 1px;"
        )
        self._link_nub.setVisible(False)

        self._update_no_members_state(control)
        self._update_min_pwm_badge(control)
        self._update_fan_count(control)
        self.apply_card_size(active_theme().base_font_size_pt, card_size, user_size)

    # ─── Public API ──────────────────────────────────────────────────

    @property
    def control(self) -> LogicalControl:
        return self._control

    # Sizing + grip API (apply_card_size / set_user_size / clear_user_size /
    # user_size / _on_grip_* / grip positioning) is inherited from
    # ResizableGridCard.

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)  # base pins the resize grip
        # Pin the link nub to the right-edge centre (DEC-214).
        self._link_nub.move(
            self.width() - self._link_nub.width(),
            (self.height() - self._link_nub.height()) // 2,
        )
        self._link_nub.raise_()

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
        if self._skipped_reason is not None:
            # 273-i: nothing is commanding these fans. Painting "Applied" over
            # that would be the lie this row exists to stop — the output label
            # above still shows the last commanded value, which is what the fans
            # are actually holding.
            return
        if self._external_pct is not None:
            # DEC-169: a foreign daemon override owns the chip — keep the
            # read-only "External" badge instead of repainting "Applied" each
            # tick. The output label above still tracks the live value.
            return
        self._apply_chip("Applied", "SuccessChip")

    def set_rpm(self, rpm_text: str) -> None:
        self._rpm_label.setText(rpm_text)

    def set_member_rpms(self, rpms: dict[str, int | None]) -> None:
        """Update each member row's live RPM column (DEC-214).

        ``rpms`` maps member/fan id → measured RPM (``None`` when unknown). No
        value is fabricated — an unknown reading leaves the column blank.
        """
        for member_id, label in self._member_row_rpm.items():
            rpm = rpms.get(member_id)
            label.setText(f"{rpm} RPM" if rpm is not None else "")

    def set_selected(self, selected: bool) -> None:
        """Expand (select) or collapse this card (DEC-214).

        The selected card reveals its detail rows (assigned curve, min-PWM, the
        auto output / manual slider) and takes an accent border + link nub; the
        others collapse to the compact mockup form (icon, name, N-Fans, members).
        Default-expanded so a standalone card shows everything (the card tests
        need no selection).
        """
        self._expanded = selected
        self._details.setVisible(selected)
        self._link_nub.setVisible(selected)
        self.setProperty("selected", selected)
        repolish(self)

    def set_theme(self, tokens) -> None:
        """Re-apply the inline-styled accents after a live theme switch.

        The link nub (accent) and the role dot are painted with inline
        stylesheets (not QSS ``.class`` rules), so an ``apply_theme`` palette
        swap does not repaint them — the Controls page's theme fan-out calls
        this to refresh both. Card sizing is re-applied separately by the page
        (``apply_card_size``); the P2-1 manual-floor slider state is untouched.
        """
        self._role_icon.setStyleSheet(
            f"color: {self._role_color(self._control)}; background: transparent;"
        )
        self._link_nub.setStyleSheet(f"background: {tokens.accent_primary}; border-radius: 1px;")

    def _role_color(self, control: LogicalControl) -> str:
        """A role-derived accent colour for the card's icon dot (mockup uses a
        distinct icon per role; we colour-code instead of depending on an icon
        font). CPU/pump = brand accent, GPU-bearing = info, else muted."""
        t = active_theme()
        if infer_control_role(control.members) == "cpu_or_pump":
            return t.accent_primary
        if any(infer_member_role(m) == CONTROL_ROLE_GPU for m in control.members):
            return t.status_info
        return t.text_secondary

    def _update_fan_count(self, control: LogicalControl) -> None:
        n = len(control.members)
        self._fan_count_label.setText(f"{n} Fan{'s' if n != 1 else ''}")
        self._fan_count_label.setVisible(n > 0)

    def _rebuild_member_rows(self, control: LogicalControl) -> None:
        """(Re)build one ``name · RPM`` row per member (DEC-214)."""
        while self._member_rows_layout.count():
            item = self._member_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)  # detach now so a rebuild can't reuse a stale row
                widget.deleteLater()
        self._member_row_rpm = {}
        self._member_row_name = {}
        for member in control.members:
            # Unique per-member objectNames (control id + member id) so tests and
            # tooling can address an individual member row / name / RPM cell
            # (CLAUDE.md objectName-uniqueness rule).
            mid = member.member_id
            row = QWidget()
            row.setObjectName(f"ControlCard_MemberRow_{control.id}_{mid}")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            name = QLabel(self._display_name(mid, member.member_label))
            name.setObjectName(f"ControlCard_MemberName_{control.id}_{mid}")
            name.setTextFormat(Qt.TextFormat.PlainText)  # DEC-231: untrusted alias/label
            self._member_row_name[mid] = name
            name.setProperty("class", "CardMeta")
            name.setStyleSheet("background: transparent;")
            row_layout.addWidget(name, 1)
            rpm = QLabel("")
            rpm.setObjectName(f"ControlCard_MemberRpm_{control.id}_{mid}")
            rpm.setProperty("class", "CardMeta")
            rpm.setStyleSheet("background: transparent;")
            row_layout.addWidget(rpm)
            self._member_rows_layout.addWidget(row)
            self._member_row_rpm[mid] = rpm
        self._member_rows.setVisible(bool(control.members))

    def _apply_chip(self, text: str, cls: str, tooltip: str = "") -> None:
        """Set the status chip text + style class + tooltip, and repolish.

        The tooltip is owned HERE, not by callers. When `set_skipped` wrote it
        directly onto `_status_chip`, every later transition inherited it — a
        card showing "Manual" carried "the daemon is not commanding these fans",
        which is the opposite of true while the user is commanding them. Qt maps
        `toolTip` to `QAccessible::Text::Description`, so a stale one is not
        merely cosmetic.
        """
        self._status_chip.setText(text)
        self._status_chip.setToolTip(tooltip)
        set_chip_class(self._status_chip, cls)

    def _restore_chip_after_manual(self) -> None:
        """Repaint whatever the daemon still says about this control.

        Leaving Manual must not blank the chip: `set_skipped` and
        `set_external_override` deliberately SUPPRESS the chip while the user
        holds Manual rather than discarding the state, so exiting Manual has to
        put it back. Blanking instead left the card with a live "Now: N%" and no
        chip at all, permanently — `set_output` early-returns while
        `_skipped_reason`/`_external_pct` is set, and the page reconciles only on
        a *delta*, so nothing ever repainted it. That is exactly the silence
        273-i exists to end, and the `_external_pct` half of it predates 273-i.
        """
        if self._skipped_reason is not None:
            text, tooltip = skipped_control_feedback(self._skipped_reason)
            self._apply_chip(text, "WarningChip", tooltip)
        elif self._external_pct is not None:
            self._apply_chip(f"External {self._external_pct}%", "InfoChip")
        else:
            self._apply_chip("", "")

    def _on_manual_toggled(self, checked: bool) -> None:
        """Reveal/hide the inline slider and signal the transient manual state."""
        if checked:
            # DEC-169: taking manual ownership supersedes any foreign-override
            # display — the user's own (fenced, renewable) override now wins.
            self._external_pct = None
            # P2-1: clamp the slider to the daemon-enforced floor so the user can
            # neither request nor see a value the daemon would floor-clamp away
            # (a 10% request on a 30%-floor pump ran at 30% but displayed "10%").
            self._manual_slider.setMinimum(round(self._effective_floor()))
        if checked and self._last_output_pct is not None:
            # Start manual at the current speed so the fan doesn't jump (clamped
            # up to the floor by the setMinimum above).
            self._manual_slider.blockSignals(True)
            self._manual_slider.setValue(round(self._last_output_pct))
            self._manual_slider.blockSignals(False)
            self._manual_pct_label.setText(f"{self._manual_slider.value()}%")
        self._manual_slider.setVisible(checked)
        self._manual_pct_label.setVisible(checked)
        self._output_label.setVisible(not checked)
        if checked:
            self._apply_chip("Manual", "WarningChip")
        else:
            self._restore_chip_after_manual()
        self.manual_toggled.emit(self._control.id, checked, self._manual_slider.value())

    def _on_manual_slider_changed(self, value: int) -> None:
        self._manual_pct_label.setText(f"{value}%")
        if self._manual_btn.isChecked():
            self.manual_value_changed.emit(self._control.id, value)

    def reflect_manual_applied(self, pct: int) -> None:
        """Show the value the daemon actually applied to the manual override.

        The daemon floor-clamps (and may thermal-clamp) the requested value; this
        makes the slider + label reflect the *granted* value rather than the raw
        request, so the card can never claim a speed the fan isn't running (P2-1).
        No-op unless manual is active; blocks signals so it doesn't re-emit an
        override, and setValue clamps into the slider's [floor, 100] range."""
        if not self._manual_btn.isChecked():
            return
        self._manual_slider.blockSignals(True)
        self._manual_slider.setValue(int(pct))  # coerce: a non-conforming daemon
        self._manual_slider.blockSignals(False)  # could send a non-int (security P3)
        self._manual_pct_label.setText(f"{self._manual_slider.value()}%")

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
        self._restore_chip_after_manual()

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

    def set_skipped(self, reason: str) -> None:
        """Show a "Not controlled" chip for a control the daemon cannot resolve
        (273-i).

        The daemon short-circuits an overridden control before curve resolution,
        so a skip and an override never co-occur — but a user-owned Manual state
        still wins the chip, because that is a local intent the next poll has yet
        to confirm and flickering it would be worse than delaying this.
        """
        self._skipped_reason = reason
        if self._manual_btn.isChecked() or self._external_pct is not None:
            # Suppressed, not discarded — `_restore_chip_after_manual` repaints
            # it when Manual ends. The `_external_pct` arm enforces the
            # "cannot co-occur" invariant LOCALLY rather than trusting the wire:
            # an override actively pins these fans, so "Not controlled" would be
            # a lie in the unsafe direction if a future daemon ever sent both.
            return
        text, tooltip = skipped_control_feedback(reason)
        self._apply_chip(text, "WarningChip", tooltip)

    def clear_skipped(self) -> None:
        """Drop the "Not controlled" chip (273-i) once the daemon stops reporting
        the control as skipped. Leaves a user-owned Manual state untouched;
        otherwise clears the chip and lets the next ``set_output`` repaint."""
        if self._skipped_reason is None:
            return
        self._skipped_reason = None
        if self._manual_btn.isChecked():
            return
        self._restore_chip_after_manual()

    def update_control(self, control: LogicalControl, curves: list[CurveConfig]) -> None:
        self._control = control
        # Keep the base's item id live (DEC-235): callers always update a card
        # with a same-id control, but re-setting keeps resized/size_reset correct
        # even if a card were ever recycled across ids (the pre-refactor code read
        # self._control.id live at emit time).
        self._item_id = control.id
        self._name_label.setText(control.name or "Unnamed")
        self._members_label.setText(self._members_text(control))
        self._role_icon.setStyleSheet(
            f"color: {self._role_color(control)}; background: transparent;"
        )
        self._rebuild_member_rows(control)
        self._update_fan_count(control)
        curve_name = self._curve_name(curves, control.curve_id)
        mode_text = "Manual" if control.mode == ControlMode.MANUAL else curve_name
        self._curve_label.setText(f"Curve: {mode_text}")
        self._update_no_members_state(control)
        self._update_min_pwm_badge(control)
        # P2-1 belt-and-suspenders (gui-finesse P3): if a floor change ever arrived
        # in place rather than via a card rebuild, keep the manual slider's minimum
        # in step so it can't request/show a now-sub-floor value.
        if self._manual_btn.isChecked():
            self._manual_slider.setMinimum(round(self._effective_floor()))
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

    def refresh_member_names(self) -> None:
        """Re-resolve member row names in place after a rename (DEC-228).

        Deliberately *not* a card rebuild: ``ControlsPage._refresh_controls_grid``
        releases every live manual override before destroying cards (DEC-163), so
        routing a rename through it would silently drop the user's override.
        """
        for member in self._control.members:
            row_label = self._member_row_name.get(member.member_id)
            if row_label is not None:
                row_label.setText(self._display_name(member.member_id, member.member_label))
        self._members_label.setText(self._members_text(self._control))

    def _members_text(self, control: LogicalControl) -> str:
        if not control.members:
            return "No outputs assigned"
        labels = [self._display_name(m.member_id, m.member_label) for m in control.members]
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
            set_chip_class(self._status_chip, "PageSubtitle")

    def _effective_floor(self) -> float:
        """The role-derived minimum PWM the daemon floor-clamps to (DEC-095/162):
        the larger of the user-set floor and the role-derived floor. Drives both
        the Min badge and the manual slider's minimum, so the slider can never
        request — or display — a value the daemon would clamp away (P2-1)."""
        return max(self._control.minimum_pct, control_minimum_pct(self._control.members))

    def _update_min_pwm_badge(self, control: LogicalControl) -> None:
        """Refresh the inline minimum-PWM badge from the control's effective floor."""
        # Show the larger of the user-set floor and the role-derived floor so
        # the user sees the clamp that actually applies. Hide entirely when
        # there is no floor (0%), so chassis-only roles authored before v4
        # don't display a misleading "Min: 0%".
        effective = self._effective_floor()
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
