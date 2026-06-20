"""Dashboard page — real-time overview of fans, sensors, and system health."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
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
from control_ofc.constants import (
    AGGREGATE_FAN_RPM_KEY,
    DEFAULT_SOCKET_PATH,
    EXPECTED_API_VERSION,
)
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.daemon_service_check import (
    ENABLE_COMMAND,
    check_daemon_service_state,
)
from control_ofc.services.fan_grouping import build_fan_groups
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import ChartMode, SeriesSelectionModel
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.hwmon_guidance import lookup_chip_guidance
from control_ofc.ui.qt_util import block_signals
from control_ofc.ui.status_banner import MODE_LABELS
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.error_banner import ErrorBanner
from control_ofc.ui.widgets.fan_zone_card import FanZoneGrid
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel
from control_ofc.ui.widgets.series_chooser_dialog import SensorPickerDialog
from control_ofc.ui.widgets.status_strip import THERMAL_STATES, DashboardStatusStrip
from control_ofc.ui.widgets.summary_card import SummaryCard
from control_ofc.ui.widgets.timeline_chart import TimelineChart

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.services.profile_service import ProfileService

# Trend deadband: a |rate| below this reads as "flat" so a near-steady
# temperature doesn't flicker the glyph between rising/falling.
_TREND_DEADBAND_C_PER_S = 0.05

# Plain-language reason per daemon thermal_state, for the Safety card detail.
# Kept qualitative (no hardcoded thresholds) so it can't drift from the daemon.
_THERMAL_REASONS: dict[str, str] = {
    "normal": "Cooling is operating normally; the daemon is following the active profile.",
    "recovery": (
        "Temperature exceeded the safety threshold. The daemon forced fans up and is holding "
        "a recovery speed until the system cools further."
    ),
    "emergency": (
        "A critical temperature was reached. The daemon has forced all controllable fans to "
        "100% to protect the hardware until temperatures fall."
    ),
    "no_sensor_fallback": (
        "No CPU temperature sensor is reachable. The daemon has forced a safe fallback fan "
        "speed because it cannot confirm the system is cool."
    ),
}


def _trend_from_rate(rate: float | None) -> str:
    """Map a °C/s rate to a trend direction ("up"/"down"/"flat"/"").

    ``None`` (no rate yet) yields "" (no glyph). Pure/testable; mirrors the
    deadband so the Summary card's arrow is stable."""
    if rate is None:
        return ""
    if rate > _TREND_DEADBAND_C_PER_S:
        return "up"
    if rate < -_TREND_DEADBAND_C_PER_S:
        return "down"
    return "flat"


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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._history = history or HistoryStore()
        self._selection = selection or SeriesSelectionModel()
        self._profile_service = profile_service
        self._settings_service = settings_service
        self._client = client
        self._fan_ids: list[str] = []  # Track fan IDs for table row mapping
        self._displayable_fan_keys: list[str] = []  # Fan series keys for selection
        self._has_data = False
        # Chart first-run seeding + poll-diff annotation state (DEC-181).
        self._seen_sensors = False
        self._seen_fans = False
        self._prev_connection = state.connection if state else None
        self._last_override_ids: set[str] = set()
        self._last_stale_sensor_ids: set[str] = set()
        self._last_stalled_fan_ids: set[str] = set()
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
            self._state.fan_zones_changed.connect(self._on_fan_zones_changed)
            self._state.fan_alias_changed.connect(self._on_fan_alias_changed)

        # Chart refresh timer — visibility-gated for performance (R48)
        self._chart_timer = QTimer(self)
        self._chart_timer.setInterval(1000)
        self._chart_timer.timeout.connect(self._chart.update_chart)
        self._chart_timer.start()
        self._chart_active_interval = 1000  # 1Hz when active
        self._chart_background_interval = 5000  # 0.2Hz when app unfocused

        # Poll-age refresh (~1 Hz) for the status strip's "Updated Xs ago" — kept
        # separate from the chart timer so it stays correct while the app is
        # unfocused (the chart throttles to 0.2 Hz then).
        self._poll_age_timer = QTimer(self)
        self._poll_age_timer.setInterval(1000)
        self._poll_age_timer.timeout.connect(self._tick_poll_age)
        self._poll_age_timer.start()
        self._tick_poll_age()

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

        # First-launch hint: the daemon service is installed but never enabled.
        # Hidden by default; populated on demand by _refresh_service_hint().
        self._service_hint_frame = QFrame()
        self._service_hint_frame.setObjectName("Dashboard_Frame_serviceHint")
        self._service_hint_frame.setProperty("class", "Card")
        self._service_hint_frame.setMaximumWidth(480)
        self._service_hint_frame.setVisible(False)
        hint_layout = QVBoxLayout(self._service_hint_frame)

        hint_title = QLabel("Daemon service is installed but disabled")
        hint_title.setProperty("class", "SectionTitle")
        hint_layout.addWidget(hint_title)

        hint_msg = QLabel(
            "The control-ofc-daemon service is present on this system but has "
            "not been enabled. Run the command below in a terminal, then "
            "re-open this GUI:"
        )
        hint_msg.setWordWrap(True)
        hint_msg.setProperty("class", "PageSubtitle")
        hint_layout.addWidget(hint_msg)

        cmd_label = QLabel(ENABLE_COMMAND)
        cmd_label.setObjectName("Dashboard_Label_enableCommand")
        cmd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cmd_label.setProperty("class", "MonoCommand")
        # Background colour pulled from the active theme so a light theme
        # gets a light tint rather than a hardcoded black wash (DEC-109).
        self._enable_cmd_label = cmd_label
        self._apply_enable_cmd_style()
        hint_layout.addWidget(cmd_label)

        copy_btn = QPushButton("Copy command")
        copy_btn.setObjectName("Dashboard_Btn_copyEnableCommand")
        copy_btn.clicked.connect(self._copy_enable_command)
        hint_layout.addWidget(copy_btn)

        layout.addWidget(self._service_hint_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        return container

    def _apply_enable_cmd_style(self) -> None:
        """Restyle the enable-command label using the current active theme.

        Called once at construction time and again from ``set_theme`` so the
        background tint follows light/dark theme changes (DEC-109).
        """
        if not hasattr(self, "_enable_cmd_label") or self._enable_cmd_label is None:
            return
        from control_ofc.ui.theme import active_theme

        tokens = active_theme()
        self._enable_cmd_label.setStyleSheet(
            f"font-family: monospace; padding: 6px; "
            f"background-color: {tokens.code_block_bg}; color: {tokens.text_primary};"
        )

    def set_theme(self, tokens) -> None:
        """Refresh widget styling for the new theme.

        Updates the inline enable-command label tint and forwards the change
        to the timeline chart so its background, axes, and crosshair pick up
        the new colours (DEC-109).
        """
        self._apply_enable_cmd_style()
        if hasattr(self, "_chart") and self._chart is not None:
            self._chart.set_theme(tokens)

    def _copy_enable_command(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(ENABLE_COMMAND)

    def _refresh_service_hint(self) -> None:
        """Probe the system once and show the enable-service hint if the
        daemon is installed but not enabled. No-op when can_check is False
        (non-systemd system, missing systemctl) or when the service is
        already enabled — in both cases the existing 'waiting' text is
        sufficient and we don't want to mislead the user."""
        if not hasattr(self, "_service_hint_frame"):
            return
        try:
            socket_path = (
                self._client.socket_path if self._client is not None else DEFAULT_SOCKET_PATH
            )
            state = check_daemon_service_state(socket_path)
        except Exception:
            self._service_hint_frame.setVisible(False)
            return
        self._service_hint_frame.setVisible(state.installed_but_not_enabled)

    def _build_no_hardware_state(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        title = QLabel("No Hardware Detected")
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
            "2. Missing motherboard sensor driver? Open Diagnostics → Troubleshooting "
            "and run Refresh Hardware Diagnostics — the readiness report names the "
            "exact kernel module or AUR package your board needs (the manual's Setup "
            "Checklist page has the full ordered walkthrough)\n"
            "3. Using an OpenFan controller? The daemon service accesses serial ports "
            "itself — it ships with the 'uucp' group on Arch / CachyOS; Debian / "
            "Ubuntu installs may need a 'dialout' drop-in (see the daemon docs)"
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

        # Title row (mode now lives in the status strip below).
        title_row = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setProperty("class", "PageTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        content_layout.addLayout(title_row)

        # Hwmon info banner — shown when hwmon is absent or all read-only
        self._hwmon_banner = ErrorBanner()
        self._hwmon_banner.setObjectName("Dashboard_Banner_hwmon")
        content_layout.addWidget(self._hwmon_banner)

        # API-version-skew banner — shown when the daemon's reported api_version
        # differs from EXPECTED_API_VERSION (out-of-lockstep package upgrade).
        self._api_version_banner = ErrorBanner()
        self._api_version_banner.setObjectName("Dashboard_Banner_api_version")
        content_layout.addWidget(self._api_version_banner)

        # Thermal-protection banner (DEC-132): surfaced from /status poll diffs
        # when the daemon's thermal_state leaves "normal" (105 °C emergency /
        # recovery). Poll is the authoritative transition source now the GUI
        # has no control loop watching thermal_state itself.
        self._thermal_banner = ErrorBanner()
        self._thermal_banner.setObjectName("Dashboard_Banner_thermal")
        content_layout.addWidget(self._thermal_banner)
        self._last_thermal_state = "normal"

        # Command + status strip (DEC-176/177): connection/profile/mode/thermal/
        # poll-age/warnings + the compact profile selector. Replaces the detached
        # profile widget; the global StatusBanner is hidden while the dashboard is
        # active (main_window), so this is the single status surface here.
        self._status_strip = DashboardStatusStrip()
        # The page owns the apply flow — reuse the strip's combo/apply verbatim.
        self._profile_combo = self._status_strip.profile_combo
        self._apply_btn = self._status_strip.apply_btn
        self._apply_btn.clicked.connect(self._on_apply_profile)
        self._status_strip.warning_clicked.connect(self._open_warnings_dialog)
        content_layout.addWidget(self._status_strip)

        # Row 1: Summary cards
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        self._cpu_card = SummaryCard("CPU Temp", category="cpu_temp")
        self._cpu_card.setObjectName("Dashboard_Card_cpu")
        self._gpu_card = SummaryCard("GPU Temp", category="gpu_temp")
        self._gpu_card.setObjectName("Dashboard_Card_gpu")
        self._mb_card = SummaryCard("Motherboard", category="mobo_temp")
        self._mb_card.setObjectName("Dashboard_Card_mobo")
        self._fans_card = SummaryCard("Fans", category="fans")
        self._fans_card.setObjectName("Dashboard_Card_fans")
        # Safety card (DEC-178): mirrors the strip's thermal chip at a glance but
        # earns its slot with a click-through thermal detail. Replaces the former
        # Warnings card — warnings now live solely in the strip's warning chip.
        self._safety_card = SummaryCard("Safety", category="safety")
        self._safety_card.setObjectName("Dashboard_Card_safety")

        for card in (
            self._cpu_card,
            self._gpu_card,
            self._mb_card,
            self._fans_card,
            self._safety_card,
        ):
            card.clicked.connect(self._on_card_clicked)
            cards_layout.addWidget(card)

        cards_layout.addStretch()
        content_layout.addLayout(cards_layout)

        # Initial render (strip + Safety card) now that every widget exists.
        self._sync_status_strip()

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
        if self._settings_service:
            # Startup default only — changing the Range combo afterwards is
            # session-local; the Settings value is re-applied on next launch.
            self._chart.set_range_index(self._settings_service.settings.chart_default_range_index)
        # Chart modes/reset (DEC-181): the chart is dumb about sensor kinds, so it
        # emits the choice and the page applies it (COMBINED is the curated subset).
        self._chart.mode_selected.connect(self._on_chart_mode_selected)
        self._chart.reset_requested.connect(self._on_chart_reset)
        self._push_chart_context()
        self._chart.setMinimumHeight(150)
        self._chart.setMinimumWidth(320)  # inspector can't crush the chart (refinement §7.5)
        self._v_splitter.addWidget(self._chart)

        # Primary fan display: zone cards (refinement §7.4, DEC-179). Driven by
        # the pure fan_grouping view-model; the dense raw table is re-homed below
        # into a collapsed "Raw fan data" expander (4A) rather than deleted.
        self._fan_zone_grid = FanZoneGrid(state=self._state, zone_provider=self._zone_names)
        zone_scroll = QScrollArea()
        zone_scroll.setObjectName("Dashboard_ScrollArea_fanZones")
        zone_scroll.setWidgetResizable(True)
        zone_scroll.setFrameShape(QFrame.Shape.NoFrame)
        zone_scroll.setWidget(self._fan_zone_grid)
        zone_scroll.setMinimumHeight(60)
        self._v_splitter.addWidget(zone_scroll)

        # Raw fan table — built intact (columns / sort / double-click rename
        # preserved) but detached from the splitter; folded into the collapsed
        # expander once the splitter is assembled.
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
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

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

        # Raw fan data (4A): the dense table, one collapsed click away. Cards are
        # primary now, so the table is advanced detail kept in context, not deleted.
        self._raw_fan_section = CollapsibleSection(
            "Raw fan data", "Dashboard_Section_rawFanData", expanded=False
        )
        self._raw_fan_section.add_widget(self._fan_table)
        content_layout.addWidget(self._raw_fan_section)

        return content

    # ─── Signal handlers ─────────────────────────────────────────────

    def _on_connection_changed(self, state: ConnectionState) -> None:
        self._status_strip.set_connection_state(state)
        # (Re)connect transition → annotation (poll-diff, DEC-181).
        if (
            state == ConnectionState.CONNECTED
            and self._prev_connection != ConnectionState.CONNECTED
        ):
            self._annotate("Connected")
        self._prev_connection = state
        if state == ConnectionState.DISCONNECTED:
            self._has_data = False
            self._reset_cards()
            self._stack.setCurrentIndex(self._IDX_DISCONNECTED)
            self._refresh_service_hint()
        elif state == ConnectionState.CONNECTED and not self._has_data:
            self._stack.setCurrentIndex(self._IDX_NO_HARDWARE)

    def _on_mode_changed(self, mode: OperationMode) -> None:
        self._status_strip.set_operation_mode(mode)
        self._push_chart_context()

    def _sync_status_strip(self) -> None:
        """Push current AppState into the strip + Safety card (initial render)."""
        if not self._state:
            return
        self._status_strip.set_connection_state(self._state.connection)
        self._status_strip.set_operation_mode(self._state.mode)
        self._status_strip.set_active_profile(self._state.active_profile_name)
        self._status_strip.set_warning_count(self._state.warning_count)
        ds = self._state.daemon_status
        if ds:
            self._status_strip.set_thermal_state(ds.thermal_state)
            self._update_safety_card(ds.thermal_state, ds.overrides)

    def _update_safety_card(self, thermal: str, overrides: list) -> None:
        """Render the Safety card from thermal_state via the shared THERMAL_STATES
        map (same source as the strip's thermal chip — no drift). Manual overrides
        appear as a secondary note; the click-through detail carries the reason."""
        label, css = THERMAL_STATES.get(thermal or "normal", (f"Thermal: {thermal}", "InfoChip"))
        self._safety_card.set_value(label)
        self._safety_card.set_status_class(css)
        n = len(overrides) if overrides else 0
        self._safety_card.set_detail_text(
            f"{n} manual override{'s' if n != 1 else ''} active" if n else ""
        )

    def _reset_cards(self) -> None:
        """Clear card faces to a neutral "—" on disconnect so a stale
        pre-disconnect value is never presented as current (refinement §4.2)."""
        for card in (self._cpu_card, self._gpu_card, self._mb_card, self._fans_card):
            card.set_value("—")
            card.set_trend("")
            card.set_detail_text("")
            card.set_status_class("")
            card.setToolTip("")
        self._safety_card.set_value("—")
        self._safety_card.set_status_class("")
        self._safety_card.set_detail_text("")
        # Drop zone tiles too — a stale tile must not survive a disconnect.
        self._fan_zone_grid.set_groups([])

    def _tick_poll_age(self) -> None:
        if self._state:
            self._status_strip.update_poll_age(time.monotonic(), self._state.last_poll_monotonic)

    # ─── Chart series: known keys, curated subset, modes (DEC-181) ────

    def _register_known_keys(self) -> None:
        """Push the displayable sensor + fan keys (and the synthetic aggregate, when
        any fan exists) into the selection model — the single source for both
        handlers so the aggregate key is never forgotten."""
        keys = [f"sensor:{sid}" for sid in self._sensor_panel.displayed_sensor_ids()]
        keys += self._displayable_fan_keys
        if self._displayable_fan_keys:
            keys.append(AGGREGATE_FAN_RPM_KEY)
        self._selection.update_known_keys(keys)

    def _curated_sensor_id(
        self, category: str, kinds: tuple[str, ...], sensors: list[SensorReading]
    ) -> str | None:
        """The one sensor id that represents a card category in the curated chart
        subset — the card's binding if set, else the first by kind (mirrors
        ``_update_card`` so the chart's default line matches the card)."""
        binding = self._card_bindings.get(category, "")
        if binding and any(s.id == binding for s in sensors):
            return binding
        for s in sensors:
            if s.kind in kinds:
                return s.id
        return None

    def _curated_chart_keys(self) -> set[str]:
        """The curated default series (refinement §7.3 / B-fork DEC-181): CPU temp,
        GPU temp, one mobo/case temp, and the aggregate fan line. Kind-aware, so it
        lives here (the pure model can't tell a CPU temp from a GPU temp by key).
        Non-existent slots are simply dropped; ``set_only_visible`` intersects with
        known keys so a filtered/absent sensor is harmless."""
        sensors = self._state.sensors if self._state else []
        keys: set[str] = set()
        for category, kinds in (
            ("cpu_temp", ("CpuTemp", "cpu_temp")),
            ("gpu_temp", ("GpuTemp", "gpu_temp")),
            ("mobo_temp", ("MbTemp", "mb_temp")),
        ):
            sid = self._curated_sensor_id(category, kinds, sensors)
            if sid:
                keys.add(f"sensor:{sid}")
        if self._displayable_fan_keys:
            keys.add(AGGREGATE_FAN_RPM_KEY)
        return keys

    def _maybe_seed_chart_defaults(self) -> None:
        """First-run only (A-fork DEC-181): once BOTH sensors and fans have been
        seen, declutter the chart to the curated subset and latch
        ``chart_series_seeded`` so a returning user who chose "show all" is never
        re-decluttered. Skipped entirely without a settings service or once seeded."""
        if not self._settings_service or self._settings_service.settings.chart_series_seeded:
            return
        if not (self._seen_sensors and self._seen_fans):
            return
        self._selection.apply_mode(ChartMode.COMBINED, self._curated_chart_keys())
        self._chart.set_mode(ChartMode.COMBINED)
        self._settings_service.update(chart_series_seeded=True)

    def _on_chart_mode_selected(self, mode: ChartMode) -> None:
        curated = self._curated_chart_keys() if mode == ChartMode.COMBINED else None
        self._selection.apply_mode(mode, curated)

    def _on_chart_reset(self) -> None:
        """Reset-to-default: restore the curated Combined subset and reflect it in
        the selector (refinement §11)."""
        self._selection.apply_mode(ChartMode.COMBINED, self._curated_chart_keys())
        self._chart.set_mode(ChartMode.COMBINED)

    def _push_chart_context(self) -> None:
        """Feed the chart's crosshair footer the current profile + mode (DEC-181)."""
        if not self._state:
            return
        self._chart.set_status_context(
            self._state.active_profile_name, MODE_LABELS.get(self._state.mode, "")
        )

    def _annotate(self, label: str) -> None:
        """Add a poll-diff event line to the chart at the current monotonic time."""
        self._chart.add_annotation(time.monotonic(), label)

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

        # Update GPU card title from detected GPU model. AMD takes priority
        # when both vendors are present; otherwise fall back to Intel (DEC-121).
        gpu = caps.amd_gpu
        if gpu.present:
            self._gpu_card.set_title(f"{gpu.display_label} Temp")
        elif caps.intel_gpu.present:
            self._gpu_card.set_title(f"{caps.intel_gpu.display_label} Temp")

        for lbl in (self._sub_openfan_label, self._sub_hwmon_label):
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

        # Hwmon info banner on live page
        if not hw.present:
            self._hwmon_banner.show_info(
                "No motherboard fan headers detected. "
                "Check Diagnostics \u2192 Troubleshooting for driver and BIOS guidance.",
                auto_dismiss_ms=0,
            )
        elif hw.present and not hw.write_support:
            self._hwmon_banner.show_warning(
                "Motherboard fan headers detected but all are read-only. "
                "Check BIOS fan settings or driver status in Diagnostics \u2192 Troubleshooting.",
                auto_dismiss_ms=0,
            )
        else:
            self._hwmon_banner.hide_banner()

        # API-version-skew guard: the GUI and daemon are independently packaged
        # (AUR), so a user can upgrade one without the other. The depends>= floor
        # only guards the minimum daemon version, not a future-incompatible one,
        # and gives no signal when the GUI is older than the daemon. Re-evaluated
        # on every reconnect (capabilities re-fetch after a daemon restart).
        if caps.api_version != EXPECTED_API_VERSION:
            import logging

            msg = (
                f"Daemon API v{caps.api_version} differs from this GUI's expected "
                f"v{EXPECTED_API_VERSION}. Align your control-ofc-daemon and "
                "control-ofc-gui package versions \u2014 some features may misbehave."
            )
            self._api_version_banner.show_warning(msg, auto_dismiss_ms=0)
            self._state.add_warning(
                level="warning", source="api", message=msg, key="api_version_skew"
            )
            logging.getLogger(__name__).warning(
                "API version skew: daemon reports api_version=%d, GUI expects %d",
                caps.api_version,
                EXPECTED_API_VERSION,
            )
        else:
            self._api_version_banner.hide_banner()
            self._state.remove_warning("api_version_skew")

    def _on_status_updated(self, status: DaemonStatus) -> None:
        # Thermal-protection transition (poll-diff): the daemon's 105 °C
        # emergency / recovery overrides fan control. Surface it the moment
        # thermal_state leaves "normal", and clear it on the return.
        thermal = status.thermal_state or "normal"
        self._status_strip.set_thermal_state(thermal)
        self._update_safety_card(thermal, status.overrides)
        if thermal != self._last_thermal_state:
            self._last_thermal_state = thermal
            self._annotate(f"Thermal: {thermal}")
            if thermal == "normal":
                self._thermal_banner.hide_banner()
            else:
                self._thermal_banner.show_error(
                    f"Thermal protection active ({thermal}) — the daemon has overridden "
                    "fan control to protect your hardware. Fans return to your profile "
                    "once temperatures recover."
                )

        # Override start/end (poll-diff, DEC-181) — net-new diff state; overrides
        # are not otherwise tracked across polls.
        override_ids = {o.control_id for o in status.overrides} if status.overrides else set()
        if override_ids != self._last_override_ids:
            for cid in sorted(override_ids - self._last_override_ids):
                self._annotate(f"Override: {cid}")
            for cid in sorted(self._last_override_ids - override_ids):
                self._annotate(f"Override end: {cid}")
            self._last_override_ids = override_ids

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
            self._seen_sensors = True
            # Stale-sensor onset → annotation (poll-diff, DEC-181). Onset only,
            # to avoid re-annotating a sensor that stays stale across polls.
            stale_now = {s.id for s in sensors if s.freshness != Freshness.FRESH}
            for sid in sorted(stale_now - self._last_stale_sensor_ids):
                self._annotate(f"Stale: {sid}")
            self._last_stale_sensor_ids = stale_now

        # Re-read the iGPU auto-hide setting each poll so the toggle applies
        # live, mirroring hide_unused_fan_headers in _on_fans_updated (F9).
        if self._settings_service:
            self._sensor_panel.hide_igpu = self._settings_service.settings.hide_igpu_sensors
        # Update sensor panel first (applies iGPU filtering)
        self._sensor_panel.update_sensors(sensors)

        # Register displayable keys for charting — DISPLAYABLE only (the panel
        # filters iGPU sensors and the fan handler filters duplicate hwmon fans).
        # Fan keys + the aggregate are folded in by _register_known_keys.
        self._register_known_keys()

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

        self._maybe_seed_chart_defaults()

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

    def cleanup(self) -> None:
        """Release chart resources before app shutdown. Idempotent."""
        self._chart_timer.stop()
        self._poll_age_timer.stop()
        self._chart.cleanup()

    def closeEvent(self, event) -> None:
        """Release chart resources when the page is closed (e.g. window-manager
        close or test teardown) and not only via an explicit ``cleanup`` call,
        so the secondary-ViewBox links are broken before destruction."""
        self.cleanup()
        super().closeEvent(event)

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
            self._seen_fans = True
            # Fan-stall onset → annotation (poll-diff, DEC-181). Onset only.
            stalled_now = {f.id for f in fans if f.stall_detected}
            for fid in sorted(stalled_now - self._last_stalled_fan_ids):
                name = self._state.fan_display_name(fid) if self._state else fid
                self._annotate(f"Stall: {name}")
            self._last_stalled_fan_ids = stalled_now

        # Update sensor panel fan groups (applies displayability + dedup)
        self._sensor_panel.update_fans(fans)

        # Unified displayability rule — applied to ALL fans (OpenFan and hwmon equally).
        hide_unused = True
        if self._settings_service:
            hide_unused = self._settings_service.settings.hide_unused_fan_headers
        aliases = self._state.fan_aliases if self._state else {}
        display_fans = filter_displayable_fans(fans, aliases, hide_unused)

        self._update_fans_card(display_fans)
        self._refresh_fan_zones()

        # Store displayable fan keys and re-register (folds in sensors + aggregate)
        self._displayable_fan_keys = [f"fan:{f.id}:rpm" for f in display_fans]
        self._register_known_keys()

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

        self._maybe_seed_chart_defaults()

    def _zone_names(self) -> list[str]:
        """Distinct existing user-zone names, for the assign-to-zone picker."""
        if not self._state:
            return []
        return sorted({z for z in self._state.fan_zones.values() if z})

    @staticmethod
    def _absent_member_ids(profile, present_ids: set[str]) -> set[str]:
        """Active-profile member fan ids that are *expected* but currently absent
        from the readings — these become OFFLINE tiles.

        Pure/testable. A present-but-idle member fan (one filtered out of the calm
        card view) stays in ``present_ids`` and is therefore simply omitted, never
        mislabelled OFFLINE — truthfulness over completeness (refinement §4.2)."""
        if profile is None:
            return set()
        member_ids = {m.member_id for c in profile.controls for m in c.members}
        return member_ids - present_ids

    def _refresh_fan_zones(self) -> None:
        """Rebuild the zone cards from the latest readings + active profile +
        overrides. The same trigger fires on poll, zone re-assignment, and rename
        so the cards always reflect current intent. Cheap and idempotent."""
        if not self._state:
            return
        fans = self._state.fans or []
        hide_unused = (
            self._settings_service.settings.hide_unused_fan_headers
            if self._settings_service
            else True
        )
        display_fans = filter_displayable_fans(fans, self._state.fan_aliases, hide_unused)
        profile = self._profile_service.active_profile if self._profile_service else None
        status = self._state.daemon_status
        overrides = status.overrides if status else []
        expected = self._absent_member_ids(profile, {f.id for f in fans})
        groups = build_fan_groups(
            display_fans,
            fan_zones=self._state.fan_zones,
            display_name=self._state.fan_display_name,
            active_profile=profile,
            overrides=overrides,
            expected_fan_ids=expected or None,
        )
        self._fan_zone_grid.set_groups(groups)

    def _on_fan_zones_changed(self, fan_id: str, zone: str) -> None:
        del fan_id, zone  # the whole grouping is recomputed from state
        self._refresh_fan_zones()

    def _on_fan_alias_changed(self, fan_id: str, display_name: str) -> None:
        del fan_id, display_name  # tiles re-resolve their display name on rebuild
        self._refresh_fan_zones()

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
            # Trend glyph only while the reading is live — a stale rate is not
            # trustworthy. Rendered in its own label, beside the value.
            card.set_trend(
                _trend_from_rate(sensor.rate_c_per_s) if freshness == Freshness.FRESH else ""
            )
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
            card.set_trend("")
            card.setToolTip("Bound sensor not available")
            card.set_range(None, None)

    def _update_fans_card(self, display_fans: list[FanReading]) -> None:
        """Fans card face: online/expected + average PWM/RPM. "Online" = a FRESH
        reading; a shortfall flags a warning. Definitions mirror the fan_grouping
        view-model (Phase 4 wires per-zone cards from the same data)."""
        total = len(display_fans)
        online = sum(1 for f in display_fans if f.freshness == Freshness.FRESH)
        self._fans_card.set_value(f"{online}/{total}")
        self._fans_card.set_status_class("WarningChip" if total and online < total else "")
        rpms = [f.rpm for f in display_fans if f.rpm is not None]
        pwms = [f.last_commanded_pwm for f in display_fans if f.last_commanded_pwm is not None]
        parts = []
        if pwms:
            parts.append(f"avg {round(sum(pwms) / len(pwms))}% PWM")
        if rpms:
            parts.append(f"{round(sum(rpms) / len(rpms))} rpm")
        self._fans_card.set_detail_text(" \u00b7 ".join(parts))

    def _on_warnings_changed(self, count: int) -> None:
        # Warnings now surface only in the strip's warning chip (DEC-178); the
        # former Warnings summary card was replaced by the Safety card.
        self._status_strip.set_warning_count(count)

    def _on_profile_changed(self, name: str) -> None:
        self._status_strip.set_active_profile(name)
        self._annotate(f"Profile: {name}" if name else "Profile cleared")
        self._push_chart_context()
        # Sync combo selection to active profile
        idx = self._profile_combo.findText(name)
        if idx >= 0:
            with block_signals(self._profile_combo):
                self._profile_combo.setCurrentIndex(idx)

    def _on_card_clicked(self, category: str) -> None:
        """Open the appropriate dialog for the clicked card."""
        if category == "safety":
            self._open_safety_detail()
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

    def _safety_detail_text(self) -> str:
        """Read-only thermal-safety summary for the Safety card's click detail.

        Pure (no I/O) so it is unit-testable. Surfaces only data we actually have
        — state, a plain reason, the current hottest CPU sensor, and any active
        manual overrides. It does NOT invent a "last safe value" or a persisted
        transition timestamp (neither is daemon-provided)."""
        ds = self._state.daemon_status if self._state else None
        thermal = (ds.thermal_state if ds else "normal") or "normal"
        label, _css = THERMAL_STATES.get(thermal, (f"Thermal: {thermal}", ""))
        lines = [
            f"State: {label}",
            "",
            _THERMAL_REASONS.get(thermal, "Current daemon thermal state."),
        ]
        sensors = self._state.sensors if self._state else []
        cpu_vals = [s.value_c for s in sensors if s.kind in ("CpuTemp", "cpu_temp")]
        if cpu_vals:
            lines += ["", f"Hottest CPU sensor: {max(cpu_vals):.1f}°C"]
        n = len(ds.overrides) if ds and ds.overrides else 0
        if n:
            lines += ["", f"{n} manual override{'s' if n != 1 else ''} active."]
        return "\n".join(lines)

    def _open_safety_detail(self) -> None:
        """Show the read-only thermal-safety detail (Safety card click)."""
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setObjectName("Dashboard_Dialog_safetyDetail")
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Thermal safety")
        box.setText(self._safety_detail_text())
        box.exec()

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
        # The daemon re-evaluates the activated profile itself (DEC-165); the
        # GUI no longer forces a local control-loop re-evaluation.
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
