"""Main application window — sidebar + status banner + stacked pages."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from control_ofc.api.client import DaemonClient
from control_ofc.api.models import ConnectionState, OperationMode, ReadinessRollup
from control_ofc.constants import PAGE_CONTROLS, PAGE_DASHBOARD, POLL_INTERVAL_MS
from control_ofc.services.app_settings_service import AppSettings, AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.dashboard_view import safety_detail_text
from control_ofc.services.demo_controller import DemoController
from control_ofc.services.demo_service import DemoService
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.profile_import_service import should_offer_import
from control_ofc.services.profile_service import ProfileService
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.components.footer import StatusFooter
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.pages.logs_page import LogsPage
from control_ofc.ui.pages.overview_page import OverviewPage
from control_ofc.ui.pages.settings_page import SettingsPage
from control_ofc.ui.pages.system_state_page import SystemStatePage
from control_ofc.ui.pages.theme_page import ThemePage
from control_ofc.ui.qt_util import block_signals
from control_ofc.ui.sidebar import Sidebar
from control_ofc.ui.status_banner import THERMAL_STATES, StatusBanner
from control_ofc.ui.status_ribbon import StatusRibbon
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
        # DEC-209: Overview is its own page (merged Diagnostics Overview/Fans/
        # Sensors). DEC-216 retired the legacy Diagnostics page entirely.
        self.overview_page = OverviewPage(
            state=self._state,
            diagnostics_service=self._diag,
            settings_service=self._settings_service,
            series_selection=self._series_selection,
            client=self._client,
        )
        # DEC-210: Logs is now its own page (migrated Diagnostics Event Log +
        # a Log Inspector). Shares the same DiagnosticsService feed.
        # DEC-222: Logs is now the single active-warnings surface, so it needs
        # AppState (the warnings live there, not in the diagnostics event feed).
        self.logs_page = LogsPage(diagnostics_service=self._diag, state=self._state)
        # DEC-211: System State is now its own page (migrated Diagnostics
        # Troubleshooting). Owns its own hw-diagnostics/verify workers; reads the
        # shared last_hw_diagnostics cache.
        self.system_state_page = SystemStatePage(
            state=self._state,
            diagnostics_service=self._diag,
            client=self._client,
            profile_service=self._profile_service,
        )
        # DEC-212: Hardware is now its own page (migrated Diagnostics Readiness).
        # Owns its own hardware-readiness worker; deep-links re-point to the
        # migrated System State / Overview / Settings pages.
        self.hardware_page = HardwarePage(
            state=self._state, diagnostics_service=self._diag, client=self._client
        )
        # DEC-215: Theme is now its own page (split from the Settings tabs). Owns
        # the theme_changed signal + the theme editor; Settings keeps the rest.
        self.theme_page = ThemePage(settings_service=self._settings_service)

        self.page_stack.addWidget(self.dashboard_page)  # PAGE_DASHBOARD = 0
        self.page_stack.addWidget(self.controls_page)  # PAGE_CONTROLS = 1
        self.page_stack.addWidget(self.settings_page)  # PAGE_SETTINGS = 2
        self.page_stack.addWidget(self.overview_page)  # PAGE_OVERVIEW = 3
        self.page_stack.addWidget(self.logs_page)  # PAGE_LOGS = 4
        self.page_stack.addWidget(self.system_state_page)  # PAGE_SYSTEM_STATE = 5
        self.page_stack.addWidget(self.hardware_page)  # PAGE_HARDWARE = 6
        self.page_stack.addWidget(self.theme_page)  # PAGE_THEME = 7

        # --- Global shell chrome (DEC-208): top ribbon + bottom footer ---
        self.status_ribbon = StatusRibbon()
        self.footer = StatusFooter()

        # --- Layout: ribbon / body(sidebar + content) / footer ---
        # content_layout keeps the existing banner + stack column UNCHANGED so the
        # StatusBanner-hidden-on-dashboard contract and banner objectNames hold.
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._gate_banner)
        content_layout.addWidget(self.status_banner)
        content_layout.addWidget(self.error_banner)
        content_layout.addWidget(self.page_stack, 1)

        content_container = QWidget()
        content_container.setLayout(content_layout)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.sidebar)
        body_layout.addWidget(content_container, 1)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.status_ribbon)
        root_layout.addWidget(body, 1)
        root_layout.addWidget(self.footer)

        # --- Signals ---
        self.sidebar.nav_activated.connect(self._on_nav_activated)
        self.theme_page.theme_changed.connect(self._on_theme_changed)  # DEC-215

        # State → status banner
        self._state.connection_changed.connect(self.status_banner.set_connection_state)
        self._state.connection_changed.connect(self._on_connection_changed)
        self._state.mode_changed.connect(self.status_banner.set_operation_mode)
        self._state.active_profile_changed.connect(self.status_banner.set_active_profile)
        self._state.warning_count_changed.connect(self.status_banner.set_warning_count)

        # State → status ribbon + footer (DEC-208). The ribbon mirrors connection,
        # feeds a warnings count, and shows daemon uptime + thermal from
        # status_updated; the footer shows the health rollup from the warning count.
        self._state.connection_changed.connect(self.status_ribbon.set_connection_state)
        self._state.warning_count_changed.connect(self.status_ribbon.set_warning_count)
        self._state.warning_count_changed.connect(self.footer.set_warning_count)
        self._state.status_updated.connect(self._on_status_for_ribbon)
        self.status_ribbon.alerts_clicked.connect(self._open_logs)
        # DEC-222: four indicators re-homed from the retired DashboardStatusStrip.
        # They were Dashboard-only; the footer is always visible, so every page
        # now answers "what mode am I in / is the data fresh / is it thermally
        # safe / is my cooling set up".
        self._state.mode_changed.connect(self.footer.set_operation_mode)
        self.footer.set_operation_mode(self._state.mode)
        self.footer.thermal_clicked.connect(self._open_safety_detail)
        self.footer.readiness_clicked.connect(self._open_readiness)
        # DEC-216: the global footer actions moved off the retired Diagnostics
        # page — Rescan surfaces the System State page (so its outcome line is
        # visible) then runs there; Export reuses the Logs page's bundle handler.
        self.footer.rescan_clicked.connect(self._on_footer_rescan)
        self.footer.export_bundle_clicked.connect(self.logs_page.export_bundle)

        # Sidebar active-profile selector (DEC-208): a third profile surface that
        # populates + reflects + applies via the same ProfileService path.
        self._profile_service.profiles_changed.connect(self._populate_sidebar_profiles)
        self._profile_service.active_changed.connect(self._reflect_sidebar_active_profile)
        self.sidebar.apply_profile_btn.clicked.connect(self._on_sidebar_apply_profile)
        self._populate_sidebar_profiles()

        # DEC-194: route the daemon-authoritative active-profile id through the
        # ProfileService so an external activation (CLI --profile, another client,
        # systemd) moves the id-based UI — the dashboard combo selection and the
        # Controls `*`-active marker — with no extra page wiring. set_active is
        # edge-triggered and a silent no-op for an id the GUI doesn't know locally
        # (dashboard findData → -1, combo left as-is), so an unknown id never
        # crashes or desyncs.
        self._state.active_profile_id_changed.connect(self._profile_service.set_active)

        # DEC-111: surface profile + mode transitions in the event log.
        self._state.active_profile_changed.connect(self._on_active_profile_for_events)
        self._state.mode_changed.connect(self._on_mode_for_events)

        # --- Restore persisted window state ---
        s = self._settings_service.settings
        idx = _resolve_startup_page(s, self.page_stack.count())
        self.page_stack.setCurrentIndex(idx)
        self.sidebar.select_page(idx)
        # Selecting the already-checked default (Dashboard) does not re-emit, so
        # set the initial global-banner visibility explicitly (hidden on the
        # dashboard — it owns its own strip).
        self.status_banner.setVisible(idx != PAGE_DASHBOARD)
        geo = s.window_geometry
        if len(geo) == 4:
            self.setGeometry(geo[0], geo[1], geo[2], geo[3])

        # Wire the dashboard readiness affordances (cooling-readiness chip +
        # no-hardware "what to do next" button) to sidebar navigation.
        self.dashboard_page.open_readiness.connect(self._open_readiness)
        # DEC-222: a fan card's Edit opens Controls and focuses that control.
        self.dashboard_page.open_control.connect(self._open_control)
        # DEC-207/DEC-216: the Cooling Hardware Readiness "set preferred sensor"
        # deep-link is owned by the Hardware page (the Diagnostics duplicate was
        # retired with the page).
        self.hardware_page.open_preferred_sensors.connect(self._open_preferred_sensors)
        self.hardware_page.open_system_state.connect(self._open_system_state)
        self.hardware_page.open_overview.connect(self._open_overview)

        # Populate dashboard profile selector
        self.dashboard_page.populate_profiles()

        # Poll-age ticks ~1 Hz. It lives here rather than on a page because the
        # footer is always visible — a page-owned timer would stop updating the
        # moment the user navigated away from it.
        self._poll_age_timer = QTimer(self)
        self._poll_age_timer.setObjectName("MainWindow_Timer_pollAge")
        self._poll_age_timer.setInterval(1000)
        self._poll_age_timer.timeout.connect(self._tick_poll_age)
        self._poll_age_timer.start()
        self._tick_poll_age()

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

    def _on_nav_activated(self, page_id: int, sub_tab: int) -> None:
        """DEC-208: a sidebar entry was activated → switch the stack page. DEC-216:
        every nav entry now maps to its own standalone page, so ``sub_tab`` (kept
        in the signal signature for compatibility) is always -1 and unused."""
        self._on_page_changed(page_id)

    def _on_status_for_ribbon(self, status) -> None:
        """Feed daemon uptime + thermal to the ribbon, and thermal + cooling
        readiness to the footer (DEC-208 / DEC-222)."""
        self.status_ribbon.set_uptime(status.uptime_seconds)
        self.status_ribbon.set_thermal_state(status.thermal_state)
        self.footer.set_thermal_state(status.thermal_state or "normal")
        self.footer.set_readiness_rollup(self._readiness_for_footer(status))

    def _readiness_for_footer(self, status) -> ReadinessRollup | None:
        """The readiness rollup to show on the footer chip, or ``None`` to hide it.

        Hidden in demo mode — synthetic hardware has no real readiness (DEC-206,
        O-2) — and whenever the daemon sends no rollup (older daemon / pre-seed).
        """
        if self._state.mode == OperationMode.DEMO:
            return None
        return status.readiness

    def _tick_poll_age(self) -> None:
        """Refresh the footer's poll-freshness label (DEC-222)."""
        self.footer.update_poll_age(time.monotonic(), self._state.last_poll_monotonic)

    def _safety_detail_text(self) -> str:
        """Read-only thermal-safety summary for the footer thermal chip.

        Assembled Qt-free by :func:`dashboard_view.safety_detail_text`; the
        THERMAL_STATES label is a presentation constant resolved here. Surfaces
        only data we actually have — state, a plain reason, the current hottest
        CPU sensor, and any active manual overrides."""
        ds = self._state.daemon_status
        thermal = (ds.thermal_state if ds else "normal") or "normal"
        label, _css = THERMAL_STATES.get(thermal, (f"Thermal: {thermal}", ""))
        cpu_vals = [s.value_c for s in self._state.sensors if s.kind in ("CpuTemp", "cpu_temp")]
        n = len(ds.overrides) if ds and ds.overrides else 0
        return safety_detail_text(thermal, label, cpu_vals, n)

    def _open_safety_detail(self) -> None:
        """Show the read-only thermal-safety detail (footer thermal chip, DEC-185)."""
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setObjectName("MainWindow_Dialog_safetyDetail")
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Thermal safety")
        box.setText(self._safety_detail_text())
        box.exec()

    def _populate_sidebar_profiles(self) -> None:
        """(Re)build the sidebar profile combo from ProfileService (DEC-208)."""
        combo = self.sidebar.profile_combo
        with block_signals(combo):
            active = self._profile_service.active_profile
            active_id = active.id if active else ""
            combo.clear()
            for profile in self._profile_service.profiles:
                combo.addItem(profile.name, profile.id)
            idx = combo.findData(active_id)
            if idx < 0 and combo.count() > 0:
                idx = 0
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def _reflect_sidebar_active_profile(self, _profile_id: str = "") -> None:
        """Move the sidebar combo to the authoritative active profile (DEC-208)."""
        combo = self.sidebar.profile_combo
        active = self._profile_service.active_profile
        active_id = active.id if active else ""
        idx = combo.findData(active_id)
        if idx >= 0:
            with block_signals(combo):
                combo.setCurrentIndex(idx)

    def _on_sidebar_apply_profile(self) -> None:
        """Apply the sidebar-selected profile via the shared ProfileService path,
        then re-reflect the authoritative active id (snaps back on failure).

        DEC-214: the Controls page dropped its own profile combo, so the
        unsaved-changes-on-switch guard relocates here. Prompt before activating
        a *different* profile while the Controls page has in-progress edits; on
        cancel, snap the sidebar combo back to the active profile and do nothing.
        """
        profile_id = self.sidebar.profile_combo.currentData()
        if not profile_id:
            return
        if (
            profile_id != self._profile_service.active_id
            and self.controls_page.has_unsaved_changes()
            and not self.controls_page.confirm_discard_unsaved()
        ):
            self._reflect_sidebar_active_profile()
            return
        res = self._profile_service.activate(profile_id, client=self._client)
        # DEC-214: bridge activation into AppState (the Controls page's removed
        # _on_activate used to do this) so the status banner / dashboard reflect
        # the newly-active profile.
        if res.activated:
            active = self._profile_service.active_profile
            if active is not None:
                self._state.set_active_profile(active.name)
        self._reflect_sidebar_active_profile()

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
        if hasattr(self, "overview_page"):
            self.overview_page.set_theme(tokens)
        if hasattr(self, "logs_page"):
            self.logs_page.set_theme(tokens)
        if hasattr(self, "system_state_page"):
            self.system_state_page.set_theme(tokens)
        if hasattr(self, "hardware_page"):
            self.hardware_page.set_theme(tokens)
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
        the polling connect/disconnect events, so this only records DEMO —
        the mode that changes what the user can do at the control surface.
        """
        if mode == OperationMode.DEMO:
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

    def _open_overview(self) -> None:
        from control_ofc.constants import NAV_OVERVIEW

        self.sidebar.activate_nav(NAV_OVERVIEW)

    def _open_readiness(self) -> None:
        """DEC-206: the Dashboard cooling-readiness chip was clicked — activate the
        Hardware entry, which opens the Hardware page (DEC-212)."""
        from control_ofc.constants import NAV_HARDWARE

        self.sidebar.activate_nav(NAV_HARDWARE)

    def _open_system_state(self) -> None:
        """DEC-212: a Hardware-page readiness action deep-links to the System State
        page (which now hosts the PWM-verify workflow)."""
        from control_ofc.constants import NAV_SYSTEM_STATE

        self.sidebar.activate_nav(NAV_SYSTEM_STATE)

    def _on_footer_rescan(self) -> None:
        """DEC-216: the global footer's Rescan Hardware action. Surface the System
        State page first (so its rescan-result line is visible), then run the
        rescan there — relocated from the retired Diagnostics page."""
        self._open_system_state()
        self.system_state_page.run_hwmon_rescan()

    def _open_preferred_sensors(self, role: str) -> None:
        """DEC-207: a Cooling Hardware Readiness action deep-links to Settings ▸
        Preferred sensors (``role`` is "cpu" | "mb") so the user can pick a sensor."""
        from control_ofc.constants import NAV_SETTINGS

        self.sidebar.activate_nav(NAV_SETTINGS)
        self.settings_page.focus_preferred_sensors(role)

    def _open_control(self, control_id: str) -> None:
        """A Dashboard fan card's Edit was clicked (DEC-222).

        The cards are read-only by design — every write lives on the Controls
        page — so Edit navigates there and focuses the control. An Unassigned card
        sends "" and simply lands the user on Controls, where the fans can be
        assigned.
        """
        self.page_stack.setCurrentIndex(PAGE_CONTROLS)
        self.sidebar.select_page(PAGE_CONTROLS)
        self.controls_page.focus_control(control_id)

    def _open_logs(self) -> None:
        """The status-ribbon Alerts indicator was clicked — open the Logs entry
        (DEC-208)."""
        from control_ofc.constants import NAV_LOGS

        self.sidebar.activate_nav(NAV_LOGS)

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
        self.controls_page.set_demo_controller(self._demo_controller)

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
        # Stop the poll-age ticker before the pages tear down: it writes into the
        # footer every second, and a tick landing mid-teardown would touch an
        # already-deleted widget (DEC-222).
        self._poll_age_timer.stop()
        if hasattr(self, "_demo_timer") and self._demo_timer is not None:
            self._demo_timer.stop()
        if hasattr(self, "_demo_controller") and self._demo_controller is not None:
            self._demo_controller.stop()
        self.dashboard_page.cleanup()
        self.controls_page.cleanup()  # DEC-214: tear down the always-mounted curve editor
        # The pages below are all constructed unconditionally in __init__, so no
        # hasattr/None guard is needed here.
        self.overview_page.cleanup()
        self.logs_page.cleanup()
        self.system_state_page.cleanup()
        self.hardware_page.cleanup()
        self.theme_page.cleanup()  # DEC-215
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
