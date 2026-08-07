"""R44: Sensor colour selector restoration, card sizing, hover lifecycle."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt

from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.widgets.timeline_chart import TimelineChart


class TestSensorPanelColourSwatch:
    """Sensor panel has 3rd column colour swatch and set_chart wiring."""

    def test_panel_has_three_columns(self, qtbot, app_state):
        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=app_state)
        qtbot.addWidget(panel)
        assert panel._tree.columnCount() == 3

    def test_set_chart_reference_is_used_for_swatch(self, qtbot, app_state):
        """set_chart isn't merely stored — the colour-swatch column is painted
        from the chart's color_for_key, so the stored reference is consulted."""
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QTreeWidgetItem

        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=app_state)
        qtbot.addWidget(panel)
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        panel.set_chart(chart)

        item = QTreeWidgetItem(["CPU", "", ""])
        panel._set_color_swatch(item, "sensor:cpu0")
        expected = QColor(chart.color_for_key("sensor:cpu0")).name()
        assert item.background(2).color().name() == expected

    def test_color_swatch_click_applies_chosen_color(self, qtbot, app_state, monkeypatch):
        """Clicking the colour swatch (column 2) opens the picker and applies the
        chosen colour to the chart series — real wiring, not just method presence."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog, QTreeWidgetItem

        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=app_state)
        qtbot.addWidget(panel)
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        panel.set_chart(chart)

        item = QTreeWidgetItem(["CPU", "", ""])
        item.setData(0, Qt.ItemDataRole.UserRole, "sensor:cpu0")
        # Accept the (non-modal) dialog with a chosen colour.
        monkeypatch.setattr(QColorDialog, "exec", lambda self: 1)
        monkeypatch.setattr(QColorDialog, "currentColor", lambda self: QColor("#abcdef"))

        panel._on_item_clicked(item, 2)
        assert chart.color_for_key("sensor:cpu0") == "#abcdef"

    def test_color_pick_persists_through_settings_validation(
        self, qtbot, app_state, settings_service, monkeypatch
    ):
        """DEC-237: the picker must persist via AppSettingsService.update().

        It used to mutate ``settings.series_colors`` in place and then call
        ``save()``, writing straight past the DEC-137 coercion layer. The
        discriminator is a pre-existing invalid entry: ``update()`` re-runs
        ``_as_color_dict`` over the whole map and drops it, whereas the old
        mutate-then-save path persisted it verbatim.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog, QTreeWidgetItem

        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=app_state)
        qtbot.addWidget(panel)
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        panel.set_chart(chart, settings_service)

        # Poison the map with a value the coercion layer must reject...
        settings_service.settings.series_colors["sensor:bogus"] = "javascript:alert(1)"
        # ...and seed a VALID neighbour. Without this the test cannot tell
        # "merge then validate" from "replace everything": swapping the merge for
        # `merged = {}` destroys every other user-chosen colour on each pick and
        # still satisfies both original assertions.
        settings_service.settings.series_colors["sensor:gpu0"] = "#123456"

        item = QTreeWidgetItem(["CPU", "", ""])
        item.setData(0, Qt.ItemDataRole.UserRole, "sensor:cpu0")
        monkeypatch.setattr(QColorDialog, "exec", lambda self: 1)
        monkeypatch.setattr(QColorDialog, "currentColor", lambda self: QColor("#abcdef"))

        panel._on_item_clicked(item, 2)

        stored = settings_service.settings.series_colors
        assert stored["sensor:cpu0"] == "#abcdef"
        assert "sensor:bogus" not in stored, "invalid entry survived — update() was bypassed"
        assert stored.get("sensor:gpu0") == "#123456", (
            "picking one colour must not discard the others — the write must merge"
        )

    def test_color_pick_without_settings_service_is_safe(self, qtbot, app_state, monkeypatch):
        """set_chart() leaves settings_service None by default — must not raise."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog, QTreeWidgetItem

        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=app_state)
        qtbot.addWidget(panel)
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        panel.set_chart(chart)  # no settings service

        item = QTreeWidgetItem(["CPU", "", ""])
        item.setData(0, Qt.ItemDataRole.UserRole, "sensor:cpu0")
        monkeypatch.setattr(QColorDialog, "exec", lambda self: 1)
        monkeypatch.setattr(QColorDialog, "currentColor", lambda self: QColor("#abcdef"))

        panel._on_item_clicked(item, 2)
        assert chart.color_for_key("sensor:cpu0") == "#abcdef"

    def test_click_off_color_column_opens_no_picker(self, qtbot, app_state, monkeypatch):
        """A click outside the colour column (column 2) must not open the picker."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QColorDialog, QTreeWidgetItem

        from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel

        panel = SensorSeriesPanel(SeriesSelectionModel(), state=app_state)
        qtbot.addWidget(panel)
        chart = TimelineChart(HistoryStore())
        qtbot.addWidget(chart)
        panel.set_chart(chart)

        item = QTreeWidgetItem(["CPU", "", ""])
        item.setData(0, Qt.ItemDataRole.UserRole, "sensor:cpu0")
        opened: list[int] = []
        monkeypatch.setattr(QColorDialog, "exec", lambda self: opened.append(1) or 1)
        panel._on_item_clicked(item, 0)  # not the colour column
        assert opened == []


class TestHoverLifecycle:
    """Hover hides on leave event and app deactivation."""

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
