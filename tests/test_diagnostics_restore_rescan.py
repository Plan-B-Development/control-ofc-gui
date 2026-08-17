"""GPU restore-to-automatic + hwmon rescan — client/worker/demo surface (DEC-147).

Covers the ``hwmon_rescan`` client wrapper (path + parsing), the shared
``_HwDiagWorker`` / ``_GpuVerifyWorker`` signal surface, and the demo stubs. The
GUI-side page behaviour (button visibility, D2 gating, click paths, result
rendering, header/refetch side-effects) moved to the System State page when the
legacy Diagnostics page was retired (DEC-216) and is covered by
``test_system_state_page`` (rescan run paths + GPU restore gate/run).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.client import DaemonClient
from control_ofc.constants import OPENFAN_RESCAN_TIMEOUT_S
from control_ofc.services.demo_service import DemoService
from control_ofc.ui.pages.diagnostics_workers import _GpuVerifyWorker, _HwDiagWorker

# ── Client wrapper ───────────────────────────────────────────────────


def test_hwmon_rescan_posts_and_parses_headers():
    client = DaemonClient.__new__(DaemonClient)
    client._post = MagicMock(
        return_value={
            "api_version": 1,
            "headers": [
                {"id": "hwmon:nct6775:pwm1", "label": "CPU_FAN", "is_writable": True},
                {"id": "hwmon:nct6775:pwm2", "label": "SYS_FAN1", "is_writable": False},
            ],
            "count": 2,
        }
    )

    headers = client.hwmon_rescan()

    client._post.assert_called_once_with("/hwmon/rescan")
    assert [h.id for h in headers] == ["hwmon:nct6775:pwm1", "hwmon:nct6775:pwm2"]
    assert headers[0].is_writable is True
    assert headers[1].is_writable is False


def test_hwmon_rescan_empty_headers():
    client = DaemonClient.__new__(DaemonClient)
    client._post = MagicMock(return_value={"api_version": 1, "headers": [], "count": 0})
    assert client.hwmon_rescan() == []


# ── Worker signal surface ────────────────────────────────────────────


def test_do_reset_emits_reset_ok_on_success():
    """DEC-231: exercise do_reset (was an existence-only ``callable`` check) — a
    successful reset forwards the client result on reset_ok."""
    worker = _GpuVerifyWorker("/tmp/x.sock")
    result = MagicMock()
    worker._client = MagicMock(reset_gpu_fan=MagicMock(return_value=result))
    got = []
    worker.reset_ok.connect(got.append)

    worker.do_reset("0000:2d:00.0")

    worker._client.reset_gpu_fan.assert_called_once_with("0000:2d:00.0")
    assert got == [result]


def test_do_reset_maps_timeout_to_unavailable():
    from control_ofc.api.errors import DaemonTimeout

    worker = _GpuVerifyWorker("/tmp/x.sock")
    worker._client = MagicMock(reset_gpu_fan=MagicMock(side_effect=DaemonTimeout("slow")))
    cats = []
    worker.reset_error.connect(lambda cat, _msg: cats.append(cat))

    worker.do_reset("gpu")

    assert cats == ["unavailable"]


def test_do_rescan_emits_rescan_ok_with_headers():
    """DEC-231: exercise do_rescan (was an existence-only ``callable`` check)."""
    worker = _HwDiagWorker("/tmp/x.sock")
    headers = [MagicMock()]
    caps = MagicMock()
    caps.control.openfan_rescan = False
    worker._client = MagicMock(
        hwmon_rescan=MagicMock(return_value=headers),
        capabilities=MagicMock(return_value=caps),
    )
    got = []
    worker.rescan_ok.connect(lambda h, port: got.append((h, port)))

    worker.do_rescan()

    worker._client.hwmon_rescan.assert_called_once_with()
    assert got == [(headers, "")]


def test_do_rescan_maps_daemon_error_to_error_category():
    from control_ofc.api.errors import DaemonError

    worker = _HwDiagWorker("/tmp/x.sock")
    worker._client = MagicMock(
        hwmon_rescan=MagicMock(
            side_effect=DaemonError(status=500, code="internal_error", message="boom")
        )
    )
    errs = []
    worker.rescan_error.connect(lambda cat, msg: errs.append((cat, msg)))

    worker.do_rescan()

    assert errs == [("error", "boom")]


def test_do_reset_maps_daemon_error_to_error_category():
    """do_reset has no 'unsupported' arm — a 404/other DaemonError is a real
    error (the reset route predates every supported daemon)."""
    from control_ofc.api.errors import DaemonError

    worker = _GpuVerifyWorker("/tmp/x.sock")
    worker._client = MagicMock(
        reset_gpu_fan=MagicMock(
            side_effect=DaemonError(status=500, code="internal_error", message="boom")
        )
    )
    errs = []
    worker.reset_error.connect(lambda cat, msg: errs.append((cat, msg)))

    worker.do_reset("gpu")

    assert errs == [("error", "boom")]


# ── Demo stubs ───────────────────────────────────────────────────────


def test_demo_reset_gpu_fan_reports_success():
    res = DemoService().reset_gpu_fan("0000:2d:00.0")
    assert res.gpu_id == "0000:2d:00.0"
    assert res.reset is True


def test_demo_hwmon_rescan_returns_demo_headers():
    demo = DemoService()
    assert [h.id for h in demo.hwmon_rescan()] == [h.id for h in demo.hwmon_headers()]


# ── OpenFan rescan (DEC-265) ─────────────────────────────────────────


def test_openfan_rescan_posts_and_returns_the_payload():
    client = DaemonClient.__new__(DaemonClient)
    client._post = MagicMock(
        return_value={"adopted": True, "already_connected": False, "port": "/dev/ttyACM0"}
    )

    result = client.openfan_rescan()

    client._post.assert_called_once_with("/fans/openfan/rescan", timeout=OPENFAN_RESCAN_TIMEOUT_S)
    assert result["adopted"] is True
    assert result["port"] == "/dev/ttyACM0"


def test_do_rescan_asks_the_daemon_to_look_for_an_openfan_controller():
    """DEC-265: the hwmon rescan carries the OpenFan leg.

    The gap it closes is not cosmetic — a controller adopted only at boot means
    the 105 °C emergency has no path to those fans for the rest of the daemon's
    life. If this call is dropped, the route exists and nothing ever calls it.
    """
    worker = _HwDiagWorker("/tmp/x.sock")
    caps = MagicMock()
    caps.control.openfan_rescan = True
    worker._client = MagicMock(
        capabilities=MagicMock(return_value=caps),
        openfan_rescan=MagicMock(return_value={"adopted": True, "port": "/dev/ttyACM0"}),
        hwmon_rescan=MagicMock(return_value=[]),
    )

    worker.do_rescan()

    worker._client.openfan_rescan.assert_called_once_with()


def test_do_rescan_skips_the_openfan_leg_on_a_daemon_without_the_route():
    """Capability-gated, so an older daemon is never asked and never 404s."""
    worker = _HwDiagWorker("/tmp/x.sock")
    caps = MagicMock()
    caps.control.openfan_rescan = False
    worker._client = MagicMock(
        capabilities=MagicMock(return_value=caps),
        openfan_rescan=MagicMock(),
        hwmon_rescan=MagicMock(return_value=[]),
    )

    worker.do_rescan()

    worker._client.openfan_rescan.assert_not_called()


def test_a_failing_openfan_leg_does_not_fail_the_hwmon_rescan():
    """Finding no controller is the NORMAL outcome on a machine without one.

    Letting that propagate would report the hwmon rescan as broken on every
    machine that has no OpenFan hardware.
    """
    from control_ofc.api.errors import DaemonError

    worker = _HwDiagWorker("/tmp/x.sock")
    caps = MagicMock()
    caps.control.openfan_rescan = True
    headers = [MagicMock()]
    worker._client = MagicMock(
        capabilities=MagicMock(return_value=caps),
        openfan_rescan=MagicMock(
            side_effect=DaemonError(
                status=503, code="hardware_unavailable", message="no controller"
            )
        ),
        hwmon_rescan=MagicMock(return_value=headers),
    )
    got = []
    errs = []
    worker.rescan_ok.connect(lambda h, port: got.append((h, port)))
    worker.rescan_error.connect(lambda c, m: errs.append((c, m)))

    worker.do_rescan()

    # DEC-266: an unadopted leg reports an empty port, so the result line keeps
    # its restart advice instead of claiming an adoption that did not happen.
    assert got == [(headers, "")], "the hwmon rescan must still succeed"
    assert errs == []


def test_capabilities_default_openfan_rescan_false_on_an_older_daemon():
    """AIP-180: a daemon that omits the field must read as 'no such route'."""
    from control_ofc.api.models import parse_capabilities

    caps = parse_capabilities({"control": {"autonomous_control": True}})
    assert caps.control.openfan_rescan is False

    caps = parse_capabilities({"control": {"openfan_rescan": True}})
    assert caps.control.openfan_rescan is True


# ── DEC-266: the OpenFan leg cannot wedge the button or mis-advise the user ──


def test_a_malformed_capabilities_body_does_not_wedge_the_rescan_button():
    """The leg must swallow EVERY failure, not just the daemon-error family.

    ``parse_capabilities`` on a malformed 200 raises ``AttributeError`` /
    ``TypeError``, which are not daemon errors. Those escaped both this handler
    and ``do_rescan``'s, so neither ``rescan_ok`` nor ``rescan_error`` fired —
    and the page only clears ``_rescan_in_flight`` in those two slots. Rescan
    Hardware was then dead for the rest of the session with the chip stuck on
    "Rescanning hardware...". Same shape as the escape documented in
    ``services/polling.py``.
    """
    worker = _HwDiagWorker("/tmp/x.sock")
    headers = [MagicMock()]
    worker._client = MagicMock(
        capabilities=MagicMock(side_effect=AttributeError("'NoneType' has no attribute 'get'")),
        hwmon_rescan=MagicMock(return_value=headers),
    )
    got: list[tuple] = []
    errs: list[tuple] = []
    worker.rescan_ok.connect(lambda h, port: got.append((h, port)))
    worker.rescan_error.connect(lambda c, m: errs.append((c, m)))

    worker.do_rescan()

    assert got == [(headers, "")], "a broken capabilities body must not swallow the whole rescan"
    assert errs == []


def test_the_hwmon_leg_runs_before_the_openfan_probe():
    """DEC-266: the contracted leg must not queue behind the opportunistic one.

    Serial probing can take seconds. Running it first held the "Rescanning..."
    chip open on a sweep the user did not ask for, and meant a client closed by
    teardown surfaced out of the *second* call as an httpx ``RuntimeError``
    rather than out of the first as a clean ``DaemonUnavailable``.
    """
    worker = _HwDiagWorker("/tmp/x.sock")
    order: list[str] = []
    caps = MagicMock()
    caps.control.openfan_rescan = True
    worker._client = MagicMock(
        capabilities=MagicMock(return_value=caps),
        hwmon_rescan=MagicMock(side_effect=lambda: order.append("hwmon") or []),
        openfan_rescan=MagicMock(side_effect=lambda: order.append("openfan") or {}),
    )

    worker.do_rescan()

    assert order == ["hwmon", "openfan"]


def test_an_adopted_controller_is_reported_to_the_caller():
    """The adoption is the one outcome of this leg worth surfacing.

    Without it the UI's success line still advised a daemon restart — the exact
    thing DEC-265 makes unnecessary — in the one case the feature had just
    handled without one.
    """
    worker = _HwDiagWorker("/tmp/x.sock")
    caps = MagicMock()
    caps.control.openfan_rescan = True
    worker._client = MagicMock(
        capabilities=MagicMock(return_value=caps),
        hwmon_rescan=MagicMock(return_value=[]),
        openfan_rescan=MagicMock(
            return_value={"adopted": True, "already_connected": False, "port": "/dev/ttyACM1"}
        ),
    )
    got: list[tuple] = []
    worker.rescan_ok.connect(lambda h, port: got.append((h, port)))

    worker.do_rescan()

    assert got == [([], "/dev/ttyACM1")]


def test_an_already_connected_controller_is_not_reported_as_a_new_adoption():
    """`adopted: false` is the idempotent no-op, not an adoption."""
    worker = _HwDiagWorker("/tmp/x.sock")
    caps = MagicMock()
    caps.control.openfan_rescan = True
    worker._client = MagicMock(
        capabilities=MagicMock(return_value=caps),
        hwmon_rescan=MagicMock(return_value=[]),
        # `port` IS present, so the test fails if the `adopted` gate is removed.
        # Without it the gate could be deleted and this test would still pass:
        # `str(result.get("port") or "")` returns "" for a payload with no port
        # regardless of the branch taken.
        openfan_rescan=MagicMock(
            return_value={
                "adopted": False,
                "already_connected": True,
                "port": "/dev/ttyACM0",
            }
        ),
    )
    got: list[tuple] = []
    worker.rescan_ok.connect(lambda h, port: got.append((h, port)))

    worker.do_rescan()

    assert got == [([], "")]


def test_a_non_daemon_error_from_the_hwmon_leg_still_reports_instead_of_wedging():
    """DEC-266: the backstop on the contracted leg.

    `parse_hwmon_headers` raises `TypeError` on a `"headers": null` body, and a
    client closed by teardown raises a bare `RuntimeError` — neither is in the
    daemon-error family. Both escaped every handler, so neither signal fired and
    the page's in-flight latch never cleared. Fixing only the OpenFan leg left
    this one reachable.
    """
    worker = _HwDiagWorker("/tmp/x.sock")
    worker._client = MagicMock(
        hwmon_rescan=MagicMock(side_effect=TypeError("'NoneType' is not iterable"))
    )
    got: list[tuple] = []
    errs: list[tuple] = []
    worker.rescan_ok.connect(lambda h, port: got.append((h, port)))
    worker.rescan_error.connect(lambda c, m: errs.append((c, m)))

    worker.do_rescan()

    assert got == []
    assert len(errs) == 1, "a failure must be reported, not swallowed into silence"
    assert errs[0][0] == "error"


def test_a_dead_socket_in_the_trailing_openfan_leg_drops_the_stale_client():
    """The leg now runs last, so `do_rescan`'s connection handler cannot see a
    socket that dies inside it. Left alone, the stale client survives into the
    chained diagnostics refetch and fails there instead."""
    worker = _HwDiagWorker("/tmp/x.sock")
    caps = MagicMock()
    caps.control.openfan_rescan = True
    client = MagicMock(
        capabilities=MagicMock(return_value=caps),
        hwmon_rescan=MagicMock(return_value=[]),
        openfan_rescan=MagicMock(side_effect=ConnectionError("socket went away")),
    )
    worker._client = client
    got: list[tuple] = []
    worker.rescan_ok.connect(lambda h, port: got.append((h, port)))

    worker.do_rescan()

    assert got == [([], "")], "the hwmon result still stands"
    assert worker._client is None, "a dead socket must not be reused by the next call"
    client.close.assert_called_once()
