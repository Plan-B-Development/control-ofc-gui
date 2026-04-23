"""Regression test: GUI auto-registers its profile directory with the daemon.

The daemon's default profile search dirs are /etc/control-ofc/profiles and
the root user's XDG dir. When the GUI runs as an unprivileged user, its
profiles live under that user's XDG config dir, which the daemon has no way
to discover on its own. Before this fix, profile activation failed with
"profile_path must be within a profile search directory" on every fresh
install. The GUI now calls POST /config/profile-search-dirs during startup
to teach the daemon where to look.

The registration runs inside the PollingService worker thread (not the Qt
main thread) so a slow or half-dead daemon cannot stall the UI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.services.polling import _PollWorker


@pytest.fixture()
def tmp_profiles_dir(tmp_path, monkeypatch):
    """Redirect profiles_dir() to a temp location for the test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Clear any lingering path overrides from previous tests
    from control_ofc.paths import set_path_overrides

    set_path_overrides()
    return tmp_path / "control-ofc" / "profiles"


class TestRegisterProfileSearchDir:
    def test_calls_daemon_with_profiles_dir(self, tmp_profiles_dir):
        """On success, the current profiles_dir() is sent to the daemon."""
        worker = _PollWorker(socket_path="/tmp/nonexistent.sock")
        client = MagicMock()

        worker._register_profile_search_dir(client)

        client.update_profile_search_dirs.assert_called_once()
        kwargs = client.update_profile_search_dirs.call_args.kwargs
        assert kwargs["add"] == [str(tmp_profiles_dir)]

    def test_swallows_daemon_error(self, tmp_profiles_dir, caplog):
        """A DaemonError response must not propagate — startup must continue."""
        worker = _PollWorker(socket_path="/tmp/nonexistent.sock")
        client = MagicMock()
        client.update_profile_search_dirs.side_effect = DaemonError(
            code="validation_error",
            message="nope",
            retryable=False,
            source="validation",
            status=400,
        )

        worker._register_profile_search_dir(client)

        assert "Could not register profile search dir" in caplog.text

    def test_swallows_daemon_unavailable(self, tmp_profiles_dir, caplog):
        """Offline daemon at startup is tolerated — polling already handles that."""
        worker = _PollWorker(socket_path="/tmp/nonexistent.sock")
        client = MagicMock()
        client.update_profile_search_dirs.side_effect = DaemonUnavailable()

        worker._register_profile_search_dir(client)

        assert "Could not register profile search dir" in caplog.text

    def test_swallows_connection_error(self, tmp_profiles_dir, caplog):
        """Transport-level errors must also be swallowed without propagating."""
        worker = _PollWorker(socket_path="/tmp/nonexistent.sock")
        client = MagicMock()
        client.update_profile_search_dirs.side_effect = ConnectionError("closed")

        worker._register_profile_search_dir(client)

        assert "Connection error registering profile search dir" in caplog.text

    def test_respects_path_override(self, tmp_path):
        """If the user configured a custom profiles dir, that path is registered."""
        from control_ofc.paths import set_path_overrides

        custom = tmp_path / "custom-profiles"
        custom.mkdir()
        set_path_overrides(profiles_dir=str(custom))
        try:
            worker = _PollWorker(socket_path="/tmp/nonexistent.sock")
            client = MagicMock()
            worker._register_profile_search_dir(client)
            kwargs = client.update_profile_search_dirs.call_args.kwargs
            assert kwargs["add"] == [str(custom)]
        finally:
            set_path_overrides()


class TestRegistrationRunsInWorkerPollCycle:
    """P1-3: Registration runs inside the worker's poll() cycle, not on the Qt
    main thread. Firing on ``_poll_count == 0`` covers both first-poll and
    reconnect (worker resets _poll_count to 0 when recovering from failure).
    """

    def test_registration_fires_on_first_successful_poll(self, tmp_profiles_dir, monkeypatch):
        """Worker.poll() calls update_profile_search_dirs when _poll_count == 0."""
        from control_ofc.api.models import ActiveProfileInfo, Capabilities, LeaseState

        worker = _PollWorker(socket_path="/tmp/nonexistent.sock")

        client = MagicMock()
        client.capabilities.return_value = Capabilities(daemon_version="1.5.0")
        client.hwmon_headers.return_value = []
        client.active_profile.return_value = ActiveProfileInfo(active=False)
        client.poll.return_value = (MagicMock(), [], [])
        client.hwmon_lease_status.return_value = LeaseState(held=False)

        monkeypatch.setattr(worker, "_ensure_client", lambda: client)

        worker.poll()

        client.update_profile_search_dirs.assert_called_once()
        kwargs = client.update_profile_search_dirs.call_args.kwargs
        assert kwargs["add"] == [str(tmp_profiles_dir)]

    def test_registration_repeats_after_reconnect(self, tmp_profiles_dir, monkeypatch):
        """On reconnect the worker sets _poll_count=0, triggering a fresh register."""
        from control_ofc.api.errors import DaemonUnavailable
        from control_ofc.api.models import ActiveProfileInfo, Capabilities, LeaseState

        worker = _PollWorker(socket_path="/tmp/nonexistent.sock")

        client = MagicMock()
        client.capabilities.return_value = Capabilities(daemon_version="1.5.0")
        client.hwmon_headers.return_value = []
        client.active_profile.return_value = ActiveProfileInfo(active=False)
        client.poll.return_value = (MagicMock(), [], [])
        client.hwmon_lease_status.return_value = LeaseState(held=False)

        monkeypatch.setattr(worker, "_ensure_client", lambda: client)

        # First successful poll → registers.
        worker.poll()
        assert client.update_profile_search_dirs.call_count == 1

        # Second poll — still connected — must NOT re-register.
        worker.poll()
        assert client.update_profile_search_dirs.call_count == 1

        # Simulate a full disconnect: both the batch and the fallback
        # individual endpoints raise, so the worker's outer DaemonError
        # handler fires (mirroring a daemon that stopped listening).
        client.poll.side_effect = DaemonUnavailable()
        client.status.side_effect = DaemonUnavailable()
        client.sensors.side_effect = DaemonUnavailable()
        client.fans.side_effect = DaemonUnavailable()
        worker.poll()
        assert client.update_profile_search_dirs.call_count == 1

        # Clear the error to simulate the daemon coming back.
        client.poll.side_effect = None
        client.status.side_effect = None
        client.sensors.side_effect = None
        client.fans.side_effect = None
        client.poll.return_value = (MagicMock(), [], [])

        # Step through enough cycles to cover the exponential backoff skip,
        # the recovery poll (which resets _poll_count to 0), and the next
        # cycle where register fires again.
        for _ in range(5):
            worker.poll()

        assert client.update_profile_search_dirs.call_count == 2
