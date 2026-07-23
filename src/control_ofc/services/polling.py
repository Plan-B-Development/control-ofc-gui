"""Polling service — periodic reads from daemon API, updates AppState.

Runs on a QTimer. API calls execute in a QThread worker to avoid blocking
the UI. Results are posted back to AppState on the main thread via signals.
"""

from __future__ import annotations

import contextlib
import logging

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    ActiveProfileInfo,
    Capabilities,
    ConnectionState,
    DaemonStatus,
    OperationMode,
)
from control_ofc.constants import CAPABILITIES_REFRESH_INTERVAL_S, POLL_INTERVAL_MS
from control_ofc.paths import profiles_dir
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.history_store import HistoryStore

log = logging.getLogger(__name__)


class _PollWorker(QObject):
    """Runs in a QThread — makes blocking API calls."""

    # Results
    capabilities_ready = Signal(Capabilities)
    status_ready = Signal(DaemonStatus)
    sensors_ready = Signal(list)
    fans_ready = Signal(list)
    headers_ready = Signal(list)
    active_profile_ready = Signal(object)  # ActiveProfileInfo | None
    hw_diagnostics_ready = Signal(object)  # HardwareDiagnosticsResult

    # Connection state
    connected = Signal()
    disconnected = Signal()

    def __init__(self, socket_path: str, history: HistoryStore | None = None) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None
        self._poll_count = 0
        self._consecutive_failures = 0
        # F-5: in-flight guard. The 1 Hz timer→poll() connection is queued, so a
        # poll that runs longer than the interval would otherwise pile up behind
        # it and fire as a back-to-back burst. While one poll() is running, the
        # next invocation is skipped outright. The worker lives on a single
        # thread, so a plain bool is race-free (no lock needed).
        self._in_flight = False
        # DEC-229: latches once /diagnostics/hardware has been fetched. DMI
        # board identity cannot change without a reboot, so one success per
        # process is enough — but a *failed* attempt must not latch, or a GUI
        # started before the daemon would never learn the board.
        self._hw_diag_sent = False
        self._caps_interval = max(1, CAPABILITIES_REFRESH_INTERVAL_S * 1000 // POLL_INTERVAL_MS)
        self._history = history
        # P2-D: dirs already announced to the daemon. Logged at INFO the
        # first time we send a dir (a real state change), DEBUG on later
        # re-registrations (post-reconnect, daemon may have restarted —
        # call is still made for safety, but it's almost always a no-op
        # on the daemon side and shouldn't clutter the journal).
        self._announced_dirs: set[str] = set()

    def _ensure_client(self) -> DaemonClient:
        if self._client is None:
            self._client = DaemonClient(socket_path=self._socket_path)
        return self._client

    def poll(self) -> None:
        """Execute one poll cycle — called from the timer thread.

        F-5: skip this invocation entirely if a prior poll() is still running.
        A poll slower than the 1 Hz interval would otherwise queue behind the
        timer and fire as a burst; the guard drops the overlapping tick instead.
        The flag is set here and cleared in ``finally`` so a raising cycle can
        never wedge polling off permanently.
        """
        if self._in_flight:
            return
        self._in_flight = True
        try:
            self._poll_once()
        finally:
            self._in_flight = False

    def _poll_once(self) -> None:
        """Body of one poll cycle (see ``poll`` for the in-flight guard)."""
        # Exponential backoff: skip cycles when daemon is unreachable.
        # After first failure: retry every 2nd cycle, then 4th, capped at 8s.
        # 8s cap is appropriate for local Unix socket (not network service).
        if self._consecutive_failures > 0:
            backoff = min(8, 2**self._consecutive_failures)
            if self._poll_count % backoff != 0:
                self._poll_count += 1
                return

        try:
            client = self._ensure_client()

            # Capabilities + active profile: on the first successful poll and
            # then every _caps_interval cycles (DEC-146 P3-1 — the daemon can
            # gain/lose hardware or profiles without a reconnect; periodic
            # re-fetch keeps capabilities, headers, and the active profile
            # from going stale between reconnects).
            if self._poll_count % self._caps_interval == 0:
                self.capabilities_ready.emit(client.capabilities())
                self.headers_ready.emit(client.hwmon_headers())
                try:
                    self.active_profile_ready.emit(client.active_profile())
                except (DaemonError, ConnectionError, OSError):
                    # Best-effort: older daemons may not support active_profile endpoint.
                    log.warning("Failed to query daemon active profile — GUI may be out of sync")
                # DEC-229: the DMI board identity keys the hwmon label fallback
                # table, so fan names on a board whose chip reports no labels
                # depend on it. Fetching it here (~0.6 ms, once) makes those
                # names correct from the first poll; previously nothing outside
                # the System State page ever asked, so the board stayed unknown
                # until the user happened to visit that page.
                if not self._hw_diag_sent:
                    try:
                        self.hw_diagnostics_ready.emit(client.hardware_diagnostics())
                        self._hw_diag_sent = True
                    except Exception as e:
                        # Deliberately broader than the poll cycle's own handler.
                        # This is a cosmetic naming lookup; it must never be able
                        # to affect telemetry. `parse_hardware_diagnostics` does
                        # bare `data.get(...)`, so a well-formed 200 carrying a
                        # malformed body raises AttributeError/TypeError — which
                        # the narrow tuple missed. AttributeError escaped BOTH
                        # handlers, and because it raised before `_poll_count +=
                        # 1` the caps branch re-fired every tick: no status /
                        # sensors / fans, no connected or disconnected emit (so
                        # no backoff and no state change), and one CRITICAL per
                        # second into the bounded event deque the support bundle
                        # reads. TypeError merely reached the outer handler and
                        # faked a disconnect. Neither is reachable against a
                        # well-formed daemon, but the blast radius is the whole
                        # GUI and the cost of catching broadly here is nil.
                        log.debug("Hardware diagnostics prefetch failed: %s", e)
                # Register the GUI's profile directory with the daemon so
                # POST /profile/activate accepts GUI-owned profile paths. Runs
                # on this worker thread to avoid stalling the Qt main loop on
                # a slow or half-dead daemon (API_TIMEOUT_S = 5s).
                # Called on every reconnect because the daemon may have
                # restarted with a stale search-dir list. The endpoint is
                # additive and deduplicated.
                self._register_profile_search_dir(client)

            # Use batch endpoint to reduce HTTP overhead (3 calls → 1)
            # (sensors list needed for history pre-fill below)
            sensors = []
            try:
                status, sensors, fans = client.poll()
            except (DaemonError, ConnectionError, OSError, KeyError, ValueError) as e:
                log.debug("Batch poll failed, falling back to individual endpoints: %s", e)
                # Fetch all three before emitting — a partial fallback must not
                # leave a fresh status paired with stale fans/sensors. If any
                # leg raises, the enclosing DaemonError handler marks the cycle
                # disconnected instead of emitting partial state.
                status = client.status()
                sensors = client.sensors()
                fans = client.fans()
            self.status_ready.emit(status)
            self.sensors_ready.emit(sensors)
            self.fans_ready.emit(fans)

            # Pre-fill history from daemon on first successful poll
            if self._poll_count == 0 and self._history and sensors:
                self._prefill_history(client, sensors)

            self.connected.emit()
            if self._consecutive_failures > 0:
                # Reconnected after failure — force capabilities re-fetch on
                # next cycle (P1-G2: daemon may have restarted with different
                # hardware while we were disconnected).
                self._poll_count = 0
            else:
                self._poll_count += 1
            self._consecutive_failures = 0

        except (DaemonError, ConnectionError, OSError, KeyError, ValueError, TypeError) as e:
            # P3-1: parse-shaped exceptions (KeyError/ValueError/TypeError from
            # a malformed-but-200 payload) and raw transport errors from the
            # fallback legs / capabilities() previously
            # escaped this handler and landed in the Qt excepthook once per
            # second with no backoff. Treat them all as a failed cycle.
            self._consecutive_failures += 1
            if self._consecutive_failures <= 3:
                log.warning("Poll failed: %s: %s", type(e).__name__, e)
            elif self._consecutive_failures == 4:
                log.warning(
                    "Poll failed: %s: %s (suppressing repeated failures)", type(e).__name__, e
                )
            self._poll_count += 1
            self.disconnected.emit()
            # Drop client so it reconnects next attempt
            self._close_client()

    def _prefill_history(self, client: DaemonClient, sensors: list) -> None:
        """Fetch daemon-side history for each sensor and pre-fill the local store."""
        for s in sensors:
            try:
                history = client.sensor_history(s.id)
                if history.points:
                    self._history.prefill_sensor(s.id, history.points)
            except (DaemonError, ConnectionError, OSError):
                # Best-effort prefill: missing history is non-fatal.
                log.debug("Failed to fetch history for %s", s.id)

    def _register_profile_search_dir(self, client: DaemonClient) -> None:
        """Tell the daemon where this GUI stores its profiles.

        Called on first poll and on every reconnect (because the daemon may
        have restarted while we were disconnected and lost its in-memory
        search-dir list). The first time per process we log at INFO so the
        registration is visible to operators; subsequent re-registrations
        log at DEBUG because they are almost always a no-op on the daemon
        side (the endpoint is additive and deduplicated).
        """
        dir_path = str(profiles_dir())
        try:
            client.update_profile_search_dirs(add=[dir_path])
        except DaemonError as exc:
            log.warning(
                "Could not register profile search dir %s with daemon: %s",
                dir_path,
                exc.message,
            )
            return
        except (ConnectionError, OSError) as exc:
            log.warning(
                "Connection error registering profile search dir %s: %s",
                dir_path,
                exc,
            )
            return

        if dir_path in self._announced_dirs:
            log.debug("Re-registered profile search dir with daemon: %s", dir_path)
        else:
            log.info("Registered profile search dir with daemon: %s", dir_path)
            self._announced_dirs.add(dir_path)

    def _close_client(self) -> None:
        if self._client:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None

    def shutdown(self) -> None:
        self._close_client()


class PollingService(QObject):
    """Manages the polling lifecycle — timer + worker thread."""

    def __init__(
        self,
        state: AppState,
        socket_path: str,
        history: HistoryStore | None = None,
        parent: QObject | None = None,
        diagnostics: DiagnosticsService | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._diag = diagnostics
        # Track previous connection state so we only emit a diag event on
        # the connected↔disconnected transition, not on every poll cycle.
        self._was_connected: bool | None = None
        self._running = False

        # Worker thread
        self._thread = QThread()
        self._worker = _PollWorker(socket_path, history=history)
        self._worker.moveToThread(self._thread)

        # Wire worker signals to state updates
        self._worker.capabilities_ready.connect(state.set_capabilities)
        self._worker.status_ready.connect(state.set_status)
        self._worker.sensors_ready.connect(state.set_sensors)
        self._worker.fans_ready.connect(state.set_fans)
        self._worker.headers_ready.connect(state.set_hwmon_headers)
        self._worker.active_profile_ready.connect(self._on_active_profile)
        self._worker.hw_diagnostics_ready.connect(self._on_hw_diagnostics)
        self._worker.connected.connect(self._on_connected)
        self._worker.disconnected.connect(self._on_disconnected)

        # Timer runs on main thread, triggers worker.poll() on worker thread
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._worker.poll, Qt.ConnectionType.QueuedConnection)

        self._thread.start()

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._timer.start()
            log.info("Polling started (interval=%dms)", POLL_INTERVAL_MS)

    def stop(self) -> None:
        if self._running:
            self._running = False
            self._timer.stop()
            log.info("Polling stopped")

    def shutdown(self) -> None:
        self.stop()
        self._worker.shutdown()
        self._thread.quit()
        if not self._thread.wait(2000):
            log.warning("Polling thread did not stop within 2s, terminating")
            self._thread.terminate()
            self._thread.wait(1000)

    def _on_connected(self) -> None:
        # Stamp every successful poll for the dashboard "Updated Xs ago" strip.
        self._state.mark_poll_success()
        was_connected = self._was_connected
        self._was_connected = True
        if self._state.connection != ConnectionState.CONNECTED:
            log.info("Daemon connection established")
        self._state.set_connection(ConnectionState.CONNECTED)
        if self._state.mode == OperationMode.READ_ONLY:
            self._state.set_mode(OperationMode.AUTOMATIC)
            log.info("Mode set to AUTOMATIC (daemon connected)")
        if was_connected is False:
            # DEC-146 P3-2: a true reconnect (not the first-ever connect)
            # invalidates session-scoped state — the daemon may have restarted
            # (resetting GPU fans to auto on its way down), so the session
            # min/max describe a session that no longer exists.
            self._state.reset_session_stats()
        # DEC-111: emit a single event per disconnect→connect transition so
        # the event log reads "Daemon connected" rather than appending one
        # row per successful poll. ``was_connected is None`` is the very
        # first cycle after startup; that's worth recording too.
        if self._diag is not None and was_connected is not True:
            self._diag.log_event("info", "polling", "Daemon connected")

    def _on_active_profile(self, info: ActiveProfileInfo | None) -> None:
        """Update AppState with the daemon's active profile on connect/reconnect."""
        if info and info.active:
            log.info("Daemon active profile: %s (id=%s)", info.profile_name, info.profile_id)
            self._state.set_active_profile(info.profile_name)
            if self._diag is not None:
                self._diag.log_event(
                    "info", "polling", f"Daemon active profile: {info.profile_name}"
                )
        else:
            log.debug("Daemon has no active profile")

    def _on_hw_diagnostics(self, result) -> None:
        """Hand the startup ``/diagnostics/hardware`` result to its single writer.

        DEC-229: ``DiagnosticsService.set_hw_diagnostics`` owns both the shared
        cache and ``AppState.board_info``; polling deliberately does not write
        either directly. With no diagnostics service wired the board stays
        unknown and the label resolver falls back to ``pwmN`` — degraded names,
        never wrong ones.
        """
        if self._diag is not None:
            self._diag.set_hw_diagnostics(result)

    def _on_disconnected(self) -> None:
        was_connected = self._was_connected
        self._was_connected = False
        self._state.set_connection(ConnectionState.DISCONNECTED)
        if self._state.mode == OperationMode.AUTOMATIC:
            self._state.set_mode(OperationMode.READ_ONLY)
        # DEC-111: only emit on the connected→disconnected edge — the worker
        # signals ``disconnected`` on every failed poll, so an unconditional
        # log_event would flood the event log with one row per second while
        # the daemon was unreachable.
        if self._diag is not None and was_connected is True:
            self._diag.log_event("warning", "polling", "Daemon disconnected")
