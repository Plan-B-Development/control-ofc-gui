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
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import DIAGNOSTIC_CHARACTERIZATION
from control_ofc.services.characterization_view import (
    CharacterizationView,
    build_characterization_view,
    pre_run_warnings,
)
from control_ofc.services.preflight_view import PreflightView, build_preflight_view
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.pwm_response_chart import PwmResponseChart

POLL_INTERVAL_MS = 1000

# AIO-MB Phase 6 §9 adds Response and Settling. Both values have been on the
# wire and parsed since Phase 3 (`CharPoint.first_change_ms` / `settle_ms`) and
# were rendered nowhere — the exact "a field that is parsed but never read
# outside tests is decoration" trap CLAUDE.md records from DEC-301.
# "Mode" is the point's own `pwmN_enable` (WIRE-u) — the observation the
# sweep-level "interference detected" verdict is derived from.
# DEC-334 adds Direction and Stability. Direction is load-bearing rather than
# decorative: a bidirectional sweep visits most duties TWICE, and without it the
# table shows two contradictory rows for the same duty with no way to tell which
# leg produced which.
_COLUMNS = (
    "PWM",
    "Direction",
    "Readback",
    "RPM",
    "Mode",
    "Response",
    "Settling",
    "Stability",
    "Result",
)


def _clear_layout(layout) -> None:
    while layout.count():
        child = layout.takeAt(0)
        widget = child.widget()
        if widget is not None:
            widget.deleteLater()


def _fill_rows(grid, rows, prefix: str) -> None:
    """Render label/value pairs, one per grid row."""
    _clear_layout(grid)
    for row, item in enumerate(rows):
        label = QLabel(item.label)
        label.setObjectName(f"{prefix}Label_{row}")
        label.setProperty("class", "CardMeta")
        grid.addWidget(label, row, 0)
        value = QLabel(item.value)
        value.setObjectName(f"{prefix}Value_{row}")
        value.setWordWrap(True)
        grid.addWidget(value, row, 1)
    grid.setColumnStretch(1, 1)


def _fill_provenance(grid, rows) -> None:
    """§8.6: a value is never shown without how it was obtained.

    The provenance token is rendered as a pill beside the figure rather than
    buried in a tooltip — the Overview's rule is that a derived or user-supplied
    value must never be silently promoted into a hardware observation, and a
    label nobody hovers over is silent.
    """
    _clear_layout(grid)
    for row, item in enumerate(rows):
        label = QLabel(item.label)
        label.setObjectName(f"Char_ProvLabel_{row}")
        label.setProperty("class", "CardMeta")
        grid.addWidget(label, row, 0)
        value = QLabel(item.value)
        value.setObjectName(f"Char_ProvValue_{row}")
        grid.addWidget(value, row, 1)
        pill = StatusPill(item.provenance, "neutral", object_name=f"Char_ProvPill_{row}")
        pill.setAccessibleName(f"{item.label} provenance: {item.provenance}")
        grid.addWidget(pill, row, 2)
    grid.setColumnStretch(1, 1)


