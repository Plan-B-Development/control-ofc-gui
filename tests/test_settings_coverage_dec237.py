"""DEC-237: GUI ▸ Settings must be a complete map of what can be configured.

Before this, 14 of 29 ``AppSettings`` fields had no Settings-page presence at
all. Some were reachable only through a context menu, an unlabelled table cell,
or a one-way dismissal with no way back; nothing asserted otherwise, so the gap
widened release by release.

The guard has two halves, and both are needed:

* **Classification** — every dataclass field is accounted for exactly once,
  either as a Settings control, a Theme-page control (DEC-215), or an
  explicitly-justified implicit field. A new field fails the suite until someone
  decides which it is.
* **Realisation** — each objectName in ``SETTINGS_FIELD_WIDGETS`` resolves to a
  widget on a constructed page. Without this, the map degrades into a list of
  promises that stays green after the control it names is deleted.
"""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtWidgets import QWidget

from control_ofc.api.models import FanReading, OperationMode
from control_ofc.services.app_settings_service import AppSettings
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.pages.settings_page import SETTINGS_FIELD_WIDGETS, SettingsPage

# Owned by the Theme page, which DEC-215 deliberately split out of Settings.
THEME_PAGE_FIELDS = frozenset({"theme_name", "card_size"})

# Fields with no user-facing control, each for a stated reason. These are the
# only fields allowed to have no way to reach them.
IMPLICIT_FIELDS: dict[str, str] = {
    "version": "schema version — bumped by migrations, never by the user",
    "window_geometry": "restored from the last session; the user 'sets' it by moving the window",
    "last_page_index": "session state, written on page change; governed by restore_last_page",
    "fan_zones": (
        "dead key. DEC-222 removed every writer and reader. Retained deliberately "
        "rather than dropped, so no AppSettings migration is required; it is inert "
        "and must not gain a control."
    ),
}


@pytest.fixture()
def page(qapp, app_state, settings_service):
    return SettingsPage(state=app_state, settings_service=settings_service)


def _all_field_names() -> set[str]:
    return {f.name for f in dataclasses.fields(AppSettings)}


def test_every_appsettings_field_is_classified():
    """No field may be silently unreachable."""
    classified = set(SETTINGS_FIELD_WIDGETS) | THEME_PAGE_FIELDS | set(IMPLICIT_FIELDS)
    unclassified = _all_field_names() - classified
    assert not unclassified, (
        f"AppSettings fields with no home: {sorted(unclassified)}. Add a Settings "
        f"control (and an entry in SETTINGS_FIELD_WIDGETS), or justify it in "
        f"IMPLICIT_FIELDS."
    )


def test_no_stale_classifications():
    """A removed field must not linger in any classification set."""
    classified = set(SETTINGS_FIELD_WIDGETS) | THEME_PAGE_FIELDS | set(IMPLICIT_FIELDS)
    stale = classified - _all_field_names()
    assert not stale, f"classified names that are no longer AppSettings fields: {sorted(stale)}"


def test_classifications_are_disjoint():
    """A field belongs to exactly one surface — two owners means two sources of truth."""
    settings = set(SETTINGS_FIELD_WIDGETS)
    implicit = set(IMPLICIT_FIELDS)
    assert not settings & THEME_PAGE_FIELDS
    assert not settings & implicit
    assert not THEME_PAGE_FIELDS & implicit


def test_every_declared_widget_exists_on_the_page(page):
    """The map must describe the page as built, not as intended."""
    missing = {
        field: name
        for field, name in SETTINGS_FIELD_WIDGETS.items()
        if page.findChild(QWidget, name) is None
    }
    assert not missing, f"SETTINGS_FIELD_WIDGETS names widgets that do not exist: {missing}"


def test_declared_widgets_have_unique_object_names():
    """Duplicate objectNames break findChild-based tests and click targeting."""
    names = list(SETTINGS_FIELD_WIDGETS.values())
    assert len(names) == len(set(names)), "duplicate objectName in SETTINGS_FIELD_WIDGETS"


def test_implicit_fields_carry_a_justification():
    """An empty reason is how an unreachable setting gets waved through."""
    for field, reason in IMPLICIT_FIELDS.items():
        assert reason.strip(), f"{field} is implicit with no stated reason"


# ── Behaviour: the cards must do the thing, through the right layer ────
# The realisation test above only proves the widgets exist. These assert the
# wiring, and specifically that the mirrored surfaces do NOT write settings
# directly — the in-context affordances persist through AppState signals, and a
# second writer bypassing that is how demo-mode data corrupts real hardware
# names (MainWindow._demo_blocks_persist).


