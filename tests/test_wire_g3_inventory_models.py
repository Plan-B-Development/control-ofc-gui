"""G3 — the ``/inventory/hwmon`` model surface (``WIRE-h``/``i``/``t``/``aa``).

Four register rows, one module. Each test below names the row it pins and, where
the row is about a *default* or a *silently dropped field*, asserts a
relationship against the class that is supposed to be mirrored rather than a
literal — a literal is satisfied by the very drift these rows describe.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields

import pytest

from control_ofc.api.models import (
    HwmonHeader,
    InventoryPwmControl,
    InventoryTempSensor,
    SensorReading,
    parse_capabilities,
    parse_hwmon_inventory,
)
from control_ofc.services.demo_service import DemoService

# ── WIRE-h ───────────────────────────────────────────────────────────────────


def test_inventory_temp_sensor_models_every_sensor_reading_field() -> None:
    """The daemon flattens ``SensorEntry`` into this struct, so the GUI class
    must carry every ``SensorReading`` field — asserted as a relationship, not a
    list of six names, so a future addition to ``SensorReading`` is covered too.
    """
    base = {f.name for f in dataclass_fields(SensorReading)}
    inv = {f.name for f in dataclass_fields(InventoryTempSensor)}
    assert base <= inv, f"InventoryTempSensor drops {sorted(base - inv)}"
    assert {"classification", "confidence", "rationale"} <= inv


def test_inventory_temp_sensor_parses_nested_thresholds() -> None:
    """Thresholds are the field the old hand-rolled parse lost.

    A raw dict in a slot typed ``SensorThresholds | None`` is not a fix, so
    assert the parsed *type* and a value, never merely that the key survived.
    """
    inv = parse_hwmon_inventory(
        {
            "temp_sensors": [
                {
                    "id": "hwmon:k10temp:pci0:Tctl",
                    "kind": "cpu_temp",
                    "value_c": 48.0,
                    "age_ms": 120,
                    "temp_type": 5,
                    "rate_c_per_s": 0.25,
                    "session_min_c": 31.0,
                    "session_max_c": 91.5,
                    "thresholds": {"crit_c": 95.0},
                    "classification": "cpu_tctl",
                    "confidence": "high",
                    "rationale": "k10temp Tctl",
                }
            ]
        }
    )
    (s,) = inv.temp_sensors
    assert s.thresholds is not None
    assert s.thresholds.crit_c == 95.0
    assert s.temp_type == 5
    assert s.age_ms == 120
    assert s.rate_c_per_s == 0.25
    assert (s.session_min_c, s.session_max_c) == (31.0, 91.5)
    assert s.classification == "cpu_tctl"


def test_inventory_pwm_control_carries_the_dec316_safety_fields() -> None:
    """``effective_min_pwm_pct`` and ``stop_permitted`` are a floor and a stop
    permission; the old copy dropped both. Asserted against ``HwmonHeader`` so
    the check cannot pass by re-listing today's field names.
    """
    header = {f.name for f in dataclass_fields(HwmonHeader)}
    control = {f.name for f in dataclass_fields(InventoryPwmControl)}
    assert control == header, f"diverged: {sorted(header ^ control)}"
    assert {"effective_min_pwm_pct", "stop_permitted"} <= control

    inv = parse_hwmon_inventory(
        {
            "pwm_controls": [
                {
                    "id": "hwmon:it8696:pci0:pwm3:AIO_PUMP",
                    "is_writable": True,
                    "effective_min_pwm_pct": 30,
                    "stop_permitted": False,
                    "cooling_device_id": "dev-1",
                }
            ]
        }
    )
    (c,) = inv.pwm_controls
    assert c.effective_min_pwm_pct == 30
    assert c.stop_permitted is False
    assert c.cooling_device_id == "dev-1"


# ── WIRE-i ───────────────────────────────────────────────────────────────────


def test_inventory_pwm_control_is_the_same_type_as_a_header() -> None:
    """One wire struct, one class. The stale "mirrors field-for-field" docstring
    is gone because there is no second class left to make the claim about.
    """
    assert InventoryPwmControl is HwmonHeader


# ── WIRE-t ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cls", [HwmonHeader, InventoryPwmControl])
def test_is_writable_defaults_to_the_safe_direction(cls: type) -> None:
    """A truncated response must not make a read-only header look controllable."""
    assert cls().is_writable is False


def test_demo_headers_declare_themselves_writable() -> None:
    """The default flip is only safe because the demo seeds say so explicitly.

    Measured before the flip: the entire suite passed with the default inverted,
    so nothing pinned it — and demo mode would have offered no hwmon headers at
    all in the Controls member-picker. This is that missing guard.
    """
    headers = DemoService().hwmon_headers()
    assert headers, "demo mode must publish hwmon headers"
    assert all(h.is_writable for h in headers)


# ── WIRE-aa ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["devices", "control", "features"])
def test_capabilities_survives_an_explicit_json_null(key: str) -> None:
    """``data.get(k, {})`` returns None for an explicit null; the parse then died
    on ``.items()``. The capabilities parse is the startup gate for every control
    feature, so this must degrade to defaults rather than raise.
    """
    caps = parse_capabilities({key: None})
    assert caps is not None
    # And the normal path still populates, so the guard did not flatten it.
    populated = parse_capabilities({"control": {"autonomous_control": True}})
    assert populated.control.autonomous_control is True
