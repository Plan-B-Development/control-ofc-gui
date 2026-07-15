"""DEC-210: Logs page Qt-free view-model + filters.

The pure builders (`build_log_row` / `build_log_rows` / `filter_log_rows`) need no
`QApplication`. (The widget↔VM filter-parity guard against the retired
Diagnostics ▸ Event Log proxy was dropped in DEC-216 together with the
``EventLogView`` widget; the VM's own filter tests below stand on their own.)
"""

from __future__ import annotations

import time

import pytest

from control_ofc.services.diagnostics_service import DiagEvent
from control_ofc.services.logs_view import (
    LogRowVM,
    build_log_row,
    build_log_rows,
    filter_log_rows,
)

_BASE = 1_700_000_000.0


def _events() -> list[DiagEvent]:
    raw = [
        ("info", "gui", "GUI started v2.16.0"),
        ("info", "profile", "Active profile: Balanced"),
        ("info", "polling", "Daemon active profile: Balanced, detecting sensors"),
        ("warning", "sensor_i2c", "Timeout reading from i2c-piix4 at 0x0b00"),
        ("error", "gpu_ctrl", "Failed to apply custom curve to AMD GPU (pmfw missing)"),
        ("info", "polling", "Fallback to driver auto control for GPU fan"),
        ("warning", "thermal", "CPU fan ramp requested"),
    ]
    return [
        DiagEvent(timestamp=_BASE + i, level=lvl, source=src, message=msg)
        for i, (lvl, src, msg) in enumerate(raw)
    ]


# ─── Pure builder tests (no QApplication) ──────────────────────────────────


@pytest.mark.parametrize(
    "level,label,state",
    [
        ("info", "INFO", "ok"),
        ("warning", "WARN", "warn"),
        ("error", "ERR", "crit"),
    ],
)
def test_level_label_and_state_maps(level, label, state):
    ev = DiagEvent(timestamp=_BASE, level=level, source="polling", message="hi")
    vm = build_log_row(ev)
    assert vm.level == level
    assert vm.level_label == label
    assert vm.level_state == state


def test_unknown_level_defaults_defensively():
    vm = build_log_row(DiagEvent(timestamp=_BASE, level="trace", source="x", message="y"))
    assert vm.level_label == "TRACE"
    assert vm.level_state == "neutral"


def test_time_strings_match_event_and_format():
    ev = DiagEvent(timestamp=_BASE + 123, level="info", source="gui", message="m")
    vm = build_log_row(ev)
    # Table Time column is byte-for-byte the DiagEvent's own time_str.
    assert vm.time_str == ev.time_str
    # Inspector timestamp is the fuller local datetime (real, not fabricated).
    assert vm.detail_time_str == time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.timestamp))
    assert vm.timestamp == ev.timestamp


def test_build_log_rows_preserves_order_and_fields():
    rows = build_log_rows(_events())
    assert len(rows) == 7
    assert [r.source for r in rows] == [
        "gui",
        "profile",
        "polling",
        "sensor_i2c",
        "gpu_ctrl",
        "polling",
        "thermal",
    ]
    assert all(isinstance(r, LogRowVM) for r in rows)


# ─── Filter tests ──────────────────────────────────────────────────────────


def test_empty_levels_yields_no_rows():
    rows = build_log_rows(_events())
    assert filter_log_rows(rows, levels=set()) == []


def test_source_exact_match_and_nonmatch():
    rows = build_log_rows(_events())
    all_levels = {"info", "warning", "error"}
    polling = filter_log_rows(rows, levels=all_levels, source="polling")
    assert {r.source for r in polling} == {"polling"}
    assert len(polling) == 2
    assert filter_log_rows(rows, levels=all_levels, source="does-not-exist") == []


def test_search_is_case_insensitive_over_message_and_source():
    rows = build_log_rows(_events())
    all_levels = {"info", "warning", "error"}
    # Matches a message substring regardless of case.
    assert len(filter_log_rows(rows, levels=all_levels, search="TIMEOUT")) == 1
    # Matches over the source field too (search box is the only source filter).
    by_source = filter_log_rows(rows, levels=all_levels, search="gpu_ctrl")
    assert len(by_source) == 1
    assert by_source[0].source == "gpu_ctrl"