@pytest.fixture()
def page_with_fans(qapp, app_state, settings_service):
    app_state.fans = [
        FanReading(id="openfan:ch00", source="openfan", rpm=900),
        FanReading(id="hwmon:nct6798:pwm1", source="hwmon", rpm=1200),
    ]
    selection = SeriesSelectionModel()
    return (
        SettingsPage(
            state=app_state,
            settings_service=settings_service,
            series_selection=selection,
        ),
        app_state,
        settings_service,
        selection,
    )


class TestFanAliasMirror:
    def test_table_lists_every_known_fan(self, page_with_fans):
        page, _state, _svc, _sel = page_with_fans
        table = page._fan_alias_table
        assert table.rowCount() == 2
        assert {table.item(r, 0).text() for r in range(2)} == {
            "openfan:ch00",
            "hwmon:nct6798:pwm1",
        }

    def test_edit_routes_through_appstate_signal(self, page_with_fans):
        """The edit must emit fan_alias_changed — that signal is what persists it.

        Writing settings.fan_aliases from the page would leave AppState (and so
        every other surface) stale, and would skip the demo-mode refusal.
        """
        page, state, _svc, _sel = page_with_fans
        seen: list[tuple[str, str]] = []
        state.fan_alias_changed.connect(lambda fid, name: seen.append((fid, name)))

        row = next(
            r
            for r in range(page._fan_alias_table.rowCount())
            if page._fan_alias_table.item(r, 0).text() == "openfan:ch00"
        )
        page._fan_alias_table.item(row, 1).setText("Front Intake")

        assert seen == [("openfan:ch00", "Front Intake")]
        assert state.fan_aliases["openfan:ch00"] == "Front Intake"

    def test_clearing_an_alias_removes_it(self, page_with_fans):
        page, state, _svc, _sel = page_with_fans
        state.set_fan_alias("openfan:ch00", "Front")
        page._refresh_fan_aliases()

        row = next(
            r
            for r in range(page._fan_alias_table.rowCount())
            if page._fan_alias_table.item(r, 0).text() == "openfan:ch00"
        )
        page._fan_alias_table.item(row, 1).setText("")
        assert "openfan:ch00" not in state.fan_aliases

    def test_alias_for_absent_fan_still_gets_a_row(self, qapp, app_state, settings_service):
        """A name for unplugged hardware must remain clearable."""
        app_state.fans = []
        app_state.set_fan_alias("openfan:ch07", "Old Rear")
        page = SettingsPage(state=app_state, settings_service=settings_service)
        ids = {
            page._fan_alias_table.item(r, 0).text() for r in range(page._fan_alias_table.rowCount())
        }
        assert "openfan:ch07" in ids

    def test_demo_mode_makes_the_table_read_only(self, qapp, app_state, settings_service):
        """Demo fan ids collide with real ones; edits here would not persist."""
        app_state.set_mode(OperationMode.DEMO)
        app_state.fans = [FanReading(id="openfan:ch00", source="openfan", rpm=800)]
        page = SettingsPage(state=app_state, settings_service=settings_service)

        from PySide6.QtCore import Qt

        name_item = page._fan_alias_table.item(0, 1)
        assert not (name_item.flags() & Qt.ItemFlag.ItemIsEditable)
        assert not page._reset_aliases_btn.isEnabled()
        assert "demo" in page._fan_alias_note.text().lower()

    def test_demo_mode_reset_is_refused(self, qapp, app_state, settings_service):
        """The reset must not fire in demo mode even if the button is reached.

        Demo fan ids collide with real ones, so a demo-session reset would clear
        the user's real names from AppState — and MainWindow would refuse to
        persist the (correct) empty result, leaving live state and disk disagreeing.
        """
        app_state.set_fan_alias("openfan:ch00", "Front")
        app_state.set_mode(OperationMode.DEMO)
        page = SettingsPage(state=app_state, settings_service=settings_service)

        page._reset_fan_aliases()
        assert app_state.fan_aliases == {"openfan:ch00": "Front"}

    def test_edit_of_row_without_a_fan_id_is_ignored(self, page_with_fans):
        from PySide6.QtWidgets import QTableWidgetItem

        page, state, _svc, _sel = page_with_fans
        before = dict(state.fan_aliases)
        page._fan_alias_table.setItem(0, 1, QTableWidgetItem("orphan"))
        assert state.fan_aliases == before

    def test_reset_clears_every_alias(self, page_with_fans):
        page, state, _svc, _sel = page_with_fans
        state.set_fan_alias("openfan:ch00", "Front")
        state.set_fan_alias("hwmon:nct6798:pwm1", "CPU")
        page._refresh_fan_aliases()

        page._reset_fan_aliases()
        assert state.fan_aliases == {}


