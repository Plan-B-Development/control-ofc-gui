"""Polling service — periodic reads from daemon API, updates AppState.

Runs on a QTimer. API calls execute in a QThread worker to avoid blocking
the UI. Results are posted back to AppState on the main thread via signals.
"""

from __future__ import annotations

import contextlib
import logging

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
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
from control_ofc.services.daemon_features import daemon_supports
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.history_store import HistoryStore

log = logging.getLogger(__name__)


def _batch_unsupported(exc: Exception) -> bool:
    """True when a failed ``GET /poll`` means this daemon cannot serve the batch
    read, so the three single endpoints are worth asking (FFA-g).

    A 404 (no such route) or a body that would not parse. Never a transport
    failure: a daemon that timed out or went away on ``/poll`` will do the same on
    ``/status``, and asking anyway doubled every hung cycle.
    """
    if isinstance(exc, (DaemonTimeout, DaemonUnavailable)):
        return False
    if isinstance(exc, DaemonError):
        return exc.status == 404 or exc.code == "parse_error"
    return isinstance(exc, (KeyError, ValueError, TypeError))


class _PollWorker(QObject):
    """Runs in a QThread — makes blocking API calls."""

    # Results
    capabilities_ready = Signal(Capabilities)
    status_ready = Signal(DaemonStatus)
    sensors_ready = Signal(list)
    fans_ready = Signal(list)
    headers_ready = Signal(list)
    #: AIO-MB Phase 6: the cooling-device topology (DEC-316 surface).
    #: Emitted on the CAPABILITIES interval, not the 1 Hz poll — topology
    #: is static configuration, and §19 forbids increasing poll load
    #: simply because more fields became visible.
    cooling_devices_ready = Signal(object)  # CoolingDeviceInventory
    #: `ROLE-f`: each OpenFan channel's role, on the capabilities interval.
    openfan_roles_ready = Signal(list)  # list[OpenFanRole]
    active_profile_ready = Signal(object)  # ActiveProfileInfo | None
    hw_diagnostics_ready = Signal(object)  # HardwareDiagnosticsResult
    #: The daemon's running poll interval in ms (`GET /config`).
    poll_interval_ready = Signal(int)
    #: The daemon restarted between two successful polls (its uptime went back,
    #: or its version changed) without a poll failing in between.
    daemon_restarted = Signal()

    # Connection state
    connected = Signal()
    disconnected = Signal()
    #: FFA-g: emitted when a ``poll()`` invocation returns, whatever it did, so
    #: ``PollingService`` can request the next cycle only once this one is over.
    cycle_done = Signal()

    def __init__(self, socket_path: str, history: HistoryStore | None = None) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._client: DaemonClient | None = None
        self._poll_count = 0
        self._consecutive_failures = 0
        # DEC-229: /diagnostics/hardware carries the DMI board identity, which
        # keys fan names. It is re-read on every capabilities cycle (~0.6 ms per
        # 300 s), on a reconnect and after a restart, not latched once per
        # process: it also carries the thermal trip point and the coolant limit
        # System State shows beside the live thermal state, and a latched copy
        # kept a limit the user had since changed, or the fallback trip point of
        # a daemon that had not finished its first tick. A request (a coolant
        # limit write) re-reads it on the next cycle.
        self._hw_diag_refresh_pending = False
        # Restart detection: the last successful poll's daemon uptime and version.
        # A restart quick enough that no poll failed leaves capabilities, headers
        # and the active profile up to 300 s stale otherwise.
        self._last_uptime_s: int | None = None
        self._last_daemon_version: str | None = None
        self._caps_refresh_pending = False
        self._caps_interval = max(1, CAPABILITIES_REFRESH_INTERVAL_S * 1000 // POLL_INTERVAL_MS)
        self._history = history
        # P2-D: dirs already announced to the daemon. Logged at INFO the
        # first time we send a dir (a real state change), DEBUG on later
        # re-registrations (post-reconnect, daemon may have restarted —
        # call is still made for safety, but it's almost always a no-op
        # on the daemon side and shouldn't clutter the journal).
        self._announced_dirs: set[str] = set()
        # DEC-256: shutdown latch. `shutdown()` closed the client, but the 1 Hz
        # timer→poll() connection is QUEUED, so an invocation already sitting in
        # the worker thread's event queue still ran afterwards — and
        # `_ensure_client()` cheerfully rebuilt the very client shutdown had just
        # closed, opening a fresh socket and blocking on it. That kept the thread
        # busy past `wait(2000)` and forced `QThread::terminate()`, which is how
        # you orphan a half-written request. The latch makes post-shutdown work a
        # no-op instead. Single-threaded worker, so a plain bool needs no lock.
        self._shutting_down = False
        # `TS-ae`: since DEC-384 `stop_permitted` and `effective_min_pwm_pct`
        # follow the active profile, so headers read up to 300 s ago can promise
        # the wrong thing about a pump. The worker re-reads them when the poll
        # shows the active profile changed, or when the GUI asks after its own
        # activation. `None` = no poll observed yet (the caps cycle reads them).
        # Single-threaded worker, so neither needs a lock.
        self._last_profile_key: tuple[bool | None, str | None] | None = None
        self._headers_refresh_pending = False
        # DEC-456: a verify or characterisation sweep that just ended may have
        # written a per-header verdict the headers carry. `verify_active` going
        # true → false marks the end; the re-read runs one cycle LATER, because
        # a verify or sweep persists its verdict after it releases the pause.
        self._last_verify_active = False
        self._headers_refresh_next_cycle = False

    def request_hw_diagnostics_refresh(self) -> None:
        """Re-read ``/diagnostics/hardware`` on the next cycle (queued from
        ``AppState``, like :meth:`request_headers_refresh`)."""
        self._hw_diag_refresh_pending = True

    def request_headers_refresh(self) -> None:
        """Re-read ``/hwmon/headers`` on the next cycle (`TS-ae`).

        Reached through a queued connection from ``AppState``, so it runs on
        this worker's thread and only ever sets the flag the poll consumes.
        """
        self._headers_refresh_pending = True

    def _ensure_client(self) -> DaemonClient:
        # DEC-256: never resurrect the client after shutdown. This is the half
        # that made the latch necessary — closing the client is not enough when
        # the next queued call simply builds another one.
        if self._shutting_down:
            raise RuntimeError("poll worker is shutting down")
        if self._client is None:
            self._client = DaemonClient(socket_path=self._socket_path)
        return self._client

    def poll(self) -> None:
        """Execute one poll cycle on the worker thread, then report it done.

        ``cycle_done`` is emitted in ``finally`` so a raising cycle can never
        wedge polling off: ``PollingService`` requests no further cycle until it
        hears this one ended (FFA-g).
        """
        try:
            if not self._shutting_down:
                self._poll_once()
        finally:
            self.cycle_done.emit()

    def _poll_once(self) -> None:
        """Body of one poll cycle (see ``poll``)."""
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
            if self._poll_count % self._caps_interval == 0 or self._caps_refresh_pending:
                self._caps_refresh_pending = False
                caps = client.capabilities()
                self.capabilities_ready.emit(caps)
                self.headers_ready.emit(client.hwmon_headers())
                # A pending `TS-ae` re-read is satisfied by this one.
                self._headers_refresh_pending = False
                # Cooling-device topology (AIO-MB Phase 6). Capability-gated:
                # a pre-2.31 daemon 404s the route, and an unguarded call would
                # log a spurious error every five minutes on every older setup.
                if getattr(caps.control, "cooling_devices", False):
                    try:
                        self.cooling_devices_ready.emit(client.get_cooling_devices())
                    except (DaemonError, ConnectionError, OSError):
                        # Best-effort and display-only: the Hardware page falls
                        # back to per-header cards, which never depended on
                        # topology (§1). Never fail a poll over it.
                        log.warning("Failed to query cooling devices — topology view may be stale")
                # `ROLE-f`: capability-gated like the topology above — an older
                # daemon 404s the route.
                if daemon_supports("openfan_header_roles", caps) is True:
                    try:
                        self.openfan_roles_ready.emit(client.openfan_roles())
                    except (DaemonError, ConnectionError, OSError):
                        log.warning("Failed to query OpenFan channel roles")
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
                self._hw_diag_refresh_pending = True
                self._fetch_poll_interval(client, caps)
                # Register the GUI's profile directory with the daemon so
                # POST /profile/activate accepts GUI-owned profile paths. Runs
                # on this worker thread to avoid stalling the Qt main loop on
                # a slow or half-dead daemon (API_TIMEOUT_S = 5s).
                # Called on every reconnect because the daemon may have
                # restarted with a stale search-dir list. The endpoint is
                # additive and deduplicated.
                self._register_profile_search_dir(client)

            if self._hw_diag_refresh_pending:
                self._hw_diag_refresh_pending = False
                try:
                    self.hw_diagnostics_ready.emit(client.hardware_diagnostics())
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

            # Use batch endpoint to reduce HTTP overhead (3 calls → 1)
            # (sensors list needed for history pre-fill below)
            sensors = []
            try:
                status, sensors, fans = client.poll()
            except (DaemonError, KeyError, ValueError, TypeError) as e:
                if not _batch_unsupported(e):
                    raise  # a failed cycle, handled below
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
            self._refresh_headers_if_due(client, status)
            self._note_daemon_identity(status)

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

        except (
            DaemonError,
            ConnectionError,
            OSError,
            KeyError,
            ValueError,
            TypeError,
            AttributeError,
        ) as e:
            # `AttributeError` too: a list element of the wrong shape (a string
            # where a header object belongs) raises it from a parser's
            # `.get(...)`, and escaping here stopped every later update while the
            # GUI still showed "connected".
            #
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

    def _note_daemon_identity(self, status: DaemonStatus) -> None:
        """Notice a daemon restart between two successful polls.

        A restart that completes while this GUI's requests simply wait in the
        socket's queue fails no poll, so the reconnect path never runs and
        capabilities stay as the old daemon described them for up to 300 s —
        after a downgrade, offering settings the running daemon ignores. Its
        uptime going back, or its version changing, says it restarted: the next
        cycle re-reads everything a reconnect would.
        """
        # Only well-formed values count: a malformed status must not fake a restart.
        uptime = status.uptime_seconds
        if isinstance(uptime, bool) or not isinstance(uptime, int):
            uptime = None
        version = status.daemon_version if isinstance(status.daemon_version, str) else None
        version = version or None
        restarted = (
            uptime is not None and self._last_uptime_s is not None and uptime < self._last_uptime_s
        ) or (
            version is not None
            and self._last_daemon_version is not None
            and version != self._last_daemon_version
        )
        if uptime is not None:
            self._last_uptime_s = uptime
        if version is not None:
            self._last_daemon_version = version
        if restarted:
            log.info("Daemon restarted (uptime %ss, version %s); re-reading", uptime, version)
            self._caps_refresh_pending = True
            self.daemon_restarted.emit()

    def _fetch_poll_interval(self, client: DaemonClient, caps: Capabilities) -> None:
        """Learn the daemon's running poll interval, which freshness scales with.

        Best-effort: a daemon that does not report its configuration, or a read
        that fails, leaves the last known interval (the daemon default at first).
        """
        if daemon_supports("daemon_config_report", caps) is False:
            return
        try:
            key = client.get_daemon_config().get("polling.poll_interval_ms")
        except Exception as e:
            # Display-only, like the diagnostics prefetch: never fail a poll over it.
            log.debug("Daemon config read for the poll interval failed: %s", e)
            return
        running = key.running_value if key is not None else None
        if isinstance(running, int) and not isinstance(running, bool) and running > 0:
            self.poll_interval_ready.emit(running)

    def _refresh_headers_if_due(self, client: DaemonClient, status: DaemonStatus) -> None:
        """Re-read the headers when the active profile moved, a diagnostic ended,
        or on request (`TS-ae`, DEC-456).

        The profile is read from the poll's own ``(has_active_profile,
        active_profile_id)``, so a switch or deactivation made anywhere — the
        tray, the CLI, another GUI — is seen within a second. A re-apply of the
        SAME profile leaves that pair unchanged, which is why the GUI's own
        activation also requests a re-read (``request_headers_refresh``).

        Best-effort: a failed re-read is logged and not retried, so it cannot
        fake a disconnect or log once a second; the capabilities-interval
        refresh stays the backstop.
        """
        key = (status.has_active_profile, status.active_profile_id)
        if self._last_profile_key is not None and key != self._last_profile_key:
            self._headers_refresh_pending = True
        self._last_profile_key = key
        # DEC-456: consume last cycle's deferred request BEFORE looking for a new
        # edge, so each edge re-reads exactly one cycle after it was seen.
        if self._headers_refresh_next_cycle:
            self._headers_refresh_next_cycle = False
            self._headers_refresh_pending = True
        verify_active = status.verify_active is True
        if self._last_verify_active and not verify_active:
            self._headers_refresh_next_cycle = True
        self._last_verify_active = verify_active
        if not self._headers_refresh_pending:
            return
        self._headers_refresh_pending = False
        try:
            self.headers_ready.emit(client.hwmon_headers())
        except (DaemonError, ConnectionError, OSError, KeyError, ValueError, TypeError) as e:
            log.warning(
                "Could not re-read hwmon headers after a profile change or a "
                "diagnostic (the %d s refresh will): %s",
                CAPABILITIES_REFRESH_INTERVAL_S,
                e,
            )

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
        # Latch first, then close: a queued poll that slips in between must find
        # the latch already set, not an open client it can keep using.
        self._shutting_down = True
        self._close_client()


class PollingService(QObject):
    """Manages the polling lifecycle — timer + worker thread."""

    #: Queued to ``_PollWorker.poll`` on the worker thread (FFA-g gate).
    _request_poll = Signal()

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
        # `TS-ae`: queued, like the timer below — the flag lives on the worker's
        # thread and is only ever touched there.
        state.hwmon_headers_refresh_requested.connect(
            self._worker.request_headers_refresh, Qt.ConnectionType.QueuedConnection
        )
        self._worker.cooling_devices_ready.connect(state.set_cooling_devices)
        self._worker.openfan_roles_ready.connect(state.set_openfan_roles)
        self._worker.active_profile_ready.connect(self._on_active_profile)
        self._worker.hw_diagnostics_ready.connect(self._on_hw_diagnostics)
        state.hw_diagnostics_refresh_requested.connect(
            self._worker.request_hw_diagnostics_refresh, Qt.ConnectionType.QueuedConnection
        )
        self._worker.poll_interval_ready.connect(state.set_daemon_poll_interval)
        self._worker.daemon_restarted.connect(self._on_daemon_restarted)
        self._worker.connected.connect(self._on_connected)
        self._worker.disconnected.connect(self._on_disconnected)

        # Timer runs on main thread, triggers worker.poll() on worker thread.
        #
        # FFA-g: at most one cycle requested at a time. A tick queued straight to
        # the worker sits in its event queue while a poll blocks, so a daemon
        # that accepts but never answers built a backlog that ran as a burst on
        # recovery — and a guard inside the worker could not see it, since each
        # queued tick starts only after the previous one returned. The gate lives
        # here instead: a tick that finds a cycle still running is dropped.
        self._cycle_in_flight = False
        self._request_poll.connect(self._worker.poll, Qt.ConnectionType.QueuedConnection)
        self._worker.cycle_done.connect(self._on_cycle_done, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

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
        # Close the client BEFORE joining the thread: an in-flight poll is a
        # synchronous blocking call, and closing the client is the only way to
        # interrupt it so quit()/wait() can join promptly (the worker slots absorb
        # the resulting connection error). Audit 2026-07-29 F-1 proposed reordering
        # this to close-after-join to match ControlsPage, but that hangs the join
        # for a blocking call — ControlsPage's override calls are short enough to
        # join without the close; these are not (verified: the reorder deadlocked
        # test_v1_2_diagnostics' in-flight-verify teardown test).
        self._worker.shutdown()
        self._thread.quit()
        if not self._thread.wait(2000):
            log.warning("Polling thread did not stop within 2s, terminating")
            self._thread.terminate()
            self._thread.wait(1000)

    def _on_tick(self) -> None:
        if self._cycle_in_flight:
            return
        self._cycle_in_flight = True
        self._request_poll.emit()

    def _on_cycle_done(self) -> None:
        self._cycle_in_flight = False

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
        """Update AppState with the daemon's active profile on connect/reconnect.

        `CTRL-d`: forwards the **id** as well as the name. The id is what
        ``main_window`` routes into ``ProfileService.set_active`` (DEC-194), and
        it is what decides which profile the sidebar marks active and the
        Controls page edits — so dropping it here left those reading a local
        value the GUI had invented at load time.

        ``info is None`` is authoritative, not an error: the worker swallows a
        failed request before it ever emits (``polling`` § run loop), so a `None`
        that reaches this slot means the daemon answered and said nothing is
        active. Clearing both fields is therefore correct, and it is the only
        correction available against a daemon older than 2.45.0, which cannot say
        so on the 1 Hz poll (`has_active_profile`).
        """
        if info and info.active:
            log.info("Daemon active profile: %s (id=%s)", info.profile_name, info.profile_id)
            self._state.set_active_profile_id(info.profile_id or "")
            self._state.set_active_profile(info.profile_name)
            if self._diag is not None:
                self._diag.log_event(
                    "info",
                    "polling",
                    f"Daemon active profile: {info.profile_name}",
                    fields={"profile": info.profile_name, "profile_id": info.profile_id or ""},
                )
        else:
            log.debug("Daemon has no active profile")
            self._state.set_active_profile_id("")
            self._state.set_active_profile("")

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

    def _on_daemon_restarted(self) -> None:
        """A restart noticed without a failed poll (``_note_daemon_identity``).

        The same session-scoped reset a reconnect does: the session min/max
        describe a daemon session that has ended.
        """
        self._state.reset_session_stats()
        if self._diag is not None:
            self._diag.log_event("info", "polling", "Daemon restarted")

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
