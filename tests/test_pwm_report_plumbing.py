"""PWM Test Report plumbing (DEC-404 Stage 4): the client's response observer
and stall-probe calls, the worker's verbatim capture, the new model fields,
the capability registry entries, the report folder and the support bundle.

The client tests run a REAL ``DaemonClient`` over an in-memory transport, so
the observer is exercised through the request path production uses rather
than a fake that would share any mistake.
"""

from __future__ import annotations

import json

import httpx
import pytest

from control_ofc.api.client import DaemonClient
from control_ofc.api.models import (
    parse_capabilities,
    parse_characterization_run,
    parse_control_path_run,
    parse_fans,
    parse_hardware_diagnostics,
    parse_stall_probe_run,
)
from control_ofc.services import daemon_features as df
from tests.pwm_report_fixtures import CPU, fan, probe_run, verify_body


def _client(handler, observer=None) -> DaemonClient:
    client = DaemonClient(socket_path="/tmp/unused.sock", response_observer=observer)
    client._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    return client


# ── The response observer ───────────────────────────────────────────────────


def test_the_observer_sees_every_body_verbatim_errors_included():
    seen: list[tuple] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/verify"):
            return httpx.Response(200, json={**verify_body(), "a_future_field": 1})
        return httpx.Response(409, json={"error": {"code": "conflict", "message": "busy"}})

    client = _client(handler, lambda *a: seen.append(a))
    client.verify_hwmon_pwm(CPU)
    method, path, status, body = seen[-1]
    assert (method, path, status) == ("POST", f"/hwmon/{CPU}/verify", 200)
    assert body["a_future_field"] == 1, "the parsed model drops it; the observer must not"
    with pytest.raises(Exception, match="busy"):
        client.start_characterization(CPU)
    assert seen[-1][2] == 409 and seen[-1][3]["error"]["code"] == "conflict"


def test_a_failing_observer_never_fails_the_request():
    def boom(*_a):
        raise RuntimeError("observer bug")

    client = _client(lambda r: httpx.Response(200, json=verify_body()), boom)
    assert client.verify_hwmon_pwm(CPU).result == "effective"


# ── The stall-probe calls ───────────────────────────────────────────────────


def test_the_probe_calls_and_their_acknowledgement():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "none"}})
        return httpx.Response(202, json=probe_run(None, state="running"))

    client = _client(handler)
    run = client.start_stall_probe(CPU, acknowledge_below_floor=True)
    assert run.is_running
    assert json.loads(requests[-1].content) == {"acknowledge_below_floor": True}
    assert requests[-1].url.path == f"/hwmon/{CPU}/stall-probe"
    assert client.stall_probe_status() is None, "404 = no probe since the daemon started"
    client.cancel_stall_probe()
    assert (requests[-1].method, requests[-1].url.path) == ("DELETE", "/diagnostics/stall-probe")


def test_the_acknowledgement_has_no_default():
    client = _client(lambda r: httpx.Response(202, json=probe_run(None, state="running")))
    with pytest.raises(TypeError):
        client.start_stall_probe(CPU)  # type: ignore[call-arg]


# ── Models ──────────────────────────────────────────────────────────────────


def test_a_probe_run_parses_and_tolerates_the_unknown():
    body = {**probe_run(), "a_future_field": True, "outcome": "a_future_outcome"}
    body["points"].append({"phase": "a_future_phase", "commanded_pct": 4})
    run = parse_stall_probe_run(body)
    assert run.outcome == "a_future_outcome", "an unrecognised token is kept (273-i)"
    assert run.points[-1].phase == "a_future_phase"
    assert run.points[1].observation == "stalled" and run.refresh_ms == 2000


def test_the_new_wire_fields_parse_and_absent_means_not_said():
    (entry,) = parse_fans({"fans": [fan(CPU, corrections=4, not_holding=True)]})
    assert (entry.duty_corrections, entry.duty_not_holding) == (4, True)
    (old,) = parse_fans({"fans": [fan(CPU, corrections=None, not_holding=None)]})
    assert (old.duty_corrections, old.duty_not_holding) == (None, None)

    hw = parse_hardware_diagnostics(
        {
            "kernel_release": "6.18.2",
            "board": {"vendor": "V", "name": "N", "bios_version": "F1", "bios_date": "01/02/2025"},
            "kernel_modules": [
                {
                    "name": "it87",
                    "loaded": True,
                    "in_mainline": False,
                    "version": "1",
                    "srcversion": "S",
                    "out_of_tree": True,
                }
            ],
        }
    )
    assert hw.kernel_release == "6.18.2" and hw.board.bios_date == "01/02/2025"
    assert (hw.kernel_modules[0].srcversion, hw.kernel_modules[0].out_of_tree) == ("S", True)
    assert parse_hardware_diagnostics({}).kernel_release is None

    run = parse_characterization_run(
        {
            "summary": {"monotonic_falling": True, "monotonic_rising": False},
            "points": [{"stability": {"window_start_ms": 5400, "update_interval_ms": 2000}}],
        }
    )
    assert (run.summary.monotonic_falling, run.summary.monotonic_rising) == (True, False)
    assert run.points[0].stability.window_start_ms == 5400
    cp = parse_control_path_run(
        {
            "cycles": [
                {
                    "cycle": 2,
                    "baseline_settled": False,
                    "settle_wait_ms": 15000,
                    "observations": [{"noise_floor_from_cycle_1": True}],
                },
                {"cycle": 1},
            ]
        }
    )
    assert (cp.cycles[0].baseline_settled, cp.cycles[0].settle_wait_ms) == (False, 15000)
    assert cp.cycles[0].observations[0].noise_floor_from_cycle_1 is True
    assert (cp.cycles[1].baseline_settled, cp.cycles[1].settle_wait_ms) == (None, None)


