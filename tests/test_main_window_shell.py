"""DEC-208: main-window shell integration.

The 8-entry sidebar routes each interim entry to the right (page, sub-tab), the
ribbon + footer are wired in, and the ribbon Alerts indicator opens Logs. The
preserved-contract checks live in test_ui_clicks / test_status_strip.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from control_ofc.constants import (
    PAGE_HARDWARE,
    PAGE_LOGS,
    PAGE_OVERVIEW,
    PAGE_SYSTEM_STATE,
    PAGE_THEME,
)
from control_ofc.ui.main_window import MainWindow


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


def _click_nav(qtbot, window, object_name):
    btn = window.findChild(QPushButton, object_name)
    assert btn is not None, object_name
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)


def test_shell_has_ribbon_and_footer(window):
    assert window.status_ribbon.objectName() == "StatusRibbon_Root"
    assert window.footer.objectName() == "StatusFooter_Root"


def test_overview_routes_to_overview_page(qtbot, window):
    # DEC-209: Overview now opens its own page, not the Diagnostics Overview tab.
    _click_nav(qtbot, window, "NavButton_Overview")
    assert window.page_stack.currentIndex() == PAGE_OVERVIEW
    assert window.page_stack.currentWidget() is window.overview_page


def test_system_state_routes_to_system_state_page(qtbot, window):
    # DEC-211: System State now opens its own page, not the Diagnostics tab.
    _click_nav(qtbot, window, "NavButton_SystemState")
    assert window.page_stack.currentIndex() == PAGE_SYSTEM_STATE
    assert window.page_stack.currentWidget() is window.system_state_page


def test_hardware_routes_to_hardware_page(qtbot, window):
    # DEC-212: Hardware now opens its own page, not the Diagnostics Readiness tab.
    _click_nav(qtbot, window, "NavButton_Hardware")
    assert window.page_stack.currentIndex() == PAGE_HARDWARE
    assert window.page_stack.currentWidget() is window.hardware_page


def test_theme_routes_to_theme_page(qtbot, window):
    """DEC-215: Theme is now its own page (PAGE_THEME), not a Settings sub-tab."""
    _click_nav(qtbot, window, "NavButton_Theme")
    assert window.page_stack.currentIndex() == PAGE_THEME
    assert window.page_stack.currentWidget() is window.theme_page


def test_logs_routes_to_logs_page(qtbot, window):
    # DEC-210: Logs now opens its own page, not the Diagnostics Event Log tab.
    _click_nav(qtbot, window, "NavButton_Logs")
    assert window.page_stack.currentIndex() == PAGE_LOGS
    assert window.page_stack.currentWidget() is window.logs_page


def test_ribbon_alerts_opens_logs(window):
    window.status_ribbon.alerts_clicked.emit()
    assert window.page_stack.currentIndex() == PAGE_LOGS
    assert window.page_stack.currentWidget() is window.logs_page


def test_sidebar_profile_combo_populated(window, profile_service):
    assert window.sidebar.profile_combo.count() == len(profile_service.profiles)
