"""Dashboard layout structure and splitter hierarchy (DEC-222 rebuild).

The graph is the primary component: it is the full-width Telemetry Stage on top
of the outer vertical splitter, with the per-control fan cards and the Thermal
Sensors rail sharing the row below inside the inner horizontal splitter. Both
splitter objectNames are preserved from the DEC-213 layout they replace.

DEC-222 removed the summary cards, the Fan Array / Fan Zone sections and the raw
fan-data expander; the suites covering those live on only as the absence checks
in :class:`TestRetiredSurfacesAreGone`, which exist so a revert or a stray
re-introduction is caught rather than silently shipped.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QSplitter, QTableWidget

from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.dashboard_inspector import DashboardInspector
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel
from control_ofc.ui.widgets.timeline_chart import TimelineChart


class TestSplitterHierarchy:
    """v_splitter(Telemetry Stage(chart), h_splitter(fan cards, sensors rail))."""

    def test_horizontal_splitter_exists(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        assert h_splitter is not None
        assert h_splitter.orientation() == Qt.Orientation.Horizontal

    def test_horizontal_splitter_inside_vertical(self, qtbot, app_state):
        """The h_splitter is the *bottom* child of the outer vertical splitter, so
        the graph keeps the full width at the top."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        v_splitter = page.findChild(QSplitter, "Dashboard_Splitter_vertical")
        assert v_splitter is not None
        assert v_splitter.orientation() == Qt.Orientation.Vertical
        assert v_splitter.widget(1) is h_splitter

    def test_inspector_is_right_pane(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        right_child = h_splitter.widget(1)
        assert isinstance(right_child, DashboardInspector)
        assert page._sensor_panel in right_child.findChildren(SensorSeriesPanel)

    def test_graph_is_the_primary_top_component(self, qtbot, app_state):
        """DEC-222: the telemetry graph is the top-level Dashboard component, and
        it gets the larger share of the vertical split."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        v_splitter = page.findChild(QSplitter, "Dashboard_Splitter_vertical")
        assert v_splitter.count() == 2
        telemetry = v_splitter.widget(0)
        assert telemetry.objectName() == "Dashboard_Section_telemetry"
        assert telemetry.findChild(TimelineChart) is not None
        assert v_splitter.sizes()[0] > v_splitter.sizes()[1]

    def test_fan_cards_pane_is_bottom_left(self, qtbot, app_state):
        """The fan cards sit bottom-left, in a scroll area so a long collection
        scrolls rather than crushing the cards."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        fan_pane = h_splitter.widget(0)
        assert fan_pane.objectName() == "Dashboard_Pane_fanCards"
        scroll = fan_pane.findChild(QScrollArea, "Dashboard_ScrollArea_fanCards")
        assert scroll is not None
        assert scroll.widget() is page._fan_cards_host

    def test_horizontal_splitter_not_collapsible(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        assert not h_splitter.isCollapsible(0)
        assert not h_splitter.isCollapsible(1)


class TestRetiredSurfacesAreGone:
    """DEC-222 removals. These pin the *absence* of surfaces that were deleted, so
    a partial revert cannot quietly restore a duplicate fan display."""

    def test_no_raw_fan_table(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        assert page.findChildren(QTableWidget) == []
        assert not hasattr(page, "_fan_table")

    def test_no_fan_zone_or_fan_array_sections(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        for name in (
            "Dashboard_Section_fanZones",
            "Dashboard_ScrollArea_fanZones",
            "Dashboard_Pane_fanArray",
            "Dashboard_Section_rawFanData",
        ):
            assert page.findChild(object, name) is None, name

    def test_no_summary_cards(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        for attr in ("_cpu_card", "_gpu_card", "_mb_card", "_fans_card"):
            assert not hasattr(page, attr), attr

    def test_no_status_strip_quick_actions_or_alerts(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        for attr in ("_status_strip", "_quick_actions", "_alerts"):
            assert not hasattr(page, attr), attr
