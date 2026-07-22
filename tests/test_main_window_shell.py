"""DEC-208: main-window shell integration.

The 8-entry sidebar routes each interim entry to the right (page, sub-tab), the
ribbon + footer are wired in, and the ribbon Alerts indicator opens Logs. The
preserved-contract checks live in test_ui_clicks (test_status_strip went with the
status strip itself in DEC-222; the footer's re-homed indicators are covered by
test_status_footer).
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


def test_window_object_name_anchors_the_base_fill(window):
    """DEC-225: the ``#MainWindow`` QSS rule paints the app's base background now
    the blanket ``QWidget`` fill is gone. If this objectName is dropped the window
    renders unpainted while the stylesheet still parses cleanly, so the widget-side
    half of the fix needs its own guard."""
    assert window.objectName() == "MainWindow"


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


def test_startup_restores_divergent_page_and_highlights_nav(
    qtbot, app_state, profile_service, settings_service
):
    """DEC-216: a persisted ``last_page_index`` at a *divergent* NAV/PAGE entry
    (Logs: PAGE_LOGS=4 but NAV_LOGS=7) must, at construction, both show the right
    stack page AND highlight the right sidebar entry. ``_resolve_startup_page`` and
    ``select_page`` are each unit-tested; this pins their composition in the
    constructor for a secondary page (audit Rank 3)."""
    from control_ofc.constants import NAV_LOGS

    settings_service.settings.restore_last_page = True
    settings_service.settings.last_page_index = PAGE_LOGS

    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)

    assert win.page_stack.currentIndex() == PAGE_LOGS
    assert win.sidebar._group.checkedId() == NAV_LOGS


def test_warning_count_propagates_to_ribbon_and_footer(window, app_state):
    """DEC-208: ``AppState.warning_count_changed`` must drive BOTH the ribbon alert
    badge and the footer health rollup through the live MainWindow wiring. The two
    setters are unit-tested in isolation; this pins the cross-component chain (audit
    Rank 5). ``isHidden()`` is used (not ``isVisible()``) so the assertions don't
    depend on the never-shown window's ancestor visibility."""
    # No warnings → ribbon badge hidden, footer nominal.
    assert window.status_ribbon._alert_badge.isHidden() is True
    assert window.footer._health_label.text() == "All systems nominal"

    # One warning propagates to both surfaces.
    app_state.add_warning("warning", "test", "sensor stale")
    assert window.status_ribbon._alert_badge.isHidden() is False
    assert window.footer._health_label.text() == "1 warning"

    # Clearing it reverts both.
    app_state.clear_warnings()
    assert window.status_ribbon._alert_badge.isHidden() is True
    assert window.footer._health_label.text() == "All systems nominal"


def test_close_stops_the_poll_age_ticker(qtbot):
    """DEC-222: the poll-age ticker writes into the footer every second. If it
    outlived closeEvent, a tick could land on an already-deleted widget during
    teardown — which is why every other timer here is stopped explicitly."""
    from control_ofc.ui.main_window import MainWindow

    window = MainWindow(demo_mode=True)
    qtbot.addWidget(window)
    assert window._poll_age_timer.isActive()

    window.close()
    assert not window._poll_age_timer.isActive()
