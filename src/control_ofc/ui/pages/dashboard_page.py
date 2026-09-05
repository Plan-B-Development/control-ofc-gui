"""Dashboard page — real-time overview of fans, sensors, and system health."""

from __future__ import annotations

import time
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
    QScrollArea,
    QSplitter,
    QStackedWidget,
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
    DEFAULT_SOCKET_PATH,
    EXPECTED_API_VERSION,
)
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.daemon_service_check import (
    ENABLE_COMMAND,
    check_daemon_service_state,
)
from control_ofc.services.dashboard_view import (
    build_capabilities_vm,
    runtime_config_degraded_message,
)
from control_ofc.services.fan_cards_view import build_fan_card_vms
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import (
    ChartMode,
    SeriesSelectionModel,
    default_series_keys,
)
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.cards import SectionHeader
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.qt_util import block_signals, repolish, set_chip_class, style_splitter
from control_ofc.ui.status_banner import MODE_LABELS
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.dashboard_inspector import DashboardInspector
from control_ofc.ui.widgets.error_banner import ErrorBanner
from control_ofc.ui.widgets.fan_control_card import FanControlCard
from control_ofc.ui.widgets.flow_layout import FlowLayout
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel
from control_ofc.ui.widgets.timeline_chart import TimelineChart

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.services.profile_service import ProfileService

# Inspector opens by default only when the page is at least this wide at first
# show; narrower windows start collapsed so the chart keeps room (DEC-182, 3A).
_INSPECTOR_WIDE_THRESHOLD_PX = 1100

# DEC-245: settings store the chart mode as its string value — the settings layer
# must not import a UI-facing service — so the page owns the lookup back.
_CHART_MODE_BY_VALUE = {m.value: m for m in ChartMode}


