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


def test_demo_sensors_returns_readings():
    demo = DemoService()
    sensors = demo.sensors()
    assert len(sensors) == 6  # deterministic contract
    # Structural invariants
    assert len(sensors) > 0
    kinds = {s.kind for s in sensors}
    assert "CpuTemp" in kinds
    assert "GpuTemp" in kinds
    assert all(hasattr(s, "value_c") for s in sensors)
    assert all(s.id for s in sensors)  # no empty IDs


def test_demo_fans_returns_readings():
    demo = DemoService()
    fans = demo.fans()
    assert len(fans) == 10  # deterministic contract
    # Structural invariants
    assert len(fans) > 0
    assert all(f.rpm is not None for f in fans)
    assert all(f.id for f in fans)
    assert all(f.source for f in fans)
    sources = {f.source for f in fans}
    assert "openfan" in sources


def test_demo_set_fan_pwm():
    demo = DemoService()
    demo.set_fan_pwm("openfan:ch00", 80)
    fans = demo.fans()
    ch0 = next(f for f in fans if f.id == "openfan:ch00")
    assert ch0.last_commanded_pwm == 80


def test_demo_hwmon_headers():
    demo = DemoService()
    headers = demo.hwmon_headers()
    assert len(headers) == 2  # deterministic contract
    assert len(headers) > 0
    assert all(h.id for h in headers)
    assert headers[0].min_pwm_percent == 30


def test_demo_status_healthy():
    demo = DemoService()
    status = demo.status()
    assert status.overall_status == "healthy"


def test_demo_fan_aliases():
    aliases = DemoService.fan_aliases()
    assert aliases["openfan:ch00"] == "Front Intake 1"
    assert len(aliases) == 10  # deterministic contract


def test_demo_fan_aliases_complete():
    """Every demo fan must have an alias."""
    demo = DemoService()
    fans = demo.fans()
    aliases = DemoService.fan_aliases()
    assert len(aliases) >= len(fans)
    for fan in fans:
        assert fan.id in aliases, f"Fan {fan.id} missing alias"
