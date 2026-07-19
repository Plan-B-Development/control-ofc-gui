"""DEC-184 (was DEC-182): dashboard inspector — Sensors side panel.

Covers the Sensors-only container in isolation and its integration into the
dashboard page (default-by-width at first show, toggle, save/restore of the
splitter split, and the shared selection model), plus the re-homed active-warnings
dialog that replaced the former Warnings tab.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QSplitter, QTabWidget, QWidget

from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.dashboard_inspector import DashboardInspector


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
        assert heading.text() == "Thermal Sensors"  # DEC-213 rename

    def test_hosts_the_sensors_widget(self, qtbot):
        insp, sensors = self._make(qtbot)
        assert sensors.objectName() == "Inspector_Panel_sensors"
        assert insp.sensors_widget() is sensors


class TestInspectorAlwaysPresent:
    """DEC-222: the show/hide toggle lived on the removed status strip. The rail is
    now always mounted and the splitter handle is how the chart reclaims width, so
    there is no hidden state to restore and no one-shot width default."""

    def test_rail_is_mounted_and_visible_in_the_splitter(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        h_splitter = page.findChild(QSplitter, "Dashboard_Splitter_horizontal")
        assert h_splitter.widget(1) is page._inspector
        assert not page._inspector.isHidden()

    def test_no_toggle_state_survives(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        for attr in (
            "_inspector_shown",
            "_inspector_saved_sizes",
            "_inspector_default_applied",
            "_toggle_inspector",
            "_set_inspector_shown",
        ):
            assert not hasattr(page, attr), attr
