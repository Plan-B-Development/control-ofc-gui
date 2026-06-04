"""DEC-119 — GUI side of the GPU diagnostics hardening.

Covers:
  * ``parse_hardware_diagnostics`` decoding the additive ``GpuDiagnostics``
    fields (OD_RANGE bounds, fan_minimum_pwm, driver-bound, advisories) plus
    the new top-level ``amd_pci_devices`` / ``amdgpu_module_loaded``.
  * Forward/backward compatibility: an older daemon that omits the fields
    yields safe defaults (no crash, no false positives).
  * The diagnostics page rendering the firmware fan-speed range, per-GPU
    advisories, and the "present but amdgpu not bound" case.
"""

from __future__ import annotations

from control_ofc.api.models import (
    AmdPciDeviceInfo,
    BoardInfo,
    GpuDiagnosticsInfo,
    HardwareDiagnosticsResult,
    HwmonDiagnostics,
    KernelWarning,
    ThermalSafetyInfo,
    parse_hardware_diagnostics,
)
from control_ofc.services.app_state import AppState
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParseNewGpuFields:
    def test_full_payload_decodes_all_new_fields(self):
        payload = {
            "api_version": 1,
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "gpu": {
                "pci_bdf": "0000:03:00.0",
                "pci_device_id": 0x7550,
                "model_name": "RX 9070 XT",
                "fan_control_method": "pmfw_curve",
                "overdrive_enabled": True,
                "zero_rpm_available": True,
                "fan_speed_min_pct": 15,
                "fan_speed_max_pct": 100,
                "fan_minimum_pwm": 20,
                "amdgpu_driver_bound": True,
                "kernel_warnings": [
                    {
                        "id": "rdna_hang_kernel_6_18_6_19",
                        "severity": "critical",
                        "message": "hang regression",
                    }
                ],
            },
            "thermal_safety": {},
            "amd_pci_devices": [
                {
                    "pci_bdf": "0000:03:00.0",
                    "pci_device_id": 0x7550,
                    "driver": "amdgpu",
                    "amdgpu_bound": True,
                    "hwmon_present": True,
                }
            ],
            "amdgpu_module_loaded": True,
        }
        result = parse_hardware_diagnostics(payload)
        assert result.gpu is not None
        assert result.gpu.fan_speed_min_pct == 15
        assert result.gpu.fan_speed_max_pct == 100
        assert result.gpu.fan_minimum_pwm == 20
        assert result.gpu.amdgpu_driver_bound is True
        # kernel_warnings hand-parsed into dataclasses, not left as raw dicts.
        assert len(result.gpu.kernel_warnings) == 1
        assert isinstance(result.gpu.kernel_warnings[0], KernelWarning)
        assert result.gpu.kernel_warnings[0].severity == "critical"
        # Top-level PCI scan.
        assert result.amdgpu_module_loaded is True
        assert len(result.amd_pci_devices) == 1
        assert isinstance(result.amd_pci_devices[0], AmdPciDeviceInfo)
        assert result.amd_pci_devices[0].amdgpu_bound is True

    def test_older_daemon_payload_yields_safe_defaults(self):
        # No new fields anywhere — must not crash and must not invent state.
        payload = {
            "api_version": 1,
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "gpu": {
                "pci_bdf": "0000:03:00.0",
                "fan_control_method": "read_only",
            },
            "thermal_safety": {},
        }
        result = parse_hardware_diagnostics(payload)
        assert result.gpu is not None
        assert result.gpu.fan_speed_min_pct is None
        assert result.gpu.fan_minimum_pwm is None
        # Forward-compat default: an hwmon GPU implies a bound driver.
        assert result.gpu.amdgpu_driver_bound is True
        assert result.gpu.kernel_warnings == []
        assert result.amd_pci_devices == []
        assert result.amdgpu_module_loaded is False

    def test_unbound_gpu_without_hwmon_block(self):
        # The gap-(a) case: AMD VGA device present, amdgpu not bound, so the
        # daemon emits no `gpu` block at all — only the PCI scan sees it.
        payload = {
            "api_version": 1,
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {},
            "amd_pci_devices": [
                {
                    "pci_bdf": "0000:03:00.0",
                    "pci_device_id": 0x7550,
                    "amdgpu_bound": False,
                    "hwmon_present": False,
                }
            ],
            "amdgpu_module_loaded": False,
        }
        result = parse_hardware_diagnostics(payload)
        assert result.gpu is None
        assert len(result.amd_pci_devices) == 1
        assert result.amd_pci_devices[0].amdgpu_bound is False
        assert result.amd_pci_devices[0].driver is None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _result(*, gpu=None, amd_pci_devices=None, amdgpu_module_loaded=False):
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(),
        gpu=gpu,
        thermal_safety=ThermalSafetyInfo(),
        board=BoardInfo(),
        amd_pci_devices=amd_pci_devices or [],
        amdgpu_module_loaded=amdgpu_module_loaded,
    )


