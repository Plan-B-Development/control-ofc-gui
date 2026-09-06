"""The "Discover Control Path" dialog (AIO Phase 8 Batch 1 §6.1, §6.2).

A thin renderer over ``services.preflight_view`` and
``services.control_path_view``: every decision about what this says lives there
and is unit-tested headlessly. The dialog owns the poll timer and nothing else.

**The first state is the safety preflight**, which is what §6.1 asks for — the
user sees what the daemon checked before anything is driven, and an unsafe
condition blocks the Start button with the daemon's own reason attached. The GUI
does not decide any of that: it reads ``verdict`` and ``blocking`` off the report.

The sweep runs **daemon-side**. Closing this dialog, or the whole GUI crashing,
does not strand the header — the daemon restores it on every exit path on which
nothing else owns it. Cancelling is a courtesy to the user, not the mechanism
that keeps hardware safe.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from control_ofc.api.models import DIAGNOSTIC_CONTROL_PATH
from control_ofc.services.control_path_view import (
    ControlPathView,
    build_control_path_view,
)
from control_ofc.services.preflight_view import PreflightView, build_preflight_view
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

POLL_INTERVAL_MS = 1000

_COLUMNS = ("Tach channel", "Baseline", "Perturbed", "Change", "Direction", "Confidence")


class ControlPathDiscoveryDialog(ModalDialog):
    """Runs and renders one PWM-to-tach control-path discovery."""

    # Emitted for the worker thread; the page wires these to workers living on
    # their own QThreads.
    preflight_requested = Signal(str, str)
    start_requested = Signal(str)
    poll_requested = Signal()
    cancel_requested = Signal()

    def __init__(
        self,
        header_id: str,
        header_label: str,
        *,
        is_pump: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Discover Control Path", parent)
        self.setObjectName("ControlPathDiscoveryDialog")
        self._header_id = header_id
        self._header_label = header_label
        self._is_pump = is_pump
        self._started = False
        self._preflight = build_preflight_view(None)

        body = self.body_layout()

        self._header_lbl = QLabel(header_label)
        self._header_lbl.setObjectName("ControlPath_Label_header")
        self._header_lbl.setProperty("class", "CardTitle")
        body.addWidget(self._header_lbl)

        self._intro = QLabel(
            "Nudges this header up or down by a small, safe amount and watches "
            "every fan tachometer on the board to see which ones respond. That "
            "is how Control-OFC learns which output really drives which device, "
            "instead of trusting the numbering in sysfs."
        )
        self._intro.setObjectName("ControlPath_Label_intro")
        self._intro.setWordWrap(True)
        body.addWidget(self._intro)

        self._warnings = QLabel("\n\n".join(self._pre_run_warnings()))
        self._warnings.setObjectName("ControlPath_Label_warnings")
        self._warnings.setWordWrap(True)
        self._warnings.setProperty("class", "CardMeta")
        body.addWidget(self._warnings)

        # ── §6.1: the safety preflight, shown BEFORE anything is driven ──
        self._preflight_section = CollapsibleSection(
            "Safety preflight", expanded=True, object_name="ControlPath_Section_preflight"
        )
        self._preflight_grid = QGridLayout()
        self._preflight_grid.setContentsMargins(0, 0, 0, 0)
        self._preflight_holder = QWidget()
        self._preflight_holder.setObjectName("ControlPath_Widget_preflight")
        self._preflight_holder.setLayout(self._preflight_grid)
        self._preflight_section.add_widget(self._preflight_holder)
        body.addWidget(self._preflight_section)

        self._verdict_row = QHBoxLayout()
        self._verdict_holder = QWidget()
        self._verdict_holder.setObjectName("ControlPath_Widget_verdict")
        self._verdict_holder.setLayout(self._verdict_row)
        body.addWidget(self._verdict_holder)

        self._blocked_lbl = QLabel("")
        self._blocked_lbl.setObjectName("ControlPath_Label_blocked")
        self._blocked_lbl.setWordWrap(True)
        self._blocked_lbl.setVisible(False)
        body.addWidget(self._blocked_lbl)

        self._perturbation_lbl = QLabel("")
        self._perturbation_lbl.setObjectName("ControlPath_Label_perturbation")
        self._perturbation_lbl.setProperty("class", "CardMeta")
        self._perturbation_lbl.setVisible(False)
        body.addWidget(self._perturbation_lbl)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("ControlPath_Table_channels")
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_dense_table(self._table)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setAccessibleName("Tach channels observed during discovery")
        body.addWidget(self._table, 1)

        self._status_lbl = QLabel("Ready to start.")
        self._status_lbl.setObjectName("ControlPath_Label_status")
        self._status_lbl.setWordWrap(True)
        body.addWidget(self._status_lbl)

        # Restoration failure "must be surfaced prominently" (§1), so this is a
        # critical-toned label of its own rather than a line inside the notes.
        self._restore_lbl = QLabel("")
        self._restore_lbl.setObjectName("ControlPath_Label_restore")
        self._restore_lbl.setWordWrap(True)
        self._restore_lbl.setProperty("class", "CriticalChip")
        self._restore_lbl.setVisible(False)
        body.addWidget(self._restore_lbl)

        self._notes_lbl = QLabel("")
        self._notes_lbl.setObjectName("ControlPath_Label_notes")
        self._notes_lbl.setWordWrap(True)
        self._notes_lbl.setProperty("class", "CardMeta")
        self._notes_lbl.setVisible(False)
        body.addWidget(self._notes_lbl)

        self._start_btn = self.add_footer_button(
            "Start", "primary", object_name="ControlPath_Btn_start"
        )
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn = self.add_footer_button(
            "Cancel run", "secondary", object_name="ControlPath_Btn_cancel"
        )
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._close_btn = self.add_footer_button(
            "Close", "ghost", object_name="ControlPath_Btn_close"
        )
        self._close_btn.clicked.connect(self.reject)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll_requested.emit)

        self._render_preflight()

    def _pre_run_warnings(self) -> list[str]:
        """What the user is agreeing to before anything moves."""
        notes = [
            "While this runs, the daemon pauses its own fan control for this "
            "header and puts it back when the test finishes — including if you "
            "cancel, close this window, or the app quits."
        ]
        if self._is_pump:
            notes.append(
                "This header is treated as a pump. It will never be stopped and "
                "never driven below its safety floor: the test nudges it within "
                "its safe range only, and the daemon enforces that, not this "
                "window."
            )
        return notes

    # ── actions ──────────────────────────────────────────────────────

    @Slot()
    def _on_start(self) -> None:
        self._started = True
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._status_lbl.setText("Starting…")
        # No tuning arguments: the DAEMON owns the perturbation size, the
        # direction, the cycle count and the floor. Sending a duty from here
        # would put a second copy of a safety rule in the GUI.
        self.start_requested.emit(self._header_id)
        self._timer.start()

    @Slot()
    def _on_cancel(self) -> None:
        self._cancel_btn.setEnabled(False)
        self._status_lbl.setText("Cancelling — the header will be restored…")
        self.cancel_requested.emit()

    def request_preflight(self) -> None:
        """Ask for the safety preflight. Called by the page before ``exec()``."""
        self.preflight_requested.emit(self._header_id, DIAGNOSTIC_CONTROL_PATH)

    # ── rendering ────────────────────────────────────────────────────

    @Slot(object)
    def apply_preflight(self, report) -> None:
        """Render the daemon's safety verdict."""
        self._preflight = build_preflight_view(report)
        self._render_preflight()

    @Slot(str, str)
    def apply_preflight_error(self, category: str, message: str) -> None:
        """A preflight we could not fetch is advisory-unavailable, not a block.

        Refusing to offer the button because an advisory endpoint failed would
        make the feature less usable than it was before the preflight existed —
        and the daemon still runs its own guards on the POST regardless.
        """
        del category
        self._preflight = build_preflight_view(None, unavailable_reason=message)
        self._render_preflight()

    def _render_preflight(self) -> None:
        view: PreflightView = self._preflight

        while self._preflight_grid.count():
            child = self._preflight_grid.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

        for row, item in enumerate(view.rows):
            label = QLabel(item.label)
            label.setObjectName(f"ControlPath_PreflightLabel_{item.check_id}")
            self._preflight_grid.addWidget(label, row, 0)
            pill = StatusPill(
                item.state_word,
                item.tone,
                object_name=f"ControlPath_PreflightPill_{item.check_id}",
            )
            pill.setAccessibleName(f"{item.label}: {item.state_word}")
            self._preflight_grid.addWidget(pill, row, 1)
            detail = QLabel(item.detail)
            detail.setObjectName(f"ControlPath_PreflightDetail_{item.check_id}")
            detail.setWordWrap(True)
            detail.setProperty("class", "CardMeta")
            self._preflight_grid.addWidget(detail, row, 2)
        self._preflight_grid.setColumnStretch(2, 1)

        while self._verdict_row.count():
            child = self._verdict_row.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        if view.verdict_word:
            pill = StatusPill(
                view.verdict_word, view.verdict_tone, object_name="ControlPath_Pill_verdict"
            )
            pill.setAccessibleName(f"Safety preflight: {view.verdict_word}")
            self._verdict_row.addWidget(pill)
        self._verdict_row.addStretch(1)

        blocked_text = ""
        if view.blocked:
            blocked_text = "Cannot run this test:\n• " + "\n• ".join(view.blocking_reasons)
        elif view.unavailable:
            blocked_text = view.unavailable_reason
        self._blocked_lbl.setText(blocked_text)
        self._blocked_lbl.setVisible(bool(blocked_text))

        # `can_start` is the view-model's, which reads the daemon's verdict — the
        # dialog never re-derives it from the rows.
        if not self._started:
            self._start_btn.setEnabled(view.can_start)
            self._start_btn.setToolTip("" if view.can_start else "\n".join(view.blocking_reasons))

    def _is_ours(self, run) -> bool:
        """Is this snapshot about the header this dialog was opened for?

        ``GET /diagnostics/control-path`` serves ONE process-global slot, so a
        snapshot can legitimately describe a different header: a poll queued
        behind our own POST returns the *previous* run, and a second client
        owning the slot has the same effect. Rendering it under this dialog's
        label would attribute another header's relationship to this one — in the
        one feature whose entire purpose is a per-header answer.
        """
        theirs = getattr(run, "header_id", "") or ""
        return not theirs or theirs == self._header_id

    @Slot(object)
    def apply_run(self, status) -> None:
        """Render a status snapshot. Safe to call with ``None``."""
        run = getattr(status, "run", None) if status is not None else None
        if run is not None and not self._is_ours(run):
            return
        if run is None and self._started:
            # A run we started that the daemon no longer knows about — it
            # restarted mid-sweep. Terminal, not "not started yet"; without this
            # the timer polls forever against a dialog reading "Ready to start."
            self._timer.stop()
            self._cancel_btn.setEnabled(False)
            self._start_btn.setEnabled(True)
            self._status_lbl.setText(
                "The daemon no longer has this run — it may have restarted. It "
                "restores the header whenever it ends a run itself, and a "
                "restarted daemon takes control back on its next tick."
            )
            return
        view = build_control_path_view(run, header_label=self._header_label)
        self._render(view)
        if run is not None and not view.running and self._started:
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
        # words rather than dressing them as an error.
        self._status_lbl.setText(
            message if category == "unavailable" else f"Control-path discovery error: {message}"
        )

    def _render(self, view: ControlPathView) -> None:
        rows = len(view.candidates) + len(view.quiet)
        self._table.setRowCount(rows)
        for row, cand in enumerate(view.candidates):
            name = f"{cand.label} (monitor-only)" if cand.monitor_only else cand.label
            self._set_row(
                row,
                (
                    name,
                    cand.baseline_text,
                    cand.perturbed_text,
                    cand.change_text,
                    cand.direction_text,
                    f"{cand.confidence_word} — {cand.repeatability_text}",
                ),
            )
        for offset, quiet in enumerate(view.quiet):
            name = f"{quiet.label} (monitor-only)" if quiet.monitor_only else quiet.label
            self._set_row(
                len(view.candidates) + offset,
                (
                    name,
                    quiet.baseline_text,
                    quiet.perturbed_text,
                    "—",
                    "no meaningful response",
                    "—",
                ),
            )

        status = view.status_text
        if view.progress_text:
            status = f"{status}  ({view.progress_text})"
        self._status_lbl.setText(status)

        self._perturbation_lbl.setText(
            f"Perturbation: {view.perturbation_text}" if view.perturbation_text else ""
        )
        self._perturbation_lbl.setVisible(bool(view.perturbation_text))

        while self._verdict_row.count():
            child = self._verdict_row.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        if view.has_result:
            rel = StatusPill(
                view.relationship_word,
                view.relationship_tone,
                object_name="ControlPath_Pill_relationship",
            )
            rel.setAccessibleName(f"Relationship: {view.relationship_word}")
            self._verdict_row.addWidget(rel)
            conf = StatusPill(
                view.confidence_word,
                view.confidence_tone,
                object_name="ControlPath_Pill_confidence",
            )
            conf.setAccessibleName(f"Confidence: {view.confidence_word}")
            self._verdict_row.addWidget(conf)
        elif self._preflight.verdict_word:
            pill = StatusPill(
                self._preflight.verdict_word,
                self._preflight.verdict_tone,
                object_name="ControlPath_Pill_verdict",
            )
            pill.setAccessibleName(f"Safety preflight: {self._preflight.verdict_word}")
            self._verdict_row.addWidget(pill)
        self._verdict_row.addStretch(1)

        self._restore_lbl.setText(view.restore_warning)
        self._restore_lbl.setVisible(bool(view.restore_warning))

        notes = list(view.notes)
        if view.resolution_text:
            notes.append(view.resolution_text)
        self._notes_lbl.setText("\n\n".join(notes))
        self._notes_lbl.setVisible(bool(notes))

    def _set_row(self, row: int, texts: tuple[str, ...]) -> None:
        for col, text in enumerate(texts):
            cell = QTableWidgetItem(text)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, col, cell)

    # ── lifecycle ────────────────────────────────────────────────────

    def stop_polling(self) -> None:
        self._timer.stop()

    def reject(self) -> None:  # Qt override
        self._timer.stop()
        super().reject()
