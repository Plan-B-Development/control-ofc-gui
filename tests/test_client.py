"""Tests for the daemon IPC client and error handling."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from control_ofc.api.errors import DaemonError, DaemonUnavailable


def test_daemon_error_fields():
    err = DaemonError(code="validation_error", message="bad input", status=400)
    assert err.code == "validation_error"
    assert err.status == 400
    assert str(err) == "bad input"


def test_daemon_error_retryable():
    err = DaemonError(code="hardware_unavailable", message="timeout", retryable=True)
    assert err.retryable is True


def test_daemon_unavailable_is_daemon_error():
    err = DaemonUnavailable(message="socket gone")
    assert isinstance(err, DaemonError)
    assert err.code == "daemon_unavailable"
    assert err.retryable is True


def test_default_socket_path():
    from control_ofc.constants import DEFAULT_SOCKET_PATH

    assert DEFAULT_SOCKET_PATH == "/run/control-ofc/control-ofc.sock"


class TestActivateProfilePayload:
    """M8: activate_profile accepts profile_path or profile_id, not both."""

    def _make_client(self) -> tuple:
        from control_ofc.api.client import DaemonClient

        client = DaemonClient.__new__(DaemonClient)
        client._post = MagicMock(
            return_value={
                "activated": True,
                "profile_id": "quiet",
                "profile_name": "Quiet",
            }
        )
        return client, client._post

    def test_profile_path_positional(self):
        client, post = self._make_client()
        client.activate_profile("/tmp/profiles/quiet.json")
        post.assert_called_once_with(
            "/profile/activate", json={"profile_path": "/tmp/profiles/quiet.json"}
        )

    def test_profile_path_keyword(self):
        client, post = self._make_client()
        client.activate_profile(profile_path="/tmp/profiles/quiet.json")
        post.assert_called_once_with(
            "/profile/activate", json={"profile_path": "/tmp/profiles/quiet.json"}
        )

    def test_profile_id_keyword(self):
        client, post = self._make_client()
        client.activate_profile(profile_id="quiet")
        post.assert_called_once_with("/profile/activate", json={"profile_id": "quiet"})

    def test_both_rejected(self):
        client, _ = self._make_client()
        with pytest.raises(ValueError):
            client.activate_profile(profile_path="/tmp/p.json", profile_id="quiet")

    def test_neither_rejected(self):
        client, _ = self._make_client()
        with pytest.raises(ValueError):
            client.activate_profile()


class TestSensorHistoryEncoding:
    """Finding F: /sensors/history must percent-encode the sysfs-derived
    entity_id (passed via httpx ``params=``) rather than interpolate it into the
    path, so a label containing query-special chars (&, #, +, space) cannot
    corrupt the request."""

    def _client_with_capture(self, capture: dict):
        import httpx

        from control_ofc.api.client import DaemonClient

        def handler(request: httpx.Request) -> httpx.Response:
            capture["params"] = dict(request.url.params)
            capture["raw_query"] = request.url.query.decode()
            return httpx.Response(200, json={"entity_id": "x", "points": []})

        client = DaemonClient.__new__(DaemonClient)
        client._client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://localhost"
        )
        return client

    def test_special_chars_round_trip(self):
        capture: dict = {}
        client = self._client_with_capture(capture)
        entity_id = "hwmon:weird&chip:0000:2d:00.0:edge temp#1"

        client.sensor_history(entity_id, last=42)

        # A correctly-encoded id survives the round-trip intact. The old
        # f-string would have split at '&' and truncated the id.
        assert capture["params"]["id"] == entity_id
        assert capture["params"]["last"] == "42"
        # The raw '&' from the label must be percent-encoded, not left literal.
        assert "weird&chip" not in capture["raw_query"]

    def test_uses_params_not_query_string(self):
        from control_ofc.api.client import DaemonClient

        client = DaemonClient.__new__(DaemonClient)
        client._get = MagicMock(return_value={"entity_id": "cpu", "points": []})

        client.sensor_history("hwmon:k10temp:Tctl", last=100)

        client._get.assert_called_once_with(
            "/sensors/history", params={"id": "hwmon:k10temp:Tctl", "last": 100}
        )
