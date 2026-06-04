"""Chart theme adherence — gridline colour follows ``chart_grid``, and the
time-range control is discoverable.

These tests cover the audit pass that confirmed:

- ``chart_grid`` was advertised in the theme editor and present in every
  bundled preset, but the timeline chart never set an explicit tick pen.
  pyqtgraph then fell back to the axis pen for gridlines, so editing
  ``chart_grid`` in the theme had zero visible effect.
- ``set_theme`` must restyle the gridlines too — not just background, axes,
  crosshair, and series — so a theme switch fully reflects the new palette.
- The dashboard chart's only user control is the time-range combobox; it
  needs a visible label so users can recognise it without clicking.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

from control_ofc.services.history_store import HistoryStore
from control_ofc.ui.theme import ThemeTokens
from control_ofc.ui.widgets.timeline_chart import TimelineChart


def _tick_pen_color(chart: TimelineChart, axis_name: str) -> str:
    plot = chart._plot_widget.getPlotItem()
    axis = plot.getAxis(axis_name)
    # AxisItem exposes the active tick pen via tickPen(); compare its colour
    # back to a hex string so we can assert against the token.
    return QColor(axis.tickPen().color()).name().lower()


class TestChartGridUsesToken:
    """The gridline colour must follow the ``chart_grid`` token on construction."""

    def test_left_axis_tick_pen_matches_chart_grid(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        expected = QColor(chart._theme.chart_grid).name().lower()
        assert _tick_pen_color(chart, "left") == expected

    def test_bottom_axis_tick_pen_matches_chart_grid(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        expected = QColor(chart._theme.chart_grid).name().lower()
        assert _tick_pen_color(chart, "bottom") == expected

    def test_right_axis_tick_pen_matches_chart_grid(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        expected = QColor(chart._theme.chart_grid).name().lower()
        assert _tick_pen_color(chart, "right") == expected

    def test_axis_pen_remains_axis_text(self, qtbot):
        """Axis line (not grid) must still follow ``chart_axis_text`` so the
        two tokens stay independently controllable."""
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        plot = chart._plot_widget.getPlotItem()
        for axis_name in ("left", "bottom", "right"):
            color = QColor(plot.getAxis(axis_name).pen().color()).name().lower()
            assert color == QColor(chart._theme.chart_axis_text).name().lower(), axis_name


class TestChartGridFollowsThemeSwitch:
    """``set_theme`` must restyle the gridline colour, not just bg/axes/crosshair."""

    def test_set_theme_updates_left_tick_pen(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        custom = ThemeTokens(name="Grid Test", chart_grid="#abcdef")
        chart.set_theme(custom)
        assert _tick_pen_color(chart, "left") == "#abcdef"

    def test_set_theme_updates_bottom_tick_pen(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        custom = ThemeTokens(name="Grid Test", chart_grid="#abcdef")
        chart.set_theme(custom)
        assert _tick_pen_color(chart, "bottom") == "#abcdef"

    def test_set_theme_updates_right_tick_pen(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        custom = ThemeTokens(name="Grid Test", chart_grid="#abcdef")
        chart.set_theme(custom)
        assert _tick_pen_color(chart, "right") == "#abcdef"

    def test_set_theme_keeps_axis_pen_independent_of_grid(self, qtbot):
        """A theme swap must update axis-text and grid independently — the
        bug being guarded against is the two collapsing onto one token."""
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        custom = ThemeTokens(
            name="Two-tone",
            chart_grid="#112233",
            chart_axis_text="#445566",
        )
        chart.set_theme(custom)

        plot = chart._plot_widget.getPlotItem()
        for axis_name in ("left", "bottom", "right"):
            axis = plot.getAxis(axis_name)
            assert QColor(axis.pen().color()).name().lower() == "#445566", axis_name
            assert QColor(axis.tickPen().color()).name().lower() == "#112233", axis_name


class TestRangeControlIsDiscoverable:
    """The time-range combo is the only chart-level control; it needs a label."""

    def test_range_label_widget_exists(self, qtbot):
        from PySide6.QtWidgets import QLabel

        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        label = chart.findChild(QLabel, "TimelineChart_Label_range")
        assert label is not None, "missing time-range label"
        assert label.text() == "Range:"

    def test_range_label_is_buddy_of_combo(self, qtbot):
        """QLabel.setBuddy lets screen readers + Alt-mnemonics jump to the
        combo. Asserting the link prevents silent re-introduction of an
        orphaned label."""
        from PySide6.QtWidgets import QLabel

        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        label = chart.findChild(QLabel, "TimelineChart_Label_range")
        assert label is not None
        assert label.buddy() is chart._range_combo

    def test_range_combo_has_tooltip(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)

        assert chart._range_combo.toolTip() != ""


class TestAxisTitleColor:
    """The axis *title* colour follows ``text_secondary``.

    pyqtgraph sets the title colour as a side effect of ``setTextPen`` (it
    writes ``labelStyle['color']`` and regenerates the label), so the title
    already tracks the theme. These tests pin that behaviour so a future
    reorder — e.g. calling ``setLabel`` after ``setTextPen`` — can't silently
    drop the title back to the pyqtgraph default colour (DEC-118).
    """

    def _label_color(self, chart: TimelineChart, axis_name: str) -> str:
        plot = chart._plot_widget.getPlotItem()
        axis = plot.getAxis(axis_name)
        return QColor(axis.labelStyle.get("color", "")).name().lower()

    def test_axis_title_follows_text_secondary(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        expected = QColor(chart._theme.text_secondary).name().lower()
        for axis_name in ("left", "bottom", "right"):
            assert self._label_color(chart, axis_name) == expected, axis_name

    def test_set_theme_updates_axis_title_color(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        custom = ThemeTokens(name="Title", text_secondary="#abcdef")
        chart.set_theme(custom)
        for axis_name in ("left", "bottom", "right"):
            assert self._label_color(chart, axis_name) == "#abcdef", axis_name
