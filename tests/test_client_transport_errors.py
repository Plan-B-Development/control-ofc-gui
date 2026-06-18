"""Regression tests: httpx transport/protocol errors map to DaemonUnavailable.

When the daemon dies mid-response (SIGKILL, OOM, or a restart while writing the
HTTP body — the most common real failure) httpx raises ``RemoteProtocolError`` /
``ReadError`` / ``WriteError``. These are siblings of ``ConnectError`` /
``TimeoutException`` under ``httpx.RequestError`` — NOT subclasses of the two the
client used to map — so before the fix they propagated uncaught out of the
polling and control-loop worker slots (frozen dashboard / dead write worker).

The client now maps the whole ``RequestError`` family to ``DaemonUnavailable``
(retryable → the workers emit ``disconnected`` / ``OUTCOME_UNAVAILABLE`` and
reconnect), while leaving non-``RequestError`` httpx faults (e.g. ``InvalidURL``,
which is our own bug) to surface raw rather than be masked as "daemon gone".
"""

from __future__ import annotations

import httpx
import pytest

from control_ofc.api.client import BASE_URL, DaemonClient
from control_ofc.api.errors import DaemonUnavailable

TRANSPORT_ERRORS = [
    httpx.RemoteProtocolError("Server disconnected without sending a response."),
    httpx.ReadError("peer reset the connection"),
    httpx.WriteError("broken pipe"),
]


def _client_raising(exc: Exception) -> DaemonClient:
    """A DaemonClient whose transport raises *exc* on every request."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    client = DaemonClient(socket_path="/nonexistent/control-ofc.sock")
    client._client.close()  # discard the real uds-backed client
    client._client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    return client


@pytest.mark.parametrize("exc", TRANSPORT_ERRORS, ids=lambda e: type(e).__name__)
def test_get_transport_error_maps_to_unavailable(exc):
    client = _client_raising(exc)
    try:
        with pytest.raises(DaemonUnavailable):
            client.status()  # GET /status
    finally:
        client.close()


@pytest.mark.parametrize("exc", TRANSPORT_ERRORS, ids=lambda e: type(e).__name__)
def test_post_transport_error_maps_to_unavailable(exc):
    client = _client_raising(exc)
    try:
        with pytest.raises(DaemonUnavailable):
            client.fan_identify("openfan:ch00", "stop")  # POST /fans/.../identify
    finally:
        client.close()


def test_invalid_url_is_not_swallowed():
    # InvalidURL is NOT a RequestError — it's a programming bug on our side and
    # must surface raw, not be reclassified as a transient daemon outage.
    client = _client_raising(httpx.InvalidURL("malformed"))
    try:
        with pytest.raises(httpx.InvalidURL):
            client.status()
    finally:
        client.close()
