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
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.history_store import HistoryStore
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


def _resolve_demo_mode(cli_demo: bool, demo_on_disconnect: bool, daemon_reachable: bool) -> bool:
    """Decide whether to start in demo mode (DEC-139).

    Demo is forced by ``--demo``; otherwise it is the fallback when the user
    enabled "start in demo mode when daemon is unavailable" and the daemon did
    not answer a launch-time probe.
    """
    if cli_demo:
        return True
    return demo_on_disconnect and not daemon_reachable


def _probe_daemon(socket_path: str, timeout: float = 1.5) -> bool:
    """Return True if the daemon answers a cheap GET /status within *timeout*.

    A connection failure (socket missing/refused) means the daemon is not
    running → not reachable. A timeout or server error means it is present but
    slow; we treat that as reachable so a sluggish daemon never silently drops
    the user into demo mode and disables real control.
    """
    from control_ofc.api.errors import DaemonError, DaemonUnavailable

    client = DaemonClient(socket_path=socket_path, timeout=timeout)
    try:
        client.status()
        return True
    except DaemonUnavailable:
        return False
    except DaemonError:
        return True
    finally:
        client.close()


# Wired up in main() once the diagnostics service exists, so the last-resort
# exception hook can drop a breadcrumb into the support bundle. Stays None in
# tests (which never call main()) and until diagnostics is constructed.
_diagnostics: DiagnosticsService | None = None


def _set_uncaught_diagnostics(diag: DiagnosticsService) -> None:
    """Register the diagnostics service used by ``_handle_uncaught``."""
    global _diagnostics
    _diagnostics = diag


def _handle_uncaught(exc_type, exc, tb) -> None:
    """Last-resort hook for exceptions that escape a Qt slot or worker thread.

    Daemon-disconnect handling proper lives in the API client (transport errors
    → ``DaemonUnavailable``) and the polling / control-loop workers. This net
    exists so a *future* uncaught exception surfaces in the log and the support
    bundle instead of silently killing a worker thread — PySide6 routes
    unhandled slot exceptions through ``sys.excepthook``. ``KeyboardInterrupt``
    and ``SystemExit`` are delegated to the default hook so Ctrl+C and clean
    exits are unaffected. Must never raise.
    """
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        sys.__excepthook__(exc_type, exc, tb)
        return
    log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
    if _diagnostics is not None:
        try:
            _diagnostics.log_event(
                "error", "gui", f"Uncaught exception: {exc_type.__name__}: {exc}"
            )
        except Exception:
            log.debug("Failed to record uncaught exception in diagnostics", exc_info=True)


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

    # Defense-in-depth: a last-resort exception hook so nothing fails silently
    # on a worker thread, plus routing Qt's own log messages into Python
    # logging. The primary daemon-disconnect handling is in the API client and
    # the polling / control-loop workers (see _handle_uncaught). Installed
    # before QApplication so even early startup errors are captured.
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    sys.excepthook = _handle_uncaught
    _qt_log_levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def _qt_message_handler(mode, _context, message) -> None:
        logging.getLogger("Qt").log(_qt_log_levels.get(mode, logging.INFO), message)

    qInstallMessageHandler(_qt_message_handler)

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
    # DEC-139: when the user opted into demo-on-disconnect, probe the daemon
    # once at launch and fall back to demo mode if it is unreachable.
    daemon_reachable = True
    if not args.demo and s.demo_on_disconnect:
        daemon_reachable = _probe_daemon(socket_path)
        if not daemon_reachable:
            log.info("Daemon unreachable at startup — starting in demo mode (demo_on_disconnect)")
    demo_mode = _resolve_demo_mode(args.demo, s.demo_on_disconnect, daemon_reachable)

    # Core services. In live mode the daemon client is created up-front so the
    # ProfileService can use the daemon-owned profile store (control migration);
    # demo mode keeps client=None and the service stays purely local.
    state = AppState()
    history = HistoryStore()
    client: DaemonClient | None = None
    if not demo_mode:
        client = DaemonClient(socket_path=socket_path)
    profile_service = ProfileService(client=client)

    # DEC-111: a single DiagnosticsService is shared between the polling
    # service, main window, and diagnostics page so every emitter writes into
    # the same in-process event deque.
    diagnostics = DiagnosticsService(
        state=state,
        settings_service=settings_service,
        profile_service=profile_service,
    )
    diagnostics.log_event("info", "gui", f"GUI started v{APP_VERSION}")
    # Now that diagnostics exists, let the last-resort exception hook record an
    # uncaught error into the support bundle (set after the early hook install).
    _set_uncaught_diagnostics(diagnostics)

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

    # Polling (only in live mode); the client was created above. The daemon is
    # the authoritative fan-control engine now (DEC-165) — the GUI runs no
    # control loop and holds no hwmon lease.
    polling: PollingService | None = None
    if not demo_mode:
        polling = PollingService(state, socket_path, history=history, diagnostics=diagnostics)
        # Profile-search-dir registration runs inside the polling worker
        # thread on first successful poll and after every reconnect
        # (see PollingService._PollWorker._register_profile_search_dir).
        # Kept off the Qt main thread so a slow daemon cannot stall the UI.

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

    exit_code = qt_app.exec()

    # DEC-111: record the exit so a support bundle saved on the way out
    # still has a closing breadcrumb. Logged before service shutdown so the
    # message lands while diagnostics is still owned by the main thread.
    diagnostics.log_event("info", "gui", "GUI exiting")

    # Cleanup
    if polling:
        polling.shutdown()
    if client:
        client.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
