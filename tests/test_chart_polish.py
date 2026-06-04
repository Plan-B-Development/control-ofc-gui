"""DEC-118: Dashboard chart polish.

Covers the three pieces of the polish pass:

- **Per-item antialiasing** on the dashboard series. The global pyqtgraph
  ``antialias`` config flag stays ``False`` (DEC-068 — keeps other charts and
  the curve editor cheap); only the dashboard's own curve items opt in, via
  the per-item ``antialias=True`` kwarg. ``GraphicsView.setAntialiasing`` is
  NOT sufficient here because each curve's ``paint()`` resets the render hint
  to its own ``opts['antialias']``.
- **Themed hover-tooltip plate** driven by the ``chart_tooltip_bg`` /
  ``chart_tooltip_border`` tokens, updated live on theme switch.
- **Latest-value markers** — one ScatterPlotItem per visible series, kept in a
  dedicated dict so they never confuse the series item-type/count assertions.

All deterministic and hardware-free.
"""

from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtGui import QColor

from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.theme import (
    ThemeTokens,
    check_contrast_warnings,
    default_dark_theme,
    list_bundled_themes,
    load_theme,
)
from control_ofc.ui.widgets.timeline_chart import TimelineChart

TEMP_KEY = "sensor:cpu0"
RPM_KEY = "fan:openfan:ch00:rpm"


def _seed_history(history: HistoryStore, key: str, n: int = 10, base: float = 40.0) -> None:
    """Append *n* points (base, base+1, …) for *key*; last value is base+n-1."""
    now = time.monotonic()
    for i in range(n):
        history._append(key, now - (n - i), base + i)


def _make_chart(qtbot, seeded: dict[str, float]):
    """Build a chart with each key in *seeded* seeded, visible, and rendered."""
    history = HistoryStore()
    selection = SeriesSelectionModel()
    chart = TimelineChart(history, selection=selection)
    qtbot.addWidget(chart)
    for key, base in seeded.items():
        _seed_history(history, key, base=base)
    selection.update_known_keys(list(seeded))
    for key in seeded:
        selection.set_visible(key, True)
    chart.update_chart()
    return chart, history, selection


class TestPerItemAntialiasing:
    """AA is enabled per-item on dashboard series; the GLOBAL flag stays False."""

    def test_global_antialias_flag_stays_false(self, qtbot):
        # Force the global flag ON first so this genuinely guards the
        # setConfigOptions(antialias=False) reset in TimelineChart.__init__
        # (DEC-068), instead of passing against pyqtgraph's default-False.
        pg.setConfigOptions(antialias=True)
        try:
            _make_chart(qtbot, {TEMP_KEY: 45.0, RPM_KEY: 1200.0})
            assert pg.getConfigOption("antialias") is False
        finally:
            pg.setConfigOptions(antialias=False)

    def test_temp_series_item_antialiased(self, qtbot):
        chart, *_ = _make_chart(qtbot, {TEMP_KEY: 45.0})
        assert chart._temp_items[TEMP_KEY].opts["antialias"] is True

    def test_rpm_series_item_antialiased(self, qtbot):
        chart, *_ = _make_chart(qtbot, {RPM_KEY: 1200.0})
        assert chart._rpm_items[RPM_KEY].opts["antialias"] is True

    def test_antialias_persists_across_updates(self, qtbot):
        """Guards a future refactor that recreates items each cycle without
        re-passing antialias=True (the current setData(x, y) path keeps it)."""
        chart, history, _ = _make_chart(qtbot, {TEMP_KEY: 45.0})
        history._append(TEMP_KEY, time.monotonic(), 99.0)
        chart.update_chart()
        assert chart._temp_items[TEMP_KEY].opts["antialias"] is True