# ── Capability registry ─────────────────────────────────────────────────────


@pytest.mark.parametrize("feature", ["stall_probe", "duty_reconciliation"])
def test_the_new_flags_are_registered_and_read_from_the_wire(feature):
    assert df.DAEMON_FEATURE_CAPABILITY_FLAGS[feature] == feature
    for value in (True, False):
        caps = parse_capabilities({"control": {feature: value}})
        assert df.daemon_supports(feature, caps) is value
    assert df.daemon_supports(feature, parse_capabilities({"control": {}})) is False
    assert "requires control-ofc-daemon" in df.unsupported_feature_message(feature)


def test_the_evidence_gate_has_no_flag_and_compares_the_version():
    assert "settled_diagnostic_evidence" not in df.DAEMON_FEATURE_CAPABILITY_FLAGS
    assert df.daemon_supports("settled_diagnostic_evidence", object()) is None
    for version, ok in (("2.51.3", False), ("2.52.0", True), ("2.60.1", True), ("", False)):
        caps = parse_capabilities({"daemon_version": version})
        assert df.daemon_meets_minimum("settled_diagnostic_evidence", caps) is ok
    with pytest.raises(KeyError):
        df.daemon_meets_minimum("not_a_feature", object())


# ── Paths and the support bundle ────────────────────────────────────────────


def test_reports_live_under_the_xdg_data_home(monkeypatch, tmp_path):
    from control_ofc import paths

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.data_dir() == tmp_path / "control-ofc"
    assert paths.reports_dir() == tmp_path / "control-ofc" / "reports"


def test_the_support_bundle_names_the_gui_version(tmp_path):
    from control_ofc.constants import APP_VERSION
    from control_ofc.services.app_state import AppState
    from control_ofc.services.diagnostics_service import DiagnosticsService

    svc = DiagnosticsService(AppState())
    out = tmp_path / "bundle.json"
    svc.export_support_bundle(out)
    assert json.loads(out.read_text())["system"]["gui_version"] == APP_VERSION


# ── The worker's verbatim capture ───────────────────────────────────────────


def test_the_worker_hands_on_the_raw_bodies(qtbot):
    from control_ofc.ui.pages.diagnostics_workers import _PwmReportWorker

    routes = {
        "/capabilities": (200, {"daemon_version": "2.54.0", "api_version": 1, "control": {}}),
        "/status": (200, {"thermal_state": "normal"}),
        "/fans": (200, {"fans": [fan(CPU)]}),
        "/sensors": (200, {"sensors": []}),
        "/hwmon/headers": (200, {"headers": []}),
        "/diagnostics/hardware": (200, {"kernel_release": "6.18.2"}),
        "/profile/active": (200, {"active": True, "profile_id": "quiet", "profile_name": "Q"}),
        "/profiles/quiet": (200, {"id": "quiet", "controls": [], "curves": []}),
        "/inventory/cooling-devices": (404, {"error": {"code": "not_found", "message": "x"}}),
        "/diagnostics/control-path": (404, {"error": {"code": "not_found", "message": "x"}}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(request.url.path, (500, {"error": {"code": "internal_error"}}))
        return httpx.Response(status, json=body)

    worker = _PwmReportWorker("/tmp/unused.sock")
    worker._client = _client(handler, worker._observe)
    snap = worker._execute("snapshot", {})
    assert snap.ok
    bundle = snap.body
    assert bundle["hardware"]["body"] == {"kernel_release": "6.18.2"}
    assert bundle["profile"]["body"]["id"] == "quiet", "the active profile is fetched by id"
    # A route an older daemon lacks is recorded as its 404, not dropped.
    assert bundle["cooling_devices"]["status"] == 404

    routes["/hwmon/" + CPU + "/stall-probe"] = (
        400,
        {
            "error": {
                "code": "validation_error",
                "message": "pump",
                "details": {"reason": "pump_protected"},
            }
        },
    )
    out = worker._execute(
        "start_probe", {"header_id": CPU, "args": {"acknowledge_below_floor": True}}
    )
    assert not out.ok and out.status == 400 and out.category == "error"
    assert out.details == {"reason": "pump_protected"}
    assert out.body["error"]["details"]["reason"] == "pump_protected"
