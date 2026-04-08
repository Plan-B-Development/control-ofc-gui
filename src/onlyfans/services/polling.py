"""Polling service — periodic reads from daemon API, updates AppState.

Runs on a QTimer. API calls execute in a QThread worker to avoid blocking
the UI. Results are posted back to AppState on the main thread via signals.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from onlyfans.api.client import DaemonClient
from onlyfans.api.errors import DaemonError
from onlyfans.api.models import (
    ActiveProfileInfo,
    Capabilities,
    ConnectionState,
    DaemonStatus,
    LeaseState,
)
from onlyfans.constants import CAPABILITIES_REFRESH_INTERVAL_S, POLL_INTERVAL_MS
from onlyfans.services.app_state import AppState
from onlyfans.services.history_store import HistoryStore

log = logging.getLogger(__name__)


class _PollWorker(QObject):
    """Runs in a QThread — makes blocking API calls."""

    # Results
    capabilities_ready = Signal(Capabilities)
    status_ready = Signal(DaemonStatus)
    sensors_ready = Signal(list)
    fans_ready = Signal(list)
    headers_ready = Signal(list)
    lease_ready = Signal(LeaseState)
    active_profile_ready = Signal(object)  # ActiveProfileInfo | None

    # Connection state
    connected = Signal()
    disconnected = Signal()

    def __init__(self, socket_path: str, history: HistoryStore | None = None) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None
        self._poll_count = 0
        self._consecutive_failures = 0
        self._caps_interval = max(1, CAPABILITIES_REFRESH_INTERVAL_S * 1000 // POLL_INTERVAL_MS)
        self._history = history

    def _ensure_client(self) -> DaemonClient:
        if self._client is None:
            self._client = DaemonClient(socket_path=self._socket_path)
        return self._client

    def poll(self) -> None:
        """Execute one poll cycle — called from the timer thread."""
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

            # Capabilities + active profile: only on first successful poll
            if self._poll_count == 0:
                self.capabilities_ready.emit(client.capabilities())
                self.headers_ready.emit(client.hwmon_headers())
                try:
                    self.active_profile_ready.emit(client.active_profile())
                except Exception:
                    log.warning("Failed to query daemon active profile — GUI may be out of sync")

            # Use batch endpoint to reduce HTTP overhead (3 calls → 1)
            # (sensors list needed for history pre-fill below)
            sensors = []
            try:
                status, sensors, fans = client.poll()
                self.status_ready.emit(status)
                self.sensors_ready.emit(sensors)
                self.fans_ready.emit(fans)
            except Exception as e:
                log.debug("Batch poll failed, falling back to individual endpoints: %s", e)
                self.status_ready.emit(client.status())
                sensors = client.sensors()
                self.sensors_ready.emit(sensors)
                self.fans_ready.emit(client.fans())

            # Pre-fill history from daemon on first successful poll
            if self._poll_count == 0 and self._history and sensors:
                self._prefill_history(client, sensors)

            self.lease_ready.emit(client.hwmon_lease_status())

            self.connected.emit()
            if self._consecutive_failures > 0:
                # Reconnected after failure — force capabilities re-fetch on
                # next cycle (P1-G2: daemon may have restarted with different
                # hardware while we were disconnected).
                self._poll_count = 0
            else:
                self._poll_count += 1
            self._consecutive_failures = 0

        except DaemonError as e:
            self._consecutive_failures += 1
            if self._consecutive_failures <= 3:
                log.warning("Poll failed: %s", e)
            elif self._consecutive_failures == 4:
                log.warning("Poll failed: %s (suppressing repeated failures)", e)
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
            except Exception:
                log.debug("Failed to fetch history for %s", s.id)

    def _close_client(self) -> None:
        if self._client:
            import contextlib

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
    ) -> None:
        super().__init__(parent)
        self._state = state
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
        self._worker.lease_ready.connect(state.set_lease)
        self._worker.active_profile_ready.connect(self._on_active_profile)
        self._worker.connected.connect(self._on_connected)
        self._worker.disconnected.connect(self._on_disconnected)

        # Timer runs on main thread, triggers worker.poll() on worker thread
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._worker.poll)

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
        self._thread.wait(2000)

    def _on_connected(self) -> None:
        if self._state.connection != ConnectionState.CONNECTED:
            log.info("Daemon connection established")
        self._state.set_connection(ConnectionState.CONNECTED)
        # Transition from READ_ONLY to AUTOMATIC when daemon is available
        from onlyfans.api.models import OperationMode

        if self._state.mode == OperationMode.READ_ONLY:
            self._state.set_mode(OperationMode.AUTOMATIC)
            log.info("Mode set to AUTOMATIC (daemon connected)")

    def _on_active_profile(self, info: ActiveProfileInfo | None) -> None:
        """Update AppState with the daemon's active profile on connect/reconnect."""
        if info and info.active:
            log.info("Daemon active profile: %s (id=%s)", info.profile_name, info.profile_id)
            self._state.set_active_profile(info.profile_name)
        else:
            log.debug("Daemon has no active profile")

    def _on_disconnected(self) -> None:
        self._state.set_connection(ConnectionState.DISCONNECTED)
        from onlyfans.api.models import OperationMode

        if self._state.mode == OperationMode.AUTOMATIC:
            self._state.set_mode(OperationMode.READ_ONLY)
