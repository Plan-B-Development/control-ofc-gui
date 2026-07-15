"""Tests for the RDNA3+ ppfeaturemask kernel-parameter guidance.

Originally these pinned the Diagnostics ``_gpu_diag_label`` prose ("Fan control
requires bit 14 …", "ppfeaturemask: not set …"). That inline prose was dropped
when the Diagnostics page was retired (approved). The *actionable* guidance
survives — and is what the operator actually needs — as the ``["fix"]`` string on
the readiness problem list. These tests re-vehicle onto that live pure function
(``detect_readiness_problems``) so the fix text can't silently regress:

* read-only GPU with no ppfeaturemask at all → a ``gpu_readonly`` problem whose
  fix tells the user to add ``amdgpu.ppfeaturemask=0xffffffff``.
* ppfeaturemask present but bit 14 unset → a ``gpu_ppfeaturemask`` problem with
  the same actionable fix (and the read-only variant must not also fire).
* an already-writable GPU, or a correctly-configured PMFW GPU, raises neither.
"""

from __future__ import annotations

from control_ofc.api.models import (
    BoardInfo,
    GpuDiagnosticsInfo,
    HardwareDiagnosticsResult,
    HwmonDiagnostics,
    ThermalSafetyInfo,
)
from control_ofc.ui.widgets.readiness_report import detect_readiness_problems

_KERNEL_ARG = "amdgpu.ppfeaturemask=0xffffffff"


def _diag(
    *,
    fan_control_method: str = "none",
    ppfeaturemask: str | None = None,
    bit14_set: bool = False,
) -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(),
        gpu=GpuDiagnosticsInfo(
            pci_bdf="0000:03:00.0",
            model_name="9070XT",
            fan_control_method=fan_control_method,
            ppfeaturemask=ppfeaturemask,
            ppfeaturemask_bit14_set=bit14_set,
        ),
        thermal_safety=ThermalSafetyInfo(),
        board=BoardInfo(),
    )


def _problems_by_key(diag: HardwareDiagnosticsResult) -> dict[str, dict]:
    return {p["key"]: p for p in detect_readiness_problems(diag)}


class TestRdnaKernelParameterFix:
    def test_readonly_gpu_without_mask_surfaces_ppfeaturemask_fix(self) -> None:
        # The classic fresh-install case for an RX 9070: card is detected,
        # daemon reports read_only because the PMFW path is gated, and
        # ppfeaturemask is None because the user never added the kernel arg.
        problems = _problems_by_key(_diag(fan_control_method="read_only"))
        assert "gpu_readonly" in problems
        assert _KERNEL_ARG in problems["gpu_readonly"]["fix"]

    def test_no_gpu_problem_when_already_writable(self) -> None:
        # Pre-RDNA3 / properly-configured card: don't badger the user.
        keys = {p["key"] for p in detect_readiness_problems(_diag(fan_control_method="hwmon_pwm"))}
        assert "gpu_readonly" not in keys
        assert "gpu_ppfeaturemask" not in keys

    def test_mask_present_bit14_unset_surfaces_ppfeaturemask_fix(self) -> None:
        # Regression: the mask is present but bit 14 is unset — this must fire
        # the ppfeaturemask fix, and only that one variant (the read-only leg is
        # an ``elif`` in the detector, so it must not also appear).
        problems = _problems_by_key(
            _diag(fan_control_method="read_only", ppfeaturemask="0xffff", bit14_set=False)
        )
        assert "gpu_ppfeaturemask" in problems
        assert _KERNEL_ARG in problems["gpu_ppfeaturemask"]["fix"]
        assert "gpu_readonly" not in problems

    def test_no_gpu_problem_when_mask_set_with_bit14(self) -> None:
        # Correctly configured PMFW card: neither variant of the fix fires.
        keys = {
            p["key"]
            for p in detect_readiness_problems(
                _diag(
                    fan_control_method="pmfw_curve",
                    ppfeaturemask="0xffffffff",
                    bit14_set=True,
                )
            )
        }
        assert "gpu_ppfeaturemask" not in keys
        assert "gpu_readonly" not in keys
