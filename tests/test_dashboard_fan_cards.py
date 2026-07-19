"""Dashboard fan-card wiring and reconciliation (DEC-222).

Covers the page-level behaviour the pure VM tests cannot: that cards are built
from live state, reconciled in place at poll rate rather than rebuilt, cleared on
disconnect, and that Edit routes to the Controls page.
"""

from __future__ import annotations

from control_ofc.api.models import ConnectionState, FanReading
from control_ofc.services.profile_service import (
    ControlMember,
    CurveConfig,
    CurvePoint,
    LogicalControl,
)
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.fan_control_card import FanControlCard


def _fan(fan_id, rpm=1200, pwm=45, source="openfan"):
    return FanReading(id=fan_id, source=source, rpm=rpm, last_commanded_pwm=pwm, age_ms=100)


def _page(qtbot, state, **kw):
    page = DashboardPage(state=state, **kw)
    qtbot.addWidget(page)
    return page


class TestCardsFromState:
    def test_no_profile_renders_one_unassigned_card(self, qtbot, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state)
        app_state.set_fans([_fan("openfan:ch00"), _fan("openfan:ch01")])
        assert len(page._fan_cards) == 1
        card = next(iter(page._fan_cards.values()))
        assert isinstance(card, FanControlCard)
        assert card._count.text() == "2 fans"

    def test_header_counts_real_controls_only(self, qtbot, app_state):
        """With no profile the two fans sit in the Unassigned card, which is NOT a
        control — reporting "1 control" would overstate how much of the system is
        actually under curve control."""
        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state)
        app_state.set_fans([_fan("openfan:ch00"), _fan("openfan:ch01")])
        assert page._fan_count_label.text() == "0 controls · 2 fans"

    def test_empty_state_message_when_nothing_is_controllable(self, qtbot, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state)
        app_state.set_fans([])
        assert page._fan_cards == {}
        assert page._fan_cards_empty.isVisibleTo(page._fan_cards_empty.parentWidget())

    def test_cards_follow_the_active_profile(self, qtbot, app_state, profile_service):
        curve = CurveConfig(id="cv", name="C", sensor_id="s0", points=[CurvePoint(30, 20)])
        control = LogicalControl(
            id="c1",
            name="Chassis",
            curve_id="cv",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        active = profile_service.active_profile
        active.controls = [control]
        active.curves = [curve]

        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state, profile_service=profile_service)
        app_state.set_fans([_fan("openfan:ch00")])

        assert "c1" in page._fan_cards
        assert page._fan_cards["c1"]._name.text() == "Chassis"

    def test_sensor_values_reach_the_card_temp_column(self, qtbot, app_state, profile_service):
        """The page must pass sensor_values into the VM builder. If that argument
        were dropped, TEMP would silently read "—" on every card forever and only
        a pure-layer test would still pass."""
        from control_ofc.api.models import SensorReading

        curve = CurveConfig(id="cv", name="C", sensor_id="s0", points=[CurvePoint(30, 20)])
        control = LogicalControl(
            id="c1",
            name="Chassis",
            curve_id="cv",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        active = profile_service.active_profile
        active.controls = [control]
        active.curves = [curve]

        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state, profile_service=profile_service)
        app_state.set_sensors(
            [SensorReading(id="s0", kind="cpu_temp", label="CPU", value_c=57.0, age_ms=100)]
        )
        app_state.set_fans([_fan("openfan:ch00")])

        assert page._fan_cards["c1"]._temp_value.text() == "57°C"


