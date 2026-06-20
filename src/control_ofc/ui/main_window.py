"""Main application window — sidebar + status banner + stacked pages."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from control_ofc.api.client import DaemonClient
from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.constants import PAGE_DASHBOARD, POLL_INTERVAL_MS
from control_ofc.services.app_settings_service import AppSettings, AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_controller import DemoController
from control_ofc.services.demo_service import DemoService
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.profile_import_service import should_offer_import
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


def _resolve_startup_page(settings: AppSettings, page_count: int) -> int:
    """Resolve the page index to show on startup, clamped to the page count.

    Honours ``default_startup_page`` when "restore last page" is off instead of
    always returning the dashboard (F3).
    """
    idx = settings.last_page_index if settings.restore_last_page else settings.default_startup_page
    return max(0, min(idx, page_count - 1))


class MainWindow(QWidget):
    """Top-level window assembling sidebar, status banner, and page stack."""

    def __init__(
        self,
        state: AppState | None = None,
        history: HistoryStore | None = None,
        profile_service: ProfileService | None = None,
        settings_service: AppSettingsService | None = None,
        client: DaemonClient | None = None,
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
        self._demo_controller: DemoController | None = None
        # Safety gate (DEC-165): True while connected to a daemon too old to be
        # the autonomous fan writer — the GUI stands its loop down and shows the
        # upgrade banner rather than pretend to control.
        self._control_blocked = False
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
        self._state.fan_zones = dict(self._settings_service.settings.fan_zones)
        self._state.sensor_class_overrides = dict(
            self._settings_service.settings.sensor_class_overrides
        )
        self._series_selection.load_hidden(self._settings_service.settings.hidden_chart_series)

        # Persist alias and series changes back to settings
        self._state.fan_alias_changed.connect(self._persist_fan_alias)
        self._state.fan_zones_changed.connect(self._persist_fan_zones)
        self._state.sensor_class_override_changed.connect(self._persist_sensor_class_override)
        self._series_selection.selection_changed.connect(self._persist_series_selection)

        # --- Status banner + error banner ---
        self.status_banner = StatusBanner()
        self.error_banner = ErrorBanner()
        # Persistent, non-dismissible upgrade-required banner (control gate,
        # DEC-165) — distinct from the transient/dismissible error_banner.
        self._gate_banner = QLabel()
        self._gate_banner.setObjectName("MainWindow_Banner_upgradeRequired")
        self._gate_banner.setWordWrap(True)
        self._gate_banner.setProperty("class", "CriticalChip")
        self._gate_banner.setVisible(False)

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
            settings_service=self._settings_service,
        )
        self.settings_page = SettingsPage(
            state=self._state,
            settings_service=self._settings_service,
            client=self._client,
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
        content_layout.addWidget(self._gate_banner)
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
        idx = _resolve_startup_page(s, self.page_stack.count())
        self.page_stack.setCurrentIndex(idx)
        self.sidebar.select_page(idx)
        # select_page() does not emit page_changed, so set the initial global-banner
        # visibility explicitly (hidden on the dashboard — it owns its own strip).
        self.status_banner.setVisible(idx != PAGE_DASHBOARD)
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

        # DEC-098: surface daemon-emitted kernel-version warnings (e.g. 6.19
        # RDNA hard hang, R9700 SMU mismatch) as a one-time popup. We listen
        # on capabilities_updated rather than checking once at startup so a
        # daemon restart with new detection logic refreshes the popup state.
        self._state.capabilities_updated.connect(self._on_capabilities_updated_for_kernel_warnings)

        # DEC-161: offer the one-time local→daemon profile import when the
        # daemon first advertises ``control.profile_storage``. Gated to fire at
        # most once per install (persisted ``daemon_import_prompted``) and once
        # per session (this guard) — see ``should_offer_import``.
        self._import_offer_done = False
        self._state.capabilities_updated.connect(self._on_capabilities_updated_for_profile_import)

        # DEC-165 control gate: block runtime control against a pre-2.0 daemon
        # (one that does not advertise ``control.autonomous_control``). Reactive
        # to capabilities so a daemon restart/upgrade clears it without a GUI
        # restart. Demo mode never reaches the daemon, so it is exempt.
        self._state.capabilities_updated.connect(self._on_control_capability_gate)

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

    def _on_page_changed(self, page_id: int) -> None:
        self.page_stack.setCurrentIndex(page_id)
        # The dashboard owns a rich status strip (DEC-176/177); hide the global
        # banner there so connection/profile/mode/warnings aren't shown twice.
        self.status_banner.setVisible(page_id != PAGE_DASHBOARD)

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

    def _on_control_capability_gate(self, caps) -> None:
        """Safety gate (DEC-165): never pretend to control a pre-2.0 daemon.

        A daemon that advertises ``control.autonomous_control`` is the sole
        authoritative fan writer (2.0.0+), so this loop-less GUI may drive intent
        against it. A daemon that omits the flag (pre-2.0) still expects a GUI
        control loop to do the writing — but this GUI has none, so against such a
        daemon fans would be left uncontrolled. The GUI must therefore refuse to
        present itself as in control: it shows a persistent upgrade-required
        banner and drives nothing. Demo mode is exempt (it never reaches a daemon).
        """
        if self._demo_mode:
            return
        control = getattr(caps, "control", None)
        autonomous = bool(control and control.autonomous_control)
        if autonomous:
            if self._control_blocked:
                self._control_blocked = False
                self._gate_banner.setVisible(False)
                log.info("Daemon now reports autonomous_control — control gate cleared")
            return
        if self._control_blocked:
            return
        self._control_blocked = True
        min_gui = (control.min_supported_gui if control else "") or "2.0.0"
        found = getattr(caps, "daemon_version", "") or "unknown"
        self._gate_banner.setText(
            f"⚠  Daemon upgrade required — this GUI needs control-ofc-daemon "
            f"≥ {min_gui} (found {found}). The GUI has stood down; the daemon's "
            f"built-in engine is controlling your fans. Upgrade the daemon for full "
            f"GUI control."
        )
        self._gate_banner.setVisible(True)
        log.warning(
            "Control gate engaged — daemon %s lacks autonomous_control (needs >= %s); "
            "GUI refuses to control (it has no local loop)",
            found,
            min_gui,
        )

    def _open_diagnostics(self) -> None:
        from control_ofc.constants import PAGE_DIAGNOSTICS

        self.page_stack.setCurrentIndex(PAGE_DIAGNOSTICS)
        self.sidebar.select_page(PAGE_DIAGNOSTICS)

    def _start_demo_mode(self) -> None:
        self._demo_service = DemoService()
        self._state.set_mode(OperationMode.DEMO)
        self._state.set_connection(ConnectionState.CONNECTED)
        self._state.fan_aliases = DemoService.fan_aliases()
        self._state.fan_zones = DemoService.fan_zones()

        # Demo evaluator (DEC-165): a demo-only mini-evaluator drives the
        # synthetic fans (live fan control is the daemon's job now). It exposes
        # the same set/clear_control_manual API as the old loop, so the Controls
        # page demo branch drives it unchanged, and emits per-control outputs to
        # keep the control cards live.
        self._demo_controller = DemoController(
            self._profile_service, self._demo_service, self._state
        )
        self._demo_controller.outputs_changed.connect(self.controls_page.update_control_outputs)
        self._demo_controller.start()
        self.controls_page._demo_controller = self._demo_controller

        # Load initial demo data
        self._state.set_capabilities(self._demo_service.capabilities())
        self._state.set_status(self._demo_service.status())
        self._state.set_hwmon_headers(self._demo_service.hwmon_headers())

        # Demo polling timer
        self._demo_timer = QTimer(self)
        self._demo_timer.setInterval(POLL_INTERVAL_MS)
        self._demo_timer.timeout.connect(self._demo_tick)
        self._demo_timer.start()
        self._demo_tick()

    def _persist_fan_alias(self, _fan_id: str, _display_name: str) -> None:
        self._settings_service.update(fan_aliases=dict(self._state.fan_aliases))

    def _persist_fan_zones(self, _fan_id: str, _zone_name: str) -> None:
        self._settings_service.update(fan_zones=dict(self._state.fan_zones))

    def _persist_sensor_class_override(self, _sensor_id: str, _source_class: str) -> None:
        self._settings_service.update(
            sensor_class_overrides=dict(self._state.sensor_class_overrides)
        )

    def _persist_series_selection(self) -> None:
        hidden = list(self._series_selection.to_dict()["hidden_keys"])
        self._settings_service.update(hidden_chart_series=hidden)

    def _demo_tick(self) -> None:
        if self._demo_service:
            self._state.set_sensors(self._demo_service.sensors())
            self._state.set_fans(self._demo_service.fans())
            self._state.mark_poll_success()  # drive the strip's poll-age in demo

    def closeEvent(self, event) -> None:
        """Persist window geometry and last page on close, then clean up timers."""
        geo = self.geometry()
        self._settings_service.update(
            last_page_index=self.page_stack.currentIndex(),
            window_geometry=[geo.x(), geo.y(), geo.width(), geo.height()],
        )
        if hasattr(self, "_demo_timer") and self._demo_timer is not None:
            self._demo_timer.stop()
        if hasattr(self, "_demo_controller") and self._demo_controller is not None:
            self._demo_controller.stop()
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

    def _on_capabilities_updated_for_profile_import(self, caps) -> None:
        """Offer the one-time local→daemon profile import (DEC-161).

        Fires when the daemon advertises ``control.profile_storage``. Gated by
        ``should_offer_import`` (capability present + not already offered on
        this install + local profiles exist + not demo) and a per-session guard
        so repeated capability emissions don't re-open the dialog. The actual
        collect/upload/report flow lives on the Settings page (shared with its
        manual "Import local profiles into daemon..." button).
        """
        if self._import_offer_done:
            return
        settings = self._settings_service.settings
        has_local = bool(self._profile_service.profiles)
        if not should_offer_import(
            caps, settings, has_local_profiles=has_local, demo=self._demo_mode
        ):
            return
        self._import_offer_done = True
        self.settings_page.run_profile_import(auto=True)

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