class TestSensorAndSeriesResets:
    def test_unhide_all_sensors(self, page_with_fans):
        page, _state, svc, _sel = page_with_fans
        svc.update(diagnostics_hidden_sensor_ids=["sensor:a", "sensor:b"])
        page._refresh_reset_buttons()
        assert "(2)" in page._unhide_sensors_btn.text()

        page._unhide_all_sensors()
        assert svc.settings.diagnostics_hidden_sensor_ids == []
        assert not page._unhide_sensors_btn.isEnabled()

    def test_reset_sensor_classes_routes_through_appstate(self, page_with_fans):
        page, state, _svc, _sel = page_with_fans
        state.set_sensor_class_override("sensor:x", "coolant")
        emitted: list[tuple[str, str]] = []
        state.sensor_class_override_changed.connect(lambda sid, cls: emitted.append((sid, cls)))

        page._reset_sensor_classes()
        assert state.sensor_class_overrides == {}
        assert emitted == [("sensor:x", "")], "Overview must be told to re-render"

    def test_reset_series_colors(self, page_with_fans):
        page, _state, svc, _sel = page_with_fans
        svc.update(series_colors={"sensor:a": "#ff0000"})
        page._refresh_reset_buttons()
        assert "(1)" in page._reset_colors_btn.text()

        page._reset_series_colors()
        assert svc.settings.series_colors == {}

    def test_show_all_series_uses_the_live_model(self, page_with_fans):
        """Writing hidden_chart_series directly would leave the chart still hiding them."""
        page, _state, _svc, selection = page_with_fans
        selection.load_hidden(["sensor:a", "sensor:b"])
        page._refresh_reset_buttons()
        assert "(2)" in page._show_series_btn.text()

        fired: list[int] = []
        selection.selection_changed.connect(lambda: fired.append(1))
        page._show_all_series()

        assert selection.to_dict()["hidden_keys"] == []
        assert fired, "selection_changed must fire — it is what persists the change"


class TestPromptsAndDismissals:
    def test_aio_pump_info_is_re_armable(self, page_with_fans):
        """The defect: dismiss-only with no way back, unlike its GPU sibling."""
        page, _state, svc, _sel = page_with_fans
        svc.update(show_aio_pump_info=False)
        page._load_current_settings()
        assert page._aio_pump_info_cb.isChecked() is False

        page._aio_pump_info_cb.setChecked(True)
        page._save_app_settings()
        assert svc.settings.show_aio_pump_info is True

    def test_clear_kernel_warnings(self, page_with_fans):
        page, _state, svc, _sel = page_with_fans
        svc.update(acknowledged_kernel_warnings=["nct6687d", "it87"])
        page._refresh_reset_buttons()
        assert "(2)" in page._clear_kernel_warnings_btn.text()

        page._clear_kernel_warnings()
        assert svc.settings.acknowledged_kernel_warnings == []

    @pytest.mark.parametrize(
        "field,action",
        [
            ("daemon_import_prompted", "_reoffer_profile_import"),
            ("fan_aliases_seeded", "_reseed_fan_aliases"),
            ("chart_series_seeded", "_reseed_chart_series"),
        ],
    )
    def test_one_time_latches_can_be_re_armed(self, page_with_fans, field, action):
        page, _state, svc, _sel = page_with_fans
        svc.update(**{field: True})
        page._refresh_reset_buttons()

        getattr(page, action)()
        assert getattr(svc.settings, field) is False

    @pytest.mark.parametrize(
        "field,button",
        [
            ("daemon_import_prompted", "_reoffer_import_btn"),
            ("fan_aliases_seeded", "_reseed_aliases_btn"),
            ("chart_series_seeded", "_reseed_series_btn"),
        ],
    )
    def test_latch_buttons_disabled_when_not_yet_fired(self, page_with_fans, field, button):
        """Re-arming a prompt that has not fired is a no-op — do not offer it."""
        page, _state, svc, _sel = page_with_fans
        svc.update(**{field: False})
        page._refresh_reset_buttons()
        assert not getattr(page, button).isEnabled()


class TestCardLayoutReset:
    def test_reset_card_sizes(self, page_with_fans):
        page, _state, svc, _sel = page_with_fans
        svc.update(controls_card_sizes={"control:a": [300, 200]})
        page._refresh_reset_buttons()
        assert "(1)" in page._reset_card_sizes_btn.text()

        page._reset_card_sizes()
        assert svc.settings.controls_card_sizes == {}
        assert not page._reset_card_sizes_btn.isEnabled()