class TestReconciliation:
    def test_repeated_polls_update_in_place(self, qtbot, app_state):
        """The 1 Hz refresh must not destroy and recreate widgets."""
        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state)
        app_state.set_fans([_fan("openfan:ch00", rpm=1200)])
        card = next(iter(page._fan_cards.values()))

        app_state.set_fans([_fan("openfan:ch00", rpm=900)])
        assert next(iter(page._fan_cards.values())) is card  # same widget
        assert card._rpm_value.text() == "900"

    def test_departed_controls_are_dropped(self, qtbot, app_state, profile_service):
        control = LogicalControl(
            id="c1",
            name="Chassis",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        active = profile_service.active_profile
        active.controls = [control]

        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state, profile_service=profile_service)
        app_state.set_fans([_fan("openfan:ch00")])
        assert "c1" in page._fan_cards

        active.controls = []
        page._refresh_fan_cards()
        assert "c1" not in page._fan_cards

    def test_removed_cards_detach_from_the_flow_layout(self, qtbot, app_state):
        """A dropped card must leave FlowLayout's item list, not just the dict.

        The cards are reconciled at 1 Hz; if removal only forgot the dict entry,
        stale QLayoutItems would accumulate in the layout on every poll and the
        pane would slowly fill with ghosts. FlowLayout implements takeAt(), which
        is what QLayout.removeWidget() drives, so this pins that contract.
        """
        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state)
        layout = page._fan_cards_layout

        app_state.set_fans([_fan("openfan:ch00")])
        assert layout.count() == len(page._fan_cards) == 1

        page._clear_fan_cards()
        assert layout.count() == 0
        assert page._fan_cards == {}

        # Re-add, then poll repeatedly: the count must not creep.
        for _ in range(5):
            app_state.set_fans([_fan("openfan:ch00")])
        assert layout.count() == len(page._fan_cards) == 1

    def test_disconnect_clears_every_card(self, qtbot, app_state):
        """A stale card must never survive a disconnect and read as current."""
        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state)
        app_state.set_fans([_fan("openfan:ch00")])
        assert page._fan_cards

        app_state.set_connection(ConnectionState.DISCONNECTED)
        assert page._fan_cards == {}
        assert page._fan_count_label.text() == ""

    def test_rename_refreshes_the_cards(self, qtbot, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state)
        app_state.set_fans([_fan("nvidia_gpu:a", pwm=None, source="nvidia_gpu")])
        card = next(iter(page._fan_cards.values()))
        assert card._name.text() == "NVIDIA D-GPU Fan"

        app_state.set_fan_alias("nvidia_gpu:a", "My GPU")
        assert next(iter(page._fan_cards.values()))._name.text() == "My GPU"


class TestEditRouting:
    def test_edit_reemits_on_the_page_signal(self, qtbot, app_state, profile_service):
        control = LogicalControl(
            id="c1",
            name="Chassis",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile_service.active_profile.controls = [control]

        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state, profile_service=profile_service)
        app_state.set_fans([_fan("openfan:ch00")])

        seen: list[str] = []
        page.open_control.connect(seen.append)
        page._fan_cards["c1"]._edit_btn.click()
        assert seen == ["c1"]

    def test_main_window_routes_edit_to_the_controls_page(self, qtbot):
        """End-to-end: the card's Edit must land the user on Controls, or every
        card's button would be a dead affordance. This caught exactly that —
        open_control was initially left unconnected."""
        from control_ofc.constants import PAGE_CONTROLS
        from control_ofc.ui.main_window import MainWindow

        window = MainWindow(demo_mode=True)
        qtbot.addWidget(window)
        window.dashboard_page.open_control.emit("")
        assert window.page_stack.currentIndex() == PAGE_CONTROLS
        assert window.page_stack.currentWidget() is window.controls_page
        window.dashboard_page.cleanup()

    def test_focus_control_selects_the_named_control(self, qtbot, app_state, profile_service):
        """The deep-link must actually reveal the control, not just open the page."""
        from control_ofc.ui.pages.controls_page import ControlsPage

        control = LogicalControl(
            id="c1",
            name="Chassis",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        profile_service.active_profile.controls = [control]
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        assert page.focus_control("c1") is True
        assert page._selected_control_id == "c1"
        # An unknown or blank id is a harmless no-op, not a crash.
        assert page.focus_control("nope") is False
        assert page.focus_control("") is False

    def test_theme_change_reaches_the_card_previews(self, qtbot, app_state):
        """The cards paint their curve preview themselves, so a theme switch must
        reach them or they keep the old palette."""
        from control_ofc.ui.theme import default_dark_theme

        app_state.set_connection(ConnectionState.CONNECTED)
        page = _page(qtbot, app_state)
        app_state.set_fans([_fan("openfan:ch00")])
        tokens = default_dark_theme()
        page.set_theme(tokens)
        for card in page._fan_cards.values():
            assert card._preview._theme is tokens
