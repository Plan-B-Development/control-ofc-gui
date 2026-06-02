"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from control_ofc.api.client import DaemonClient
from control_ofc.constants import APP_NAME, APP_VERSION, DEFAULT_SOCKET_PATH
from control_ofc.paths import ensure_dirs, set_path_overrides, themes_dir
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import ControlLoopService
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.lease_service import LeaseService
from control_ofc.services.polling import PollingService
from control_ofc.services.profile_service import ProfileService
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.theme import (
    ThemeTokens,
    apply_theme_font,
    build_stylesheet,
    default_dark_theme,
    ensure_bundled_themes_installed,
    load_theme,
    set_active_theme,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger(__name__)


def _resolve_startup_theme(theme_name: str) -> ThemeTokens:
    """Return the persisted theme by name, or the default dark theme.

    Resolution order:
      1. ``Default Dark`` (or empty / unknown name) — bundled default tokens
      2. Any matching JSON file in ``themes_dir()`` whose loaded
         ``ThemeTokens.name`` equals ``theme_name``
      3. Fallback to default dark if the file is missing or invalid

    Failures are logged but never raise: a corrupted theme must not prevent
    the GUI from starting. The user can re-pick a theme from Settings if
    their persisted choice is no longer loadable.
    """
    if not theme_name or theme_name == "Default Dark":
        return default_dark_theme()
    td = themes_dir()
    if not td.exists():
        return default_dark_theme()
    for path in sorted(td.glob("*.json")):
        try:
            tokens = load_theme(path)
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Skipping unreadable theme %s on startup: %s", path, exc)
            continue
        if tokens.name == theme_name:
            return tokens
    log.info("Persisted theme %r not found in %s; falling back to Default Dark", theme_name, td)
    return default_dark_theme()


def main() -> None:
    ensure_dirs()

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setApplicationVersion(APP_VERSION)

    # Load settings early so directory overrides apply before profiles/themes load
    settings_service = AppSettingsService()
    settings_service.load()

    # Apply user-configured directory overrides before loading profiles/themes
    s = settings_service.settings
    set_path_overrides(
        profiles_dir=s.profiles_dir_override,
        themes_dir=s.themes_dir_override,
        export_dir=s.export_default_dir,
    )

    # Install bundled presets (Solar Light, Noctua Dark) on first run so they
    # show up in the Settings → Theme selector. Existing files are left alone
    # so a user who has edited a preset doesn't lose their changes.
    installed = ensure_bundled_themes_installed(themes_dir())
    for path in installed:
        log.info("Installed bundled theme preset: %s", path.name)

    # Resolve the persisted theme (or fall back to Default Dark) and apply it
    # *after* themes_dir is correct so the selection actually loads from the
    # right location. The active theme is registered so widgets that don't
    # carry a parent reference can look up live tokens via active_theme().
    theme = _resolve_startup_theme(s.theme_name)
    set_active_theme(theme)
    qt_app.setStyleSheet(build_stylesheet(theme))
    apply_theme_font(theme)

    parser = argparse.ArgumentParser(description="Control-OFC desktop fan control GUI")
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH, help="Daemon socket path")
    parser.add_argument("--demo", action="store_true", help="Run in demo mode")
    args = parser.parse_args()

    socket_path = args.socket
    demo_mode = args.demo

    # Core services
    state = AppState()
    history = HistoryStore()
    profile_service = ProfileService()

    # DEC-111: a single DiagnosticsService is shared between the polling
    # service, lease service, control loop, main window, and diagnostics
    # page so every emitter writes into the same in-process event deque.
    diagnostics = DiagnosticsService(
        state=state,
        settings_service=settings_service,
        profile_service=profile_service,
    )
    diagnostics.log_event("info", "gui", f"GUI started v{APP_VERSION}")

    profile_load_errors = profile_service.load()
    for path, reason in profile_load_errors:
        # Surface per-profile load failures to Diagnostics so a corrupted
        # profile is obviously broken, not silently missing from the UI.
        state.add_warning(
            level="warning",
            source="profile_service",
            message=f"Failed to load profile '{os.path.basename(path)}': {reason}",
            key=f"profile_load_fail:{path}",
        )
        # DEC-111: also record in the event log so the support bundle
        # carries the failure even after the user acknowledges the warning.
        diagnostics.log_event(
            "warning",
            "profile",
            f"Failed to load profile '{os.path.basename(path)}': {reason}",
        )

    # Wire history recording to state updates
    state.sensors_updated.connect(history.record_sensors)
    state.fans_updated.connect(history.record_fans)

    # Polling and control loop (only in live mode)
    client: DaemonClient | None = None
    polling: PollingService | None = None
    control_loop: ControlLoopService | None = None
    lease: LeaseService | None = None
    if not demo_mode:
        client = DaemonClient(socket_path=socket_path)
        polling = PollingService(state, socket_path, history=history, diagnostics=diagnostics)
        # Profile-search-dir registration runs inside the polling worker
        # thread on first successful poll and after every reconnect
        # (see PollingService._PollWorker._register_profile_search_dir).
        # Kept off the Qt main thread so a slow daemon cannot stall the UI.
        # socket_path enables the dedicated lease worker thread (DEC-108) so
        # take/renew/release HTTP calls never block the Qt main thread.
        lease = LeaseService(client, socket_path=socket_path, diagnostics=diagnostics)
        control_loop = ControlLoopService(
            state=state,
            profile_service=profile_service,
            client=client,
            lease_service=lease,
            socket_path=socket_path,
            diagnostics=diagnostics,
        )

    # Set app icon
    from control_ofc.ui.branding import load_app_icon

    icon = load_app_icon()
    if icon:
        qt_app.setWindowIcon(icon)

    # Main window
    window = MainWindow(
        state=state,
        history=history,
        profile_service=profile_service,
        settings_service=settings_service,
        client=client if not demo_mode else None,
        control_loop=control_loop,
        lease_service=lease,
        diagnostics_service=diagnostics,
        demo_mode=demo_mode,
    )
    window.show()

    # Allow Ctrl+C to exit cleanly
    signal.signal(signal.SIGINT, lambda *_: qt_app.quit())
    _sigint_timer = QTimer()
    _sigint_timer.timeout.connect(lambda: None)
    _sigint_timer.start(200)

    # Start services after window is visible
    if polling:
        polling.start()
    if control_loop:
        control_loop.start()

    exit_code = qt_app.exec()

    # DEC-111: record the exit so a support bundle saved on the way out
    # still has a closing breadcrumb. Logged before service shutdown so the
    # message lands while diagnostics is still owned by the main thread.
    diagnostics.log_event("info", "gui", "GUI exiting")

    # Cleanup
    if control_loop:
        control_loop.shutdown()
    if lease:
        lease.shutdown()
    if polling:
        polling.shutdown()
    if client:
        client.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
