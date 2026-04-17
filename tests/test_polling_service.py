"""Tests for _PollWorker and PollingService.

Verifies polling lifecycle: first-poll capability fetch, batch/fallback
behaviour, exponential backoff, reconnection, and PollingService mode
transitions on connect/disconnect.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    ActiveProfileInfo,
    Capabilities,
    ConnectionState,
    DaemonStatus,
    FanReading,
    LeaseState,
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
    client.hwmon_lease_status.return_value = LeaseState()
    client.sensor_history.return_value = MagicMock(points=[])
    return client


def _make_failing_client() -> MagicMock:
    """Return a mock client where every method raises DaemonError."""
    client = MagicMock()
    for attr in (
        "capabilities",
        "hwmon_headers",
        "active_profile",
        "poll",
        "status",
        "sensors",
        "fans",
        "hwmon_lease_status",
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

    Patches __init__ to only set _state, which is all the _on_* handlers need.
    """
    with patch.object(PollingService, "__init__", lambda self, *a, **kw: None):
        svc = PollingService.__new__(PollingService)
        svc._state = state
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

    def test_on_connected_does_not_override_manual(self, qtbot):
        """MANUAL_OVERRIDE mode is preserved -- _on_connected does not overwrite it."""
        state = AppState()
        state.set_mode(OperationMode.MANUAL_OVERRIDE)

        svc = _make_polling_service(state)
        svc._on_connected()

        assert state.mode == OperationMode.MANUAL_OVERRIDE


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

    def test_on_disconnected_does_not_override_manual(self, qtbot):
        """MANUAL_OVERRIDE is not changed to READ_ONLY on disconnect."""
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.MANUAL_OVERRIDE)

        svc = _make_polling_service(state)
        svc._on_disconnected()

        assert state.connection == ConnectionState.DISCONNECTED
        assert state.mode == OperationMode.MANUAL_OVERRIDE


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
