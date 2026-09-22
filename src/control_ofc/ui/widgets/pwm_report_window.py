"""The PWM Test Report window (DEC-404): Reports → Scope → Your setup → Review &
consent → Run → Report, plus the comparison page.

A thin renderer. What may be tested, what is pre-selected, what a run records
and what the report may claim are all decided in the Qt-free
``services/pwm_report`` package; the run itself is driven by the Hardware
page's :class:`~control_ofc.ui.pages.pwm_report_controller.PwmReportController`,
which outlives this window.

Non-modal and single-instance (DEC-404 decision 10). Closing it **hides** it:
the window keeps its pages, and reopening shows the run in progress or the last
report. Closing it mid-run asks first, then cancels the run — the controller
sees the cancel through and saves the report.

Stage 5 (S5-1): the window opens on the **Reports** page — the history list —
unless a run is in progress or has just finished. A report opened from the list
or from a file is shown **without** "Re-apply profile" (S5-7): that button acts
on the machine now, and a reopened report's evidence is from then. Exports go
through one Export drop-down menu (S5-9).

Consent (S4-2): Start stays disabled until the general consent is ticked
whenever any selected test writes to a header, and until each selected stall
probe has its own "I'll stay at the machine" confirmation. Only a confirmed
header's probe carries the daemon's ``acknowledge_below_floor``.
"""

from __future__ import annotations

import platform
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, qVersion
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.constants import APP_VERSION
from control_ofc.services.diagnostic_estimates import duration_words
from control_ofc.services.pwm_report import catalog as cat
from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report import setup_facts as sf
from control_ofc.services.pwm_report.compare import compare_reports
from control_ofc.services.pwm_report.runner import (
    HANDBACK_WAIT_S,
    REASON_WINDOW_CLOSED,
    build_plan,
    start_refusals,
)
from control_ofc.services.pwm_report.view import (
    PROBE_COLUMNS,
    FindingRow,
    ReportView,
    build_report_view,
    evidence_text,
    step_status,
)
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import SectionHeader
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.pwm_report_compare import PwmReportComparePage
from control_ofc.ui.widgets.pwm_report_export import (
    COMPARISON_FORMATS,
    REPORT_FORMATS,
    attach_export_menu,
    export_comparison,
    export_report,
)
from control_ofc.ui.widgets.pwm_report_history import PwmReportHistoryPage
from control_ofc.ui.widgets.pwm_response_chart import PwmResponseChart

if TYPE_CHECKING:
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.app_state import AppState
    from control_ofc.ui.pages.pwm_report_controller import PwmReportController

PAGE_SCOPE, PAGE_SETUP, PAGE_REVIEW, PAGE_RUN, PAGE_REPORT, PAGE_HISTORY, PAGE_COMPARE = range(7)

#: How the report on the Report page got there (S5-7). Only a ``live`` one — the
#: run that just finished in this window — may offer "Re-apply profile".
CONTEXT_LIVE = "live"
CONTEXT_SAVED = "saved"
CONTEXT_FILE = "file"

REOPENED_REAPPLY_NOTE = (
    "When this report ran, a header had not been put back as it was found, and the "
    "report offered to re-apply the profile. That was the state then — run a new "
    "report to check the machine now."
)

_SCOPE_FIXED_COLS = ("Channel", "Role", "Writable", "Fan detected", "In profile")

_TONE_TO_STATE = {"ok": "ok", "warn": "warn", "bad": "crit", "info": "info", "muted": "neutral"}


def _slug(value: str) -> str:
    """A stable objectName fragment from a stable id (the header-card rule)."""
    return "".join(c if c.isalnum() else "_" for c in value)


def _pill_state(tone: str) -> str:
    return _TONE_TO_STATE.get(tone, "neutral")


def _plain(text: str, object_name: str, *, meta: bool = False) -> QLabel:
    """A word-wrapped label that never interprets markup — every string here can
    carry a daemon label or a user alias (DEC-106)."""
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if meta:
        label.setProperty("class", "CardMeta")
    return label


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


