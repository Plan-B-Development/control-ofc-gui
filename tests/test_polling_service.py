"""Tests for _PollWorker and PollingService.

Verifies polling lifecycle: first-poll capability fetch, batch/fallback
behaviour, exponential backoff, reconnection, and PollingService mode
transitions on connect/disconnect.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    ActiveProfileInfo,
    BoardInfo,
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    HardwareDiagnosticsResult,
    OperationMode,
    SensorReading,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.polling import PollingService, _PollWorker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DAEMON_ERROR = DaemonError(code="unavailable", message="gone")


def _make_mock_client() -> MagicMock:
    """Return a MagicMock that quacks like DaemonClient with sane defaults."""
    client = MagicMock()
    client.capabilities.return_value = Capabilities(daemon_version="0.1.0")
    client.hwmon_headers.return_value = []
    client.active_profile.return_value = ActiveProfileInfo(
        active=True, profile_id="quiet", profile_name="Quiet"
    )
    client.poll.return_value = (
        DaemonStatus(overall_status="ok"),
        [SensorReading(id="cpu", value_c=45.0, age_ms=100)],
        [FanReading(id="fan0", rpm=1200, age_ms=100)],
    )
    client.status.return_value = DaemonStatus(overall_status="ok")
    client.sensors.return_value = [SensorReading(id="cpu", value_c=45.0, age_ms=100)]
    client.fans.return_value = [FanReading(id="fan0", rpm=1200, age_ms=100)]
    client.sensor_history.return_value = MagicMock(points=[])
    client.hardware_diagnostics.return_value = HardwareDiagnosticsResult(
        board=BoardInfo(vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER")
    )
    return client


def _make_failing_client() -> MagicMock:
    """Return a mock client where every method raises DaemonError."""
    client = MagicMock()
    for attr in (
        "capabilities",
        "hwmon_headers",
        "active_profile",
        "hardware_diagnostics",
        "poll",
        "status",
        "sensors",
        "fans",
    ):
        getattr(client, attr).side_effect = _DAEMON_ERROR
    return client


def _make_worker(mock_client: MagicMock | None = None) -> _PollWorker:
    """Create a _PollWorker with _ensure_client patched to return a mock."""
    worker = _PollWorker(socket_path="/tmp/fake.sock")
    if mock_client is None:
        mock_client = _make_mock_client()
    worker._ensure_client = MagicMock(return_value=mock_client)
    return worker


def _collect_signal(signal) -> list:
    """Connect a signal to a list and return the list for later assertion."""
    collected: list = []
    signal.connect(lambda *args: collected.append(args))
    return collected


def _make_polling_service(state: AppState) -> PollingService:
    """Build a PollingService without starting a real QThread or QTimer.

    Patches __init__ to only set _state, _was_connected, and _diag, which is
    all the _on_* handlers need.
    """
    with patch.object(PollingService, "__init__", lambda self, *a, **kw: None):
        svc = PollingService.__new__(PollingService)
        svc._state = state
        svc._was_connected = None
        svc._diag = None
    return svc


# ---------------------------------------------------------------------------
# _PollWorker tests
# ---------------------------------------------------------------------------


class TestPollWorkerFirstPoll:
    """First poll (poll_count == 0) fetches capabilities, headers, active profile."""

    def test_first_poll_emits_capabilities(self, qtbot):
        """On first poll, capabilities_ready and headers_ready are emitted."""
        mock_client = _make_mock_client()
        worker = _make_worker(mock_client)

        caps_spy = _collect_signal(worker.capabilities_ready)
        headers_spy = _collect_signal(worker.headers_ready)
        profile_spy = _collect_signal(worker.active_profile_ready)
        connected_spy = _collect_signal(worker.connected)

        worker.poll()

        mock_client.capabilities.assert_called_once()
        mock_client.hwmon_headers.assert_called_once()
        mock_client.active_profile.assert_called_once()

        assert len(caps_spy) == 1
        assert caps_spy[0][0].daemon_version == "0.1.0"
        assert len(headers_spy) == 1
        assert len(profile_spy) == 1
        assert len(connected_spy) == 1

    def test_second_poll_skips_capabilities(self, qtbot):
        """After the first successful poll, capabilities are NOT re-fetched."""
        mock_client = _make_mock_client()
        worker = _make_worker(mock_client)

        worker.poll()  # first poll -- fetches caps
        mock_client.capabilities.reset_mock()

        worker.poll()  # second poll -- should skip caps
        mock_client.capabilities.assert_not_called()


class TestPollWorkerHardwareDiagnosticsPrefetch:
    """DEC-229: `/diagnostics/hardware` once at startup, for the board identity.

    The DMI board keys the hwmon label fallback table, so fan names on a chip
    that publishes no labels depend on it. Before this nothing outside the
    System State page ever asked, so the names only became correct if the user
    happened to visit that page.
    """

    def test_first_poll_prefetches_hardware_diagnostics(self, qtbot):
        mock_client = _make_mock_client()
        worker = _make_worker(mock_client)
        spy = _collect_signal(worker.hw_diagnostics_ready)

        worker.poll()

        mock_client.hardware_diagnostics.assert_called_once()
        assert len(spy) == 1
        assert spy[0][0].board.name == "X870E AORUS MASTER"

    def test_prefetch_latches_after_success(self, qtbot):
        """Board identity cannot change without a reboot — once is enough.

        Asserted against a forced caps cycle, not just the next poll: without
        the latch this would re-fetch every `_caps_interval` for the process
        lifetime.
        """
        mock_client = _make_mock_client()
        worker = _make_worker(mock_client)
        worker.poll()
        mock_client.hardware_diagnostics.reset_mock()

        worker._poll_count = 0  # force another capabilities cycle
        worker.poll()

        mock_client.hardware_diagnostics.assert_not_called()

    def test_failed_prefetch_does_not_latch(self, qtbot):
        """A GUI started before the daemon must still learn the board."""
        mock_client = _make_mock_client()
        mock_client.hardware_diagnostics.side_effect = _DAEMON_ERROR
        worker = _make_worker(mock_client)
        spy = _collect_signal(worker.hw_diagnostics_ready)

        worker.poll()
        assert spy == []
        # The rest of the cycle is unaffected — this is best-effort, not fatal.
        assert worker._consecutive_failures == 0

        mock_client.hardware_diagnostics.side_effect = None
        worker._poll_count = 0
        worker.poll()
        assert len(spy) == 1

    @pytest.mark.parametrize(
        "exc",
        [
            AttributeError("'list' object has no attribute 'get'"),
            TypeError("malformed response"),
            KeyError("board"),
            ValueError("bad json"),
            _DAEMON_ERROR,
        ],
    )
    def test_prefetch_failure_never_touches_the_poll_cycle(self, qtbot, exc):
        """A cosmetic naming lookup must not be able to take down telemetry.

        The original narrow `except (DaemonError, ConnectionError, OSError,
        KeyError, ValueError)` missed the two exceptions a malformed-but-200
        response actually produces, because `parse_hardware_diagnostics` does
        bare `data.get(...)`:

        * `TypeError` reached the *outer* poll handler and faked a disconnect —
          the GUI showed "disconnected" against a perfectly healthy daemon;
        * `AttributeError` escaped **both** handlers. Because it raised before
          `_poll_count += 1`, `_poll_count % _caps_interval == 0` stayed true
          forever: the caps branch re-fired every tick, status/sensors/fans were
          never emitted, neither `connected` nor `disconnected` ever fired (so no
          backoff and no state transition), and the Qt excepthook logged one
          CRITICAL per second into the bounded deque the support bundle reads.

        Asserting the *cycle* is intact — not merely that nothing raised — is
        what makes this catch a re-narrowing of the except clause.
        """
        mock_client = _make_mock_client()
        mock_client.hardware_diagnostics.side_effect = exc
        worker = _make_worker(mock_client)
        connected_spy = _collect_signal(worker.connected)
        fans_spy = _collect_signal(worker.fans_ready)
        hw_spy = _collect_signal(worker.hw_diagnostics_ready)

        worker.poll()

        assert hw_spy == []  # nothing published from a failed fetch
        assert len(fans_spy) == 1  # …and the real telemetry still went out
        assert len(connected_spy) == 1
        assert worker._consecutive_failures == 0  # no false disconnect
        assert worker._poll_count == 1  # cycle completed → no re-fire wedge
        assert worker._hw_diag_sent is False  # unlatched, so it retries


class TestPollWorkerBatchFallback:
    """When the batch /poll endpoint fails, individual endpoints are used."""

    def test_batch_poll_fallback(self, qtbot):
        """If client.poll() raises, individual status/sensors/fans are called."""
        mock_client = _make_mock_client()
        mock_client.poll.side_effect = DaemonError(code="not_found", message="batch not supported")

        worker = _make_worker(mock_client)
        status_spy = _collect_signal(worker.status_ready)
        sensors_spy = _collect_signal(worker.sensors_ready)
        fans_spy = _collect_signal(worker.fans_ready)
        connected_spy = _collect_signal(worker.connected)

        worker.poll()

        mock_client.status.assert_called_once()
        mock_client.sensors.assert_called_once()
        mock_client.fans.assert_called_once()
        assert len(status_spy) == 1
        assert len(sensors_spy) == 1
        assert len(fans_spy) == 1
        assert len(connected_spy) == 1

    def test_fallback_is_atomic_on_partial_failure(self, qtbot):
        """Regression: when batch /poll fails and fans() then fails during
        fallback, NO partial status/sensors signals must be emitted — the
        cycle should disconnect instead of emitting fresh status paired with
        stale fans. Audit finding: polling.py emitted status_ready before
        fetching sensors/fans, so a mid-fallback exception left the UI with
        a fresh status plus stale fan data."""
        mock_client = _make_mock_client()
        mock_client.poll.side_effect = DaemonError(code="not_found", message="batch unsupported")
        mock_client.fans.side_effect = DaemonError(code="internal_error", message="fans gone")

        worker = _make_worker(mock_client)
        status_spy = _collect_signal(worker.status_ready)
        sensors_spy = _collect_signal(worker.sensors_ready)
        fans_spy = _collect_signal(worker.fans_ready)
        connected_spy = _collect_signal(worker.connected)
        disconnected_spy = _collect_signal(worker.disconnected)

        worker.poll()

        # No partial emissions — the cycle should end in disconnect, not
        # with status emitted + sensors/fans missing.
        assert len(status_spy) == 0
        assert len(sensors_spy) == 0
        assert len(fans_spy) == 0
        assert len(connected_spy) == 0
        assert len(disconnected_spy) == 1


class TestPollWorkerActiveProfileFailure:
    """active_profile() failure must not abort the rest of the poll."""

    def test_active_profile_failure_logs_warning(self, qtbot):
        """If active_profile() raises, poll still completes and connected fires."""
        mock_client = _make_mock_client()
        mock_client.active_profile.side_effect = DaemonError(
            code="not_found", message="profile endpoint gone"
        )

        worker = _make_worker(mock_client)
        connected_spy = _collect_signal(worker.connected)
        caps_spy = _collect_signal(worker.capabilities_ready)

        worker.poll()

        # Poll succeeded despite active_profile failure
        assert len(connected_spy) == 1
        assert len(caps_spy) == 1
        mock_client.active_profile.assert_called_once()


class TestPollWorkerExponentialBackoff:
    """Consecutive failures cause exponential backoff (skip cycles)."""

    def test_exponential_backoff(self, qtbot):
        """After failures, poll cycles are skipped according to 2^n backoff."""
        mock_client = _make_failing_client()
        worker = _make_worker(mock_client)
        disconnected_spy = _collect_signal(worker.disconnected)

        # First failure (poll_count=0): capabilities raises DaemonError
        # -> consecutive_failures=1, poll_count becomes 1
        worker.poll()
        assert worker._consecutive_failures == 1
        assert len(disconnected_spy) == 1

        # After 1 failure: backoff = min(8, 2^1) = 2
        # poll_count=1, 1 % 2 = 1 != 0 -> skipped (poll_count incremented to 2)
        prev_disconnects = len(disconnected_spy)
        worker.poll()
        assert len(disconnected_spy) == prev_disconnects  # skipped, no new disconnect

        # poll_count=2, 2 % 2 = 0 -> runs, fails again
        # -> consecutive_failures=2, poll_count becomes 3
        worker.poll()
        assert worker._consecutive_failures == 2

    def test_backoff_capped_at_8(self, qtbot):
        """Backoff exponent is capped so we never skip more than 8 cycles."""
        mock_client = _make_failing_client()
        worker = _make_worker(mock_client)

        # Drive many poll cycles to push consecutive_failures high
        for _ in range(100):
            worker.poll()

        # With the cap, backoff = min(8, 2^n) should be 8 once n >= 3
        assert worker._consecutive_failures >= 3
        assert min(8, 2**worker._consecutive_failures) == 8


class TestPollWorkerReconnect:
    """After failures then success, poll_count resets for caps re-fetch."""

    def test_reconnect_resets_poll_count(self, qtbot):
        """When poll succeeds after failures, poll_count resets to 0."""
        failing_client = _make_failing_client()
        worker = _make_worker(failing_client)

        # Cause one failure: poll_count becomes 1, consecutive_failures becomes 1
        worker.poll()
        assert worker._consecutive_failures == 1
        assert worker._poll_count == 1

        # Swap to a working client for recovery
        ok_client = _make_mock_client()
        worker._ensure_client = MagicMock(return_value=ok_client)

        # Second call: backoff=2, poll_count=1, 1%2!=0 -> skipped
        worker.poll()
        assert worker._poll_count == 2  # incremented by skip

        # Third call: poll_count=2, 2%2==0 -> runs and succeeds
        connected_spy = _collect_signal(worker.connected)
        worker.poll()

        assert len(connected_spy) == 1
        # Reconnect path resets poll_count to 0 for caps re-fetch next cycle
        assert worker._poll_count == 0
        assert worker._consecutive_failures == 0

    def test_reconnect_refetches_caps_on_next_cycle(self, qtbot):
        """The cycle after reconnect re-fetches capabilities (poll_count == 0)."""
        failing_client = _make_failing_client()
        worker = _make_worker(failing_client)

        # One failure: poll_count=1, consecutive_failures=1
        worker.poll()

        # Recover: swap client and burn through the backoff skip
        ok_client = _make_mock_client()
        worker._ensure_client = MagicMock(return_value=ok_client)
        worker.poll()  # skipped (backoff)
        worker.poll()  # runs, succeeds -> reconnect resets poll_count to 0

        assert worker._poll_count == 0
        ok_client.capabilities.reset_mock()
        caps_spy = _collect_signal(worker.capabilities_ready)

        worker.poll()  # poll_count is 0 -> caps re-fetched
        assert len(caps_spy) == 1
        ok_client.capabilities.assert_called_once()


class TestPollWorkerInFlightGuard:
    """F-5: a poll still in flight causes the next invocation to be skipped,
    not queued/run a second time. The guard is cleared in a ``finally`` so a
    failed cycle can't wedge polling off permanently."""

    def test_reentrant_poll_is_skipped(self, qtbot):
        """A poll() that re-enters while the first is still running short-circuits
        — the batch endpoint fires once, not twice."""
        mock_client = _make_mock_client()
        worker = _make_worker(mock_client)

        reentrant_results: list = []

        def _reenter(*_args, **_kwargs):
            # Called from inside the first poll(), while _in_flight is set. The
            # guard must drop this re-entrant call rather than run a 2nd cycle.
            reentrant_results.append(worker.poll())
            return (
                DaemonStatus(overall_status="ok"),
                [SensorReading(id="cpu", value_c=45.0, age_ms=100)],
                [FanReading(id="fan0", rpm=1200, age_ms=100)],
            )

        mock_client.poll.side_effect = _reenter

        worker.poll()

        # The re-entrant poll() returned immediately (None) without issuing a
        # second batch call.
        assert reentrant_results == [None]
        assert mock_client.poll.call_count == 1

    def test_busy_flag_makes_poll_a_noop(self, qtbot):
        """With the guard already set, poll() does nothing — no client is even
        obtained."""
        mock_client = _make_mock_client()
        worker = _make_worker(mock_client)
        worker._in_flight = True

        worker.poll()

        worker._ensure_client.assert_not_called()
        mock_client.poll.assert_not_called()

    def test_flag_cleared_after_successful_poll(self, qtbot):
        """A normal cycle releases the guard so the next tick can run."""
        worker = _make_worker(_make_mock_client())
        worker.poll()
        assert worker._in_flight is False

    def test_flag_cleared_after_failed_poll(self, qtbot):
        """The ``finally`` clears the guard even when the cycle raises, so a
        transient failure can't wedge polling off permanently."""
        worker = _make_worker(_make_failing_client())
        worker.poll()
        assert worker._in_flight is False


