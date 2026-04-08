"""R28: Dashboard layout structure, splitter hierarchy, and fan table column tests.

Verifies that the splitter restructure (horizontal-outer, vertical-inner) produces
the correct widget hierarchy: graph and fan table share the left-column width, and
the sensor panel spans the full height as a sibling of the left vertical splitter.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QSplitter, QTableWidget

from onlyfans.ui.pages.dashboard_page import DashboardPage
from onlyfans.ui.widgets.sensor_series_panel import SensorSeriesPanel
from onlyfans.ui.widgets.summary_card import SummaryCard
from onlyfans.ui.widgets.timeline_chart import TimelineChart


class TestSplitterHierarchy:
    """Dashboard uses h_splitter(v_splitter(chart, table), sensor_panel)."""

    def test_horizontal_splitter_exists(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        assert h_splitter is not None
        assert h_splitter.orientation() == Qt.Orientation.Horizontal

    def test_vertical_splitter_inside_horizontal(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        v_splitter = page.findChild(QSplitter, "Dashboard_Splitter_vertical")
        assert v_splitter is not None
        assert v_splitter.orientation() == Qt.Orientation.Vertical
        # v_splitter is the left child of h_splitter
        assert h_splitter.widget(0) is v_splitter

    def test_sensor_panel_is_right_pane(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        right_child = h_splitter.widget(1)
        assert isinstance(right_child, SensorSeriesPanel)

    def test_chart_and_table_share_left_column(self, qtbot, app_state):
        """Chart and fan table are both children of the vertical splitter."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        v_splitter = page.findChild(QSplitter, "Dashboard_Splitter_vertical")
        assert v_splitter.count() == 2
        assert isinstance(v_splitter.widget(0), TimelineChart)
        assert isinstance(v_splitter.widget(1), QTableWidget)

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

    def test_all_columns_stretch_evenly(self, qtbot, app_state):
        """All 4 fan table columns use Stretch for even spacing (R55)."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        header = page._fan_table.horizontalHeader()
        for col in range(4):
            assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Stretch

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
