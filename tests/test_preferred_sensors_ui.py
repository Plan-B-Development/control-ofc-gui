"""Tests for the preferred-sensor selectors (Phase 4 / DEC-200).

Covers the Settings ▸ Application combos (populate/preselect/POST/clear, and the
programmatic-population guard) and the Overview ▸ Sensors context-menu POST
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


def test_focus_preferred_sensors_populates(settings_page):
    """DEC-206/DEC-215: the merged readiness view's 'Pick a sensor' deep-link lands
    on the (now single-surface) Settings page with the picker populated, both roles."""
    page, _client = settings_page
    page.focus_preferred_sensors("cpu")
    assert page._pref_cpu_combo.count() > 0  # refreshed from the daemon on arrival
    page.focus_preferred_sensors("mb")  # MB role accepted too
    assert page._pref_mb_combo.count() > 0


# ── Overview ▸ Sensors context-menu ───────────────────────────────────
# Re-vehicled off the retired Diagnostics page: OverviewPage owns the equivalent
# sensor context-menu — `_set_preferred_sensor` plus the
# `_preferred_sensor_unsupported` version gate.
#
# DEC-237: the status-string feedback is back. When the context menu moved here
# from DiagnosticsPage (DEC-216) the result label did not come with it, so every
# failure mode — daemon rejection, daemon unreachable — looked exactly like a
# success: the menu closed and nothing happened. `_pref_result_label` restores
# the DiagnosticsPage behaviour, so these tests now assert the reported text as
# well as the capability gate.


def _overview_page(qtbot, client):
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.overview_page import OverviewPage

    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    page = OverviewPage(state=state, diagnostics_service=DiagnosticsService(state), client=client)
    qtbot.addWidget(page)
    return page


def test_context_menu_posts_preferred(qtbot):
    client = _StubClient()
    page = _overview_page(qtbot, client)
    page._set_preferred_sensor("hwmon:x:Tctl", "cpu")
    page._set_preferred_sensor("hwmon:x:SYSTIN", "mb")
    assert client.cpu_calls == ["hwmon:x:Tctl"]
    assert client.mb_calls == ["hwmon:x:SYSTIN"]


def test_context_menu_404_hides_feature(qtbot):
    class _OldClient(_StubClient):
        def set_preferred_cpu_sensor(self, sensor_id):
            raise DaemonError(code="not_found", message="no route", status=404)

    client = _OldClient()
    page = _overview_page(qtbot, client)
    page._set_preferred_sensor("x", "cpu")
    assert page._preferred_sensor_unsupported is True


def test_context_menu_mb_404_hides_feature(qtbot):
    # The motherboard leg must gate the feature on 404 exactly like the CPU leg.
    class _OldClient(_StubClient):
        def set_preferred_mb_sensor(self, sensor_id):
            raise DaemonError(code="not_found", message="no route", status=404)

    client = _OldClient()
    page = _overview_page(qtbot, client)
    page._set_preferred_sensor("x", "mb")
    assert page._preferred_sensor_unsupported is True


def test_context_menu_non_404_error_keeps_feature(qtbot):
    # A non-404 error is a transient failure, NOT a version gap: the feature must
    # stay enabled, and (DEC-237) the daemon-supplied reason is surfaced.
    class _FlakyClient(_StubClient):
        def set_preferred_cpu_sensor(self, sensor_id):
            raise DaemonError(code="internal_error", message="disk full", status=500)

    client = _FlakyClient()
    page = _overview_page(qtbot, client)
    page._set_preferred_sensor("x", "cpu")
    assert page._preferred_sensor_unsupported is False
    assert "disk full" in page._pref_result_label.text()
    assert not page._pref_result_label.isHidden()


# ── DEC-237: the preferred-sensor result line ─────────────────────────
# Regression guard for the silent-failure defect. Each branch of
# `_set_preferred_sensor` must leave the operator able to tell what happened.


def test_pref_result_label_is_in_the_page_tree(qtbot):
    """An attribute is not a widget the user can see.

    Every other test here reads `page._pref_result_label` off the attribute, so
    dropping its `addWidget` would leave them all green while the label became a
    parentless floating top-level — and the whole point of DEC-237's fix is that
    the user SEES the line, because before it every failure looked like success.
    """
    from PySide6.QtWidgets import QWidget

    page = _overview_page(qtbot, _StubClient())
    found = page.findChild(QWidget, "Overview_Label_prefResult")
    assert found is not None, "the result label must be in the page's widget tree"
    assert found is page._pref_result_label


def test_pref_result_hidden_until_an_action_occurs(qtbot):
    page = _overview_page(qtbot, _StubClient())
    assert page._pref_result_label.text() == ""
    assert page._pref_result_label.isHidden()


def test_pref_result_reports_success(qtbot):
    client = _StubClient()
    page = _overview_page(qtbot, client)
    page._set_preferred_sensor("hwmon:x:Tctl", "cpu")
    assert "CPU" in page._pref_result_label.text()
    assert not page._pref_result_label.isHidden()

    page._set_preferred_sensor("hwmon:x:SYSTIN", "mb")
    assert "motherboard" in page._pref_result_label.text()


def test_pref_result_reports_daemon_unreachable(qtbot):
    """A transport failure used to be indistinguishable from success."""

    class _DeadClient(_StubClient):
        def set_preferred_cpu_sensor(self, sensor_id):
            raise ConnectionError("socket gone")

    page = _overview_page(qtbot, _DeadClient())
    page._set_preferred_sensor("x", "cpu")
    assert "unavailable" in page._pref_result_label.text().lower()
    assert not page._pref_result_label.isHidden()


def test_pref_result_reports_os_error(qtbot):
    class _BrokenClient(_StubClient):
        def set_preferred_mb_sensor(self, sensor_id):
            raise OSError("EPIPE")

    page = _overview_page(qtbot, _BrokenClient())
    page._set_preferred_sensor("x", "mb")
    assert "unavailable" in page._pref_result_label.text().lower()


def test_pref_result_explains_404_version_gap(qtbot):
    """The menu entries vanish after a 404 — say why rather than just hiding them."""

    class _OldClient(_StubClient):
        def set_preferred_cpu_sensor(self, sensor_id):
            raise DaemonError(code="not_found", message="no route", status=404)

    page = _overview_page(qtbot, _OldClient())
    page._set_preferred_sensor("x", "cpu")
    assert page._preferred_sensor_unsupported is True
    assert "too old" in page._pref_result_label.text().lower()
