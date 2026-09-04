"""DEC-214: Controls page restyle — header contract, always-mounted editor +
placeholder, Test Curve, real Unlink, and the viewed-profile re-source."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QPushButton

from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
)
from control_ofc.ui.pages.controls_page import ControlsPage


def _page(qtbot, app_state, profile_service):
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    return page


class TestHeaderContract:
    def test_root_object_name(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        assert page.objectName() == "Controls_Root"

    def test_header_keeps_save_wizard_manage(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        assert page.findChild(QPushButton, "Controls_Btn_save") is not None
        assert page.findChild(QPushButton, "Controls_Btn_manageProfiles") is not None
        # DEC-233: the fan wizard moved into the "Set up ▾" menu as an action.
        assert page.findChild(QPushButton, "Controls_Btn_setup") is not None
        assert page._wizard_action is not None
        # DEC-233: Save is renamed "Save" (was "Save Profile").
        assert page.findChild(QPushButton, "Controls_Btn_save").text() == "Save"

    def test_profile_combo_and_activate_removed(self, qtbot, app_state, profile_service):
        """DEC-214: profile selection/activation moved to the sidebar."""
        page = _page(qtbot, app_state, profile_service)
        assert page.findChild(QComboBox, "Controls_Combo_profile") is None
        assert page.findChild(QPushButton, "Controls_Btn_activate") is None


class TestViewedProfile:
    def test_select_profile_sets_viewed(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        p = profile_service.create_profile("Draft")
        page.select_profile(p.id)
        assert page._viewed_profile_id == p.id
        assert page._get_current_profile().id == p.id

    def test_has_unsaved_changes_tracks_flag(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        assert page.has_unsaved_changes() is False
        page._set_unsaved(True)
        assert page.has_unsaved_changes() is True

    def test_active_changed_falls_back_to_active(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        p = profile_service.create_profile("Draft")
        page.select_profile(p.id)
        page._on_active_profile_changed()
        assert page._viewed_profile_id is None


class TestAlwaysMountedEditor:
    def test_placeholder_shown_initially(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        # No graph curve edited yet → placeholder visible, editor hidden.
        assert page._editor_placeholder.isVisible() or not page._curve_editor.isVisibleTo(page)

    def test_edit_graph_curve_swaps_in_editor(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        profile = page._get_current_profile()
        curve = CurveConfig(id="g1", name="CPU", type=CurveType.GRAPH)
        profile.curves.append(curve)
        page._on_edit_curve("g1")
        assert page._curve_editor.get_curve() is not None
        assert page._editor_placeholder.isHidden()

    def test_close_editor_restores_placeholder(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        profile = page._get_current_profile()
        profile.curves.append(CurveConfig(id="g1", name="CPU", type=CurveType.GRAPH))
        page._on_edit_curve("g1")
        page._close_editor()
        assert not page._editor_placeholder.isHidden()
        assert page._editor_title.text() == "Editing: —"


class TestUnlink:
    def test_unlink_detaches_curve_from_roles(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        profile = page._get_current_profile()
        profile.curves.append(CurveConfig(id="c1", name="CPU", type=CurveType.GRAPH))
        profile.controls.append(
            LogicalControl(id="r1", name="CPU", mode=ControlMode.CURVE, curve_id="c1")
        )
        page._refresh_all()
        page._on_unlink_curve("c1")
        ctrl = next(c for c in profile.controls if c.id == "r1")
        assert ctrl.curve_id == ""
        assert ctrl.mode == ControlMode.MANUAL
        assert page._has_unsaved is True


class TestCleanup:
    def test_cleanup_is_idempotent(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        page.cleanup()
        page.cleanup()  # second call must not raise
        assert page._curve_editor._cleaned_up is True


class TestCurveEditorRoleFloorIsWired:
    """[SAFETY] Register row `TT-a` — the DEC-095/DEC-312 authoring guardrail.

    `_curve_min_output_floor` is unit-tested in `test_controls_view.py`;
    `set_min_output` is unit-tested in `test_curve_editor.py`; and until now
    **nothing joined them**. Mutation B1 proved it: replacing
    `set_min_output(min_floor)` with `set_min_output(0.0)` left the full suite
    green (3473 passed), because no test drove the Controls-page inline
    curve-edit path. That is CLAUDE.md's most-repeated failure — *extracting a
    rule into a testable function does not test the call site*.

    **Not a hardware-safety hole**: the daemon independently clamps the pump/CPU
    floor at write time and on every eval tick (DEC-162), so a sub-30% point
    authored here is enforced up regardless. What breaks is truthfulness — the
    GUI would silently let a user draw what the daemon overrides.
    """

    PUMP_MEMBER_ID = "hwmon:nct6799:isa-0a20:pwm5:AIO_PUMP"

    def _profile_with_pump_curve(self, page, curve_type):
        from control_ofc.services.profile_service import ControlMember

        profile = page._get_current_profile()
        profile.curves.append(CurveConfig(id="c1", name="Pump", type=curve_type))
        profile.controls.append(
            LogicalControl(
                id="r1",
                name="Pump",
                mode=ControlMode.CURVE,
                curve_id="c1",
                members=[
                    ControlMember(
                        source="hwmon",
                        member_id=self.PUMP_MEMBER_ID,
                        member_label="AIO_PUMP",
                    )
                ],
            )
        )
        return profile

    def test_the_inline_editor_receives_the_pump_floor(self, qtbot, app_state, profile_service):
        """The mutation-confirmed survivor: `_curve_editor.set_min_output(...)`.

        Asserted as a RELATIONSHIP against the page's own floor helper, not as
        the literal 30, so the test still means the same thing if the role floor
        is ever re-derived — and cannot be satisfied by a hardcoded constant.
        """
        page = _page(qtbot, app_state, profile_service)
        profile = self._profile_with_pump_curve(page, CurveType.GRAPH)
        expected = page._curve_min_output_floor(profile, "c1")
        assert expected == 30.0, "precondition: a pump member must earn the 30% floor"

        page._on_edit_curve("c1")

        assert page._curve_editor._min_output == expected

    def test_a_chassis_only_curve_gets_the_lower_floor(self, qtbot, app_state, profile_service):
        """The other branch, so the assertion above is not always-true."""
        from control_ofc.services.profile_service import ControlMember

        page = _page(qtbot, app_state, profile_service)
        profile = page._get_current_profile()
        profile.curves.append(CurveConfig(id="c2", name="Chassis", type=CurveType.GRAPH))
        profile.controls.append(
            LogicalControl(
                id="r2",
                name="Chassis",
                mode=ControlMode.CURVE,
                curve_id="c2",
                members=[
                    ControlMember(
                        source="hwmon",
                        member_id="hwmon:nct6799:isa-0a20:pwm3:CHA_FAN1",
                        member_label="CHA_FAN1",
                    )
                ],
            )
        )
        expected = page._curve_min_output_floor(profile, "c2")
        assert expected == 20.0
        page._on_edit_curve("c2")
        assert page._curve_editor._min_output == expected

    def test_the_strictest_floor_wins_for_a_shared_curve(self, qtbot, app_state, profile_service):
        """One curve driving both a pump and a chassis control must be authored
        against 30, not 20 — otherwise a point legal for the chassis role is
        silently clamped at write time for the pump."""
        from control_ofc.services.profile_service import ControlMember

        page = _page(qtbot, app_state, profile_service)
        profile = self._profile_with_pump_curve(page, CurveType.GRAPH)
        profile.controls.append(
            LogicalControl(
                id="r2",
                name="Chassis",
                mode=ControlMode.CURVE,
                curve_id="c1",
                members=[
                    ControlMember(
                        source="hwmon",
                        member_id="hwmon:nct6799:isa-0a20:pwm3:CHA_FAN1",
                        member_label="CHA_FAN1",
                    )
                ],
            )
        )
        page._on_edit_curve("c1")
        assert page._curve_editor._min_output == 30.0

    def test_the_modal_branch_receives_the_same_floor(
        self, qtbot, app_state, profile_service, monkeypatch
    ):
        """DEC-312: a Flat curve is how the Fixed pump strategy is stored, so
        the MODAL branch was the one place a pump's speed could be authored
        below its floor. It is the identical unpinned call site, one `if` away
        from the inline editor's."""
        from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

        page = _page(qtbot, app_state, profile_service)
        profile = self._profile_with_pump_curve(page, CurveType.FLAT)
        expected = page._curve_min_output_floor(profile, "c1")

        built: list[CurveEditDialog] = []

        def fake_exec(dialog):
            built.append(dialog)
            return 0  # Rejected: this test is about construction, not the save.

        monkeypatch.setattr(CurveEditDialog, "exec", fake_exec, raising=True)
        page._on_edit_curve("c1")

        assert len(built) == 1, "a Flat curve must open the modal editor"
        assert built[0]._min_output == expected == 30.0
        # And the guardrail must reach the widget the user actually types into.
        assert built[0]._flat_spin.minimum() == expected
