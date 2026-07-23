"""DEC-227 — fan renaming, OpenFan fallback names, and the two bugs found with them.

Three clusters:

* the shared rename rule on ``AppState`` (Qt-free — the rule is the same wherever
  it is invoked from, so it is tested once, here);
* the Sensors-rail panel wiring (editability is scoped to the name column, the
  delegate owns the commit, and the ``(AIO)`` tag survives a poll);
* the demo-persistence guard, which is a *data-loss* regression rather than a
  cosmetic one and is asserted against the settings actually written to disk.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QStyleOptionViewItem

from control_ofc.api.models import FanReading, HwmonHeader
from control_ofc.services.app_state import MAX_FAN_ALIAS_LEN, AppState
from control_ofc.services.overview_view import build_fan_rows
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.widgets.sensor_series_panel import (
    _FAN_KEY_PREFIX,
    _FAN_KEY_SUFFIX,
    SensorSeriesPanel,
)

OPENFAN = "openfan:ch00"
AIO_ID = "hwmon:nct6799:pwm1"


def _aio_state() -> AppState:
    """State with one AIO-tagged hwmon header labelled "Pump" and one OpenFan fan."""
    state = AppState()
    header = HwmonHeader(id=AIO_ID, chip_name="nct6799", pwm_index=1, label="Pump")
    header.is_aio = True
    state.hwmon_headers = [header]
    state.fans = [
        FanReading(id=AIO_ID, source="hwmon", rpm=900),
        FanReading(id=OPENFAN, source="openfan", rpm=1182),
    ]
    return state


# ── The shared rule (AppState.apply_fan_rename) ──────────────────────


class TestApplyFanRename:
    def test_stores_stripped_text(self):
        state = AppState()
        state.apply_fan_rename(OPENFAN, "  Front Intake  ")
        assert state.fan_aliases == {OPENFAN: "Front Intake"}
        assert state.fan_display_name(OPENFAN) == "Front Intake"

    def test_empty_text_clears_and_reverts_to_fallback(self):
        state = AppState()
        state.apply_fan_rename(OPENFAN, "Front Intake")
        state.apply_fan_rename(OPENFAN, "")
        assert OPENFAN not in state.fan_aliases
        assert state.fan_display_name(OPENFAN) == "OpenFan CH0"

    def test_typing_the_fallback_name_does_not_create_an_alias(self):
        """Pressing Enter on an unchanged row must be a true no-op.

        The in-place editor opens pre-filled with the *resolved* label (Qt aliases
        EditRole to DisplayRole), so without this rule simply committing an
        untouched row would mint an alias — and an alias means "keep this fan
        visible" to filter_displayable_fans, silently pinning a dead header.
        """
        state = AppState()
        state.apply_fan_rename(OPENFAN, "OpenFan CH0")
        assert state.fan_aliases == {}

        idle = FanReading(id=OPENFAN, source="openfan", rpm=0)
        assert filter_displayable_fans([idle], state.fan_aliases, hide_unused=True) == []

    def test_retyping_the_current_alias_keeps_it(self):
        """ "Same as displayed" must not be misread as "clear"."""
        state = AppState()
        state.apply_fan_rename(OPENFAN, "Front Intake")
        state.apply_fan_rename(OPENFAN, "Front Intake")
        assert state.fan_aliases == {OPENFAN: "Front Intake"}

    def test_aio_suffix_is_never_captured_into_the_alias(self):
        state = _aio_state()
        state.apply_fan_rename(AIO_ID, "Kraken Pump (AIO)")
        assert state.fan_aliases[AIO_ID] == "Kraken Pump"

    def test_aio_suffix_on_an_unchanged_row_clears(self):
        """The row reads "Pump (AIO)"; committing it unchanged must not alias."""
        state = _aio_state()
        state.apply_fan_rename(AIO_ID, "Pump (AIO)")
        assert AIO_ID not in state.fan_aliases

    def test_recommitting_an_aliased_aio_row_keeps_the_alias(self):
        """The load-bearing fallback-vs-display distinction, on the hardest case.

        An already-aliased AIO fan renders "Kraken (AIO)", so the editor pre-fills
        with that. Stripping the tag yields "Kraken" — which equals the *display*
        name but NOT the *fallback* name ("Pump"). Comparing against
        `fan_display_name` instead of `fan_fallback_name` would therefore read this
        as "unchanged, clear it" and silently delete the user's name — and with it
        the alias pin that keeps a 0-RPM fan on screen.
        """
        state = _aio_state()
        state.apply_fan_rename(AIO_ID, "Kraken")
        assert state.fan_aliases[AIO_ID] == "Kraken"
        state.apply_fan_rename(AIO_ID, "Kraken (AIO)")
        assert state.fan_aliases[AIO_ID] == "Kraken"

    def test_aio_tag_is_stripped_before_the_cap_is_applied(self):
        """Ordering matters: capping first would eat 6 chars of a max-length name."""
        state = _aio_state()
        name = "K" * MAX_FAN_ALIAS_LEN
        state.apply_fan_rename(AIO_ID, f"{name} (AIO)")
        assert state.fan_aliases[AIO_ID] == name

    def test_alias_is_length_capped(self):
        state = AppState()
        state.apply_fan_rename(OPENFAN, "X" * 500)
        assert len(state.fan_aliases[OPENFAN]) == MAX_FAN_ALIAS_LEN

    def test_wizard_path_is_capped_too(self):
        """The cap lives in set_fan_alias, so every writer inherits it."""
        state = AppState()
        state.set_fan_alias(OPENFAN, "Y" * 500)
        assert len(state.fan_aliases[OPENFAN]) == MAX_FAN_ALIAS_LEN

    def test_cap_counts_name_characters_not_whitespace(self):
        state = AppState()
        state.set_fan_alias(OPENFAN, "   " + "Z" * 500)
        assert state.fan_aliases[OPENFAN] == "Z" * MAX_FAN_ALIAS_LEN

    def test_emits_display_name_for_listeners(self):
        state = AppState()
        seen: list[tuple[str, str]] = []
        state.fan_alias_changed.connect(lambda fid, name: seen.append((fid, name)))
        state.apply_fan_rename(OPENFAN, "Front Intake")
        assert seen == [(OPENFAN, "Front Intake")]


class TestFanFallbackName:
    def test_ignores_an_existing_alias(self):
        """apply_fan_rename compares against this, so it must see past the alias."""
        state = AppState()
        state.set_fan_alias(OPENFAN, "Front Intake")
        assert state.fan_display_name(OPENFAN) == "Front Intake"
        assert state.fan_fallback_name(OPENFAN) == "OpenFan CH0"

    @pytest.mark.parametrize(
        ("fan_id", "expected"),
        [
            ("openfan:ch00", "OpenFan CH0"),
            ("openfan:ch07", "OpenFan CH7"),
            ("openfan:ch10", "OpenFan CH10"),
            ("openfan:chXX", "openfan:chXX"),
            ("openfan:ch", "openfan:ch"),
            ("openfan:other", "openfan:other"),
            # isdigit() accepts these but int() rejects them — the channel parse
            # must not raise on a path that runs once per fan per poll.
            ("openfan:ch²", "openfan:ch²"),
            ("openfan:ch½", "openfan:ch½"),
        ],
    )
    def test_openfan_channel_labels(self, fan_id, expected):
        assert AppState().fan_fallback_name(fan_id) == expected

    def test_hwmon_precedence_is_unchanged(self):
        state = _aio_state()
        assert state.fan_fallback_name(AIO_ID) == "Pump"

    def test_gpu_precedence_is_unchanged(self):
        state = AppState()
        assert state.fan_fallback_name("amd_gpu:0000:03:00.0") == "D-GPU Fan"
        assert state.fan_fallback_name("nvidia_gpu:0000:01:00.0") == "NVIDIA D-GPU Fan"
        assert state.fan_fallback_name("intel_gpu:0000:03:00.0") == "Intel D-GPU Fan"


# ── Sensors rail panel ───────────────────────────────────────────────


@pytest.fixture()
def panel(qtbot):
    state = _aio_state()
    widget = SensorSeriesPanel(SeriesSelectionModel(), state=state)
    qtbot.addWidget(widget)
    widget.update_fans(state.fans)
    return widget


class TestAioSuffixRegression:
    def test_aio_tag_survives_a_second_poll(self, panel):
        """Regression: _update_fan_values used to recompute a bare display name.

        The fan set is stable across polls, so the second 1 Hz tick took the
        in-place branch and erased the tag permanently — it was visible for about
        one second after discovery and never again.
        """
        assert panel._fan_items[AIO_ID].text(0) == "Pump (AIO)"
        panel.update_fans(panel._state.fans)
        assert panel._fan_items[AIO_ID].text(0) == "Pump (AIO)"

    def test_aio_tag_survives_a_rename(self, panel):
        panel.rename_fan(AIO_ID, "Kraken")
        assert panel._fan_items[AIO_ID].text(0) == "Kraken (AIO)"
        panel.update_fans(panel._state.fans)
        assert panel._fan_items[AIO_ID].text(0) == "Kraken (AIO)"


class TestPanelRename:
    def test_openfan_row_shows_channel_label_not_raw_id(self, panel):
        assert panel._fan_items[OPENFAN].text(0) == "OpenFan CH0"

    def test_rename_updates_row_and_state(self, panel):
        panel.rename_fan(OPENFAN, "Front Intake")
        assert panel._state.fan_aliases[OPENFAN] == "Front Intake"
        assert panel._fan_items[OPENFAN].text(0) == "Front Intake"

    def test_rename_survives_the_next_poll(self, panel):
        panel.rename_fan(OPENFAN, "Front Intake")
        panel.update_fans(panel._state.fans)
        assert panel._fan_items[OPENFAN].text(0) == "Front Intake"

    def test_external_rename_repaints_without_a_poll(self, panel):
        """A rename from a fan card or the Overview table lands here immediately."""
        panel._state.apply_fan_rename(OPENFAN, "Set Elsewhere")
        assert panel._fan_items[OPENFAN].text(0) == "Set Elsewhere"

    def test_fan_rows_are_editable_and_sensor_rows_are_not(self, panel, qtbot):
        from control_ofc.api.models import SensorReading

        panel.update_sensors([SensorReading(id="s1", kind="cpu_temp", label="Tctl", value_c=50.0)])
        fan_item = panel._fan_items[OPENFAN]
        sensor_item = panel._sensor_items["s1"]
        assert fan_item.flags() & Qt.ItemFlag.ItemIsEditable
        assert not (sensor_item.flags() & Qt.ItemFlag.ItemIsEditable)

    def test_fan_id_recovered_from_a_colon_heavy_id(self, panel):
        """Fan ids contain colons, so the series key must be sliced, not split."""
        long_id = "hwmon:it8696:it87.2624:pwm1:pwm1"
        panel._state.fans = [FanReading(id=long_id, source="hwmon", rpm=800)]
        panel.update_fans(panel._state.fans)
        assert panel._fan_id_for_item(panel._fan_items[long_id]) == long_id

    def test_group_rows_are_not_renamable(self, panel):
        group = panel._group_items["fans_openfan"]
        assert panel._fan_id_for_item(group) == ""


class TestPanelDelegate:
    def test_editor_is_scoped_to_the_name_column(self, panel):
        delegate = panel._tree.itemDelegate()
        option = QStyleOptionViewItem()
        model = panel._tree.model()
        # The positive case matters as much as the negatives: a createEditor that
        # returned None for everything would disable renaming entirely and still
        # satisfy the two assertions below.
        assert delegate.createEditor(panel._tree, option, model.index(0, 0)) is not None
        assert delegate.createEditor(panel._tree, option, model.index(0, 1)) is None
        assert delegate.createEditor(panel._tree, option, model.index(0, 2)) is None

    def test_commit_routes_through_the_rename_rule(self, panel, qtbot):
        """setModelData must not write the raw text — the stored alias may differ."""
        item = panel._fan_items[AIO_ID]
        index = panel._tree.indexFromItem(item, 0)
        editor = QLineEdit()
        editor.setText("Kraken Pump (AIO)")
        panel._tree.itemDelegate().setModelData(editor, panel._tree.model(), index)
        assert panel._state.fan_aliases[AIO_ID] == "Kraken Pump"
        assert item.text(0) == "Kraken Pump (AIO)"

    def test_commit_on_a_value_column_is_ignored(self, panel):
        item = panel._fan_items[OPENFAN]
        index = panel._tree.indexFromItem(item, 1)
        editor = QLineEdit()
        editor.setText("nonsense")
        panel._tree.itemDelegate().setModelData(editor, panel._tree.model(), index)
        assert panel._state.fan_aliases == {}

    def test_checkbox_toggling_still_syncs_chart_visibility(self, panel):
        """The rename path must not disturb _on_item_changed.

        A delegate commit emits no itemChanged, so the checkbox handler is
        untouched — this pins that, because a wrong branch there would silently
        hide chart series.
        """
        item = panel._fan_items[OPENFAN]
        # Built from the row's own series key rather than hardcoded, so a change
        # to the key format fails loudly here instead of silently comparing
        # against a key the selection model never holds.
        key = item.data(0, Qt.ItemDataRole.UserRole)
        assert key == f"{_FAN_KEY_PREFIX}{OPENFAN}{_FAN_KEY_SUFFIX}"
        item.setCheckState(0, Qt.CheckState.Unchecked)
        assert panel._selection.is_hidden(key)
        item.setCheckState(0, Qt.CheckState.Checked)
        assert not panel._selection.is_hidden(key)

    def test_rename_does_not_emit_item_changed(self, panel):
        seen: list[int] = []
        panel._tree.itemChanged.connect(lambda _item, col: seen.append(col))
        index = panel._tree.indexFromItem(panel._fan_items[OPENFAN], 0)
        editor = QLineEdit()
        editor.setText("Front Intake")
        panel._tree.itemDelegate().setModelData(editor, panel._tree.model(), index)
        assert seen == []


class TestPanelContextMenu:
    """Menu *contents* per row state. build_fan_menu is split from showing the
    popup precisely so this needs no laid-out viewport and no real event grab."""

    @staticmethod
    def _names(menu) -> list[str]:
        return [] if menu is None else [a.objectName() for a in menu.actions()]

    def test_rename_offered_for_a_fan_row(self, panel):
        names = self._names(panel.build_fan_menu(panel._fan_items[OPENFAN]))
        assert "SensorSeriesPanel_Action_renameFan" in names
        # Nothing to reset until an alias exists.
        assert "SensorSeriesPanel_Action_resetFanName" not in names

    def test_reset_offered_once_aliased(self, panel):
        panel.rename_fan(OPENFAN, "Front Intake")
        names = self._names(panel.build_fan_menu(panel._fan_items[OPENFAN]))
        assert "SensorSeriesPanel_Action_resetFanName" in names

    def test_no_menu_on_a_group_row(self, panel):
        assert panel.build_fan_menu(panel._group_items["fans_openfan"]) is None

    def test_no_menu_on_empty_space(self, panel):
        assert panel.build_fan_menu(None) is None

    def test_no_menu_on_a_sensor_row(self, panel):
        from control_ofc.api.models import SensorReading

        panel.update_sensors([SensorReading(id="s1", kind="cpu_temp", label="Tctl", value_c=50.0)])
        assert panel.build_fan_menu(panel._sensor_items["s1"]) is None

    def test_rename_action_opens_the_editor(self, panel, monkeypatch):
        opened: list[int] = []
        monkeypatch.setattr(
            type(panel._tree), "editItem", lambda _s, _i, col: opened.append(col), raising=False
        )
        menu = panel.build_fan_menu(panel._fan_items[OPENFAN])
        menu.actions()[0].trigger()
        assert opened == [0]

    def test_reset_action_clears_the_alias(self, panel):
        panel.rename_fan(OPENFAN, "Front Intake")
        menu = panel.build_fan_menu(panel._fan_items[OPENFAN])
        next(a for a in menu.actions() if "reset" in a.objectName().lower()).trigger()
        assert OPENFAN not in panel._state.fan_aliases
        assert panel._fan_items[OPENFAN].text(0) == "OpenFan CH0"


# ── Overview fan table ───────────────────────────────────────────────


class TestOverviewFanRows:
    def test_rows_carry_the_fan_id(self):
        fans = [FanReading(id=OPENFAN, source="openfan", rpm=1200)]
        rows = build_fan_rows(fans, [], None, display_name=lambda x: x)
        assert rows[0].fan_id == OPENFAN

    def test_pwm_only_rows_carry_their_header_id_and_resolve_names(self):
        """A PWM-only header is renamable too, so its row must show the alias."""
        header = HwmonHeader(id="hwmon:nct6798:pwm2", label="SYS", is_writable=True)
        rows = build_fan_rows([], [header], None, display_name=lambda x: f"named:{x}")
        assert rows[0].is_pwm_only
        assert rows[0].fan_id == "hwmon:nct6798:pwm2"
        assert rows[0].name == "named:hwmon:nct6798:pwm2"


# ── Fan cards ────────────────────────────────────────────────────────


class TestFanCardRename:
    def _vm(self, **kw):
        from control_ofc.services.fan_cards_view import FanCardVM, FanState

        base = dict(
            control_id="readonly:amd_gpu:0000:03:00.0",
            card_key="readonly:amd_gpu:0000:03:00.0",
            label="9070XT Fan",
            is_unassigned=False,
            is_read_only=True,
            fan_count=1,
            member_fan_ids=("amd_gpu:0000:03:00.0",),
            rpm=0,
            pwm_pct=None,
            duty_pct=None,
            temp_c=None,
            state=FanState.NORMAL,
            overridden=False,
            curve=None,
        )
        base.update(kw)
        return FanCardVM(**base)

    def test_read_only_card_is_renamable(self, qtbot):
        from control_ofc.ui.widgets.fan_control_card import FanControlCard

        card = FanControlCard(self._vm())
        qtbot.addWidget(card)
        assert card.build_rename_menu() is not None

    def test_control_card_is_not_renamable(self, qtbot):
        """A control card is titled with profile data — renaming it is a profile
        write and stays on the Controls page (DEC-222)."""
        from control_ofc.ui.widgets.fan_control_card import FanControlCard

        card = FanControlCard(
            self._vm(
                control_id="ctl-1",
                card_key="ctl-1",
                is_read_only=False,
                fan_count=3,
                member_fan_ids=("a", "b", "c"),
            )
        )
        qtbot.addWidget(card)
        assert card.build_rename_menu() is None

    def test_update_vm_revokes_renameability(self, qtbot):
        """Cards are re-rendered in place each poll, so a card that stops being
        read-only must stop offering a rename — not keep a stale fan id."""
        from control_ofc.ui.widgets.fan_control_card import FanControlCard

        card = FanControlCard(self._vm())
        qtbot.addWidget(card)
        assert card.build_rename_menu() is not None

        card.update_vm(
            self._vm(
                control_id="ctl-1",
                card_key="ctl-1",
                is_read_only=False,
                fan_count=3,
                member_fan_ids=("a", "b", "c"),
            )
        )
        assert card.build_rename_menu() is None

    def test_update_vm_grants_renameability(self, qtbot):
        from control_ofc.ui.widgets.fan_control_card import FanControlCard

        card = FanControlCard(
            self._vm(control_id="ctl-1", card_key="ctl-1", is_read_only=False, fan_count=3)
        )
        qtbot.addWidget(card)
        assert card.build_rename_menu() is None

        card.update_vm(self._vm())
        assert card.build_rename_menu() is not None

    def test_dashboard_wires_the_card_rename_signal(self, qtbot, monkeypatch):
        """The connect() at card-creation time, end to end.

        Without this, deleting `card.rename_requested.connect(...)` would break
        right-click rename on every Dashboard fan card while the card-level and
        page-level tests both kept passing — the signal still fires, and
        `_rename_fan` still works when called directly. Nothing would fail.
        """
        from PySide6.QtWidgets import QInputDialog

        from control_ofc.ui.pages.dashboard_page import DashboardPage

        state = AppState()
        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        # A read-only (NVIDIA, no write path) fan gets its own per-fan card.
        gpu_id = "nvidia_gpu:0000:01:00.0"
        state.set_fans([FanReading(id=gpu_id, source="nvidia_gpu", rpm=1000)])
        page._refresh_fan_cards()

        card = next(c for c in page._fan_cards.values() if c.build_rename_menu() is not None)
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("My GPU Fan", True))
        card.build_rename_menu().actions()[0].trigger()

        assert state.fan_aliases == {gpu_id: "My GPU Fan"}

    def test_rename_action_emits_the_fan_id(self, qtbot):
        from control_ofc.ui.widgets.fan_control_card import FanControlCard

        card = FanControlCard(self._vm())
        qtbot.addWidget(card)
        seen: list[str] = []
        card.rename_requested.connect(seen.append)

        menu = card.build_rename_menu()
        assert [a.objectName() for a in menu.actions()] == ["FanCard_Action_renameFan"]
        menu.actions()[0].trigger()
        assert seen == ["amd_gpu:0000:03:00.0"]

    def test_control_card_builds_no_menu(self, qtbot):
        from control_ofc.ui.widgets.fan_control_card import FanControlCard

        card = FanControlCard(
            self._vm(control_id="ctl-1", card_key="ctl-1", is_read_only=False, fan_count=3)
        )
        qtbot.addWidget(card)
        assert card.build_rename_menu() is None


# ── Dialog-driven surfaces (cards, Overview) ─────────────────────────


class TestRenamePrompts:
    """The two QInputDialog wrappers. The rule itself is covered above; what is
    pinned here is the ``if ok:`` gate — Cancel must not rename."""

    @staticmethod
    def _answer(monkeypatch, text: str, accepted: bool) -> None:
        from PySide6.QtWidgets import QInputDialog

        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (text, accepted))

    def test_dashboard_cancel_does_not_rename(self, qtbot, monkeypatch):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        state = AppState()
        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        self._answer(monkeypatch, "Typed But Cancelled", False)
        page._rename_fan(OPENFAN)
        assert state.fan_aliases == {}

    def test_dashboard_accept_renames(self, qtbot, monkeypatch):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        state = AppState()
        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        self._answer(monkeypatch, "Front Intake", True)
        page._rename_fan(OPENFAN)
        assert state.fan_aliases == {OPENFAN: "Front Intake"}

    def test_dashboard_accepting_an_empty_name_clears(self, qtbot, monkeypatch):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        state = AppState()
        state.set_fan_alias(OPENFAN, "Old Name")
        page = DashboardPage(state=state)
        qtbot.addWidget(page)
        self._answer(monkeypatch, "", True)
        page._rename_fan(OPENFAN)
        assert OPENFAN not in state.fan_aliases

    def test_overview_cancel_does_not_rename(self, qtbot, monkeypatch):
        from control_ofc.ui.pages.overview_page import OverviewPage

        state = AppState()
        page = OverviewPage(state=state)
        qtbot.addWidget(page)
        self._answer(monkeypatch, "Typed But Cancelled", False)
        page._prompt_fan_rename(OPENFAN)
        assert state.fan_aliases == {}

    def test_overview_accept_renames(self, qtbot, monkeypatch):
        from control_ofc.ui.pages.overview_page import OverviewPage

        state = AppState()
        page = OverviewPage(state=state)
        qtbot.addWidget(page)
        self._answer(monkeypatch, "Front Intake", True)
        page._prompt_fan_rename(OPENFAN)
        assert state.fan_aliases == {OPENFAN: "Front Intake"}

    def test_overview_accepting_an_empty_name_clears(self, qtbot, monkeypatch):
        """Accepting an emptied field must route through the rule and clear,
        not be short-circuited as "nothing typed"."""
        from control_ofc.ui.pages.overview_page import OverviewPage

        state = AppState()
        state.set_fan_alias(OPENFAN, "Old Name")
        page = OverviewPage(state=state)
        qtbot.addWidget(page)
        self._answer(monkeypatch, "", True)
        page._prompt_fan_rename(OPENFAN)
        assert OPENFAN not in state.fan_aliases

    def test_overview_menu_offered_for_a_pwm_only_row(self, qtbot):
        """PWM-only header rows are renamable too — they carry a header id."""
        from control_ofc.api.models import HwmonHeader
        from control_ofc.ui.pages.overview_page import OverviewPage

        state = AppState()
        header = HwmonHeader(id="hwmon:nct6798:pwm2", label="SYS", is_writable=True)
        state.set_hwmon_headers([header])
        page = OverviewPage(state=state)
        qtbot.addWidget(page)
        state.set_fans([])

        assert page._row_to_fan_id(0) == "hwmon:nct6798:pwm2"
        menu = page.build_fan_menu("hwmon:nct6798:pwm2")
        assert menu is not None
        assert "Overview_Action_renameFan" in [a.objectName() for a in menu.actions()]


# ── Demo persistence guard (data-loss regression) ────────────────────


def _persisted_aliases(tmp_path) -> dict:
    path = tmp_path / "control-ofc" / "app_settings.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("fan_aliases", {})


class TestDemoPersistenceGuard:
    """Demo replaces state.fan_aliases with DemoService's synthetic map, whose ids
    collide exactly with real hardware (openfan:ch00 …). Persisting from a demo
    session therefore both wipes the user's real labels and writes demo names onto
    their actual fans. fan_aliases is portable, so it can travel in an export."""

    def test_demo_rename_does_not_touch_persisted_aliases(self, qtbot, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from control_ofc.services.app_settings_service import AppSettingsService
        from control_ofc.ui.main_window import MainWindow

        svc = AppSettingsService()
        svc.update(fan_aliases={OPENFAN: "My Real Intake"})

        window = MainWindow(settings_service=svc, demo_mode=True)
        qtbot.addWidget(window)
        window._state.apply_fan_rename(OPENFAN, "Renamed In Demo")

        on_disk = _persisted_aliases(tmp_path)
        assert on_disk == {OPENFAN: "My Real Intake"}
        assert "Renamed In Demo" not in on_disk.values()
        # And no *other* demo alias leaked. Checked on a fan the test never
        # renamed: asserting on openfan:ch00's own demo label would pass even
        # unguarded, because the rename overwrote that entry anyway.
        from control_ofc.services.demo_service import DemoService

        assert "openfan:ch02" not in on_disk
        assert DemoService.fan_aliases()["openfan:ch02"] not in on_disk.values()

    def test_demo_zone_change_does_not_persist(self, qtbot, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from control_ofc.services.app_settings_service import AppSettingsService
        from control_ofc.ui.main_window import MainWindow

        svc = AppSettingsService()
        svc.update(fan_zones={OPENFAN: "Real Zone"})

        window = MainWindow(settings_service=svc, demo_mode=True)
        qtbot.addWidget(window)
        # fan_zones is dormant since DEC-222 — the zone UI was removed, so nothing
        # emits fan_zones_changed today and the signal is the only way in. The
        # guard is defence-in-depth for whenever a zone surface returns.
        window._state.fan_zones = {OPENFAN: "Demo Zone"}
        window._state.fan_zones_changed.emit(OPENFAN, "Demo Zone")

        path = tmp_path / "control-ofc" / "app_settings.json"
        assert json.loads(path.read_text())["fan_zones"] == {OPENFAN: "Real Zone"}

    def test_live_rename_still_persists(self, qtbot, tmp_path, monkeypatch):
        """Guards against over-fixing: the guard must be demo-only."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from control_ofc.services.app_settings_service import AppSettingsService
        from control_ofc.ui.main_window import MainWindow

        svc = AppSettingsService()
        window = MainWindow(settings_service=svc, demo_mode=False)
        qtbot.addWidget(window)
        window._state.apply_fan_rename(OPENFAN, "Front Intake")

        assert _persisted_aliases(tmp_path) == {OPENFAN: "Front Intake"}
