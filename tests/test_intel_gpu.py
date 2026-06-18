"""DEC-121: Intel discrete GPU (Arc) support — models, parsing, display, and
read-only safety.

Covers: IntelGpuCapability parsing (present/absent/forward-compat/pci coalesce),
IntelGpuDiagnosticsInfo parsing, fan displayability + dedup, fan display name,
sensor classification, diagnostics control-method, demo mode, the fan wizard
exclusion, the dashboard GPU card title, and the control-loop write guard that
guarantees an Intel GPU fan is never written (read-only, firmware-managed).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import (
    Capabilities,
    FanReading,
    IntelGpuCapability,
    parse_capabilities,
    parse_hardware_diagnostics,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_service import DemoService
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.pages.diagnostics_page import _fan_control_method
from control_ofc.ui.sensor_knowledge import classify_sensor


def _make_intel_caps(
    *,
    present: bool = True,
    display_label: str = "Arc B580",
    fan_control_method: str = "read_only",
) -> Capabilities:
    return Capabilities(
        daemon_version="1.11.0",
        intel_gpu=IntelGpuCapability(
            present=present,
            model_name="Intel Arc B580" if present else None,
            display_label=display_label if present else "Intel D-GPU",
            pci_id="0000:03:00.0" if present else None,
            pci_device_id=0xE20B if present else None,
            driver="xe" if present else None,
            fan_control_method=fan_control_method,
            fan_rpm_available=present,
            is_discrete=present,
        ),
    )


# ---------------------------------------------------------------------------
# Capability model + parsing
# ---------------------------------------------------------------------------


class TestIntelGpuCapabilityModel:
    def test_default_not_present(self):
        cap = IntelGpuCapability()
        assert not cap.present
        assert cap.display_label == "Intel D-GPU"
        assert cap.fan_control_method == "none"
        # Read-only contract: there is no fan_write_supported field at all.
        assert not hasattr(cap, "fan_write_supported")

    def test_full_capability(self):
        cap = IntelGpuCapability(
            present=True,
            model_name="Intel Arc B580",
            display_label="Arc B580",
            pci_id="0000:03:00.0",
            pci_device_id=0xE20B,
            driver="xe",
            fan_control_method="read_only",
            fan_rpm_available=True,
            is_discrete=True,
        )
        assert cap.present
        assert cap.driver == "xe"
        assert cap.fan_control_method == "read_only"


class TestCapabilitiesParsing:
    def _payload(self, intel: dict | None) -> dict:
        devices: dict = {"openfan": {"present": False}, "hwmon": {"present": True}}
        if intel is not None:
            devices["intel_gpu"] = intel
        return {"api_version": 1, "daemon_version": "1.11.0", "devices": devices}

    def test_parse_with_intel_gpu(self):
        caps = parse_capabilities(
            self._payload(
                {
                    "present": True,
                    "model_name": "Intel Arc B580",
                    "display_label": "Arc B580",
                    "pci_bdf": "0000:03:00.0",
                    "pci_device_id": 0xE20B,
                    "driver": "xe",
                    "fan_control_method": "read_only",
                    "fan_rpm_available": True,
                    "is_discrete": True,
                }
            )
        )
        assert caps.intel_gpu.present
        assert caps.intel_gpu.display_label == "Arc B580"
        assert caps.intel_gpu.driver == "xe"
        # pci_bdf on the wire is coalesced into pci_id (like amd_gpu).
        assert caps.intel_gpu.pci_id == "0000:03:00.0"

    def test_parse_without_intel_gpu_field(self):
        """Older daemons omit intel_gpu entirely → present defaults False."""
        caps = parse_capabilities(self._payload(None))
        assert not caps.intel_gpu.present
        assert caps.intel_gpu.display_label == "Intel D-GPU"

    def test_parse_with_unknown_intel_fields(self):
        """Forward compat: unknown fields are dropped, not fatal."""
        caps = parse_capabilities(
            self._payload({"present": True, "display_label": "Arc B580", "future_field": "ignored"})
        )
        assert caps.intel_gpu.present
        assert caps.intel_gpu.display_label == "Arc B580"


class TestDiagnosticsParsing:
    def test_parse_intel_gpu_diagnostics(self):
        data = {
            "api_version": 1,
            "hwmon": {},
            "intel_gpu": {
                "pci_bdf": "0000:03:00.0",
                "pci_device_id": 0xE20B,
                "pci_revision": 0,
                "model_name": "Intel Arc B580",
                "driver": "xe",
                "fan_control_method": "read_only",
                "fan_rpm_available": True,
                "fan_control_note": "firmware-managed",
            },
        }
        result = parse_hardware_diagnostics(data)
        assert result.intel_gpu is not None
        assert result.intel_gpu.driver == "xe"
        assert result.intel_gpu.fan_control_method == "read_only"
        assert "firmware" in result.intel_gpu.fan_control_note

    def test_parse_without_intel_gpu_diagnostics(self):
        result = parse_hardware_diagnostics({"api_version": 1, "hwmon": {}})
        assert result.intel_gpu is None


# ---------------------------------------------------------------------------
# Display: always-show, dedup, name
# ---------------------------------------------------------------------------


class TestFanDisplay:
    def test_intel_gpu_fan_always_displayable_at_zero_rpm(self):
        fans = [FanReading(id="intel_gpu:0000:03:00.0", source="intel_gpu", rpm=0)]
        out = filter_displayable_fans(fans, aliases={}, hide_unused=True)
        assert len(out) == 1

    def test_intel_gpu_fan_dedups_hwmon_shadow(self):
        fans = [
            FanReading(id="intel_gpu:0000:03:00.0", source="intel_gpu", rpm=1500),
            FanReading(id="hwmon:xe:0000:03:00.0:fan1", source="hwmon", rpm=1500),
        ]
        out = filter_displayable_fans(fans, aliases={}, hide_unused=False)
        ids = {f.id for f in out}
        assert "intel_gpu:0000:03:00.0" in ids
        assert "hwmon:xe:0000:03:00.0:fan1" not in ids


class TestFanDisplayName:
    def test_name_uses_capability_label(self):
        state = AppState()
        state.set_capabilities(_make_intel_caps())
        assert state.fan_display_name("intel_gpu:0000:03:00.0") == "Arc B580 Fan"

    def test_name_fallback_without_capability(self):
        state = AppState()
        assert state.fan_display_name("intel_gpu:0000:03:00.0") == "Intel D-GPU Fan"


# ---------------------------------------------------------------------------
# Read-only safety
# ---------------------------------------------------------------------------


class TestControlLoopNeverWritesIntel:
    def test_diagnostics_fan_control_method_is_read_only(self):
        state = AppState()
        state.set_capabilities(_make_intel_caps())
        fan = FanReading(id="intel_gpu:0000:03:00.0", source="intel_gpu", rpm=1500)
        assert _fan_control_method(fan, state) == "read-only"

    def test_diagnostics_fan_control_method_read_only_without_caps(self):
        # Source alone is authoritative — read-only even if caps absent.
        fan = FanReading(id="intel_gpu:0000:03:00.0", source="intel_gpu", rpm=1500)
        assert _fan_control_method(fan, None) == "read-only"


# ---------------------------------------------------------------------------
# Sensor classification (xe/i915)
# ---------------------------------------------------------------------------


class TestSensorClassification:
    def test_xe_temp2_is_package(self):
        c = classify_sensor("xe", "temp2")
        assert c.source_class == "gpu_package"
        assert "package" in c.display_description.lower()

    def test_xe_temp3_is_vram(self):
        c = classify_sensor("xe", "temp3")
        assert c.source_class == "gpu_memory"

    def test_xe_high_index_is_vram_channel(self):
        c = classify_sensor("xe", "temp9")
        assert c.source_class == "gpu_memory"

    def test_i915_temp1_is_package(self):
        c = classify_sensor("i915", "temp1")
        assert c.source_class == "gpu_package"

    def test_unknown_intel_label_is_generic_gpu(self):
        c = classify_sensor("i915", "weird")
        assert "Intel GPU" in c.display_description


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------


class TestDemoMode:
    def test_demo_capabilities_includes_intel_gpu(self):
        caps = DemoService().capabilities()
        assert caps.intel_gpu.present
        assert caps.intel_gpu.fan_control_method == "read_only"

    def test_demo_intel_fan_is_read_only(self):
        fans = DemoService().fans()
        intel = [f for f in fans if f.source == "intel_gpu"]
        assert len(intel) == 1
        # Read-only: the daemon never commands it, so no last_commanded_pwm.
        assert intel[0].last_commanded_pwm is None

    def test_demo_sensors_include_intel_gpu(self):
        sensors = DemoService().sensors()
        intel = [s for s in sensors if s.source == "intel_gpu"]
        assert len(intel) >= 1
        assert all(s.chip_name == "xe" for s in intel)

    def test_demo_diagnostics_include_intel_gpu(self):
        diag = DemoService().hardware_diagnostics()
        assert diag.intel_gpu is not None
        assert diag.intel_gpu.fan_control_method == "read_only"


# ---------------------------------------------------------------------------
# Fan wizard excludes read-only Intel fans
# ---------------------------------------------------------------------------


class TestFanWizardExcludesIntel:
    def test_build_targets_skips_intel_gpu(self, qtbot):
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        state = AppState()
        state.fans = [
            FanReading(id="openfan:ch00", source="openfan", rpm=1200),
            FanReading(id="intel_gpu:0000:03:00.0", source="intel_gpu", rpm=1500),
        ]
        wizard = FanConfigWizard(state=state, client=MagicMock())
        qtbot.addWidget(wizard)
        target_ids = {t["id"] for t in wizard._targets}
        assert "openfan:ch00" in target_ids
        assert "intel_gpu:0000:03:00.0" not in target_ids


# ---------------------------------------------------------------------------
# Dashboard GPU card
# ---------------------------------------------------------------------------


class TestDashboardGpuCard:
    def test_card_title_uses_intel_when_no_amd(self, qtbot, app_state, profile_service):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        app_state.set_capabilities(_make_intel_caps())
        assert page._gpu_card._title_label.text() == "Arc B580 Temp"
