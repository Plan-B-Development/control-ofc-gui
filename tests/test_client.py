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


class TestInventoryAndPreferredSensors:
    """Phase 4 (DEC-200): inventory reads + preferred-sensor POSTs."""

    def _make_client(self, *, get_return=None, post_return=None):
        from control_ofc.api.client import DaemonClient

        client = DaemonClient.__new__(DaemonClient)
        client._get = MagicMock(return_value=get_return or {})
        client._post = MagicMock(return_value=post_return or {})
        return client

    def test_inventory_hwmon_calls_get(self):
        client = self._make_client(
            get_return={
                "api_version": 1,
                "temp_sensors": [
                    {
                        "id": "hwmon:k10temp:x:Tctl",
                        "classification": "cpu_tctl",
                        "confidence": "high",
                    }
                ],
                "default_cpu": {"sensor_id": "hwmon:k10temp:x:Tctl", "source": "auto"},
            }
        )
        inv = client.inventory_hwmon()
        client._get.assert_called_once_with("/inventory/hwmon")
        assert inv.temp_sensors[0].classification == "cpu_tctl"
        assert inv.default_cpu.source == "auto"

    # Wire-protocol coverage for the two live preferred-sensor POSTs. Restored
    # during the v2.26.0 release review: the DEC-224 dead-code sweep removed
    # these alongside the legitimately-dead `test_inventory_readiness_calls_get`
    # (they shared this class), leaving `set_preferred_{cpu,mb}_sensor`'s
    # endpoint/payload contract tested only through UI stubs. These methods are
    # live (settings_page + overview_page); a typo in the path or `sensor_id`
    # key would otherwise be silent until a real daemon connection.
    def test_set_preferred_cpu_sensor_posts_id(self):
        client = self._make_client(
            post_return={"updated": True, "role": "cpu", "preferred_sensor": "hwmon:x:Tctl"}
        )
        res = client.set_preferred_cpu_sensor("hwmon:x:Tctl")
        client._post.assert_called_once_with(
            "/config/preferred-cpu-sensor", json={"sensor_id": "hwmon:x:Tctl"}
        )
        assert res.updated is True
        assert res.preferred_sensor == "hwmon:x:Tctl"

    def test_set_preferred_cpu_sensor_clears_with_none(self):
        client = self._make_client(
            post_return={"updated": True, "role": "cpu", "preferred_sensor": None}
        )
        res = client.set_preferred_cpu_sensor(None)
        client._post.assert_called_once_with(
            "/config/preferred-cpu-sensor", json={"sensor_id": None}
        )
        assert res.preferred_sensor is None

    def test_set_preferred_mb_sensor_posts_id(self):
        client = self._make_client(
            post_return={"updated": True, "role": "mb", "preferred_sensor": "hwmon:x:SYSTIN"}
        )
        client.set_preferred_mb_sensor("hwmon:x:SYSTIN")
        client._post.assert_called_once_with(
            "/config/preferred-mb-sensor", json={"sensor_id": "hwmon:x:SYSTIN"}
        )
