"""The "Characterise PWM Response" dialog (AIO-MB Phase 3).

A thin renderer over ``services.characterization_view``: every decision about
what this says lives there and is unit-tested headlessly. The dialog owns the
worker thread, the 1 Hz poll timer, and nothing else.

The sweep itself runs **daemon-side**, which is what ``AIO-Phase3.md`` asks for:
closing this dialog — or the whole GUI crashing — does not strand the header,
because the daemon restores it on every exit path on which nothing else owns it.
Cancelling is therefore a courtesy to the user, not the mechanism that keeps
hardware safe. Where the restore is deliberately skipped — a thermal force, or
daemon shutdown — the run reports which, and the header is left high, never low.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from control_ofc.services.characterization_view import (
    CharacterizationView,
    build_characterization_view,
    pre_run_warnings,
)
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.tables import apply_dense_table

POLL_INTERVAL_MS = 1000

_COLUMNS = ("PWM", "Readback", "RPM", "Result")


class PwmCharacterizationDialog(ModalDialog):
    """Runs and renders one PWM/RPM characterisation sweep."""

    # Emitted for the worker thread; the page/dialog wires these to a
    # ``_CharacterizationWorker`` living on its own QThread.
    start_requested = Signal(str, object, object)
    poll_requested = Signal()
    cancel_requested = Signal()

    def __init__(
        self,
        header_id: str,
        header_label: str,
        *,
        is_pump: bool,
        header=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Characterise PWM Response", parent)
        self.setObjectName("PwmCharacterizationDialog")
        self._header_id = header_id
        self._header_label = header_label
        self._is_pump = is_pump
        self._started = False
        self._finished = False

        body = self.body_layout()

        self._header_lbl = QLabel(header_label)
        self._header_lbl.setObjectName("Char_Label_header")
        self._header_lbl.setProperty("class", "CardTitle")
        body.addWidget(self._header_lbl)

        self._intro = QLabel(
            "Holds this header at a series of PWM duties and records what the "
            "hardware reports back and what the fan actually does. This is a "
            "deeper test than “Test PWM Control”, and it takes longer."
        )
        self._intro.setObjectName("Char_Label_intro")
        self._intro.setWordWrap(True)
        body.addWidget(self._intro)

        self._warnings = QLabel("\n\n".join(pre_run_warnings(header, is_pump=is_pump)))
        self._warnings.setObjectName("Char_Label_warnings")
        self._warnings.setWordWrap(True)
        self._warnings.setProperty("class", "CardMeta")
        body.addWidget(self._warnings)

        self._verdict_row = QHBoxLayout()
        self._verdict_holder = QWidget()
        self._verdict_holder.setObjectName("Char_Widget_verdicts")
        self._verdict_holder.setLayout(self._verdict_row)
        self._verdict_holder.setVisible(False)
        body.addWidget(self._verdict_holder)

        self._range_lbl = QLabel("")
        self._range_lbl.setObjectName("Char_Label_range")
        self._range_lbl.setVisible(False)
        body.addWidget(self._range_lbl)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("Char_Table_points")
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_dense_table(self._table)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setAccessibleName("Measured PWM and RPM points")
        body.addWidget(self._table, 1)

        self._status_lbl = QLabel("Ready to start.")
        self._status_lbl.setObjectName("Char_Label_status")
        self._status_lbl.setWordWrap(True)
        body.addWidget(self._status_lbl)

        self._notes_lbl = QLabel("")
        self._notes_lbl.setObjectName("Char_Label_notes")
        self._notes_lbl.setWordWrap(True)
        self._notes_lbl.setProperty("class", "CardMeta")
        self._notes_lbl.setVisible(False)
        body.addWidget(self._notes_lbl)

        self._start_btn = self.add_footer_button("Start", "primary", object_name="Char_Btn_start")
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn = self.add_footer_button(
            "Cancel run", "secondary", object_name="Char_Btn_cancel"
        )
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._close_btn = self.add_footer_button("Close", "ghost", object_name="Char_Btn_close")
        self._close_btn.clicked.connect(self.reject)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll_requested.emit)

    # ── actions ──────────────────────────────────────────────────────

    @Slot()
    def _on_start(self) -> None:
        self._started = True
        self._finished = False
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._status_lbl.setText("Starting…")
        # `None` for both tuning arguments on purpose: the DAEMON owns the point
        # list and the settle window, including the pump floor. Sending a
        # client-side list here would put a second copy of a safety rule in the
        # GUI, and the two would drift.
        self.start_requested.emit(self._header_id, None, None)
        self._timer.start()

    @Slot()
    def _on_cancel(self) -> None:
        self._cancel_btn.setEnabled(False)
        self._status_lbl.setText("Cancelling — the header will be restored…")
        self.cancel_requested.emit()

    # ── rendering ────────────────────────────────────────────────────

    def _is_ours(self, run) -> bool:
        """Is this snapshot about the header this dialog was opened for?

        `GET /diagnostics/characterization` serves ONE process-global slot, so a
        snapshot can legitimately describe a different header: a poll queued
        behind our own blocking POST returns the *previous* run, and any second
        client owning the slot has the same effect. Rendering it under this
        dialog's label would attribute another header's points, verdicts and
        notes to this one — in the single feature whose whole purpose is a
        per-header verdict (`AUD2-a`).

        An empty `header_id` is accepted: it is what a daemon too old to send one
        would produce, and refusing those would blank the dialog instead.
        """
        theirs = getattr(run, "header_id", "") or ""
        return not theirs or theirs == self._header_id

    @Slot(object)
    def apply_run(self, run) -> None:
        """Render a run snapshot. Safe to call with ``None`` (nothing started)."""
        if run is not None and not self._is_ours(run):
            return
        # A run we started that the daemon no longer knows about (it restarted
        # mid-sweep, so GET now 404s -> None) is terminal, not "not started yet".
        # Without this the poll timer runs forever against a dialog that reads
        # "Ready to start." — a silent stall rather than an answer.
        if run is None and self._started:
            self._timer.stop()
            self._cancel_btn.setEnabled(False)
            self._start_btn.setEnabled(True)
            self._status_lbl.setText(
                "The daemon no longer has this run — it may have restarted. "
                "It restores the header whenever it ends a sweep itself, and a "
                "restarted daemon takes control back on its next tick."
            )
            return
        view = build_characterization_view(run, header_label=self._header_label)
        self._render(view)
        if run is not None and not view.running and self._started:
            self._finished = True
            self._timer.stop()
            self._cancel_btn.setEnabled(False)
            self._start_btn.setEnabled(True)
            self._start_btn.setText("Run again")

    @Slot(str, str)
    def apply_error(self, category: str, message: str) -> None:
        self._timer.stop()
        self._cancel_btn.setEnabled(False)
        self._start_btn.setEnabled(True)
        # A safety refusal is protection, not failure — show the daemon's own
        # words rather than dressing them as an error (the shared taxonomy in
        # `diagnostics_workers._is_soft_safety_refusal`).
        self._status_lbl.setText(
            message if category == "unavailable" else f"Characterisation error: {message}"
        )

    def _render(self, view: CharacterizationView) -> None:
        self._table.setRowCount(len(view.rows))
        for row, item in enumerate(view.rows):
            for col, text in enumerate((item.pwm, item.readback, item.rpm, item.result)):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(row, col, cell)
        self._table.scrollToBottom()

        status = view.status_text
        if view.progress_text:
            status = f"{status}  ({view.progress_text})"
        self._status_lbl.setText(status)

        while self._verdict_row.count():
            child = self._verdict_row.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        for idx, chip in enumerate(view.verdicts):
            label = QLabel(f"{chip.label}:")
            label.setObjectName(f"Char_Label_verdict{idx}")
            self._verdict_row.addWidget(label)
            pill = StatusPill(chip.value, chip.state, object_name=f"Char_Pill_verdict{idx}")
            pill.setAccessibleName(f"{chip.label}: {chip.value}")
            self._verdict_row.addWidget(pill)
        self._verdict_row.addStretch(1)
        self._verdict_holder.setVisible(bool(view.verdicts))

        self._range_lbl.setText(
            f"Observed range: {view.observed_range}" if view.observed_range else ""
        )
        self._range_lbl.setVisible(bool(view.observed_range))

        self._notes_lbl.setText("\n\n".join(view.notes))
        self._notes_lbl.setVisible(bool(view.notes))

    # ── lifecycle ────────────────────────────────────────────────────

    def stop_polling(self) -> None:
        self._timer.stop()

    def reject(self) -> None:  # Qt override
        self._timer.stop()
        super().reject()
