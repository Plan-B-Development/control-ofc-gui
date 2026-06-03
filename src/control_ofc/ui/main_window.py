"""Main application window — sidebar + status banner + stacked pages."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError
from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.constants import PAGE_DASHBOARD, POLL_INTERVAL_MS
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import ControlLoopService
from control_ofc.services.demo_service import DemoService
from control_ofc.services.diagnostics_service import DiagnosticsService
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

log = logging.getLogger(__name__)


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
        diagnostics_service: DiagnosticsService | None = None,
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
        # DEC-111: share one DiagnosticsService across the page, snapshots,
        # and the event-log view so every emitter writes to the same deque.
        # Tests construct MainWindow without one, so we fall back to a fresh
        # instance with whatever services are available.
        self._diag = diagnostics_service or DiagnosticsService(
            self._state,
            settings_service=self._settings_service,
            profile_service=self._profile_service,
        )
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
            control_loop=self._control_loop,
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
            diagnostics_service=self._diag,
            settings_service=self._settings_service,
            profile_service=self._profile_service,
            client=self._client,
            series_selection=self._series_selection,
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

        # DEC-111: surface profile + mode transitions in the event log.
        self._state.active_profile_changed.connect(self._on_active_profile_for_events)
        self._state.mode_changed.connect(self._on_mode_for_events)

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
            if self._control_loop:
                self._control_loop.status_changed.connect(self._on_control_loop_status)
                self._wire_diagnostics_to_control_loop()
            self._state.set_connection(ConnectionState.DISCONNECTED)
            self._state.set_mode(OperationMode.READ_ONLY)

        # DEC-098: surface daemon-emitted kernel-version warnings (e.g. 6.19
        # RDNA hard hang, R9700 SMU mismatch) as a one-time popup. We listen
        # on capabilities_updated rather than checking once at startup so a
        # daemon restart with new detection logic refreshes the popup state.
        self._state.capabilities_updated.connect(self._on_capabilities_updated_for_kernel_warnings)

        # DEC-102: when fresh hwmon header data arrives, sanitize any
        # profile member that targets an unknown or read-only header.
        # Load-time sanitization (``_drop_dead_hwmon_members``) only
        # catches the canonical ``hwmon:amdgpu:`` shape. This runtime
        # pass covers every other case using the daemon's authoritative
        # writability flag. Runs once per ``headers_updated`` emission
        # but persists to disk only when the member set actually
        # changes, so steady-state polling does not thrash the profile
        # files.
        self._headers_sanitization_done = False
        self._state.headers_updated.connect(self._sanitize_profiles_against_headers)

    def _wire_diagnostics_to_control_loop(self) -> None:
        """Connect Diagnostics' verify_started/completed signals to the
        control loop's pause/resume so the 1Hz tick does not race the
        daemon's 3-second verify wait (A1)."""
        loop = self._control_loop
        page = getattr(self, "diagnostics_page", None)
        if loop is None or page is None:
            return
        page.verify_started.connect(loop.pause_writes_for_header)
        page.verify_completed.connect(loop.resume_writes_for_header)

    def _on_page_changed(self, page_id: int) -> None:
        self.page_stack.setCurrentIndex(page_id)

    def _on_theme_changed(self, tokens) -> None:
        from PySide6.QtWidgets import QApplication

        from control_ofc.ui.theme import apply_theme_font, build_stylesheet, set_active_theme

        # Register the new theme so widgets without a parent reference
        # (diagnostics page, timeline chart, etc.) read the live tokens on
        # the next render instead of an import-time snapshot (DEC-109).
        set_active_theme(tokens)

        app = QApplication.instance()
        if app:
            app.setStyleSheet(build_stylesheet(tokens))
        apply_theme_font(tokens)
        self.controls_page.set_theme(tokens)
        # Propagate to widgets that need to refresh internal styling
        # (chart background, axis colours, freshness cell colours).
        if hasattr(self, "dashboard_page"):
            self.dashboard_page.set_theme(tokens)
        if hasattr(self, "diagnostics_page"):
            self.diagnostics_page.set_theme(tokens)
        # DEC-111: record the theme change in the event log so a session
        # bundle reflects what the user was actually looking at.
        name = getattr(tokens, "name", "") or "(unnamed)"
        self._diag.log_event("info", "gui", f"Theme changed: {name}")

    def _on_active_profile_for_events(self, name: str) -> None:
        """Mirror profile activation/deactivation into the event log."""
        if name:
            self._diag.log_event("info", "profile", f"Active profile: {name}")
        else:
            self._diag.log_event("info", "profile", "Profile deactivated")

    def _on_mode_for_events(self, mode: OperationMode) -> None:
        """Mirror notable mode transitions into the event log.

        AUTOMATIC/READ_ONLY churn during reconnects is already captured by
        the polling connect/disconnect events, so this only records
        MANUAL_OVERRIDE and DEMO — the two modes that change what the user
        can do at the control surface.
        """
        if mode == OperationMode.MANUAL_OVERRIDE:
            self._diag.log_event("info", "gui", "Manual override enabled")
        elif mode == OperationMode.DEMO:
            self._diag.log_event("info", "gui", "Demo mode active")

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

        # Pages were constructed before the demo control loop existed —
        # propagate the new reference so Activate-profile calls can reach it.
        self.dashboard_page._control_loop = self._control_loop
        self.controls_page._control_loop = self._control_loop
        self._wire_diagnostics_to_control_loop()

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
        """Persist window geometry and last page on close, then clean up timers."""
        geo = self.geometry()
        self._settings_service.update(
            last_page_index=self.page_stack.currentIndex(),
            window_geometry=[geo.x(), geo.y(), geo.width(), geo.height()],
        )
        if hasattr(self, "_demo_timer") and self._demo_timer is not None:
            self._demo_timer.stop()
        if hasattr(self, "_control_loop") and self._control_loop is not None:
            self._control_loop.shutdown()
        self._maybe_reset_gpu_on_close()
        self.dashboard_page.cleanup()
        if hasattr(self, "diagnostics_page") and self.diagnostics_page is not None:
            self.diagnostics_page.cleanup()
        super().closeEvent(event)

    def _on_capabilities_updated_for_kernel_warnings(self, caps) -> None:
        """Surface daemon-emitted kernel-version warnings as a one-time popup.

        DEC-098: ``amd_gpu.kernel_warnings`` is populated by the daemon when
        the running kernel matches a known amdgpu regression. We show a
        ``QMessageBox`` for each unacknowledged ``high``/``critical`` entry
        and remember the dismissal in ``acknowledged_kernel_warnings`` so
        the popup doesn't fire on every reconnect or restart.
        """
        if self._demo_mode:
            return
        gpu = getattr(caps, "amd_gpu", None)
        if gpu is None or not getattr(gpu, "kernel_warnings", None):
            return

        settings = self._settings_service.settings
        acknowledged = set(settings.acknowledged_kernel_warnings)
        unack = [
            w
            for w in gpu.kernel_warnings
            if w.id not in acknowledged and w.severity in ("high", "critical")
        ]
        if not unack:
            return

        # Lazy-import QMessageBox so this method stays cheap when there's
        # nothing to show (the common case).
        from PySide6.QtWidgets import QMessageBox

        from control_ofc.ui.hwmon_guidance import lookup_amd_gpu_guidance

        for warning in unack:
            box = QMessageBox(self)
            box.setIcon(
                QMessageBox.Icon.Critical
                if warning.severity == "critical"
                else QMessageBox.Icon.Warning
            )
            box.setWindowTitle("Kernel advisory for your GPU")
            box.setText(warning.message)

            # Attach GUI-side guidance text + references when we have a
            # known entry for this warning ID. Falls back gracefully for
            # warnings the GUI hasn't shipped a knowledge entry for.
            guidance = lookup_amd_gpu_guidance(warning.id)
            if guidance is not None:
                detail_lines: list[str] = list(guidance.details)
                if guidance.references:
                    detail_lines.append("")
                    detail_lines.append("References:")
                    detail_lines.extend(f"  • {ref}" for ref in guidance.references)
                box.setDetailedText("\n".join(detail_lines))

            box.setInformativeText(
                "Click 'Don't show again' to suppress this advisory until "
                "the warning ID changes (e.g. you boot a different kernel "
                "or the daemon adds new detections)."
            )
            box.addButton(QMessageBox.StandardButton.Ok)
            dismiss = box.addButton("Don't show again", QMessageBox.ButtonRole.DestructiveRole)
            box.exec()
            if box.clickedButton() is dismiss:
                acknowledged.add(warning.id)
                log.info("Acknowledged kernel warning %s", warning.id)
                self._diag.log_event("info", "kernel", f"Kernel warning acknowledged: {warning.id}")

        if acknowledged != set(settings.acknowledged_kernel_warnings):
            self._settings_service.update(acknowledged_kernel_warnings=sorted(acknowledged))

    def _sanitize_profiles_against_headers(self, headers) -> None:
        """Drop profile members that target unknown / read-only hwmon headers.

        DEC-102: pairs with the load-time ``_drop_dead_hwmon_members``
        sanitizer. Load-time sanitization knows only the canonical
        pre-DEC-102 ``hwmon:amdgpu:`` shape; this runtime pass uses the
        daemon's authoritative writability flag to catch every other case.

        Runs once per session: the first non-empty ``headers_updated``
        emission triggers the sweep. Subsequent emissions are ignored to
        avoid log noise on steady-state polling. A daemon ``/hwmon/rescan``
        does not refresh this — by that point any new dead members would
        already be filtered by the picker (Option C-1).
        """
        if self._demo_mode:
            return
        if self._headers_sanitization_done:
            return
        if not headers:
            return  # wait for the first real header set

        writable_ids = {h.id for h in headers if getattr(h, "is_writable", True)}
        all_ids = {h.id for h in headers}

        total_dropped = 0
        affected: list = []
        for profile in self._profile_service.profiles:
            dropped = profile.sanitize_hwmon_members(writable_ids, all_ids)
            if dropped:
                total_dropped += dropped
                affected.append(profile)

        for profile in affected:
            self._profile_service.save_profile(profile)
        if total_dropped:
            log.info(
                "DEC-102 runtime sanitization: dropped %d member(s) across %d profile(s)",
                total_dropped,
                len(affected),
            )
        self._headers_sanitization_done = True

    def _maybe_reset_gpu_on_close(self) -> None:
        """Reset the GPU fan to automatic when the GUI drove it and no
        profile is active to keep driving it after we exit (M9).

        When a profile is active, the daemon's profile engine takes over
        after the GUI's 30s heartbeat lapses, so there's nothing to reset.
        Uses cached ``active_profile_name`` — a blocking API call here could
        hang the close. Best-effort: failures are logged, not surfaced.
        """
        if self._demo_mode or not self._client:
            return
        if not self._state.gui_wrote_gpu_fan:
            return
        if self._state.active_profile_name:
            return
        caps = self._state.capabilities
        if not caps or not caps.amd_gpu or not caps.amd_gpu.present:
            return
        gpu_id = caps.amd_gpu.pci_id
        if not gpu_id:
            return
        try:
            self._client.reset_gpu_fan(gpu_id)
            log.info("GPU fan reset to automatic on GUI close (%s)", gpu_id)
        except (DaemonError, ConnectionError, OSError) as exc:
            log.debug("GPU reset on close failed (best-effort): %s", exc)
