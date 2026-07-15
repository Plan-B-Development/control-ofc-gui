"""DEC-208: the 8-entry grouped sidebar nav + active-profile selector.

Only four QStackedWidget pages exist during the staged migration, so the sidebar
routes each entry to a ``(page_id, sub_tab)`` pair. Crucially the four primary
entries keep ``nav_id == page_id`` so the historical nav→stack contract holds.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QPushButton

from control_ofc.constants import (
    NAV_HARDWARE,
    NAV_LOGS,
    NAV_OVERVIEW,
    NAV_SYSTEM_STATE,
    PAGE_CONTROLS,
    PAGE_DASHBOARD,
    PAGE_HARDWARE,
    PAGE_LOGS,
    PAGE_OVERVIEW,
    PAGE_SETTINGS,
    PAGE_SYSTEM_STATE,
    PAGE_THEME,
)
from control_ofc.ui.sidebar import Sidebar

_EXPECTED_NAV_OBJECTS = [
    "NavButton_Dashboard",
    "NavButton_Overview",
    "NavButton_Controls",
    "NavButton_SystemState",
    "NavButton_Hardware",
    "NavButton_Settings",
    "NavButton_Theme",
    "NavButton_Logs",
    "NavButton_About",
]


def test_all_eight_nav_buttons_plus_about_present(qtbot):
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    for obj in _EXPECTED_NAV_OBJECTS:
        assert sidebar.findChild(QPushButton, obj) is not None, obj


def test_default_selection_is_dashboard(qtbot):
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    assert sidebar._group.checkedId() == PAGE_DASHBOARD


def test_controls_nav_maps_to_controls_page(qtbot):
    # Preserved contract: Controls activates PAGE_CONTROLS with no sub-tab.
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    btn = sidebar.findChild(QPushButton, "NavButton_Controls")
    with qtbot.waitSignal(sidebar.nav_activated) as blocker:
        btn.click()
    assert blocker.args == [PAGE_CONTROLS, -1]
    assert sidebar._group.checkedId() == PAGE_CONTROLS


def test_secondary_entries_route_to_page_and_subtab(qtbot):
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    cases = {
        # DEC-215: Settings + Theme are now standalone pages (no sub-tab).
        "NavButton_Settings": (PAGE_SETTINGS, -1),
        "NavButton_Theme": (PAGE_THEME, -1),
    }
    for obj, expected in cases.items():
        btn = sidebar.findChild(QPushButton, obj)
        with qtbot.waitSignal(sidebar.nav_activated) as blocker:
            btn.click()
        assert tuple(blocker.args) == expected, obj


def test_overview_is_its_own_page(qtbot):
    # DEC-209: Overview re-homed to PAGE_OVERVIEW with no sub-tab.
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    btn = sidebar.findChild(QPushButton, "NavButton_Overview")
    with qtbot.waitSignal(sidebar.nav_activated) as blocker:
        btn.click()
    assert blocker.args == [PAGE_OVERVIEW, -1]
    assert sidebar._group.checkedId() == NAV_OVERVIEW


def test_logs_is_its_own_page(qtbot):
    # DEC-210: Logs re-homed to PAGE_LOGS with no sub-tab (was the Diagnostics
    # Event Log sub-tab).
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    btn = sidebar.findChild(QPushButton, "NavButton_Logs")
    with qtbot.waitSignal(sidebar.nav_activated) as blocker:
        btn.click()
    assert blocker.args == [PAGE_LOGS, -1]
    assert sidebar._group.checkedId() == NAV_LOGS


def test_system_state_is_its_own_page(qtbot):
    # DEC-211: System State re-homed to PAGE_SYSTEM_STATE with no sub-tab (was
    # the Diagnostics Troubleshooting sub-tab).
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    btn = sidebar.findChild(QPushButton, "NavButton_SystemState")
    with qtbot.waitSignal(sidebar.nav_activated) as blocker:
        btn.click()
    assert blocker.args == [PAGE_SYSTEM_STATE, -1]
    assert sidebar._group.checkedId() == NAV_SYSTEM_STATE


def test_hardware_is_its_own_page(qtbot):
    # DEC-212: Hardware re-homed to PAGE_HARDWARE with no sub-tab (was the
    # Diagnostics Readiness sub-tab).
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    btn = sidebar.findChild(QPushButton, "NavButton_Hardware")
    with qtbot.waitSignal(sidebar.nav_activated) as blocker:
        btn.click()
    assert blocker.args == [PAGE_HARDWARE, -1]
    assert sidebar._group.checkedId() == NAV_HARDWARE


def test_select_page_finds_first_entry_by_page(qtbot):
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    sidebar.select_page(PAGE_SETTINGS)  # nav_id still == page_id here
    assert sidebar._group.checkedId() == PAGE_SETTINGS
    sidebar.select_page(PAGE_OVERVIEW)
    assert sidebar._group.checkedId() == NAV_OVERVIEW
    # No NavItem routes to PAGE_DIAGNOSTICS anymore — the four config entries all
    # have their own pages now.
    sidebar.select_page(PAGE_LOGS)
    assert sidebar._group.checkedId() == NAV_LOGS
    sidebar.select_page(PAGE_SYSTEM_STATE)
    assert sidebar._group.checkedId() == NAV_SYSTEM_STATE
    sidebar.select_page(PAGE_HARDWARE)
    assert sidebar._group.checkedId() == NAV_HARDWARE


def test_select_nav_highlights_secondary_entry(qtbot):
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    sidebar.select_nav(NAV_HARDWARE)
    assert sidebar._group.checkedId() == NAV_HARDWARE


def test_profile_selector_widgets_present(qtbot):
    sidebar = Sidebar()
    qtbot.addWidget(sidebar)
    assert sidebar.findChild(QComboBox, "Sidebar_Combo_profile") is not None
    assert sidebar.findChild(QPushButton, "Sidebar_Btn_applyProfile") is not None
    assert isinstance(sidebar.profile_combo, QComboBox)
    assert isinstance(sidebar.apply_profile_btn, QPushButton)
