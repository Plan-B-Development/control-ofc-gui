"""Tests for the preferred-sensor selectors (Phase 4 / DEC-200).

Covers the Settings ▸ Application combos (populate/preselect/POST/clear, and the
programmatic-population guard) and the Diagnostics ▸ Sensors context-menu POST
handler (including the 404 → hide-feature path).
"""

from __future__ import annotations

import pytest

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    ConnectionState,
    DefaultCpuSensor,
    HwmonInventory,
    InventoryPreferences,
    InventoryTempSensor,
)
from control_ofc.services.app_state import AppState


class _StubClient:
    """Records preferred-sensor POSTs and returns a canned inventory."""

    def __init__(self, inventory: HwmonInventory | None = None):
        self._inventory = inventory or HwmonInventory()
        self.cpu_calls: list = []
        self.mb_calls: list = []
        self.socket_path = "/tmp/x.sock"

    def inventory_hwmon(self) -> HwmonInventory:
        return self._inventory

    def set_preferred_cpu_sensor(self, sensor_id):
        self.cpu_calls.append(sensor_id)
        return None

    def set_preferred_mb_sensor(self, sensor_id):
        self.mb_calls.append(sensor_id)
        return None


def _inventory() -> HwmonInventory:
    return HwmonInventory(
        temp_sensors=[
            InventoryTempSensor(id="hwmon:k10temp:x:Tctl", label="Tctl", classification="cpu_tctl"),
            InventoryTempSensor(
                id="hwmon:nct6798:x:SYSTIN", label="SYSTIN", classification="motherboard_temp"
            ),
        ],
        default_cpu=DefaultCpuSensor(sensor_id="hwmon:k10temp:x:Tctl", source="auto"),
        preferences=InventoryPreferences(cpu_sensor_id="hwmon:k10temp:x:Tctl"),
    )


# ── Settings ▸ Application combos ─────────────────────────────────────


@pytest.fixture
def settings_page(qapp, app_state, settings_service):
    from control_ofc.ui.pages.settings_page import SettingsPage

    client = _StubClient(_inventory())
    page = SettingsPage(state=app_state, settings_service=settings_service, client=client)
    return page, client


def test_preferred_combos_populate_and_preselect(settings_page):
    page, _client = settings_page
    page._refresh_preferred_sensors()
    cpu = page._pref_cpu_combo
    assert cpu.count() == 3  # Automatic + 2 sensors
    assert cpu.itemData(0) is None  # Automatic clears the preference
    assert cpu.currentData() == "hwmon:k10temp:x:Tctl"  # current preference preselected
    labels = [cpu.itemText(i) for i in range(cpu.count())]
    assert any(t.startswith("★") for t in labels)  # recommended CPU is starred


def test_programmatic_population_does_not_post(settings_page):
    page, client = settings_page
    page._refresh_preferred_sensors()
    assert client.cpu_calls == []
    assert client.mb_calls == []


def test_selecting_cpu_sensor_posts(settings_page):
    page, client = settings_page
    page._refresh_preferred_sensors()
    idx = page._pref_cpu_combo.findData("hwmon:nct6798:x:SYSTIN")
    page._pref_cpu_combo.setCurrentIndex(idx)
    assert client.cpu_calls == ["hwmon:nct6798:x:SYSTIN"]


def test_selecting_automatic_clears(settings_page):
    page, client = settings_page
    page._refresh_preferred_sensors()
    client.cpu_calls.clear()
    page._pref_cpu_combo.setCurrentIndex(0)  # Automatic
    assert client.cpu_calls == [None]


# ── Diagnostics ▸ Sensors context-menu ───────────────────────────────


def _diag_page(qtbot, client):
    from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage

    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    page = DiagnosticsPage(state=state, client=client)
    qtbot.addWidget(page)
    return page


def test_context_menu_posts_preferred(qtbot):
    client = _StubClient()
    page = _diag_page(qtbot, client)
    page._set_preferred_sensor_from_menu("hwmon:x:Tctl", "cpu")
    page._set_preferred_sensor_from_menu("hwmon:x:SYSTIN", "mb")
    assert client.cpu_calls == ["hwmon:x:Tctl"]
    assert client.mb_calls == ["hwmon:x:SYSTIN"]
    assert "Preferred" in page._status_label.text()


def test_context_menu_404_hides_feature(qtbot):
    class _OldClient(_StubClient):
        def set_preferred_cpu_sensor(self, sensor_id):
            raise DaemonError(code="not_found", message="no route", status=404)

    client = _OldClient()
    page = _diag_page(qtbot, client)
    page._set_preferred_sensor_from_menu("x", "cpu")
    assert page._preferred_sensor_unsupported is True