class TestLatestValueMarker:
    """One latest-value marker per visible series, at the most recent reading."""

    def test_marker_created_for_visible_temp(self, qtbot):
        chart, *_ = _make_chart(qtbot, {TEMP_KEY: 45.0})
        assert TEMP_KEY in chart._latest_items
        assert isinstance(chart._latest_items[TEMP_KEY], pg.ScatterPlotItem)

    def test_marker_created_for_visible_rpm(self, qtbot):
        chart, *_ = _make_chart(qtbot, {RPM_KEY: 1200.0})
        assert RPM_KEY in chart._latest_items
        assert isinstance(chart._latest_items[RPM_KEY], pg.ScatterPlotItem)

    def test_rpm_marker_added_to_secondary_viewbox(self, qtbot):
        # The RPM marker must live on the secondary ViewBox so it tracks the
        # right-hand RPM scale, not the left-hand temperature scale.
        chart, *_ = _make_chart(qtbot, {RPM_KEY: 1200.0})
        assert chart._latest_items[RPM_KEY] in chart._rpm_vb.addedItems

    def test_marker_sits_at_latest_value(self, qtbot):
        # base=45.0, n=10 -> values 45..54; the marker tracks the last one.
        chart, *_ = _make_chart(qtbot, {TEMP_KEY: 45.0})
        _xd, yd = chart._latest_items[TEMP_KEY].getData()
        assert len(yd) == 1
        assert yd[0] == 54.0

    def test_marker_kept_out_of_series_dicts(self, qtbot):
        """Markers must never live in _temp_items/_rpm_items, so the series
        item-type/count assertions in test_chart_cliptoview_r71 keep holding."""
        chart, *_ = _make_chart(qtbot, {TEMP_KEY: 45.0, RPM_KEY: 1200.0})
        assert chart._latest_items[TEMP_KEY] is not chart._temp_items[TEMP_KEY]
        assert chart._latest_items[RPM_KEY] is not chart._rpm_items[RPM_KEY]

    def test_marker_color_matches_series(self, qtbot):
        chart, *_ = _make_chart(qtbot, {TEMP_KEY: 45.0})
        expected = QColor(chart.color_for_key(TEMP_KEY)).name().lower()
        brush = chart._latest_items[TEMP_KEY].opts["brush"]
        assert QColor(brush.color()).name().lower() == expected

    def test_marker_recolours_on_series_color_override(self, qtbot):
        chart, *_ = _make_chart(qtbot, {TEMP_KEY: 45.0})
        chart.set_series_color(TEMP_KEY, "#abcdef")
        brush = chart._latest_items[TEMP_KEY].opts["brush"]
        assert QColor(brush.color()).name().lower() == "#abcdef"

    def test_set_theme_recolours_existing_markers(self, qtbot):
        # set_theme must restyle markers already on the chart (not just series
        # pens) so a palette switch isn't left two-toned.
        chart, *_ = _make_chart(qtbot, {TEMP_KEY: 45.0})
        custom = ThemeTokens(name="Recolor", chart_series=["#ff00ff"] * 8)
        chart.set_theme(custom)
        brush = chart._latest_items[TEMP_KEY].opts["brush"]
        assert QColor(brush.color()).name().lower() == "#ff00ff"

    def test_hidden_series_marker_cleared(self, qtbot):
        chart, _history, selection = _make_chart(qtbot, {TEMP_KEY: 45.0})
        assert TEMP_KEY in chart._latest_items
        selection.set_visible(TEMP_KEY, False)
        chart.update_chart()
        xd, _yd = chart._latest_items[TEMP_KEY].getData()
        assert xd is None or len(xd) == 0

    def test_stale_series_marker_removed(self, qtbot):
        """A series dropping out of the known set removes its marker too."""
        chart, _history, selection = _make_chart(qtbot, {TEMP_KEY: 45.0, "sensor:cpu1": 40.0})
        assert TEMP_KEY in chart._latest_items
        selection.update_known_keys(["sensor:cpu1"])  # cpu0 now stale
        chart.update_chart()
        assert TEMP_KEY not in chart._temp_items
        assert TEMP_KEY not in chart._latest_items

    def test_cleanup_clears_markers(self, qtbot):
        chart, *_ = _make_chart(qtbot, {TEMP_KEY: 45.0, RPM_KEY: 1200.0})
        assert chart._latest_items
        chart.cleanup()
        assert chart._latest_items == {}
        # Idempotent — a second call must not raise.
        chart.cleanup()


class TestTooltipPlate:
    """The hover readout has a themed background plate and border."""

    def test_hover_label_fill_follows_token(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        expected = QColor(chart._theme.chart_tooltip_bg).name().lower()
        assert QColor(chart._hover_label.fill.color()).name().lower() == expected

    def test_hover_label_border_follows_token(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        expected = QColor(chart._theme.chart_tooltip_border).name().lower()
        assert QColor(chart._hover_label.border.color()).name().lower() == expected

    def test_set_theme_updates_tooltip_plate(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        custom = ThemeTokens(
            name="Tooltip Test",
            chart_tooltip_bg="#123456",
            chart_tooltip_border="#654321",
        )
        chart.set_theme(custom)
        assert QColor(chart._hover_label.fill.color()).name().lower() == "#123456"
        assert QColor(chart._hover_label.border.color()).name().lower() == "#654321"


class TestTooltipTokensDefined:
    """The new tokens exist everywhere and keep WCAG-AA contrast."""

    def test_default_theme_defines_tooltip_tokens(self):
        t = default_dark_theme()
        assert t.chart_tooltip_bg.startswith("#")
        assert t.chart_tooltip_border.startswith("#")

    def test_bundled_presets_define_tooltip_tokens(self):
        presets = list_bundled_themes()
        assert presets, "no bundled presets found"
        for path in presets:
            tokens = load_theme(path)
            assert tokens.chart_tooltip_bg.startswith("#"), f"{path.name} chart_tooltip_bg"
            assert tokens.chart_tooltip_border.startswith("#"), f"{path.name} chart_tooltip_border"

    def test_tooltip_text_contrast_ok_in_all_themes(self):
        """text_primary on chart_tooltip_bg must hit AA in default + presets."""
        themes = [default_dark_theme()] + [load_theme(p) for p in list_bundled_themes()]
        for tokens in themes:
            offending = [w for w in check_contrast_warnings(tokens) if "chart_tooltip_bg" in w]
            assert not offending, f"{tokens.name}: {offending}"
