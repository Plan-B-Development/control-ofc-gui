"""P6a: daemon-control client + model additions (DEC-160 / DEC-163 / DEC-166).

Covers the new ``autonomous_control`` capability gate field, the override and
fan-identify client methods, the profile CRUD/validate methods, and
``parse_field_violations``. The client methods are exercised end-to-end through
an ``httpx.MockTransport`` so the HTTP method, path, query, and body are pinned.
"""

from __future__ import annotations

import json

import httpx

from control_ofc.api.client import DaemonClient
from control_ofc.api.models import parse_capabilities, parse_field_violations


def _client(handler) -> DaemonClient:
    client = DaemonClient.__new__(DaemonClient)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://localhost"
    )
    return client


class TestAutonomousControlCapability:
    def test_parsed_when_present(self):
        caps = parse_capabilities(
            {"control": {"autonomous_control": True, "min_supported_gui": "2.0.0"}}
        )
        assert caps.control.autonomous_control is True
        assert caps.control.min_supported_gui == "2.0.0"

    def test_defaults_false_when_flag_absent(self):
        # A pre-2.0 daemon omits the flag → the safety gate defaults it False.
        caps = parse_capabilities({"control": {"profile_storage": True}})
        assert caps.control.autonomous_control is False

    def test_defaults_false_when_control_block_absent(self):
        assert parse_capabilities({}).control.autonomous_control is False


class TestOverrideClient:
    def test_take_posts_and_parses_grant(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["json"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "control_id": "cpu",
                    "override_token": 7,
                    "pwm_percent": 60,
                    "ttl_secs": 15,
                    "renew_secs": 5,
                    "expires_in_secs": 15,
                },
            )

        grant = _client(handler).override_take("cpu", 60, ttl_secs=15)
        assert seen["method"] == "POST"
        assert seen["path"] == "/control/cpu/override"
        assert seen["json"] == {"pwm_percent": 60, "ttl_secs": 15}
        assert grant.override_token == 7
        assert grant.renew_secs == 5
        assert grant.pwm_percent == 60

    def test_take_omits_ttl_when_none(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json={"control_id": "cpu", "override_token": 1})

        _client(handler).override_take("cpu", 50)
        assert seen["json"] == {"pwm_percent": 50}

    def test_renew_posts_token(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["json"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "control_id": "cpu",
                    "override_token": 2,
                    "ttl_secs": 15,
                    "expires_in_secs": 15,
                },
            )

        res = _client(handler).override_renew("cpu", 2)
        assert seen["path"] == "/control/cpu/override/renew"
        assert seen["json"] == {"override_token": 2}
        assert res.override_token == 2

    def test_release_deletes_with_body(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json={"control_id": "cpu", "released": True})

        res = _client(handler).override_release("cpu", 9)
        assert seen["method"] == "DELETE"
        assert seen["path"] == "/control/cpu/override"
        assert seen["json"] == {"override_token": 9}
        assert res.released is True


class TestFanIdentifyClient:
    def test_stop_posts_action(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["json"] = json.loads(request.content)
            return httpx.Response(
                200, json={"fan_id": "openfan:ch00", "action": "stop", "expires_in_secs": 15}
            )

        res = _client(handler).fan_identify("openfan:ch00", "stop")
        assert seen["method"] == "POST"
        assert seen["path"].endswith("/identify")
        assert "openfan" in seen["path"]
        assert seen["json"] == {"action": "stop"}
        assert res.action == "stop"
        assert res.expires_in_secs == 15


class TestProfileCrudClient:
    def test_validate_sets_query_param(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"valid": True})

        _client(handler).validate_profile({"id": "p1"})
        assert seen["method"] == "POST"
        assert seen["path"] == "/profiles"
        assert seen["params"] == {"validate_only": "true"}

    def test_delete_uses_delete_method(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(200, json={"deleted": True})

        _client(handler).delete_profile("p1")
        assert seen["method"] == "DELETE"
        assert seen["path"] == "/profiles/p1"

    def test_update_uses_put_method(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(200, json={"updated": True})

        _client(handler).update_profile("p1", {"id": "p1"})
        assert seen["method"] == "PUT"
        assert seen["path"] == "/profiles/p1"

    def test_list_returns_profiles_array(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/profiles"
            return httpx.Response(200, json={"profiles": [{"id": "a"}, {"id": "b"}]})

        profiles = _client(handler).list_profiles()
        assert [p["id"] for p in profiles] == ["a", "b"]

    def test_get_returns_document(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/profiles/p1"
            return httpx.Response(200, json={"id": "p1", "name": "Quiet"})

        assert _client(handler).get_profile("p1")["name"] == "Quiet"


class TestFieldViolations:
    def test_parses_from_details(self):
        details = {
            "field_violations": [
                {
                    "field": "minimum_pct",
                    "reason": "FLOOR_TOO_LOW",
                    "description": "pump < 30%",
                    "severity": "error",
                }
            ]
        }
        violations = parse_field_violations(details)
        assert len(violations) == 1
        assert violations[0].field == "minimum_pct"
        assert violations[0].reason == "FLOOR_TOO_LOW"
        assert violations[0].severity == "error"

    def test_parses_warning_severity(self):
        # Regression: the daemon sends a `severity` tier (DEC-160) and it must
        # survive parsing — it used to be silently dropped by _filter_fields.
        details = {
            "field_violations": [
                {
                    "field": "controls[0].sensor_id",
                    "reason": "UNKNOWN_SENSOR",
                    "description": "sensor not present on this machine",
                    "severity": "warning",
                }
            ]
        }
        violations = parse_field_violations(details)
        assert violations[0].severity == "warning"

    def test_severity_defaults_to_error_when_omitted(self):
        # Older daemons omit `severity`; a 400-rejection violation defaults to error.
        details = {"field_violations": [{"field": "x", "reason": "OUT_OF_RANGE"}]}
        violations = parse_field_violations(details)
        assert violations[0].severity == "error"

    def test_empty_for_non_violation_shapes(self):
        assert parse_field_violations(None) == []
        assert parse_field_violations("oops") == []
        assert parse_field_violations({"other": 1}) == []
