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
    worker._client = MagicMock(hwmon_rescan=MagicMock(return_value=headers))
    got = []
    worker.rescan_ok.connect(got.append)

    worker.do_rescan()

    worker._client.hwmon_rescan.assert_called_once_with()
    assert got == [headers]


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
