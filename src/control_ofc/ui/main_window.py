"""Main application window — sidebar + status banner + stacked pages."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from control_ofc.api.client import DaemonClient
from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.constants import PAGE_DASHBOARD, POLL_INTERVAL_MS
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import ControlLoopService
from control_ofc.services.demo_service import DemoService
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.lease_service import LeaseService
from control_ofc.services.profile_service import ProfileService
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage
from control_ofc.ui.pages.settings_page import SettingsPage
from control_ofc.ui.sidebar import Sidebar
from control_ofc.ui.status_banner import StatusBanner
from control_ofc.ui.widgets.error_banner import ErrorBanner


class MainWindow(QWidget):
    """Top-level window assembling sidebar, status banner, and page stack."""

    def __init__(
        self,
        state: AppState | None = None,
        history: HistoryStore | None = None,
        profile_service: ProfileService | None = None,
        settings_service: AppSettingsService | None = None,
        client: DaemonClient | None = None,
        control_loop: ControlLoopService | None = None,
        lease_service: LeaseService | None = None,
        demo_mode: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Control-OFC — Fan Control")
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)

        self._state = state or AppState()
        self._history = history or HistoryStore()
        self._profile_service = profile_service or ProfileService()
        self._settings_service = settings_service or AppSettingsService()
        self._client = client
        self._demo_mode = demo_mode
        self._demo_service: DemoService | None = None
        self._control_loop: ControlLoopService | None = control_loop
        self._lease_service = lease_service
        self._series_selection = SeriesSelectionModel()

        # Restore persisted settings into state
        self._state.fan_aliases = dict(self._settings_service.settings.fan_aliases)
        self._series_selection.load_hidden(self._settings_service.settings.hidden_chart_series)

        # Persist alias and series changes back to settings
        self._state.fan_alias_changed.connect(self._persist_fan_alias)
        self._series_selection.selection_changed.connect(self._persist_series_selection)

        # --- Status banner + error banner ---
        self.status_banner = StatusBanner()
        self.error_banner = ErrorBanner()

        # --- Sidebar ---
        self.sidebar = Sidebar()

        # --- Page stack ---
        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("MainWindow_Stack_pages")

        self.dashboard_page = DashboardPage(
            state=self._state,
            history=self._history,
            selection=self._series_selection,
            profile_service=self._profile_service,
            settings_service=self._settings_service,
            client=self._client,
        )
        self.controls_page = ControlsPage(
            state=self._state,
            profile_service=self._profile_service,
            client=self._client,
            control_loop=self._control_loop,
            lease_service=self._lease_service,
            settings_service=self._settings_service,
        )
        self.settings_page = SettingsPage(
            state=self._state, settings_service=self._settings_service
        )
        self.diagnostics_page = DiagnosticsPage(
            state=self._state,
            settings_service=self._settings_service,
            profile_service=self._profile_service,
        )

        self.page_stack.addWidget(self.dashboard_page)
        self.page_stack.addWidget(self.controls_page)
        self.page_stack.addWidget(self.settings_page)
        self.page_stack.addWidget(self.diagnostics_page)

        # --- Layout ---
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.status_banner)
        content_layout.addWidget(self.error_banner)
        content_layout.addWidget(self.page_stack, 1)

        content_container = QWidget()
        content_container.setLayout(content_layout)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(content_container, 1)

        # --- Signals ---
        self.sidebar.page_changed.connect(self._on_page_changed)
        self.settings_page.theme_changed.connect(self._on_theme_changed)

        # State → status banner
        self._state.connection_changed.connect(self.status_banner.set_connection_state)
        self._state.connection_changed.connect(self._on_connection_changed)
        self._state.mode_changed.connect(self.status_banner.set_operation_mode)
        self._state.active_profile_changed.connect(self.status_banner.set_active_profile)
        self._state.warning_count_changed.connect(self.status_banner.set_warning_count)

        # --- Restore persisted window state ---
        s = self._settings_service.settings
        if s.restore_last_page:
            idx = max(0, min(s.last_page_index, self.page_stack.count() - 1))
            self.page_stack.setCurrentIndex(idx)
            self.sidebar.select_page(idx)
        else:
            self.page_stack.setCurrentIndex(PAGE_DASHBOARD)
            self.sidebar.select_page(PAGE_DASHBOARD)
        geo = s.window_geometry
        if len(geo) == 4:
            self.setGeometry(geo[0], geo[1], geo[2], geo[3])

        # Wire dashboard "Open Diagnostics" to sidebar navigation
        self.dashboard_page.open_diagnostics.connect(self._open_diagnostics)

        # Populate dashboard profile selector
        self.dashboard_page.populate_profiles()

        if demo_mode:
            self._start_demo_mode()
        else:
            self._state.set_connection(ConnectionState.DISCONNECTED)
            self._state.set_mode(OperationMode.READ_ONLY)

    def _on_page_changed(self, page_id: int) -> None:
        self.page_stack.setCurrentIndex(page_id)

    def _on_theme_changed(self, tokens) -> None:
        from PySide6.QtWidgets import QApplication

        from control_ofc.ui.theme import apply_theme_font, build_stylesheet

        app = QApplication.instance()
        if app:
            app.setStyleSheet(build_stylesheet(tokens))
        apply_theme_font(tokens)
        self.controls_page.set_theme(tokens)

    def _on_connection_changed(self, state: ConnectionState) -> None:
        if state == ConnectionState.DISCONNECTED:
            self.error_banner.show_warning("Daemon disconnected — retrying...")
        elif state == ConnectionState.CONNECTED:
            self.error_banner.show_info("Connected to daemon", auto_dismiss_ms=3000)

    def _on_control_loop_status(self, status) -> None:
        if status.control_outputs:
            self.controls_page.update_control_outputs(status.control_outputs)

    def _open_diagnostics(self) -> None:
        from control_ofc.constants import PAGE_DIAGNOSTICS

        self.page_stack.setCurrentIndex(PAGE_DIAGNOSTICS)
        self.sidebar.select_page(PAGE_DIAGNOSTICS)

    def _start_demo_mode(self) -> None:
        self._demo_service = DemoService()
        self._state.set_mode(OperationMode.DEMO)
        self._state.set_connection(ConnectionState.CONNECTED)
        self._state.fan_aliases = DemoService.fan_aliases()

        # Start control loop in demo mode
        self._control_loop = ControlLoopService(
            state=self._state,
            profile_service=self._profile_service,
            demo_service=self._demo_service,
        )
        self._control_loop.status_changed.connect(self._on_control_loop_status)
        self._control_loop.start()

        # Load initial demo data
        self._state.set_capabilities(self._demo_service.capabilities())
        self._state.set_status(self._demo_service.status())
        self._state.set_hwmon_headers(self._demo_service.hwmon_headers())
        self._state.set_lease(self._demo_service.lease_status())

        # Demo polling timer
        self._demo_timer = QTimer(self)
        self._demo_timer.setInterval(POLL_INTERVAL_MS)
        self._demo_timer.timeout.connect(self._demo_tick)
        self._demo_timer.start()
        self._demo_tick()

    def _persist_fan_alias(self, _fan_id: str, _display_name: str) -> None:
        self._settings_service.update(fan_aliases=dict(self._state.fan_aliases))

    def _persist_series_selection(self) -> None:
        hidden = list(self._series_selection.to_dict()["hidden_keys"])
        self._settings_service.update(hidden_chart_series=hidden)

    def _demo_tick(self) -> None:
        if self._demo_service:
            self._state.set_sensors(self._demo_service.sensors())
            self._state.set_fans(self._demo_service.fans())

    def closeEvent(self, event) -> None:
        """Persist window geometry and last page on close."""
        geo = self.geometry()
        self._settings_service.update(
            last_page_index=self.page_stack.currentIndex(),
            window_geometry=[geo.x(), geo.y(), geo.width(), geo.height()],
        )
        super().closeEvent(event)
