"""R41: Dashboard graph — y-axis limits, colour persistence, hover, fan table colour column."""

from __future__ import annotations

from onlyfans.services.app_settings_service import AppSettings
from onlyfans.services.history_store import HistoryStore
from onlyfans.services.series_selection import SeriesSelectionModel
from onlyfans.ui.widgets.timeline_chart import TimelineChart


class TestYAxisLimits:
    """Y-axis must never go below 0 for temperature or RPM."""

    def test_temp_viewbox_ymin_zero(self, qtbot):
        history = HistoryStore()
        chart = TimelineChart(history)
        qtbot.addWidget(chart)

        plot = chart._plot_widget.getPlotItem()
        limits = plot.getViewBox().state["limits"]
        assert limits["yLimits"][0] == 0

    def test_rpm_viewbox_ymin_zero(self, qtbot):
        history = HistoryStore()
        chart = TimelineChart(history)
        qtbot.addWidget(chart)

        limits = chart._rpm_vb.state["limits"]
        assert limits["yLimits"][0] == 0


class TestColourOverride:
    """User colour overrides take precedence over hash default."""

    def test_default_colour_from_theme(self, qtbot):
        history = HistoryStore()
        chart = TimelineChart(history)
        qtbot.addWidget(chart)

        color = chart.color_for_key("sensor:test")
        assert color.startswith("#")

    def test_override_takes_precedence(self, qtbot):
        history = HistoryStore()
        chart = TimelineChart(history, color_overrides={"sensor:test": "#ff0000"})
        qtbot.addWidget(chart)

        assert chart.color_for_key("sensor:test") == "#ff0000"

    def test_set_series_color_updates(self, qtbot):
        history = HistoryStore()
        chart = TimelineChart(history)
        qtbot.addWidget(chart)

        chart.set_series_color("sensor:test", "#00ff00")
        assert chart.color_for_key("sensor:test") == "#00ff00"


class TestColourPersistence:
    """series_colors setting persists across save/load."""

    def test_roundtrip(self, tmp_path, monkeypatch):
        from onlyfans.services.app_settings_service import AppSettingsService

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()
        svc.settings.series_colors["sensor:cpu0"] = "#ff0000"
        svc.save()

        svc2 = AppSettingsService()
        svc2.load()
        assert svc2.settings.series_colors.get("sensor:cpu0") == "#ff0000"

    def test_default_empty(self):
        settings = AppSettings()
        assert settings.series_colors == {}


class TestCrosshairHover:
    """Crosshair infrastructure exists and is initially hidden."""

    def test_crosshair_exists(self, qtbot):
        history = HistoryStore()
        chart = TimelineChart(history)
        qtbot.addWidget(chart)

        assert chart._crosshair_v is not None
        assert not chart._crosshair_v.isVisible()

    def test_hover_label_exists(self, qtbot):
        history = HistoryStore()
        chart = TimelineChart(history)
        qtbot.addWidget(chart)

        assert chart._hover_label is not None
        assert not chart._hover_label.isVisible()

    def test_proxy_exists(self, qtbot):
        history = HistoryStore()
        chart = TimelineChart(history)
        qtbot.addWidget(chart)

        assert chart._proxy is not None


class TestFanTableColumns:
    """Fan table has 4 columns (Colour removed in R42)."""

    def test_fan_table_column_count(self, qtbot, app_state):
        from onlyfans.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)

        assert page._fan_table.columnCount() == 4


class TestSensorPanelColumns:
    """Sensor panel tree has 3 columns (name, value, colour swatch)."""

    def test_tree_column_count(self, qtbot, app_state):
        from onlyfans.ui.widgets.sensor_series_panel import SensorSeriesPanel

        selection = SeriesSelectionModel()
        panel = SensorSeriesPanel(selection, state=app_state)
        qtbot.addWidget(panel)

        assert panel._tree.columnCount() == 3