class TestRenderNewGpuFields:
    def test_firmware_speed_range_rendered(self, qtbot):
        page = DiagnosticsPage(state=AppState())
        qtbot.addWidget(page)
        gpu = GpuDiagnosticsInfo(
            pci_bdf="0000:03:00.0",
            model_name="9070XT",
            fan_control_method="pmfw_curve",
            fan_speed_min_pct=15,
            fan_speed_max_pct=100,
        )
        page._populate_hw_diagnostics(_result(gpu=gpu))
        text = page._gpu_diag_label.text()
        assert "Firmware fan-speed range: 15% to 100%" in text
        assert "clamped by the GPU firmware" in text

    def test_per_gpu_advisory_rendered(self, qtbot):
        page = DiagnosticsPage(state=AppState())
        qtbot.addWidget(page)
        gpu = GpuDiagnosticsInfo(
            pci_bdf="0000:03:00.0",
            fan_control_method="pmfw_curve",
            kernel_warnings=[
                KernelWarning(id="x", severity="critical", message="hard-hang regression")
            ],
        )
        page._populate_hw_diagnostics(_result(gpu=gpu))
        text = page._gpu_diag_label.text()
        assert "Advisory [critical]: hard-hang regression" in text

    def test_unbound_gpu_rendered_without_hwmon_block(self, qtbot):
        page = DiagnosticsPage(state=AppState())
        qtbot.addWidget(page)
        dev = AmdPciDeviceInfo(
            pci_bdf="0000:03:00.0", pci_device_id=0x7550, driver=None, amdgpu_bound=False
        )
        page._populate_hw_diagnostics(_result(amd_pci_devices=[dev], amdgpu_module_loaded=False))
        text = page._gpu_diag_label.text()
        assert "present but amdgpu is NOT bound" in text
        assert "module is not loaded" in text

    def test_unbound_gpu_with_module_loaded_points_at_bind_failure(self, qtbot):
        page = DiagnosticsPage(state=AppState())
        qtbot.addWidget(page)
        dev = AmdPciDeviceInfo(
            pci_bdf="0000:03:00.0", pci_device_id=0x7550, driver="vfio-pci", amdgpu_bound=False
        )
        page._populate_hw_diagnostics(_result(amd_pci_devices=[dev], amdgpu_module_loaded=True))
        text = page._gpu_diag_label.text()
        assert "did not bind this device" in text
        assert "vfio-pci" in text

    def test_bound_gpu_does_not_render_unbound_warning(self, qtbot):
        page = DiagnosticsPage(state=AppState())
        qtbot.addWidget(page)
        gpu = GpuDiagnosticsInfo(pci_bdf="0000:03:00.0", fan_control_method="pmfw_curve")
        dev = AmdPciDeviceInfo(
            pci_bdf="0000:03:00.0", pci_device_id=0x7550, driver="amdgpu", amdgpu_bound=True
        )
        page._populate_hw_diagnostics(
            _result(gpu=gpu, amd_pci_devices=[dev], amdgpu_module_loaded=True)
        )
        text = page._gpu_diag_label.text()
        assert "NOT bound" not in text
