"""Diagnostics page — daemon health, sensor/fan status, lease, logs, support export."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import Capabilities, DaemonStatus, Freshness, LeaseState
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService

_TRANSPARENT = "background: transparent;"


def _transparent_label(text: str, object_name: str, *, bold: bool = False) -> QLabel:
    """Create a QLabel with transparent background, suitable for use inside Card frames."""
    label = QLabel(text)
    label.setObjectName(object_name)
    style = _TRANSPARENT
    if bold:
        style += " font-weight: bold;"
    label.setStyleSheet(style)
    return label


class DiagnosticsPage(QWidget):
    """System health, device discovery, lease state, logs, and support bundle export."""

    def __init__(
        self,
        state: AppState | None = None,
        diagnostics_service: DiagnosticsService | None = None,
        settings_service: object | None = None,
        profile_service: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._diag = diagnostics_service or DiagnosticsService(
            state, settings_service=settings_service, profile_service=profile_service
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(8)

        title = QLabel("Diagnostics")
        title.setProperty("class", "PageTitle")
        layout.addWidget(title)

        subtitle = QLabel("Daemon health, device status, logs, and support tools")
        subtitle.setProperty("class", "PageSubtitle")
        layout.addWidget(subtitle)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_overview_tab(), "Overview")
        self._tabs.addTab(self._build_sensors_tab(), "Sensors")
        self._tabs.addTab(self._build_fans_tab(), "Fans")
        self._tabs.addTab(self._build_lease_tab(), "Lease")
        self._tabs.addTab(self._build_logs_tab(), "Event Log")
        layout.addWidget(self._tabs, 1)

        # Action buttons
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("Diagnostics_Btn_refreshOverview")
        refresh_btn.clicked.connect(self._refresh_all)
        btn_row.addWidget(refresh_btn)

        export_btn = QPushButton("Export Support Bundle...")
        export_btn.setObjectName("Diagnostics_Btn_export")
        export_btn.setToolTip(
            "Includes system configuration and daemon logs. Review before sharing."
        )
        export_btn.clicked.connect(self._export_bundle)
        btn_row.addWidget(export_btn)

        btn_row.addStretch()
        self._status_label = QLabel("")
        self._status_label.setProperty("class", "CardMeta")
        btn_row.addWidget(self._status_label)
        layout.addLayout(btn_row)

        # Wire signals
        if self._state:
            self._state.capabilities_updated.connect(self._on_capabilities)
            self._state.status_updated.connect(self._on_status)
            self._state.sensors_updated.connect(self._on_sensors)
            self._state.fans_updated.connect(self._on_fans)
            self._state.lease_updated.connect(self._on_lease)

    # ─── Tab builders ────────────────────────────────────────────────

    def _build_overview_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(12)

        # Daemon health card
        daemon_frame = QFrame()
        daemon_frame.setProperty("class", "Card")
        daemon_layout = QVBoxLayout(daemon_frame)

        self._daemon_version_label = _transparent_label(
            "Daemon: \u2014", "Diagnostics_Label_daemonVersion", bold=True
        )
        self._daemon_version_label.setProperty("class", "PageSubtitle")
        daemon_layout.addWidget(self._daemon_version_label)

        self._daemon_status_label = _transparent_label(
            "Status: \u2014", "Diagnostics_Label_daemonStatus"
        )
        daemon_layout.addWidget(self._daemon_status_label)

        self._daemon_uptime_label = _transparent_label(
            "Uptime: \u2014", "Diagnostics_Label_daemonUptime"
        )
        self._daemon_uptime_label.setProperty("class", "CardMeta")
        daemon_layout.addWidget(self._daemon_uptime_label)

        self._subsystems_label = _transparent_label(
            "Subsystems: \u2014", "Diagnostics_Label_subsystems"
        )
        self._subsystems_label.setWordWrap(True)
        daemon_layout.addWidget(self._subsystems_label)

        # Age explanation note
        age_note = _transparent_label(
            "Age = time since daemon last polled this hardware subsystem",
            "Diagnostics_Label_ageNote",
        )
        age_note.setProperty("class", "CardMeta")
        daemon_layout.addWidget(age_note)

        layout.addWidget(daemon_frame)

        # Device discovery card
        device_frame = QFrame()
        device_frame.setProperty("class", "Card")
        device_layout = QVBoxLayout(device_frame)

        device_title = _transparent_label(
            "Device Discovery", "Diagnostics_Label_deviceTitle", bold=True
        )
        device_title.setProperty("class", "PageSubtitle")
        device_layout.addWidget(device_title)

        self._openfan_label = _transparent_label("OpenFan: \u2014", "Diagnostics_Label_openfan")
        device_layout.addWidget(self._openfan_label)

        self._hwmon_label = _transparent_label("hwmon: \u2014", "Diagnostics_Label_hwmon")
        device_layout.addWidget(self._hwmon_label)

        self._amd_gpu_label = _transparent_label("AMD GPU: \u2014", "Diagnostics_Label_amdGpu")
        device_layout.addWidget(self._amd_gpu_label)

        self._features_label = _transparent_label("Features: \u2014", "Diagnostics_Label_features")
        self._features_label.setWordWrap(True)
        device_layout.addWidget(self._features_label)

        layout.addWidget(device_frame)
        layout.addStretch()
        scroll.setWidget(container)
        return scroll

    def _build_sensors_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        self._sensor_table = QTableWidget(0, 5)
        self._sensor_table.setObjectName("Diagnostics_Table_sensors")
        self._sensor_table.setHorizontalHeaderLabels(
            ["Label", "Kind", "Value (\u00b0C)", "Age (ms)", "Freshness"]
        )
        self._sensor_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._sensor_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._sensor_table)
        return container

    def _build_fans_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        self._fan_table = QTableWidget(0, 5)
        self._fan_table.setObjectName("Diagnostics_Table_fans")
        self._fan_table.setHorizontalHeaderLabels(["ID", "Source", "RPM", "PWM (%)", "Freshness"])
        self._fan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._fan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._fan_table)
        return container

    def _build_lease_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(12)

        # Explanation card
        explain_frame = QFrame()
        explain_frame.setProperty("class", "Card")
        explain_layout = QVBoxLayout(explain_frame)

        explain_title = _transparent_label(
            "What is a lease?", "Diagnostics_Label_leaseExplainTitle", bold=True
        )
        explain_title.setProperty("class", "PageSubtitle")
        explain_layout.addWidget(explain_title)

        explain_text = _transparent_label(
            "A lease grants exclusive write access to your motherboard\u2019s fan "
            "headers (hwmon). Only one client can hold the lease at a time, "
            "preventing conflicting PWM commands from different tools.\n\n"
            "The GUI automatically acquires and renews the lease while controlling "
            "fans. The lease expires after 60 seconds if not renewed (e.g. if the "
            "GUI crashes), allowing other tools to take over.\n\n"
            "If another tool holds the lease, the GUI cannot write PWM values "
            "until the lease is released or expires. OpenFan Controller writes "
            "do not require a lease \u2014 only motherboard hwmon writes do.",
            "Diagnostics_Label_leaseExplainText",
        )
        explain_text.setWordWrap(True)
        explain_text.setProperty("class", "CardMeta")
        explain_layout.addWidget(explain_text)

        layout.addWidget(explain_frame)

        # Status card
        frame = QFrame()
        frame.setProperty("class", "Card")
        frame_layout = QVBoxLayout(frame)

        self._lease_held_label = _transparent_label(
            "Lease: \u2014", "Diagnostics_Label_leaseHeld", bold=True
        )
        self._lease_held_label.setProperty("class", "PageSubtitle")
        frame_layout.addWidget(self._lease_held_label)

        self._lease_id_label = _transparent_label("Lease ID: \u2014", "Diagnostics_Label_leaseId")
        frame_layout.addWidget(self._lease_id_label)

        self._lease_owner_label = _transparent_label(
            "Owner: \u2014", "Diagnostics_Label_leaseOwner"
        )
        frame_layout.addWidget(self._lease_owner_label)

        self._lease_ttl_label = _transparent_label(
            "TTL remaining: \u2014", "Diagnostics_Label_leaseTtl"
        )
        frame_layout.addWidget(self._lease_ttl_label)

        self._lease_required_label = _transparent_label(
            "Required: \u2014", "Diagnostics_Label_leaseRequired"
        )
        frame_layout.addWidget(self._lease_required_label)

        layout.addWidget(frame)
        layout.addStretch()
        scroll.setWidget(container)
        return scroll

    def _build_logs_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        # Category buttons — fetch detail on demand
        cat_row = QHBoxLayout()

        daemon_btn = QPushButton("Daemon Status")
        daemon_btn.setObjectName("Diagnostics_Btn_daemonStatus")
        daemon_btn.setToolTip("Fetch current daemon health and subsystem status")
        daemon_btn.clicked.connect(self._fetch_daemon_status)
        cat_row.addWidget(daemon_btn)

        controller_btn = QPushButton("Controller Status")
        controller_btn.setObjectName("Diagnostics_Btn_controllerStatus")
        controller_btn.setToolTip("Fetch OpenFan controller detection and capabilities")
        controller_btn.clicked.connect(self._fetch_controller_status)
        cat_row.addWidget(controller_btn)

        gpu_btn = QPushButton("GPU Status")
        gpu_btn.setObjectName("Diagnostics_Btn_gpuStatus")
        gpu_btn.setToolTip("Fetch AMD GPU detection, fan capabilities, and fan state")
        gpu_btn.clicked.connect(self._fetch_gpu_status)
        cat_row.addWidget(gpu_btn)

        journal_btn = QPushButton("System Journal")
        journal_btn.setObjectName("Diagnostics_Btn_systemJournal")
        journal_btn.setToolTip("Fetch recent control-ofc-daemon.service entries from journalctl")
        journal_btn.clicked.connect(self._fetch_journal)
        cat_row.addWidget(journal_btn)

        cat_row.addStretch()
        layout.addLayout(cat_row)

        # Standard log controls
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh Log")
        refresh_btn.setObjectName("Diagnostics_Btn_refreshLogs")
        refresh_btn.clicked.connect(self._refresh_log)
        btn_row.addWidget(refresh_btn)

        clear_btn = QPushButton("Clear Log")
        clear_btn.setObjectName("Diagnostics_Btn_clearLogs")
        clear_btn.clicked.connect(self._clear_log)
        btn_row.addWidget(clear_btn)

        clear_warn_btn = QPushButton("Clear Warnings")
        clear_warn_btn.setObjectName("Diagnostics_Btn_clearWarnings")
        clear_warn_btn.clicked.connect(self._clear_warnings)
        btn_row.addWidget(clear_warn_btn)

        copy_errors_btn = QPushButton("Copy Last Errors")
        copy_errors_btn.setObjectName("Diagnostics_Btn_copyErrors")
        copy_errors_btn.setToolTip(
            "Copy recent errors and warnings to clipboard (GPU, hwmon, serial, control loop)"
        )
        copy_errors_btn.clicked.connect(self._copy_last_errors)
        btn_row.addWidget(copy_errors_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("Diagnostics_Text_logView")
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        font = self._log_view.font()
        font.setFamily("monospace")
        self._log_view.setFont(font)
        layout.addWidget(self._log_view, 1)
        return container

    # ─── Signal handlers ─────────────────────────────────────────────

    def _on_capabilities(self, caps: Capabilities) -> None:
        self._daemon_version_label.setText(
            f"Daemon: v{caps.daemon_version} (API v{caps.api_version})"
        )

        of = caps.openfan
        of_status = f"Present ({of.channels} ch" if of.present else "Not present"
        if of.present:
            parts = []
            if of.write_support:
                parts.append("write")
            if of.rpm_support:
                parts.append("RPM")
            of_status += ", " + "+".join(parts) + ")" if parts else ")"
        self._openfan_label.setText(f"OpenFan: {of_status}")

        hw = caps.hwmon
        hw_status = f"Present ({hw.pwm_header_count} headers" if hw.present else "Not present"
        if hw.present:
            parts = []
            if hw.write_support:
                parts.append("write")
            if hw.lease_required:
                parts.append("lease required")
            hw_status += ", " + ", ".join(parts) + ")" if parts else ")"
        self._hwmon_label.setText(f"hwmon: {hw_status}")

        gpu = caps.amd_gpu
        if gpu.present:
            gpu_parts = [gpu.display_label]
            if gpu.pci_id:
                gpu_parts.append(f"PCI {gpu.pci_id}")
            gpu_parts.append(f"fan: {gpu.fan_control_method}")
            self._amd_gpu_label.setText(f"AMD GPU: {', '.join(gpu_parts)}")
        else:
            self._amd_gpu_label.setText("AMD GPU: Not detected")

        f = caps.features
        features = []
        if f.openfan_write_supported:
            features.append("OpenFan writes")
        if f.hwmon_write_supported:
            features.append("hwmon writes")
        self._features_label.setText(f"Features: {', '.join(features) or 'none'}")

    def _on_status(self, status: DaemonStatus) -> None:
        self._daemon_status_label.setText(f"Status: {status.overall_status}")

        if status.uptime_seconds is not None:
            mins, secs = divmod(status.uptime_seconds, 60)
            hrs, mins = divmod(mins, 60)
            if hrs:
                self._daemon_uptime_label.setText(f"Uptime: {hrs}h {mins}m {secs}s")
            elif mins:
                self._daemon_uptime_label.setText(f"Uptime: {mins}m {secs}s")
            else:
                self._daemon_uptime_label.setText(f"Uptime: {secs}s")
        else:
            self._daemon_uptime_label.setText("Uptime: \u2014")

        parts = []
        for s in status.subsystems:
            age = f" (age {s.age_ms}ms)" if s.age_ms is not None else ""
            reason = f" \u2014 {s.reason}" if s.reason else ""
            parts.append(f"{s.name}: {s.status}{age}{reason}")
        self._subsystems_label.setText(
            "Subsystems:\n" + "\n".join(parts) if parts else "Subsystems: \u2014"
        )

    def _on_sensors(self, sensors: list) -> None:
        prev_count = self._sensor_table.rowCount()
        if len(sensors) != prev_count:
            self._sensor_table.setRowCount(len(sensors))

        for i, s in enumerate(sensors):
            if i >= prev_count:
                for col in range(5):
                    self._sensor_table.setItem(i, col, QTableWidgetItem())

            self._sensor_table.item(i, 0).setText(s.label or s.id)
            self._sensor_table.item(i, 1).setText(s.kind)
            self._sensor_table.item(i, 2).setText(f"{s.value_c:.1f}")
            self._sensor_table.item(i, 3).setText(str(s.age_ms))

            freshness_item = self._sensor_table.item(i, 4)
            freshness_item.setText(s.freshness.value)
            if s.freshness == Freshness.STALE:
                freshness_item.setForeground(Qt.GlobalColor.yellow)
            elif s.freshness == Freshness.INVALID:
                freshness_item.setForeground(Qt.GlobalColor.red)
            else:
                freshness_item.setForeground(Qt.GlobalColor.white)

    def _on_fans(self, fans: list) -> None:
        fan_ids = {f.id for f in fans}
        pwm_only = []
        if self._state:
            pwm_only = [h for h in self._state.hwmon_headers if h.id not in fan_ids]

        total = len(fans) + len(pwm_only)
        prev_count = self._fan_table.rowCount()
        if total != prev_count:
            self._fan_table.setRowCount(total)

        row = 0
        for f in fans:
            if row >= prev_count:
                for col in range(5):
                    self._fan_table.setItem(row, col, QTableWidgetItem())

            display_name = f.id
            if self._state:
                display_name = self._state.fan_display_name(f.id)
            self._fan_table.item(row, 0).setText(display_name)
            self._fan_table.item(row, 1).setText(f.source)
            self._fan_table.item(row, 2).setText(str(f.rpm) if f.rpm is not None else "\u2014")
            self._fan_table.item(row, 3).setText(
                str(f.last_commanded_pwm) if f.last_commanded_pwm is not None else "\u2014"
            )
            freshness_item = self._fan_table.item(row, 4)
            freshness_item.setText(f.freshness.value)
            if f.freshness == Freshness.STALE:
                freshness_item.setForeground(Qt.GlobalColor.yellow)
            elif f.freshness == Freshness.INVALID:
                freshness_item.setForeground(Qt.GlobalColor.red)
            else:
                freshness_item.setForeground(Qt.GlobalColor.white)
            row += 1

        for h in pwm_only:
            if row >= prev_count:
                for col in range(5):
                    self._fan_table.setItem(row, col, QTableWidgetItem())

            self._fan_table.item(row, 0).setText(h.label or h.id)
            self._fan_table.item(row, 1).setText("hwmon (PWM-only)")
            self._fan_table.item(row, 2).setText("\u2014")
            self._fan_table.item(row, 3).setText("\u2014")
            self._fan_table.item(row, 4).setText("N/A")
            row += 1

    def _on_lease(self, lease: LeaseState) -> None:
        held_text = "Held" if lease.held else "Not held"
        self._lease_held_label.setText(f"Lease: {held_text}")
        self._lease_id_label.setText(f"Lease ID: {lease.lease_id or '\u2014'}")
        self._lease_owner_label.setText(f"Owner: {lease.owner_hint or '\u2014'}")
        ttl = f"{lease.ttl_seconds_remaining}s" if lease.ttl_seconds_remaining else "\u2014"
        self._lease_ttl_label.setText(f"TTL remaining: {ttl}")
        self._lease_required_label.setText(f"Required: {'Yes' if lease.lease_required else 'No'}")

    # ─── Actions ─────────────────────────────────────────────────────

    def _refresh_all(self) -> None:
        """Re-populate all tabs from current state."""
        if not self._state:
            return
        if self._state.capabilities:
            self._on_capabilities(self._state.capabilities)
        if self._state.daemon_status:
            self._on_status(self._state.daemon_status)
        self._on_sensors(self._state.sensors)
        self._on_fans(self._state.fans)
        self._on_lease(self._state.lease)
        self._refresh_log()
        self._status_label.setText("Refreshed")

    def _refresh_log(self) -> None:
        lines = []
        for e in self._diag.events:
            lines.append(f"[{e.time_str}] [{e.level.upper():7s}] [{e.source}] {e.message}")
        self._log_view.setPlainText("\n".join(lines) if lines else "(no events)")

    def _clear_log(self) -> None:
        self._diag.clear_events()
        self._log_view.setPlainText("(cleared)")

    def _clear_warnings(self) -> None:
        if self._state:
            self._state.clear_warnings()

    def _copy_last_errors(self) -> None:
        """Copy recent errors/warnings from the diagnostics event log to clipboard."""
        from PySide6.QtWidgets import QApplication

        error_levels = {"error", "warning"}
        lines = []
        for e in self._diag.events:
            if e.level in error_levels:
                lines.append(f"[{e.time_str}] [{e.level.upper():7s}] [{e.source}] {e.message}")

        if lines:
            text = "\n".join(lines)
            QApplication.clipboard().setText(text)
            self._status_label.setText(f"Copied {len(lines)} error(s) to clipboard")
        else:
            self._status_label.setText("No recent errors to copy")

    def _fetch_daemon_status(self) -> None:
        """Fetch and display a daemon status snapshot in the log view."""
        text = self._diag.format_daemon_status()
        self._append_detail_block("DAEMON STATUS", text)

    def _fetch_controller_status(self) -> None:
        """Fetch and display controller detection info in the log view."""
        text = self._diag.format_controller_status()
        self._append_detail_block("CONTROLLER STATUS", text)

    def _fetch_gpu_status(self) -> None:
        """Fetch and display GPU detection and fan state in the log view."""
        text = self._diag.format_gpu_status()
        self._append_detail_block("GPU STATUS", text)

    def _fetch_journal(self) -> None:
        """Fetch and display recent journal entries in the log view."""
        text = self._diag.fetch_journal_entries()
        self._append_detail_block("SYSTEM JOURNAL", text)

    def _append_detail_block(self, source: str, text: str) -> None:
        """Append a labeled detail block to the log view."""
        import time

        timestamp = time.strftime("%H:%M:%S")
        separator = "\u2500" * 60
        block = f"\n{separator}\n[{timestamp}] [{source}]\n{separator}\n{text}\n"
        self._log_view.appendPlainText(block)
        # Scroll to bottom
        scrollbar = self._log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _export_bundle(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Support Bundle",
            "control_ofc_support_bundle.json",
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            self._diag.export_support_bundle(Path(path))
            self._status_label.setText(f"Bundle exported to {Path(path).name}")
        except PermissionError:
            self._status_label.setText("Export failed: permission denied")
        except Exception as e:
            msg = str(e)[:80] if len(str(e)) > 80 else str(e)
            self._status_label.setText(f"Export failed: {msg}")
