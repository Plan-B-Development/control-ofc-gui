"""The validation / lifecycle session dialog (AIO-MB Phase 6 §12-§17, DEC-318).

Phase 5 built the whole engine, the typed models, the view-model and the
serializers, and deliberately shipped no UI. This is that UI, and it is the only
consumer: one dialog serves a validation session, a lifecycle recording and a
thermal observation (DEC-335), because Phase 5 Decision 8 made them one engine
with a ``kind`` discriminator. Building a dialog per kind would recreate the
duplication §21 forbids, and Phase 8's Overview repeats the instruction: do not
duplicate Phase 3/5/6 implementations under new names.

Three rules the renderer must not undo, all decided in
``services/validation_view`` and merely displayed here:

* ``unavailable`` and ``not_tested`` render **neutrally**, never as errors — a
  capability the hardware does not expose is not a failure (§15).
* ``possible_device_override`` is **observed evidence, never a fail** (§10).
* An unrecognised finding id, state or event kind renders **humanised**, never
  dropped, so a newer daemon cannot make a result vanish (the 273-i rule).

**The "charts are deliberately absent" note here is RETRACTED (DEC-335).** It was
correct for Phase 6: §14 says "do not make graphing mandatory" and prefers a
stable tabular implementation, and ``TimelineChart`` is coupled to live
``AppState`` history and cannot render a session's sample array. That is still
true of ``TimelineChart`` — so Batch 3a did not reuse it. It adds
``SessionTimelineChart``, which takes a Qt-free trace built from the session's
own samples, on the ``PwmResponseChart`` pattern. The tables remain; the chart is
additive, and §14's "not mandatory" is honoured by it drawing nothing when the
session carries no series.
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
    VALIDATION_KIND_THERMAL,
    VALIDATION_KIND_VALIDATION,
    ValidationSession,
)
from control_ofc.services.thermal_view import (
    ISOLATION_TEMPLATES,
    build_live_summary,
    build_session_trace,
    build_startup_views,
    build_steady_state_view,
    isolation_stage_gate,
)
from control_ofc.services.validation_view import (
    ValidationSessionView,
    build_validation_session_view,
    event_label,
)
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.session_timeline_chart import SessionTimelineChart

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

#: Title and lead-in per session kind (DEC-335). One table rather than nested
#: ternaries — see the REWRITE note in `__init__`.
_KIND_TITLES = {
    VALIDATION_KIND_VALIDATION: "AIO Validation",
    VALIDATION_KIND_LIFECYCLE: "Startup / Lifecycle Recording",
    VALIDATION_KIND_THERMAL: "Thermal Observation",
}

_KIND_INTROS = {
    VALIDATION_KIND_VALIDATION: (
        "Records what this cooler actually does — PWM command, hardware "
        "readback, RPM, temperature, control ownership and thermal state — "
        "and finalises into evidence you can export.\n\n"
        "Nothing here lowers a safety floor or stops a pump: any diagnostic "
        "you enable runs through the daemon's existing, floor-clamped "
        "implementation."
    ),
    VALIDATION_KIND_LIFECYCLE: (
        "Records how this cooler behaves across startup, resume and "
        "profile changes. Passive by default — enable a diagnostic below "
        "only if you want one run at the start."
    ),
    # §2's workload boundary, stated where the user starts the run rather than
    # buried in help: Control-OFC records, the user drives the load. The spec is
    # explicit that no stress tool is launched, and this is the sentence that
    # makes that a promise rather than an omission.
    VALIDATION_KIND_THERMAL: (
        "Records how this cooler responds to a workload you choose — "
        "temperature, CPU package power, pump and radiator duty and RPM — and "
        "reports whether the temperature reached a steady state.\n\n"
        "Control-OFC does NOT start, stop or control your workload. Start it "
        "yourself, then come back here and record. Nothing in this observation "
        "drives a fan or a pump; it only watches."
    ),
}

_START_LABELS = {
    VALIDATION_KIND_VALIDATION: "Start Validation",
    VALIDATION_KIND_LIFECYCLE: "Start Recording",
    VALIDATION_KIND_THERMAL: "Start Observation",
}

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
        # REWRITE (DEC-335): this was `lifecycle = kind == KIND_LIFECYCLE` plus a
        # ternary on the title and another on the intro. A third kind would have
        # made that a second special case layered on a first, which is the shape
        # the rewrite rule exists to stop. One table, three entries, and adding a
        # fourth kind is a row rather than another branch.
        thermal = kind == VALIDATION_KIND_THERMAL
        self._thermal = thermal
        title = _KIND_TITLES.get(kind, _KIND_TITLES[VALIDATION_KIND_VALIDATION])
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
        #: Something else owns the daemon's single session slot right now. Not a
        #: session we may render — only a reason our Start would be refused.
        self._foreign_recording = False
        #: A start has been asked for and neither a recording snapshot nor an
        #: error has come back yet. Suppresses the poll timer's stop-on-finished
        #: rule for exactly that window — see `apply_session`.
        self._start_pending = False

        body = self.body_layout()
        body.setSpacing(10)

        self._device_lbl = QLabel(f"Device: {device_name}", self)
        self._device_lbl.setObjectName("Validation_Label_device")
        self._device_lbl.setTextFormat(Qt.TextFormat.PlainText)
        body.addWidget(self._device_lbl)

        self._intro = QLabel(
            _KIND_INTROS.get(kind, _KIND_INTROS[VALIDATION_KIND_VALIDATION]),
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

        # DEC-335 §9.1/§9.2. Built for every kind and shown only when the
        # session actually carries the material: a daemon predating Batch 3a
        # sends neither, and an empty 'Steady state' panel would read as a
        # measurement that came back blank rather than one never taken.
        body.addWidget(self._build_thermal_sections())
        body.addWidget(self._build_evidence_section())
        body.addWidget(self._build_measurement_form())

        # ── Footer ───────────────────────────────────────────────────────────
        self._start_btn = self.add_footer_button(
            _START_LABELS.get(kind, _START_LABELS[VALIDATION_KIND_VALIDATION]),
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

    def _build_thermal_sections(self) -> QWidget:
        """The §9.1/§9.2 blocks: live readout, steady state, startup, chart."""
        host = QWidget(self)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        # Live readout (§9.2). Thermal only — it is the block a user watches
        # while their workload runs, and it would be noise on a PWM validation.
        self._live_box = QWidget(host)
        live_grid = QFormLayout(self._live_box)
        live_grid.setContentsMargins(0, 0, 0, 0)
        self._live_rows: dict[str, QLabel] = {}
        for key, label in (
            ("elapsed", "Elapsed"),
            ("temperature", "Control temperature"),
            ("package_power", "CPU package power"),
            ("gpu_power", "GPU power"),
            ("pump", "Pump"),
            ("radiator", "Radiator"),
            ("slope", "Temperature trend"),
            ("steady", "Steady state"),
        ):
            value = QLabel("—", self._live_box)
            value.setObjectName(f"Validation_Live_{key}")
            value.setTextFormat(Qt.TextFormat.PlainText)
            self._live_rows[key] = value
            live_grid.addRow(f"{label}:", value)
        self._live_box.setVisible(False)
        col.addWidget(self._live_box)

        self._workload_lbl = QLabel("", host)
        self._workload_lbl.setObjectName("Validation_Label_workload")
        self._workload_lbl.setWordWrap(True)
        self._workload_lbl.setVisible(False)
        col.addWidget(self._workload_lbl)

        # Component-isolation templates (§4 / §9.3), GUIDED ONLY.
        #
        # These instruct; they never drive. Q3-A: this batch adds no PWM write
        # path, so a stage tells the user which duty to set through the Controls
        # page's existing floor-clamped override, and the session records what
        # happens. `Next stage` drops a `user_marker` so the timeline shows where
        # each step began.
        self._template_section = CollapsibleSection("Isolation template", parent=host)
        self._template_section.setObjectName("Validation_Section_template")
        picker = QWidget(host)
        prow = QHBoxLayout(picker)
        prow.setContentsMargins(0, 0, 0, 0)
        self._template_combo = QComboBox(picker)
        self._template_combo.setObjectName("Validation_Combo_template")
        self._template_combo.addItem("None", "")
        for key, (label, _stages) in ISOLATION_TEMPLATES.items():
            self._template_combo.addItem(label, key)
        name_value_control(self._template_combo, "Isolation template")
        prow.addWidget(self._template_combo, 1)
        self._stage_btn = make_button(
            "Next stage",
            "secondary",
            object_name="Validation_Btn_nextStage",
            accessible_name="Record the next isolation-template stage",
        )
        prow.addWidget(self._stage_btn)
        self._template_section.add_widget(picker)

        self._stage_lbl = QLabel("", host)
        self._stage_lbl.setObjectName("Validation_Label_stage")
        self._stage_lbl.setWordWrap(True)
        self._stage_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self._template_section.add_widget(self._stage_lbl)
        self._template_index = 0
        # Connected LAST, deliberately. Both handlers read `_stage_lbl` and
        # `_template_index`, and connecting beside the widgets that emit —
        # which is where these lines started — leaves a window in which a
        # signal would reach a handler whose state does not exist yet. No
        # current code path fires one there; this removes the trap rather
        # than relying on that staying true.
        self._template_combo.currentIndexChanged.connect(self._on_template_changed)
        self._stage_btn.clicked.connect(self._on_next_stage)
        self._template_section.setVisible(False)
        col.addWidget(self._template_section)

        # Steady state (§3). A CollapsibleSection like every other detail block.
        self._steady_section = CollapsibleSection("Steady state", parent=host)
        self._steady_section.setObjectName("Validation_Section_steady")
        self._steady_pill = StatusPill("—", "neutral", object_name="Validation_Pill_steady")
        self._steady_section.add_widget(self._steady_pill)
        self._steady_body = QLabel("", host)
        self._steady_body.setObjectName("Validation_Label_steady")
        self._steady_body.setWordWrap(True)
        self._steady_body.setTextFormat(Qt.TextFormat.PlainText)
        self._steady_section.add_widget(self._steady_body)
        self._steady_section.setVisible(False)
        col.addWidget(self._steady_section)

        # Startup fingerprint (§9.1).
        self._startup_section = CollapsibleSection("Startup behaviour", parent=host)
        self._startup_section.setObjectName("Validation_Section_startup")
        self._startup_body = QLabel("", host)
        self._startup_body.setObjectName("Validation_Label_startup")
        self._startup_body.setWordWrap(True)
        self._startup_body.setTextFormat(Qt.TextFormat.PlainText)
        self._startup_section.add_widget(self._startup_body)
        self._startup_section.setVisible(False)
        col.addWidget(self._startup_section)

        # The timeline (Q9). Inside a disclosure so §14's "graphing is not
        # mandatory" still holds: the tables above are the primary surface and
        # the chart is opened by someone who wants it.
        self._chart_section = CollapsibleSection("Timeline", parent=host)
        self._chart_section.setObjectName("Validation_Section_chart")
        self._chart = SessionTimelineChart(object_name="Validation_Chart_timeline")
        self._chart.setMinimumHeight(220)
        self._chart_section.add_widget(self._chart)
        self._chart_section.setVisible(False)
        col.addWidget(self._chart_section)

        return host

    def _render_thermal(self, session: ValidationSession) -> None:
        """Render the Batch 3a blocks from the session the daemon sent.

        Every block hides itself when its material is absent. That is the same
        rule the chart follows and it matters most here: an older daemon sends
        no `steady_state` and no `startup_fingerprints`, and a visible-but-empty
        panel would report a measurement that came back blank rather than one
        this daemon never takes.
        """
        steady = getattr(session, "steady_state", None)
        fingerprints = getattr(session, "startup_fingerprints", None) or []

        if self._thermal:
            self._template_section.setVisible(True)
            self._render_template()
            live = build_live_summary(session, steady)
            self._live_rows["elapsed"].setText(live.elapsed_text)
            self._live_rows["temperature"].setText(live.temperature_text)
            self._live_rows["package_power"].setText(live.package_power_text)
            self._live_rows["gpu_power"].setText(live.gpu_power_text)
            self._live_rows["pump"].setText(live.pump_text)
            self._live_rows["radiator"].setText(live.radiator_text)
            self._live_rows["slope"].setText(live.slope_text)
            self._live_rows["steady"].setText(live.steady_text)
            self._live_box.setVisible(True)
            self._workload_lbl.setText(live.workload_note)
            self._workload_lbl.setVisible(True)

        steady_view = build_steady_state_view(steady)
        if steady_view is not None:
            self._steady_pill.set_text(steady_view.verdict_label)
            self._steady_pill.set_state(_pill_state(steady_view.tone))
            lines = [f"{r.label}: {r.value}" for r in steady_view.rows]
            if steady_view.criterion:
                # Rendered verbatim from the daemon, never restated here — §3
                # requires the criterion be reported, and a GUI-side copy of the
                # thresholds would be falsified the moment either constant moved.
                lines.append(f"Criterion: {steady_view.criterion}")
            self._steady_body.setText("\n".join(lines))
        self._steady_section.setVisible(steady_view is not None)

        startup_views = build_startup_views(fingerprints)
        if startup_views:
            blocks = []
            for v in startup_views:
                rows = "\n".join(f"  {r.label}: {r.value}" for r in v.rows)
                blocks.append(f"{v.interpretation_label}\n{rows}")
            self._startup_body.setText("\n\n".join(blocks))
        self._startup_section.setVisible(bool(startup_views))

        trace = build_session_trace(session, steady, event_label=event_label)
        self._chart.set_trace(trace)
        self._chart_section.setVisible(trace.has_data)

    def _on_template_changed(self) -> None:
        self._template_index = 0
        self._render_template()

    def _on_next_stage(self) -> None:
        """Advance one stage, but only when the machine is in a safe state.

        The gate lives in the view model so the rule is testable without Qt; this
        only refuses and reports. §12 asks that an unsafe rising temperature
        block progression, and under Q3-A this is the only place a guided
        workflow can refuse anything.
        """
        gate = isolation_stage_gate(self._session)
        if not gate.can_advance:
            self._status_lbl.setText(gate.reason)
            return
        key = self._template_combo.currentData()
        if not key:
            return
        label, stages = ISOLATION_TEMPLATES[key]
        if self._template_index >= len(stages):
            return
        stage = stages[self._template_index]
        self.marker_requested.emit(f"{label} — stage {self._template_index + 1}: {stage}", "")
        self._template_index += 1
        self._render_template()

    def _render_template(self) -> None:
        key = self._template_combo.currentData() if hasattr(self, "_template_combo") else ""
        if not key:
            self._stage_lbl.setText(
                "Pick a template to step through a guided observation. "
                "Control-OFC will not change any duty for you — it records and "
                "marks the timeline while you do."
            )
            self._stage_btn.setEnabled(False)
            return
        label, stages = ISOLATION_TEMPLATES[key]
        if self._template_index >= len(stages):
            self._stage_lbl.setText(f"{label}: all {len(stages)} stages recorded.")
            self._stage_btn.setEnabled(False)
            return
        gate = isolation_stage_gate(self._session)
        nxt = stages[self._template_index]
        suffix = "" if gate.can_advance else f"\n\nCannot advance: {gate.reason}"
        self._stage_lbl.setText(
            f"Stage {self._template_index + 1} of {len(stages)} — {label}\n\n{nxt}{suffix}"
        )
        self._stage_btn.setEnabled(gate.can_advance)

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
        self._start_pending = True
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

    def _is_ours(self, session: ValidationSession) -> bool:
        """Is this snapshot the session this dialog was opened to run?

        ``GET /validation/session`` serves ONE process-global slot and
        ``finish()`` finalises **in place**, so the daemon keeps serving the most
        recently completed session indefinitely — a lifecycle recording made an
        hour ago, or the ``[startup] record_startup`` auto-record from this
        boot. Rendering that under this dialog's device name would attribute
        another assembly's members, findings and timeline to this one, which is
        the same defect ``AUD2-a`` records for the per-header diagnostics and the
        reason both sibling dialogs carry this method (``control_path_dialog``,
        ``pwm_characterization_dialog``).

        Matched on device **and** kind: a thermal observation and a validation
        run of the same assembly answer different questions and render different
        sections, so a completed validation session is not this dialog's to show.

        An empty ``cooling_device_id`` is accepted, for the same reason the two
        siblings accept an empty ``header_id``: a daemon that sends none would
        otherwise blank the dialog permanently.
        """
        theirs = session.metadata.cooling_device_id if session.metadata else ""
        return (not theirs or theirs == self._device_id) and session.kind == self._kind

    def apply_session(self, session: ValidationSession | None) -> None:
        """Render a session (or its absence) from the daemon.

        The poll timer stops on a finished session of ours — **unless a start is
        pending.** That exception exists because `P8-q` made Start clickable
        while a finished session is on screen, and this dialog has no
        single-poll-in-flight guard: a poll issued before the click can be
        answered after it, and stopping the timer on that stale reply would
        freeze the dialog on the previous session while the new one records,
        with Stop, Cancel and Mark all disabled. The flag is cleared by the first
        recording snapshot, or by `apply_error` if the start was refused.
        """
        # [P8-q] A session that is not ours is not rendered — but the fact that
        # SOMETHING is recording is still load-bearing, because the daemon holds
        # one slot and would answer our start with 409. Dropping it entirely
        # would offer a Start that cannot succeed; keeping it as the session
        # would show another device's data under this one's name.
        if session is not None and not self._is_ours(session):
            self._foreign_recording = bool(session.is_recording)
            session = None
        else:
            self._foreign_recording = False
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
        self._render_thermal(session)
        if view.recording:
            # Our start landed; anything still queued behind it is current.
            self._start_pending = False
        elif not self._start_pending:
            self._timer.stop()
        self._apply_enablement()

    def apply_error(self, category: str, message: str) -> None:
        # The start did not take (or a poll failed), so stop holding the timer
        # open for a session that is not coming. Cleared here rather than only on
        # a recording snapshot, so a refused start cannot leave the dialog
        # polling for the rest of its life.
        self._start_pending = False
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
        # [P8-q] Gated on "nothing is recording", NOT on "no session object".
        # The daemon serves its most recent COMPLETED session forever, so
        # `self._session is None` made Start un-re-enablable for the life of the
        # daemon once any session had finished — including the startup
        # auto-record, which finishes ~2 minutes after every boot. The daemon
        # itself admits a new session whenever the slot holds a finished one
        # (`ValidationEngine::start` rejects only `is_recording()`), so this now
        # matches what the daemon will actually accept.
        can_start = not recording and not self._foreign_recording
        self._start_btn.setEnabled(can_start)
        self._mark_btn.setEnabled(recording)
        self._stop_btn.setEnabled(recording)
        self._cancel_btn.setEnabled(recording)
        self._m_add.setEnabled(recording)
        # Exports need a session with content — a session that never started has
        # nothing to serialize, and offering the button would produce an empty
        # file the user would reasonably read as a failed export.
        self._csv_btn.setEnabled(finished or recording)
        self._json_btn.setEnabled(finished or recording)
        # The options ARE the configuration of the next session, so they follow
        # Start exactly. Leaving them on `_session is None` would offer an
        # enabled Start over a form the user could not edit — a second, quieter
        # version of the same defect.
        self._options_section.setEnabled(can_start)

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
