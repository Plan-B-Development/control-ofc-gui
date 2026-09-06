"""The validation / lifecycle session dialog (AIO-MB Phase 6 §12-§17, DEC-318).

Phase 5 built the whole engine, the typed models, the view-model and the
serializers, and deliberately shipped no UI. This is that UI, and it is the only
consumer: one dialog serves BOTH a validation session and a lifecycle recording,
because Phase 5 Decision 8 made them one engine with a ``kind`` discriminator.
Building two dialogs would recreate the duplication §21 forbids.

Three rules the renderer must not undo, all decided in
``services/validation_view`` and merely displayed here:

* ``unavailable`` and ``not_tested`` render **neutrally**, never as errors — a
  capability the hardware does not expose is not a failure (§15).
* ``possible_device_override`` is **observed evidence, never a fail** (§10).
* An unrecognised finding id, state or event kind renders **humanised**, never
  dropped, so a newer daemon cannot make a result vanish (the 273-i rule).

Charts are deliberately absent. §14 says "do not make graphing mandatory" and "a
stable tabular implementation is preferable"; ``TimelineChart`` is coupled to
live ``AppState`` history and cannot render a session's sample array without a
new plot, which is recorded as deferred work rather than half-built here.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import (
    VALIDATION_KIND_LIFECYCLE,
    VALIDATION_KIND_VALIDATION,
    ValidationSession,
)
from control_ofc.services.validation_view import (
    ValidationSessionView,
    build_validation_session_view,
    event_label,
)
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

#: The dialog's own refresh while it is open. One request per second against a
#: session the daemon is already sampling at 1 Hz — matching its cadence rather
#: than beating it, and stopped the moment the dialog closes (§19).
POLL_INTERVAL_MS = 1000

#: Diagnostics a session can be asked to orchestrate. Each is an existing,
#: lease-owning, floor-clamped operation the daemon already performs — a session
#: orchestrates them, it never reimplements one.
#:
#: Control-path discovery is offered only against a daemon that advertises it;
#: the caller filters this list, because sending an unknown token would have the
#: daemon reject the whole session rather than skip one diagnostic.
_DIAGNOSTIC_CHOICES = (
    ("pwm_verify", "PWM control test (~10 s)"),
    ("pwm_characterization", "PWM response characterisation (~2-3 min)"),
    # DEC-334. Filtered out against a daemon without the capability, like every
    # other entry — an unknown token on the wire fails the WHOLE session, so the
    # gate is at the checkbox rather than at submit.
    #
    # Requesting this AND the basic sweep runs only this one: the daemon treats
    # it as a strict superset and supersedes the basic run, so a member is never
    # swept twice. That is why both may be ticked without a warning here.
    (
        "pwm_behaviour_characterization",
        "PWM behaviour characterisation — adds hysteresis and stability (~4-5 min)",
    ),
    ("control_path_discovery", "Control-path discovery (~1 min)"),
)

#: Measurement kinds offered for an external observation (§17). Free-form on the
#: wire; this list is a convenience, and the unit travels with the value.
_MEASUREMENT_KINDS = (
    ("supply_voltage", "12 V supply voltage", "V"),
    ("pwm_duty", "PWM duty cycle", "%"),
    ("pwm_frequency", "PWM frequency", "Hz"),
    ("tach_frequency", "Tach frequency", "Hz"),
    ("tach_pulses_per_rev", "Tach pulses/revolution", ""),
    ("device_current", "Device current", "A"),
    ("device_power", "Device power", "W"),
)

_FINDING_COLUMNS = ("Check", "Result", "Detail")
_MEMBER_COLUMNS = ("Member", "Role", "Samples", "Requested", "Readback", "RPM")


class ValidationSessionDialog(ModalDialog):
    """Start, watch, annotate, finish and export one session."""

    start_requested = Signal(str, str, list, list, dict)
    poll_requested = Signal()
    stop_requested = Signal()
    cancel_requested = Signal()
    marker_requested = Signal(str, str)  # detail, member_id
    measurement_requested = Signal(str, float, str, str, str)
    export_requested = Signal(str)  # "csv" | "json"

    def __init__(
        self,
        device_id: str,
        device_name: str,
        *,
        kind: str = VALIDATION_KIND_VALIDATION,
        members: list[tuple[str, str]] | None = None,
        supported_diagnostics: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        lifecycle = kind == VALIDATION_KIND_LIFECYCLE
        title = "Startup / Lifecycle Recording" if lifecycle else "AIO Validation"
        super().__init__(title, parent)
        self.setObjectName("ValidationSessionDialog")
        # `ModalDialog` renders the title into its own header label but does NOT
        # call `setWindowTitle` — every other dialog in this project sets its own
        # (aio_config_dialog.py:108, curve_edit_dialog.py:43, fan_wizard.py:106,
        # …), and without it assistive tech announces an unnamed window. Found
        # only because a reviewer flagged the vacuous `or` in the test that was
        # supposed to be checking this.
        self.setWindowTitle(title)
        self._device_id = device_id
        self._kind = kind
        self._members = list(members or [])
        # Which diagnostics this daemon actually accepts. `None` means "offer
        # everything", which is what the pre-Phase-8 callers did.
        #
        # Filtered at the CHECKBOX rather than at submit: the daemon rejects a
        # session whose `diagnostics[]` carries an unknown token outright, so an
        # unfilterable box would fail the whole session rather than skip one
        # diagnostic — a worse outcome than not offering it.
        self._supported_diagnostics = supported_diagnostics
        self._session: ValidationSession | None = None

        body = self.body_layout()
        body.setSpacing(10)

        self._device_lbl = QLabel(f"Device: {device_name}", self)
        self._device_lbl.setObjectName("Validation_Label_device")
        self._device_lbl.setTextFormat(Qt.TextFormat.PlainText)
        body.addWidget(self._device_lbl)

        self._intro = QLabel(
            "Records what this cooler actually does — PWM command, hardware "
            "readback, RPM, temperature, control ownership and thermal state — "
            "and finalises into evidence you can export.\n\n"
            "Nothing here lowers a safety floor or stops a pump: any diagnostic "
            "you enable runs through the daemon's existing, floor-clamped "
            "implementation."
            if not lifecycle
            else "Records how this cooler behaves across startup, resume and "
            "profile changes. Passive by default — enable a diagnostic below "
            "only if you want one run at the start.",
            self,
        )
        self._intro.setObjectName("Validation_Label_intro")
        self._intro.setWordWrap(True)
        body.addWidget(self._intro)

        body.addWidget(self._build_start_form())

        # ── Live state ───────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._state_pill = StatusPill("Not started", "neutral", object_name="Validation_Pill_state")
        self._state_pill.setAccessibleName("Session state: not started")
        status_row.addWidget(self._state_pill)
        self._elapsed_lbl = QLabel("", self)
        self._elapsed_lbl.setObjectName("Validation_Label_elapsed")
        status_row.addWidget(self._elapsed_lbl)
        status_row.addStretch(1)
        body.addLayout(status_row)

        self._status_lbl = QLabel("Ready to start.", self)
        self._status_lbl.setObjectName("Validation_Label_status")
        self._status_lbl.setWordWrap(True)
        body.addWidget(self._status_lbl)

        self._member_table = QTableWidget(0, len(_MEMBER_COLUMNS), self)
        self._member_table.setObjectName("Validation_Table_members")
        self._member_table.setHorizontalHeaderLabels(list(_MEMBER_COLUMNS))
        apply_dense_table(self._member_table)
        self._member_table.setVisible(False)
        body.addWidget(self._member_table)

        self._findings_table = QTableWidget(0, len(_FINDING_COLUMNS), self)
        self._findings_table.setObjectName("Validation_Table_findings")
        self._findings_table.setHorizontalHeaderLabels(list(_FINDING_COLUMNS))
        apply_dense_table(self._findings_table)
        self._findings_table.setVisible(False)
        body.addWidget(self._findings_table)

        body.addWidget(self._build_evidence_section())
        body.addWidget(self._build_measurement_form())

        # ── Footer ───────────────────────────────────────────────────────────
        self._start_btn = self.add_footer_button(
            "Start Recording" if lifecycle else "Start Validation",
            "primary",
            object_name="Validation_Btn_start",
        )
        self._start_btn.clicked.connect(self._emit_start)
        self._mark_btn = self.add_footer_button(
            "Mark Event", "secondary", object_name="Validation_Btn_mark"
        )
        self._mark_btn.clicked.connect(self._emit_marker)
        self._stop_btn = self.add_footer_button(
            "Stop", "secondary", object_name="Validation_Btn_stop"
        )
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        self._cancel_btn = self.add_footer_button(
            "Cancel Session", "danger", object_name="Validation_Btn_cancelSession"
        )
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        self._csv_btn = self.add_footer_button(
            "Export CSV", "secondary", object_name="Validation_Btn_exportCsv"
        )
        self._csv_btn.clicked.connect(lambda: self.export_requested.emit("csv"))
        self._json_btn = self.add_footer_button(
            "Export JSON", "secondary", object_name="Validation_Btn_exportJson"
        )
        self._json_btn.clicked.connect(lambda: self.export_requested.emit("json"))
        self._close_btn = self.add_footer_button(
            "Close", "ghost", object_name="Validation_Btn_close"
        )
        self._close_btn.clicked.connect(self.reject)

        # Its own timer, stopped on every exit path. The dialog polls only while
        # it is open; the 1 Hz application poll is untouched (§19).
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll_requested.emit)

        self._apply_enablement()

    # ── construction helpers ─────────────────────────────────────────────────

    def _build_start_form(self) -> QWidget:
        section = CollapsibleSection(
            "Session options", "Validation_Section_options", expanded=True, parent=self
        )
        host = QWidget(section)
        form = QFormLayout(host)
        form.setContentsMargins(0, 0, 0, 0)

        self._diag_boxes: list[tuple[str, QCheckBox]] = []
        for token, label in _DIAGNOSTIC_CHOICES:
            if self._supported_diagnostics is not None and token not in self._supported_diagnostics:
                continue
            box = QCheckBox(label, host)
            box.setObjectName(f"Validation_Check_{token}")
            box.setAccessibleName(f"Run {label} during this session")
            form.addRow("", box)
            self._diag_boxes.append((token, box))

        self._sweep_combo = QComboBox(host)
        self._sweep_combo.setObjectName("Validation_Combo_sweepMember")
        self._sweep_combo.addItem("Pump (default)", "")
        for member_id, label in self._members:
            self._sweep_combo.addItem(label, member_id)
        form.addRow("Sweep member", self._sweep_combo)
        name_value_control(self._sweep_combo, "Member to sweep during characterisation")

        self._note_edit = QLineEdit(host)
        self._note_edit.setObjectName("Validation_Edit_note")
        self._note_edit.setPlaceholderText("Optional note stored with the session")
        form.addRow("Note", self._note_edit)
        name_value_control(self._note_edit, "Session note")

        section.add_widget(host)
        self._options_section = section
        return section

    def _build_evidence_section(self) -> QWidget:
        """The §6.4 "Evidence & confidence" disclosure.

        Collapsed by default. §6.4 asks for evidence classifications and
        confidence "without overwhelming the primary view", and the primary view
        here is already two tables — so this is disclosure, not a fourth panel.

        The provenance legend is static: it explains the vocabulary rather than
        classifying any particular value, so it is correct before a session has
        produced anything and needs no data to render.
        """
        from control_ofc.services.provenance import (
            PROVENANCE_LABELS,
            PROVENANCE_TOOLTIPS,
            UNVERIFIABLE,
        )

        section = CollapsibleSection(
            "Evidence & confidence",
            "Validation_Section_evidence",
            expanded=False,
            parent=self,
        )
        host = QWidget(section)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        intro = QLabel(
            "Every value in a report is classified by where it came from. "
            "Control-OFC never presents something it inferred, or something you "
            "typed in, as a direct hardware measurement."
        )
        intro.setObjectName("Validation_Label_evidenceIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)
        for row, (token, label) in enumerate(PROVENANCE_LABELS.items()):
            name = QLabel(label, host)
            name.setObjectName(f"Validation_Provenance_{token}")
            name.setProperty("class", "CardValue")
            grid.addWidget(name, row, 0)
            desc = QLabel(PROVENANCE_TOOLTIPS.get(token, ""), host)
            desc.setObjectName(f"Validation_ProvenanceDetail_{token}")
            desc.setWordWrap(True)
            desc.setProperty("class", "CardMeta")
            grid.addWidget(desc, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        # §5: "Never represent an untested item as PASS." An absent row reads as
        # a pass to most people, so the things software CANNOT establish are
        # listed explicitly rather than omitted.
        unverified = QLabel(
            "Not measurable from motherboard sensors — these need an external "
            "instrument and are reported as UNVERIFIED, never as a pass:\n• "
            + "\n• ".join(text for _, text in UNVERIFIABLE)
        )
        unverified.setObjectName("Validation_Label_unverifiable")
        unverified.setWordWrap(True)
        unverified.setProperty("class", "CardMeta")
        layout.addWidget(unverified)

        section.add_widget(host)
        return section

    def _build_measurement_form(self) -> QWidget:
        """External electrical observations (§17).

        Untrusted by construction: recorded alongside the session and read by
        nothing in the daemon's control path. The form says so, because §17
        requires that these are "never treated as trusted daemon safety or
        configuration data".
        """
        section = CollapsibleSection(
            "External measurements",
            "Validation_Section_measurements",
            expanded=False,
            parent=self,
        )
        host = QWidget(section)
        form = QFormLayout(host)
        form.setContentsMargins(0, 0, 0, 0)

        caution = QLabel(
            "Recorded with the session for your own analysis. Control-OFC never "
            "uses these values for control or safety decisions.",
            host,
        )
        caution.setObjectName("Validation_Label_measurementCaution")
        caution.setWordWrap(True)
        form.addRow(caution)

        self._m_kind = QComboBox(host)
        self._m_kind.setObjectName("Validation_Combo_measurementKind")
        for token, label, unit in _MEASUREMENT_KINDS:
            self._m_kind.addItem(label, (token, unit))
        self._m_kind.currentIndexChanged.connect(self._sync_measurement_unit)
        form.addRow("Measurement", self._m_kind)
        name_value_control(self._m_kind, "Measurement type")

        self._m_member = QComboBox(host)
        self._m_member.setObjectName("Validation_Combo_measurementMember")
        self._m_member.addItem("Not specified", "")
        for member_id, label in self._members:
            self._m_member.addItem(label, member_id)
        form.addRow("Header", self._m_member)
        name_value_control(self._m_member, "Header this measurement applies to")

        self._m_value = QDoubleSpinBox(host)
        self._m_value.setObjectName("Validation_Spin_measurementValue")
        self._m_value.setDecimals(3)
        self._m_value.setRange(-1_000_000.0, 1_000_000.0)
        form.addRow("Value", self._m_value)
        name_value_control(self._m_value, "Measured value")

        self._m_unit = QLineEdit(host)
        self._m_unit.setObjectName("Validation_Edit_measurementUnit")
        form.addRow("Unit", self._m_unit)
        name_value_control(self._m_unit, "Measurement unit")

        # The wire carries no `instrument` field, so it is folded into the note
        # rather than inventing one. Untrusted free text either way (§17).
        self._m_instrument = QLineEdit(host)
        self._m_instrument.setObjectName("Validation_Edit_measurementInstrument")
        self._m_instrument.setPlaceholderText("e.g. Logic analyser")
        form.addRow("Instrument", self._m_instrument)
        name_value_control(self._m_instrument, "Instrument used")

        self._m_note = QLineEdit(host)
        self._m_note.setObjectName("Validation_Edit_measurementNote")
        form.addRow("Notes", self._m_note)
        name_value_control(self._m_note, "Measurement notes")

        self._m_add = self._make_add_button(host)
        form.addRow("", self._m_add)

        section.add_widget(host)
        self._measurement_section = section
        self._sync_measurement_unit()
        return section

    def _make_add_button(self, host: QWidget):
        from control_ofc.ui.components.buttons import make_button

        button = make_button(
            "Record Measurement",
            "secondary",
            object_name="Validation_Btn_addMeasurement",
            accessible_name="Record this external measurement with the session",
            parent=host,
        )
        button.clicked.connect(self._emit_measurement)
        return button

    def _sync_measurement_unit(self) -> None:
        data = self._m_kind.currentData()
        if isinstance(data, tuple) and len(data) == 2:
            self._m_unit.setText(data[1])

    # ── emitters ─────────────────────────────────────────────────────────────

    def _emit_start(self) -> None:
        diagnostics = [token for token, box in self._diag_boxes if box.isChecked()]
        sweep = self._sweep_combo.currentData() or ""
        metadata = {}
        note = self._note_edit.text().strip()
        if note:
            metadata["note"] = note
        self.start_requested.emit(
            self._device_id,
            self._kind,
            diagnostics,
            [sweep] if sweep else [],
            metadata,
        )

    def _emit_marker(self) -> None:
        self.marker_requested.emit(self._note_edit.text().strip(), "")

    def _emit_measurement(self) -> None:
        data = self._m_kind.currentData()
        kind = data[0] if isinstance(data, tuple) else str(data)
        note_parts = []
        instrument = self._m_instrument.text().strip()
        if instrument:
            note_parts.append(f"Instrument: {instrument}")
        extra = self._m_note.text().strip()
        if extra:
            note_parts.append(extra)
        self.measurement_requested.emit(
            kind,
            float(self._m_value.value()),
            self._m_unit.text().strip(),
            self._m_member.currentData() or "",
            " — ".join(note_parts),
        )

    # ── external updates ─────────────────────────────────────────────────────

    def start_polling(self) -> None:
        self._timer.start()

    def stop_polling(self) -> None:
        self._timer.stop()

    def session(self) -> ValidationSession | None:
        return self._session

    def apply_session(self, session: ValidationSession | None) -> None:
        """Render a session (or its absence) from the daemon."""
        self._session = session
        if session is None:
            self._state_pill.set_text("Not started")
            self._state_pill.set_state("neutral")
            self._status_lbl.setText("Ready to start.")
            self._member_table.setVisible(False)
            self._findings_table.setVisible(False)
            self._apply_enablement()
            return
        view = build_validation_session_view(session)
        self._render(view)
        if not view.recording:
            self._timer.stop()
        self._apply_enablement()

    def apply_error(self, category: str, message: str) -> None:
        # A soft safety refusal arrives as "unavailable" and is shown verbatim,
        # never prefixed as an error: the daemon declining to move a pump during
        # a thermal event is protection working, not a failure.
        self._status_lbl.setText(
            message if category == "unavailable" else f"Session error: {message}"
        )

    def apply_action_ok(self, message: str) -> None:
        self._status_lbl.setText(message)

    # ── rendering ────────────────────────────────────────────────────────────

    def _render(self, view: ValidationSessionView) -> None:
        self._state_pill.set_text(view.state_label)
        self._state_pill.set_state(_pill_state(view.state_tone))
        self._state_pill.setAccessibleName(f"Session state: {view.state_label}")
        self._elapsed_lbl.setText(
            f"Elapsed {view.elapsed_text} · {view.sample_count} samples · {view.event_count} events"
        )

        lines = [view.diagnostics_note]
        if view.interrupted_note:
            lines.append(view.interrupted_note)
        if view.limit_note:
            lines.append(view.limit_note)
        self._status_lbl.setText("\n".join(x for x in lines if x))

        self._member_table.setRowCount(len(view.members))
        for row, member in enumerate(view.members):
            for col, text in enumerate(
                (
                    member.label,
                    member.role_label,
                    str(member.samples),
                    member.requested_range,
                    member.readback_range,
                    member.rpm_range,
                )
            ):
                self._member_table.setItem(row, col, QTableWidgetItem(text))
        self._member_table.setVisible(bool(view.members))

        self._findings_table.setRowCount(len(view.findings))
        for row, finding in enumerate(view.findings):
            for col, text in enumerate((finding.label, finding.state_label, finding.detail or "")):
                item = QTableWidgetItem(text)
                self._findings_table.setItem(row, col, item)
        self._findings_table.setVisible(bool(view.findings))

    def _apply_enablement(self) -> None:
        recording = bool(self._session and self._session.is_recording)
        finished = self._session is not None and not recording
        self._start_btn.setEnabled(self._session is None)
        self._mark_btn.setEnabled(recording)
        self._stop_btn.setEnabled(recording)
        self._cancel_btn.setEnabled(recording)
        self._m_add.setEnabled(recording)
        # Exports need a session with content — a session that never started has
        # nothing to serialize, and offering the button would produce an empty
        # file the user would reasonably read as a failed export.
        self._csv_btn.setEnabled(finished or recording)
        self._json_btn.setEnabled(finished or recording)
        self._options_section.setEnabled(self._session is None)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def reject(self) -> None:  # Qt override
        self._timer.stop()
        super().reject()

    def accept(self) -> None:  # Qt override
        self._timer.stop()
        super().accept()


def _pill_state(tone: str) -> str:
    """Map the view-model's tone vocabulary onto the shared pill states."""
    return {
        "ok": "ok",
        "success": "ok",
        "warn": "warn",
        "warning": "warn",
        "crit": "critical",
        "critical": "critical",
        "info": "info",
    }.get(tone, "neutral")


__all__ = ["ValidationSessionDialog", "event_label"]
