"""DEC-231 (audit 2026-07-26, P1): the Controls-page member-edit and role-edit
*accept* paths must be exercised through the page, not just via the pure
functions they call.

The load-bearing assertion is ``test_edit_members_accept_reapplies_role_floor``:
it drives ``_on_edit_members`` to its accepted branch so the safety-relevant
``apply_role_floor(control)`` call (controls_page.py, the role-aware 30% CPU/pump
floor reapplied when membership changes) actually runs. A pure-function test of
``apply_role_floor`` cannot catch the regression the audit flagged — silently
dropping that call from the UI handler — because the page path was untested.
Removing the call leaves ``minimum_pct`` at 0, so this test goes red.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    LogicalControl,
)
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.widgets import fan_role_dialog, member_editor
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.fan_role_dialog import FanRoleDialog


def _page(qtbot, app_state, profile_service):
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    return page


def _profile_with_control(profile_service, control):
    profile = profile_service.create_profile("Draft")
    profile.controls.append(control)
    return profile


def _install_fake_member_editor(monkeypatch, *, accepted, new_members):
    """Replace MemberEditorDialog (imported inline in the handler) with a stub
    that reports ``accepted`` from ``exec()`` and returns ``new_members``."""

    class _FakeMemberEditor:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return 1 if accepted else 0

        def get_members(self):
            return new_members

    monkeypatch.setattr(member_editor, "MemberEditorDialog", _FakeMemberEditor)


def _install_fake_role_dialog(monkeypatch, *, accepted, result):
    class _FakeRoleDialog:
        def __init__(self, *args, **kwargs):
            pass

        def set_edit_members_callback(self, _cb):
            pass

        def exec(self):
            return 1 if accepted else 0

        def get_result(self):
            return result

    monkeypatch.setattr(fan_role_dialog, "FanRoleDialog", _FakeRoleDialog)


# --- P1a: _on_edit_members accept path (the safety-floor reapply) ------------


class TestEditMembersAcceptPath:
    def test_edit_members_accept_reapplies_role_floor(
        self, qtbot, app_state, profile_service, monkeypatch
    ):
        """Adding a pump member through the member editor must raise the control's
        role floor to 30% via apply_role_floor. MUTATION KILL: if the
        ``apply_role_floor(control)`` call is dropped from the accept branch,
        minimum_pct stays 0 and this assertion fails."""
        page = _page(qtbot, app_state, profile_service)
        ctrl = LogicalControl(
            name="Group",
            mode=ControlMode.CURVE,
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
            minimum_pct=0.0,
        )
        profile = _profile_with_control(profile_service, ctrl)
        page.select_profile(profile.id)
        page._set_unsaved(False)

        new_members = [
            ControlMember(source="hwmon", member_id="hwmon:nct6775:pwm1", member_label="AIO_PUMP")
        ]
        _install_fake_member_editor(monkeypatch, accepted=True, new_members=new_members)

        page._on_edit_members(ctrl.id)

        assert ctrl.members == new_members
        assert ctrl.minimum_pct == 30.0  # <-- the floor reapply must have run
        assert page.has_unsaved_changes()

    def test_edit_members_reject_leaves_control_untouched(
        self, qtbot, app_state, profile_service, monkeypatch
    ):
        page = _page(qtbot, app_state, profile_service)
        original = [ControlMember(source="openfan", member_id="openfan:ch00")]
        ctrl = LogicalControl(
            name="Group", mode=ControlMode.CURVE, members=original, minimum_pct=0.0
        )
        profile = _profile_with_control(profile_service, ctrl)
        page.select_profile(profile.id)
        page._set_unsaved(False)

        _install_fake_member_editor(
            monkeypatch,
            accepted=False,
            new_members=[
                ControlMember(
                    source="hwmon", member_id="hwmon:nct6775:pwm1", member_label="AIO_PUMP"
                )
            ],
        )

        page._on_edit_members(ctrl.id)

        assert ctrl.members == original
        assert ctrl.minimum_pct == 0.0
        assert not page.has_unsaved_changes()


# --- P1b: _on_edit_role accept path ------------------------------------------


class TestEditRoleAcceptPath:
    def test_edit_role_accept_applies_name_mode_curve(
        self, qtbot, app_state, profile_service, monkeypatch
    ):
        page = _page(qtbot, app_state, profile_service)
        ctrl = LogicalControl(
            name="Old", mode=ControlMode.CURVE, curve_id="", manual_output_pct=50.0
        )
        profile = _profile_with_control(profile_service, ctrl)
        page.select_profile(profile.id)
        page._set_unsaved(False)

        _install_fake_role_dialog(
            monkeypatch,
            accepted=True,
            result={
                "name": "New",
                "mode": ControlMode.MANUAL,
                "curve_id": "curve-xyz",
                "manual_output_pct": 42.0,
                "gpu_fan_zero_rpm": {},
                "delete": False,
            },
        )

        page._on_edit_role(ctrl.id)

        assert ctrl.name == "New"
        assert ctrl.mode == ControlMode.MANUAL
        assert ctrl.curve_id == "curve-xyz"
        assert ctrl.manual_output_pct == 42.0
        assert page.has_unsaved_changes()

    def test_edit_role_delete_routes_to_delete(
        self, qtbot, app_state, profile_service, monkeypatch
    ):
        page = _page(qtbot, app_state, profile_service)
        ctrl = LogicalControl(name="Doomed", mode=ControlMode.CURVE)
        profile = _profile_with_control(profile_service, ctrl)
        page.select_profile(profile.id)

        _install_fake_role_dialog(
            monkeypatch,
            accepted=True,
            result={"delete": True, "name": "Doomed", "mode": ControlMode.CURVE, "curve_id": ""},
        )

        page._on_edit_role(ctrl.id)

        assert all(c.id != ctrl.id for c in profile.controls)

    def test_edit_role_persists_gpu_zero_rpm(self, qtbot, app_state, profile_service, monkeypatch):
        page = _page(qtbot, app_state, profile_service)
        gpu = ControlMember(source="amd_gpu", member_id="amd_gpu:0000:03:00.0", fan_zero_rpm=False)
        ctrl = LogicalControl(name="GPU", mode=ControlMode.CURVE, members=[gpu])
        profile = _profile_with_control(profile_service, ctrl)
        page.select_profile(profile.id)

        _install_fake_role_dialog(
            monkeypatch,
            accepted=True,
            result={
                "name": "GPU",
                "mode": ControlMode.CURVE,
                "curve_id": "",
                "manual_output_pct": 50.0,
                "gpu_fan_zero_rpm": {"amd_gpu:0000:03:00.0": True},
                "delete": False,
            },
        )

        page._on_edit_role(ctrl.id)

        assert ctrl.members[0].fan_zero_rpm is True

    def test_edit_role_reject_leaves_control_untouched(
        self, qtbot, app_state, profile_service, monkeypatch
    ):
        page = _page(qtbot, app_state, profile_service)
        ctrl = LogicalControl(name="Old", mode=ControlMode.CURVE, curve_id="c1")
        profile = _profile_with_control(profile_service, ctrl)
        page.select_profile(profile.id)
        page._set_unsaved(False)

        _install_fake_role_dialog(
            monkeypatch,
            accepted=False,
            result={"name": "New", "mode": ControlMode.MANUAL, "curve_id": "c2", "delete": False},
        )

        page._on_edit_role(ctrl.id)

        assert ctrl.name == "Old"
        assert ctrl.mode == ControlMode.CURVE
        assert ctrl.curve_id == "c1"
        assert not page.has_unsaved_changes()


# --- P3 security: untrusted profile strings render as PlainText ---------------


class TestUntrustedLabelsPlainText:
    """Profile-sourced names (control name, member alias/label) are untrusted and
    must render as PlainText so stray markup is never reinterpreted as rich text
    (matches the fan_control_card / warnings_view convention)."""

    def test_control_card_name_and_member_labels_are_plaintext(self, qtbot):
        ctrl = LogicalControl(
            id="c1",
            name="Group",
            mode=ControlMode.CURVE,
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        card = ControlCard(ctrl, [])
        qtbot.addWidget(card)
        card.update_control(ctrl, [])

        name = card.findChild(QLabel, "ControlCard_Label_c1")
        assert name is not None
        assert name.textFormat() == Qt.TextFormat.PlainText

        member = card._member_row_name["openfan:ch00"]
        assert member.textFormat() == Qt.TextFormat.PlainText

        # DEC-231: the curve-name label is profile-authored too.
        curve = card.findChild(QLabel, "ControlCard_Label_curve_c1")
        assert curve is not None
        assert curve.textFormat() == Qt.TextFormat.PlainText

    def test_fan_role_dialog_member_chip_is_plaintext(self, qtbot):
        ctrl = LogicalControl(
            id="r",
            name="Role",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        dlg = FanRoleDialog(ctrl, [])
        qtbot.addWidget(dlg)
        chip = dlg.findChild(QLabel, "FanRoleDialog_Chip_member_openfan:ch00")
        assert chip is not None
        assert chip.textFormat() == Qt.TextFormat.PlainText

    def test_fan_role_dialog_gpu_member_label_is_plaintext(self, qtbot):
        ctrl = LogicalControl(
            id="r",
            name="GPU",
            members=[ControlMember(source="amd_gpu", member_id="amd_gpu:0000:03:00.0")],
        )
        dlg = FanRoleDialog(ctrl, [])
        qtbot.addWidget(dlg)
        label = dlg.findChild(QLabel, "FanRoleDialog_Label_gpuMember_amd_gpu:0000:03:00.0")
        assert label is not None
        assert label.textFormat() == Qt.TextFormat.PlainText

    def test_dialog_title_is_plaintext(self, qtbot):
        # DEC-231: ModalDialog titles embed untrusted profile/control names
        # (e.g. "Edit Fan Role: {control.name}") — hardened at the shared base.
        ctrl = LogicalControl(id="r", name="Role", members=[])
        dlg = FanRoleDialog(ctrl, [])
        qtbot.addWidget(dlg)
        title = dlg.findChild(QLabel, "ModalDialog_Label_title")
        assert title is not None
        assert title.textFormat() == Qt.TextFormat.PlainText


# --- P3 concurrency: queued override results dropped after cleanup ------------


class TestOverrideResultGuardAfterCleanup:
    """A take/renew result queued before cleanup() must not mutate torn-down
    state — the _is_shut_down guard drops it."""

    def test_renew_result_after_cleanup_is_ignored(self, qtbot, app_state, profile_service):
        from control_ofc.api.errors import DaemonError

        page = _page(qtbot, app_state, profile_service)
        page._overrides = {"c1": "tok"}
        page.cleanup()
        assert page._is_shut_down is True

        page._on_renew_result(
            "c1", "tok", None, DaemonError(status=404, code="override_expired", message="x")
        )
        # Without the guard this token matches and takes the lapse path, popping
        # "c1"; the guard returns early so the map is untouched.
        assert page._overrides == {"c1": "tok"}

    def test_take_result_after_cleanup_is_ignored(self, qtbot, app_state, profile_service):
        from control_ofc.api.errors import DaemonError

        page = _page(qtbot, app_state, profile_service)
        page._manual_intent = {"c1"}
        page.cleanup()

        page._on_take_result(
            "c1", 50, None, DaemonError(status=503, code="hardware_unavailable", message="x")
        )
        # Without the guard the error path discards intent; the guard keeps it.
        assert page._manual_intent == {"c1"}


class TestOverrideReleasedMidRenew:
    """Releasing while a renew is in flight must not strand the fan.

    The override worker is sequential, so a queued renew is sent BEFORE the
    release. The daemon answers the renew with a fresh token, which supersedes
    the one the release then carries — so the release is rejected (409
    stale_fencing_token, suppressed) and the daemon keeps pinning the fan under
    a token the GUI never recorded. The card meanwhile shows curve control, so
    the fan ignores user intent until the ~15 s deadman expires.
    """

    def _capture_releases(self, page):
        # Fencing tokens are monotonic ints (OverrideGrant.override_token), and
        # _request_release is Signal(str, int) — a str token cannot cross it.
        released: list[tuple[str, int]] = []
        page._request_release.connect(lambda cid, tok: released.append((cid, tok)))
        return released

    def test_successful_renew_for_released_control_releases_the_orphan(
        self, qtbot, app_state, profile_service
    ):
        page = _page(qtbot, app_state, profile_service)
        released = self._capture_releases(page)

        # The control was released while the renew was in flight, so it is no
        # longer in _overrides. The renew then succeeds with a NEW token.
        page._overrides = {}
        page._on_renew_result("c1", 1, 2, None)

        assert released == [("c1", 2)], "the orphaned token must be released immediately"
        assert "c1" not in page._overrides

    def test_renew_after_repin_does_not_release_the_new_pin(
        self, qtbot, app_state, profile_service
    ):
        """A re-pin (slider drag) also makes the held token differ from the
        renewed one — but there the user still wants control, so the newer pin
        must be left alone."""
        page = _page(qtbot, app_state, profile_service)
        released = self._capture_releases(page)

        page._overrides = {"c1": 3}  # newer token from a re-pin
        page._on_renew_result("c1", 1, 2, None)

        assert released == []
        assert page._overrides == {"c1": 3}, "the re-pin token must not be clobbered"

    def test_large_fencing_token_survives_the_signal(self, qtbot, app_state, profile_service):
        """Tokens cross the worker signals as ``object``, not ``int``.

        A queued cross-thread connection marshals through 32-bit
        ``QMetaType::Int``, so a monotonic token past 2**31 would be truncated
        (or rejected) — and a mangled token means the release silently fails
        fencing and the fan stays pinned until the deadman.
        """
        page = _page(qtbot, app_state, profile_service)
        released = self._capture_releases(page)

        big = 2**31 + 7
        page._overrides = {}
        page._on_renew_result("c1", 1, big, None)

        assert released == [("c1", big)], "the token must cross the signal unmangled"

    def test_successful_renew_still_advances_a_held_token(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        released = self._capture_releases(page)

        page._overrides = {"c1": 1}
        page._on_renew_result("c1", 1, 2, None)

        assert released == []
        assert page._overrides == {"c1": 2}
