"""Tests for the diagnostics page's RDNA3+ kernel-parameter guidance.

The pre-existing tip surface only fired when ``ppfeaturemask`` had a value
present and bit 14 was unset. A user who never added the kernel parameter
at all (the most common state on a fresh install) saw no guidance — the
``if gpu.ppfeaturemask:`` branch was simply skipped.

These tests pin the broader behaviour: when the GPU is read-only and the
kernel parameter is completely absent, the diagnostics page must surface
the actionable hint.
"""

from __future__ import annotations

from control_ofc.api.models import (
    BoardInfo,
    GpuDiagnosticsInfo,
    HardwareDiagnosticsResult,
    HwmonDiagnostics,
    ThermalSafetyInfo,
)
from control_ofc.services.app_state import AppState
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage


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


class TestRdnaKernelParameterHint:
    def test_hint_shown_when_ppfeaturemask_absent_and_read_only(self, qtbot):
        # The classic fresh-install case for an RX 9070: card is detected,
        # daemon reports read_only because the PMFW path is gated, and
        # ppfeaturemask is None because the user never added the kernel arg.
        page = DiagnosticsPage(state=AppState())
        qtbot.addWidget(page)
        page._populate_hw_diagnostics(_diag(fan_control_method="read_only"))
        text = page._gpu_diag_label.text()
        assert "ppfeaturemask: not set" in text
        assert "amdgpu.ppfeaturemask=0xffffffff" in text
        assert "man control-ofc-daemon" in text

    def test_hint_not_shown_when_already_writable(self, qtbot):
        # Pre-RDNA3 / properly-configured card: don't badger the user.
        page = DiagnosticsPage(state=AppState())
        qtbot.addWidget(page)
        page._populate_hw_diagnostics(_diag(fan_control_method="hwmon_pwm"))
        text = page._gpu_diag_label.text()
        assert "ppfeaturemask: not set" not in text

    def test_existing_bit14_unset_path_still_works(self, qtbot):
        # Regression: the prior tip path fired only inside
        # ``if gpu.ppfeaturemask:``; make sure it still fires when the
        # mask is present but bit 14 is unset.
        page = DiagnosticsPage(state=AppState())
        qtbot.addWidget(page)
        page._populate_hw_diagnostics(
            _diag(
                fan_control_method="read_only",
                ppfeaturemask="0xffff",
                bit14_set=False,
            )
        )
        text = page._gpu_diag_label.text()
        assert "Fan control requires bit 14" in text
        # The new "absent" branch must not also fire for the same GPU —
        # only one variant of the tip should be shown.
        assert "ppfeaturemask: not set" not in text

    def test_no_double_tip_when_mask_set_with_bit14(self, qtbot):
        page = DiagnosticsPage(state=AppState())
        qtbot.addWidget(page)
        page._populate_hw_diagnostics(
            _diag(
                fan_control_method="pmfw_curve",
                ppfeaturemask="0xffffffff",
                bit14_set=True,
            )
        )
        text = page._gpu_diag_label.text()
        assert "amdgpu.ppfeaturemask=0xffffffff" not in text
        assert "ppfeaturemask: not set" not in text
