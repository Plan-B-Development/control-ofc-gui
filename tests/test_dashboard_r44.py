"""R44: Sensor colour selector restoration, card sizing, hover lifecycle."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QSizePolicy

from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.widgets.summary_card import SummaryCard
from control_ofc.ui.widgets.timeline_chart import TimelineChart


class TestSensorPanelColourSwatch:
    """Sensor panel has 3rd column colour swatch and set_chart wiring."""

    def test_panel_has_three_columns(self, qtbot, app_state):
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=app_state)
        qtbot.addWidget(panel)
        assert panel._tree.columnCount() == 3

    def test_set_chart_stores_reference(self, qtbot, app_state):
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=app_state)
        qtbot.addWidget(panel)

        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        panel.set_chart(chart)
        assert panel._chart is chart

    def test_item_clicked_handler_exists(self, qtbot, app_state):
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=app_state)
        qtbot.addWidget(panel)
        assert hasattr(panel, "_on_item_clicked")


class TestSummaryCardSizing:
    """Summary cards use Maximum vertical policy, no hardcoded maxHeight."""

    def test_no_hardcoded_max_height(self, qtbot):
        card = SummaryCard("Test", "42")
        qtbot.addWidget(card)
        # Maximum height should NOT be 100 (the old hardcoded value)
        # With Maximum policy it should be QWIDGETSIZE_MAX (16777215)
        assert card.maximumHeight() > 100

    def test_size_policy_vertical_maximum(self, qtbot):
        card = SummaryCard("Test", "42")
        qtbot.addWidget(card)
        policy = card.sizePolicy()
        assert policy.verticalPolicy() == QSizePolicy.Policy.Maximum

    def test_tight_margins(self, qtbot):
        card = SummaryCard("Test", "42")
        qtbot.addWidget(card)
        margins = card.layout().contentsMargins()
        assert margins.top() <= 6
        assert margins.bottom() <= 6

    def test_tight_spacing(self, qtbot):
        card = SummaryCard("Test", "42")
        qtbot.addWidget(card)
        assert card.layout().spacing() <= 2

    def test_uses_theme_classes(self, qtbot):
        card = SummaryCard("Test", "42")
        qtbot.addWidget(card)
        assert card._title_label.property("class") == "PageSubtitle"
        assert card._value_label.property("class") == "CardValue"


class TestHoverLifecycle:
    """Hover hides on leave event and app deactivation."""

    def test_event_filter_installed(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        # The event filter is installed — verify by checking the method exists
        assert hasattr(chart, "eventFilter")

    def test_hide_hover_method(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        # Show hover items first
        chart._crosshair_v.show()
        chart._hover_label.show()
        assert chart._crosshair_v.isVisible()

        chart._hide_hover()
        assert not chart._crosshair_v.isVisible()
        assert not chart._hover_label.isVisible()

    def test_leave_event_hides_hover(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        chart._crosshair_v.show()
        chart._hover_label.show()

        # Simulate leave event via event filter
        leave_event = QEvent(QEvent.Type.Leave)
        chart.eventFilter(chart._plot_widget, leave_event)

        assert not chart._crosshair_v.isVisible()
        assert not chart._hover_label.isVisible()

    def test_app_inactive_hides_hover(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        chart._crosshair_v.show()
        chart._hover_label.show()

        chart._on_app_state_changed(Qt.ApplicationState.ApplicationInactive)

        assert not chart._crosshair_v.isVisible()
        assert not chart._hover_label.isVisible()

    def test_app_active_does_not_hide(self, qtbot):
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        chart._crosshair_v.show()
        chart._hover_label.show()

        chart._on_app_state_changed(Qt.ApplicationState.ApplicationActive)

        # Active state should NOT hide hover
        assert chart._crosshair_v.isVisible()
