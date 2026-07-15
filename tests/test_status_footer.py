"""DEC-208: the global footer / status strip + the page nav/action shims."""

from __future__ import annotations

import platform

from PySide6.QtWidgets import QLabel

from control_ofc.constants import APP_VERSION
from control_ofc.ui.components.footer import StatusFooter


def test_footer_shows_version_kernel_arch(qtbot):
    footer = StatusFooter()
    qtbot.addWidget(footer)
    assert footer.objectName() == "StatusFooter_Root"
    labels = {c.objectName(): c.text() for c in footer.findChildren(QLabel)}
    assert labels["StatusFooter_Label_version"] == f"v{APP_VERSION}"
    assert platform.release() in labels["StatusFooter_Label_kernel"]
    assert labels["StatusFooter_Label_arch"] == platform.machine()


def test_footer_health_tracks_warning_count(qtbot):
    footer = StatusFooter()
    qtbot.addWidget(footer)
    assert footer._health_label.text() == "All systems nominal"
    assert footer._health_led._role == "ok"
    footer.set_warning_count(2)
    assert footer._health_label.text() == "2 warnings"
    assert footer._health_led._role == "warn"
    footer.set_warning_count(1)
    assert footer._health_label.text() == "1 warning"
    footer.set_warning_count(0)
    assert footer._health_label.text() == "All systems nominal"
    assert footer._health_led._role == "ok"


def test_footer_buttons_emit(qtbot):
    footer = StatusFooter()
    qtbot.addWidget(footer)
    with qtbot.waitSignal(footer.rescan_clicked):
        footer._rescan_btn.click()
    with qtbot.waitSignal(footer.export_bundle_clicked):
        footer._export_btn.click()


def test_pages_expose_nav_and_action_shims():
    # DEC-216: the global footer's two actions moved off the retired Diagnostics
    # page — Rescan Hardware to System State, Export Bundle to Logs.
    from control_ofc.ui.pages.logs_page import LogsPage
    from control_ofc.ui.pages.settings_page import SettingsPage
    from control_ofc.ui.pages.system_state_page import SystemStatePage

    assert hasattr(SystemStatePage, "run_hwmon_rescan")
    assert hasattr(LogsPage, "_export_bundle")
    # DEC-215: SettingsPage lost select_tab (single-surface, no sub-tabs).
    assert not hasattr(SettingsPage, "select_tab")