class PwmReportWindow(ModalDialog):
    """The report's five pages over one controller."""

    def __init__(
        self,
        controller: PwmReportController,
        state: AppState | None,
        settings_service: AppSettingsService | None,
        *,
        profile_member_ids: Callable[[], frozenset[str]] = frozenset,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("PWM Test Report", parent, modal=False)
        self.setObjectName("PwmReport_Window")
        self.resize(980, 760)
        self._controller = controller
        self._state = state
        self._settings = settings_service
        self._profile_member_ids = profile_member_ids
        self._channels: list[cat.Channel] = []
        self._checks: dict[tuple[str, str], QCheckBox] = {}
        self._probe_consents: dict[str, QCheckBox] = {}
        self._setup_widgets: dict[str, tuple[QComboBox, QComboBox, QComboBox, QLineEdit]] = {}
        self._report_doc: dict | None = None
        self._report_context = CONTEXT_LIVE
        self._report_path = None
        self._evidence_filled = False
        self._reapply_btn: QPushButton | None = None
        self._reapply_msg: QLabel | None = None

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("PwmReport_Stack")
        self.body_layout().addWidget(self._stack)
        self._stack.addWidget(self._build_scope_page())
        self._stack.addWidget(self._build_setup_page())
        self._stack.addWidget(self._build_review_page())
        self._stack.addWidget(self._build_run_page())
        self._stack.addWidget(self._build_report_page())
        self._history = PwmReportHistoryPage(
            directory=controller.directory, running_report_id=controller.running_report_id
        )
        self._history.open_requested.connect(self._open_report)
        self._history.compare_requested.connect(self._show_comparison)
        self._history.export_requested.connect(self._export_other)
        self._stack.addWidget(self._history)
        self._compare_page = PwmReportComparePage()
        self._stack.addWidget(self._compare_page)

        self._back_btn = self.add_footer_button("Back", object_name="PwmReport_Btn_back")
        self._next_btn = self.add_footer_button("Next", "primary", object_name="PwmReport_Btn_next")
        self._start_btn = self.add_footer_button(
            "Start", "primary", object_name="PwmReport_Btn_start"
        )
        self._cancel_btn = self.add_footer_button(
            "Cancel run", "danger", object_name="PwmReport_Btn_cancel"
        )
        self._export_btn = self.add_footer_button("Export", object_name="PwmReport_Btn_export")
        attach_export_menu(
            self._export_btn, REPORT_FORMATS, self._export_current, "PwmReport_Action_export"
        )
        self._export_cmp_btn = self.add_footer_button(
            "Export comparison", object_name="PwmReport_Btn_exportCompare"
        )
        attach_export_menu(
            self._export_cmp_btn,
            COMPARISON_FORMATS,
            self._export_comparison,
            "PwmReport_Action_exportCompare",
        )
        self._history_btn = self.add_footer_button(
            "All reports", object_name="PwmReport_Btn_history"
        )
        self._new_btn = self.add_footer_button("New report", object_name="PwmReport_Btn_new")
        self._close_btn = self.add_footer_button(
            "Close", "ghost", object_name="PwmReport_Btn_close"
        )
        self._back_btn.clicked.connect(self._go_back)
        self._next_btn.clicked.connect(self._go_next)
        self._start_btn.clicked.connect(self._start_run)
        self._cancel_btn.clicked.connect(self._cancel_run)
        self._history_btn.clicked.connect(self.show_history)
        self._new_btn.clicked.connect(self._new_report)
        self._close_btn.clicked.connect(self.close)

        controller.changed.connect(self._on_run_changed)
        controller.finished.connect(self._on_run_finished)
        controller.save_failed.connect(self._on_save_failed)
        controller.reapply_done.connect(self._on_reapply_done)
        if state is not None:
            state.status_updated.connect(self._refresh_refusals)
            state.connection_changed.connect(self._refresh_refusals)
            state.mode_changed.connect(self._refresh_refusals)
            state.fans_updated.connect(self._refresh_live)

        if controller.is_running():
            self._show_page(PAGE_RUN)
            self._on_run_changed()
        elif controller.document() is not None:
            self._on_run_finished(controller.document())
        else:
            self.show_history()

    # ── Page: Scope ─────────────────────────────────────────────────────────

    def _build_scope_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PwmReport_Page_scope")
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.addWidget(SectionHeader("What to test", object_name="PwmReport_Header_scope"))
        v.addWidget(
            _plain(
                "The report reads the state of every fan, header and sensor before and "
                "after, and records them once a second while it runs. Tick the tests to "
                "run on each motherboard header. Every test is performed and undone by "
                "the daemon; OpenFan channels and GPU fans are reported read-only.",
                "PwmReport_Label_scopeIntro",
            )
        )
        legend = QHBoxLayout()
        legend.setSpacing(8)
        legend.addWidget(StatusPill("Read-only", "info", object_name="PwmReport_Pill_readOnly"))
        legend.addWidget(
            _plain("The snapshot and the trace — always.", "PwmReport_Label_readOnly", meta=True)
        )
        legend.addWidget(StatusPill("Writes", "warn", object_name="PwmReport_Pill_writes"))
        legend.addWidget(
            _plain(
                "Every test column — it changes the header's duty while it runs.",
                "PwmReport_Label_writes",
                meta=True,
            )
        )
        legend.addStretch(1)
        v.addLayout(legend)
        self._scope_refusals = _plain("", "PwmReport_Label_scopeRefusals")
        self._scope_refusals.setProperty("class", "WarningChip")
        self._scope_refusals.setVisible(False)
        v.addWidget(self._scope_refusals)
        self._scope_table = QTableWidget(0, len(_SCOPE_FIXED_COLS) + len(cat.TEST_ORDER))
        self._scope_table.setObjectName("PwmReport_Table_scope")
        self._scope_table.setHorizontalHeaderLabels(
            [*_SCOPE_FIXED_COLS, *(cat.SPECS[t].title for t in cat.TEST_ORDER)]
        )
        apply_dense_table(self._scope_table)
        self._scope_table.verticalHeader().setVisible(False)
        self._scope_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._scope_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._scope_table, 1)
        self._estimate_lbl = _plain("", "PwmReport_Label_estimate", meta=True)
        v.addWidget(self._estimate_lbl)
        return page

    def _populate_scope(self) -> None:
        state = self._state
        caps = state.capabilities if state is not None else None
        name_of = state.fan_display_name if state is not None else (lambda cid: cid)
        self._channels = cat.build_channels(
            state.hwmon_headers if state is not None else [],
            state.fans if state is not None else [],
            caps,
            name_of=name_of,
            profile_member_ids=self._profile_member_ids(),
        )
        defaults = cat.default_selection(self._channels, caps)
        table = self._scope_table
        table.setRowCount(len(self._channels))
        self._checks.clear()
        for row, ch in enumerate(self._channels):
            role = f"{ch.role} ({ch.role_source})" if ch.is_hwmon else ch.source
            cells = (
                ch.name,
                role,
                "yes" if ch.writable else "no",
                "yes" if ch.fan_detected else ("no" if ch.rpm is not None else "—"),
                "yes" if ch.in_profile else "no",
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setToolTip(ch.channel_id)
                table.setItem(row, col, item)
            for offset, test in enumerate(cat.TEST_ORDER):
                col = len(_SCOPE_FIXED_COLS) + offset
                avail = cat.availability(ch, test, caps)
                box = QCheckBox()
                box.setObjectName(f"PwmReport_Check_{test}_{_slug(ch.channel_id)}")
                box.setAccessibleName(f"{cat.SPECS[test].title} on {ch.name}")
                box.setEnabled(avail.available)
                box.setChecked(avail.available and test in defaults.get(ch.channel_id, ()))
                box.setToolTip(avail.reason)
                box.toggled.connect(self._on_selection_changed)
                holder = QWidget()
                lay = QHBoxLayout(holder)
                lay.setContentsMargins(6, 0, 6, 0)
                lay.addWidget(box, 0, Qt.AlignmentFlag.AlignCenter)
                table.setCellWidget(row, col, holder)
                self._checks[(ch.channel_id, test)] = box
        table.resizeColumnsToContents()
        self._on_selection_changed()

    def selection(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for (cid, test), box in self._checks.items():
            if box.isEnabled() and box.isChecked():
                out.setdefault(cid, set()).add(test)
        return out

    def _on_selection_changed(self, *_args) -> None:
        sel = self.selection()
        typical, worst = cat.estimate_seconds(sel)
        steps = sum(len(t) for t in sel.values())
        if not steps:
            self._estimate_lbl.setText(
                "No tests selected: the report will be a read-only snapshot of every channel."
            )
        else:
            gaps = int(steps * HANDBACK_WAIT_S)
            self._estimate_lbl.setText(
                f"{steps} test(s) selected: about {duration_words(typical + gaps)}, "
                f"up to {duration_words(worst + gaps)}."
            )

    # ── Page: Your setup ────────────────────────────────────────────────────

    def _build_setup_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PwmReport_Page_setup")
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.addWidget(SectionHeader("Your setup (optional)", object_name="PwmReport_Header_setup"))
        v.addWidget(
            _plain(
                "Tell the report what software cannot see. A splitter behind a header "
                "means its RPM describes one fan of several. Anything left as “Not sure” "
                "or blank is recorded as not supplied. Remembered for this machine only, "
                "and never included in a settings export.",
                "PwmReport_Label_setupIntro",
            )
        )
        cooler = QGridLayout()
        cooler.setHorizontalSpacing(10)
        model_lbl = QLabel("Cooler model:")
        self._cooler_model = QLineEdit()
        self._cooler_model.setObjectName("PwmReport_Edit_coolerModel")
        self._cooler_model.setMaxLength(sf.MODEL_MAX)
        name_value_control(self._cooler_model, model_lbl)
        switch_lbl = QLabel("Pump mode switch:")
        self._pump_switch = QLineEdit()
        self._pump_switch.setObjectName("PwmReport_Edit_pumpSwitch")
        self._pump_switch.setMaxLength(sf.SWITCH_MAX)
        self._pump_switch.setPlaceholderText("its position, in the manufacturer's words")
        name_value_control(self._pump_switch, switch_lbl)
        cooler.addWidget(model_lbl, 0, 0)
        cooler.addWidget(self._cooler_model, 0, 1)
        cooler.addWidget(switch_lbl, 1, 0)
        cooler.addWidget(self._pump_switch, 1, 1)
        v.addLayout(cooler)
        self._setup_grid_host = QWidget()
        self._setup_grid_host.setObjectName("PwmReport_Setup_headers")
        self._setup_grid = QGridLayout(self._setup_grid_host)
        self._setup_grid.setHorizontalSpacing(10)
        v.addWidget(self._setup_grid_host)
        v.addStretch(1)
        return page

    def _populate_setup(self) -> None:
        settings = self._settings.settings if self._settings is not None else None
        cooler = sf.cooler_facts_from(settings.cooler_notes if settings else {})
        self._cooler_model.setText(cooler.model)
        self._pump_switch.setText(cooler.pump_switch)
        notes = settings.hardware_notes if settings else {}
        _clear(self._setup_grid)
        self._setup_widgets.clear()
        for col, title in enumerate(("Header", "Connected", "Fans on it", "BIOS mode", "Notes")):
            head = QLabel(title)
            head.setProperty("class", "CardMeta")
            self._setup_grid.addWidget(head, 0, col)
        row = 1
        for ch in self._channels:
            if not ch.is_hwmon:
                continue
            facts = sf.header_facts_from(notes.get(ch.channel_id))
            slug = _slug(ch.channel_id)
            self._setup_grid.addWidget(_plain(ch.name, f"PwmReport_Label_setup_{slug}"), row, 0)
            connected = QComboBox()
            connected.setObjectName(f"PwmReport_Combo_connected_{slug}")
            for token, label in sf.CONNECTED_CHOICES:
                connected.addItem(label, token)
            connected.setCurrentIndex(max(0, connected.findData(facts.connected)))
            name_value_control(connected, f"What is connected to {ch.name}")
            count = QComboBox()
            count.setObjectName(f"PwmReport_Combo_fans_{slug}")
            count.addItem("—", None)
            for n in range(1, sf.FANS_BEHIND_MAX + 1):
                count.addItem(str(n), n)
            count.setCurrentIndex(max(0, count.findData(facts.fans_behind)))
            count.setToolTip("More than one means a splitter or a hub.")
            name_value_control(count, f"How many fans are on {ch.name}")
            bios = QComboBox()
            bios.setObjectName(f"PwmReport_Combo_bios_{slug}")
            for token, label in sf.BIOS_MODE_CHOICES:
                bios.addItem(label, token)
            bios.setCurrentIndex(max(0, bios.findData(facts.bios_mode)))
            name_value_control(bios, f"BIOS header mode for {ch.name}")
            note = QLineEdit(facts.notes)
            note.setObjectName(f"PwmReport_Edit_notes_{slug}")
            note.setMaxLength(sf.NOTES_MAX)
            name_value_control(note, f"Notes for {ch.name}")
            for col, widget in enumerate((connected, count, bios, note), start=1):
                self._setup_grid.addWidget(widget, row, col)
            self._setup_widgets[ch.channel_id] = (connected, count, bios, note)
            row += 1
        self._setup_grid.setColumnStretch(4, 1)

    def _setup_values(self) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
        headers: dict[str, dict[str, object]] = {}
        for cid, (connected, count, bios, note) in self._setup_widgets.items():
            facts = sf.HeaderFacts(
                connected=connected.currentData() or "",
                fans_behind=count.currentData(),
                bios_mode=bios.currentData() or "",
                notes=note.text().strip(),
            )
            if not facts.is_blank():
                headers[cid] = facts.to_dict()
        cooler = sf.CoolerFacts(
            model=self._cooler_model.text().strip(), pump_switch=self._pump_switch.text().strip()
        )
        return headers, ({} if cooler.is_blank() else cooler.to_dict())

    def _save_setup(self) -> None:
        """Remember the facts (decision 7). Headers not on screen keep theirs."""
        if self._settings is None:
            return
        headers, cooler = self._setup_values()
        stored = dict(self._settings.settings.hardware_notes)
        for cid in self._setup_widgets:
            stored.pop(cid, None)
        stored.update(headers)
        if stored != self._settings.settings.hardware_notes or cooler != dict(
            self._settings.settings.cooler_notes
        ):
            self._settings.update(hardware_notes=stored, cooler_notes=cooler)

    # ── Page: Review & consent ──────────────────────────────────────────────

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PwmReport_Page_review")
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.addWidget(SectionHeader("Review and consent", object_name="PwmReport_Header_review"))
        self._review_host = QWidget()
        self._review_host.setObjectName("PwmReport_Review_plan")
        self._review_layout = QVBoxLayout(self._review_host)
        self._review_layout.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._review_host)
        v.addWidget(
            _plain(
                "The daemon performs every test and puts every header back when a test "
                "ends — even if Control-OFC is closed. A pump-protected header is never "
                "driven below 30 %. A test stops, and the run with it, if the daemon's "
                "thermal protection becomes active, if a temperature passes 85 °C or if "
                "temperature readings go stale; the tests after it are listed as not "
                "tested. You can cancel at any time.",
                "PwmReport_Label_safety",
            )
        )
        self._consent_box = QCheckBox("I understand these tests change fan speeds while they run.")
        self._consent_box.setObjectName("PwmReport_Check_consent")
        self._consent_box.toggled.connect(self._refresh_start)
        v.addWidget(self._consent_box)
        self._probe_host = QWidget()
        self._probe_host.setObjectName("PwmReport_Review_probes")
        self._probe_layout = QVBoxLayout(self._probe_host)
        self._probe_layout.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._probe_host)
        self._review_refusals = _plain("", "PwmReport_Label_reviewRefusals")
        self._review_refusals.setProperty("class", "WarningChip")
        self._review_refusals.setVisible(False)
        v.addWidget(self._review_refusals)
        v.addStretch(1)
        return page

    def consent_lines(self) -> list[str]:
        """The plain-words plan the user consents to — also saved in the report."""
        names = {c.channel_id: c.name for c in self._channels}
        lines: list[str] = []
        for step in build_plan(self.selection()):
            spec = cat.SPECS[step.test]
            name = names.get(step.channel_id, step.channel_id)
            est = duration_words(spec.worst_s)
            lines.append(f"{name} — {spec.title}: {spec.consent.format(name=name)} (up to {est})")
        return lines

    def _populate_review(self) -> None:
        _clear(self._review_layout)
        lines = self.consent_lines()
        if not lines:
            self._review_layout.addWidget(
                _plain(
                    "No tests selected. The report will read every channel's state, record "
                    "a short trace and check that nothing changed. Nothing is written.",
                    "PwmReport_Label_reviewEmpty",
                )
            )
        for i, line in enumerate(lines):
            self._review_layout.addWidget(_plain(f"• {line}", f"PwmReport_Label_plan_{i}"))
        _clear(self._probe_layout)
        self._probe_consents.clear()
        names = {c.channel_id: c.name for c in self._channels}
        for cid, tests in sorted(self.selection().items()):
            if cat.TEST_PROBE not in tests:
                continue
            box = QCheckBox(
                f"I'll stay at the machine while the stall probe runs on {names.get(cid, cid)} "
                "— the fan may stop for a short time."
            )
            box.setObjectName(f"PwmReport_Check_probeConsent_{_slug(cid)}")
            box.toggled.connect(self._refresh_start)
            self._probe_layout.addWidget(box)
            self._probe_consents[cid] = box
        self._consent_box.setChecked(False)
        self._consent_box.setVisible(bool(lines))
        self._refresh_refusals()

    def refusals(self) -> list[str]:
        state = self._state
        status = state.daemon_status if state is not None else None
        session = status.validation_session if status is not None else None
        return start_refusals(
            connected=state is not None and state.connection != ConnectionState.DISCONNECTED,
            thermal_state=status.thermal_state if status is not None else "normal",
            diagnostic_running=bool(status.verify_active) if status is not None else False,
            session_recording=bool(session is not None and session.is_recording),
            demo_mode=state is not None and state.mode == OperationMode.DEMO,
        )

    def _refresh_refusals(self, *_args) -> None:
        if self._controller.is_running():
            return
        reasons = self.refusals()
        text = "Cannot start now:\n" + "\n".join(f"• {r}" for r in reasons) if reasons else ""
        for label in (self._scope_refusals, self._review_refusals):
            label.setText(text)
            label.setVisible(bool(reasons))
        self._refresh_start()

    def can_start(self) -> bool:
        if self._controller.is_running() or self.refusals():
            return False
        writes = bool(self.selection())
        if writes and not self._consent_box.isChecked():
            return False
        return all(box.isChecked() for box in self._probe_consents.values())

    def _refresh_start(self, *_args) -> None:
        self._start_btn.setEnabled(self.can_start())

    # ── Page: Run ───────────────────────────────────────────────────────────

    def _build_run_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PwmReport_Page_run")
        v = QVBoxLayout(page)
        v.setSpacing(10)
        v.addWidget(SectionHeader("Running", object_name="PwmReport_Header_run"))
        row = QHBoxLayout()
        self._run_pill = StatusPill("Starting", "info", object_name="PwmReport_Pill_run")
        row.addWidget(self._run_pill)
        self._run_status = _plain("", "PwmReport_Label_runStatus")
        row.addWidget(self._run_status, 1)
        v.addLayout(row)
        self._run_live = _plain("", "PwmReport_Label_runLive", meta=True)
        v.addWidget(self._run_live)
        self._run_time = _plain("", "PwmReport_Label_runTime", meta=True)
        v.addWidget(self._run_time)
        self._run_table = QTableWidget(0, 4)
        self._run_table.setObjectName("PwmReport_Table_run")
        self._run_table.setHorizontalHeaderLabels(["Channel", "Test", "Status", "Note"])
        apply_dense_table(self._run_table)
        self._run_table.verticalHeader().setVisible(False)
        self._run_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._run_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._run_table, 1)
        return page

    def _on_run_changed(self) -> None:
        runner = self._controller.runner
        if runner is None:
            return
        doc = runner.doc
        names = {c.get("channel_id"): c.get("name") for c in doc.get("channels") or []}
        steps = doc.get("steps") or []
        table = self._run_table
        if table.rowCount() != len(steps):
            table.setRowCount(len(steps))
        for row, step in enumerate(steps):
            label, tone = step_status(str(step.get("status") or ""))
            cid = str(step.get("channel_id") or "")
            values = (
                str(names.get(cid) or cid),
                cat.SPECS[step["test"]].title if step.get("test") in cat.SPECS else "",
                label,
                str(step.get("reason") or ""),
            )
            for col, text in enumerate(values):
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, col, item)
                if item.text() != text:
                    item.setText(text)
                if col == 2:
                    item.setData(Qt.ItemDataRole.UserRole, tone)
        done, total = runner.progress()
        current = runner.current_step
        if runner.phase == "baseline":
            status = "Reading the starting state of every channel…"
        elif runner.phase == "final":
            status = "Reading the final state and checking that everything was put back…"
        elif runner.finished:
            status = "Finished."
        elif current is not None:
            name = names.get(current.get("channel_id")) or current.get("channel_id")
            title = cat.SPECS[current["test"]].title
            status = f"Test {done + 1} of {total}: {title} on {name}"
            if runner.step_phase == "handback":
                status += " — waiting for the daemon to hand the header back"
        else:
            status = "Working…"
        if runner.stopping and not runner.finished:
            status += " (stopping)"
        self._run_status.setText(status)
        self._run_pill.set_text("Running" if not runner.finished else "Done")
        self._run_pill.set_state("info" if not runner.finished else "ok")
        remaining = sum(
            cat.SPECS[s["test"]].typical_s
            for s in steps
            if s.get("status") in (d.STEP_PENDING, d.STEP_RUNNING) and s.get("test") in cat.SPECS
        )
        elapsed = int(self._controller.elapsed_s())
        self._run_time.setText(
            f"Elapsed {elapsed // 60}:{elapsed % 60:02d}"
            + (f" · about {duration_words(remaining)} left" if remaining else "")
        )
        self._cancel_btn.setEnabled(not runner.finished and not runner.stopping)
        self._refresh_live()

    def _refresh_live(self, *_args) -> None:
        runner = self._controller.runner
        if runner is None or runner.finished or self._state is None:
            self._run_live.setText("")
            return
        step = runner.current_step
        status = self._state.daemon_status
        thermal = status.thermal_state if status is not None else "unknown"
        if step is None:
            self._run_live.setText(f"Thermal state: {thermal}")
            return
        cid = step.get("channel_id")
        fan = next((f for f in self._state.fans if f.id == cid), None)
        parts = [str(self._state.fan_display_name(cid))]
        if fan is not None:
            if fan.pwm_commanded_pct is not None:
                parts.append(f"commanded {fan.pwm_commanded_pct} %")
            if fan.pwm_readback_pct is not None:
                parts.append(f"reads back {fan.pwm_readback_pct} %")
            parts.append(f"{fan.rpm} rpm" if fan.rpm is not None else "no RPM")
        self._run_live.setText(" · ".join(parts) + f" — thermal state: {thermal}")

    # ── Page: Report ────────────────────────────────────────────────────────

    def _build_report_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PwmReport_Page_report")
        self._report_layout = QVBoxLayout(page)
        self._report_layout.setSpacing(10)
        return page

    def _render_report(self, doc: dict, *, context: str = CONTEXT_LIVE, path=None) -> None:
        self._report_doc = doc
        self._report_context = context
        self._report_path = path
        self._evidence_filled = False
        # The previous report's widgets are being deleted below; a late
        # "re-apply done" must not reach through a stale reference to them.
        self._reapply_btn = None
        self._reapply_msg = None
        vm = build_report_view(doc)
        layout = self._report_layout
        _clear(layout)

        head = QHBoxLayout()
        head.addWidget(
            StatusPill(
                vm.state_label, _pill_state(vm.state_tone), object_name="PwmReport_Pill_state"
            )
        )
        head.addWidget(_plain(vm.when_line, "PwmReport_Label_when", meta=True), 1)
        layout.addLayout(head)
        if vm.state_reason:
            layout.addWidget(_plain(vm.state_reason, "PwmReport_Label_stateReason"))
        for i, line in enumerate(vm.summary_lines):
            layout.addWidget(_plain(line, f"PwmReport_Label_summary_{i}"))
        if vm.trace_note:
            layout.addWidget(_plain(vm.trace_note, "PwmReport_Label_traceNote", meta=True))
        if context == CONTEXT_LIVE:
            saved = self._controller.last_path
            where = f"Saved to {saved}" if saved is not None else ""
        elif context == CONTEXT_SAVED:
            where = f"Saved report, opened from {path}"
        else:
            where = f"Opened from {path} — shown only; it is not saved on this computer."
        if where:
            layout.addWidget(_plain(where, "PwmReport_Label_savedPath", meta=True))

        self._add_findings(layout, "Needs attention", "attention", vm.attention, expanded=True)
        self._add_findings(layout, "Observations", "observations", vm.observations, expanded=True)
        self._add_restoration(layout, vm)
        for section in vm.channels:
            self._add_channel(layout, section)
        self._add_findings(layout, "Not tested", "notTested", vm.not_tested, expanded=False)
        self._add_rows(layout, "Environment", "environment", vm.environment)
        self._add_rows(layout, "Configuration", "configuration", vm.configuration)
        self._add_rows(layout, "How to read this report", "legend", vm.legend)

        evidence = CollapsibleSection(
            "Evidence (the daemon's raw answers)", "PwmReport_Section_evidence", expanded=False
        )
        self._evidence_text = QPlainTextEdit()
        self._evidence_text.setObjectName("PwmReport_Text_evidence")
        self._evidence_text.setReadOnly(True)
        self._evidence_text.setMinimumHeight(240)
        name_value_control(self._evidence_text, "Evidence: the daemon's raw answers")
        evidence.add_widget(self._evidence_text)
        # Filled on first expand: a long run's evidence is hundreds of kilobytes.
        evidence.toggled.connect(self._fill_evidence)
        layout.addWidget(evidence)
        layout.addStretch(1)

    def _finding_widget(self, row: FindingRow, object_name: str) -> QWidget:
        card = QFrame()
        card.setObjectName(object_name)
        card.setProperty("class", "Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 6, 10, 6)
        v.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(
            StatusPill(
                row.result_label, _pill_state(row.result_tone), object_name=f"{object_name}_Pill"
            )
        )
        top.addWidget(_plain(row.statement, f"{object_name}_Statement"), 1)
        v.addLayout(top)
        meta = [f"Source: {row.provenance_label}"]
        if row.scope:
            meta.insert(0, f"Scope: {row.scope}")
        v.addWidget(_plain(" · ".join(meta), f"{object_name}_Meta", meta=True))
        return card

    def _add_findings(
        self, layout, title: str, key: str, rows: list[FindingRow], *, expanded: bool
    ) -> None:
        section = CollapsibleSection(
            f"{title} ({len(rows)})", f"PwmReport_Section_{key}", expanded=expanded
        )
        if not rows:
            section.add_widget(_plain("Nothing here.", f"PwmReport_Label_{key}Empty", meta=True))
        for row in rows:
            section.add_widget(
                self._finding_widget(row, f"PwmReport_Finding_{key}_{row.finding_id}")
            )
        layout.addWidget(section)

    def _add_restoration(self, layout, vm: ReportView) -> None:
        section = CollapsibleSection(
            f"Restoration ({len(vm.restoration)})", "PwmReport_Section_restoration", expanded=True
        )
        for row in vm.restoration:
            section.add_widget(
                self._finding_widget(row, f"PwmReport_Finding_restoration_{row.finding_id}")
            )
        if vm.can_reapply and self._report_context != CONTEXT_LIVE:
            section.add_widget(
                _plain(REOPENED_REAPPLY_NOTE, "PwmReport_Label_reapplyReopened", meta=True)
            )
        elif vm.can_reapply:
            self._reapply_btn = make_button(
                "Re-apply profile",
                "primary",
                object_name="PwmReport_Btn_reapply",
                accessible_name="Re-apply the active profile",
            )
            self._reapply_btn.clicked.connect(self._confirm_reapply)
            section.add_widget(self._reapply_btn)
        self._reapply_msg = _plain("", "PwmReport_Label_reapply", meta=True)
        self._reapply_msg.setVisible(False)
        section.add_widget(self._reapply_msg)
        layout.addWidget(section)

    def _add_channel(self, layout, section) -> None:
        slug = _slug(section.channel_id)
        box = CollapsibleSection(section.name, f"PwmReport_Section_channel_{slug}", expanded=False)
        box.add_widget(_plain(section.subtitle, f"PwmReport_Label_channelSub_{slug}", meta=True))
        grid = QGridLayout()
        for r, (label, value) in enumerate(section.facts):
            grid.addWidget(_plain(label, f"PwmReport_Label_fact_{slug}_{r}", meta=True), r, 0)
            grid.addWidget(_plain(value, f"PwmReport_Value_fact_{slug}_{r}"), r, 1)
        grid.setColumnStretch(1, 1)
        box.add_layout(grid)
        for i, test in enumerate(section.tests):
            line = QHBoxLayout()
            line.addWidget(
                StatusPill(
                    test.status_label,
                    _pill_state(test.status_tone),
                    object_name=f"PwmReport_Pill_test_{slug}_{i}",
                )
            )
            text = f"{test.title}" + (f" — {test.reason}" if test.reason else "")
            line.addWidget(_plain(text, f"PwmReport_Label_test_{slug}_{i}"), 1)
            box.add_layout(line)
        for row in section.findings:
            box.add_widget(self._finding_widget(row, f"PwmReport_Finding_{slug}_{row.finding_id}"))
        if section.probe_rows:
            table = QTableWidget(len(section.probe_rows), len(PROBE_COLUMNS))
            table.setObjectName(f"PwmReport_Table_probe_{slug}")
            table.setHorizontalHeaderLabels(list(PROBE_COLUMNS))
            apply_dense_table(table)
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            for r, values in enumerate(section.probe_rows):
                for c, value in enumerate(values):
                    table.setItem(r, c, QTableWidgetItem(value))
            table.resizeColumnsToContents()
            table.setMinimumHeight(min(320, 40 + 26 * len(section.probe_rows)))
            box.add_widget(table)
        for kind, curve in (("sweep", section.sweep_curve), ("probe", section.probe_curve)):
            if curve is None:
                continue
            box.add_widget(
                _plain(
                    "Full sweep: RPM against duty (both legs)"
                    if kind == "sweep"
                    else "Stall probe: RPM against duty (down, then back up)",
                    f"PwmReport_Label_chart_{kind}_{slug}",
                    meta=True,
                )
            )
            chart = PwmResponseChart(object_name=f"PwmReport_Chart_{kind}_{slug}")
            chart.setMinimumHeight(220)
            chart.set_curve(curve)
            box.add_widget(chart)
        layout.addWidget(box)

    def _add_rows(self, layout, title: str, key: str, rows: list[tuple[str, str]]) -> None:
        section = CollapsibleSection(title, f"PwmReport_Section_{key}", expanded=False)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        for r, (label, value) in enumerate(rows):
            grid.addWidget(_plain(label, f"PwmReport_Label_{key}_{r}", meta=True), r, 0)
            grid.addWidget(_plain(value, f"PwmReport_Value_{key}_{r}"), r, 1)
        grid.setColumnStretch(1, 1)
        section.add_widget(host)
        layout.addWidget(section)

    def _fill_evidence(self, expanded: bool) -> None:
        if expanded and not self._evidence_filled and self._report_doc is not None:
            self._evidence_text.setPlainText(evidence_text(self._report_doc))
            self._evidence_filled = True

    # ── Navigation ──────────────────────────────────────────────────────────

    def current_page(self) -> int:
        return self._stack.currentIndex()

    def _show_page(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        running = self._controller.is_running()
        self._back_btn.setVisible(index in (PAGE_SETUP, PAGE_REVIEW))
        self._next_btn.setVisible(index in (PAGE_SCOPE, PAGE_SETUP))
        self._start_btn.setVisible(index == PAGE_REVIEW)
        self._cancel_btn.setVisible(index == PAGE_RUN and running)
        self._export_btn.setVisible(index == PAGE_REPORT)
        self._export_cmp_btn.setVisible(index == PAGE_COMPARE)
        self._history_btn.setVisible(
            index in (PAGE_SCOPE, PAGE_REPORT, PAGE_COMPARE) and not running
        )
        self._new_btn.setVisible(index in (PAGE_REPORT, PAGE_HISTORY) and not running)
        if index == PAGE_REVIEW:
            self._refresh_start()

    def _go_next(self) -> None:
        index = self.current_page()
        if index == PAGE_SCOPE:
            self._populate_setup()
            self._show_page(PAGE_SETUP)
        elif index == PAGE_SETUP:
            self._save_setup()
            self._populate_review()
            self._show_page(PAGE_REVIEW)

    def _go_back(self) -> None:
        index = self.current_page()
        if index == PAGE_SETUP:
            self._save_setup()
            self._show_page(PAGE_SCOPE)
        elif index == PAGE_REVIEW:
            self._show_page(PAGE_SETUP)

    def _new_report(self) -> None:
        if self._controller.is_running():
            return
        self._populate_scope()
        self._show_page(PAGE_SCOPE)
        self._refresh_refusals()

    # ── Run control ─────────────────────────────────────────────────────────

    def _start_run(self) -> None:
        if not self.can_start():
            return
        selection = self.selection()
        consent = frozenset(cid for cid, box in self._probe_consents.items() if box.isChecked())
        headers, cooler = self._setup_values()
        caps = self._state.capabilities if self._state is not None else None
        unavailable = {
            ch.channel_id: {
                t: a.reason
                for t in cat.TEST_ORDER
                if not (a := cat.availability(ch, t, caps)).available
            }
            for ch in self._channels
        }
        typical, worst = cat.estimate_seconds(selection)
        now = d.utc_now_iso()
        doc = d.new_document(
            report_id=d.new_report_id(now),
            created_at=now,
            gui_facts={
                "gui_version": APP_VERSION,
                "kernel": platform.release(),
                "python": platform.python_version(),
                "qt": f"Qt {qVersion()} / PySide6 {pyside_version}",
            },
            channels=[
                {
                    "channel_id": ch.channel_id,
                    "source": ch.source,
                    "name": ch.name,
                    "role": ch.role,
                    "role_source": ch.role_source,
                    "writable": ch.writable,
                    "rpm_available": ch.rpm_available,
                    "rpm": ch.rpm,
                    "in_profile": ch.in_profile,
                    "pump_protected": ch.pump_protected,
                    "effective_min_pwm_pct": ch.effective_min_pwm_pct,
                    "stop_permitted": ch.stop_permitted,
                }
                for ch in self._channels
            ],
            user_facts={
                "cooler": cooler,
                "headers": {
                    cid: facts
                    for cid, facts in headers.items()
                    if any(ch.channel_id == cid for ch in self._channels)
                },
            },
            plan={
                "selections": {cid: sorted(tests) for cid, tests in sorted(selection.items())},
                "probe_consent": sorted(consent),
                "consent_text": self.consent_lines(),
                "confirmed_at": now,
                "estimate_s": [typical, worst],
                "unavailable": {cid: v for cid, v in unavailable.items() if v},
            },
        )
        if self._controller.start(doc, build_plan(selection), consent):
            self._run_table.setRowCount(0)
            self._show_page(PAGE_RUN)
            self._on_run_changed()

    def _cancel_run(self) -> None:
        self._controller.cancel()
        self._cancel_btn.setEnabled(False)

    def _on_run_finished(self, doc: object) -> None:
        if isinstance(doc, dict):
            self._render_report(doc, context=CONTEXT_LIVE)
            self._show_page(PAGE_REPORT)

    # ── History and comparison (Stage 5) ────────────────────────────────────

    @property
    def history_page(self) -> PwmReportHistoryPage:
        return self._history

    @property
    def compare_page(self) -> PwmReportComparePage:
        return self._compare_page

    def show_history(self) -> None:
        if self._controller.is_running():
            return
        self._history.refresh()
        self._show_page(PAGE_HISTORY)

    def _open_report(self, doc: object, path: object, imported: bool) -> None:
        if not isinstance(doc, dict) or self._controller.is_running():
            return
        try:
            self._render_report(doc, context=CONTEXT_FILE if imported else CONTEXT_SAVED, path=path)
        except Exception as e:  # a file from elsewhere is untrusted beyond its schema
            self._report_doc = None
            self._damaged(path, e)
            return
        self._show_page(PAGE_REPORT)

    def _show_comparison(self, first: object, second: object) -> None:
        if not (isinstance(first, dict) and isinstance(second, dict)):
            return
        try:
            self._compare_page.show_comparison(compare_reports(first, second))
        except Exception as e:  # as above: either side may be a file from elsewhere
            self._damaged(None, e)
            return
        self._show_page(PAGE_COMPARE)

    def _damaged(self, path: object, error: Exception) -> None:
        where = f"{path}: " if path else ""
        QMessageBox.warning(
            self,
            "Cannot show the report",
            f"{where}the report file is damaged or was not written by Control-OFC "
            f"({type(error).__name__}).",
        )
        self.show_history()

    def _on_save_failed(self, message: str) -> None:
        self._run_status.setText(f"The report could not be saved: {message}")

    # ── Export and re-apply ─────────────────────────────────────────────────

    def _export_current(self, fmt: str) -> None:
        if self._report_doc is not None:
            export_report(self, self._report_doc, fmt)

    def _export_other(self, doc: object, fmt: str, _imported: bool) -> None:
        if isinstance(doc, dict):
            export_report(self, doc, fmt)

    def _export_comparison(self, fmt: str) -> None:
        if self._compare_page.comparison is not None:
            export_comparison(self, self._compare_page.comparison, fmt)

    def reapply_profile_id(self) -> str:
        """The profile to re-apply: the one active at the end of the run, else at
        its start. Never a guess — without either there is nothing to offer."""
        doc = self._report_doc or {}
        final = d.status_body((doc.get("snapshots") or {}).get("final"))
        return str(
            final.get("active_profile_id")
            or (doc.get("configuration") or {}).get("active_profile_id")
            or ""
        )

    def _confirm_reapply(self) -> None:
        profile_id = self.reapply_profile_id()
        if not profile_id:
            return
        answer = QMessageBox.question(
            self,
            "Re-apply profile",
            "Re-activating the profile makes the daemon take every header it controls "
            "back under the profile's curves. It also ends any manual override that is "
            "active (DEC-189). Continue?",
        )
        if answer == QMessageBox.StandardButton.Yes and self._controller.reapply_profile(
            profile_id
        ):
            if self._reapply_btn is not None:
                self._reapply_btn.setEnabled(False)
            if self._reapply_msg is not None:
                self._reapply_msg.setText("Re-applying the profile…")
                self._reapply_msg.setVisible(True)

    def _on_reapply_done(self, ok: bool, message: str) -> None:
        if self._reapply_msg is None:
            return
        self._reapply_msg.setText(message)
        self._reapply_msg.setVisible(True)
        if not ok and self._reapply_btn is not None:
            self._reapply_btn.setEnabled(True)

    # ── Theme ───────────────────────────────────────────────────────────────

    def set_theme(self, tokens) -> None:
        """Forward a live theme change to the charts (`P8-bx`: the page's fan-out
        stops at this window unless it carries the last hop itself)."""
        for chart in self.findChildren(PwmResponseChart):
            chart.set_theme(tokens)

    # ── Closing ─────────────────────────────────────────────────────────────

    def _confirm_close_during_run(self) -> bool:
        """True when the window may close. Mid-run: ask, then cancel."""
        if not self._controller.is_running():
            return True
        answer = QMessageBox.question(
            self,
            "Cancel the running report?",
            "A test is running. Closing this window cancels it: the daemon stops the "
            "current test and puts the header back, and the report is saved with the "
            "remaining tests listed as not tested. Close and cancel?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._controller.cancel(REASON_WINDOW_CLOSED)
        return True

    def reject(self) -> None:
        # Esc and the title-bar close both arrive here or in closeEvent.
        if self._confirm_close_during_run():
            self.hide()

    def closeEvent(self, event) -> None:
        if self._confirm_close_during_run():
            event.ignore()
            self.hide()
        else:
            event.ignore()
