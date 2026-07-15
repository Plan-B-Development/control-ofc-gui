"""Tests for hardware readiness UI — models, parsing, dashboard banner, and settings.

The Diagnostics-page readiness-widget tests were retired when
``/diagnostics/hardware`` rendering moved to the live System State page; that
rendering is covered by ``tests/test_system_state_view.py`` +
``tests/test_system_state_page.py``. What stays here is the daemon-model /
parser layer plus the dashboard hwmon banner and the settings flag.
"""

import pytest

from control_ofc.api.models import (
    AcpiConflictInfo,
    AmdGpuCapability,
    Capabilities,
    GpuDiagnosticsInfo,
    HardwareDiagnosticsResult,
    HwmonCapability,
    HwmonChipInfo,
    HwmonHeader,
    KernelModuleInfo,
    ThermalSafetyInfo,
    parse_hardware_diagnostics,
)
from control_ofc.services.app_state import AppState


@pytest.fixture()
def app(qapp):
    return qapp


class TestHwmonHeaderDeviceId:
    def test_device_id_field_exists(self):
        h = HwmonHeader(id="h1", device_id="nct6798.656")
        assert h.device_id == "nct6798.656"

    def test_device_id_defaults_empty(self):
        h = HwmonHeader()
        assert h.device_id == ""


class TestAmdGpuNewFields:
    def test_pci_device_id(self):
        gpu = AmdGpuCapability(pci_device_id=0x7550, pci_revision=0xC0)
        assert gpu.pci_device_id == 0x7550
        assert gpu.pci_revision == 0xC0

    def test_gpu_zero_rpm_available(self):
        gpu = AmdGpuCapability(gpu_zero_rpm_available=True)
        assert gpu.gpu_zero_rpm_available is True

    def test_defaults(self):
        gpu = AmdGpuCapability()
        assert gpu.pci_device_id is None
        assert gpu.pci_revision is None
        assert gpu.gpu_zero_rpm_available is False


class TestHardwareDiagnosticsDataclasses:
    def test_hwmon_chip_info(self):
        chip = HwmonChipInfo(
            chip_name="nct6798",
            device_id="nct6798.656",
            expected_driver="nct6775",
            in_mainline_kernel=True,
            header_count=5,
        )
        assert chip.chip_name == "nct6798"
        assert chip.header_count == 5

    def test_kernel_module_info(self):
        mod = KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True)
        assert mod.loaded is True

    def test_acpi_conflict_info(self):
        conflict = AcpiConflictInfo(
            io_range="0290-0299",
            claimed_by="ACPI OpRegion",
            conflicts_with_driver="nct6775",
        )
        assert conflict.conflicts_with_driver == "nct6775"

    def test_thermal_safety_info(self):
        ts = ThermalSafetyInfo(
            state="normal",
            cpu_sensor_found=True,
            emergency_threshold_c=105.0,
            release_threshold_c=80.0,
        )
        assert ts.emergency_threshold_c == 105.0

    def test_gpu_diagnostics_info(self):
        gpu = GpuDiagnosticsInfo(
            pci_bdf="0000:03:00.0",
            pci_device_id=0x7550,
            pci_revision=0xC0,
            model_name="9070XT",
            fan_control_method="pmfw",
            overdrive_enabled=True,
            ppfeaturemask="0xffffffff",
            ppfeaturemask_bit14_set=True,
            zero_rpm_available=True,
        )
        assert gpu.model_name == "9070XT"