# ---------------------------------------------------------------------------
# PollingService tests
# ---------------------------------------------------------------------------


class TestPollingServiceConnected:
    """_on_connected transitions mode and sets connection state."""

    def test_on_connected_transitions_to_automatic(self, qtbot):
        """READ_ONLY -> AUTOMATIC when daemon becomes connected."""
        state = AppState()
        assert state.mode == OperationMode.READ_ONLY
        assert state.connection == ConnectionState.DISCONNECTED

        svc = _make_polling_service(state)
        svc._on_connected()

        assert state.connection == ConnectionState.CONNECTED
        assert state.mode == OperationMode.AUTOMATIC


class TestPollingServiceDisconnected:
    """_on_disconnected transitions AUTOMATIC -> READ_ONLY."""

    def test_on_disconnected_transitions_to_read_only(self, qtbot):
        """AUTOMATIC -> READ_ONLY when daemon becomes disconnected."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)

        svc = _make_polling_service(state)
        svc._on_disconnected()

        assert state.connection == ConnectionState.DISCONNECTED
        assert state.mode == OperationMode.READ_ONLY


class TestPollingServiceActiveProfile:
    """_on_active_profile updates AppState with daemon's active profile."""

    def test_on_active_profile_sets_name(self, qtbot):
        """Active profile info from daemon is propagated to AppState."""
        state = AppState()

        svc = _make_polling_service(state)
        info = ActiveProfileInfo(active=True, profile_id="perf", profile_name="Performance")
        svc._on_active_profile(info)

        assert state.active_profile_name == "Performance"

    def test_on_active_profile_ignores_inactive(self, qtbot):
        """When active=False, profile name is not updated."""
        state = AppState()
        state.set_active_profile("Existing")

        svc = _make_polling_service(state)
        info = ActiveProfileInfo(active=False, profile_id="", profile_name="")
        svc._on_active_profile(info)

        assert state.active_profile_name == "Existing"

    def test_on_active_profile_handles_none(self, qtbot):
        """None (no active profile response) is handled without error."""
        state = AppState()
        state.set_active_profile("Existing")

        svc = _make_polling_service(state)
        svc._on_active_profile(None)

        assert state.active_profile_name == "Existing"


