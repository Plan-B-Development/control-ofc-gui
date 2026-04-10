"""Regression test: GUI auto-registers its profile directory with the daemon.

The daemon's default profile search dirs are /etc/control-ofc/profiles and
the root user's XDG dir. When the GUI runs as an unprivileged user, its
profiles live under that user's XDG config dir, which the daemon has no way
to discover on its own. Before this fix, profile activation failed with
"profile_path must be within a profile search directory" on every fresh
install. The GUI now calls POST /config/profile-search-dirs during startup
to teach the daemon where to look.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.main import register_profile_search_dir


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
        client = MagicMock()

        register_profile_search_dir(client)

        client.update_profile_search_dirs.assert_called_once()
        kwargs = client.update_profile_search_dirs.call_args.kwargs
        assert kwargs["add"] == [str(tmp_profiles_dir)]

    def test_swallows_daemon_error(self, tmp_profiles_dir, caplog):
        """A DaemonError response must not propagate — startup must continue."""
        client = MagicMock()
        client.update_profile_search_dirs.side_effect = DaemonError(
            code="validation_error",
            message="nope",
            retryable=False,
            source="validation",
            status=400,
        )

        # Must not raise.
        register_profile_search_dir(client)

        assert "Could not register profile search dir" in caplog.text

    def test_swallows_daemon_unavailable(self, tmp_profiles_dir, caplog):
        """Offline daemon at startup is tolerated — polling already handles that."""
        client = MagicMock()
        client.update_profile_search_dirs.side_effect = DaemonUnavailable()

        register_profile_search_dir(client)

        # DaemonUnavailable is a subclass of DaemonError, so it hits the same branch.
        assert "Could not register profile search dir" in caplog.text

    def test_swallows_unexpected_exception(self, tmp_profiles_dir, caplog):
        """Any non-DaemonError exception must also be swallowed with a warning."""
        client = MagicMock()
        client.update_profile_search_dirs.side_effect = RuntimeError("boom")

        register_profile_search_dir(client)

        assert "Unexpected error registering profile search dir" in caplog.text

    def test_respects_path_override(self, tmp_path, monkeypatch):
        """If the user configured a custom profiles dir, that path is registered."""
        from control_ofc.paths import set_path_overrides

        custom = tmp_path / "custom-profiles"
        custom.mkdir()
        set_path_overrides(profiles_dir=str(custom))
        try:
            client = MagicMock()
            register_profile_search_dir(client)
            kwargs = client.update_profile_search_dirs.call_args.kwargs
            assert kwargs["add"] == [str(custom)]
        finally:
            set_path_overrides()
