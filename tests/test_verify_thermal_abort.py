"""Verify workers map a daemon thermal_abort (Phase 6 / DEC-201) to a soft
safety notice ('unavailable' category = message shown verbatim), while other
daemon errors stay hard ('error' category = prefixed as a failure)."""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.errors import DaemonError
from control_ofc.services.daemon_features import unsupported_feature_message
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
    # `UDOC-l`: the message now comes from the shared registry so it names the
    # daemon version that provides the route. Asserted as a relationship against
    # the registry rather than as a literal — a literal here would have to be
    # re-edited every time the wording moves, and would not check the thing that
    # matters, which is that the worker emits the registry's message at all.
    assert seen == [("unsupported", unsupported_feature_message("gpu_fan_verify"))]
    assert "1.11.0" in seen[0][1], "the message must name the version that provides the route"


def test_hwmon_verify_thermal_forcing_refusal_is_soft(qapp):
    """DEC-297: the second safety refusal, which uses a different code.

    While the ladder is forcing a duty the daemon returns `validation_error`
    with `retryable: true` — the 85 degC test cannot see the latched 80-85 degC
    band. It is protection, not failure, so it must be softened exactly like a
    `thermal_abort`; showing "Verify error: ..." would tell the user their
    hardware failed while the daemon was protecting it.
    """
    worker = _VerifyWorker("/tmp/x.sock")
    client = MagicMock()
    client.verify_hwmon_pwm.side_effect = DaemonError(
        code="validation_error",
        message="thermal safety is forcing fan output (emergency); a fan verify cannot run",
        status=409,
        retryable=True,
    )
    worker._ensure_client = MagicMock(return_value=client)
    seen = _capture(worker)
    worker.do_verify("hwmon:x")
    assert seen == [
        (
            "unavailable",
            "thermal safety is forcing fan output (emergency); a fan verify cannot run",
        )
    ]


def test_gpu_verify_thermal_forcing_refusal_is_soft(qapp):
    """DEC-297: the GPU arm shares `verify_thermal_guard`, so it shares the code."""
    worker = _GpuVerifyWorker("/tmp/x.sock")
    client = MagicMock()
    client.verify_gpu_fan.side_effect = DaemonError(
        code="validation_error",
        message="thermal safety is forcing fan output (recovery); a fan verify cannot run",
        status=409,
        retryable=True,
    )
    worker._ensure_client = MagicMock(return_value=client)
    seen = _capture(worker)
    worker.do_verify("0000:03:00.0")
    assert seen == [
        (
            "unavailable",
            "thermal safety is forcing fan output (recovery); a fan verify cannot run",
        )
    ]


def test_a_non_retryable_validation_error_stays_hard(qapp):
    """The gate is `retryable`, not the code alone.

    `validation_error` is also the daemon's code for a genuinely malformed
    request and for the single-flight "already in progress" refusal, which is
    NOT retryable. Softening every `validation_error` would hide a real client
    bug behind a reassuring notice, so this pins the discriminator.
    """
    worker = _VerifyWorker("/tmp/x.sock")
    client = MagicMock()
    client.verify_hwmon_pwm.side_effect = DaemonError(
        code="validation_error", message="unknown header id", status=400, retryable=False
    )
    worker._ensure_client = MagicMock(return_value=client)
    seen = _capture(worker)
    worker.do_verify("hwmon:x")
    assert seen == [("error", "unknown header id")]
