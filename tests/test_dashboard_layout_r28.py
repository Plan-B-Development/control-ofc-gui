"""R28: Dashboard layout structure, splitter hierarchy, and fan table column tests.

Verifies the DEC-213 inverted splitter nesting (vertical-outer, horizontal-inner):
the chart is the full-width Telemetry Stage on top, and the Fan Array cards + the
right-rail inspector share the row below inside the inner horizontal splitter. Both
splitter objectNames are preserved so the inspector toggle contract is unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QScrollArea, QSplitter, QTableWidget

from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.dashboard_inspector import DashboardInspector
from control_ofc.ui.widgets.fan_zone_card import FanZoneGrid
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel
from control_ofc.ui.widgets.summary_card import SummaryCard
from control_ofc.ui.widgets.timeline_chart import TimelineChart


class TestSplitterHierarchy:
    """Dashboard uses v_splitter(Telemetry Stage(chart), h_splitter(Fan Array,
    inspector)) — the DEC-213 inversion of the old h(v(chart, fans), inspector);
    the raw fan table is re-homed to a collapsed expander (DEC-179, 4A)."""

    def test_horizontal_splitter_exists(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        assert h_splitter is not None
        assert h_splitter.orientation() == Qt.Orientation.Horizontal

    def test_horizontal_splitter_inside_vertical(self, qtbot, app_state):
        """DEC-213 inversion: the h_splitter is now the *bottom* child of the outer
        vertical splitter (was the other way round)."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        v_splitter = page.findChild(QSplitter, "Dashboard_Splitter_vertical")
        assert v_splitter is not None
        assert v_splitter.orientation() == Qt.Orientation.Vertical
        assert h_splitter.orientation() == Qt.Orientation.Horizontal
        # h_splitter is the bottom child of the outer v_splitter
        assert v_splitter.widget(1) is h_splitter

    def test_inspector_is_right_pane(self, qtbot, app_state):
        """DEC-182: the right pane is the tabbed inspector; the sensor panel is its
        Sensors tab, still reachable as ``page._sensor_panel``."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        right_child = h_splitter.widget(1)
        assert isinstance(right_child, DashboardInspector)
        assert page._sensor_panel in right_child.findChildren(SensorSeriesPanel)

    def test_chart_top_fans_bottom_left(self, qtbot, app_state):
        """DEC-213: the chart is the full-width Telemetry Stage (v_splitter top); the
        fan-zone cards sit bottom-left in the inner h_splitter, still a collapsible
        'Fan zones' section wrapping a scroll area + FanZoneGrid (DEC-187)."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        v_splitter = page.findChild(QSplitter, "Dashboard_Splitter_vertical")
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        assert v_splitter.count() == 2
        # Telemetry Stage (top of v_splitter) hosts the reused chart.
        telemetry = v_splitter.widget(0)
        assert telemetry.objectName() == "Dashboard_Section_telemetry"
        assert telemetry.findChild(TimelineChart) is not None
        # Fan Array pane (left of the inner h_splitter) hosts the fan-zone section.
        fan_pane = h_splitter.widget(0)
        section = fan_pane.findChild(CollapsibleSection, "Dashboard_Section_fanZones")
        assert section is not None
        scroll = section.findChild(QScrollArea, "Dashboard_ScrollArea_fanZones")
        assert scroll is not None
        assert isinstance(scroll.widget(), FanZoneGrid)

    def test_raw_fan_table_rehomed_to_collapsed_expander(self, qtbot, app_state):
        """The raw fan table is intact but lives inside a collapsed 'Raw fan
        data' expander, not the splitter (4A)."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        section = page.findChild(CollapsibleSection, "Dashboard_Section_rawFanData")
        assert section is not None
        assert section.is_expanded() is False  # collapsed by default — cards are primary
        # The table is a descendant of the expander, and still the page's _fan_table.
        assert page._fan_table is not None
        assert page._fan_table in section.findChildren(QTableWidget)

    def test_horizontal_splitter_not_collapsible(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        assert not h_splitter.isCollapsible(0)
        assert not h_splitter.isCollapsible(1)


class TestFanTableColumns:
    """Fan table retains all 4 columns with correct resize modes."""

    def test_fan_table_has_four_columns(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        table = page._fan_table
        assert table.columnCount() == 4  # Label, Source, RPM, PWM%

    def test_fan_table_column_headers(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        table = page._fan_table
        headers = [table.horizontalHeaderItem(i).text() for i in range(4)]
        assert headers == ["Label", "Source", "RPM", "PWM%"]

    def test_columns_interactive_resizable(self, qtbot, app_state):
        """All fan table columns use Interactive mode for user-resizable columns."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        header = page._fan_table.horizontalHeader()
        for col in range(4):
            assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
        assert header.stretchLastSection() is True

    def test_minimum_section_size_set(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        header = page._fan_table.horizontalHeader()
        assert header.minimumSectionSize() >= 30  # Colour column uses 30px min


class TestSummaryCardTypography:
    """Summary cards have larger fonts and transparent label background."""

    def test_value_label_uses_card_value_class(self, qtbot):
        """Value label uses CardValue CSS class for theme-driven font size."""
        card = SummaryCard("CPU Temp", "55°C")
        qtbot.addWidget(card)
        assert card._value_label.property("class") == "CardValue"

    def test_title_label_has_transparent_background(self, qtbot):
        card = SummaryCard("CPU Temp", "55°C")
        qtbot.addWidget(card)
        style = card._title_label.styleSheet()
        assert "transparent" in style.lower()

    def test_value_label_has_transparent_background(self, qtbot):
        card = SummaryCard("CPU Temp", "55°C")
        qtbot.addWidget(card)
        style = card._value_label.styleSheet()
        assert "transparent" in style.lower()

    def test_card_max_height_accommodates_larger_fonts(self, qtbot):
        card = SummaryCard("CPU Temp", "55°C")
        qtbot.addWidget(card)
        assert card.maximumHeight() >= 100
