"""Hardware page — migrated Diagnostics ▸ Readiness (DEC-212).

A thin renderer over the Qt-free ``services.hardware_view`` view-models (which
reuse the existing ``ui/cooling_readiness`` mapping), styled with the Stage-1
components. Presents the readiness checklist (verdict + grouped PASS/WARN rows —
no fabricated score), the recommended-action cards (with routed action buttons),
and the Super-I/O table (real per-chip columns only, with a per-chip "How to
enable" + copy-command) + the opt-in port probe.

Owns its own ``_HardwareReadinessWorker`` (fetch/refresh/probe) — the same
``_SocketWorker`` class the old tab uses. Presentation-only: no daemon/API/schema/
control/safety change. Action deep-links are re-pointed to the already-migrated
pages (System State / Overview / Settings). The old ``CoolingReadinessView`` +
Diagnostics Readiness tab are left untouched.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QMessageBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.hardware_view import (
    build_checklist,
    build_readiness_summary,
    build_recommended_actions,
    build_superio_panel,
)
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.cooling_readiness import build_readiness_items
from control_ofc.ui.pages.diagnostics_workers import _HardwareReadinessWorker
from control_ofc.ui.readiness_merge import ACTION_NONE
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

if TYPE_CHECKING:
    from collections.abc import Callable

    from control_ofc.api.client import DaemonClient
    from control_ofc.api.models import HardwareReadiness, SuperIoReport
    from control_ofc.services.app_state import AppState

log = logging.getLogger(__name__)

_SUPERIO_COLS = ["Chip", "Vendor", "Driver", "Module loaded", "Confidence", "Health", "Notes"]
_SIO_HEALTH = 5


class HardwarePage(QWidget):
    """The migrated Cooling Hardware Readiness content as a standalone page."""

    # Cross-page deep-links (routed by main_window).
    open_preferred_sensors = Signal(str)  # "cpu" | "mb"
    open_system_state = Signal()  # pwm_verify
    open_overview = Signal()  # sensors

    # Main-thread → worker-thread requests (queued).
    _readiness_request = Signal()
    _readiness_refresh_request = Signal()
    _readiness_probe_request = Signal()

    def __init__(
        self,
        state: AppState | None = None,
        diagnostics_service: DiagnosticsService | None = None,
        client: DaemonClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Hardware_Root")
        self._state = state
        self._diag = diagnostics_service or DiagnosticsService(state)
        self._client = client

        self._readiness_thread: QThread | None = None
        self._readiness_worker: _HardwareReadinessWorker | None = None
        self._readiness_auto_fetched = False
        self._readiness_unsupported = False
        self._last_report: HardwareReadiness | None = None

        self._build_ui()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        outer.addWidget(self._scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self._scroll.setWidget(body)

        # Header.
        header_row = QHBoxLayout()
        head = QVBoxLayout()
        title = QLabel("Hardware Readiness")
        title.setObjectName("Hardware_Label_title")
        title.setProperty("class", "PageTitle")
        head.addWidget(title)
        subtitle = QLabel(
            "Pre-flight hardware verification · driver mapping · Super-I/O architecture"
        )
        subtitle.setObjectName("Hardware_Label_subtitle")
        subtitle.setProperty("class", "PageSubtitle")
        head.addWidget(subtitle)
        header_row.addLayout(head)
        header_row.addStretch(1)
        self._refresh_btn = make_button("Re-scan", "secondary", object_name="Hardware_Btn_refresh")
        self._refresh_btn.clicked.connect(self._refresh_readiness)
        header_row.addWidget(self._refresh_btn)
        layout.addLayout(header_row)

        self._status_label = QLabel("")
        self._status_label.setObjectName("Hardware_Label_status")
        self._status_label.setProperty("class", "CardMeta")
        self._status_label.setWordWrap(True)
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        # Hero grid.
        hero = QHBoxLayout()
        hero.setSpacing(12)
        hero.addWidget(self._build_checklist_card(), 1)
        hero.addWidget(self._build_actions_card(), 1)
        layout.addLayout(hero)

        # Super-I/O.
        layout.addWidget(self._build_superio_card())
        layout.addStretch(1)

    def _build_checklist_card(self) -> QWidget:
        card = Card()
        card.setObjectName("Hardware_Card_checklist")
        v = QVBoxLayout(card)
        header = SectionHeader(
            "System Readiness Checklist", object_name="Hardware_SectionHeader_checklist"
        )
        self._verdict_pill = StatusPill("—", "neutral")
        self._verdict_pill.setObjectName("Hardware_Pill_verdict")
        header.add_trailing(self._verdict_pill)
        v.addWidget(header)

        self._top_label = QLabel("")
        self._top_label.setObjectName("Hardware_Label_topStep")
        self._top_label.setProperty("class", "CardMeta")
        self._top_label.setWordWrap(True)
        self._top_label.setVisible(False)
        v.addWidget(self._top_label)

        self._checklist_container = QWidget()
        self._checklist_layout = QVBoxLayout(self._checklist_container)
        self._checklist_layout.setContentsMargins(0, 0, 0, 0)
        self._checklist_layout.setSpacing(6)
        v.addWidget(self._checklist_container)

        self._scanned_label = QLabel("")
        self._scanned_label.setObjectName("Hardware_Label_scanned")
        self._scanned_label.setProperty("class", "SmallLabel")
        self._scanned_label.setWordWrap(True)
        v.addWidget(self._scanned_label)
        v.addStretch(1)
        return card

    def _build_actions_card(self) -> QWidget:
        card = Card()
        card.setObjectName("Hardware_Card_actions")
        v = QVBoxLayout(card)
        header = SectionHeader("Recommended Actions", object_name="Hardware_SectionHeader_actions")
        self._action_count_label = QLabel("—")
        self._action_count_label.setObjectName("Hardware_Label_actionCount")
        self._action_count_label.setProperty("class", "CardMeta")
        header.add_trailing(self._action_count_label)
        v.addWidget(header)

        self._actions_container = QWidget()
        self._actions_layout = QVBoxLayout(self._actions_container)
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(8)
        v.addWidget(self._actions_container)

        self._summary_bar = QWidget()
        self._summary_bar.setObjectName("Hardware_Bar_summary")
        self._summary_bar_layout = QHBoxLayout(self._summary_bar)
        self._summary_bar_layout.setContentsMargins(0, 6, 0, 0)
        self._summary_bar_layout.setSpacing(8)
        v.addWidget(self._summary_bar)
        v.addStretch(1)
        return card

    def _build_superio_card(self) -> QWidget:
        card = Card()
        card.setObjectName("Hardware_Card_superio")
        self._superio_card = card
        v = QVBoxLayout(card)
        v.addWidget(
            SectionHeader("Super-I/O Architecture", object_name="Hardware_SectionHeader_superio")
        )
        self._superio_container = QWidget()
        self._superio_layout = QVBoxLayout(self._superio_container)
        self._superio_layout.setContentsMargins(0, 0, 0, 0)
        self._superio_layout.setSpacing(8)
        v.addWidget(self._superio_container)
        return card

    # ── Fetch + render ───────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._readiness_unsupported and not self._readiness_auto_fetched:
            self._readiness_auto_fetched = True  # latch BEFORE emit → no double-fetch
            self._fetch_readiness()

    def _fetch_readiness(self, *, force: bool = False) -> None:
        if self._readiness_unsupported:
            self._set_status(
                "Hardware readiness is unavailable — the daemon predates this feature."
            )
            return
        if not self._client:
            self._set_status("Cannot fetch readiness: no daemon connection")
            return
        if not self._ensure_readiness_worker():
            self._set_status("Cannot fetch readiness: no daemon socket path")
            return
        self._set_status(
            "Refreshing hardware assessment…" if force else "Fetching hardware readiness…"
        )
        (self._readiness_refresh_request if force else self._readiness_request).emit()

    def _refresh_readiness(self) -> None:
        self._fetch_readiness(force=True)

    @Slot(object)
    def _on_readiness_ok(self, result: HardwareReadiness) -> None:
        self._last_report = result
        self._status_label.setVisible(False)
        self._render(result)

    @Slot(str, str)
    def _on_readiness_error(self, category: str, message: str) -> None:
        if category == "unsupported":
            self._readiness_unsupported = True
            self._set_status(
                "Hardware readiness is unavailable — the daemon predates this feature."
            )
        else:
            self._set_status(f"Cannot fetch readiness: {message}")

    @Slot(object)
    def _on_readiness_probe_ok(self, result: SuperIoReport) -> None:
        # The probe enriches only the Super-I/O half — re-render that section.
        if self._last_report is not None:
            self._last_report.superio = result
        self._render_superio(build_superio_panel(result))

    @Slot(str, str)
    def _on_readiness_probe_error(self, _category: str, message: str) -> None:
        self._set_status(message)

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setVisible(True)

    def _render(self, hw: HardwareReadiness) -> None:
        self._last_report = hw
        summary = build_readiness_summary(hw)
        items = build_readiness_items(hw)
        self._verdict_pill.set_text(summary.verdict_word)
        self._verdict_pill.set_state(summary.verdict_state)
        self._top_label.setText(summary.top_summary_line)
        self._top_label.setVisible(bool(summary.top_summary_line))
        scanned = summary.scanned_age_line
        if summary.partial_note:
            scanned = f"{summary.partial_note}  {scanned}"
        self._scanned_label.setText(scanned)
        self._render_checklist(build_checklist(items))
        self._render_actions(build_recommended_actions(items), summary)
        self._render_superio(build_superio_panel(hw.superio))

    def _render_checklist(self, groups) -> None:
        _clear_layout(self._checklist_layout)
        if not groups:
            empty = QLabel("No hardware checks reported.")
            empty.setObjectName("Hardware_Label_checksEmpty")
            empty.setProperty("class", "CardMeta")
            self._checklist_layout.addWidget(empty)
            return
        theme = active_theme()
        for group in groups:
            head = QLabel(group.name)
            head.setProperty("class", "SmallLabel")
            head.setStyleSheet(f"color: {theme.text_secondary}; font-weight: 600;")
            self._checklist_layout.addWidget(head)
            for row in group.rows:
                self._checklist_layout.addWidget(self._make_check_row(row))

    def _make_check_row(self, vm) -> QWidget:
        holder = QWidget()
        holder.setObjectName(f"Hardware_Check_{vm.code}")
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        line = QHBoxLayout()
        line.setSpacing(8)
        glyph = QLabel(vm.glyph)
        glyph.setStyleSheet(f"color: {_state_color(vm.severity_state, active_theme())};")
        line.addWidget(glyph)
        title = QLabel(vm.title)
        title.setWordWrap(True)
        line.addWidget(title, 1)
        pill = StatusPill(vm.badge_word, vm.severity_state)
        pill.setObjectName(f"Hardware_CheckBadge_{vm.code}")
        line.addWidget(pill)
        col.addLayout(line)
        if vm.expandable:
            section = CollapsibleSection(
                "Details", f"Hardware_CheckDetail_{vm.code}", expanded=False
            )
            detail = QLabel(vm.detail)
            detail.setProperty("class", "CardMeta")
            detail.setWordWrap(True)
            detail.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(detail)
            col.addWidget(section)
        return holder

    def _render_actions(self, actions, summary) -> None:
        self._action_count_label.setText(f"{len(actions)} items · {summary.crit_count} critical")
        _clear_layout(self._actions_layout)
        if not actions:
            ok = QLabel("No recommended actions — hardware looks ready.")
            ok.setObjectName("Hardware_Label_actionsEmpty")
            ok.setProperty("class", "CardMeta")
            self._actions_layout.addWidget(ok)
        else:
            for vm in actions:
                self._actions_layout.addWidget(self._make_action_card(vm))
        _clear_layout(self._summary_bar_layout)
        for seg in summary.segments:
            self._summary_bar_layout.addWidget(StatusPill(f"{seg.count} {seg.label}", seg.state))
        self._summary_bar_layout.addStretch(1)

    def _make_action_card(self, vm) -> QWidget:
        card = QFrame()
        card.setObjectName(f"Hardware_Action_{vm.code}")
        card.setProperty("class", "Card")
        col = QVBoxLayout(card)
        col.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        badge = StatusPill(vm.badge_word, vm.severity_state)
        badge.setObjectName(f"Hardware_Badge_{vm.code}")
        top.addWidget(badge)
        headline = QLabel(vm.headline)
        headline.setObjectName(f"Hardware_Headline_{vm.code}")
        headline.setWordWrap(True)
        headline.setTextFormat(Qt.TextFormat.PlainText)
        headline.setStyleSheet("font-weight: 600;")
        top.addWidget(headline, 1)
        if vm.component:
            comp = QLabel(vm.component)
            comp.setProperty("class", "SmallLabel")
            top.addWidget(comp)
        col.addLayout(top)

        if vm.impact_chips:
            chip_row = QHBoxLayout()
            chip_row.setSpacing(6)
            for chip in vm.impact_chips:
                chip_row.addWidget(StatusPill(chip.label, chip.state))
            chip_row.addStretch(1)
            col.addLayout(chip_row)

        if vm.plain_detail:
            detail = QLabel(vm.plain_detail)
            detail.setProperty("class", "CardMeta")
            detail.setWordWrap(True)
            detail.setTextFormat(Qt.TextFormat.PlainText)
            col.addWidget(detail)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        if vm.action_kind != ACTION_NONE and vm.action_label:
            act = make_button(vm.action_label, "secondary", object_name=f"Hardware_Do_{vm.code}")
            act.clicked.connect(lambda _=False, t=vm.action_target: self._route_action(t))
            btn_row.addWidget(act)
        if vm.doc_url:
            doc = make_button(
                f"{vm.doc_title or 'Learn how'} ↗", "ghost", object_name=f"Hardware_Doc_{vm.code}"
            )
            doc.clicked.connect(lambda _=False, url=vm.doc_url: QDesktopServices.openUrl(QUrl(url)))
            btn_row.addWidget(doc)
        btn_row.addStretch(1)
        col.addLayout(btn_row)
        return card

    def _render_superio(self, panel) -> None:
        _clear_layout(self._superio_layout)
        if not panel.arch_supported:
            self._superio_layout.addWidget(
                _note_label(panel.arch_note, "Hardware_Label_superioNote")
            )
            return
        if not panel.has_chips:
            self._superio_layout.addWidget(
                _note_label(panel.empty_note, "Hardware_Label_superioNote")
            )
            if panel.notes_text:
                self._superio_layout.addWidget(
                    _note_label(panel.notes_text, "Hardware_Label_superioNotes")
                )
            self._superio_layout.addWidget(self._build_advanced(panel))
            return

        summary_pill = StatusPill(panel.summary_text, panel.summary_state)
        summary_pill.setObjectName("Hardware_Pill_superioSummary")
        holder = QWidget()
        holder_l = QHBoxLayout(holder)
        holder_l.setContentsMargins(0, 0, 0, 0)
        holder_l.addWidget(summary_pill)
        holder_l.addStretch(1)
        self._superio_layout.addWidget(holder)

        table = QTableWidget(len(panel.rows), len(_SUPERIO_COLS))
        table.setObjectName("Hardware_Table_superio")
        table.setHorizontalHeaderLabels(_SUPERIO_COLS)
        apply_dense_table(table)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Health holds a pill cell-widget: ResizeToContents measures the (empty)
        # item, not the widget, so pin a fixed width wide enough for the pill.
        header.setSectionResizeMode(_SIO_HEALTH, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(_SIO_HEALTH, 112)
        header.setStretchLastSection(True)
        for i, row in enumerate(panel.rows):
            _ensure_items(table, i, len(_SUPERIO_COLS))
            table.item(i, 0).setText(row.chip)
            table.item(i, 1).setText(row.vendor)
            table.item(i, 2).setText(row.driver_text)
            table.item(i, 3).setText(row.module_text)
            table.item(i, 4).setText(row.confidence)
            table.item(i, 6).setText(row.notes)
            _set_pill(table, i, _SIO_HEALTH, row.health_word, row.health_state)
        self._superio_layout.addWidget(table)

        for row in panel.rows:
            if row.has_recommendation:
                self._superio_layout.addWidget(self._make_chip_detail(row))

        if panel.show_liability:
            self._superio_layout.addWidget(
                _note_label(panel.liability_text, "Hardware_Label_superioLiability")
            )
        if panel.notes_text:
            self._superio_layout.addWidget(
                _note_label(panel.notes_text, "Hardware_Label_superioNotes")
            )
        self._superio_layout.addWidget(self._build_advanced(panel))

    def _make_chip_detail(self, row) -> QWidget:
        section = CollapsibleSection(
            f"How to enable — {row.chip}", f"Hardware_ChipHow_{row.chip}", expanded=False
        )
        if row.reason:
            reason = QLabel(row.reason)
            reason.setProperty("class", "CardMeta")
            reason.setWordWrap(True)
            reason.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(reason)
        if row.copy_command:
            cmd_holder = QWidget()
            cl = QHBoxLayout(cmd_holder)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(8)
            mono = QLabel(row.copy_command)
            mono.setProperty("class", "MonoCommand")
            mono.setTextFormat(Qt.TextFormat.PlainText)
            mono.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cl.addWidget(mono, 1)
            copy = make_button("Copy command", "ghost", object_name=f"Hardware_CmdCopy_{row.chip}")
            copy.clicked.connect(lambda _=False, c=row.copy_command: _copy_to_clipboard(c))
            cl.addWidget(copy)
            section.add_widget(cmd_holder)
        if row.mainline_text:
            ml = StatusPill(row.mainline_text, row.mainline_state)
            section.add_widget(ml)
        for note in row.risk_notes:
            rn = QLabel(f"⚠ {note}")
            rn.setProperty("class", "WarningChip")
            rn.setWordWrap(True)
            rn.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(rn)
        for cav in row.caveats:
            cv = QLabel(cav)
            cv.setProperty("class", "CardMeta")
            cv.setWordWrap(True)
            cv.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(cv)
        return section

    def _build_advanced(self, panel) -> QWidget:
        section = CollapsibleSection(
            "Advanced detection", "Hardware_Section_advanced", expanded=False
        )
        blurb = QLabel(
            "Active Super-I/O port probing reads hardware I/O ports directly. It is never "
            "run automatically — only when you request it below."
        )
        blurb.setProperty("class", "CardMeta")
        blurb.setWordWrap(True)
        section.add_widget(blurb)
        self._probe_btn = make_button(
            "Probe ports (advanced)", "secondary", object_name="Hardware_Btn_probe"
        )
        self._probe_btn.setEnabled(panel.probe_available)
        if not panel.probe_available and panel.probe_reason:
            self._probe_btn.setToolTip(panel.probe_reason)
        self._probe_btn.clicked.connect(self._confirm_probe)
        section.add_widget(self._probe_btn)
        return section

    # ── Actions / routing ────────────────────────────────────────────

    def _route_action(self, target: str) -> None:
        if target == "preferred_cpu":
            self.open_preferred_sensors.emit("cpu")
        elif target == "preferred_mb":
            self.open_preferred_sensors.emit("mb")
        elif target == "superio":
            self._scroll.ensureWidgetVisible(self._superio_card)
        elif target == "pwm_verify":
            self.open_system_state.emit()
        elif target == "sensors":
            self.open_overview.emit()

    def _confirm_probe(self) -> None:
        answer = QMessageBox.question(
            self,
            "Probe Super-I/O ports?",
            "Active port probing reads hardware I/O ports directly (needs elevated "
            "privileges). It is read-only but touches the hardware. Proceed?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run_readiness_probe()

    def _run_readiness_probe(self) -> None:
        if not self._client or not self._ensure_readiness_worker():
            return
        self._set_status("Probing Super-I/O ports…")
        self._readiness_probe_request.emit()

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

    def _ensure_readiness_worker(self) -> bool:
        def connect(w: _HardwareReadinessWorker) -> None:
            self._readiness_request.connect(w.do_fetch, Qt.ConnectionType.QueuedConnection)
            self._readiness_refresh_request.connect(
                w.do_refresh, Qt.ConnectionType.QueuedConnection
            )
            self._readiness_probe_request.connect(w.do_probe, Qt.ConnectionType.QueuedConnection)
            w.fetch_ok.connect(self._on_readiness_ok, Qt.ConnectionType.QueuedConnection)
            w.fetch_error.connect(self._on_readiness_error, Qt.ConnectionType.QueuedConnection)
            w.probe_ok.connect(self._on_readiness_probe_ok, Qt.ConnectionType.QueuedConnection)
            w.probe_error.connect(
                self._on_readiness_probe_error, Qt.ConnectionType.QueuedConnection
            )

        self._readiness_worker, self._readiness_thread, ok = self._ensure_worker(
            self._readiness_worker, self._readiness_thread, _HardwareReadinessWorker, connect
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
        self._teardown_worker(self._readiness_worker, self._readiness_thread, "Readiness")
        self._readiness_worker = None
        self._readiness_thread = None

    def set_theme(self, _tokens) -> None:
        if self._last_report is not None:
            self._render(self._last_report)


# ── Module helpers ───────────────────────────────────────────────────


def _state_color(state: str, theme) -> str:
    return {
        "ok": theme.status_ok,
        "warn": theme.status_warn,
        "crit": theme.status_crit,
        "info": theme.status_info,
    }.get(state, theme.text_secondary)


def _note_label(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setProperty("class", "CardMeta")
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _copy_to_clipboard(text: str) -> None:
    clip = QApplication.clipboard()
    if clip is not None:
        clip.setText(text)


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
