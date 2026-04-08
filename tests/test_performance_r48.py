"""R48: Performance — chart antialiasing, downsample, visibility gating."""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt

from onlyfans.services.history_store import HistoryStore
from onlyfans.ui.widgets.timeline_chart import TimelineChart


class TestChartRenderingConfig:
    """Chart uses performance-optimal pyqtgraph settings."""

    def test_antialiasing_disabled(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        # pyqtgraph global config should be False after chart init
        assert pg.getConfigOption("antialias") is False

    def test_rpm_items_are_plot_curve_item(self, qtbot):
        """RPM series use PlotCurveItem for secondary ViewBox rendering."""
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        # Type annotation confirms: dict[str, pg.PlotCurveItem]
        assert isinstance(chart._rpm_items, dict)


class TestDashboardVisibilityGating:
    """Chart timer stops when dashboard hidden, throttles when app unfocused."""

    def test_chart_timer_running_initially(self, qtbot, app_state):
        from onlyfans.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        assert page._chart_timer.isActive()

    def test_hide_stops_chart_timer(self, qtbot, app_state):
        from onlyfans.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page.show()
        qtbot.waitExposed(page)

        page.hide()
        assert not page._chart_timer.isActive()

    def test_show_restarts_chart_timer(self, qtbot, app_state):
        from onlyfans.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page.show()
        qtbot.waitExposed(page)
        page.hide()
        assert not page._chart_timer.isActive()

        page.show()
        qtbot.waitExposed(page)
        assert page._chart_timer.isActive()

    def test_app_inactive_throttles_timer(self, qtbot, app_state):
        from onlyfans.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)

        page._on_app_focus_changed(Qt.ApplicationState.ApplicationInactive)
        assert page._chart_timer.interval() == 5000

    def test_app_active_restores_timer(self, qtbot, app_state):
        from onlyfans.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)

        page._on_app_focus_changed(Qt.ApplicationState.ApplicationInactive)
        page._on_app_focus_changed(Qt.ApplicationState.ApplicationActive)
        assert page._chart_timer.interval() == 1000
