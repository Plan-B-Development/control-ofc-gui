"""System State page — migrated Diagnostics ▸ Troubleshooting (DEC-211).

A thin renderer over the Qt-free ``services.system_state_view`` view-models,
styled with the Stage-1 components. Presents the health issues (unified,
severity-sorted cards with doc-link buttons), the BIOS-reclaim Interference
Monitor (a static custom-paint radial gauge), the Safety & GPU limits, and the
Hardware Registry table. The preserved PWM/GPU verification actions live behind
an "Advanced actions" collapsible.

Fed by the shared ``DiagnosticsService.last_hw_diagnostics`` cache (``GET
/diagnostics/hardware``, fetched on first show if nothing warmed it). Owns three
QThread workers (PWM verify single+all, GPU verify/restore, hw-diag fetch) — the
same ``_SocketWorker`` classes the old tab uses, which take only a socket path.
Presentation-only: no daemon/API/schema/control/safety change.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.knowledge.hwmon_label_resolver import clear_libsensors_cache
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.system_state_view import (
    build_system_state_vm,
    build_verify_headers,
    daemon_version_at_least,
)
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader
from control_ofc.ui.components.gauges import RadialGauge
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.hwmon_guidance import dual_chip_verify_hint, verification_guidance
from control_ofc.ui.pages.diagnostics_workers import _GpuVerifyWorker, _HwDiagWorker, _VerifyWorker
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.readiness_report import (
    ReadinessReportDialog,
    build_readiness_report_html,
    gpu_verify_problems,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from control_ofc.api.client import DaemonClient
    from control_ofc.api.models import (
        GpuFanResetResult,
        GpuVerifyResult,
        HardwareDiagnosticsResult,
        HwmonHeader,
        HwmonVerifyResult,
    )
    from control_ofc.services.app_state import AppState
    from control_ofc.services.profile_service import ProfileService

log = logging.getLogger(__name__)

# DEC-147 GPU restore tooltips (copied — the old page is untouched this stage).
_GPU_RESTORE_TOOLTIP_READY = (
    "Hand the GPU fan back to the firmware's automatic curve (PMFW default) — "
    "undoes a static speed set this session."
)
_GPU_RESTORE_TOOLTIP_GATED = (
    "The active profile is driving the GPU fan — remove it from its fan role "
    "or deactivate the profile first."
)

_REGISTRY_COLS = ["Status", "Chip / Component", "Driver", "Driver Status", "Mainline", "Headers"]
_REG_STATUS = 0
_REG_MAINLINE = 4


class SystemStatePage(QWidget):
    """The migrated Troubleshooting content as a standalone page."""

    # Main-thread → worker-thread requests (queued).
    _verify_request = Signal(str)
    _gpu_verify_request = Signal(str)
    _gpu_reset_request = Signal(str)
    _hw_diag_request = Signal()
    _rescan_request = Signal()  # DEC-216: footer Rescan Hardware (relocated from Diagnostics)

    def __init__(
        self,
        state: AppState | None = None,
        diagnostics_service: DiagnosticsService | None = None,
        client: DaemonClient | None = None,
        profile_service: ProfileService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SystemState_Root")
        self._state = state
        self._diag = diagnostics_service or DiagnosticsService(state)
        self._client = client
        self._profile_service = profile_service

        # Worker/thread pairs (lazy).
        self._verify_thread: QThread | None = None
        self._verify_worker: _VerifyWorker | None = None
        self._hw_diag_thread: QThread | None = None
        self._hw_diag_worker: _HwDiagWorker | None = None
        self._gpu_verify_thread: QThread | None = None
        self._gpu_verify_worker: _GpuVerifyWorker | None = None

        # Action state.
        self._verify_active_header: str | None = None
        self._verify_all_queue: list[str] = []
        self._verify_all_results: list[tuple[str, str]] = []
        self._verify_all_total = 0
        self._gpu_verify_bdf: str | None = None
        self._gpu_verify_unsupported = False
        self._report_dialog: ReadinessReportDialog | None = None
        self._hw_diag_fetched = False
        self._rescan_in_flight = False  # DEC-216: guards the footer Rescan action

        self._build_ui()

        if state is not None:
            # Re-gate the GPU restore button when the active profile changes.
            state.active_profile_changed.connect(self._update_gpu_restore_gate)

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        scroll.setWidget(body)

        # Header.
        title = QLabel("System State")
        title.setObjectName("SystemState_Label_title")
        title.setProperty("class", "PageTitle")
        layout.addWidget(title)
        subtitle = QLabel("Health overview, hardware registry, and system interference")
        subtitle.setObjectName("SystemState_Label_subtitle")
        subtitle.setProperty("class", "PageSubtitle")
        layout.addWidget(subtitle)

        # DEC-216: outcome line for the global footer's Rescan Hardware action,
        # relocated here from the retired Diagnostics page. Hidden until a rescan
        # runs; the footer surfaces this page first so the result is always seen.
        self._rescan_result_label = QLabel("")
        self._rescan_result_label.setObjectName("SystemState_Label_rescanResult")
        self._rescan_result_label.setProperty("class", "CardMeta")
        self._rescan_result_label.setWordWrap(True)
        self._rescan_result_label.setVisible(False)
        layout.addWidget(self._rescan_result_label)

        # Row 1: health (2) | interference + safety (1).
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        row1.addWidget(self._build_health_card(), 2)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_interference_card())
        right.addWidget(self._build_safety_card())
        right.addStretch(1)
        right_holder = QWidget()
        right_holder.setLayout(right)
        row1.addWidget(right_holder, 1)
        layout.addLayout(row1)

        # Row 2: hardware registry.
        layout.addWidget(self._build_registry_card())

        # Advanced actions (preserved PWM/GPU verify + open report).
        layout.addWidget(self._build_advanced_section())
        layout.addStretch(1)

    def _build_health_card(self) -> QWidget:
        card = Card()
        card.setObjectName("SystemState_Card_health")
        v = QVBoxLayout(card)
        header = SectionHeader(
            "System Health Overview", object_name="SystemState_SectionHeader_health"
        )
        self._issue_pill = StatusPill("—", "neutral")
        self._issue_pill.setObjectName("SystemState_Pill_issueCount")
        header.add_trailing(self._issue_pill)
        v.addWidget(header)

        self._summary_label = QLabel("—")
        self._summary_label.setObjectName("SystemState_Label_summary")
        self._summary_label.setProperty("class", "CardMeta")
        self._summary_label.setWordWrap(True)
        v.addWidget(self._summary_label)

        self._issues_container = QWidget()
        self._issues_layout = QVBoxLayout(self._issues_container)
        self._issues_layout.setContentsMargins(0, 0, 0, 0)
        self._issues_layout.setSpacing(8)
        v.addWidget(self._issues_container)
        v.addStretch(1)
        return card

    def _build_interference_card(self) -> QWidget:
        card = Card()
        card.setObjectName("SystemState_Card_interference")
        v = QVBoxLayout(card)
        v.addWidget(
            SectionHeader(
                "Interference Monitor", object_name="SystemState_SectionHeader_interference"
            )
        )
        self._gauge = RadialGauge(object_name="SystemState_Gauge_reverts")
        gauge_row = QHBoxLayout()
        gauge_row.addStretch(1)
        gauge_row.addWidget(self._gauge)
        gauge_row.addStretch(1)
        v.addLayout(gauge_row)

        self._contention_title = QLabel("—")
        self._contention_title.setObjectName("SystemState_Label_contentionTitle")
        self._contention_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._contention_title.setProperty("class", "PageSubtitle")
        v.addWidget(self._contention_title)

        self._header_id_label = QLabel("")
        self._header_id_label.setObjectName("SystemState_Label_headerId")
        self._header_id_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _mono(self._header_id_label)
        v.addWidget(self._header_id_label)

        self._interference_explain = QLabel("")
        self._interference_explain.setObjectName("SystemState_Label_interferenceExplain")
        self._interference_explain.setProperty("class", "CardMeta")
        self._interference_explain.setWordWrap(True)
        v.addWidget(self._interference_explain)
        return card

    def _build_safety_card(self) -> QWidget:
        card = Card()
        card.setObjectName("SystemState_Card_safety")
        v = QVBoxLayout(card)
        v.addWidget(
            SectionHeader("Safety & GPU Limits", object_name="SystemState_SectionHeader_safety")
        )

        thermal_row = QHBoxLayout()
        tl = QLabel("CPU Thermal State")
        tl.setProperty("class", "CardMeta")
        thermal_row.addWidget(tl)
        thermal_row.addStretch(1)
        self._thermal_label = QLabel("—")
        self._thermal_label.setObjectName("SystemState_Label_thermal")
        thermal_row.addWidget(self._thermal_label)
        self._thermal_pill = StatusPill("—", "neutral")
        self._thermal_pill.setObjectName("SystemState_Pill_thermal")
        thermal_row.addWidget(self._thermal_pill)
        v.addLayout(thermal_row)

        self._gpu_model_label = QLabel("")
        self._gpu_model_label.setObjectName("SystemState_Label_gpuModel")
        v.addWidget(self._gpu_model_label)

        self._gpu_rows_container = QWidget()
        self._gpu_rows_layout = QVBoxLayout(self._gpu_rows_container)
        self._gpu_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._gpu_rows_layout.setSpacing(4)
        v.addWidget(self._gpu_rows_container)

        self._speed_bar_holder = QWidget()
        self._speed_bar_holder.setObjectName("SystemState_Bar_speedRange")
        speed_v = QVBoxLayout(self._speed_bar_holder)
        speed_v.setContentsMargins(0, 4, 0, 0)
        speed_v.setSpacing(2)
        self._speed_label = QLabel("")
        self._speed_label.setProperty("class", "CardMeta")
        speed_v.addWidget(self._speed_label)
        self._speed_bar = QWidget()
        self._speed_bar.setFixedHeight(6)
        self._speed_bar_inner = QHBoxLayout(self._speed_bar)
        self._speed_bar_inner.setContentsMargins(0, 0, 0, 0)
        self._speed_bar_inner.setSpacing(0)
        speed_v.addWidget(self._speed_bar)
        v.addWidget(self._speed_bar_holder)
        self._speed_bar_holder.setVisible(False)
        return card

    def _build_registry_card(self) -> QWidget:
        card = Card()
        card.setObjectName("SystemState_Card_registry")
        v = QVBoxLayout(card)
        header = SectionHeader(
            "Hardware Registry", object_name="SystemState_SectionHeader_registry"
        )
        self._registry_summary = QLabel("—")
        self._registry_summary.setObjectName("SystemState_Label_registrySummary")
        self._registry_summary.setProperty("class", "CardMeta")
        header.add_trailing(self._registry_summary)
        v.addWidget(header)

        self._registry_table = QTableWidget(0, len(_REGISTRY_COLS))
        self._registry_table.setObjectName("SystemState_Table_registry")
        self._registry_table.setHorizontalHeaderLabels(_REGISTRY_COLS)
        apply_dense_table(self._registry_table)
        self._registry_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._registry_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._registry_table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self._registry_table)
        return card

    def _build_advanced_section(self) -> QWidget:
        section = CollapsibleSection(
            "Advanced actions", "SystemState_Section_advanced", expanded=False
        )
        # PWM verify.
        pwm_row = QHBoxLayout()
        self._verify_combo = QComboBox()
        self._verify_combo.setObjectName("SystemState_Combo_verifyHeader")
        pwm_row.addWidget(self._verify_combo, 1)
        self._verify_btn = make_button(
            "Test PWM Control", "secondary", object_name="SystemState_Btn_verifyPwm"
        )
        self._verify_btn.clicked.connect(self._run_pwm_verify)
        pwm_row.addWidget(self._verify_btn)
        self._verify_all_btn = make_button(
            "Verify All Writable", "secondary", object_name="SystemState_Btn_verifyAll"
        )
        self._verify_all_btn.clicked.connect(self._run_pwm_verify_all)
        pwm_row.addWidget(self._verify_all_btn)
        section.add_layout(pwm_row)

        self._verify_result_label = QLabel("")
        self._verify_result_label.setObjectName("SystemState_Label_verifyResult")
        self._verify_result_label.setWordWrap(True)
        self._verify_result_label.setVisible(False)
        section.add_widget(self._verify_result_label)
        self._verify_all_progress_label = QLabel("")
        self._verify_all_progress_label.setObjectName("SystemState_Label_verifyAllProgress")
        self._verify_all_progress_label.setWordWrap(True)
        self._verify_all_progress_label.setVisible(False)
        section.add_widget(self._verify_all_progress_label)

        # GPU verify + restore.
        gpu_row = QHBoxLayout()
        self._gpu_verify_btn = make_button(
            "Test GPU Fan Control", "secondary", object_name="SystemState_Btn_verifyGpu"
        )
        self._gpu_verify_btn.clicked.connect(self._run_gpu_verify)
        self._gpu_verify_btn.setVisible(False)
        gpu_row.addWidget(self._gpu_verify_btn)
        self._gpu_restore_btn = make_button(
            "Restore GPU Fan to Automatic", "secondary", object_name="SystemState_Btn_restoreGpu"
        )
        self._gpu_restore_btn.clicked.connect(self._run_gpu_restore)
        self._gpu_restore_btn.setVisible(False)
        gpu_row.addWidget(self._gpu_restore_btn)
        gpu_row.addStretch(1)
        section.add_layout(gpu_row)

        self._gpu_verify_result_label = QLabel("")
        self._gpu_verify_result_label.setObjectName("SystemState_Label_verifyGpuResult")
        self._gpu_verify_result_label.setWordWrap(True)
        self._gpu_verify_result_label.setVisible(False)
        section.add_widget(self._gpu_verify_result_label)
        self._gpu_restore_result_label = QLabel("")
        self._gpu_restore_result_label.setObjectName("SystemState_Label_restoreGpuResult")
        self._gpu_restore_result_label.setWordWrap(True)
        self._gpu_restore_result_label.setVisible(False)
        section.add_widget(self._gpu_restore_result_label)

        # Open Full Report.
        self._open_report_btn = make_button(
            "Open Full Report", "ghost", object_name="SystemState_Btn_openReport"
        )
        self._open_report_btn.clicked.connect(self._open_readiness_report)
        self._open_report_btn.setEnabled(False)
        section.add_widget(self._open_report_btn)
        return section

    # ── Fetch + render ───────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        cached = self._diag.last_hw_diagnostics
        if cached is not None:
            self._render(cached)  # old tab already fetched → render from cache
        elif not self._hw_diag_fetched:
            self._hw_diag_fetched = True  # latch BEFORE emit → a re-show never double-fetches
            self._fetch_hardware_diagnostics()

    def _fetch_hardware_diagnostics(self) -> None:
        if not self._client:
            self._summary_label.setText("Cannot fetch: no daemon connection")
            return
        if not self._ensure_hw_diag_worker():
            self._summary_label.setText("Cannot fetch: no daemon socket path")
            return
        self._hw_diag_request.emit()

    @Slot(object)
    def _on_hw_diag_ok(self, result: HardwareDiagnosticsResult) -> None:
        # Warm the shared cache (Overview/Sensors read board vendor from it on
        # their next poll) and render this page.
        self._diag.last_hw_diagnostics = result
        self._render(result)

    @Slot(str, str)
    def _on_hw_diag_error(self, category: str, message: str) -> None:
        if category == "unavailable":
            self._summary_label.setText(message or "Daemon unavailable — cannot fetch diagnostics")
        else:
            self._summary_label.setText(f"Diagnostics error: {message}")

    def _render(self, diag: HardwareDiagnosticsResult) -> None:
        vm = build_system_state_vm(diag)
        self._issue_pill.set_text(vm.issue_count_label)
        self._issue_pill.set_state(vm.issue_count_state)
        summary = vm.summary_line
        if vm.board_line:
            summary = f"{summary}  ·  {vm.board_line}"
        self._summary_label.setText(summary)
        self._registry_summary.setText(vm.summary_line)

        self._rebuild_issue_cards(vm.issue_cards)
        self._render_interference(vm.interference)
        self._render_safety(vm.safety_gpu)
        self._render_registry(vm.registry_rows)
        self._populate_verify_combo()
        self._update_gpu_verify_availability(diag)
        self._open_report_btn.setEnabled(True)
        if self._report_dialog is not None and self._report_dialog.isVisible():
            self._report_dialog.set_html(build_readiness_report_html(diag))

    def _rebuild_issue_cards(self, cards) -> None:
        _clear_layout(self._issues_layout)
        if not cards:
            ok = QLabel("No hardware issues detected — system ready.")
            ok.setObjectName("SystemState_Label_noIssues")
            ok.setProperty("class", "CardMeta")
            self._issues_layout.addWidget(ok)
            return
        for vm in cards:
            self._issues_layout.addWidget(self._make_issue_card(vm))

    def _make_issue_card(self, vm) -> QWidget:
        theme = active_theme()
        color = _severity_border_color(vm.severity_state, theme)
        card = QFrame()
        card.setObjectName(f"SystemState_IssueCard_{vm.key}")
        card.setProperty("class", "Card")
        row = QHBoxLayout(card)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        strip = QFrame()
        strip.setFixedWidth(3)
        strip.setStyleSheet(f"background: {color}; border: none;")
        row.addWidget(strip)

        body = QWidget()
        v = QVBoxLayout(body)
        v.setSpacing(4)
        caption = QLabel(f"{vm.severity_glyph} {vm.severity_word}")
        caption.setObjectName(f"SystemState_IssueSeverity_{vm.key}")
        caption.setStyleSheet(f"color: {color}; font-weight: bold;")
        v.addWidget(caption)
        title = QLabel(vm.title)
        title.setObjectName(f"SystemState_IssueTitle_{vm.key}")
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {theme.text_primary}; font-weight: 600;")
        v.addWidget(title)
        if vm.description:
            desc = QLabel(vm.description)
            desc.setObjectName(f"SystemState_IssueDesc_{vm.key}")
            desc.setProperty("class", "CardMeta")
            desc.setWordWrap(True)
            v.addWidget(desc)
        if vm.detail:
            box = QLabel(vm.detail)
            box.setObjectName(f"SystemState_IssueDetail_{vm.key}")
            box.setTextFormat(Qt.TextFormat.RichText)
            box.setWordWrap(True)
            box.setOpenExternalLinks(True)
            box.setStyleSheet(
                f"background: {theme.surface_2}; border: 1px solid {theme.border_default};"
                " border-radius: 4px; padding: 6px;"
            )
            v.addWidget(box)
        if vm.doc_url:
            btn = make_button(
                f"{vm.doc_title or 'Hardware Guide'} ↗",
                "secondary",
                object_name=f"SystemState_IssueDoc_{vm.key}",
            )
            btn.clicked.connect(lambda _=False, url=vm.doc_url: QDesktopServices.openUrl(QUrl(url)))
            doc_row = QHBoxLayout()
            doc_row.addWidget(btn)
            doc_row.addStretch(1)
            v.addLayout(doc_row)
        row.addWidget(body, 1)
        return card

    def _render_interference(self, vm) -> None:
        if vm.has_contention:
            self._gauge.set_value(
                vm.gauge_fraction,
                center_text=str(vm.highest_count),
                caption="REVERTS",
                state=vm.severity_state,
            )
        else:
            self._gauge.set_value(0.0, center_text="0", caption="REVERTS", state="ok")
        self._contention_title.setText(vm.title)
        self._header_id_label.setText(vm.header_id or "")
        self._header_id_label.setVisible(bool(vm.header_id))
        self._interference_explain.setText(vm.explanation)

    def _render_safety(self, vm) -> None:
        thermal = vm.thermal_text
        if vm.thermal_limit_text:
            thermal = f"{thermal}  ({vm.thermal_limit_text})"
        self._thermal_label.setText(thermal)
        self._thermal_pill.set_text(vm.thermal_text)
        self._thermal_pill.set_state(vm.thermal_state)

        _clear_layout(self._gpu_rows_layout)
        self._gpu_model_label.setText(vm.gpu_model if vm.has_gpu else "No discrete GPU detected")
        theme = active_theme()
        for r in vm.gpu_rows:
            label = QLabel(f"{r.label}: {r.value}")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {_row_state_color(r.state, theme)};")
            self._gpu_rows_layout.addWidget(label)

        if vm.speed_bar_visible and vm.speed_min is not None and vm.speed_max is not None:
            self._speed_label.setText(f"Firmware speed range: {vm.speed_min}% - {vm.speed_max}%")
            _clear_layout(self._speed_bar_inner)
            below = QFrame()
            below.setStyleSheet(f"background: {theme.status_crit}; border: none;")
            within = QFrame()
            within.setStyleSheet(f"background: {theme.status_ok}; border: none;")
            self._speed_bar_inner.addWidget(below, max(vm.speed_min, 1))
            self._speed_bar_inner.addWidget(within, max(vm.speed_max - vm.speed_min, 1))
            self._speed_bar_holder.setVisible(True)
        else:
            self._speed_bar_holder.setVisible(False)

    def _render_registry(self, rows) -> None:
        for r in range(self._registry_table.rowCount()):
            self._registry_table.removeCellWidget(r, _REG_STATUS)
        self._registry_table.setRowCount(len(rows))
        theme = active_theme()
        for i, vm in enumerate(rows):
            _ensure_items(self._registry_table, i, len(_REGISTRY_COLS))
            self._registry_table.item(i, 1).setText(vm.component)
            self._registry_table.item(i, 2).setText(vm.driver)
            self._registry_table.item(i, 3).setText(vm.driver_status)
            mainline_item = self._registry_table.item(i, _REG_MAINLINE)
            mainline_item.setText(vm.mainline)
            mainline_item.setForeground(QColor(_row_state_color(vm.mainline_state, theme)))
            self._registry_table.item(i, 5).setText(vm.headers)
            if vm.tooltip:
                for c in range(1, len(_REGISTRY_COLS)):
                    self._registry_table.item(i, c).setToolTip(vm.tooltip)
            _set_pill(self._registry_table, i, _REG_STATUS, vm.status_label, vm.status_state)

    def _populate_verify_combo(self) -> None:
        self._verify_combo.clear()
        headers = self._state.hwmon_headers if self._state else []
        for text, hid in build_verify_headers(headers):
            self._verify_combo.addItem(text, hid)
        self._verify_btn.setEnabled(self._verify_combo.count() > 0)

    # ── Worker lifecycle (ported) ────────────────────────────────────

    def _ensure_worker(self, worker, thread, worker_cls, connect: Callable) -> tuple:
        if worker is not None:
            return worker, thread, True
        socket_path = self._client.socket_path if self._client else None
        if not socket_path:
            return None, None, False
        thread = QThread(self)
        worker = worker_cls(socket_path)
        worker.moveToThread(thread)
        connect(worker)
        thread.start()
        return worker, thread, True

    def _ensure_verify_worker(self) -> bool:
        def connect(w: _VerifyWorker) -> None:
            self._verify_request.connect(w.do_verify, Qt.ConnectionType.QueuedConnection)
            w.verify_ok.connect(self._on_verify_ok, Qt.ConnectionType.QueuedConnection)
            w.verify_error.connect(self._on_verify_error, Qt.ConnectionType.QueuedConnection)

        self._verify_worker, self._verify_thread, ok = self._ensure_worker(
            self._verify_worker, self._verify_thread, _VerifyWorker, connect
        )
        return ok

    def _ensure_hw_diag_worker(self) -> bool:
        def connect(w: _HwDiagWorker) -> None:
            self._hw_diag_request.connect(w.do_fetch, Qt.ConnectionType.QueuedConnection)
            w.fetch_ok.connect(self._on_hw_diag_ok, Qt.ConnectionType.QueuedConnection)
            w.fetch_error.connect(self._on_hw_diag_error, Qt.ConnectionType.QueuedConnection)
            # DEC-216: this page now owns the footer's Rescan Hardware action
            # (relocated from the retired Diagnostics page).
            self._rescan_request.connect(w.do_rescan, Qt.ConnectionType.QueuedConnection)
            w.rescan_ok.connect(self._on_rescan_ok, Qt.ConnectionType.QueuedConnection)
            w.rescan_error.connect(self._on_rescan_error, Qt.ConnectionType.QueuedConnection)

        self._hw_diag_worker, self._hw_diag_thread, ok = self._ensure_worker(
            self._hw_diag_worker, self._hw_diag_thread, _HwDiagWorker, connect
        )
        return ok

    # ── hwmon rescan (footer action, relocated from Diagnostics — DEC-216) ──

    def run_hwmon_rescan(self) -> None:
        """Ask the daemon to re-enumerate hwmon devices (DEC-147), triggered by
        the global footer's Rescan Hardware action. main_window surfaces this
        page first so the outcome line is visible (fixing the retired nav-dead
        Diagnostics page's invisible feedback)."""
        if self._rescan_in_flight:
            return
        if not self._client:
            self._show_rescan_message("Cannot rescan: no daemon connection", "CriticalChip")
            return
        if not self._ensure_hw_diag_worker():
            # Unreachable in production (DaemonClient always has a socket path).
            self._show_rescan_message("Rescan unavailable: no daemon socket path", "CriticalChip")
            return
        self._rescan_in_flight = True
        self._show_rescan_message("Rescanning hardware…", "InfoChip")
        self._rescan_request.emit()

    def _show_rescan_message(self, text: str, css_class: str = "CardMeta") -> None:
        self._rescan_result_label.setText(text)
        set_chip_class(self._rescan_result_label, css_class)
        self._rescan_result_label.setVisible(True)

    @Slot(object)
    def _on_rescan_ok(self, headers: list[HwmonHeader]) -> None:
        """Apply a successful rescan: push the fresh header list through AppState
        (feeding the member picker, profile sanitization, and every other
        ``headers_updated`` consumer), drop the cached libsensors label config
        (so an ``/etc/sensors.d`` relabel surfaces without a GUI restart), then
        chain a hardware-diagnostics refetch so readiness reflects reality."""
        clear_libsensors_cache()
        if self._state is not None:
            self._state.set_hwmon_headers(headers)
        n = len(headers)
        self._show_rescan_message(
            f"Rescan complete — {n} PWM header(s) found. Sensors refresh on the "
            "next poll cycle. New fan-control hardware still requires a daemon "
            "restart.",
            "SuccessChip",
        )
        self._diag.log_event("info", "hwmon", f"Hardware rescan: {n} PWM header(s) found")
        self._rescan_in_flight = False
        self._fetch_hardware_diagnostics()

    @Slot(str, str)
    def _on_rescan_error(self, category: str, message: str) -> None:
        """Surface a rescan failure. The previously known header set is kept — a
        failed re-enumeration says nothing about the existing hardware."""
        if category == "unavailable":
            self._show_rescan_message(
                message or "Daemon unavailable — cannot rescan hardware", "CriticalChip"
            )
        else:
            self._show_rescan_message(f"Rescan error: {message}", "CriticalChip")
        self._diag.log_event("error", "hwmon", f"Hardware rescan failed: {message}")
        self._rescan_in_flight = False

    def _ensure_gpu_verify_worker(self) -> bool:
        def connect(w: _GpuVerifyWorker) -> None:
            self._gpu_verify_request.connect(w.do_verify, Qt.ConnectionType.QueuedConnection)
            self._gpu_reset_request.connect(w.do_reset, Qt.ConnectionType.QueuedConnection)
            w.verify_ok.connect(self._on_gpu_verify_ok, Qt.ConnectionType.QueuedConnection)
            w.verify_error.connect(self._on_gpu_verify_error, Qt.ConnectionType.QueuedConnection)
            w.reset_ok.connect(self._on_gpu_restore_ok, Qt.ConnectionType.QueuedConnection)
            w.reset_error.connect(self._on_gpu_restore_error, Qt.ConnectionType.QueuedConnection)

        self._gpu_verify_worker, self._gpu_verify_thread, ok = self._ensure_worker(
            self._gpu_verify_worker, self._gpu_verify_thread, _GpuVerifyWorker, connect
        )
        return ok

    def _teardown_worker(self, worker: QObject | None, thread: QThread | None, label: str) -> None:
        if worker is not None:
            QObject.disconnect(worker, None, None, None)
            worker.shutdown()
        if thread is not None:
            thread.quit()
            if not thread.wait(2000):
                log.warning("%s thread did not stop within 2s, terminating", label)
                thread.terminate()
                thread.wait(1000)

    def cleanup(self) -> None:
        self._teardown_worker(self._verify_worker, self._verify_thread, "Verify")
        self._verify_worker = self._verify_thread = None
        self._teardown_worker(self._hw_diag_worker, self._hw_diag_thread, "HW diagnostics")
        self._hw_diag_worker = self._hw_diag_thread = None
        self._teardown_worker(self._gpu_verify_worker, self._gpu_verify_thread, "GPU verify")
        self._gpu_verify_worker = self._gpu_verify_thread = None
        if self._report_dialog is not None:
            self._report_dialog.close()
            self._report_dialog = None

    # ── PWM verify (ported) ──────────────────────────────────────────

    def _run_pwm_verify(self) -> None:
        header_id = self._verify_combo.currentData()
        if not header_id:
            self._verify_result_label.setText("No writable header selected")
            self._verify_result_label.setVisible(True)
            return
        if not self._client:
            self._verify_result_label.setText("Cannot verify: no daemon connection")
            self._verify_result_label.setVisible(True)
            return
        self._verify_btn.setEnabled(False)
        self._verify_btn.setText("Testing...")
        self._verify_result_label.setVisible(False)
        if not self._ensure_verify_worker():
            self._verify_btn.setEnabled(True)
            self._verify_btn.setText("Test PWM Control")
            self._verify_result_label.setText("Verify unavailable: no socket path")
            self._verify_result_label.setVisible(True)
            return
        self._verify_active_header = header_id
        self._verify_request.emit(header_id)

    @Slot(object)
    def _on_verify_ok(self, result: HwmonVerifyResult) -> None:
        self._show_verify_result(result)
        self._verify_btn.setEnabled(True)
        self._verify_btn.setText("Test PWM Control")
        self._verify_active_header = None
        if self._verify_all_total > 0:
            self._verify_all_results.append((result.header_id, result.result))
            self._step_pwm_verify_all()

    @Slot(str, str)
    def _on_verify_error(self, category: str, message: str) -> None:
        if category == "unavailable":
            self._verify_result_label.setText(message or "Daemon unavailable during verify")
        else:
            self._verify_result_label.setText(f"Verify error: {message}")
        self._verify_result_label.setVisible(True)
        self._verify_btn.setEnabled(True)
        self._verify_btn.setText("Test PWM Control")
        header_id = self._verify_active_header or "unknown"
        self._verify_active_header = None
        if self._verify_all_total > 0:
            self._verify_all_results.append((header_id, f"error:{category}"))
            self._step_pwm_verify_all()

    def _show_verify_result(self, result: HwmonVerifyResult) -> None:
        status_map = {
            "effective": ("PWM control is working correctly", "SuccessChip"),
            "pwm_enable_reverted": (
                "BIOS/EC reverted pwm_enable — fan control is being overridden",
                "CriticalChip",
            ),
            "pwm_value_clamped": ("PWM value was clamped or ignored by hardware", "WarningChip"),
            "no_rpm_effect": (
                "PWM accepted but RPM did not change (fan may be disconnected or stalled)",
                "WarningChip",
            ),
            "rpm_unavailable": ("PWM write accepted but RPM readback unavailable", "CardMeta"),
        }
        summary, css_class = status_map.get(result.result, (f"Result: {result.result}", "CardMeta"))
        lines = [f"Result: {summary}"]
        if result.details:
            lines.append(result.details)
        init, final = result.initial_state, result.final_state
        if init.rpm is not None and final.rpm is not None:
            lines.append(f"RPM: {init.rpm} → {final.rpm}")

        board_vendor = chip_name = ""
        expected_chips: list[str] = []
        detected: list[str] = []
        hw = self._diag.last_hw_diagnostics
        if self._state:
            header = next((h for h in self._state.hwmon_headers if h.id == result.header_id), None)
            if header:
                chip_name = header.chip_name
        if hw is not None:
            board_vendor = hw.board.vendor
            expected_chips = list(hw.expected_chips)
            detected = [c.chip_name for c in hw.hwmon.chips_detected]
        guidance = verification_guidance(result.result, board_vendor, chip_name)
        if guidance:
            lines.append("")
            lines.append(f"Next step: {guidance}")
        dual_hint = dual_chip_verify_hint(result.result, expected_chips, detected)
        if dual_hint:
            lines.append("")
            lines.append(dual_hint)
        self._verify_result_label.setText("\n".join(lines))
        set_chip_class(self._verify_result_label, css_class)
        self._verify_result_label.setVisible(True)

    def _run_pwm_verify_all(self) -> None:
        if not self._state:
            self._verify_all_progress_label.setText("Cannot verify: no app state")
            self._verify_all_progress_label.setVisible(True)
            return
        if not self._client:
            self._verify_all_progress_label.setText("Cannot verify: no daemon connection")
            self._verify_all_progress_label.setVisible(True)
            return
        if self._verify_all_total > 0:
            return  # already running
        writable = [h.id for h in self._state.hwmon_headers if h.is_writable]
        if not writable:
            self._verify_all_progress_label.setText("No writable headers to test.")
            self._verify_all_progress_label.setVisible(True)
            return
        if not self._ensure_verify_worker():
            self._verify_all_progress_label.setText("Verify unavailable: no socket path")
            self._verify_all_progress_label.setVisible(True)
            return
        self._verify_all_queue = list(writable)
        self._verify_all_results = []
        self._verify_all_total = len(writable)
        self._verify_btn.setEnabled(False)
        self._verify_all_btn.setEnabled(False)
        self._verify_all_btn.setText("Testing...")
        set_chip_class(self._verify_all_progress_label, "CardMeta")
        self._verify_all_progress_label.setVisible(True)
        self._step_pwm_verify_all()

    def _finish_verify_all(self) -> None:
        self._verify_all_total = 0
        self._verify_btn.setEnabled(self._verify_combo.count() > 0)
        self._verify_all_btn.setEnabled(True)
        self._verify_all_btn.setText("Verify All Writable")

    def _step_pwm_verify_all(self) -> None:
        if not self._verify_all_queue:
            self._show_verify_all_summary()
            self._finish_verify_all()
            return
        header_id = self._verify_all_queue.pop(0)
        index = self._verify_all_total - len(self._verify_all_queue)
        self._verify_all_progress_label.setText(
            f"Testing {index}/{self._verify_all_total}: {header_id}"
        )
        self._verify_active_header = header_id
        self._verify_request.emit(header_id)

    def _show_verify_all_summary(self) -> None:
        if not self._verify_all_results:
            self._verify_all_progress_label.setText("Verify all: no results.")
            return
        critical_keys = {"pwm_enable_reverted"}
        warning_keys = {"pwm_value_clamped", "no_rpm_effect"}
        has_critical = any(
            r.startswith("error:") or r in critical_keys for _, r in self._verify_all_results
        )
        has_warning = any(r in warning_keys for _, r in self._verify_all_results)
        css_class = (
            "CriticalChip" if has_critical else "WarningChip" if has_warning else "SuccessChip"
        )
        n_done = len(self._verify_all_results)
        lines = [f"Verify all complete ({n_done}/{self._verify_all_total} tested):"]
        for header_id, result_str in self._verify_all_results:
            short = {
                "effective": "OK",
                "pwm_enable_reverted": "BIOS reclaimed",
                "pwm_value_clamped": "clamped",
                "no_rpm_effect": "no RPM change",
                "rpm_unavailable": "no tach",
            }.get(result_str, result_str)
            lines.append(f"  • {header_id}: {short}")
        self._verify_all_progress_label.setText("\n".join(lines))
        set_chip_class(self._verify_all_progress_label, css_class)

    # ── GPU verify + restore (ported) ────────────────────────────────

    def _update_gpu_verify_availability(self, diag: HardwareDiagnosticsResult) -> None:
        gpu = diag.gpu
        writable = bool(gpu and gpu.fan_control_method not in ("read_only", "none", ""))
        caps = getattr(self._state, "capabilities", None) if self._state else None
        version_ok = daemon_version_at_least(caps.daemon_version if caps else "", (1, 11, 0))
        self._gpu_verify_bdf = gpu.pci_bdf if (gpu and writable) else None
        show = bool(self._gpu_verify_bdf) and version_ok and not self._gpu_verify_unsupported
        self._gpu_verify_btn.setVisible(show)
        if not show:
            self._gpu_verify_result_label.setVisible(False)
        show_restore = bool(self._gpu_verify_bdf)
        self._gpu_restore_btn.setVisible(show_restore)
        if show_restore:
            self._update_gpu_restore_gate()
        else:
            self._gpu_restore_result_label.setVisible(False)

    def _run_gpu_verify(self) -> None:
        bdf = self._gpu_verify_bdf
        if not bdf:
            self._gpu_verify_result_label.setText("No GPU with a writable fan-control path.")
            self._gpu_verify_result_label.setVisible(True)
            return
        if not self._client:
            self._gpu_verify_result_label.setText("Cannot verify: no daemon connection")
            self._gpu_verify_result_label.setVisible(True)
            return
        self._gpu_verify_btn.setEnabled(False)
        self._gpu_verify_btn.setText("Testing...")
        self._gpu_verify_result_label.setVisible(False)
        if not self._ensure_gpu_verify_worker():
            self._gpu_verify_result_label.setText("GPU verify unavailable: no daemon socket path")
            self._gpu_verify_result_label.setVisible(True)
            self._gpu_verify_btn.setEnabled(True)
            self._gpu_verify_btn.setText("Test GPU Fan Control")
            return
        self._gpu_verify_request.emit(bdf)

    @Slot(object)
    def _on_gpu_verify_ok(self, result: GpuVerifyResult) -> None:
        self._show_gpu_verify_result(result)
        self._gpu_verify_btn.setEnabled(True)
        self._gpu_verify_btn.setText("Test GPU Fan Control")

    @Slot(str, str)
    def _on_gpu_verify_error(self, category: str, message: str) -> None:
        if category == "unsupported":
            self._gpu_verify_unsupported = True
            self._gpu_verify_btn.setVisible(False)
            self._gpu_verify_result_label.setVisible(False)
        elif category == "unavailable":
            self._gpu_verify_result_label.setText(message or "Daemon unavailable during GPU verify")
            self._gpu_verify_result_label.setVisible(True)
        else:
            self._gpu_verify_result_label.setText(f"GPU verify error: {message}")
            self._gpu_verify_result_label.setVisible(True)
        self._gpu_verify_btn.setEnabled(True)
        self._gpu_verify_btn.setText("Test GPU Fan Control")

    def _show_gpu_verify_result(self, result: GpuVerifyResult) -> None:
        summary_map = {
            "effective": (
                "GPU fan control is working — the fan responded to the test.",
                "SuccessChip",
            ),
            "zero_rpm_suppressed": (
                "GPU fan control works; the fan is in zero-RPM idle (normal).",
                "SuccessChip",
            ),
            "rpm_unavailable": (
                "Write confirmed via curve read-back, but this GPU exposes no fan-RPM sensor.",
                "WarningChip",
            ),
            "curve_not_applied": ("The GPU ignored the fan-control write.", "CriticalChip"),
            "no_rpm_effect": (
                "The fan curve was applied but the fan did not respond.",
                "CriticalChip",
            ),
            "pwm_enable_reverted": (
                "The BIOS/EC reclaimed GPU fan control during the test.",
                "CriticalChip",
            ),
            "write_failed": (
                "The GPU fan write was rejected by the driver/firmware.",
                "CriticalChip",
            ),
        }
        summary, css_class = summary_map.get(
            result.result, (f"GPU verify: {result.result}", "CardMeta")
        )
        lines = [f"Result: {summary}"]
        init, final = result.initial_state, result.final_state
        if init.rpm is not None and final.rpm is not None:
            lines.append(f"RPM: {init.rpm} → {final.rpm}")
        if result.test_speed_pct:
            lines.append(
                f"Test: drove the fan to {result.test_speed_pct}%, waited {result.wait_seconds}s"
            )
        for prob in gpu_verify_problems(result):
            lines.append(f"• To fix: {prob['fix']}")
        if result.restore_failed:
            lines.append("Note: the GPU fan could not be restored — set it manually if needed.")
        self._gpu_verify_result_label.setText("\n".join(lines))
        set_chip_class(self._gpu_verify_result_label, css_class)
        self._gpu_verify_result_label.setVisible(True)

    def _active_profile_controls_gpu(self) -> bool:
        ps = self._profile_service
        profile = ps.active_profile if ps is not None else None
        if profile is None:
            return False
        return any(
            member.target_id.startswith("amd_gpu:")
            for control in profile.controls
            for member in control.members
        )

    def _update_gpu_restore_gate(self, _profile_name: str | None = None) -> None:
        managed = self._active_profile_controls_gpu()
        self._gpu_restore_btn.setEnabled(not managed)
        self._gpu_restore_btn.setToolTip(
            _GPU_RESTORE_TOOLTIP_GATED if managed else _GPU_RESTORE_TOOLTIP_READY
        )

    def _run_gpu_restore(self) -> None:
        bdf = self._gpu_verify_bdf
        if not bdf:
            self._show_gpu_restore_message("No GPU with a writable fan-control path.")
            return
        if not self._client:
            self._show_gpu_restore_message("Cannot restore: no daemon connection")
            return
        if self._active_profile_controls_gpu():
            self._update_gpu_restore_gate()
            self._show_gpu_restore_message(f"Not restored: {_GPU_RESTORE_TOOLTIP_GATED}")
            return
        self._gpu_restore_btn.setEnabled(False)
        self._gpu_restore_btn.setText("Restoring...")
        self._gpu_restore_result_label.setVisible(False)
        if not self._ensure_gpu_verify_worker():
            self._show_gpu_restore_message("GPU restore unavailable: no daemon socket path")
            self._finish_gpu_restore()
            return
        self._gpu_reset_request.emit(bdf)

    def _show_gpu_restore_message(self, text: str, css_class: str = "CardMeta") -> None:
        self._gpu_restore_result_label.setText(text)
        set_chip_class(self._gpu_restore_result_label, css_class)
        self._gpu_restore_result_label.setVisible(True)

    def _finish_gpu_restore(self) -> None:
        self._gpu_restore_btn.setEnabled(True)
        self._gpu_restore_btn.setText("Restore GPU Fan to Automatic")
        self._update_gpu_restore_gate()

    @Slot(object)
    def _on_gpu_restore_ok(self, result: GpuFanResetResult) -> None:
        if result.reset:
            self._show_gpu_restore_message(
                "GPU fan restored to automatic — the firmware's default curve is back in control.",
                "SuccessChip",
            )
            self._diag.log_event("info", "gpu", "GPU fan restored to automatic (user action)")
        else:
            self._show_gpu_restore_message(
                "The daemon reported no restore was performed.", "WarningChip"
            )
            self._diag.log_event("warning", "gpu", "GPU fan restore: daemon reported no-op")
        self._finish_gpu_restore()

    @Slot(str, str)
    def _on_gpu_restore_error(self, category: str, message: str) -> None:
        if category == "unavailable":
            self._show_gpu_restore_message(
                message or "Daemon unavailable during GPU restore", "CriticalChip"
            )
        else:
            self._show_gpu_restore_message(f"GPU restore error: {message}", "CriticalChip")
        self._diag.log_event("error", "gpu", f"GPU fan restore failed: {message}")
        self._finish_gpu_restore()

    # ── Open Full Report + theme ─────────────────────────────────────

    def _open_readiness_report(self) -> None:
        diag = self._diag.last_hw_diagnostics
        if diag is None:
            return
        html = build_readiness_report_html(diag)
        if self._report_dialog is None:
            self._report_dialog = ReadinessReportDialog(html, self)
        else:
            self._report_dialog.set_html(html)
        self._report_dialog.show()
        self._report_dialog.raise_()
        self._report_dialog.activateWindow()

    def set_theme(self, _tokens) -> None:
        cached = self._diag.last_hw_diagnostics
        if cached is not None:
            self._render(cached)


# ── Module helpers ───────────────────────────────────────────────────


def _severity_border_color(state: str, theme) -> str:
    if state == "crit":
        return theme.status_crit
    if state == "warn":
        return theme.status_warn
    return theme.text_muted


def _row_state_color(state: str, theme) -> str:
    return {
        "ok": theme.status_ok,
        "warn": theme.status_warn,
        "crit": theme.status_crit,
    }.get(state, theme.text_primary)


def _mono(widget) -> None:
    font = widget.font()
    font.setFamily("monospace")
    widget.setFont(font)


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        else:
            child = item.layout()
            if child is not None:
                _clear_layout(child)


def _set_pill(table: QTableWidget, row: int, col: int, text: str, state: str) -> None:
    pill = StatusPill(text, state)
    pill.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    holder = QWidget()
    holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(6, 2, 6, 2)
    lay.setSpacing(0)
    lay.addWidget(pill)
    lay.addStretch(1)
    table.setCellWidget(row, col, holder)


def _ensure_items(table: QTableWidget, row: int, ncols: int) -> None:
    for col in range(ncols):
        if table.item(row, col) is None:
            table.setItem(row, col, QTableWidgetItem())
