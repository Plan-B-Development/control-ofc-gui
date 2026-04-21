"""Dashboard page — real-time overview of fans, sensors, and system health."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import (
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    Freshness,
    OperationMode,
    SensorReading,
)
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.hwmon_guidance import lookup_chip_guidance
from control_ofc.ui.microcopy import get as mc
from control_ofc.ui.qt_util import block_signals
from control_ofc.ui.widgets.error_banner import ErrorBanner
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel
from control_ofc.ui.widgets.series_chooser_dialog import SensorPickerDialog
from control_ofc.ui.widgets.summary_card import SummaryCard
from control_ofc.ui.widgets.timeline_chart import TimelineChart

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.services.control_loop import ControlLoopService
    from control_ofc.services.profile_service import ProfileService


class DashboardPage(QWidget):
    """Landing page showing fan speeds, temperatures, and profile status."""

    open_diagnostics = Signal()

    # Stack indices
    _IDX_DISCONNECTED = 0
    _IDX_NO_HARDWARE = 1
    _IDX_LIVE = 2

    def __init__(
        self,
        state: AppState | None = None,
        history: HistoryStore | None = None,
        selection: SeriesSelectionModel | None = None,
        profile_service: ProfileService | None = None,
        settings_service: AppSettingsService | None = None,
        client: DaemonClient | None = None,
        control_loop: ControlLoopService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._history = history or HistoryStore()
        self._selection = selection or SeriesSelectionModel()
        self._profile_service = profile_service
        self._settings_service = settings_service
        self._client = client
        self._control_loop = control_loop
        self._fan_ids: list[str] = []  # Track fan IDs for table row mapping
        self._displayable_fan_keys: list[str] = []  # Fan series keys for selection
        self._has_data = False
        # Card-to-sensor bindings: category -> sensor_id (empty = auto)
        self._card_bindings: dict[str, str] = {}
        if settings_service:
            self._card_bindings = dict(settings_service.settings.card_sensor_bindings)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Stacked: empty states vs content
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_disconnected_state())
        self._stack.addWidget(self._build_no_hardware_state())
        self._stack.addWidget(self._build_live_content())
        self._stack.setCurrentIndex(self._IDX_DISCONNECTED)
        layout.addWidget(self._stack)

        # Wire state signals
        if self._state:
            self._state.sensors_updated.connect(self._on_sensors_updated)
            self._state.fans_updated.connect(self._on_fans_updated)
            self._state.active_profile_changed.connect(self._on_profile_changed)
            self._state.warning_count_changed.connect(self._on_warnings_changed)
            self._state.connection_changed.connect(self._on_connection_changed)
            self._state.mode_changed.connect(self._on_mode_changed)
            self._state.capabilities_updated.connect(self._on_capabilities_updated)
            self._state.status_updated.connect(self._on_status_updated)

        # Chart refresh timer — visibility-gated for performance (R48)
        self._chart_timer = QTimer(self)
        self._chart_timer.setInterval(1000)
        self._chart_timer.timeout.connect(self._chart.update_chart)
        self._chart_timer.start()
        self._chart_active_interval = 1000  # 1Hz when active
        self._chart_background_interval = 5000  # 0.2Hz when app unfocused

        # Throttle chart when app loses focus (reduces compositor work while gaming)
        app = QApplication.instance()
        if app:
            app.applicationStateChanged.connect(self._on_app_focus_changed)

    # ─── State builders ──────────────────────────────────────────────

    def _build_disconnected_state(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        title = QLabel("Not Connected")
        title.setProperty("class", "PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        msg = QLabel("Waiting for daemon connection...\nUse --demo to run without hardware.")
        msg.setProperty("class", "PageSubtitle")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        msg.setMaximumWidth(400)
        layout.addWidget(msg)

        return container

    def _build_no_hardware_state(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        title = QLabel(mc("dashboard_empty_title"))
        title.setProperty("class", "PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        msg = QLabel(
            "Connected to the daemon, but no sensor or fan data has been received.\n"
            "This may mean hardware is not detected or the daemon has no subsystems online."
        )
        msg.setProperty("class", "PageSubtitle")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        msg.setMaximumWidth(500)
        layout.addWidget(msg)

        # Subsystem breakdown card
        self._subsystem_frame = QFrame()
        self._subsystem_frame.setProperty("class", "Card")
        self._subsystem_frame.setMaximumWidth(420)
        sub_layout = QVBoxLayout(self._subsystem_frame)

        sub_title = QLabel("Subsystem Status")
        sub_title.setProperty("class", "SectionTitle")
        sub_layout.addWidget(sub_title)

        self._sub_openfan_label = QLabel("OpenFan: unknown")
        self._sub_openfan_label.setObjectName("Dashboard_Label_subOpenfan")
        sub_layout.addWidget(self._sub_openfan_label)

        self._sub_hwmon_label = QLabel("hwmon: unknown")
        self._sub_hwmon_label.setObjectName("Dashboard_Label_subHwmon")
        sub_layout.addWidget(self._sub_hwmon_label)

        layout.addWidget(self._subsystem_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        # What to do next
        next_frame = QFrame()
        next_frame.setProperty("class", "Card")
        next_frame.setMaximumWidth(420)
        next_layout = QVBoxLayout(next_frame)

        next_title = QLabel("What to do next")
        next_title.setProperty("class", "SectionTitle")
        next_layout.addWidget(next_title)

        next_msg = QLabel(
            "1. Check that the daemon is running: systemctl status control-ofc-daemon\n"
            "2. Verify hardware permissions (user in 'dialout' group for serial)\n"
            "3. Open Diagnostics for detailed subsystem health"
        )
        next_msg.setWordWrap(True)
        next_msg.setProperty("class", "PageSubtitle")
        next_layout.addWidget(next_msg)

        diag_btn = QPushButton("Open Diagnostics")
        diag_btn.setObjectName("Dashboard_Btn_openDiagnostics")
        diag_btn.clicked.connect(self.open_diagnostics.emit)
        next_layout.addWidget(diag_btn)

        layout.addWidget(next_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        return container

    def _build_live_content(self) -> QWidget:
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 16, 24, 16)
        content_layout.setSpacing(12)

        # Title + mode badge
        title_row = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setProperty("class", "PageTitle")
        title_row.addWidget(title)

        self._mode_badge = QLabel("")
        self._mode_badge.setProperty("class", "SectionTitle")
        title_row.addWidget(self._mode_badge)
        title_row.addStretch()
        content_layout.addLayout(title_row)

        # Hwmon info banner — shown when hwmon is absent or all read-only
        self._hwmon_banner = ErrorBanner()
        self._hwmon_banner.setObjectName("Dashboard_Banner_hwmon")
        content_layout.addWidget(self._hwmon_banner)

        # Row 1: Summary cards + profile quick switch
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        self._cpu_card = SummaryCard("CPU Temp", category="cpu_temp")
        self._gpu_card = SummaryCard("GPU Temp", category="gpu_temp")
        self._mb_card = SummaryCard("Motherboard", category="mobo_temp")
        self._fans_card = SummaryCard("Fans", category="fans")
        self._warnings_card = SummaryCard("Warnings", "0", category="warnings")

        for card in [self._cpu_card, self._gpu_card, self._mb_card]:
            card.clicked.connect(self._on_card_clicked)
            cards_layout.addWidget(card)

        self._fans_card.clicked.connect(self._on_card_clicked)
        cards_layout.addWidget(self._fans_card)

        self._warnings_card.clicked.connect(self._on_card_clicked)
        cards_layout.addWidget(self._warnings_card)

        cards_layout.addStretch()

        # Profile quick switch — far right
        self._profile_widget = self._build_profile_widget()
        cards_layout.addWidget(self._profile_widget)

        content_layout.addLayout(cards_layout)

        # Horizontal splitter: left content (chart+table) | right sensor panel
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._h_splitter.setObjectName("Dashboard_Splitter_horizontal")

        # Left pane: vertical splitter (chart on top, fan table on bottom)
        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._v_splitter.setObjectName("Dashboard_Splitter_vertical")

        color_overrides = {}
        if self._settings_service:
            color_overrides = dict(self._settings_service.settings.series_colors)
        self._chart = TimelineChart(
            self._history, selection=self._selection, color_overrides=color_overrides
        )
        self._chart.setMinimumHeight(150)
        self._v_splitter.addWidget(self._chart)

        # Fan table (shares left-column width with chart via vertical splitter)
        self._fan_table = QTableWidget(0, 4)
        self._fan_table.setHorizontalHeaderLabels(["Label", "Source", "RPM", "PWM%"])
        self._fan_table.verticalHeader().setVisible(False)
        self._fan_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._fan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._fan_table.setAlternatingRowColors(True)
        self._fan_table.verticalHeader().setDefaultSectionSize(24)
        self._fan_table.setMinimumHeight(60)
        self._fan_table.doubleClicked.connect(self._on_fan_table_double_click)
        from PySide6.QtWidgets import QHeaderView

        header = self._fan_table.horizontalHeader()
        header.setMinimumSectionSize(50)
        header.setStretchLastSection(False)
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self._v_splitter.addWidget(self._fan_table)

        self._v_splitter.setStretchFactor(0, 3)
        self._v_splitter.setStretchFactor(1, 1)
        self._v_splitter.setSizes([500, 200])
        self._h_splitter.addWidget(self._v_splitter)

        # Right pane: sensor series panel (spans full chart+table height)
        self._sensor_panel = SensorSeriesPanel(self._selection, state=self._state)
        if self._settings_service:
            self._sensor_panel.hide_igpu = self._settings_service.settings.hide_igpu_sensors
        self._sensor_panel.set_chart(self._chart, self._settings_service)
        self._h_splitter.addWidget(self._sensor_panel)

        self._h_splitter.setStretchFactor(0, 3)
        self._h_splitter.setStretchFactor(1, 1)
        self._h_splitter.setSizes([800, 260])
        self._h_splitter.setCollapsible(0, False)
        self._h_splitter.setCollapsible(1, False)
        content_layout.addWidget(self._h_splitter, 1)

        return content

    def _build_profile_widget(self) -> QWidget:
        """Build the profile quick-switch card."""
        widget = QFrame()
        widget.setProperty("class", "Card")
        widget.setMinimumWidth(140)
        widget.setMaximumHeight(90)

        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        lbl = QLabel("Profile")
        lbl.setProperty("class", "PageSubtitle")
        layout.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(4)
        self._profile_combo = QComboBox()
        self._profile_combo.setObjectName("Dashboard_Combo_profileSwitch")
        self._profile_combo.setMinimumWidth(80)
        row.addWidget(self._profile_combo, 1)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setObjectName("Dashboard_Btn_applyProfile")
        self._apply_btn.clicked.connect(self._on_apply_profile)
        row.addWidget(self._apply_btn)
        layout.addLayout(row)

        return widget

    # ─── Signal handlers ─────────────────────────────────────────────

    def _on_connection_changed(self, state: ConnectionState) -> None:
        if state == ConnectionState.DISCONNECTED:
            self._has_data = False
            self._stack.setCurrentIndex(self._IDX_DISCONNECTED)
        elif state == ConnectionState.CONNECTED and not self._has_data:
            self._stack.setCurrentIndex(self._IDX_NO_HARDWARE)

    def _on_mode_changed(self, mode: OperationMode) -> None:
        badges = {
            OperationMode.MANUAL_OVERRIDE: ("MANUAL OVERRIDE", "ManualBadge"),
            OperationMode.DEMO: ("DEMO MODE", "DemoBadge"),
            OperationMode.READ_ONLY: ("READ ONLY", "PageSubtitle"),
        }
        if mode in badges:
            text, css_class = badges[mode]
            self._mode_badge.setText(text)
            self._mode_badge.setProperty("class", css_class)
        else:
            self._mode_badge.setText("")
            self._mode_badge.setProperty("class", "")
        self._mode_badge.style().unpolish(self._mode_badge)
        self._mode_badge.style().polish(self._mode_badge)

    def _on_capabilities_updated(self, caps: Capabilities) -> None:
        of = caps.openfan
        if of.present:
            self._sub_openfan_label.setText(f"OpenFan: detected ({of.channels} ch)")
            self._sub_openfan_label.setProperty("class", "SuccessChip")
        else:
            self._sub_openfan_label.setText("OpenFan: not detected")
            self._sub_openfan_label.setProperty("class", "PageSubtitle")

        hw = caps.hwmon
        if hw.present:
            self._sub_hwmon_label.setText(f"hwmon: detected ({hw.pwm_header_count} headers)")
            self._sub_hwmon_label.setProperty("class", "SuccessChip")
        else:
            self._sub_hwmon_label.setText("hwmon: not detected")
            self._sub_hwmon_label.setProperty("class", "PageSubtitle")

        # Update GPU card title from detected GPU model
        gpu = caps.amd_gpu
        if gpu.present:
            self._gpu_card.set_title(f"{gpu.display_label} Temp")

        for lbl in (self._sub_openfan_label, self._sub_hwmon_label):
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

        # Hwmon info banner on live page
        if not hw.present:
            self._hwmon_banner.show_info(
                "No motherboard fan headers detected. "
                "Check Diagnostics \u2192 Fans for driver and BIOS guidance.",
                auto_dismiss_ms=0,
            )
        elif hw.present and not hw.write_support:
            self._hwmon_banner.show_warning(
                "Motherboard fan headers detected but all are read-only. "
                "Check BIOS fan settings or driver status in Diagnostics \u2192 Fans.",
                auto_dismiss_ms=0,
            )
        else:
            self._hwmon_banner.hide_banner()

    def _on_status_updated(self, status: DaemonStatus) -> None:
        for sub in status.subsystems:
            if sub.name == "openfan" and sub.status != "ok":
                reason = f" ({sub.reason})" if sub.reason else ""
                self._sub_openfan_label.setText(f"OpenFan: {sub.status}{reason}")
                self._sub_openfan_label.setProperty("class", "WarningChip")
                self._sub_openfan_label.style().unpolish(self._sub_openfan_label)
                self._sub_openfan_label.style().polish(self._sub_openfan_label)
            elif sub.name == "hwmon" and sub.status != "ok":
                reason = f" ({sub.reason})" if sub.reason else ""
                self._sub_hwmon_label.setText(f"hwmon: {sub.status}{reason}")
                self._sub_hwmon_label.setProperty("class", "WarningChip")
                self._sub_hwmon_label.style().unpolish(self._sub_hwmon_label)
                self._sub_hwmon_label.style().polish(self._sub_hwmon_label)

    def _on_sensors_updated(self, sensors: list[SensorReading]) -> None:
        if sensors:
            self._show_content()

        # Update sensor panel first (applies iGPU filtering)
        self._sensor_panel.update_sensors(sensors)

        # Seed selection model from DISPLAYABLE keys only — not raw history.
        # The panel filters iGPU sensors and the fan handler filters duplicate
        # hwmon fans. Only keys for entities visible in panel/table should be
        # graphable. Fan keys are added in _on_fans_updated.
        displayed_ids = self._sensor_panel.displayed_sensor_ids()
        displayable_sensor_keys = [f"sensor:{sid}" for sid in displayed_ids]
        self._selection.update_known_keys(displayable_sensor_keys + self._displayable_fan_keys)

        sensor_by_id = {s.id: s for s in sensors}

        # Update each card using binding if set, else auto by kind
        # Daemon sends snake_case kinds (cpu_temp), demo sends PascalCase (CpuTemp)
        self._update_card(
            self._cpu_card,
            "cpu_temp",
            ("CpuTemp", "cpu_temp"),
            sensors,
            sensor_by_id,
            warn=75,
            crit=85,
        )
        self._update_card(
            self._gpu_card,
            "gpu_temp",
            ("GpuTemp", "gpu_temp"),
            sensors,
            sensor_by_id,
            warn=80,
            crit=90,
        )
        self._update_card(self._mb_card, "mobo_temp", ("MbTemp", "mb_temp"), sensors, sensor_by_id)

    # ─── Visibility gating (R48 performance) ───────────────────────

    def showEvent(self, event) -> None:
        """Resume chart timer when dashboard becomes visible."""
        if not self._chart_timer.isActive():
            self._chart_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        """Stop chart timer when dashboard is hidden (e.g. switched to another page)."""
        self._chart_timer.stop()
        super().hideEvent(event)

    def _on_app_focus_changed(self, state) -> None:
        """Throttle chart when app loses focus (reduces compositor work while gaming)."""
        if state == Qt.ApplicationState.ApplicationActive:
            self._chart_timer.setInterval(self._chart_active_interval)
        else:
            self._chart_timer.setInterval(self._chart_background_interval)

    # ─── Fan updates ─────────────────────────────────────────────────

    def _on_fans_updated(self, fans: list[FanReading]) -> None:
        if fans:
            self._show_content()

        # Update sensor panel fan groups (applies displayability + dedup)
        self._sensor_panel.update_fans(fans)

        # Unified displayability rule — applied to ALL fans (OpenFan and hwmon equally).
        hide_unused = True
        if self._settings_service:
            hide_unused = self._settings_service.settings.hide_unused_fan_headers
        aliases = self._state.fan_aliases if self._state else {}
        display_fans = filter_displayable_fans(fans, aliases, hide_unused)

        self._fans_card.set_value(str(len(display_fans)))

        # Store displayable fan keys and re-seed selection model
        self._displayable_fan_keys = []
        for f in display_fans:
            self._displayable_fan_keys.append(f"fan:{f.id}:rpm")
        displayable_sensor_keys = [
            f"sensor:{sid}" for sid in self._sensor_panel.displayed_sensor_ids()
        ]
        self._selection.update_known_keys(displayable_sensor_keys + self._displayable_fan_keys)

        # Update fan table rows (add rows for new fans, update existing)
        self._fan_ids = [f.id for f in display_fans]
        if self._fan_table.rowCount() != len(display_fans):
            self._fan_table.setRowCount(len(display_fans))

        for row, fan in enumerate(display_fans):
            display_name = self._state.fan_display_name(fan.id) if self._state else fan.id
            rpm_text = f"{fan.rpm}" if fan.rpm is not None else "\u2014"
            pwm_text = f"{fan.last_commanded_pwm}%" if fan.last_commanded_pwm is not None else ""

            for col, text in enumerate([display_name, fan.source, rpm_text, pwm_text]):
                item = self._fan_table.item(row, col)
                if item is None:
                    item = QTableWidgetItem(text)
                    if col == 0:
                        item.setToolTip(self._fan_tooltip(fan))
                    self._fan_table.setItem(row, col, item)
                else:
                    if item.text() != text:
                        item.setText(text)

    def _fan_tooltip(self, fan: FanReading) -> str:
        """Build a tooltip for a fan row, including hwmon chip/driver context."""
        parts = [f"ID: {fan.id}"]
        if self._state and fan.source == "hwmon":
            header = next((h for h in self._state.hwmon_headers if h.id == fan.id), None)
            if header and header.chip_name:
                parts.append(f"Chip: {header.chip_name}")
                g = lookup_chip_guidance(header.chip_name)
                if g:
                    status = "mainline" if g.in_mainline else g.driver_package
                    parts.append(f"Driver: {g.driver_name} ({status})")
                mode = {0: "DC", 1: "PWM"}.get(
                    header.pwm_mode if header.pwm_mode is not None else -1
                )
                if mode:
                    parts.append(f"Mode: {mode}")
                if not header.is_writable:
                    parts.append("Status: read-only")
        return "\n".join(parts)

    def _update_card(
        self,
        card: SummaryCard,
        category: str,
        kinds: tuple[str, ...],
        sensors: list[SensorReading],
        sensor_by_id: dict[str, SensorReading],
        warn: float = 0,
        crit: float = 0,
    ) -> None:
        """Update a summary card from binding or auto-match by kind."""
        binding = self._card_bindings.get(category, "")
        sensor: SensorReading | None = None
        if binding and binding in sensor_by_id:
            sensor = sensor_by_id[binding]
        else:
            matches = [s for s in sensors if s.kind in kinds]
            if matches:
                sensor = matches[0]
        if sensor:
            freshness = sensor.freshness
            if freshness == Freshness.INVALID:
                card.set_value(f"{sensor.value_c:.1f}\u00b0C \u26a0")
                card.setToolTip(f"Stale reading ({sensor.age_ms / 1000:.0f}s old)")
                card.set_status_class("CriticalChip")
            elif freshness == Freshness.STALE:
                card.set_value(f"{sensor.value_c:.1f}\u00b0C \u23f1")
                card.setToolTip(f"Aging reading ({sensor.age_ms / 1000:.1f}s old)")
                card.set_status_class("WarningChip")
            else:
                card.set_value(f"{sensor.value_c:.1f}\u00b0C")
                card.setToolTip("")
                if crit and sensor.value_c > crit:
                    card.set_status_class("CriticalChip")
                elif warn and sensor.value_c > warn:
                    card.set_status_class("WarningChip")
                else:
                    card.set_status_class("")
            # Session min/max from GUI-side tracker
            stats = self._state.session_stats.get(sensor.id) if self._state else None
            card.set_range(
                stats.min_c if stats else None,
                stats.max_c if stats else None,
            )
        elif binding:
            card.set_value("\u2014")
            card.setToolTip("Bound sensor not available")
            card.set_range(None, None)

    def _on_warnings_changed(self, count: int) -> None:
        self._warnings_card.set_value(str(count))
        if count > 0:
            self._warnings_card.set_status_class("WarningChip")
        else:
            self._warnings_card.set_status_class("SuccessChip")

    def _on_profile_changed(self, name: str) -> None:
        # Sync combo selection to active profile
        idx = self._profile_combo.findText(name)
        if idx >= 0:
            with block_signals(self._profile_combo):
                self._profile_combo.setCurrentIndex(idx)

    def _on_card_clicked(self, category: str) -> None:
        """Open the appropriate dialog for the clicked card."""
        if category == "warnings":
            self._open_warnings_dialog()
            return

        # Sensor picker for temp/fan cards
        sensors = self._state.sensors if self._state else []
        fans = self._state.fans if self._state else []
        current = self._card_bindings.get(category, "")
        dialog = SensorPickerDialog(
            category=category,
            sensors=sensors,
            fans=fans,
            current_binding=current,
            parent=self,
        )
        # Connect live updates for the dialog's duration, then disconnect (P1-G5).
        _live_update = None
        if self._state:

            def _live_update(s):
                dialog.update_values(s, self._state.fans if self._state else [])

            self._state.sensors_updated.connect(_live_update)
        try:
            accepted = dialog.exec() == SensorPickerDialog.DialogCode.Accepted
        finally:
            if self._state and _live_update is not None:
                import contextlib

                with contextlib.suppress(RuntimeError):
                    self._state.sensors_updated.disconnect(_live_update)
        if accepted:
            selected = dialog.selected_sensor_id
            if selected:
                self._card_bindings[category] = selected
            else:
                self._card_bindings.pop(category, None)
            if self._settings_service:
                self._settings_service.update(card_sensor_bindings=dict(self._card_bindings))
            if self._state:
                self._on_sensors_updated(self._state.sensors)

    def _open_warnings_dialog(self) -> None:
        """Open the warnings viewer dialog."""
        if not self._state:
            return
        from control_ofc.ui.widgets.warnings_dialog import WarningsDialog

        dialog = WarningsDialog(self._state, parent=self)
        dialog.exec()

    def _on_fan_table_double_click(self, index) -> None:
        """Open rename dialog on double-click of fan table row."""
        row = index.row()
        if row < 0 or row >= len(self._fan_ids):
            return
        fan_id = self._fan_ids[row]
        current_name = self._fan_table.item(row, 0).text() if self._fan_table.item(row, 0) else ""
        new_name, ok = QInputDialog.getText(self, "Rename Fan", "Label:", text=current_name)
        if ok and new_name.strip() and new_name.strip() != current_name:
            if self._state:
                self._state.set_fan_alias(fan_id, new_name.strip())
            item = self._fan_table.item(row, 0)
            if item:
                item.setText(new_name.strip())

    def _on_apply_profile(self) -> None:
        """Apply the selected profile with visual feedback."""
        import logging

        log = logging.getLogger(__name__)

        profile_name = self._profile_combo.currentText()
        if not profile_name or not self._profile_service:
            return

        # Find profile by name
        target = None
        for p in self._profile_service.profiles:
            if p.name == profile_name:
                target = p
                break

        if not target:
            return

        # Save latest version then send to daemon
        self._profile_service.save_profile(target)
        profile_path = str(self._profile_service.profile_path(target.id))

        if self._client:
            try:
                from control_ofc.api.errors import DaemonError

                result = self._client.activate_profile(profile_path)
                if not result.activated:
                    log.warning("Daemon rejected profile: %s", target.name)
                    self._apply_btn.setText("Rejected")
                    self._apply_btn.setEnabled(False)
                    QTimer.singleShot(1500, self._reset_apply_btn)
                    return
                log.info("Profile activated on daemon: %s", result.profile_name)
            except DaemonError as exc:
                log.error("Profile activation failed: %s", exc)
                self._apply_btn.setText("Failed")
                self._apply_btn.setEnabled(False)
                QTimer.singleShot(1500, self._reset_apply_btn)
                return

        # Only update local state after daemon confirms (or no client)
        self._profile_service.set_active(target.id)
        if self._state:
            self._state.set_active_profile(target.name)
        # Force immediate control loop re-evaluation — active_profile_changed
        # is suppressed when the name is unchanged, so rely on a direct call.
        if self._control_loop is not None:
            self._control_loop.reevaluate_now()
        self._apply_btn.setText("Applied!")
        self._apply_btn.setEnabled(False)
        QTimer.singleShot(1500, self._reset_apply_btn)

    def _reset_apply_btn(self) -> None:
        self._apply_btn.setText("Apply")
        self._apply_btn.setEnabled(True)

    def populate_profiles(self) -> None:
        """Fill the profile combo from ProfileService. Call after profiles are loaded."""
        if not self._profile_service:
            return
        with block_signals(self._profile_combo):
            self._profile_combo.clear()
            for p in self._profile_service.profiles:
                self._profile_combo.addItem(p.name)
            # Select the active one
            if self._profile_service.active_profile:
                idx = self._profile_combo.findText(self._profile_service.active_profile.name)
                if idx >= 0:
                    self._profile_combo.setCurrentIndex(idx)

    def _show_content(self) -> None:
        if not self._has_data:
            self._has_data = True
            self._stack.setCurrentIndex(self._IDX_LIVE)
