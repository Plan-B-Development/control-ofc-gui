"""PWM Test Report — exports, comparison and history storage (DEC-404 Stage 5).

Qt-free. Every export is asserted on the realised text it would write, and every
escaping rule against a hostile string placed where a daemon label or a user
alias really arrives — never against the escaping helper alone.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import typing
from dataclasses import fields, is_dataclass

import pytest

from control_ofc.api.models import CharPoint
from control_ofc.services.characterization_view import ResponseCurve, SeriesPoint
from control_ofc.services.pwm_report import compare as cmp
from control_ofc.services.pwm_report import export_csv, export_html, export_markdown, store
from control_ofc.services.pwm_report import svg_chart as svg
from control_ofc.services.pwm_report.compare import (
    CATEGORY_CONFIGURATION,
    CATEGORY_ENVIRONMENT,
    CATEGORY_HARDWARE,
    CATEGORY_NOT_COMPARABLE,
    CATEGORY_RESPONSE,
    compare_reports,
)
from control_ofc.ui.theme import bundled_themes_dir, default_dark_theme, load_theme
from tests.pwm_report_fixtures import (
    CPU,
    OPENFAN,
    SYS,
    channel,
    complete_doc,
    sweep_run,
)

HOSTILE = '<script>alert(1)</script> [x](javascript:alert(1)) | pipe\n# Heading & "q"'


def _hostile_doc() -> dict:
    doc = complete_doc()
    for ch in doc["channels"]:
        if ch["channel_id"] == CPU:
            ch["name"] = HOSTILE
    doc["user_facts"]["cooler"]["model"] = HOSTILE
    # The evidence carries the daemon's own words, verbatim.
    doc["snapshots"]["baseline"]["headers"]["body"]["headers"][0]["label"] = HOSTILE
    return doc


# ── Markdown ─────────────────────────────────────────────────────────────────


def test_markdown_escapes_every_dynamic_string_where_it_really_arrives():
    text = export_markdown.report_markdown(_hostile_doc())
    assert "HOSTILE" not in text  # sanity: the constant's name never leaks
    assert "alert" in text, "precondition: the hostile channel name reached the export"
    assert "<script>" not in text
    assert "](javascript" not in text
    # A line break in a label must not start a heading or end a table row.
    assert not any(line.startswith("# Heading") for line in text.splitlines())
    table_rows = [line for line in text.splitlines() if "alert" in line and line.startswith("|")]
    assert table_rows, "the hostile name should appear in the channel table"
    for row in table_rows:
        cells = row.split(" | ")
        assert len(cells) == 4, f"an escaped pipe must not add a column: {row}"


def test_markdown_says_to_attach_rather_than_paste_and_carries_the_sections():
    text = export_markdown.report_markdown(complete_doc())
    head = "\n".join(text.splitlines()[:4])
    assert "attach the file" in head
    for section in ("## Needs attention", "## Restoration", "## Channels", "## Not tested"):
        assert section in text
    assert "## Environment" in text and "X870E" in text


# ── HTML ─────────────────────────────────────────────────────────────────────


def test_html_is_self_contained_and_escapes_label_notes_and_evidence():
    text = export_html.report_html(_hostile_doc())
    assert "alert(1)" in text, "precondition: the hostile strings reached the export"
    assert "<script" not in text.lower()
    assert "&lt;script&gt;" in text
    # Nothing loads from anywhere: no link, src, @import or url().
    for token in ("<link", " src=", "@import", "url("):
        assert token not in text
    assert "Content-Security-Policy" in text and "default-src 'none'" in text
    # The only URL is the SVG namespace, which is a name, not a fetch.
    urls = {w for w in text.replace('"', " ").split() if w.startswith(("http://", "https://"))}
    assert urls <= {"http://www.w3.org/2000/svg"}
    # S5-4: the evidence is present, escaped, and collapsed by default.
    assert "<details><summary>Evidence" in text
    assert "<details open><summary>Evidence" not in text


def test_html_colours_come_from_the_theme_tokens_for_both_schemes():
    text = export_html.report_html(complete_doc())
    dark = default_dark_theme()
    light = load_theme(bundled_themes_dir() / "solar_light.json")
    root, _, media = text.partition("@media (prefers-color-scheme: light)")
    assert f"--bg: {dark.app_bg};" in root
    assert f"--bg: {light.app_bg};" in media.split("}")[0] + "}"
    assert dark.app_bg != light.app_bg, "precondition: the two schemes differ"


def test_html_draws_a_trace_chart_per_tested_header_and_one_for_the_cpu():
    doc = complete_doc()
    text = export_html.report_html(doc)
    tested = export_html.tested_channel_ids(doc)
    assert set(tested) == {CPU, SYS}
    assert text.count("RPM and duty read back over the run") == 2 * len(tested)  # label+title
    assert "Hottest CPU temperature over the run" in text
    # A read-only channel is in the report but gets no trace chart (S5-3).
    assert "ch00" in text
    assert "ch00: RPM and duty" not in text
    assert OPENFAN not in tested


def test_hottest_cpu_series_takes_the_max_and_keeps_gaps():
    temps = {"a": [40.0, None, 50.0], "b": [45.0, None, None]}
    assert export_html.hottest_series(temps, ["a", "b"], 3) == [45.0, None, 50.0]


def test_thermal_shading_covers_only_reported_non_normal_samples():
    spans = export_html.thermal_spans(
        [0, 1000, 2000, 3000, 4000], ["normal", "emergency", "emergency", None, "normal"]
    )
    assert spans == [(1000, 3000)]


# ── SVG ──────────────────────────────────────────────────────────────────────


def test_a_gap_in_a_series_breaks_the_line():
    chart, decimated = svg.time_chart(
        [0, 1000, 2000, 3000, 4000],
        [svg.TimeSeries("RPM", [900, 910, None, 905, 900], "series-rpm")],
        title="t",
        left_label="RPM",
    )
    assert not decimated
    assert chart.count("<polyline") == 2


def test_decimation_keeps_every_extreme_and_says_it_happened():
    n = 5000
    values = [1000.0] * n
    values[1234] = 4000.0  # a spike
    values[4321] = 0.0  # a stall
    runs = svg.split_runs([float(i) for i in range(n)], values)
    reduced = svg.decimate_minmax(runs[0], 0.0, float(n - 1), svg.PLOT_W)
    assert len(reduced) < n
    ys = {y for _, y in reduced}
    assert 4000.0 in ys and 0.0 in ys
    _, decimated = svg.time_chart(
        [i * 1000 for i in range(n)],
        [svg.TimeSeries("RPM", values, "series-rpm")],
        title="t",
        left_label="RPM",
    )
    assert decimated


def test_short_series_are_not_decimated():
    _, decimated = svg.time_chart(
        [i * 1000 for i in range(svg.DECIMATE_ABOVE)],
        [svg.TimeSeries("RPM", [900] * svg.DECIMATE_ABOVE, "series-rpm")],
        title="t",
        left_label="RPM",
    )
    assert not decimated


def test_svg_text_is_escaped():
    curve = ResponseCurve(falling=[SeriesPoint(50, 900)], rising=[], has_data=True)
    chart = svg.response_chart(curve, title=HOSTILE)
    assert "<script>" not in chart and "&lt;script&gt;" in chart


# ── CSV ──────────────────────────────────────────────────────────────────────


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_sweep_columns_cover_every_charpoint_field_and_nest_only_the_known_ones():
    hints = typing.get_type_hints(CharPoint)
    cols = export_csv.sweep_columns()
    for f in fields(CharPoint):
        kinds = {hints[f.name], *typing.get_args(hints[f.name])}
        nested = any(is_dataclass(k) for k in kinds)
        if f.name in export_csv.SWEEP_NESTED:
            assert nested
            assert any(c.startswith(f"{f.name}.") for c in cols)
        else:
            assert not nested, f"{f.name} is a dataclass: add it to SWEEP_NESTED"
            assert f.name in cols


def test_csv_writes_absent_values_as_empty_cells_never_zero():
    run = sweep_run()
    run["points"][0]["rpm_after"] = None
    doc = complete_doc(sweep=run)
    rows = _rows(export_csv.csv_tables(doc)["sweep"])
    first = next(r for r in rows if r["step_index"] == "0")
    assert first["rpm_after"] == ""
    assert first["stability.median_rpm"] == ""  # the fixture carries no stability


def test_csv_neutralises_formulas_in_text_but_never_touches_numbers():
    doc = complete_doc()
    for ch in doc["channels"]:
        if ch["channel_id"] == CPU:
            ch["name"] = '=HYPERLINK("http://x","y")'
    doc["trace"]["temps_c"]["cpu0"][0] = -5.0
    tables = export_csv.csv_tables(doc)
    sweep = _rows(tables["sweep"])
    assert sweep[0]["channel_name"].startswith("'=HYPERLINK")
    trace = _rows(tables["trace"])
    assert trace[0]["cpu0 value_c"] == "-5.0"


def test_csv_files_are_rfc4180_one_row_per_sample_and_skip_empty_tables():
    doc = complete_doc(samples=12)
    doc["steps"] = [s for s in doc["steps"] if s["test"] != "probe"]
    tables = export_csv.csv_tables(doc)
    assert set(tables) == {"sweep", "trace"}
    assert tables["trace"].count("\r\n") == 12 + 1
    assert export_csv.csv_file_name(doc, "trace") == "pwm-report-20260922T000000Z-abcdef-trace.csv"
    empty = complete_doc()
    empty["steps"], empty["trace"] = [], None
    assert export_csv.csv_tables(empty) == {}


# ── Comparison ───────────────────────────────────────────────────────────────


def _pair(**later_kwargs) -> tuple[dict, dict]:
    earlier = complete_doc(report_id="a", started_at="2026-09-01T00:00:00Z")
    later = complete_doc(report_id="b", started_at="2026-09-20T00:00:00Z", **later_kwargs)
    return earlier, later


def test_reports_are_ordered_earlier_first_whatever_order_they_are_given():
    earlier, later = _pair()
    result = compare_reports(later, earlier)
    assert (result.earlier.report_id, result.later.report_id) == ("a", "b")


def test_channels_pair_by_stable_id_and_a_rename_is_never_matched():
    renamed = "hwmon:it8696:it87.2624:pwm2:REAR_FAN"
    chans = [channel(CPU, in_profile=True), channel(renamed), channel(OPENFAN, source="openfan")]
    earlier, later = _pair(channels=chans)
    for step in later["steps"]:
        if step["channel_id"] == SYS:
            step["channel_id"] = renamed
    result = compare_reports(earlier, later)
    assert [cid for cid, _ in result.only_earlier] == [SYS]
    assert [cid for cid, _ in result.only_later] == [renamed]
    assert result.possible_renames == [(SYS, renamed)]
    assert SYS not in {cid for cid, _ in result.paired}
    # Presence first: the probe on SYS/REAR is a real measurement on both sides...
    assert any(s["channel_id"] == renamed and s["test"] == "probe" for s in later["steps"])
    # ...and it is compared nowhere, because the pairing is only an inference.
    assert not [d for d in result.differences if d.channel_id in (SYS, renamed)]


def test_rename_key_is_the_id_without_its_label():
    assert cmp.rename_key(CPU) == "hwmon:it8696:it87.2624:pwm1"
    assert cmp.rename_key("openfan:ch00") is None


def test_each_difference_lands_in_its_category():
    earlier, later = _pair()
    later["environment"]["daemon"]["daemon_version"] = "2.55.0"
    later["configuration"]["profile_hash"] = "f" * 64
    later["user_facts"]["headers"] = {CPU: {"fans_behind": 3}}
    result = compare_reports(earlier, later)
    by_cat = {c: [d.subject for d in result.in_category(c)] for c in cmp.CATEGORY_ORDER}
    assert "Control-OFC daemon" in by_cat[CATEGORY_ENVIRONMENT]
    assert "Active profile content" in by_cat[CATEGORY_CONFIGURATION]
    assert any("fans on the header" in s for s in by_cat[CATEGORY_HARDWARE])
    assert by_cat[CATEGORY_RESPONSE], "matching tests are compared"
    assert not by_cat[CATEGORY_NOT_COMPARABLE]


def test_a_measured_delta_sits_beside_each_runs_own_spread():
    run = sweep_run()
    run["points"][0]["stability"] = {
        "median_rpm": 1700,
        "min_rpm": 1690,
        "max_rpm": 1712,
        "cv_pct": 0.4,
        "usable": 20,
    }
    earlier, later = _pair(sweep=run)
    result = compare_reports(earlier, later)
    row = next(
        d for d in result.in_category(CATEGORY_RESPONSE) if d.subject.endswith("100 % (falling)")
    )
    assert (row.earlier, row.later, row.delta) == ("1600 rpm", "1700 rpm", "+100 rpm")
    assert "later: 1690\u20131712 rpm, CV 0.4 %" in row.note
    assert "earlier: no spread recorded" in row.note


def test_different_parameters_make_a_test_not_comparable():
    run = sweep_run()
    run["settle_seconds"] = 15
    earlier, later = _pair(sweep=run)
    result = compare_reports(earlier, later)
    reasons = [
        d for d in result.in_category(CATEGORY_NOT_COMPARABLE) if "Full PWM sweep" in d.subject
    ]
    assert reasons and "settle_seconds: 12 → 15" in reasons[0].note
    sweep_rows = [
        d
        for d in result.in_category(CATEGORY_RESPONSE)
        if "full sweep" in d.subject or d.subject.endswith(("(falling)", "(rising)"))
    ]
    assert not sweep_rows


def test_a_different_thermal_state_at_the_start_makes_every_response_not_comparable():
    earlier, later = _pair(thermal_state="emergency")
    result = compare_reports(earlier, later)
    assert not result.in_category(CATEGORY_RESPONSE)
    assert all("thermal state" in d.note for d in result.in_category(CATEGORY_NOT_COMPARABLE))


def test_start_temperature_is_shown_with_its_difference_and_never_judged():
    earlier, later = _pair(cpu_temp=61.5)
    result = compare_reports(earlier, later)
    row = next(d for d in result.start_conditions if "CPU" in d.subject)
    assert (row.earlier, row.later, row.delta) == ("45 °C", "61.5 °C", "+16.5 °C")
    # S5-5: a warmer start alone moves nothing into "not comparable".
    assert result.in_category(CATEGORY_RESPONSE)
    assert not result.in_category(CATEGORY_NOT_COMPARABLE)


def test_a_step_that_did_not_complete_is_not_comparable():
    earlier, later = _pair()
    later["steps"][0]["status"] = "cancelled"
    result = compare_reports(earlier, later)
    row = next(
        d for d in result.in_category(CATEGORY_NOT_COMPARABLE) if "PWM control test" in d.subject
    )
    assert "did not complete in the later report" in row.note


def test_comparison_exports_escape_and_carry_every_category():
    earlier, later = _pair()
    later["channels"][0]["name"] = HOSTILE
    result = compare_reports(earlier, later)
    md = export_markdown.comparison_markdown(result)
    page = export_html.comparison_html(result)
    for title in cmp.CATEGORY_TITLES.values():
        assert title in md and title in page
    assert "alert" in page and "<script" not in page.lower()
    assert "<script>" not in md


# ── History storage ──────────────────────────────────────────────────────────


def _save(doc: dict, folder) -> object:
    return store.save_report(doc, folder)


def test_history_lists_newest_first_and_lists_unreadable_files(tmp_path):
    _save(complete_doc(report_id="old", started_at="2026-09-01T00:00:00Z"), tmp_path)
    _save(complete_doc(report_id="new", started_at="2026-09-20T00:00:00Z"), tmp_path)
    (tmp_path / "pwm-report-broken.json").write_text("{not json")
    other = copy.deepcopy(complete_doc(report_id="x"))
    other["kind"] = "something-else"
    (tmp_path / "pwm-report-foreign.json").write_text(json.dumps(other))
    entries = store.list_reports(tmp_path)
    readable = [e for e in entries if not e.error]
    assert [e.report_id for e in readable] == ["new", "old"]
    assert readable[0].machine == "Gigabyte X870E AORUS MASTER"
    assert readable[0].tests == "3 of 3 test(s) completed on 2 header(s)"
    assert {e.path.name for e in entries if e.error} == {
        "pwm-report-broken.json",
        "pwm-report-foreign.json",
    }


def test_history_rejects_an_oversized_file(tmp_path, monkeypatch):
    path = _save(complete_doc(report_id="big"), tmp_path)
    monkeypatch.setattr(store, "REPORT_MAX_BYTES", path.stat().st_size - 1)
    (entry,) = store.list_reports(tmp_path)
    assert entry.error
    monkeypatch.setattr(store, "REPORT_MAX_BYTES", path.stat().st_size)
    # The limit is a constant in production; changing it under an unchanged file
    # needs the listing cache cleared, or the first answer is (rightly) reused.
    monkeypatch.setattr(store, "_LIST_CACHE", {})
    (entry,) = store.list_reports(tmp_path)
    assert not entry.error


def test_delete_removes_a_saved_report_and_nothing_else(tmp_path):
    folder = tmp_path / "reports"
    path = _save(complete_doc(report_id="gone"), folder)
    store.delete_report(path, folder)
    assert not path.exists()
    outside = tmp_path / "pwm-report-outside.json"
    outside.write_text("{}")
    with pytest.raises(ValueError):
        store.delete_report(outside, folder)
    stray = folder / "notes.json"
    stray.write_text("{}")
    with pytest.raises(ValueError):
        store.delete_report(stray, folder)
    assert outside.exists() and stray.exists()


def test_delete_removes_a_link_and_never_its_target(tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    target = tmp_path / "precious.json"
    target.write_text("{}")
    link = folder / "pwm-report-link.json"
    link.symlink_to(target)
    store.delete_report(link, folder)
    assert not link.exists() and target.exists()


def test_the_probe_chart_names_its_own_legs():
    """The probe walks down from 20 %, never from 100 % — its legend must say so."""
    text = export_html.report_html(complete_doc())
    start = text.index("stall probe, RPM against duty")
    probe_svg = text[start : text.index("</svg>", start)]
    assert "Down from 20 %" in probe_svg and "100 %" not in probe_svg


# ── Review remediation (DEC-409): bounded work over files from elsewhere ─────


def test_a_real_report_passes_the_bounded_schema_check():
    from control_ofc.services.pwm_report import document as d

    doc = complete_doc(samples=50)
    assert d.validate_document(doc) is doc
    doc["trace"] = None
    assert d.validate_document(doc) is doc


def test_the_schema_check_bounds_every_list_a_renderer_walks(monkeypatch):
    from control_ofc.services.pwm_report import document as d

    doc = complete_doc()
    monkeypatch.setattr(d, "MAX_CHANNELS", len(doc["channels"]) - 1)
    with pytest.raises(d.ReportSchemaError, match="channels"):
        d.validate_document(doc)
    monkeypatch.setattr(d, "MAX_CHANNELS", 1024)
    monkeypatch.setattr(d, "MAX_STEPS", len(doc["steps"]) - 1)
    with pytest.raises(d.ReportSchemaError, match="steps"):
        d.validate_document(doc)


def test_the_schema_check_refuses_a_trace_an_export_would_blow_up(monkeypatch):
    from control_ofc.services.pwm_report import document as d
    from control_ofc.services.pwm_report import trace as tr

    long = complete_doc(samples=20)
    monkeypatch.setattr(tr, "MAX_SAMPLES", 19)
    with pytest.raises(d.ReportSchemaError, match="t_ms"):
        d.validate_document(long)
    monkeypatch.setattr(tr, "MAX_SAMPLES", 10800)
    short = complete_doc(samples=20)
    short["trace"]["fans"][CPU]["rpm"] = [900] * 5  # N x K padding needs this
    with pytest.raises(d.ReportSchemaError, match="rpm"):
        d.validate_document(short)
    temps = complete_doc(samples=20)
    temps["trace"]["temps_c"]["x"] = []
    with pytest.raises(d.ReportSchemaError, match="temps_c"):
        d.validate_document(temps)


def test_csv_trace_writes_only_the_recorders_own_quantities():
    doc = complete_doc(samples=5)
    doc["trace"]["fans"][CPU]["injected"] = [1] * 5
    header = export_csv.csv_tables(doc)["trace"].splitlines()[0]
    assert f"{CPU} rpm" in header
    assert "injected" not in header


def test_shading_never_draws_more_rectangles_than_the_chart_has_columns():
    n = 10_000
    states = ["emergency" if i % 2 else "normal" for i in range(n)]
    t_ms = [i * 1000 for i in range(n)]
    spans = export_html.thermal_spans(t_ms, states)
    assert len(spans) == n // 2, "precondition: thousands of separate spans"
    chart, _ = svg.time_chart(
        t_ms,
        [svg.TimeSeries("RPM", [900] * n, "series-rpm")],
        title="t",
        left_label="RPM",
        shading=spans,
    )
    assert chart.count('class="shade-thermal"') <= svg.PLOT_W


def test_a_value_that_is_not_a_number_is_a_gap_not_a_crash():
    runs = svg.split_runs([0, 1, 2, 3], [900, "x", [1], 905])
    assert runs == [[(0.0, 900.0)], [(3.0, 905.0)]]


def test_the_cpu_chart_reads_only_sensors_the_trace_recorded():
    doc = complete_doc(samples=10)
    sensors = doc["snapshots"]["baseline"]["sensors"]["body"]["sensors"]
    sensors += [{"id": f"ghost{i}", "kind": "cpu_temp", "value_c": 40.0} for i in range(500)]
    text = export_html.report_html(doc)
    assert "highest of 1 CPU sensor(s)" in text


def test_history_lists_a_file_nested_too_deeply_as_unreadable(tmp_path):
    _save(complete_doc(report_id="fine"), tmp_path)
    (tmp_path / "pwm-report-deep.json").write_text("[" * 100_000 + "]" * 100_000)
    entries = store.list_reports(tmp_path)
    assert {e.report_id for e in entries if not e.error} == {"fine"}
    (bad,) = [e for e in entries if e.error]
    assert "nested too deeply" in bad.error


def test_history_reuses_an_unchanged_file_and_rereads_a_changed_one(tmp_path, monkeypatch):
    path = _save(complete_doc(report_id="once"), tmp_path)
    reads: list[object] = []
    real = store._read
    monkeypatch.setattr(store, "_read", lambda p: reads.append(p) or real(p))
    store.list_reports(tmp_path)
    store.list_reports(tmp_path)
    assert reads == [path], "an unchanged file is parsed once"
    _save(complete_doc(report_id="once", started_at="2026-09-23T00:00:00Z"), tmp_path)
    (entry,) = store.list_reports(tmp_path)
    assert reads == [path, path] and entry.started_at == "2026-09-23T00:00:00Z"
