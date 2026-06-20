"""DEC-182: dashboard inspector — tabbed Sensors/Events/Warnings side panel.

Covers the composition widget in isolation and its integration into the
dashboard page (default-by-width at first show, toggle, save/restore of the
splitter split, and the shared selection model).
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QSplitter, QTabWidget, QWidget

from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.dashboard_inspector import DashboardInspector
from control_ofc.ui.widgets.sensor_series_panel import SensorSeriesPanel


class TestDashboardInspectorWidget:
    """The composition widget in isolation (plain stand-in tab pages)."""

    def _make(self, qtbot):
        sensors, events, warnings = QWidget(), QWidget(), QWidget()
        insp = DashboardInspector(sensors, events, warnings)
        qtbot.addWidget(insp)
        return insp, sensors, events, warnings

    def test_three_tabs_with_labels(self, qtbot):
        insp, *_ = self._make(qtbot)
        tabs = insp.findChild(QTabWidget, "Inspector_Tabs")
        assert tabs.count() == 3
        assert [tabs.tabText(i) for i in range(3)] == ["Sensors", "Events", "Warnings"]

    def test_tab_pages_get_inspector_objectnames(self, qtbot):
        _insp, sensors, events, warnings = self._make(qtbot)
        assert sensors.objectName() == "Inspector_Tab_sensors"
        assert events.objectName() == "Inspector_Tab_events"
        assert warnings.objectName() == "Inspector_Tab_warnings"

    def test_show_warnings_tab_selects_warnings(self, qtbot):
        insp, _sensors, _events, warnings = self._make(qtbot)
        tabs = insp.findChild(QTabWidget, "Inspector_Tabs")
        tabs.setCurrentIndex(0)
        insp.show_warnings_tab()
        assert tabs.currentWidget() is warnings


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
    def test_sensors_tab_shares_the_chart_selection_model(self, qtbot, app_state):
        """Toggling a series in the Sensors tab reflects on the chart because they
        are the *same* SeriesSelectionModel instance (DEC-181 contract preserved)."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        # Sensor panel lives inside the inspector and is still page._sensor_panel.
        panel = page._inspector.findChild(SensorSeriesPanel, "Inspector_Tab_sensors")
        assert panel is page._sensor_panel
        assert panel._selection is page._selection
        assert page._chart._selection is page._selection


class TestInspectorTabContent:
    def test_tabs_carry_expected_objectnames(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        for name in ("Inspector_Tab_sensors", "Inspector_Tab_events", "Inspector_Tab_warnings"):
            assert page._inspector.findChild(QWidget, name) is not None

    def test_events_tab_falls_back_to_own_diag(self, qtbot, app_state):
        """No injected diag → the page builds its own DiagnosticsService and the
        Events tab's EventLogView reads from that same deque (mirrors MainWindow)."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        assert page._inspector.findChild(QWidget, "Inspector_Tab_events") is not None
        assert page._diag is not None
        assert page._event_log_view._diag is page._diag

    def test_injected_diag_is_shared_with_events_tab(self, qtbot, app_state):
        """The whole point of the ctor kwarg: the dashboard EventLogView must read
        the SAME shared deque MainWindow passes (DEC-111), not a private copy."""
        from control_ofc.services.diagnostics_service import DiagnosticsService

        diag = DiagnosticsService(app_state)
        page = DashboardPage(state=app_state, diagnostics_service=diag)
        qtbot.addWidget(page)
        assert page._diag is diag
        assert page._event_log_view._diag is diag
