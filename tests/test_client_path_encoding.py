"""Every id the client puts in a URL path reaches the daemon intact.

A header id embeds its sysfs label (``System Fan #1`` on nct6687 boards) and a
profile id may hold ``#``, ``?``, ``%`` or a space (both sides allow them). Sent
raw, httpx read ``#`` as a fragment and dropped the rest and ``?`` as a query,
so ``DELETE /profiles/a?x`` deleted profile ``a`` and a diagnostic on
``System Fan #1`` 404'd after its preflight passed. The daemon decodes the
segment after routing (pinned daemon-side by
``ipc_integration::profile_routes_decode_a_percent_encoded_id``).
"""

from __future__ import annotations

import ast
import contextlib
import inspect
from urllib.parse import unquote

import httpx
import pytest

from control_ofc.api import client as client_module
from control_ofc.api.client import DaemonClient

HOSTILE = "hwmon:nct6687:nct6687.2592:pwm1:System Fan #1?x=%41/b"

# (method, call, path before the id, path after the id)
CASES = [
    (
        "delete_cooling_device",
        lambda c, i: c.delete_cooling_device(i),
        "/config/cooling-device/",
        "",
    ),
    (
        "validation_session_by_id",
        lambda c, i: c.validation_session_by_id(i),
        "/validation/sessions/",
        "",
    ),
    ("verify_hwmon_pwm", lambda c, i: c.verify_hwmon_pwm(i), "/hwmon/", "/verify"),
    (
        "start_characterization",
        lambda c, i: c.start_characterization(i),
        "/hwmon/",
        "/characterize",
    ),
    (
        "start_control_path_discovery",
        lambda c, i: c.start_control_path_discovery(i),
        "/hwmon/",
        "/discover-control-path",
    ),
    (
        "start_stall_probe",
        lambda c, i: c.start_stall_probe(i, acknowledge_below_floor=False),
        "/hwmon/",
        "/stall-probe",
    ),
    ("get_profile", lambda c, i: c.get_profile(i), "/profiles/", ""),
    ("update_profile", lambda c, i: c.update_profile(i, {"id": i}), "/profiles/", ""),
    ("delete_profile", lambda c, i: c.delete_profile(i), "/profiles/", ""),
    ("override_take", lambda c, i: c.override_take(i, 50), "/control/", "/override"),
    ("override_renew", lambda c, i: c.override_renew(i, 1), "/control/", "/override/renew"),
    ("override_release", lambda c, i: c.override_release(i, 1), "/control/", "/override"),
    ("fan_identify", lambda c, i: c.fan_identify(i, "stop"), "/fans/", "/identify"),
    ("reset_gpu_fan", lambda c, i: c.reset_gpu_fan(i), "/gpu/", "/fan/reset"),
    ("verify_gpu_fan", lambda c, i: c.verify_gpu_fan(i), "/gpu/", "/fan/verify"),
]


def _capturing_client(seen: list[httpx.Request]) -> DaemonClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    client = DaemonClient.__new__(DaemonClient)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://localhost"
    )
    client._response_observer = None
    return client


# A whole-segment "." or ".." is a dot segment httpx would remove: GET
# /profiles/. became GET /profiles, the list. The daemon accepts both as ids.
IDS = [HOSTILE, ".", "..", "é ü"]


@pytest.mark.parametrize("ident", IDS, ids=["hostile", "dot", "dotdot", "non-ascii"])
@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_the_id_round_trips_as_one_path_segment(case, ident):
    _name, call, prefix, suffix = case
    seen: list[httpx.Request] = []
    # Only the request matters; parsing the stub ``{}`` response may fail.
    with contextlib.suppress(Exception):
        call(_capturing_client(seen), ident)
    assert len(seen) == 1, "precondition: the call sent its request"
    request = seen[0]
    raw = request.url.raw_path.decode("ascii")
    assert request.url.query == b"", "a `?` in the id became a query"
    assert raw.startswith(prefix) and raw.endswith(suffix), raw
    segment = raw[len(prefix) : len(raw) - len(suffix)]
    assert "/" not in segment, f"the id spans several path segments: {raw}"
    assert unquote(segment) == ident


def test_every_path_parameter_is_encoded():
    """Invert the default: every interpolation in a client URL path must go
    through ``_seg`` (or be an ``int``), so a new method cannot forget."""
    tree = ast.parse(inspect.getsource(client_module))
    paths = 0
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or not node.values:
            continue
        first = node.values[0]
        if not (isinstance(first, ast.Constant) and str(first.value).startswith("/")):
            continue
        paths += 1
        for part in node.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            func = part.value.func if isinstance(part.value, ast.Call) else None
            name = getattr(func, "id", None)
            if name not in ("_seg", "int", "quote"):
                offenders.append(f"line {node.lineno}: {ast.unparse(part.value)}")
    assert paths >= len(CASES), "precondition: the scan found the client's paths"
    assert not offenders, offenders


def test_every_encoded_path_method_has_a_round_trip_case():
    """A method that builds an id path is listed in CASES, so its request is
    proven, not just scanned."""
    tree = ast.parse(inspect.getsource(client_module))
    methods = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "_seg"
                and fn.name != "_seg"
            ):
                methods.add(fn.name)
    assert methods, "precondition: some method encodes a path segment"
    assert methods == {c[0] for c in CASES}
