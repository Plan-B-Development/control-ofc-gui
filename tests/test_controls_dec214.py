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