class DashboardPage(QWidget):
    """Landing page showing fan speeds, temperatures, and profile status."""

    # DEC-206: the no-hardware "what to do next" button was clicked — main_window
    # switches to the Hardware page (the merged readiness view, DEC-212). The
    # cooling-readiness *chip* moved to the always-visible footer (DEC-222).
    open_readiness = Signal()

    # DEC-222: a fan card's Edit was clicked — main_window opens the Controls page
    # and focuses that control. Carries "" for the Unassigned card, which has no
    # control to focus.
    open_control = Signal(str)

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
        self._displayable_fan_keys: list[str] = []  # Fan series keys for selection
        self._has_data = False
        # Chart first-run seeding + poll-diff annotation state (DEC-181).
        self._seen_sensors = False
        self._seen_fans = False
        # Session-local mirror of settings.chart_series_seeded (DEC-244). A demo
        # session seals that key, so the persisted flag never flips and the
        # first-run seeding would re-fire on every 1 Hz tick, stamping the
        # curated subset back over whatever the user selected mid-session.
        self._chart_defaults_seeded = False
        # DEC-245: coalesce a wheel-scroll over the Range combo into one write.
        self._pending_range_index: int | None = None
        self._prev_connection = state.connection if state else None
        self._last_override_ids: set[str] = set()
        self._last_stale_sensor_ids: set[str] = set()
        self._last_stalled_fan_ids: set[str] = set()
        # Live fan cards keyed by control id (DEC-222). Reconciled in place each
        # poll rather than rebuilt, so a 1 Hz refresh never destroys and recreates
        # widgets the user may be interacting with.
        self._fan_cards: dict[str, FanControlCard] = {}

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
            self._state.connection_changed.connect(self._on_connection_changed)
            self._state.mode_changed.connect(self._on_mode_changed)
            self._state.capabilities_updated.connect(self._on_capabilities_updated)
            self._state.status_updated.connect(self._on_status_updated)
            self._state.fan_alias_changed.connect(self._on_fan_alias_changed)

        # Keep the dashboard profile combo in sync with changes made elsewhere
        # (e.g. profile CRUD / activation on the Controls page). profiles_changed
        # rebuilds the item list — previously the combo was populated once at
        # startup and went stale after any create/rename/delete; active_changed
        # re-selects by id (complements the by-name AppState.active_profile_changed
        # wiring above).
        if self._profile_service:
            self._profile_service.profiles_changed.connect(self.populate_profiles)
            self._profile_service.active_changed.connect(self._on_active_id_changed)

        self._range_write_timer = QTimer(self)
        self._range_write_timer.setObjectName("Dashboard_Timer_rangeWrite")
        self._range_write_timer.setSingleShot(True)
        self._range_write_timer.setInterval(400)
        self._range_write_timer.timeout.connect(self._persist_chart_range)

        # Chart refresh timer — visibility-gated for performance (R48)
        self._chart_timer = QTimer(self)
        self._chart_timer.setInterval(1000)
        self._chart_timer.timeout.connect(self._chart.update_chart)
        self._chart_timer.start()
        self._chart_active_interval = 1000  # 1Hz when active
        self._chart_background_interval = 5000  # 0.2Hz when app unfocused

        # Owned single-shot timer that restores the Apply button caption after a
        # transient "Applied!"/"Failed" state. Owned (not QTimer.singleShot) so
        # cleanup() can cancel a pending fire — an uncancellable singleShot could
        # otherwise reach _reset_apply_btn on an already-torn-down widget.
        self._reset_apply_timer = QTimer(self)
        self._reset_apply_timer.setSingleShot(True)
        self._reset_apply_timer.setInterval(1500)
        self._reset_apply_timer.timeout.connect(self._reset_apply_btn)

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
        self._chart.set_theme(tokens)
        # DEC-213: recolour the Telemetry Stage legend swatches for the new palette
        # (they are built once, so a theme switch would otherwise leave them stale).
        self._legend_temp_chip.setStyleSheet(
            f"background:{tokens.accent_primary}; border-radius:2px;"
        )
        self._legend_rpm_chip.setStyleSheet(
            f"background:{tokens.text_secondary}; border-radius:2px;"
        )
        # DEC-222: the fan cards paint their curve preview themselves, so they
        # need the new palette too (they are built once and updated in place).
        for card in self._fan_cards.values():
            card.set_theme(tokens)

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
        try:
            socket_path = (
                self._client.socket_path if self._client is not None else DEFAULT_SOCKET_PATH
            )
            state = check_daemon_service_state(socket_path)
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug("daemon service-state probe failed: %s", e)
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
        # The subsystem status + reason are daemon-supplied; render verbatim so
        # a stray '<...>' in a reason string can't be reinterpreted as rich text.
        self._sub_openfan_label.setTextFormat(Qt.TextFormat.PlainText)
        sub_layout.addWidget(self._sub_openfan_label)

        self._sub_hwmon_label = QLabel("hwmon: unknown")
        self._sub_hwmon_label.setObjectName("Dashboard_Label_subHwmon")
        self._sub_hwmon_label.setTextFormat(Qt.TextFormat.PlainText)
        sub_layout.addWidget(self._sub_hwmon_label)

        # 279-a: the daemon's fourth subsystem (daemon >= 2.22.0, DEC-279) — is
        # every control actually being commanded? Unlike its two siblings this has
        # no capabilities-derived baseline, so it starts hidden and appears only
        # while something is wrong. That also keeps the frame quiet on the healthy
        # machine, which is every machine most of the time.
        self._sub_controls_label = QLabel("")
        self._sub_controls_label.setObjectName("Dashboard_Label_subControls")
        self._sub_controls_label.setTextFormat(Qt.TextFormat.PlainText)
        self._sub_controls_label.setVisible(False)
        sub_layout.addWidget(self._sub_controls_label)

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
            "2. Missing motherboard sensor driver? Open the Hardware page — its "
            "readiness report names the exact kernel module or AUR package your "
            "board needs (the manual's Setup Checklist page has the full ordered "
            "walkthrough)\n"
            "3. Using an OpenFan controller? The daemon service accesses serial ports "
            "itself — it ships with the 'uucp' group on Arch / CachyOS; Debian / "
            "Ubuntu installs may need a 'dialout' drop-in (see the daemon docs)"
        )
        next_msg.setWordWrap(True)
        next_msg.setProperty("class", "PageSubtitle")
        next_layout.addWidget(next_msg)

        readiness_btn = QPushButton("Open Hardware readiness")
        readiness_btn.setObjectName("Dashboard_Btn_openReadiness")
        readiness_btn.clicked.connect(self.open_readiness.emit)
        next_layout.addWidget(readiness_btn)

        layout.addWidget(next_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        return container

    def _build_live_content(self) -> QWidget:
        """The live surface (DEC-222): telemetry graph on top, fan cards and the
        sensors rail below.

        The graph is the primary component — it gets the top of the vertical
        splitter at full width. Beneath it, a horizontal splitter carries the
        per-control fan cards on the left and the Thermal Sensors rail on the
        right. The former summary cards, Fan Array, Fan Zone grid, raw fan table,
        Quick Actions and Alerts panels were all removed with this rebuild.
        """
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 16, 24, 16)
        content_layout.setSpacing(12)

        # Title row + the profile selector. The sidebar also carries a profile
        # combo; this one is kept deliberately so the landing page can answer
        # "what profile is active, and how do I change it?" without navigating.
        title_row = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setProperty("class", "PageTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self._profile_combo = QComboBox()
        self._profile_combo.setObjectName("Dashboard_Combo_profile")
        # Sits in the title row with no label of its own — without a name it
        # announces only the profile it happens to be showing (273-g).
        name_value_control(self._profile_combo, "Active profile")
        self._profile_combo.setMinimumWidth(160)
        title_row.addWidget(self._profile_combo)
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setObjectName("Dashboard_Btn_apply")
        self._apply_btn.clicked.connect(self._on_apply_profile)
        title_row.addWidget(self._apply_btn)
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
        # when the daemon's thermal_state leaves "normal" (thermal emergency /
        # recovery). Poll is the authoritative transition source now the GUI
        # has no control loop watching thermal_state itself.
        self._thermal_banner = ErrorBanner()
        self._thermal_banner.setObjectName("Dashboard_Banner_thermal")
        content_layout.addWidget(self._thermal_banner)
        self._last_thermal_state = "normal"

        # Engine liveness (DEC-249), surfaced the same way and for the same
        # reason as the thermal banner above: poll is the authoritative source,
        # and the GUI has no loop of its own to notice.
        #
        # This is a BANNER on the live page, not a chip on the "Subsystem
        # Status" card — that card is built inside `_build_no_hardware_state`,
        # so it renders only when no hardware was detected at all. A wedged
        # engine on a working machine would never have reached it. The release
        # review caught the missing branch; the card it would have gone in was
        # the deeper half of the same bug.
        self._engine_banner = ErrorBanner()
        self._engine_banner.setObjectName("Dashboard_Banner_engine")
        content_layout.addWidget(self._engine_banner)
        self._last_engine_status = "ok"

        # Daemon running on fallback settings (DEC-321 / `WIRE-a`), surfaced the
        # same way and for the same reason as the two banners above: /poll is the
        # authoritative source and the GUI has no loop of its own to notice.
        self._runtime_config_banner = ErrorBanner()
        self._runtime_config_banner.setObjectName("Dashboard_Banner_runtime_config")
        content_layout.addWidget(self._runtime_config_banner)
        # Poll-diff key, not a bool: a repaired daemon reconnecting must clear the
        # banner, and a *different* degradation must re-raise it.
        self._last_runtime_config_key: tuple[str, str, str] | None = None

        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._v_splitter.setObjectName("Dashboard_Splitter_vertical")
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._h_splitter.setObjectName("Dashboard_Splitter_horizontal")
        style_splitter(self._v_splitter)  # shared resize-handle convention (DEC-234)
        style_splitter(self._h_splitter)

        # ── Telemetry Stage (v_splitter top): header + legend + the chart ──
        telemetry_stage = QWidget()
        telemetry_stage.setObjectName("Dashboard_Section_telemetry")
        telemetry_layout = QVBoxLayout(telemetry_stage)
        telemetry_layout.setContentsMargins(0, 0, 0, 0)
        telemetry_layout.setSpacing(6)
        telemetry_header = SectionHeader(
            "Telemetry Stage", object_name="Dashboard_SectionHeader_telemetry"
        )
        _tokens = active_theme()
        _temp_legend, self._legend_temp_chip = self._make_legend(
            "Temperature (°C)", _tokens.accent_primary
        )
        _rpm_legend, self._legend_rpm_chip = self._make_legend("RPM", _tokens.text_secondary)
        telemetry_header.add_trailing(_temp_legend)
        telemetry_header.add_trailing(_rpm_legend)
        telemetry_layout.addWidget(telemetry_header)

        color_overrides = {}
        if self._settings_service:
            color_overrides = dict(self._settings_service.settings.series_colors)
        self._chart = TimelineChart(
            self._history, selection=self._selection, color_overrides=color_overrides
        )
        if self._settings_service:
            s = self._settings_service.settings
            self._chart.set_range_index(s.chart_default_range_index)
            # DEC-245: the Range combo used to be session-local — the persisted
            # value was applied here and every later change discarded.
            self._chart.range_changed.connect(self._on_range_changed)
            # Restore the mode *label* only. The saved hidden set is already this
            # mode's result plus any later tweaks, so re-applying the preset would
            # throw those away; the divergence was that the label reset while the
            # data did not.
            mode = _CHART_MODE_BY_VALUE.get(s.chart_mode)
            if mode is not None:
                self._selection.restore_mode(mode)
                self._chart.set_mode(mode)
        # Chart modes/reset (DEC-181): the chart is dumb about sensor kinds, so it
        # emits the choice and the page applies it (COMBINED is the curated subset).
        self._chart.mode_selected.connect(self._on_chart_mode_selected)
        self._chart.reset_requested.connect(self._on_chart_reset)
        self._push_chart_context()
        self._chart.setMinimumHeight(180)
        self._chart.setMinimumWidth(320)  # the rail can't crush the chart
        telemetry_layout.addWidget(self._chart, 1)
        self._v_splitter.addWidget(telemetry_stage)

        # ── Fan cards pane (h_splitter left) ──
        # One card per logical control (DEC-222), in a flow layout so the
        # collection reflows responsively as the pane is resized.
        fan_pane = QWidget()
        fan_pane.setObjectName("Dashboard_Pane_fanCards")
        fan_layout = QVBoxLayout(fan_pane)
        fan_layout.setContentsMargins(0, 0, 0, 0)
        fan_layout.setSpacing(6)
        fan_header = SectionHeader("Fans", object_name="Dashboard_SectionHeader_fans")
        self._fan_count_label = QLabel("")
        self._fan_count_label.setObjectName("Dashboard_Label_fanCount")
        self._fan_count_label.setProperty("class", "CardMeta")
        fan_header.add_trailing(self._fan_count_label)
        fan_layout.addWidget(fan_header)

        self._fan_cards_host = QWidget()
        self._fan_cards_host.setObjectName("Dashboard_Host_fanCards")
        self._fan_cards_layout = FlowLayout(
            self._fan_cards_host, margin=0, h_spacing=8, v_spacing=8
        )
        self._fan_cards_empty = QLabel("No controllable fans detected.")
        self._fan_cards_empty.setObjectName("Dashboard_Label_fanCardsEmpty")
        self._fan_cards_empty.setProperty("class", "CardMeta")

        fan_scroll = QScrollArea()
        fan_scroll.setObjectName("Dashboard_ScrollArea_fanCards")
        fan_scroll.setWidgetResizable(True)
        fan_scroll.setFrameShape(QFrame.Shape.NoFrame)
        fan_scroll.setWidget(self._fan_cards_host)
        fan_scroll.setMinimumHeight(80)
        fan_layout.addWidget(self._fan_cards_empty)
        fan_layout.addWidget(fan_scroll, 1)
        self._h_splitter.addWidget(fan_pane)

        # ── Thermal Sensors rail (h_splitter right) ──
        # Always present now; the splitter handle is how the chart reclaims width
        # on a narrow window, so the old show/hide toggle is gone with the strip.
        self._sensor_panel = SensorSeriesPanel(self._selection, state=self._state)
        if self._settings_service:
            self._sensor_panel.hide_igpu = self._settings_service.settings.hide_igpu_sensors
        self._sensor_panel.set_chart(self._chart, self._settings_service)
        self._inspector = DashboardInspector(self._sensor_panel)
        self._h_splitter.addWidget(self._inspector)

        self._h_splitter.setStretchFactor(0, 3)
        self._h_splitter.setStretchFactor(1, 1)
        self._h_splitter.setSizes([800, 300])
        self._h_splitter.setCollapsible(0, False)
        self._h_splitter.setCollapsible(1, False)

        # Graph first (primary), fan cards + rail below.
        self._v_splitter.addWidget(self._h_splitter)
        self._v_splitter.setStretchFactor(0, 3)
        self._v_splitter.setStretchFactor(1, 2)
        self._v_splitter.setSizes([460, 340])
        content_layout.addWidget(self._v_splitter, 1)

        # Populate the profile combo (kept current thereafter by profiles_changed).
        self.populate_profiles()
        self._refresh_fan_cards()

        return content

    def _make_legend(self, text: str, color: str) -> tuple[QWidget, QFrame]:
        """Build a small legend key (colour chip + label) for the Telemetry Stage
        header. Returns the widget and its chip so ``set_theme`` can recolour it.

        The two keys (Temperature / RPM) map to the chart's real dual axes — an
        honest legend, not invented data.
        """
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        chip = QFrame()
        chip.setFixedSize(10, 10)
        chip.setStyleSheet(f"background:{color}; border-radius:2px;")
        label = QLabel(text)
        label.setProperty("class", "CardMeta")
        layout.addWidget(chip)
        layout.addWidget(label)
        return widget, chip

    # ─── Signal handlers ─────────────────────────────────────────────

    def _on_connection_changed(self, state: ConnectionState) -> None:
        # (Re)connect transition → annotation (poll-diff, DEC-181).
        if (
            state == ConnectionState.CONNECTED
            and self._prev_connection != ConnectionState.CONNECTED
        ):
            self._annotate("Connected")
        self._prev_connection = state
        if state == ConnectionState.DISCONNECTED:
            self._has_data = False
            self._clear_fan_cards()
            self._stack.setCurrentIndex(self._IDX_DISCONNECTED)
            self._refresh_service_hint()
        elif state == ConnectionState.CONNECTED and not self._has_data:
            self._stack.setCurrentIndex(self._IDX_NO_HARDWARE)

    def _on_mode_changed(self, mode: OperationMode) -> None:
        del mode  # the footer renders the mode word (DEC-222)
        self._push_chart_context()

    # ─── Chart series: known keys, curated subset, modes (DEC-181) ────

    def _register_known_keys(self) -> None:
        """Push the displayable sensor + fan keys into the selection model — the
        single source for both poll handlers."""
        keys = [f"sensor:{sid}" for sid in self._sensor_panel.displayed_sensor_ids()]
        keys += self._displayable_fan_keys
        self._selection.update_known_keys(keys)

    def _curated_chart_keys(self) -> set[str]:
        """The curated default chart series for the current sensors.

        Thin wrapper over :func:`series_selection.default_series_keys`, which owns
        the kind-aware selection. DEC-222 removed the summary cards that used to
        let the user pin a specific sensor per category, so the curated subset is
        now always the auto-match-by-kind result.
        """
        return default_series_keys(self._state.sensors if self._state else [])

    def _maybe_seed_chart_defaults(self) -> None:
        """First-run only (A-fork DEC-181): once BOTH sensors and fans have been
        seen, declutter the chart to the curated subset and latch
        ``chart_series_seeded`` so a returning user who chose "show all" is never
        re-decluttered. Skipped entirely without a settings service or once seeded."""
        if self._chart_defaults_seeded:
            return
        if not self._settings_service or self._settings_service.settings.chart_series_seeded:
            return
        if not (self._seen_sensors and self._seen_fans):
            return
        self._selection.apply_mode(ChartMode.COMBINED, self._curated_chart_keys())
        self._chart.set_mode(ChartMode.COMBINED)
        self._chart_defaults_seeded = True
        self._settings_service.update(chart_series_seeded=True)

    def _on_range_changed(self, index: int) -> None:
        """Debounce the Range combo (DEC-245).

        The only one of this release's write paths that was undebounced, and the
        one most exposed to it: a QComboBox changes index on every wheel notch, so
        a single scroll over the Range control produced five to ten whole-file
        writes on the GUI thread — each an asdict + json.dumps + mkstemp + two
        fsyncs. Cheap on tmpfs, a visible hitch on a loaded real filesystem.
        """
        self._pending_range_index = index
        self._range_write_timer.start()

    def _persist_chart_range(self) -> None:
        if self._settings_service and self._pending_range_index is not None:
            self._settings_service.update(chart_default_range_index=self._pending_range_index)

    def _on_chart_mode_selected(self, mode: ChartMode) -> None:
        curated = self._curated_chart_keys() if mode == ChartMode.COMBINED else None
        self._selection.apply_mode(mode, curated)
        if self._settings_service:
            self._settings_service.update(chart_mode=mode.value)

    def _on_chart_reset(self) -> None:
        """Reset-to-default: restore the curated Combined subset and reflect it in
        the selector (refinement §11).

        Persists the mode for the same reason `_on_chart_mode_selected` does. Reset
        is a second way to change it, and leaving it out reintroduced the very
        divergence DEC-245 exists to close: reset while in Fans, and the next launch
        showed "Fans" in the selector over Combined data.
        """
        self._selection.apply_mode(ChartMode.COMBINED, self._curated_chart_keys())
        self._chart.set_mode(ChartMode.COMBINED)
        if self._settings_service:
            self._settings_service.update(chart_mode=ChartMode.COMBINED.value)

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
        vm = build_capabilities_vm(caps)
        self._sub_openfan_label.setText(vm.openfan.text)
        self._sub_openfan_label.setProperty("class", vm.openfan.css_class)
        self._sub_hwmon_label.setText(vm.hwmon.text)
        self._sub_hwmon_label.setProperty("class", vm.hwmon.css_class)
        for lbl in (self._sub_openfan_label, self._sub_hwmon_label):
            repolish(lbl)

        # Hwmon info/warning banner on the live page (None \u2192 hide).
        if vm.hwmon_banner is None:
            self._hwmon_banner.hide_banner()
        elif vm.hwmon_banner.kind == "info":
            self._hwmon_banner.show_info(vm.hwmon_banner.message, auto_dismiss_ms=0)
        else:
            self._hwmon_banner.show_warning(vm.hwmon_banner.message, auto_dismiss_ms=0)

        # API-version-skew guard (the warning-store + log side effects stay here).
        if vm.api_skew_message is None:
            self._api_version_banner.hide_banner()
            self._state.remove_warning("api_version_skew")
        else:
            import logging

            self._api_version_banner.show_warning(vm.api_skew_message, auto_dismiss_ms=0)
            self._state.add_warning(
                level="warning", source="api", message=vm.api_skew_message, key="api_version_skew"
            )
            logging.getLogger(__name__).warning(
                "API version skew: daemon reports api_version=%d, GUI expects %d",
                caps.api_version,
                EXPECTED_API_VERSION,
            )

    def _on_status_updated(self, status: DaemonStatus) -> None:
        # Thermal-protection transition (poll-diff): the daemon's thermal
        # emergency / recovery overrides fan control. Surface it the moment
        # thermal_state leaves "normal", and clear it on the return.
        thermal = status.thermal_state or "normal"
        # The thermal chip + cooling-readiness chip live on the footer now
        # (DEC-222); this page keeps only the transition banner + annotation.
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

        # Engine liveness (DEC-249). The profile engine is the sole PWM writer
        # and owns the thermal emergency, so its death is the most consequential
        # thing this page can report — and the daemon reports it ONLY here, as a
        # subsystem entry. Older daemons omit it; absence is not a failure.
        # Wire values are ok | warn | crit (daemon `HealthStatus::Display`). The
        # three must NOT collapse into one message, and that is DEC-259's whole
        # point: the daemon added `warn` precisely because reporting a slow tick
        # as a dead engine is, in its own words, "exactly inverted". The
        # canonical slow tick is the thermal force walking ten OpenFan
        # channels at up to a second each, so the critical wording would claim
        # thermal protection was off, and tell the user to restart the sole PWM
        # writer, at the exact moment it was saving their hardware.
        engine = next((s for s in status.subsystems if s.name == "engine"), None)
        engine_status = engine.status if engine else "ok"
        if engine_status != self._last_engine_status:
            self._last_engine_status = engine_status
            self._annotate(f"Engine: {engine_status}")
            reason = f" ({engine.reason})" if engine and engine.reason else ""
            if engine_status == "ok":
                self._engine_banner.hide_banner()
            elif engine_status == "warn":
                # Running, just late. Say so, and prescribe nothing.
                self._engine_banner.show_warning(
                    f"Fan control engine is running slowly{reason}. Fans are still "
                    "being driven and thermal protection is active \u2014 no action "
                    "needed unless this persists."
                )
            else:
                # crit \u2014 and anything a future daemon adds, because silence
                # about a dead engine is the worse failure.
                self._engine_banner.show_error(
                    f"Fan control engine has stopped{reason} \u2014 the daemon is not "
                    "driving your fans, and its thermal emergency protection is not "
                    "running. Restart control-ofc-daemon."
                )

        # Daemon runtime-config degradation (DEC-321 / `WIRE-a`). **[SAFETY]:**
        # the daemon fell back to built-in defaults, which carry no
        # `header_roles` — so a pump role the user assigned by hand is gone,
        # taking its 30% floor, its stop exemption and its pump-safe identify
        # with it. Before this field the daemon's entire notification was one
        # `warn!` in its journal and no client could tell.
        #
        # Poll-diff rather than set-every-tick, and that is not a micro-
        # optimisation: `ErrorBanner.show_warning` calls `set_chip_class`, which
        # repolishes the widget, and this slot runs at 1 Hz. The field is sticky
        # for the daemon's lifetime so there is normally exactly one transition —
        # but keying on the whole identity also clears the banner when a repaired
        # daemon reconnects, and re-raises it if the degradation changes.
        degraded = status.runtime_config_degraded
        rc_key = (degraded.reason, degraded.path, degraded.phase) if degraded is not None else None
        if rc_key != self._last_runtime_config_key:
            self._last_runtime_config_key = rc_key
            rc_message = runtime_config_degraded_message(degraded)
            # Guarded on `degraded` as well as on the message. The `else` branch
            # dereferences `degraded`, and "a message implies a degradation" is a
            # cross-file invariant of the view model — if that ever stopped
            # holding (an unrecognised shape returning None, say) this slot would
            # raise `AttributeError` at 1 Hz. Degrade to "hide" instead.
            if degraded is None or rc_message is None:
                self._runtime_config_banner.hide_banner()
                self._state.remove_warning("runtime_config_degraded")
            else:
                import logging

                self._runtime_config_banner.show_warning(rc_message, auto_dismiss_ms=0)
                self._state.add_warning(
                    level="warning",
                    source="daemon",
                    message=rc_message,
                    key="runtime_config_degraded",
                )
                self._annotate("Daemon config: degraded")
                # `detail` is verbatim daemon prose and can be a multi-line TOML
                # parse error, so it is logged rather than pushed into a banner —
                # the same split the API-skew guard above uses.
                logging.getLogger(__name__).warning(
                    "Daemon runtime config degraded: reason=%s phase=%s path=%s detail=%s",
                    degraded.reason,
                    degraded.phase,
                    degraded.path,
                    degraded.detail,
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

        # 279-a: hide first, then let the loop below re-raise it. Its two
        # siblings are repainted from the capabilities VM on every refresh, so a
        # poll that finds them healthy can leave them alone — nothing else ever
        # writes THIS label, so it needs both an `ok` path and an ABSENT path.
        # Absent matters on a reconnect to a pre-2.22.0 daemon, which sends three
        # subsystems: without this the chip would pin the last warning for the
        # rest of the session, long after the control was fixed or the daemon
        # that reported it was replaced.
        self._sub_controls_label.setVisible(False)
        for sub in status.subsystems:
            if sub.name == "openfan" and sub.status != "ok":
                reason = f" ({sub.reason})" if sub.reason else ""
                self._sub_openfan_label.setText(f"OpenFan: {sub.status}{reason}")
                set_chip_class(self._sub_openfan_label, "WarningChip")
            elif sub.name == "hwmon" and sub.status != "ok":
                reason = f" ({sub.reason})" if sub.reason else ""
                self._sub_hwmon_label.setText(f"hwmon: {sub.status}{reason}")
                set_chip_class(self._sub_hwmon_label, "WarningChip")
            elif sub.name == "controls" and sub.status != "ok":
                reason = f" ({sub.reason})" if sub.reason else ""
                self._sub_controls_label.setText(f"Controls: {sub.status}{reason}")
                set_chip_class(self._sub_controls_label, "WarningChip")
                self._sub_controls_label.setVisible(True)

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
        self._reset_apply_timer.stop()
        # DEC-257: FLUSH, don't just stop. The chart-range write is debounced by
        # 400 ms, so closing within that window silently discarded the user's
        # last range change — the fifth instance of the DEC-245 pattern, where a
        # pending debounce is dropped at teardown instead of being committed.
        # Stopping a timer whose payload has not run is data loss, not cleanup.
        if self._range_write_timer.isActive():
            self._range_write_timer.stop()
            self._persist_chart_range()
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

        self._refresh_fan_cards()

        # Store displayable fan keys and re-register (folds in sensors + aggregate)
        self._displayable_fan_keys = [f"fan:{f.id}:rpm" for f in display_fans]
        self._register_known_keys()

        self._maybe_seed_chart_defaults()

    # ─── Fan cards (DEC-222) ─────────────────────────────────────────

    def _refresh_fan_cards(self) -> None:
        """Reconcile the fan cards against the latest readings + active profile.

        Cards are keyed by control id and updated in place; only genuinely new or
        departed controls cause a widget to be created or destroyed. The same
        trigger fires on poll, profile change and fan rename, so the cards always
        reflect current intent. Cheap and idempotent.
        """
        if not self._state:
            return
        hide_unused = (
            self._settings_service.settings.hide_unused_fan_headers
            if self._settings_service
            else True
        )
        display_fans = filter_displayable_fans(
            self._state.fans or [], self._state.fan_aliases, hide_unused
        )
        profile = self._profile_service.active_profile if self._profile_service else None
        status = self._state.daemon_status
        vms = build_fan_card_vms(
            display_fans,
            active_profile=profile,
            overrides=status.overrides if status else [],
            headers=self._state.hwmon_headers,
            caps=self._state.capabilities,
            sensor_values={s.id: s.value_c for s in (self._state.sensors or [])},
            display_name=self._state.fan_display_name,
        )

        # Keyed by vm.card_key, not vm.control_id: a malformed profile can repeat
        # a control id, and keying on it would make the second card overwrite the
        # first instead of getting its own.
        seen: set[str] = set()
        for vm in vms:
            seen.add(vm.card_key)
            card = self._fan_cards.get(vm.card_key)
            if card is None:
                card = FanControlCard(vm)
                card.edit_requested.connect(self.open_control)
                card.rename_requested.connect(self._rename_fan)
                self._fan_cards[vm.card_key] = card
                self._fan_cards_layout.addWidget(card)
            else:
                card.update_vm(vm)

        for key in [k for k in self._fan_cards if k not in seen]:
            self._drop_fan_card(key)

        # Keep the layout order in step with the VM order (controls in profile
        # order, then Unassigned, then read-only fans). New cards are appended, so
        # without this a card created first stays first forever — starting with no
        # profile and then activating one would pin the Unassigned card above every
        # control, permanently inverting the documented order.
        for index, vm in enumerate(vms):
            card = self._fan_cards[vm.card_key]
            item = self._fan_cards_layout.itemAt(index)
            if item is None or item.widget() is not card:
                self._fan_cards_layout.removeWidget(card)
                self._fan_cards_layout.insertWidget(index, card)

        # Count only genuine controls: the Unassigned pseudo-card and the per-fan
        # read-only cards are not controls, and calling them that would overstate
        # how much of the system is actually under curve control.
        total_fans = sum(vm.fan_count for vm in vms)
        controls = sum(1 for vm in vms if not vm.is_unassigned and not vm.is_read_only)
        self._fan_count_label.setText(
            f"{controls} control{'' if controls == 1 else 's'} · "
            f"{total_fans} fan{'' if total_fans == 1 else 's'}"
        )
        self._fan_cards_empty.setVisible(not vms)

    def _drop_fan_card(self, card_key: str) -> None:
        """Remove one card, detaching it from the flow layout before deletion."""
        card = self._fan_cards.pop(card_key, None)
        if card is None:
            return
        self._fan_cards_layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()

    def _clear_fan_cards(self) -> None:
        """Drop every card — a stale card must not survive a disconnect."""
        for key in list(self._fan_cards):
            self._drop_fan_card(key)
        self._fan_count_label.setText("")
        self._fan_cards_empty.setVisible(True)

    def _on_fan_alias_changed(self, fan_id: str, display_name: str) -> None:
        del fan_id, display_name  # cards re-resolve their labels on rebuild
        self._refresh_fan_cards()

    def _rename_fan(self, fan_id: str) -> None:
        """Prompt for a new name for a read-only fan card (DEC-227).

        A card has no in-place editor, so this uses the same modal shape as the
        Controls page's curve/profile renames. The rule itself lives in
        ``AppState.apply_fan_rename`` — shared with the Sensors rail and the
        Overview table, and testable without driving this dialog.
        """
        if not self._state:
            return
        current = self._state.fan_display_name(fan_id)
        name, ok = QInputDialog.getText(self, "Rename Fan", "Fan name:", text=current)
        if ok:
            self._state.apply_fan_rename(fan_id, name)

    def _on_profile_changed(self, name: str) -> None:
        self._annotate(f"Profile: {name}" if name else "Profile cleared")
        self._push_chart_context()
        # A different profile means different controls — rebuild the cards.
        self._refresh_fan_cards()
        # Sync combo selection to active profile
        idx = self._profile_combo.findText(name)
        if idx >= 0:
            with block_signals(self._profile_combo):
                self._profile_combo.setCurrentIndex(idx)

    def _on_apply_profile(self) -> None:
        """Apply the combo-selected profile (the page's Apply button)."""
        profile_id = self._profile_combo.currentData()
        if profile_id:
            self._activate_profile_by_id(profile_id)

    def _activate_profile_by_id(self, profile_id: str) -> None:
        """Activate ``profile_id`` via ProfileService (Apply button + Quick Actions).

        Delegates the save → daemon-confirm → set-active flow to the shared
        ProfileService.activate(). On failure it reverts the combo to the
        previously-active profile (bug fix — the combo used to stay on the failed
        pick) and surfaces the real reason to the log (previously lost)."""
        import logging

        log = logging.getLogger(__name__)

        if not self._profile_service or not profile_id:
            return

        # Capture the active id up-front so a rejected switch reverts cleanly.
        prev_active_id = self._profile_service.active_id
        res = self._profile_service.activate(profile_id, client=self._client)
        if not res.activated:
            log.warning("Profile activation failed for %s: %s", profile_id, res.error)
            self._revert_profile_combo(prev_active_id)
            self._apply_btn.setText("Failed")
            self._apply_btn.setEnabled(False)
            self._reset_apply_timer.start()
            return

        # Mirror the new active into AppState so the whole UI reflects it.
        target = self._profile_service.get_profile(profile_id)
        if self._state and target:
            self._state.set_active_profile(target.name)
        # The daemon re-evaluates the activated profile itself (DEC-165); the
        # GUI no longer forces a local control-loop re-evaluation.
        self._apply_btn.setText("Applied!")
        self._apply_btn.setEnabled(False)
        self._reset_apply_timer.start()

    def _revert_profile_combo(self, profile_id: str) -> None:
        """Re-select ``profile_id`` in the combo, blocking signals so the
        reversion never re-triggers the apply handler."""
        idx = self._profile_combo.findData(profile_id)
        if idx >= 0:
            with block_signals(self._profile_combo):
                self._profile_combo.setCurrentIndex(idx)

    def _on_active_id_changed(self, profile_id: str) -> None:
        """Reflect a service-side active-profile change in the combo by id
        (blocking signals so it never re-triggers apply)."""
        idx = self._profile_combo.findData(profile_id)
        if idx >= 0:
            with block_signals(self._profile_combo):
                self._profile_combo.setCurrentIndex(idx)

    def _reset_apply_btn(self) -> None:
        self._apply_btn.setText("Apply")
        self._apply_btn.setEnabled(True)

    def populate_profiles(self) -> None:
        """(Re)build the profile combo from ProfileService, tagging each item with
        its profile id (userData) so the apply flow is by-id, not by-name.

        Wired to ``ProfileService.profiles_changed`` so profile CRUD on the
        Controls page keeps this combo current. Preserves the current selection
        across the rebuild where possible, else falls back to the active
        profile."""
        if not self._profile_service:
            return
        with block_signals(self._profile_combo):
            prev_id = self._profile_combo.currentData()
            active = self._profile_service.active_profile
            active_id = active.id if active else ""
            target_id = prev_id or active_id
            self._profile_combo.clear()
            select_idx = -1
            for i, p in enumerate(self._profile_service.profiles):
                self._profile_combo.addItem(p.name, p.id)
                if p.id == target_id:
                    select_idx = i
            # If the remembered selection is gone, fall back to the active profile.
            if select_idx < 0 and active_id:
                select_idx = self._profile_combo.findData(active_id)
            if select_idx < 0 and self._profile_combo.count() > 0:
                select_idx = 0
            if select_idx >= 0:
                self._profile_combo.setCurrentIndex(select_idx)

    def _show_content(self) -> None:
        if not self._has_data:
            self._has_data = True
            self._stack.setCurrentIndex(self._IDX_LIVE)
