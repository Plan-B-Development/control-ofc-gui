"""Tests for the demo service.

DemoService is a deterministic fixture — exact counts document the contract.
Structural invariants validate shape independent of specific count.
"""

from __future__ import annotations

from control_ofc.services.demo_service import DemoService


def test_demo_capabilities():
    demo = DemoService()
    caps = demo.capabilities()
    assert caps.openfan.present is True
    assert caps.openfan.channels == 8
    assert caps.hwmon.present is True
    assert caps.amd_gpu.present is True
    assert caps.amd_gpu.display_label == "RX 7900 XTX"


def test_demo_sensors_returns_readings():
    demo = DemoService()
    sensors = demo.sensors()
    assert len(sensors) >= 4
    kinds = {s.kind for s in sensors}
    assert "CpuTemp" in kinds
    assert "GpuTemp" in kinds
    assert all(hasattr(s, "value_c") for s in sensors)
    assert all(s.id for s in sensors)


def test_demo_fans_returns_readings():
    demo = DemoService()
    fans = demo.fans()
    assert len(fans) >= 8
    assert all(f.rpm is not None for f in fans)
    assert all(f.id for f in fans)
    assert all(f.source for f in fans)
    sources = {f.source for f in fans}
    assert "openfan" in sources
    assert "amd_gpu" in sources


def test_demo_set_fan_pwm():
    demo = DemoService()
    demo.set_fan_pwm("openfan:ch00", 80)
    fans = demo.fans()
    ch0 = next(f for f in fans if f.id == "openfan:ch00")
    assert ch0.last_commanded_pwm == 80


def test_demo_hwmon_headers():
    demo = DemoService()
    headers = demo.hwmon_headers()
    assert len(headers) >= 1
    assert all(h.id for h in headers)
    # Daemon sets min_pwm_percent=0 for all headers — safety floors are
    # GUI-side profile constraints, not per-header hardware limits (M10).
    assert headers[0].min_pwm_percent == 0


def test_demo_status_healthy():
    demo = DemoService()
    status = demo.status()
    assert status.overall_status == "healthy"


def test_demo_fan_aliases():
    aliases = DemoService.fan_aliases()
    assert aliases["openfan:ch00"] == "Front Intake 1"


def test_demo_fan_aliases_complete():
    """Every demo fan must have an alias."""
    demo = DemoService()
    fans = demo.fans()
    aliases = DemoService.fan_aliases()
    assert len(aliases) >= len(fans)
    for fan in fans:
        assert fan.id in aliases, f"Fan {fan.id} missing alias"


def test_demo_hardware_diagnostics_shape():
    """hardware_diagnostics() returns a populated fixture used by the
    screenshot tooling. The GUI's _populate_hw_diagnostics expects every
    nested field to be present — a placeholder/empty result would defeat
    the purpose of capturing a live-looking Hardware Readiness card.
    """
    demo = DemoService()
    diag = demo.hardware_diagnostics()
    assert diag.api_version == 1
    # Board info populated so the auto-shown vendor quirk panel fires.
    assert diag.board.vendor == "Gigabyte"
    assert "X870E AORUS MASTER" in diag.board.name
    # At least one chip detected, with a driver name and header count.
    assert len(diag.hwmon.chips_detected) >= 1
    chip = diag.hwmon.chips_detected[0]
    assert chip.chip_name
    assert chip.expected_driver
    assert chip.header_count >= 1
    # Total/writable header counts should reflect the chip header_count.
    assert diag.hwmon.total_headers >= 1
    assert diag.hwmon.writable_headers <= diag.hwmon.total_headers
    # Kernel modules populated with at least it87 + k10temp + amdgpu.
    module_names = {m.name for m in diag.kernel_modules}
    assert {"it87", "k10temp", "amdgpu"} <= module_names
    # Thermal safety reports a CPU sensor was found (so the safety panel
    # does not render a critical state in the screenshot).
    assert diag.thermal_safety.cpu_sensor_found is True
    # GPU diagnostics populated — RDNA3 PMFW path.
    assert diag.gpu is not None
    assert diag.gpu.fan_control_method == "pmfw"
    assert diag.gpu.ppfeaturemask_bit14_set is True


def test_demo_hardware_diagnostics_revert_counts_match_writable_headers():
    """Per-header revert counts should reference the writable header IDs so
    the Diagnostics revert-count panel renders rows for the same fans the
    user can actually control."""
    demo = DemoService()
    diag = demo.hardware_diagnostics()
    headers = demo.hwmon_headers()
    writable_ids = {h.id for h in headers}
    for header_id in diag.hwmon.enable_revert_counts:
        assert header_id in writable_ids, (
            f"revert-count header {header_id} not in writable demo headers"
        )
