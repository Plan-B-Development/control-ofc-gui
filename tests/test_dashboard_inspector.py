"""DEC-184 (was DEC-182): dashboard inspector — Sensors side panel.

Covers the Sensors-only container in isolation and its integration into the
dashboard page (default-by-width at first show, toggle, save/restore of the
splitter split, and the shared selection model), plus the re-homed active-warnings
dialog that replaced the former Warnings tab.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPushButton, QSplitter, QTabWidget, QWidget

from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.dashboard_inspector import DashboardInspector
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel


class TestDashboardInspectorWidget:
    """The Sensors-only container in isolation (plain stand-in panel)."""

    def _make(self, qtbot):
        sensors = QWidget()
        insp = DashboardInspector(sensors)
        qtbot.addWidget(insp)
        return insp, sensors

    def test_no_tabs_just_sensors_heading(self, qtbot):
        insp, _sensors = self._make(qtbot)
        # The tabbed Sensors/Events/Warnings structure (DEC-182) is gone (DEC-184).
        assert insp.findChildren(QTabWidget) == []
        heading = insp.findChild(QLabel, "Inspector_Heading")
        assert heading is not None
        assert heading.text() == "Sensors"

    def test_hosts_the_sensors_widget(self, qtbot):
        insp, sensors = self._make(qtbot)
        assert sensors.objectName() == "Inspector_Panel_sensors"
        assert insp.sensors_widget() is sensors


class TestInspectorDefaultByWidth:
    """3A: open/closed default decided once, on the first real width."""

    def test_default_expanded_on_wide(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page._apply_inspector_default(1400)
        assert page._inspector_shown is True
        assert page._inspector.isHidden() is False
        assert page._status_strip.inspector_toggle.text().startswith("▾")

    def test_default_collapsed_on_narrow(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page._apply_inspector_default(800)
        assert page._inspector_shown is False
        assert page._inspector.isHidden() is True
        assert page._status_strip.inspector_toggle.text().startswith("▸")

    def test_default_is_one_shot(self, qtbot, app_state):
        """Once applied, a later width never re-decides — the user owns it."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page._apply_inspector_default(800)  # collapse
        page._apply_inspector_default(1400)  # must be ignored
        assert page._inspector_shown is False


class TestInspectorToggle:
    def test_toggle_flips_visibility_and_button(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        assert page._inspector_shown is True  # coherent from build

        page._toggle_inspector()
        assert page._inspector_shown is False
        assert page._inspector.isHidden() is True
        assert page._status_strip.inspector_toggle.text().startswith("▸")

        page._toggle_inspector()
        assert page._inspector_shown is True
        assert page._inspector.isHidden() is False
        assert page._status_strip.inspector_toggle.text().startswith("▾")

    def test_collapse_saves_split_and_reopen_restores(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")

        page._set_inspector_shown(False)
        assert page._inspector_saved_sizes is not None
        assert len(page._inspector_saved_sizes) == 2

        saved = list(page._inspector_saved_sizes)
        page._set_inspector_shown(True)
        assert page._inspector.isHidden() is False
        # The saved split must be re-applied verbatim, not zeroed or clamped.
        assert h_splitter.sizes() == saved

    def test_toggle_button_click_flips_pane(self, qtbot, app_state):
        """End-to-end: clicking the strip button drives the whole
        clicked → inspector_toggle_clicked → _toggle_inspector chain (the
        non-hover accessibility path). A severed connection fails here."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        btn = page.findChild(QPushButton, "Inspector_Btn_toggle")
        assert btn is not None
        assert btn.toolTip() != ""  # affordance present

        assert page._inspector_shown is True
        btn.click()
        assert page._inspector_shown is False
        btn.click()
        assert page._inspector_shown is True


class TestInspectorSharesSelectionModel:
    def test_sensors_panel_shares_the_chart_selection_model(self, qtbot, app_state):
        """Toggling a series in the Sensors panel reflects on the chart because they
        are the *same* SeriesSelectionModel instance (DEC-181 contract preserved)."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        # Sensor panel lives inside the inspector and is still page._sensor_panel.
        panel = page._inspector.findChild(SensorSeriesPanel, "Inspector_Panel_sensors")
        assert panel is page._sensor_panel
        assert panel._selection is page._selection
        assert page._chart._selection is page._selection


class TestInspectorContent:
    def test_sensor_panel_hosted_no_event_or_warning_surfaces(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        assert page._inspector.findChild(QWidget, "Inspector_Panel_sensors") is not None
        # The former Events/Warnings tab pages are gone (DEC-184).
        assert page._inspector.findChild(QWidget, "Inspector_Tab_events") is None
        assert page._inspector.findChild(QWidget, "Inspector_Tab_warnings") is None

    def test_diagnostics_service_still_accepted_and_stored(self, qtbot, app_state):
        """DEC-111: the page accepts MainWindow's shared DiagnosticsService even
        though the dashboard no longer renders an event log (DEC-184)."""
        from control_ofc.services.diagnostics_service import DiagnosticsService

        diag = DiagnosticsService(app_state)
        page = DashboardPage(state=app_state, diagnostics_service=diag)
        qtbot.addWidget(page)
        assert page._diag is diag

    def test_diagnostics_service_falls_back_when_absent(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        assert page._diag is not None
