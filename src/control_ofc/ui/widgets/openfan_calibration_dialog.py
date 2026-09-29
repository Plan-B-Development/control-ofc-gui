"""The "Calibrate OpenFan Channel" dialog (DEC-452 daemon, DEC-453 GUI).

A thin renderer over ``services.openfan_calibration_view``: every decision about
what this says lives there and is unit-tested headlessly. The dialog owns the
channel picker, the per-channel pump confirmation, the 1 Hz poll timer and
nothing else.

The walk runs **daemon-side**. Closing the dialog — or the GUI crashing — does
not strand the channel: the daemon kicks a fan it may have left stopped and
restores the original duty on every exit path. Closing mid-run therefore ASKS
(DEC-453): the run holds every curve paused and this channel near 0 %, so
leaving it running silently is the surprising choice, but it is a safe one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from control_ofc.services.openfan_calibration_view import (
    DEMO_REFUSAL,
    NO_CHANNELS_TEXT,
    PRE_RUN_WARNINGS,
    CalibrationView,
    ChannelOption,
    build_calibration_view,
    pump_confirmation_text,
)
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.widgets.pwm_response_chart import PwmResponseChart

POLL_INTERVAL_MS = 1000

_COLUMNS = ("Duty", "Direction", "RPM", "Result")

#: What `_ask_close_mid_run` answers.
CLOSE_STOP = "stop"
CLOSE_KEEP = "keep"
CLOSE_STAY = "stay"


def _clear_layout(layout) -> None:
    while layout.count():
        child = layout.takeAt(0)
        widget = child.widget()
        if widget is not None:
            widget.deleteLater()


def _set_class(widget: QWidget, css_class: str) -> None:
    """Swap a QSS class and repolish, so a tone change actually repaints."""
    if widget.property("class") == css_class:
        return
    widget.setProperty("class", css_class)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class OpenFanCalibrationDialog(ModalDialog):
    """Runs and renders one OpenFan channel calibration."""

    start_requested = Signal(int)  # channel
    poll_requested = Signal()
    cancel_requested = Signal()

    def __init__(
        self,
        options: list[ChannelOption],
        *,
        demo: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Calibrate OpenFan Channel", parent)
        self.setObjectName("OpenFanCalibrationDialog")
        self._demo = demo
        self._options: list[ChannelOption] = []
        self._started = False
        self._finished = False
        #: The run this dialog started, learned from the `202`. The GET serves
        #: ONE process-global slot, so a snapshot of someone else's run — a
        #: different channel, or an older run — must never be rendered here.
        self._run_id = ""
        self._channel: int | None = None
        self._fan_id = ""
        #: Whether a DELETE can still stop the run: only during the walk itself.
        self._can_cancel = False
        self._channel_label = ""
        # P8-e: one poll in flight at a time (see PwmCharacterizationDialog).
        self._poll_in_flight = False

        body = self.body_layout()

        self._intro = QLabel("\n\n".join(PRE_RUN_WARNINGS))
        self._intro.setObjectName("OfanCal_Label_intro")
        self._intro.setWordWrap(True)
        body.addWidget(self._intro)

        picker_row = QHBoxLayout()
        self._channel_lbl = QLabel("Channel:")
        self._channel_lbl.setObjectName("OfanCal_Label_channel")
        picker_row.addWidget(self._channel_lbl)
        self._channel_combo = QComboBox()
        self._channel_combo.setObjectName("OfanCal_Combo_channel")
        name_value_control(self._channel_combo, self._channel_lbl)
        self._channel_combo.currentIndexChanged.connect(self._on_channel_changed)
        picker_row.addWidget(self._channel_combo, 1)
        picker_holder = QWidget()
        picker_holder.setObjectName("OfanCal_Widget_picker")
        picker_holder.setLayout(picker_row)
        body.addWidget(picker_holder)

        # DEC-453: the acknowledgement the daemon requires is this box, per
        # channel. It is unticked on every channel change and after every run,
        # so a consent given for one fan can never carry over to another.
        self._pump_check = QCheckBox("")
        self._pump_check.setObjectName("OfanCal_Check_notPump")
        self._pump_check.toggled.connect(self._refresh_start)
        body.addWidget(self._pump_check)

        self._blocked_lbl = QLabel("")
        self._blocked_lbl.setObjectName("OfanCal_Label_blocked")
        self._blocked_lbl.setWordWrap(True)
        self._blocked_lbl.setProperty("class", "CardMeta")
        self._blocked_lbl.setVisible(False)
        body.addWidget(self._blocked_lbl)

        self._status_lbl = QLabel("Ready to start.")
        self._status_lbl.setObjectName("OfanCal_Label_status")
        self._status_lbl.setWordWrap(True)
        body.addWidget(self._status_lbl)

        self._advice_lbl = QLabel("")
        self._advice_lbl.setObjectName("OfanCal_Label_advice")
        self._advice_lbl.setWordWrap(True)
        self._advice_lbl.setVisible(False)
        body.addWidget(self._advice_lbl)

        self._summary_grid = QGridLayout()
        self._summary_grid.setContentsMargins(0, 0, 0, 0)
        self._summary_holder = QWidget()
        self._summary_holder.setObjectName("OfanCal_Widget_summary")
        self._summary_holder.setLayout(self._summary_grid)
        self._summary_holder.setVisible(False)
        body.addWidget(self._summary_holder)

        # DEC-453: the existing chart, the descent as its falling series and the
        # ascent as its rising one, so the gap between stop and restart shows.
        self._chart = PwmResponseChart(object_name="OfanCal_Chart_walk")
        self._chart.setVisible(False)
        body.addWidget(self._chart, 1)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("OfanCal_Table_points")
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_dense_table(self._table)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setAccessibleName("Calibration duties and RPM")
        self._table.setVisible(False)
        body.addWidget(self._table, 1)

        self._notes_lbl = QLabel("")
        self._notes_lbl.setObjectName("OfanCal_Label_notes")
        self._notes_lbl.setWordWrap(True)
        self._notes_lbl.setProperty("class", "CardMeta")
        self._notes_lbl.setVisible(False)
        body.addWidget(self._notes_lbl)

        self._start_btn = self.add_footer_button(
            "Start", "primary", object_name="OfanCal_Btn_start"
        )
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn = self.add_footer_button(
            "Cancel run", "secondary", object_name="OfanCal_Btn_cancel"
        )
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._close_btn = self.add_footer_button("Close", "ghost", object_name="OfanCal_Btn_close")
        self._close_btn.clicked.connect(self.reject)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._on_poll_tick)

        self.set_channels(options)

    # ── channel picker ───────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._started and not self._finished

    def set_channels(self, options: list[ChannelOption]) -> None:
        """Refresh the picker (live RPM), keeping the selected channel.

        Rebuilding the list must not read as a channel CHANGE: the pump
        confirmation is kept when the same channel is still selected afterwards,
        and dropped only when the selection really moved.

        This runs on every 1 Hz poll, so when the channel set is unchanged only
        the item texts are rewritten: clearing and re-adding the items each
        second would reset an open popup's highlight under the user's pointer
        and re-announce the whole list to a screen reader.
        """
        current = self.selected_option()
        if options and [o.fan_id for o in options] == [o.fan_id for o in self._options]:
            self._options = list(options)
            for index, opt in enumerate(self._options):
                self._channel_combo.setItemText(index, opt.text)
            self._refresh_start()
            return
        self._options = list(options)
        self._channel_combo.blockSignals(True)
        self._channel_combo.clear()
        for opt in self._options:
            self._channel_combo.addItem(opt.text, opt.fan_id)
        index = 0
        if current is not None:
            found = self._channel_combo.findData(current.fan_id)
            index = found if found >= 0 else 0
        if self._options:
            self._channel_combo.setCurrentIndex(index)
        self._channel_combo.blockSignals(False)
        after = self.selected_option()
        if current is None or after is None or after.fan_id != current.fan_id:
            self._on_channel_changed()
        else:
            self._refresh_start()

    def selected_option(self) -> ChannelOption | None:
        index = self._channel_combo.currentIndex()
        if 0 <= index < len(self._options):
            return self._options[index]
        return None

    @Slot()
    def _on_channel_changed(self, *_args) -> None:
        opt = self.selected_option()
        self._pump_check.blockSignals(True)
        self._pump_check.setChecked(False)
        self._pump_check.blockSignals(False)
        self._pump_check.setText(pump_confirmation_text(opt.label) if opt else "")
        self._pump_check.setVisible(opt is not None)
        self._refresh_start()

    def _refresh_start(self, *_args) -> None:
        running = self.is_running
        has_channel = self.selected_option() is not None
        blocked = ""
        if self._demo:
            blocked = DEMO_REFUSAL
        elif not has_channel:
            blocked = NO_CHANNELS_TEXT
        self._blocked_lbl.setText(blocked)
        self._blocked_lbl.setVisible(bool(blocked))
        self._channel_combo.setEnabled(not running and has_channel)
        self._pump_check.setEnabled(not running and not self._demo and has_channel)
        can_start = not running and not self._demo and has_channel and self._pump_check.isChecked()
        self._start_btn.setEnabled(can_start)
        if blocked:
            self._start_btn.setToolTip(blocked)
        elif not running and not self._pump_check.isChecked():
            self._start_btn.setToolTip("Confirm first that this channel does not power a pump.")
        else:
            self._start_btn.setToolTip("")

    # ── actions ──────────────────────────────────────────────────────

    @Slot()
    def _on_start(self) -> None:
        opt = self.selected_option()
        # Belt and braces: the button is disabled in each of these cases, but
        # the acknowledgement it leads to is a safety statement, so the rule is
        # enforced where the request is made too.
        if opt is None or self._demo or not self._pump_check.isChecked() or self.is_running:
            return
        self._started = True
        self._finished = False
        self._run_id = ""
        self._channel = opt.channel
        self._fan_id = opt.fan_id
        self._channel_label = opt.label
        self._can_cancel = True
        self._poll_in_flight = False
        self._cancel_btn.setEnabled(True)
        self._status_lbl.setText("Starting…")
        self._refresh_start()
        self.start_requested.emit(opt.channel)
        self._timer.start()

    @Slot()
    def _on_poll_tick(self) -> None:
        if self._poll_in_flight:
            return
        self._poll_in_flight = True
        self.poll_requested.emit()

    @Slot()
    def _on_cancel(self) -> None:
        self._can_cancel = False
        self._cancel_btn.setEnabled(False)
        self._status_lbl.setText(
            "Stopping — the daemon starts the fan again if it may be stopped, then "
            "restores its speed…"
        )
        self.cancel_requested.emit()

    # ── rendering ────────────────────────────────────────────────────

    def _is_ours(self, run) -> bool:
        """Is this snapshot the run this dialog started?

        The first snapshot after Start is the `202` (one worker thread, queued
        in order), so it names the run; every later one must match it. Before a
        run id is known, the channel must at least match.
        """
        if self._run_id:
            return getattr(run, "run_id", "") == self._run_id
        theirs = getattr(run, "fan_id", "") or ""
        return getattr(run, "channel", None) == self._channel and theirs in ("", self._fan_id)

    @Slot(object)
    def apply_run(self, run) -> None:
        """Render a run snapshot. ``None`` means the daemon has no run."""
        self._poll_in_flight = False
        # A reply queued before the run ended must not repaint a finished
        # dialog with a stale "Walking down…".
        if not self._started or self._finished:
            return
        if run is None:
            # A run we started that the daemon no longer knows about (it
            # restarted mid-walk, so the GET 404s) is terminal.
            self._end_run()
            self._status_lbl.setText(
                "The daemon no longer has this run — it may have restarted. It "
                "restores the channel whenever it ends a calibration itself, and a "
                "restarted daemon takes control back on its next tick."
            )
            return
        if not self._is_ours(run):
            return
        if not self._run_id:
            self._run_id = getattr(run, "run_id", "") or ""
        view = build_calibration_view(run, channel_label=self._channel_label)
        self._render(view)
        if not view.can_cancel:
            self._can_cancel = False
            self._cancel_btn.setEnabled(False)
        if view.finished:
            self._end_run()
            self._start_btn.setText("Run again")

    @Slot(str, str)
    def apply_error(self, category: str, message: str) -> None:
        """Show a failed request — and end the run only if it never started.

        Once the `202` has named the run, the daemon owns it: a poll that timed
        out, or a cancel that lost the race with the run's own end (`409`), says
        nothing about whether the walk is still going. Ending here would stop
        polling while the channel may still be near 0 %, drop the close-mid-run
        question, and never show the result. So the message is shown and polling
        carries on; the next snapshot, or a `404` (the run is gone), decides.
        """
        self._poll_in_flight = False
        if not self._run_id:
            self._end_run()
        # A safety refusal is protection, not failure — show the daemon's own
        # words rather than dressing them as an error.
        self._status_lbl.setText(
            message if category == "unavailable" else f"Calibration error: {message}"
        )

    def _end_run(self) -> None:
        self._timer.stop()
        self._finished = True
        self._can_cancel = False
        self._cancel_btn.setEnabled(False)
        # A consent is for one run: the next one asks again.
        self._pump_check.setChecked(False)
        self._refresh_start()

    def _render(self, view: CalibrationView) -> None:
        self._status_lbl.setText(view.status_text)

        self._advice_lbl.setText(view.advice)
        _set_class(self._advice_lbl, "WarningChip" if view.advice_state == "warn" else "CardTitle")
        self._advice_lbl.setVisible(bool(view.advice))

        _clear_layout(self._summary_grid)
        for row, item in enumerate(view.summary_rows):
            label = QLabel(item.label)
            label.setObjectName(f"OfanCal_SummaryLabel_{row}")
            label.setProperty("class", "CardMeta")
            self._summary_grid.addWidget(label, row, 0)
            value = QLabel(item.value)
            value.setObjectName(f"OfanCal_SummaryValue_{row}")
            value.setWordWrap(True)
            self._summary_grid.addWidget(value, row, 1)
        self._summary_grid.setColumnStretch(1, 1)
        self._summary_holder.setVisible(bool(view.summary_rows))

        self._chart.set_curve(view.curve)
        self._chart.setVisible(view.curve.has_data)

        self._table.setRowCount(len(view.rows))
        for row, item in enumerate(view.rows):
            for col, text in enumerate((item.duty, item.phase, item.rpm, item.observation)):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, col, cell)
        self._table.setVisible(bool(view.rows))
        if view.rows:
            self._table.scrollToBottom()

        self._notes_lbl.setText("\n\n".join(view.notes))
        self._notes_lbl.setVisible(bool(view.notes))

    # ── lifecycle ────────────────────────────────────────────────────

    def set_theme(self, tokens) -> None:
        """Forward a live theme switch to the chart (latent under ``exec()``;
        ``HardwarePage.set_theme`` forwards here, as it does to the
        characterisation dialog)."""
        self._chart.set_theme(tokens)

    def stop_polling(self) -> None:
        self._timer.stop()

    def _ask_close_mid_run(self) -> str:
        """Stop the run, keep it running daemon-side, or stay (DEC-453)."""
        box = QMessageBox(self)
        box.setObjectName("OfanCal_Msg_closeMidRun")
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Calibration still running")
        box.setText(
            "The calibration is still running. Curve control stays paused and this "
            "channel stays near its test duty until it ends."
        )
        box.setInformativeText(
            "Stop it now, or keep it running and close this window? The daemon "
            "restores the channel itself either way."
        )
        stop = box.addButton("Stop calibration", QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("Keep running", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Stay here", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is stop:
            return CLOSE_STOP
        if clicked is keep:
            return CLOSE_KEEP
        return CLOSE_STAY

    def reject(self) -> None:  # Qt override — Close, Escape and the title-bar X
        # Only while a DELETE can still act: during the kick or the restore the
        # run is seconds from its end and nothing the user picks would change it.
        if self.is_running and self._can_cancel:
            answer = self._ask_close_mid_run()
            if answer == CLOSE_STAY:
                return
            if answer == CLOSE_STOP:
                self.cancel_requested.emit()
        self._timer.stop()
        super().reject()
