"""R41: Dashboard graph — y-axis limits, colour persistence, hover, fan table colour column."""

from __future__ import annotations

from control_ofc.services.app_settings_service import AppSettings
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.widgets.timeline_chart import TimelineChart


class TestYAxisLimits:
    """Y-axis must never go below 0 for temperature or RPM.

    Asserted *behaviourally* via the public ``viewRange()`` after asking for a
    negative range — the ``setLimits(yMin=0)`` floor clamps it — instead of
    reading pyqtgraph's undocumented ``viewbox.state['limits']`` dict (which a
    pyqtgraph version bump could silently rename).
    """

    def test_temp_axis_clamps_to_zero(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        vb = chart._plot_widget.getPlotItem().getViewBox()
        vb.setYRange(-50.0, 80.0)  # ask to show negative temperatures
        (_x_min, _x_max), (y_min, _y_max) = vb.viewRange()
        assert y_min >= 0

    def test_rpm_axis_clamps_to_zero(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        chart._rpm_vb.setYRange(-50.0, 5000.0)
        (_x_min, _x_max), (y_min, _y_max) = chart._rpm_vb.viewRange()
        assert y_min >= 0


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
        from control_ofc.services.app_settings_service import AppSettingsService

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


_TEMP_KEY = "sensor:cpu0"
_RPM_KEY = "fan:openfan:ch00:rpm"
_ZERO_RPM_KEY = "fan:openfan:ch09:rpm"


def _seed_flat(history: HistoryStore, key: str, value: float, n: int = 10) -> None:
    """Append *n* points all equal to *value*, so the hover readout at any x is
    deterministic regardless of which sample the cursor lands on."""
    import time

    now = time.monotonic()
    for i in range(n):
        history._append(key, now - (n - i), value)


def _shown_chart(qtbot, seeded: dict[str, float]):
    """Build a chart, seed each key flat at its value, make every key visible, and
    give the widget real geometry (shown) — the hover hit-test needs a non-empty
    ``plot.sceneBoundingRect()`` to map the cursor into data coordinates."""
    history = HistoryStore()
    selection = SeriesSelectionModel()
    chart = TimelineChart(history, selection=selection)
    qtbot.addWidget(chart)
    for key, value in seeded.items():
        _seed_flat(history, key, value)
    selection.update_known_keys(list(seeded))
    for key in seeded:
        selection.set_visible(key, True)
    chart.resize(640, 480)
    with qtbot.waitExposed(chart):
        chart.show()
    chart.update_chart()
    return chart, history, selection


class TestCrosshairHover:
    """Real crosshair/hover *behaviour*, driven through ``_on_mouse_moved`` with a
    scene position — not mere existence checks."""

    def test_crosshair_shows_inside_and_hides_outside(self, qtbot):
        from PySide6.QtCore import QPointF

        chart, *_ = _shown_chart(qtbot, {_TEMP_KEY: 45.0})
        rect = chart._plot_widget.getPlotItem().sceneBoundingRect()
        chart._on_mouse_moved((rect.center(),))
        assert chart._crosshair_v.isVisible()
        # A point well outside the plot scene hides the crosshair + readout.
        chart._on_mouse_moved((QPointF(rect.right() + 500, rect.bottom() + 500),))
        assert not chart._crosshair_v.isVisible()
        assert not chart._hover_label.isVisible()

    def test_hover_lists_only_visible_series(self, qtbot, monkeypatch):
        chart, _h, selection = _shown_chart(qtbot, {_TEMP_KEY: 45.0, _RPM_KEY: 1200.0})
        selection.set_visible(_RPM_KEY, False)
        chart.update_chart()
        captured: list[str] = []
        monkeypatch.setattr(chart._hover_label, "setText", captured.append)
        rect = chart._plot_widget.getPlotItem().sceneBoundingRect()
        chart._on_mouse_moved((rect.center(),))
        assert captured, "hover readout should have been built for the visible series"
        text = captured[-1]
        assert "cpu0" in text and "45.0" in text  # the visible temp series is shown
        assert "ch00" not in text  # the hidden RPM series is omitted

    def test_hover_suppresses_zero_rpm(self, qtbot, monkeypatch):
        chart, *_ = _shown_chart(qtbot, {_RPM_KEY: 1200.0, _ZERO_RPM_KEY: 0.0})
        captured: list[str] = []
        monkeypatch.setattr(chart._hover_label, "setText", captured.append)
        rect = chart._plot_widget.getPlotItem().sceneBoundingRect()
        chart._on_mouse_moved((rect.center(),))
        assert captured
        text = captured[-1]
        assert "ch00" in text and "1200 RPM" in text  # live RPM shown
        assert "ch09" not in text  # the 0-RPM series is suppressed from the readout


class TestFanTableColumns:
    """Fan table has 4 columns (Colour removed in R42)."""

    def test_fan_table_column_count(self, qtbot, app_state):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)

        assert page._fan_table.columnCount() == 4


class TestSensorPanelColumns:
    """Sensor panel tree has 3 columns (name, value, colour swatch)."""

    def test_tree_column_count(self, qtbot, app_state):
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        selection = SeriesSelectionModel()
        panel = SensorSeriesPanel(selection, state=app_state)
        qtbot.addWidget(panel)

        assert panel._tree.columnCount() == 3
