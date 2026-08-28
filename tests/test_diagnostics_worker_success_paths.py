"""DEC-293: the diagnostics workers' SUCCESS paths, driven through the real signal.

Closes a gap `/ofc:audit` found on 2026-08-28. `_HwDiagWorker.do_fetch` and
`_GpuVerifyWorker.do_verify` had no test of their success path at all: every
existing test either fed the page handler directly
(`test_system_state_page.py` calls `page._on_hw_diag_ok(...)`) or exercised only
the error branches (`test_verify_thermal_abort.py`), and `test_gpu_verify.py`
asserted merely that the signal *attribute exists*.

So a wrong signal name, a forgotten `.emit()`, or an exception swallowed before
the emit would have left the System State hardware-diagnostics panel and the GPU
verify control silently blank — with the whole suite green. That is the
"extracting a rule does not test the call site" family `CLAUDE.md` records as
having recurred five times.

These drive the real `do_*` slot on a real worker and assert the real signal
fires with the payload the client returned. Mirrors the harness already used for
`do_rescan` in `test_diagnostics_restore_rescan.py`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.ui.pages.diagnostics_workers import _GpuVerifyWorker, _HwDiagWorker


def test_hw_diag_worker_emits_fetch_ok_with_the_daemons_result() -> None:
    worker = _HwDiagWorker("/tmp/x.sock")
    report = MagicMock(name="HardwareDiagnosticsResult")
    worker._client = MagicMock(hardware_diagnostics=MagicMock(return_value=report))

    got: list = []
    worker.fetch_ok.connect(got.append)
    errors: list = []
    worker.fetch_error.connect(lambda cat, msg: errors.append((cat, msg)))

    worker.do_fetch()

    assert errors == [], f"a successful fetch must not report an error: {errors}"
    assert got == [report], (
        "fetch_ok did not fire with the daemon's report — the System State "
        "hardware panel would render blank with the suite still green"
    )


def test_gpu_verify_worker_emits_verify_ok_with_the_daemons_result() -> None:
    worker = _GpuVerifyWorker("/tmp/x.sock")
    result = MagicMock(name="GpuVerifyResult")
    worker._client = MagicMock(verify_gpu_fan=MagicMock(return_value=result))

    got: list = []
    worker.verify_ok.connect(got.append)
    errors: list = []
    worker.verify_error.connect(lambda cat, msg: errors.append((cat, msg)))

    worker.do_verify("amd_gpu:0000:03:00.0")

    assert errors == [], f"a successful verify must not report an error: {errors}"
    assert got == [result], (
        "verify_ok did not fire with the daemon's result — the GPU verify "
        "control would render blank with the suite still green"
    )
    worker._client.verify_gpu_fan.assert_called_once_with("amd_gpu:0000:03:00.0")
