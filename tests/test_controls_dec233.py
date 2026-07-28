"""DEC-233: Controls page UX pass.

Covers the Close-editor button + editing highlight, the Save rename, the
edited-profile orientation label, the "Set up ▾" menu, the actionable
Unassigned-Fans button (list + quick-assign), and Revert.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from control_ofc.api.models import FanReading, HwmonHeader
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


def _graph(profile, cid="g1", name="CPU"):
    curve = CurveConfig(id=cid, name=name, type=CurveType.GRAPH)
    profile.curves.append(curve)
    return curve


def _writable_header(hid="hwmon:x:pwm2", label="SYS_FAN"):
    return HwmonHeader(id=hid, label=label, chip_name="nct6799", is_writable=True, pwm_index=2)


class TestSaveRename:
    def test_save_button_text_is_save(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        assert page.findChild(QPushButton, "Controls_Btn_save").text() == "Save"


class TestEditedProfileLabel:
    def test_label_shows_current_profile(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        assert page._edited_profile_label.text() == f"Editing: {prof.name}"

    def test_label_updates_on_new_profile(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        draft = profile_service.create_profile("Draft-X")
        page.select_profile(draft.id)
        assert page._edited_profile_label.text() == "Editing: Draft-X"


class TestSetupMenu:
    def test_setup_actions_present_and_gated(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        assert page.findChild(QPushButton, "Controls_Btn_setup") is not None
        # The wizard is always offered; AIO/GPU stay hidden until detected.
        assert page._wizard_action.isVisible() is True
        assert page._configure_aio_action.isVisible() is False
        assert page._dedicate_gpu_action.isVisible() is False


class TestEditingHighlight:
    def test_edit_graph_curve_lights_card_and_reveals_actions(
        self, qtbot, app_state, profile_service
    ):
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        _graph(prof, "g1")
        page._refresh_all()
        page._on_edit_curve("g1")
        card = page._curve_cards["g1"]
        assert card.is_editing is True
        assert card.property("editing") is True
        assert page._editing_curve_id == "g1"
        # Contextual header actions are revealed (isHidden, not isVisible, since
        # the offscreen page is never shown).
        assert not page._close_editor_btn.isHidden()
        assert not page._test_curve_btn.isHidden()

    def test_close_editor_clears_highlight_and_actions(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        _graph(prof, "g1")
        page._refresh_all()
        page._on_edit_curve("g1")
        page._close_editor()
        card = page._curve_cards["g1"]
        assert card.is_editing is False
        assert page._editing_curve_id is None
        assert page._close_editor_btn.isHidden()
        assert page._test_curve_btn.isHidden()
        assert not page._editor_placeholder.isHidden()

    def test_switching_curve_moves_highlight(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        _graph(prof, "g1", "A")
        _graph(prof, "g2", "B")
        page._refresh_all()
        page._on_edit_curve("g1")
        page._on_edit_curve("g2")
        assert page._curve_cards["g1"].is_editing is False
        assert page._curve_cards["g2"].is_editing is True
        assert page._editing_curve_id == "g2"

    def test_highlight_survives_grid_rebuild(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        _graph(prof, "g1")
        page._refresh_all()
        page._on_edit_curve("g1")
        page._refresh_curves_grid(prof)  # e.g. add/reorder rebuilds the grid
        assert page._curve_cards["g1"].is_editing is True
        assert page._editing_curve_id == "g1"

    def test_delete_edited_curve_closes_editor(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        _graph(prof, "g1")
        page._refresh_all()
        page._on_edit_curve("g1")
        page._on_delete_curve("g1")
        assert page._editing_curve_id is None
        assert "g1" not in page._curve_cards
        assert not page._editor_placeholder.isHidden()


class TestRevert:
    def test_revert_button_tracks_unsaved(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        assert page._revert_btn.isEnabled() is False
        page._set_unsaved(True)
        assert page._revert_btn.isEnabled() is True
        page._set_unsaved(False)
        assert page._revert_btn.isEnabled() is False

    def test_revert_noop_when_clean(self, qtbot, app_state, profile_service):
        """The `_has_unsaved` guard must short-circuit BEFORE the confirm dialog —
        otherwise a "Revert?" prompt pops with nothing to revert. Isolated with a
        confirm spy so the conftest modal-decline can't mask a deleted guard."""
        page = _page(qtbot, app_state, profile_service)
        confirmed = []
        page.confirm_revert = lambda: confirmed.append(True) or True
        page._set_unsaved(False)
        before = len(page._get_current_profile().controls)
        page._on_revert()
        assert confirmed == []  # never reached the confirm → the guard fired
        assert len(page._get_current_profile().controls) == before

    def test_revert_discards_in_memory_edits(self, qtbot, app_state, profile_service, monkeypatch):
        page = _page(qtbot, app_state, profile_service)
        monkeypatch.setattr(page, "confirm_revert", lambda: True)
        prof = page._get_current_profile()
        before = len(prof.controls)
        prof.controls.append(LogicalControl(id="tmp", name="Temp", mode=ControlMode.MANUAL))
        page._set_unsaved(True)
        page._on_revert()
        assert page._has_unsaved is False
        reverted = page._get_current_profile()
        assert len(reverted.controls) == before
        assert not any(c.id == "tmp" for c in reverted.controls)

    def test_revert_cancelled_keeps_edits(self, qtbot, app_state, profile_service, monkeypatch):
        page = _page(qtbot, app_state, profile_service)
        monkeypatch.setattr(page, "confirm_revert", lambda: False)
        prof = page._get_current_profile()
        prof.controls.append(LogicalControl(id="tmp", name="Temp", mode=ControlMode.MANUAL))
        page._set_unsaved(True)
        page._on_revert()
        assert page._has_unsaved is True
        assert any(c.id == "tmp" for c in page._get_current_profile().controls)


