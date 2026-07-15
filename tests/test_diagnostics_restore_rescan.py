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


def test_gpu_worker_has_reset_slot_and_signals():
    worker = _GpuVerifyWorker("/tmp/x.sock")
    assert hasattr(worker, "reset_ok")
    assert hasattr(worker, "reset_error")
    assert callable(worker.do_reset)


def test_hw_diag_worker_has_rescan_slot_and_signals():
    worker = _HwDiagWorker("/tmp/x.sock")
    assert hasattr(worker, "rescan_ok")
    assert hasattr(worker, "rescan_error")
    assert callable(worker.do_rescan)


# ── Demo stubs ───────────────────────────────────────────────────────


def test_demo_reset_gpu_fan_reports_success():
    res = DemoService().reset_gpu_fan("0000:2d:00.0")
    assert res.gpu_id == "0000:2d:00.0"
    assert res.reset is True


def test_demo_hwmon_rescan_returns_demo_headers():
    demo = DemoService()
    assert [h.id for h in demo.hwmon_rescan()] == [h.id for h in demo.hwmon_headers()]
