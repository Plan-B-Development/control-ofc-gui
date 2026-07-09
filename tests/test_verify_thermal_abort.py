"""Verify workers map a daemon thermal_abort (Phase 6 / DEC-201) to a soft
safety notice ('unavailable' category = message shown verbatim), while other
daemon errors stay hard ('error' category = prefixed as a failure)."""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.errors import DaemonError
from control_ofc.ui.pages.diagnostics_workers import _GpuVerifyWorker, _VerifyWorker


def _capture(worker):
    seen: list[tuple[str, str]] = []
    worker.verify_error.connect(lambda cat, msg: seen.append((cat, msg)))
    return seen


def test_hwmon_verify_thermal_abort_is_soft(qapp):
    worker = _VerifyWorker("/tmp/x.sock")
    client = MagicMock()
    client.verify_hwmon_pwm.side_effect = DaemonError(
        code="thermal_abort", message="Cannot verify while hot: cool down", status=409
    )
    worker._ensure_client = MagicMock(return_value=client)
    seen = _capture(worker)
    worker.do_verify("hwmon:x")
    assert seen == [("unavailable", "Cannot verify while hot: cool down")]


def test_hwmon_verify_other_error_is_hard(qapp):
    worker = _VerifyWorker("/tmp/x.sock")
    client = MagicMock()
    client.verify_hwmon_pwm.side_effect = DaemonError(
        code="hardware_unavailable", message="nope", status=503
    )
    worker._ensure_client = MagicMock(return_value=client)
    seen = _capture(worker)
    worker.do_verify("hwmon:x")
    assert seen == [("error", "nope")]


def test_gpu_verify_thermal_abort_is_soft(qapp):
    worker = _GpuVerifyWorker("/tmp/x.sock")
    client = MagicMock()
    client.verify_gpu_fan.side_effect = DaemonError(
        code="thermal_abort", message="too hot", status=409
    )
    worker._ensure_client = MagicMock(return_value=client)
    seen = _capture(worker)
    worker.do_verify("0000:03:00.0")
    assert seen == [("unavailable", "too hot")]


def test_gpu_verify_other_error_is_hard(qapp):
    # The GPU analog of the hwmon "other error is hard" case: a non-404,
    # non-thermal DaemonError must stay a hard 'error' (prefixed as a failure),
    # not be softened like a thermal_abort.
    worker = _GpuVerifyWorker("/tmp/x.sock")
    client = MagicMock()
    client.verify_gpu_fan.side_effect = DaemonError(
        code="hardware_unavailable", message="nope", status=503
    )
    worker._ensure_client = MagicMock(return_value=client)
    seen = _capture(worker)
    worker.do_verify("0000:03:00.0")
    assert seen == [("error", "nope")]


def test_gpu_verify_404_is_unsupported(qapp):
    # An old daemon predating the GPU-verify route answers 404 — the worker must
    # map that to 'unsupported' (the page then hides the control for the session),
    # NOT a hard 'error'. Complements the page-side test in test_gpu_verify.py,
    # which covers what the page does once it receives 'unsupported'.
    worker = _GpuVerifyWorker("/tmp/x.sock")
    client = MagicMock()
    client.verify_gpu_fan.side_effect = DaemonError(
        code="not_found", message="no route", status=404
    )
    worker._ensure_client = MagicMock(return_value=client)
    seen = _capture(worker)
    worker.do_verify("0000:03:00.0")
    assert seen == [("unsupported", "This daemon version does not support GPU fan verification.")]
