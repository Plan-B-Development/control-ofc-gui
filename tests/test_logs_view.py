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
    REPEAT_MARK,
    LogRowVM,
    bucket_window,
    build_log_row,
    build_log_rows,
    collapse_repeats,
    filter_log_rows,
    format_event_with_context,
    format_raw_record,
    format_row_line,
    histogram_buckets,
    index_for_window,
    level_counts,
    level_state,
    newest_first,
    related_rows,
    source_names,
    time_span,
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


# ─── DEC-314: collapsing, windows, facets, buckets, correlation ────────────


def _ev(i: int, level: str, source: str, message: str, **fields) -> DiagEvent:
    return DiagEvent(
        timestamp=_BASE + i,
        level=level,
        source=source,
        message=message,
        seq=i + 1,
        fields=dict(fields),
    )


def test_collapse_merges_a_consecutive_run_and_keeps_the_first_identity():
    """The surviving row must be anchored on the run's FIRST event: that is what
    keeps a selection put while the run grows under the user's cursor."""
    rows = build_log_rows([_ev(i, "warning", "fan", "stall") for i in range(3)])
    collapsed = collapse_repeats(rows)

    assert len(collapsed) == 1
    assert collapsed[0].repeat_count == 3
    assert collapsed[0].event_id == rows[0].event_id
    assert collapsed[0].first_timestamp == rows[0].timestamp
    assert collapsed[0].timestamp == rows[-1].timestamp, "and it carries the latest time"


def test_collapse_does_not_merge_across_an_intervening_event():
    """Brief §6: "do not collapse unrelated events merely because their text is
    similar" — equivalence is *consecutive*, not global."""
    rows = build_log_rows(
        [
            _ev(0, "warning", "fan", "stall"),
            _ev(1, "info", "gui", "something else"),
            _ev(2, "warning", "fan", "stall"),
        ]
    )
    collapsed = collapse_repeats(rows)
    assert [r.repeat_count for r in collapsed] == [1, 1, 1]


def test_collapse_distinguishes_level_and_source():
    rows = build_log_rows(
        [
            _ev(0, "warning", "fan", "same text"),
            _ev(1, "error", "fan", "same text"),
            _ev(2, "error", "hwmon", "same text"),
        ]
    )
    assert len(collapse_repeats(rows)) == 3


def test_collapse_keeps_the_first_events_fields():
    rows = build_log_rows(
        [
            _ev(0, "warning", "fan", "stall", component="cpu_fan"),
            _ev(1, "warning", "fan", "stall", component="cpu_fan"),
        ]
    )
    assert collapse_repeats(rows)[0].fields == (("component", "cpu_fan"),)


def test_newest_first_is_the_only_ordering_flip():
    rows = build_log_rows([_ev(i, "info", "gui", f"m{i}") for i in range(3)])
    assert [r.message for r in newest_first(rows)] == ["m2", "m1", "m0"]
    assert [r.message for r in rows] == ["m0", "m1", "m2"], "the input is not mutated"


def test_a_time_window_is_half_open():
    rows = build_log_rows([_ev(i, "info", "gui", f"m{i}") for i in range(4)])
    kept = filter_log_rows(rows, levels={"info"}, window=(_BASE + 1, _BASE + 3))
    assert [r.message for r in kept] == ["m1", "m2"], "start inclusive, end exclusive"


def test_level_counts_cover_every_severity_even_at_zero():
    rows = build_log_rows([_ev(0, "info", "gui", "a"), _ev(1, "error", "gui", "b")])
    assert level_counts(rows) == {"info": 1, "warning": 0, "error": 1}


def test_level_counts_count_rows_not_events():
    """The chips describe what checking them will show, so a collapsed run counts
    once — the row's repeat marker is what explains the difference to the reader."""
    rows = collapse_repeats(build_log_rows([_ev(i, "info", "gui", "same") for i in range(5)]))
    assert level_counts(rows)["info"] == 1


def test_source_names_are_sorted_distinct_and_drop_blanks():
    rows = build_log_rows(
        [_ev(0, "info", "polling", "a"), _ev(1, "info", "gui", "b"), _ev(2, "info", "", "c")]
    )
    assert source_names(rows) == ["gui", "polling"]