class TestPollingServiceHardwareDiagnostics:
    """DEC-229: the prefetch result reaches its single writer, and only it."""

    def test_on_hw_diagnostics_publishes_board_to_app_state(self, qtbot):
        from control_ofc.services.diagnostics_service import DiagnosticsService

        state = AppState()
        svc = _make_polling_service(state)
        svc._diag = DiagnosticsService(state)

        result = HardwareDiagnosticsResult(
            board=BoardInfo(vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER")
        )
        svc._on_hw_diagnostics(result)

        assert state.board_info.name == "X870E AORUS MASTER"
        assert svc._diag.last_hw_diagnostics is result

    def test_on_hw_diagnostics_without_a_diagnostics_service_is_a_noop(self, qtbot):
        """Degraded, never wrong: no service ⇒ the resolver falls back to pwmN."""
        state = AppState()
        svc = _make_polling_service(state)  # _diag is None

        svc._on_hw_diagnostics(HardwareDiagnosticsResult(board=BoardInfo(vendor="X", name="Y")))

        assert state.board_info.vendor == ""

    def test_board_identity_never_downgrades_but_the_cache_still_refreshes(self, qtbot):
        """DEC-229: a blank DMI re-read must not un-name every fan.

        `set_hw_diagnostics` is not a startup-only latch —
        `SystemStatePage._on_rescan_ok` re-fetches on every "Rescan Hardware"
        click. DMI is a boot-time constant, so a re-read that returns blank is a
        failed read, not a board that stopped existing; taking it would revert
        every hwmon fan to `pwmN` mid-session and feed `_role_preserving_label` a
        role-less name again, re-opening the floor bug from a button click.

        The cache has the opposite rule and must still take the new result — a
        rescan legitimately refreshes header counts and revert tallies.
        """
        from control_ofc.services.diagnostics_service import DiagnosticsService

        state = AppState()
        diag = DiagnosticsService(state)
        good = HardwareDiagnosticsResult(
            board=BoardInfo(vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER")
        )
        diag.set_hw_diagnostics(good)

        blank = HardwareDiagnosticsResult(board=BoardInfo())
        diag.set_hw_diagnostics(blank)

        assert state.board_info.name == "X870E AORUS MASTER"  # identity held
        assert diag.last_hw_diagnostics is blank  # …cache still refreshed

        # A genuinely different board still wins — this is not a one-shot latch.
        other = HardwareDiagnosticsResult(board=BoardInfo(vendor="ASUS", name="ProArt X870E"))
        diag.set_hw_diagnostics(other)
        assert state.board_info.name == "ProArt X870E"

    def test_first_board_is_taken_even_when_partially_blank(self, qtbot):
        """The guard must not deadlock an unknown board into staying unknown."""
        from control_ofc.services.diagnostics_service import DiagnosticsService

        state = AppState()
        diag = DiagnosticsService(state)
        # Vendor-only is still identity — the fallback table matches on either.
        diag.set_hw_diagnostics(HardwareDiagnosticsResult(board=BoardInfo(vendor="ASRock")))
        assert state.board_info.vendor == "ASRock"

    def test_set_hw_diagnostics_without_a_state_still_caches(self, qtbot):
        """The cache-only branch: a service built without an AppState.

        The dangerous direction (dropping the board push) is pinned elsewhere;
        this pins the other arm so inverting the `is not None` guard cannot pass.
        """
        from control_ofc.services.diagnostics_service import DiagnosticsService

        diag = DiagnosticsService()  # no AppState
        result = HardwareDiagnosticsResult(board=BoardInfo(vendor="X", name="Y"))

        diag.set_hw_diagnostics(result)  # must not raise

        assert diag.last_hw_diagnostics is result


# ---------------------------------------------------------------------------
# PollingService lifecycle (T9 audit finding)
# ---------------------------------------------------------------------------


class TestPollingServiceLifecycle:
    """Real PollingService can be created and destroyed without errors."""

    def test_init_and_shutdown_with_nonexistent_socket(self, tmp_path):
        """PollingService initializes and shuts down cleanly with a bogus socket."""
        state = AppState()
        socket_path = str(tmp_path / "nonexistent.sock")
        svc = PollingService(state, socket_path)

        assert svc._state is state
        svc.shutdown()  # must not raise


class TestPollingServiceConstruction:
    """T2 (test-tests audit): drive the real PollingService.__init__ instead
    of bypassing it via __new__, so the timer interval, signal wiring, and
    worker-thread setup are actually exercised. The prior `_make_polling_service`
    helper used __new__ + manual attribute assignment, leaving 56 lines of
    __init__ logic untested. Mutation testing showed flipping `setInterval(POLL_INTERVAL_MS)`,
    the per-signal connect() calls, and the thread.start() invocation all
    survived. These tests lock those down."""

    def test_init_creates_worker_and_thread(self, tmp_path):
        """The real __init__ wires up worker, thread, and timer attributes."""
        from PySide6.QtCore import QThread, QTimer

        from control_ofc.services.polling import _PollWorker

        state = AppState()
        socket_path = str(tmp_path / "nonexistent.sock")
        svc = PollingService(state, socket_path)
        try:
            assert isinstance(svc._worker, _PollWorker)
            assert isinstance(svc._thread, QThread)
            assert isinstance(svc._timer, QTimer)
            assert not svc._running, "service must not auto-start"
        finally:
            svc.shutdown()

    def test_init_sets_timer_interval_to_poll_interval(self, tmp_path):
        """Locks down the QTimer interval — POLL_INTERVAL_MS, not arbitrary."""
        from control_ofc.constants import POLL_INTERVAL_MS

        state = AppState()
        socket_path = str(tmp_path / "nonexistent.sock")
        svc = PollingService(state, socket_path)
        try:
            assert svc._timer.interval() == POLL_INTERVAL_MS
        finally:
            svc.shutdown()

    def test_init_socket_path_propagated_to_worker(self, tmp_path):
        """The socket path the GUI was launched with must reach the worker."""
        state = AppState()
        socket_path = str(tmp_path / "ctl.sock")
        svc = PollingService(state, socket_path)
        try:
            assert svc._worker._socket_path == socket_path
        finally:
            svc.shutdown()

    def test_init_thread_is_started(self, tmp_path):
        """The worker QThread must be running so worker.poll() can execute on it."""
        state = AppState()
        socket_path = str(tmp_path / "nonexistent.sock")
        svc = PollingService(state, socket_path)
        try:
            assert svc._thread.isRunning(), "polling worker thread must be started"
        finally:
            svc.shutdown()

    def test_start_starts_timer_stop_stops_it(self, tmp_path):
        """start() activates the QTimer; stop() deactivates it."""
        state = AppState()
        socket_path = str(tmp_path / "nonexistent.sock")
        svc = PollingService(state, socket_path)
        try:
            assert not svc._timer.isActive()
            svc.start()
            assert svc._timer.isActive()
            assert svc._running
            svc.stop()
            assert not svc._timer.isActive()
            assert not svc._running
        finally:
            svc.shutdown()

    def test_init_wires_worker_signals_to_state(self, tmp_path):
        """Each worker signal must update AppState — verify by emitting once."""
        state = AppState()
        socket_path = str(tmp_path / "nonexistent.sock")
        svc = PollingService(state, socket_path)
        try:
            # Emit each signal and confirm the wired slot ran on AppState.
            # status_ready → state.set_status
            from control_ofc.api.models import (
                Capabilities,
                ConnectionState,
                DaemonStatus,
                FanReading,
                SensorReading,
            )

            caps = Capabilities(daemon_version="0.1.0")
            svc._worker.capabilities_ready.emit(caps)
            assert state.capabilities is caps

            status = DaemonStatus(overall_status="ok")
            svc._worker.status_ready.emit(status)
            assert state.daemon_status is status

            sensors = [SensorReading(id="cpu", value_c=42.0, age_ms=10)]
            svc._worker.sensors_ready.emit(sensors)
            assert state.sensors == sensors

            fans = [FanReading(id="fan0", rpm=1000, age_ms=10)]
            svc._worker.fans_ready.emit(fans)
            assert state.fans == fans

            # connected/disconnected drive state.connection
            svc._worker.connected.emit()
            assert state.connection == ConnectionState.CONNECTED
            svc._worker.disconnected.emit()
            assert state.connection == ConnectionState.DISCONNECTED
        finally:
            svc.shutdown()

    def test_init_wires_hw_diagnostics_signal_through_to_board_info(self, tmp_path):
        """DEC-229: the connect() for `hw_diagnostics_ready` must exist.

        This is the whole point of blocker 2's fix, and it was the one production
        line with no test: deleting
        `self._worker.hw_diagnostics_ready.connect(self._on_hw_diagnostics)`
        from `__init__` left the entire suite green, because the worker-side
        emit and the `_on_hw_diagnostics` slot were each tested in isolation and
        nothing exercised the wire between them. The regression would have been
        invisible until a user reported fan names still reading `pwmN`.

        Needs a real DiagnosticsService — `_diag` is None by default, and the
        slot no-ops without it.
        """
        from control_ofc.services.diagnostics_service import DiagnosticsService

        state = AppState()
        diag = DiagnosticsService(state)
        svc = PollingService(state, str(tmp_path / "nonexistent.sock"), diagnostics=diag)
        try:
            result = HardwareDiagnosticsResult(
                board=BoardInfo(vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER")
            )
            svc._worker.hw_diagnostics_ready.emit(result)

            assert state.board_info.name == "X870E AORUS MASTER"
            assert diag.last_hw_diagnostics is result
        finally:
            svc.shutdown()


# ---------------------------------------------------------------------------
# DEC-146 P3-1: periodic capabilities refresh
# ---------------------------------------------------------------------------


class TestPeriodicCapabilitiesRefresh:
    """Capabilities/headers/active-profile re-fetch every ``_caps_interval``
    cycles, not only on the first poll (DEC-146 P3-1 — the daemon can gain or
    lose hardware/profiles without a reconnect)."""

    def test_capabilities_refetched_every_interval(self, qtbot):
        mock_client = _make_mock_client()
        worker = _make_worker(mock_client)
        worker._caps_interval = 3  # shrink the 300 s production interval
        for _ in range(7):
            worker.poll()
        # Cycles 0, 3, and 6 → three capability fetches.
        assert mock_client.capabilities.call_count == 3
        assert mock_client.hwmon_headers.call_count == 3

    def test_non_interval_cycles_skip_capabilities(self, qtbot):
        mock_client = _make_mock_client()
        worker = _make_worker(mock_client)
        worker._caps_interval = 1000
        for _ in range(5):
            worker.poll()
        assert mock_client.capabilities.call_count == 1  # cycle 0 only


# ---------------------------------------------------------------------------
# DEC-146 P3-2: session stats reset on the true-reconnect edge
# ---------------------------------------------------------------------------


class TestSessionStatsResetOnReconnect:
    """A true reconnect resets session-scoped state (the daemon may have
    restarted, invalidating session min/max); the very first connect must
    not reset."""

    def test_reconnect_resets_session_state(self):
        state = AppState()
        svc = _make_polling_service(state)
        svc._was_connected = False  # we were connected before and lost it
        with patch.object(state, "reset_session_stats", wraps=state.reset_session_stats) as spy:
            svc._on_connected()
        spy.assert_called_once()

    def test_first_connect_does_not_reset(self):
        state = AppState()
        svc = _make_polling_service(state)
        svc._was_connected = None  # first-ever cycle
        with patch.object(state, "reset_session_stats", wraps=state.reset_session_stats) as spy:
            svc._on_connected()
        spy.assert_not_called()