class TestUnassignedFans:
    def test_button_reflects_count(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        page._unassigned_fan_ids = ["a", "b"]
        page._update_unassigned_button()
        assert page._unassigned_btn.text() == "Unassigned Fans (2)"
        assert page._unassigned_btn.isEnabled() is True
        page._unassigned_fan_ids = []
        page._update_unassigned_button()
        assert page._unassigned_btn.text() == "All fans assigned"
        assert page._unassigned_btn.isEnabled() is False

    def test_make_member_skips_readonly_and_gpu(self, qtbot, app_state, profile_service):
        app_state.hwmon_headers = [
            _writable_header("hwmon:x:pwm2", "SYS_FAN"),
            HwmonHeader(
                id="hwmon:ro:pwm1", label="RO", chip_name="nct6799", is_writable=False, pwm_index=1
            ),
        ]
        app_state.fans = [
            FanReading(id="hwmon:x:pwm2", source="hwmon", rpm=800),
            FanReading(id="hwmon:ro:pwm1", source="hwmon", rpm=0),
            FanReading(id="openfan:ch01", source="openfan", rpm=600),
            FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=0),
        ]
        page = _page(qtbot, app_state, profile_service)
        assert page._make_member_for_fan("hwmon:x:pwm2") is not None
        assert page._make_member_for_fan("openfan:ch01") is not None
        assert page._make_member_for_fan("hwmon:ro:pwm1") is None
        assert page._make_member_for_fan("amd_gpu:0000:03:00.0") is None

    def test_assign_member_adds_and_marks_unsaved(self, qtbot, app_state, profile_service):
        app_state.hwmon_headers = [_writable_header()]
        app_state.fans = [FanReading(id="hwmon:x:pwm2", source="hwmon", rpm=800)]
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        ctrl = LogicalControl(id="r1", name="Case", mode=ControlMode.CURVE)
        prof.controls.append(ctrl)
        page._refresh_all()
        page._unassigned_fan_ids = ["hwmon:x:pwm2"]
        member = page._make_member_for_fan("hwmon:x:pwm2")
        page._assign_member_to_control("r1", member)
        assigned = next(c for c in page._get_current_profile().controls if c.id == "r1")
        assert any(m.member_id == "hwmon:x:pwm2" for m in assigned.members)
        assert page._has_unsaved is True
        assert "hwmon:x:pwm2" not in page._unassigned_fan_ids

    def test_build_menu_offers_submenu_for_writable_fan(self, qtbot, app_state, profile_service):
        app_state.hwmon_headers = [_writable_header()]
        app_state.fans = [FanReading(id="hwmon:x:pwm2", source="hwmon", rpm=800)]
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        prof.controls.append(LogicalControl(id="r1", name="Case", mode=ControlMode.CURVE))
        page._refresh_all()
        page._unassigned_fan_ids = ["hwmon:x:pwm2"]
        menu = page._build_unassigned_menu()
        assert menu is not None
        # The writable fan gets a submenu of roles to add it to.
        assert any(a.menu() is not None for a in menu.actions())

    def test_build_menu_none_when_all_assigned(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        page._unassigned_fan_ids = []
        assert page._build_unassigned_menu() is None


class TestReviewFollowups:
    """Regression tests added from the DEC-233 pre-release review."""

    def test_quick_assign_pump_fan_applies_30pct_floor(self, qtbot, app_state, profile_service):
        """P1: quick-assigning a pump/CPU fan must raise the control to the 30%
        safety floor via apply_role_floor (DEC-095/162), same as the member editor.
        A mutant dropping that call would otherwise survive."""
        app_state.hwmon_headers = [
            HwmonHeader(
                id="hwmon:x:pwm5", label="PUMP", chip_name="nct6799", is_writable=True, pwm_index=5
            )
        ]
        app_state.fans = [FanReading(id="hwmon:x:pwm5", source="hwmon", rpm=2200)]
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        ctrl = LogicalControl(id="r1", name="Cooling", mode=ControlMode.CURVE, minimum_pct=0.0)
        prof.controls.append(ctrl)
        page._refresh_all()
        member = page._make_member_for_fan("hwmon:x:pwm5")
        assert member is not None
        assert "pump" in member.member_label.lower()
        page._assign_member_to_control("r1", member)
        assert ctrl.minimum_pct >= 30.0

    def test_quick_assign_aio_header_keeps_role_tag(self, qtbot, app_state, profile_service):
        """P2: an AIO header whose resolved label carries no cpu/pump keyword must
        still get the '(AIO …)' role tag on quick-assign (mirroring _on_edit_members),
        so it keeps the 30% floor — the 'aio' keyword is the only role signal."""
        app_state.hwmon_headers = [
            HwmonHeader(
                id="hwmon:aio:pwm1",
                label="FAN2",
                chip_name="nct6799",
                is_writable=True,
                is_aio=True,
                pwm_index=1,
            )
        ]
        app_state.fans = [FanReading(id="hwmon:aio:pwm1", source="hwmon", rpm=1500)]
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        ctrl = LogicalControl(id="r1", name="Loop", mode=ControlMode.CURVE, minimum_pct=0.0)
        prof.controls.append(ctrl)
        page._refresh_all()
        member = page._make_member_for_fan("hwmon:aio:pwm1")
        assert member is not None
        assert "aio" in member.member_label.lower()
        page._assign_member_to_control("r1", member)
        assert ctrl.minimum_pct >= 30.0

    def test_profile_switch_closes_stranded_editor(self, qtbot, app_state, profile_service):
        """P2: switching the viewed profile while a graph curve is open returns the
        editor to its placeholder — it must not strand its pane/title/actions on the
        old profile's curve."""
        page = _page(qtbot, app_state, profile_service)
        p1 = page._get_current_profile()
        _graph(p1, "g1", "CPU")
        page._refresh_all()
        page._on_edit_curve("g1")
        assert page._editor_placeholder.isHidden()  # editor is shown
        p2 = profile_service.create_profile("Other")
        page.select_profile(p2.id)
        assert page._editing_curve_id is None
        assert not page._editor_placeholder.isHidden()  # back to placeholder
        assert page._close_editor_btn.isHidden()
        assert page._test_curve_btn.isHidden()
        assert page._editor_title.text() == "Editing: —"

    def test_composite_curve_edit_does_not_highlight(self, qtbot, app_state, profile_service):
        """P2: composite curves edit in a modal dialog, not the inline editor, so
        editing one must not light a card or reveal the editor chrome."""
        page = _page(qtbot, app_state, profile_service)
        prof = page._get_current_profile()
        prof.curves.append(CurveConfig(id="f1", name="Flat", type=CurveType.FLAT))
        page._refresh_all()
        page._on_edit_curve("f1")  # opens CurveEditDialog (conftest → Rejected)
        assert page._editing_curve_id is None
        assert page._curve_cards["f1"].is_editing is False
        assert page._close_editor_btn.isHidden()

    def test_revert_none_when_never_saved(self, qtbot, app_state, profile_service, monkeypatch):
        """P2: reverting a brand-new draft with no stored version surfaces a message
        and does not fabricate a reload."""
        page = _page(qtbot, app_state, profile_service)
        monkeypatch.setattr(page, "confirm_revert", lambda: True)
        # A viewed draft whose id is not on disk / daemon → reload_profile returns None.
        monkeypatch.setattr(page._profile_service, "reload_profile", lambda _id: None)
        page._set_unsaved(True)
        page._on_revert()
        assert page._has_unsaved is True  # nothing was reverted
        assert "Nothing to revert" in page._unsaved_label.text()

    def test_untrusted_labels_are_plaintext(self, qtbot, app_state, profile_service):
        """P2 (DEC-231): the edited-profile label and editor title render untrusted
        names as plain text so imported markup can't be reinterpreted."""
        page = _page(qtbot, app_state, profile_service)
        assert page._edited_profile_label.textFormat() == Qt.TextFormat.PlainText
        assert page._editor_title.textFormat() == Qt.TextFormat.PlainText