def test_time_span_is_none_without_rows():
    assert time_span([]) is None
    rows = build_log_rows([_ev(0, "info", "gui", "a"), _ev(5, "info", "gui", "b")])
    assert time_span(rows) == (_BASE, _BASE + 5)


def test_histogram_places_each_event_in_its_own_slice():
    rows = build_log_rows([_ev(i * 10, "info", "gui", f"m{i}") for i in range(4)])
    buckets = histogram_buckets(rows, span=time_span(rows), bucket_count=4)
    assert [b.total for b in buckets] == [1, 1, 1, 1]
    assert sum(b.total for b in buckets) == 4


def test_histogram_separates_severities_within_a_bucket():
    rows = build_log_rows(
        [_ev(0, "info", "gui", "a"), _ev(0, "error", "gui", "b"), _ev(1, "info", "gui", "c")]
    )
    buckets = histogram_buckets(rows, span=time_span(rows), bucket_count=1)
    assert buckets[0].counts == {"info": 2, "warning": 0, "error": 1}
    assert buckets[0].total == 3


def test_histogram_survives_a_zero_width_span():
    """Several events inside one clock tick must not divide by zero."""
    rows = build_log_rows([_ev(0, "info", "gui", f"m{i}") for i in range(3)])
    buckets = histogram_buckets(rows, span=(_BASE, _BASE), bucket_count=4)
    assert sum(b.total for b in buckets) == 3


@pytest.mark.parametrize("span,count", [(None, 4), ((0.0, 1.0), 0), ((0.0, 1.0), -1)])
def test_histogram_returns_nothing_without_a_usable_request(span, count):
    assert histogram_buckets([], span=span, bucket_count=count) == []


def test_the_last_bucket_window_includes_the_newest_event():
    """The newest event sits exactly on span[1] and a half-open range would drop it —
    selecting the last column must not filter out the event that defined it."""
    rows = build_log_rows([_ev(i, "info", "gui", f"m{i}") for i in range(4)])
    buckets = histogram_buckets(rows, span=time_span(rows), bucket_count=4)
    window = bucket_window(buckets, 3)
    kept = filter_log_rows(rows, levels={"info"}, window=window)
    assert [r.message for r in kept] == ["m3"]


def test_bucket_window_rejects_an_out_of_range_index():
    rows = build_log_rows([_ev(0, "info", "gui", "a")])
    buckets = histogram_buckets(rows, span=time_span(rows), bucket_count=2)
    assert bucket_window(buckets, -1) is None
    assert bucket_window(buckets, 99) is None


def test_related_prefers_component_over_source():
    rows = build_log_rows(
        [
            _ev(0, "warning", "fan", "stall", component="cpu_fan"),
            _ev(1, "info", "fan", "unrelated fan event"),
            _ev(2, "info", "fan", "recovered", component="cpu_fan"),
        ]
    )
    result = related_rows(rows, rows[2])
    assert result is not None
    assert [r.message for r in result.rows] == ["stall"]
    assert result.component == "cpu_fan"
    assert "component" in result.label.lower()


def test_related_falls_back_to_source_and_says_so():
    rows = build_log_rows([_ev(0, "info", "polling", "a"), _ev(1, "info", "polling", "b")])
    result = related_rows(rows, rows[1])
    assert result is not None
    assert result.component == ""
    assert "source" in result.label.lower()
    assert [r.message for r in result.rows] == ["a"]


def test_related_excludes_the_selected_event_itself():
    rows = build_log_rows([_ev(0, "info", "polling", "only one")])
    result = related_rows(rows, rows[0])
    assert result is not None and result.rows == ()


def test_related_is_none_without_a_selection_or_a_source():
    rows = build_log_rows([_ev(0, "info", "", "sourceless")])
    assert related_rows(rows, None) is None
    assert related_rows(rows, rows[0]) is None


def test_related_is_capped():
    rows = build_log_rows([_ev(i, "info", "polling", f"m{i}") for i in range(30)])
    result = related_rows(rows, rows[0], limit=5)
    assert result is not None and len(result.rows) == 5


