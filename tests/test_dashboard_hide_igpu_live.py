"""F9: iGPU auto-hide is re-read each poll so the toggle applies live."""

from __future__ import annotations

from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.ui.pages.dashboard_page import DashboardPage


def test_hide_igpu_reapplied_each_poll(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(hide_igpu_sensors=True)
    page = DashboardPage(state=AppState(), settings_service=svc)
    qtbot.addWidget(page)

    page._on_sensors_updated([])
    assert page._sensor_panel.hide_igpu is True

    # Toggle the setting and poll again — applies without rebuilding the page.
    svc.update(hide_igpu_sensors=False)
    page._on_sensors_updated([])
    assert page._sensor_panel.hide_igpu is False
