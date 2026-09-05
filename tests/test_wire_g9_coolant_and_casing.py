"""G9 — coolant, and the dual-casing tax that hid it (`WIRE-c`, `WIRE-ai`).

The measured symptom was an Overview line reading `Sensors: 10 total` with no
breakdown at all in demo mode, against `Sensors: 5 total · 1 CPU · 1 board ·
1 GPU · 1 disk` on daemon data. Two causes, and only one of them is the missing
`coolant_temp` branch: `DemoService` emitted PascalCase kinds the daemon never
sends, six modules grew a dual-casing tax to compensate, and the seventh — this
summary, written against the wire contract — matched nothing.

Fixed at the source. These tests pin both halves, and the casing half is
asserted *against the daemon's own contract* rather than against a list of
today's spellings.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import SensorReading
from control_ofc.services.demo_service import DemoService
from control_ofc.services.overview_view import build_sensor_summary
from control_ofc.services.series_selection import default_series_keys

#: The `kind` vocabulary `docs/08` § GET /sensors defines. Written out here so
#: the assertions below compare against the CONTRACT, not against whatever the
#: demo service happens to emit — which is the failure this package is about.
WIRE_SENSOR_KINDS = frozenset({"cpu_temp", "mb_temp", "disk_temp", "gpu_temp", "coolant_temp"})


def _summary(sensors: list[SensorReading]) -> str:
    return build_sensor_summary(sensors, hidden_count=0, unavailable_count=0, board_vendor="")


# ── WIRE-c: the root cause ───────────────────────────────────────────────────


def test_demo_sensor_kinds_are_all_wire_tokens() -> None:
    kinds = {s.kind for s in DemoService().sensors()}
    assert kinds <= WIRE_SENSOR_KINDS, (
        f"not on the wire contract: {sorted(kinds - WIRE_SENSOR_KINDS)}"
    )


def test_demo_mode_gets_a_full_sensor_breakdown() -> None:
    """The measured defect, on the production path.

    Every kind the demo seed contains must appear in the summary — asserted as a
    relationship against the seed, so adding a sensor kind to demo mode without
    a summary branch fails here rather than silently vanishing from the line.
    """
    sensors = DemoService().sensors()
    text = _summary(sensors)
    assert "Sensors: " in text
    for kind, word in (
        ("cpu_temp", "CPU"),
        ("mb_temp", "board"),
        ("gpu_temp", "GPU"),
        ("disk_temp", "disk"),
        ("coolant_temp", "liquid"),
    ):
        if any(s.kind == kind for s in sensors):
            assert word in text, f"{kind} present in demo mode but missing from: {text}"


def test_the_coolant_branch_counts(qapp=None) -> None:
    sensors = [
        SensorReading(id="a", kind="coolant_temp", value_c=32.0),
        SensorReading(id="b", kind="coolant_temp", value_c=33.0),
        SensorReading(id="c", kind="cpu_temp", value_c=50.0),
    ]
    assert "2 liquid" in _summary(sensors)
    assert "1 CPU" in _summary(sensors)


def test_no_coolant_adds_no_liquid_segment() -> None:
    """The opposite branch — an unconditional segment would pass the test above
    and put "0 liquid" on every air-cooled machine."""
    assert "liquid" not in _summary([SensorReading(id="c", kind="cpu_temp", value_c=50.0)])


def test_the_dual_casing_tax_is_gone_from_production_source() -> None:
    """A compensation left in one module is a rule the next author will copy.

    Scans production source for the PascalCase spellings. This is a source scan,
    so it is written to match a *string literal* rather than any occurrence —
    prose that names the retired spelling (this docstring included, were it in
    `src/`) must not trip it.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "control_ofc"
    retired = ["Cpu" + "Temp", "Gpu" + "Temp", "Mb" + "Temp", "Disk" + "Temp", "Coolant" + "Temp"]
    offenders = []
    for path in sorted(src.rglob("*.py")):
        text = path.read_text()
        for token in retired:
            if f'"{token}"' in text or f"'{token}'" in text:
                offenders.append(f"{path.name}:{token}")
    assert not offenders, f"PascalCase sensor kinds are back: {offenders}"


# ── WIRE-ai: coolant in the default chart ────────────────────────────────────


def _s(sid: str, kind: str) -> SensorReading:
    return SensorReading(id=sid, kind=kind, value_c=40.0, age_ms=100)


def test_a_liquid_cooled_machine_charts_its_coolant_by_default() -> None:
    keys = default_series_keys(
        [_s("c", "cpu_temp"), _s("g", "gpu_temp"), _s("m", "mb_temp"), _s("l", "coolant_temp")]
    )
    assert "sensor:l" in keys


def test_an_air_cooled_machine_is_unchanged() -> None:
    keys = default_series_keys([_s("c", "cpu_temp"), _s("g", "gpu_temp"), _s("m", "mb_temp")])
    assert keys == {"sensor:c", "sensor:g", "sensor:m"}


def test_demo_mode_charts_its_coolant() -> None:
    """Demo mode has a Kraken; before `WIRE-c` its kinds did not match, so this
    could not have passed even with the coolant slot present."""
    sensors = DemoService().sensors()
    coolant = [s for s in sensors if s.kind == "coolant_temp"]
    assert coolant, "the demo seed must contain a coolant sensor for this to mean anything"
    assert f"sensor:{coolant[0].id}" in default_series_keys(sensors)


@pytest.mark.parametrize("kind", ["cpu_temp", "gpu_temp", "mb_temp", "coolant_temp"])
def test_each_default_slot_is_independently_droppable(kind: str) -> None:
    """Each slot resolves on its own, so a machine missing any one category gets
    the rest rather than an empty chart."""
    assert default_series_keys([_s("x", kind)]) == {"sensor:x"}