def test_raw_record_serialises_the_stored_event_not_a_syslog_line():
    """Brief §7.3: a GUI event has no wire line, so the record itself is the raw
    view — and it must carry what the message text does not."""
    row = build_log_row(_ev(0, "error", "hwmon", "write failed", errno="EACCES"))
    raw = format_raw_record(row)
    assert "errno" in raw and "EACCES" in raw
    assert "write failed" in raw and "hwmon" in raw
    assert not raw.startswith("[")


def test_raw_record_returns_a_genuine_source_line_untouched():
    """The forward-compatible half: a row that *does* carry a verbatim line (a future
    second event source) must be shown exactly as it arrived."""
    row = build_log_row(_ev(0, "info", "gui", "m"))
    verbatim = "Sep 02 10:30:58 host control-ofc-daemon[738]: [INFO  mod] hello"
    assert format_raw_record(LogRowVM(**{**row.__dict__, "raw": verbatim})) == verbatim


def test_raw_record_reports_a_run():
    rows = collapse_repeats(build_log_rows([_ev(i, "warning", "fan", "stall") for i in range(3)]))
    raw = format_raw_record(rows[0])
    assert "repeats" in raw and "3" in raw


def test_row_line_marks_a_repeat_run_only_when_there_is_one():
    single = build_log_row(_ev(0, "info", "gui", "one"))
    assert REPEAT_MARK not in format_row_line(single)
    run = collapse_repeats(build_log_rows([_ev(i, "info", "gui", "one") for i in range(2)]))[0]
    assert f"{REPEAT_MARK}2" in format_row_line(run)


def test_event_with_context_carries_the_related_rows():
    rows = build_log_rows(
        [
            _ev(0, "warning", "fan", "stall", component="cpu_fan"),
            _ev(1, "info", "fan", "recovered", component="cpu_fan"),
        ]
    )
    text = format_event_with_context(rows[1], related_rows(rows, rows[1]))
    assert "recovered" in text and "stall" in text


def test_event_with_context_omits_an_empty_related_block():
    row = build_log_row(_ev(0, "info", "gui", "alone"))
    assert format_event_with_context(row, None) == format_raw_record(row)


@pytest.mark.parametrize(
    "level,state", [("info", "ok"), ("warning", "warn"), ("error", "crit"), ("bogus", "neutral")]
)
def test_level_state_is_the_shared_severity_mapping(level, state):
    """One mapping for the pill, the row edge and the histogram — so the same event
    is never one colour in one widget and another elsewhere."""
    assert level_state(level) == state


def test_index_for_window_finds_the_column_representing_a_window():
    rows = build_log_rows([_ev(i * 10, "info", "gui", f"m{i}") for i in range(4)])
    buckets = histogram_buckets(rows, span=time_span(rows), bucket_count=4)
    for i in range(4):
        assert index_for_window(buckets, bucket_window(buckets, i)) == i


def test_index_for_window_is_none_without_a_window():
    rows = build_log_rows([_ev(0, "info", "gui", "a")])
    buckets = histogram_buckets(rows, span=time_span(rows), bucket_count=2)
    assert index_for_window(buckets, None) is None


def test_index_for_window_survives_the_span_growing():
    """The point of re-deriving rather than remembering: a later event moves every
    boundary, and the column representing a fixed window moves with them."""
    rows = build_log_rows([_ev(i * 10, "info", "gui", f"m{i}") for i in range(4)])
    buckets = histogram_buckets(rows, span=time_span(rows), bucket_count=4)
    window = bucket_window(buckets, 3)

    wider = [*rows, build_log_row(_ev(1000, "info", "gui", "much later"))]
    regrouped = histogram_buckets(wider, span=time_span(wider), bucket_count=4)
    idx = index_for_window(regrouped, window)

    assert idx is not None
    b = regrouped[idx]
    assert b.start <= window[0] < b.end, "the resolved column must cover the window"
    assert idx != 3, "and it is genuinely a different column than before"


def test_index_for_window_returns_none_for_a_window_off_the_end():
    rows = build_log_rows([_ev(i, "info", "gui", f"m{i}") for i in range(3)])
    buckets = histogram_buckets(rows, span=time_span(rows), bucket_count=3)
    assert index_for_window(buckets, (_BASE - 5000, _BASE - 4000)) is None
