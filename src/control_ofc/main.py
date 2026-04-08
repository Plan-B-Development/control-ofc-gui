"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from control_ofc.api.client import DaemonClient
from control_ofc.constants import APP_NAME, APP_VERSION, DEFAULT_SOCKET_PATH
from control_ofc.paths import ensure_dirs, set_path_overrides
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import ControlLoopService
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.lease_service import LeaseService
from control_ofc.services.polling import PollingService
from control_ofc.services.profile_service import ProfileService
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.microcopy import set_fun_mode
from control_ofc.ui.theme import apply_theme_font, build_stylesheet, default_dark_theme

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main() -> None:
    ensure_dirs()

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setApplicationVersion(APP_VERSION)

    # Apply dark theme
    theme = default_dark_theme()
    qt_app.setStyleSheet(build_stylesheet(theme))
    apply_theme_font(theme)

    # Load settings early for splash/fun_mode decisions
    settings_service = AppSettingsService()
    settings_service.load()
    set_fun_mode(settings_service.settings.fun_mode)

    # Apply user-configured directory overrides before loading profiles/themes
    s = settings_service.settings
    set_path_overrides(
        profiles_dir=s.profiles_dir_override,
        themes_dir=s.themes_dir_override,
        export_dir=s.export_default_dir,
    )

    # Splash screen
    splash = None
    if settings_service.settings.show_splash:
        from control_ofc.ui.splash import AppSplashScreen

        splash = AppSplashScreen()
        splash.show()
        qt_app.processEvents()

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
    profile_service.load()

    if splash:
        splash.set_status("splash_status_connecting")
        qt_app.processEvents()

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
        polling = PollingService(state, socket_path, history=history)
        lease = LeaseService(client)
        control_loop = ControlLoopService(
            state=state,
            profile_service=profile_service,
            client=client,
            lease_service=lease,
            socket_path=socket_path,
        )

    if splash:
        splash.set_status("splash_status_loading")
        qt_app.processEvents()

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
        demo_mode=demo_mode,
    )
    window.show()

    # Splash finish
    if splash:
        splash.set_status("splash_status_ready")
        splash.finish_with_delay(window, delay_ms=3000)

    # Allow Ctrl+C to exit cleanly
    signal.signal(signal.SIGINT, lambda *_: qt_app.quit())
    _sigint_timer = QTimer()
    _sigint_timer.timeout.connect(lambda: None)
    _sigint_timer.start(200)

    # Wire control loop status → controls page (live mode)
    if control_loop:
        control_loop.status_changed.connect(
            lambda status: (
                window.controls_page.update_control_outputs(status.control_outputs)
                if status.control_outputs
                else None
            )
        )

    # Start services after window is visible
    if polling:
        polling.start()
    if control_loop:
        control_loop.start()

    exit_code = qt_app.exec()

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