class TestParseHardwareDiagnostics:
    def test_full_response(self):
        data = {
            "api_version": 1,
            "hwmon": {
                "chips_detected": [
                    {
                        "chip_name": "nct6798",
                        "device_id": "nct6798.656",
                        "expected_driver": "nct6775",
                        "in_mainline_kernel": True,
                        "header_count": 5,
                    }
                ],
                "total_headers": 5,
                "writable_headers": 3,
            },
            "gpu": {
                "pci_bdf": "0000:03:00.0",
                "pci_device_id": 30032,
                "pci_revision": 192,
                "model_name": "9070XT",
                "fan_control_method": "pmfw",
                "overdrive_enabled": True,
                "ppfeaturemask": "0xffffffff",
                "ppfeaturemask_bit14_set": True,
                "zero_rpm_available": True,
            },
            "thermal_safety": {
                "state": "normal",
                "cpu_sensor_found": True,
                "emergency_threshold_c": 105.0,
                "release_threshold_c": 80.0,
            },
            "kernel_modules": [
                {"name": "nct6775", "loaded": True, "in_mainline": True},
                {"name": "it87", "loaded": False, "in_mainline": True},
            ],
            "acpi_conflicts": [
                {
                    "io_range": "0290-0299",
                    "claimed_by": "ACPI OpRegion",
                    "conflicts_with_driver": "nct6775",
                }
            ],
        }
        result = parse_hardware_diagnostics(data)
        assert isinstance(result, HardwareDiagnosticsResult)
        assert result.hwmon.total_headers == 5
        assert result.hwmon.writable_headers == 3
        assert len(result.hwmon.chips_detected) == 1
        assert result.hwmon.chips_detected[0].chip_name == "nct6798"
        assert result.gpu is not None
        assert result.gpu.model_name == "9070XT"
        assert result.thermal_safety.state == "normal"
        assert len(result.kernel_modules) == 2
        assert result.kernel_modules[0].loaded is True
        assert len(result.acpi_conflicts) == 1

    def test_no_gpu(self):
        data = {
            "api_version": 1,
            "hwmon": {
                "chips_detected": [],
                "total_headers": 0,
                "writable_headers": 0,
            },
            "thermal_safety": {
                "state": "normal",
                "cpu_sensor_found": False,
                "emergency_threshold_c": 105.0,
                "release_threshold_c": 80.0,
            },
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.gpu is None
        assert len(result.kernel_modules) == 0

    def test_forward_compatible_ignores_unknown_fields(self):
        data = {
            "api_version": 1,
            "hwmon": {
                "chips_detected": [
                    {
                        "chip_name": "test",
                        "device_id": "test.1",
                        "expected_driver": "test",
                        "in_mainline_kernel": True,
                        "header_count": 1,
                        "future_field": "ignored",
                    }
                ],
                "total_headers": 1,
                "writable_headers": 1,
            },
            "thermal_safety": {
                "state": "normal",
                "cpu_sensor_found": True,
                "emergency_threshold_c": 105.0,
                "release_threshold_c": 80.0,
                "future_field": True,
            },
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.hwmon.chips_detected[0].chip_name == "test"


class TestDashboardHwmonBanner:
    def test_banner_shown_when_hwmon_not_present(self, app):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        state = AppState()
        page = DashboardPage(state=state)
        caps = Capabilities(hwmon=HwmonCapability(present=False))
        page._on_capabilities_updated(caps)
        assert not page._hwmon_banner.isHidden()

    def test_banner_shown_when_all_readonly(self, app):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        state = AppState()
        page = DashboardPage(state=state)
        caps = Capabilities(hwmon=HwmonCapability(present=True, write_support=False))
        page._on_capabilities_updated(caps)
        assert not page._hwmon_banner.isHidden()

    def test_banner_hidden_when_writable(self, app):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        state = AppState()
        page = DashboardPage(state=state)
        caps = Capabilities(hwmon=HwmonCapability(present=True, write_support=True))
        page._on_capabilities_updated(caps)
        assert page._hwmon_banner.isHidden()


class TestSettingsShowHardwareGuidance:
    def test_default_is_true(self):
        from control_ofc.services.app_settings_service import AppSettings

        settings = AppSettings()
        assert settings.show_hardware_guidance is True

    def test_from_dict_preserves(self):
        from control_ofc.services.app_settings_service import AppSettings

        data = {"show_hardware_guidance": False}
        settings = AppSettings.from_dict(data)
        assert settings.show_hardware_guidance is False

    def test_roundtrip(self):
        from control_ofc.services.app_settings_service import AppSettings

        settings = AppSettings(show_hardware_guidance=False)
        data = settings.to_dict()
        restored = AppSettings.from_dict(data)
        assert restored.show_hardware_guidance is False
