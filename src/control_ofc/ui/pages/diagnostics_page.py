"""Diagnostics page — daemon health, sensor/fan status, lease, logs, support export."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QColor

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.profile_service import ProfileService
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import (
    Capabilities,
    DaemonStatus,
    Freshness,
    HardwareDiagnosticsResult,
    HwmonVerifyResult,
    LeaseState,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService, format_uptime
from control_ofc.ui.hwmon_guidance import (
    detect_module_conflicts,
    format_driver_status,
    lookup_chip_guidance,
    lookup_vendor_quirks,
    verification_guidance,
)
from control_ofc.ui.theme import default_dark_theme

_TRANSPARENT = "background: transparent;"
_THEME = default_dark_theme()


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
        settings_service: AppSettingsService | None = None,
        profile_service: ProfileService | None = None,
        client: DaemonClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._client = client
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
        self._sensor_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._sensor_table.horizontalHeader().setStretchLastSection(True)
        self._sensor_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._sensor_table)
        return container

    def _build_fans_tab(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("Diagnostics_Splitter_fans")
        splitter.setChildrenCollapsible(False)

        # ─── Top pane: Hardware Readiness (scrollable) ────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        hw_container = QWidget()
        hw_container_layout = QVBoxLayout(hw_container)
        hw_container_layout.setSpacing(12)

        # Hardware Readiness card
        self._hw_ready_frame = QFrame()
        self._hw_ready_frame.setProperty("class", "Card")
        self._hw_ready_frame.setObjectName("Diagnostics_Frame_hwReadiness")
        hw_layout = QVBoxLayout(self._hw_ready_frame)

        hw_title = _transparent_label(
            "Hardware Readiness", "Diagnostics_Label_hwReadyTitle", bold=True
        )
        hw_title.setProperty("class", "PageSubtitle")
        hw_layout.addWidget(hw_title)

        self._hw_ready_summary = _transparent_label(
            "Fetching hardware diagnostics\u2026",
            "Diagnostics_Label_hwReadySummary",
        )
        self._hw_ready_summary.setWordWrap(True)
        hw_layout.addWidget(self._hw_ready_summary)

        # Board info
        self._board_info_label = _transparent_label("", "Diagnostics_Label_boardInfo")
        self._board_info_label.setWordWrap(True)
        self._board_info_label.setProperty("class", "CardMeta")
        self._board_info_label.setVisible(False)
        hw_layout.addWidget(self._board_info_label)

        # Vendor quirk alert
        self._vendor_quirk_label = _transparent_label("", "Diagnostics_Label_vendorQuirk")
        self._vendor_quirk_label.setWordWrap(True)
        self._vendor_quirk_label.setVisible(False)
        hw_layout.addWidget(self._vendor_quirk_label)

        # Chip/driver table
        self._chip_table = QTableWidget(0, 5)
        self._chip_table.setObjectName("Diagnostics_Table_chips")
        self._chip_table.setHorizontalHeaderLabels(
            ["Chip", "Driver", "Status", "Mainline", "Headers"]
        )
        self._chip_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._chip_table.horizontalHeader().setStretchLastSection(True)
        self._chip_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._chip_table.setMinimumHeight(80)

        _chip_header_tooltips = [
            "Super I/O or sensor chip model detected by the daemon",
            "Linux kernel driver expected for this chip",
            "Whether the driver is loaded and where it comes from",
            "Whether the driver is included in the mainline Linux kernel",
            "Number of PWM fan headers exposed by this chip",
        ]
        for col, tip in enumerate(_chip_header_tooltips):
            item = self._chip_table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tip)

        # Kernel modules table
        self._modules_table = QTableWidget(0, 3)
        self._modules_table.setObjectName("Diagnostics_Table_kernelModules")
        self._modules_table.setHorizontalHeaderLabels(["Module", "Loaded", "Mainline"])
        self._modules_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._modules_table.horizontalHeader().setStretchLastSection(True)
        self._modules_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._modules_table.setMinimumHeight(80)

        _mod_header_tooltips = [
            "Kernel module name (e.g. nct6775, it87)",
            "Whether the module is currently loaded in the running kernel",
            "Whether the module ships with the mainline Linux kernel",
        ]
        for col, tip in enumerate(_mod_header_tooltips):
            item = self._modules_table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tip)

        table_splitter = QSplitter(Qt.Orientation.Vertical)
        table_splitter.setObjectName("Diagnostics_Splitter_hwTables")
        table_splitter.setChildrenCollapsible(False)
        table_splitter.addWidget(self._chip_table)
        table_splitter.addWidget(self._modules_table)
        table_splitter.setStretchFactor(0, 1)
        table_splitter.setStretchFactor(1, 2)
        hw_layout.addWidget(table_splitter)

        # ACPI conflicts
        self._acpi_label = _transparent_label("", "Diagnostics_Label_acpiConflicts")
        self._acpi_label.setWordWrap(True)
        self._acpi_label.setVisible(False)
        hw_layout.addWidget(self._acpi_label)

        # Module conflicts
        self._module_conflict_label = _transparent_label("", "Diagnostics_Label_moduleConflicts")
        self._module_conflict_label.setWordWrap(True)
        self._module_conflict_label.setVisible(False)
        hw_layout.addWidget(self._module_conflict_label)

        # BIOS interference (revert counts)
        self._revert_label = _transparent_label("", "Diagnostics_Label_revertCounts")
        self._revert_label.setWordWrap(True)
        self._revert_label.setVisible(False)
        hw_layout.addWidget(self._revert_label)

        # Thermal safety
        self._thermal_label = _transparent_label("", "Diagnostics_Label_thermalSafety")
        self._thermal_label.setWordWrap(True)
        self._thermal_label.setProperty("class", "CardMeta")
        hw_layout.addWidget(self._thermal_label)

        # GPU diagnostics
        self._gpu_diag_label = _transparent_label("", "Diagnostics_Label_gpuDiag")
        self._gpu_diag_label.setWordWrap(True)
        self._gpu_diag_label.setVisible(False)
        hw_layout.addWidget(self._gpu_diag_label)

        # Guidance detail (rich text with clickable driver doc links)
        self._guidance_label = _transparent_label("", "Diagnostics_Label_guidance")
        self._guidance_label.setWordWrap(True)
        self._guidance_label.setTextFormat(Qt.TextFormat.RichText)
        self._guidance_label.setOpenExternalLinks(True)
        self._guidance_label.setProperty("class", "CardMeta")
        self._guidance_label.setVisible(False)
        hw_layout.addWidget(self._guidance_label)

        # Documentation reference link
        self._docs_link_label = _transparent_label("", "Diagnostics_Label_docsLink")
        self._docs_link_label.setWordWrap(True)
        self._docs_link_label.setTextFormat(Qt.TextFormat.RichText)
        self._docs_link_label.setOpenExternalLinks(True)
        self._docs_link_label.setProperty("class", "CardMeta")
        self._docs_link_label.setVisible(False)
        hw_layout.addWidget(self._docs_link_label)

        # PWM verify section
        verify_row = QHBoxLayout()
        self._verify_combo = QComboBox()
        self._verify_combo.setObjectName("Diagnostics_Combo_verifyHeader")
        self._verify_combo.setMinimumWidth(200)
        verify_row.addWidget(self._verify_combo)

        self._verify_btn = QPushButton("Test PWM Control")
        self._verify_btn.setObjectName("Diagnostics_Btn_verifyPwm")
        self._verify_btn.setToolTip(
            "Write a test PWM value and check if the BIOS overrides it (~3s)"
        )
        self._verify_btn.clicked.connect(self._run_pwm_verify)
        verify_row.addWidget(self._verify_btn)
        verify_row.addStretch()
        hw_layout.addLayout(verify_row)

        self._verify_result_label = _transparent_label("", "Diagnostics_Label_verifyResult")
        self._verify_result_label.setWordWrap(True)
        self._verify_result_label.setVisible(False)
        hw_layout.addWidget(self._verify_result_label)

        # Fetch button
        fetch_btn = QPushButton("Refresh Hardware Diagnostics")
        fetch_btn.setObjectName("Diagnostics_Btn_fetchHwDiag")
        fetch_btn.clicked.connect(self._fetch_hardware_diagnostics)
        hw_layout.addWidget(fetch_btn)

        hw_container_layout.addWidget(self._hw_ready_frame)
        scroll.setWidget(hw_container)
        splitter.addWidget(scroll)

        # ─── Bottom pane: Fan Status table ────────────────────────────
        fan_pane = QWidget()
        fan_pane_layout = QVBoxLayout(fan_pane)
        fan_pane_layout.setContentsMargins(0, 4, 0, 0)

        fan_label = _transparent_label("Fan Status", "Diagnostics_Label_fanTableTitle", bold=True)
        fan_label.setProperty("class", "PageSubtitle")
        fan_pane_layout.addWidget(fan_label)

        self._fan_table = QTableWidget(0, 5)
        self._fan_table.setObjectName("Diagnostics_Table_fans")
        self._fan_table.setHorizontalHeaderLabels(["ID", "Source", "RPM", "PWM (%)", "Freshness"])
        self._fan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._fan_table.horizontalHeader().setStretchLastSection(True)
        self._fan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        _fan_header_tooltips = [
            "Fan identifier — display name or hardware ID",
            "Hardware backend: openfan, hwmon, amd_gpu, or hwmon (PWM-only)",
            "Hardware-measured fan speed in RPM.\n'—' means no tachometer or fan stopped.",
            "Last PWM duty cycle commanded by the daemon (0-100%).\n'—' means no command sent yet.",
            "Data freshness: ok (<2 s), stale (2-5 s), invalid (>5 s or never updated)",
        ]
        for col, tip in enumerate(_fan_header_tooltips):
            item = self._fan_table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tip)

        fan_pane_layout.addWidget(self._fan_table, 1)

        splitter.addWidget(fan_pane)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        return splitter

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
            self._daemon_uptime_label.setText(f"Uptime: {format_uptime(status.uptime_seconds)}")
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
                freshness_item.setForeground(QColor(_THEME.status_warn))
            elif s.freshness == Freshness.INVALID:
                freshness_item.setForeground(QColor(_THEME.status_crit))
            else:
                freshness_item.setForeground(QColor(_THEME.text_primary))

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
                freshness_item.setForeground(QColor(_THEME.status_warn))
            elif f.freshness == Freshness.INVALID:
                freshness_item.setForeground(QColor(_THEME.status_crit))
            else:
                freshness_item.setForeground(QColor(_THEME.text_primary))

            tooltip = self._fan_row_tooltip(f)
            for col in range(5):
                self._fan_table.item(row, col).setToolTip(tooltip)
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

            pwm_tip = f"ID: {h.id}\nSource: hwmon (PWM output only — no RPM tachometer)"
            if h.label:
                pwm_tip += f"\nLabel: {h.label}"
            if h.chip_name:
                pwm_tip += f"\nChip: {h.chip_name}"
            if not h.is_writable:
                pwm_tip += "\nStatus: read-only"
            for col in range(5):
                self._fan_table.item(row, col).setToolTip(pwm_tip)
            row += 1

    def _fan_row_tooltip(self, fan) -> str:
        """Build a tooltip for a fan row with chip/driver context."""
        parts = [f"ID: {fan.id}", f"Source: {fan.source}"]
        if fan.age_ms is not None:
            parts.append(f"Data age: {fan.age_ms} ms")
        if self._state:
            if fan.source == "hwmon":
                header = next((h for h in self._state.hwmon_headers if h.id == fan.id), None)
                if header:
                    if header.chip_name:
                        parts.append(f"Chip: {header.chip_name}")
                    g = lookup_chip_guidance(header.chip_name) if header.chip_name else None
                    if g:
                        status = "mainline" if g.in_mainline else g.driver_package
                        parts.append(f"Driver: {g.driver_name} ({status})")
                    mode = {0: "DC", 1: "PWM"}.get(
                        header.pwm_mode if header.pwm_mode is not None else -1
                    )
                    if mode:
                        parts.append(f"Control mode: {mode}")
                    if not header.is_writable:
                        parts.append("Status: read-only")
            elif fan.source == "amd_gpu":
                caps = self._state.capabilities
                if caps and caps.amd_gpu.present:
                    gpu = caps.amd_gpu
                    parts.append(f"GPU: {gpu.display_label}")
                    parts.append(f"Fan control: {gpu.fan_control_method}")
                    if gpu.pci_id:
                        parts.append(f"PCI: {gpu.pci_id}")
        return "\n".join(parts)

    def _on_lease(self, lease: LeaseState) -> None:
        held_text = "Held" if lease.held else "Not held"
        self._lease_held_label.setText(f"Lease: {held_text}")
        self._lease_id_label.setText(f"Lease ID: {lease.lease_id or '\u2014'}")
        self._lease_owner_label.setText(f"Owner: {lease.owner_hint or '\u2014'}")
        ttl = f"{lease.ttl_seconds_remaining}s" if lease.ttl_seconds_remaining else "\u2014"
        self._lease_ttl_label.setText(f"TTL remaining: {ttl}")
        self._lease_required_label.setText(f"Required: {'Yes' if lease.lease_required else 'No'}")

    # ─── Hardware diagnostics ──────────────────────────────────────────

    def _fetch_hardware_diagnostics(self) -> None:
        """Fetch hardware diagnostics from daemon and populate the UI."""
        if not self._client:
            self._hw_ready_summary.setText("Cannot fetch: no daemon connection")
            return
        try:
            from control_ofc.api.errors import DaemonError, DaemonUnavailable

            result = self._client.hardware_diagnostics()
            self._diag.last_hw_diagnostics = result
            self._populate_hw_diagnostics(result)
            self._status_label.setText("Hardware diagnostics refreshed")
        except DaemonUnavailable:
            self._hw_ready_summary.setText("Daemon unavailable — cannot fetch diagnostics")
        except DaemonError as e:
            self._hw_ready_summary.setText(f"Diagnostics error: {e.message}")

    def _populate_hw_diagnostics(self, diag: HardwareDiagnosticsResult) -> None:
        """Populate hardware readiness UI from a diagnostics result."""
        hw = diag.hwmon

        # Board info
        board = diag.board
        if board.vendor or board.name:
            parts = []
            if board.vendor:
                parts.append(board.vendor)
            if board.name:
                parts.append(board.name)
            if board.bios_version:
                parts.append(f"BIOS {board.bios_version}")
            self._board_info_label.setText("Board: " + " — ".join(parts))
            self._board_info_label.setVisible(True)
        else:
            self._board_info_label.setVisible(False)

        # Vendor quirks
        all_quirks = []
        for chip in hw.chips_detected:
            all_quirks.extend(lookup_vendor_quirks(board.vendor, chip.chip_name))
        if all_quirks:
            quirk_lines: list[str] = []
            for q in all_quirks:
                quirk_lines.append(f"[{q.severity.upper()}] {q.summary}")
                for d in q.details:
                    quirk_lines.append(f"  • {d}")
            self._vendor_quirk_label.setText("\n".join(quirk_lines))
            has_critical = any(q.severity == "critical" for q in all_quirks)
            css = "CriticalChip" if has_critical else "WarningChip"
            self._vendor_quirk_label.setProperty("class", css)
            self._vendor_quirk_label.style().unpolish(self._vendor_quirk_label)
            self._vendor_quirk_label.style().polish(self._vendor_quirk_label)
            self._vendor_quirk_label.setVisible(True)
        else:
            self._vendor_quirk_label.setVisible(False)

        summary_parts = []
        summary_parts.append(
            f"{hw.total_headers} PWM header(s) detected, {hw.writable_headers} writable"
        )
        if hw.total_headers > 0 and hw.writable_headers == 0:
            summary_parts.append(
                "All headers are read-only. Check BIOS fan settings or driver status."
            )
        if len(hw.chips_detected) == 0:
            summary_parts.append(
                "No hwmon chips detected. Motherboard fan control may require "
                "a kernel driver — see the modules table below."
            )
        self._hw_ready_summary.setText("\n".join(summary_parts))

        # Chip table
        self._chip_table.setRowCount(len(hw.chips_detected))
        for i, chip in enumerate(hw.chips_detected):
            for col in range(5):
                if self._chip_table.item(i, col) is None:
                    self._chip_table.setItem(i, col, QTableWidgetItem())
            self._chip_table.item(i, 0).setText(chip.chip_name)
            self._chip_table.item(i, 1).setText(chip.expected_driver)

            loaded_modules = {m.name for m in diag.kernel_modules if m.loaded}
            driver_loaded = chip.expected_driver in loaded_modules
            status_text = format_driver_status(chip.chip_name, driver_loaded)
            self._chip_table.item(i, 2).setText(status_text)

            mainline_text = "Yes" if chip.in_mainline_kernel else "No (out-of-tree)"
            self._chip_table.item(i, 3).setText(mainline_text)
            self._chip_table.item(i, 4).setText(str(chip.header_count))

        # Kernel modules table
        modules = diag.kernel_modules
        self._modules_table.setRowCount(len(modules))
        for i, mod in enumerate(modules):
            for col in range(3):
                if self._modules_table.item(i, col) is None:
                    self._modules_table.setItem(i, col, QTableWidgetItem())
            self._modules_table.item(i, 0).setText(mod.name)
            self._modules_table.item(i, 1).setText("Loaded" if mod.loaded else "Not loaded")
            self._modules_table.item(i, 2).setText("Yes" if mod.in_mainline else "No")

        # ACPI conflicts
        if diag.acpi_conflicts:
            lines = ["ACPI I/O port conflicts detected:"]
            has_it87 = False
            for c in diag.acpi_conflicts:
                lines.append(
                    f"  {c.io_range} claimed by '{c.claimed_by}' "
                    f"— conflicts with {c.conflicts_with_driver}"
                )
                if c.conflicts_with_driver == "it87":
                    has_it87 = True
            if has_it87:
                lines.append(
                    "Tip (ITE chips): prefer driver-local 'ignore_resource_conflict=1' "
                    "(add 'options it87 ignore_resource_conflict=1' to "
                    "/etc/modprobe.d/it87.conf) over the system-wide "
                    "'acpi_enforce_resources=lax' kernel parameter."
                )
            else:
                lines.append(
                    "Tip: add 'acpi_enforce_resources=lax' to kernel parameters, "
                    "or disable ACPI hardware monitoring in BIOS."
                )
            self._acpi_label.setText("\n".join(lines))
            self._acpi_label.setProperty("class", "WarningChip")
            self._acpi_label.style().unpolish(self._acpi_label)
            self._acpi_label.style().polish(self._acpi_label)
            self._acpi_label.setVisible(True)
        else:
            self._acpi_label.setVisible(False)

        # Module conflicts
        loaded_names = [m.name for m in diag.kernel_modules if m.loaded]
        mod_conflicts = detect_module_conflicts(loaded_names)
        if mod_conflicts:
            lines = ["Driver module conflicts detected:"]
            for mc in mod_conflicts:
                lines.append(f"  {mc.module_a} + {mc.module_b}: {mc.explanation}")
            self._module_conflict_label.setText("\n".join(lines))
            self._module_conflict_label.setProperty("class", "CriticalChip")
            self._module_conflict_label.style().unpolish(self._module_conflict_label)
            self._module_conflict_label.style().polish(self._module_conflict_label)
            self._module_conflict_label.setVisible(True)
        else:
            self._module_conflict_label.setVisible(False)

        # BIOS interference (revert counts)
        reverts = hw.enable_revert_counts
        if reverts:
            lines = ["BIOS interference detected — the EC/BIOS reclaimed fan control:"]
            for header_id, count in reverts.items():
                lines.append(f"  {header_id}: {count} revert(s)")
            lines.append(
                "The daemon watchdog automatically re-enables manual mode when this happens."
            )
            self._revert_label.setText("\n".join(lines))
            self._revert_label.setProperty("class", "WarningChip")
            self._revert_label.style().unpolish(self._revert_label)
            self._revert_label.style().polish(self._revert_label)
            self._revert_label.setVisible(True)
        else:
            self._revert_label.setVisible(False)

        # Thermal safety
        ts = diag.thermal_safety
        thermal_text = (
            f"Thermal safety: {ts.state} | CPU sensor: "
            f"{'found' if ts.cpu_sensor_found else 'NOT found'} | "
            f"Emergency: {ts.emergency_threshold_c:.0f}\u00b0C | "
            f"Release: {ts.release_threshold_c:.0f}\u00b0C"
        )
        self._thermal_label.setText(thermal_text)

        # GPU diagnostics
        if diag.gpu:
            gpu = diag.gpu
            lines = [f"GPU: {gpu.model_name or 'AMD D-GPU'} (PCI {gpu.pci_bdf})"]
            lines.append(f"  Fan control: {gpu.fan_control_method}")
            lines.append(f"  Overdrive: {'enabled' if gpu.overdrive_enabled else 'disabled'}")
            if gpu.ppfeaturemask:
                bit14 = "set" if gpu.ppfeaturemask_bit14_set else "NOT set"
                lines.append(f"  ppfeaturemask: {gpu.ppfeaturemask} (bit 14: {bit14})")
                if not gpu.ppfeaturemask_bit14_set:
                    lines.append(
                        "  Fan control requires bit 14 — add "
                        "'amdgpu.ppfeaturemask=0xffffffff' to kernel parameters"
                    )
            lines.append(
                f"  Zero-RPM: {'available' if gpu.zero_rpm_available else 'not available'}"
            )
            self._gpu_diag_label.setText("\n".join(lines))
            self._gpu_diag_label.setVisible(True)
        else:
            self._gpu_diag_label.setVisible(False)

        # Guidance from chip knowledge base (HTML with clickable links)
        guidance_parts: list[str] = []
        seen_prefixes: set[str] = set()
        for chip in hw.chips_detected:
            g = lookup_chip_guidance(chip.chip_name)
            if g and g.chip_prefix not in seen_prefixes:
                seen_prefixes.add(g.chip_prefix)
                if g.bios_tips:
                    guidance_parts.append(f"<b>{chip.chip_name} — BIOS tips:</b>")
                    for tip in g.bios_tips:
                        guidance_parts.append(f"&nbsp;&nbsp;\u2022 {tip}")
                if g.known_issues:
                    guidance_parts.append(f"<b>{chip.chip_name} — Known issues:</b>")
                    for issue in g.known_issues:
                        guidance_parts.append(f"&nbsp;&nbsp;\u2022 {issue}")
                if g.driver_url:
                    guidance_parts.append(
                        f'&nbsp;&nbsp;Driver docs: <a href="{g.driver_url}">{g.driver_url}</a>'
                    )
        if guidance_parts:
            self._guidance_label.setText("<br>".join(guidance_parts))
            self._guidance_label.setVisible(True)
        else:
            self._guidance_label.setVisible(False)

        # Show docs link when any hardware chips were detected
        if hw.chips_detected:
            self._docs_link_label.setText(
                "For detailed hardware compatibility information, see the "
                '<a href="https://github.com/control-ofc/control-ofc-gui/blob/main/'
                'docs/19_Hardware_Compatibility.md">Hardware Compatibility Guide</a>.'
            )
            self._docs_link_label.setVisible(True)
        else:
            self._docs_link_label.setVisible(False)

        # Populate verify header combo
        self._verify_combo.clear()
        if self._state:
            for h in self._state.hwmon_headers:
                if h.is_writable:
                    label = h.label or h.id
                    self._verify_combo.addItem(f"{label} ({h.id})", h.id)
        self._verify_btn.setEnabled(self._verify_combo.count() > 0)

    def _run_pwm_verify(self) -> None:
        """Run PWM verification test on the selected header."""
        header_id = self._verify_combo.currentData()
        if not header_id:
            self._verify_result_label.setText("No writable header selected")
            self._verify_result_label.setVisible(True)
            return
        if not self._client:
            self._verify_result_label.setText("Cannot verify: no daemon connection")
            self._verify_result_label.setVisible(True)
            return
        if not self._state or not self._state.lease.held:
            self._verify_result_label.setText(
                "Cannot verify: no hwmon lease held. Start fan control or acquire a lease first."
            )
            self._verify_result_label.setVisible(True)
            return

        lease_id = self._state.lease.lease_id
        if not lease_id:
            self._verify_result_label.setText("Cannot verify: lease ID unavailable")
            self._verify_result_label.setVisible(True)
            return

        self._verify_btn.setEnabled(False)
        self._verify_btn.setText("Testing...")
        self._verify_result_label.setVisible(False)

        try:
            from control_ofc.api.errors import DaemonError, DaemonUnavailable

            result = self._client.verify_hwmon_pwm(header_id, lease_id)
            self._show_verify_result(result)
        except DaemonUnavailable:
            self._verify_result_label.setText("Daemon unavailable during verify")
            self._verify_result_label.setVisible(True)
        except DaemonError as e:
            self._verify_result_label.setText(f"Verify error: {e.message}")
            self._verify_result_label.setVisible(True)
        finally:
            self._verify_btn.setEnabled(True)
            self._verify_btn.setText("Test PWM Control")

    def _show_verify_result(self, result: HwmonVerifyResult) -> None:
        """Display the result of a PWM verification test."""
        status_map = {
            "effective": (
                "PWM control is working correctly",
                "SuccessChip",
            ),
            "reverted": (
                "BIOS/EC reverted pwm_enable — fan control is being overridden",
                "CriticalChip",
            ),
            "clamped": (
                "PWM value was clamped or ignored by hardware",
                "WarningChip",
            ),
            "no_rpm_effect": (
                "PWM accepted but RPM did not change (fan may be disconnected or stalled)",
                "WarningChip",
            ),
            "rpm_unavailable": (
                "PWM write accepted but RPM readback unavailable to confirm",
                "CardMeta",
            ),
        }
        summary, css_class = status_map.get(
            result.result, (f"Unknown result: {result.result}", "CardMeta")
        )

        lines = [f"Result: {summary}"]
        if result.details:
            lines.append(result.details)
        lines.append(f"Test: wrote {result.test_pwm_percent}% PWM, waited {result.wait_seconds}s")
        init = result.initial_state
        final = result.final_state
        if init.rpm is not None and final.rpm is not None:
            lines.append(f"RPM: {init.rpm} → {final.rpm}")
        if init.pwm_enable is not None and final.pwm_enable is not None:
            lines.append(f"pwm_enable: {init.pwm_enable} → {final.pwm_enable}")

        # Post-verification guidance based on result + board/chip context
        board_vendor = ""
        chip_name = ""
        if self._state:
            header = next(
                (h for h in self._state.hwmon_headers if h.id == result.header_id),
                None,
            )
            if header:
                chip_name = header.chip_name
            if self._diag.last_hw_diagnostics:
                board_vendor = self._diag.last_hw_diagnostics.board.vendor

        daemon_result = result.result
        if daemon_result == "reverted":
            daemon_result = "pwm_enable_reverted"
        elif daemon_result == "clamped":
            daemon_result = "pwm_value_clamped"

        guidance = verification_guidance(daemon_result, board_vendor, chip_name)
        if guidance:
            lines.append("")
            lines.append(f"Next step: {guidance}")

        self._verify_result_label.setText("\n".join(lines))
        self._verify_result_label.setProperty("class", css_class)
        self._verify_result_label.style().unpolish(self._verify_result_label)
        self._verify_result_label.style().polish(self._verify_result_label)
        self._verify_result_label.setVisible(True)

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