class PwmCharacterizationDialog(ModalDialog):
    """Runs and renders one PWM/RPM characterisation sweep."""

    # Emitted for the worker thread; the page/dialog wires these to a
    # ``_CharacterizationWorker`` living on its own QThread.
    # DEC-334 adds the two behaviour inputs. Still `object` rather than typed
    # slots so `None` means "let the daemon decide", which is what every one of
    # these arguments means.
    start_requested = Signal(str, object, object, object, object)
    poll_requested = Signal()
    cancel_requested = Signal()
    #: AIO Phase 8 Batch 1 §6.1, wired here by DEC-334. The daemon's preflight
    #: already accepts `pwm_characterization`, so this is pure client wiring.
    preflight_requested = Signal(str, str)

    def __init__(
        self,
        header_id: str,
        header_label: str,
        *,
        is_pump: bool,
        header=None,
        capabilities=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Characterise PWM Response", parent)
        self.setObjectName("PwmCharacterizationDialog")
        self._header_id = header_id
        self._header_label = header_label
        self._is_pump = is_pump
        self._started = False
        self._finished = False
        self._preflight = PreflightView()
        # P8-e: the 1 Hz timer must not queue a second poll behind an
        # outstanding one. On a busy socket that turns a slow reply into an
        # unbounded backlog, and every queued reply then renders in turn.
        self._poll_in_flight = False
        self._behaviour_supported = bool(
            getattr(getattr(capabilities, "control", None), "pwm_behaviour_characterization", False)
        )

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

        # AIO Phase 8 Batch 1 §6.1: "when an active diagnostic is started",
        # show the daemon's own safety statement first. The control-path dialog
        # has had this since Batch 1; this one did not, and the asymmetry meant
        # the longer, more invasive of the two diagnostics was the one that
        # warned you less. Expanded by default — a safety statement folded away
        # is a safety statement nobody reads.
        self._preflight_section = CollapsibleSection(
            "Safety preflight",
            object_name="Char_Section_preflight",
            expanded=True,
        )
        self._preflight_grid = QGridLayout()
        self._preflight_grid.setContentsMargins(0, 0, 0, 0)
        preflight_body = QWidget()
        preflight_body.setObjectName("Char_Widget_preflightBody")
        preflight_body.setLayout(self._preflight_grid)
        self._preflight_section.add_widget(preflight_body)
        body.addWidget(self._preflight_section)

        self._blocked_lbl = QLabel("")
        self._blocked_lbl.setObjectName("Char_Label_blocked")
        self._blocked_lbl.setWordWrap(True)
        self._blocked_lbl.setProperty("class", "CardMeta")
        self._blocked_lbl.setVisible(False)
        body.addWidget(self._blocked_lbl)

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

        # §8.2's compact block: "Avoid overwhelming casual users." This is the
        # answer most people need; everything finer lives in §8.4's section.
        self._summary_grid = QGridLayout()
        self._summary_grid.setContentsMargins(0, 0, 0, 0)
        self._summary_holder = QWidget()
        self._summary_holder.setObjectName("Char_Widget_summary")
        self._summary_holder.setLayout(self._summary_grid)
        self._summary_holder.setVisible(False)
        body.addWidget(self._summary_holder)

        # §8.5. Toned as a caution, never as a failure: the spec forbids a
        # generic red "hardware failed" for an out-of-range response.
        self._override_lbl = QLabel("")
        self._override_lbl.setObjectName("Char_Label_overrideWarning")
        self._override_lbl.setWordWrap(True)
        self._override_lbl.setProperty("class", "WarningChip")
        self._override_lbl.setVisible(False)
        body.addWidget(self._override_lbl)

        self._chart = PwmResponseChart(object_name="Char_Chart_response")
        self._chart.setVisible(False)
        body.addWidget(self._chart, 1)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("Char_Table_points")
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_dense_table(self._table)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setAccessibleName("Measured PWM and RPM points")
        body.addWidget(self._table, 1)

        # §8.4/§8.6, collapsed: "Advanced detail may be collapsible."
        self._detail_section = CollapsibleSection(
            "Timing, stability and provenance",
            object_name="Char_Section_detail",
            expanded=False,
        )
        detail_body = QWidget()
        detail_body.setObjectName("Char_Widget_detailBody")
        detail_layout = QVBoxLayout(detail_body)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self._detail_grid = QGridLayout()
        self._detail_grid.setContentsMargins(0, 0, 0, 0)
        detail_grid_holder = QWidget()
        detail_grid_holder.setObjectName("Char_Widget_detailGrid")
        detail_grid_holder.setLayout(self._detail_grid)
        detail_layout.addWidget(detail_grid_holder)
        self._provenance_grid = QGridLayout()
        self._provenance_grid.setContentsMargins(0, 0, 0, 0)
        provenance_holder = QWidget()
        provenance_holder.setObjectName("Char_Widget_provenance")
        provenance_holder.setLayout(self._provenance_grid)
        detail_layout.addWidget(provenance_holder)
        self._detail_section.add_widget(detail_body)
        self._detail_section.setVisible(False)
        body.addWidget(self._detail_section)

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
        self._timer.timeout.connect(self._on_poll_tick)

    # ── actions ──────────────────────────────────────────────────────

    @Slot()
    def _on_start(self) -> None:
        self._started = True
        self._finished = False
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._status_lbl.setText("Starting…")
        # `None` for the tuning arguments on purpose: the DAEMON owns the point
        # list and the settle window, including the pump floor. Sending a
        # client-side list here would put a second copy of a safety rule in the
        # GUI, and the two would drift.
        #
        # DEC-334 Q3: the bidirectional walk is ON by default in this standalone
        # dialog (the user is watching one header and asked for the deeper test),
        # and OFF inside a validation session, where selecting the behaviour
        # token is the opt-in. Both are `None` against an older daemon, so it
        # gets exactly the request it has always got.
        bidirectional = True if self._behaviour_supported else None
        stability = None
        self._poll_in_flight = False
        self.start_requested.emit(self._header_id, None, None, bidirectional, stability)
        self._timer.start()

    @Slot()
    def _on_poll_tick(self) -> None:
        """P8-e: one poll in flight at a time.

        The timer fires on a fixed 1 Hz cadence whether or not the previous reply
        has arrived. Without this guard a slow socket queues polls without bound
        and every queued reply then renders in turn — the dialog appearing to
        replay the sweep. The flag is cleared by whichever of `apply_run` or
        `apply_error` answers, so a dropped reply cannot wedge polling either:
        the worker answers on both paths.
        """
        if self._poll_in_flight:
            return
        self._poll_in_flight = True
        self.poll_requested.emit()

    @Slot()
    def _on_cancel(self) -> None:
        self._cancel_btn.setEnabled(False)
        self._status_lbl.setText("Cancelling — the header will be restored…")
        self.cancel_requested.emit()

    # ── rendering ────────────────────────────────────────────────────

    def request_preflight(self) -> None:
        """Ask for the daemon's safety verdict. Call BEFORE ``exec()``.

        Read-only: it takes no lease, claims no slot and writes no hardware, so
        asking costs nothing and reserves nothing. The subsequent POST still runs
        its own guards — a `ready` verdict is a statement about *now*.
        """
        self.preflight_requested.emit(self._header_id, DIAGNOSTIC_CHARACTERIZATION)

    @Slot(object)
    def apply_preflight(self, report) -> None:
        self._preflight = build_preflight_view(report)
        self._render_preflight()

    @Slot(str, str)
    def apply_preflight_error(self, category: str, message: str) -> None:
        """A preflight failure is ADVISORY and must never block Start.

        The preflight is a courtesy: an older daemon does not serve it, and the
        diagnostic's own POST enforces every rule the preflight merely reports.
        Disabling Start because the advisory was unavailable would refuse a run
        the daemon would have accepted.
        """
        del category
        self._preflight = build_preflight_view(None, unavailable_reason=message)
        self._render_preflight()

    def _render_preflight(self) -> None:
        view: PreflightView = self._preflight
        _clear_layout(self._preflight_grid)
        for row, item in enumerate(view.rows):
            label = QLabel(item.label)
            label.setObjectName(f"Char_PreflightLabel_{item.check_id}")
            self._preflight_grid.addWidget(label, row, 0)
            pill = StatusPill(
                item.state_word, item.tone, object_name=f"Char_PreflightPill_{item.check_id}"
            )
            pill.setAccessibleName(f"{item.label}: {item.state_word}")
            self._preflight_grid.addWidget(pill, row, 1)
            detail = QLabel(item.detail)
            detail.setObjectName(f"Char_PreflightDetail_{item.check_id}")
            detail.setWordWrap(True)
            detail.setProperty("class", "CardMeta")
            self._preflight_grid.addWidget(detail, row, 2)
        self._preflight_grid.setColumnStretch(2, 1)

        blocked_text = ""
        if view.blocked:
            blocked_text = "Cannot run this test:\n• " + "\n• ".join(view.blocking_reasons)
        elif view.unavailable:
            blocked_text = view.unavailable_reason
        self._blocked_lbl.setText(blocked_text)
        self._blocked_lbl.setVisible(bool(blocked_text))

        # `can_start` is the view-model's, which reads the DAEMON's verdict. The
        # dialog never rolls the rows up itself — a second copy of that rule is
        # one that can disagree, and the copy the user is looking at would be
        # the wrong one.
        if not self._started:
            self._start_btn.setEnabled(view.can_start)
            self._start_btn.setToolTip("" if view.can_start else "\n".join(view.blocking_reasons))

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
        self._poll_in_flight = False
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
        self._poll_in_flight = False
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
            for col, text in enumerate(
                (
                    item.pwm,
                    item.direction,
                    item.readback,
                    item.rpm,
                    item.control_mode,
                    item.response,
                    item.settling,
                    item.stability,
                    item.result,
                )
            ):
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

        # The summary block: observed range, then the two timing lines §9 asks
        # for. Each line appears only when something was actually measured —
        # "use measured values only" — so a header with no tach shows neither
        # rather than showing zeroes.
        summary_lines = []
        if view.observed_range:
            summary_lines.append(f"Observed range: {view.observed_range}")
        if view.response_latency:
            summary_lines.append(f"Response latency: {view.response_latency}")
        if view.settling_time:
            summary_lines.append(f"Typical settling time: {view.settling_time}")
        self._range_lbl.setText("\n".join(summary_lines))
        self._range_lbl.setVisible(bool(summary_lines))

        # §8.2 / §8.4 / §8.6. Each block hides itself when it has nothing to
        # show, rather than rendering an empty frame that reads as a measurement
        # of zero.
        _fill_rows(self._summary_grid, view.summary_rows, "Char_Summary")
        self._summary_holder.setVisible(bool(view.summary_rows))

        self._override_lbl.setText(view.override_warning)
        self._override_lbl.setVisible(bool(view.override_warning))

        self._chart.set_curve(view.curve)
        self._chart.setVisible(view.curve.has_data)

        _fill_rows(self._detail_grid, view.detail_rows, "Char_Detail")
        _fill_provenance(self._provenance_grid, view.provenance_rows)
        self._detail_section.setVisible(bool(view.detail_rows or view.provenance_rows))

        self._notes_lbl.setText("\n\n".join(view.notes))
        self._notes_lbl.setVisible(bool(view.notes))

    # ── lifecycle ────────────────────────────────────────────────────

    def stop_polling(self) -> None:
        self._timer.stop()

    def reject(self) -> None:  # Qt override
        self._timer.stop()
        super().reject()
