"""fan_zones persistence + AppState plumbing + demo seed (DEC-176).

Mirrors the established fan_aliases tests (test_app_settings.py /
test_app_state.py) — fan_zones is the portable, fan_id-keyed sibling of
fan_aliases.
"""

from __future__ import annotations

from control_ofc.services.app_settings_service import (
    MACHINE_SPECIFIC_KEYS,
    AppSettings,
    AppSettingsService,
)
from control_ofc.services.demo_service import DemoService

# ---------------------------------------------------------------------------
# AppSettings coercion / round-trip / portability
# ---------------------------------------------------------------------------


def test_fan_zones_roundtrip():
    original = AppSettings(fan_zones={"openfan:ch00": "Front Intake", "hwmon:fan1": "Exhaust"})
    restored = AppSettings.from_dict(original.to_dict())
    assert restored.fan_zones == {"openfan:ch00": "Front Intake", "hwmon:fan1": "Exhaust"}


def test_fan_zones_default_empty():
    assert AppSettings().fan_zones == {}
    assert AppSettings.from_dict({}).fan_zones == {}


def test_fan_zones_garbage_coerced():
    # non-str keys/values dropped by _as_str_dict; a non-dict becomes {}.
    s = AppSettings.from_dict({"fan_zones": {"openfan:ch00": "Intake", "bad": 5, 7: "x"}})
    assert s.fan_zones == {"openfan:ch00": "Intake"}
    assert AppSettings.from_dict({"fan_zones": "not-a-dict"}).fan_zones == {}


def test_fan_zones_portable_like_aliases():
    # DEC-176: zones travel with export (NOT machine-specific), mirroring aliases.
    assert "fan_zones" not in MACHINE_SPECIFIC_KEYS
    s = AppSettings(fan_zones={"openfan:ch00": "Intake"})
    assert s.portable_dict()["fan_zones"] == {"openfan:ch00": "Intake"}


def test_service_persist_fan_zones(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(fan_zones={"openfan:ch00": "Front Intake"})

    svc2 = AppSettingsService()
    svc2.load()
    assert svc2.settings.fan_zones == {"openfan:ch00": "Front Intake"}


# ---------------------------------------------------------------------------
# Demo seed
# ---------------------------------------------------------------------------


def test_demo_fan_zones_one_zone_per_fan():
    z = DemoService.fan_zones()
    assert z, "demo seed should be populated"
    # dict keys are unique by construction; every value is a non-empty zone name.
    assert all(isinstance(v, str) and v for v in z.values())


def test_demo_fan_zones_leaves_gpu_unassigned():
    # GPU fans are intentionally unzoned so demo exercises the fallback path too.
    z = DemoService.fan_zones()
    assert not any(k.startswith(("amd_gpu:", "intel_gpu:")) for k in z)


def test_demo_fan_zones_ids_are_real_demo_fans():
    valid = {f.id for f in DemoService().fans()}
    assert set(DemoService.fan_zones()) <= valid


# ---------------------------------------------------------------------------
# MainWindow wiring (persist on change)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# DEC-222: the Dashboard's Fan Zone UI was removed. The zone MODEL and its two
# settings keys are retained (dormant, no schema migration) and stay covered
# above; the two tests that drove them through the deleted FanZoneGrid — card
# drag-order persistence and the collapsible section's state — had no surviving
# subject and were removed with it.
# ---------------------------------------------------------------------------
