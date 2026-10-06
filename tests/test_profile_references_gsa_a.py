"""GSA-a: ordinary Controls-page edits must not leave a profile the daemon refuses.

The daemon rejects the whole profile when any reference inside it does not resolve
(`control-ofc-daemon/daemon/src/profile.rs`, `validate_profile`): a curve-mode control
whose `curve_id` is empty or unknown, a Mix input that is not a curve of the profile,
a Sync curve whose `sync_control_id` is empty or unknown. Before the fix, deleting a
curve left curve-mode controls with no curve and Mix curves naming it, deleting a
control left Sync curves aimed at nothing, a new Sync curve had no target, and a new
control in a profile without curves had no curve — each saved as "daemon offline"
(`FFA-d`) and none could be published.

`_daemon_reference_errors` is that rule restated from the daemon's source, as the
independent right-hand side.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QPushButton

from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
    remove_curve,
    sync_curves_targeting,
)
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.widgets.fan_role_dialog import FanRoleDialog


def _daemon_reference_errors(profile: Profile) -> list[str]:
    """The reference-integrity errors `validate_profile` reports (profile.rs)."""
    doc = profile.to_dict()
    curve_ids = {c["id"] for c in doc["curves"]}
    control_ids = {c["id"] for c in doc["controls"]}
    errors: list[str] = []
    for i, curve in enumerate(doc["curves"]):
        if curve["type"] == "mix":
            errors += [
                f"curves[{i}].mix_curve_ids[{k}]"
                for k, mid in enumerate(curve.get("mix_curve_ids", []))
                if mid not in curve_ids
            ]
        elif curve["type"] == "sync":
            sid = curve.get("sync_control_id", "")
            if not sid or sid not in control_ids:
                errors.append(f"curves[{i}].sync_control_id")
    for i, ctrl in enumerate(doc["controls"]):
        if ctrl["mode"] == "curve" and (not ctrl["curve_id"] or ctrl["curve_id"] not in curve_ids):
            errors.append(f"controls[{i}].curve_id")
    return errors


def _profile() -> Profile:
    """Two graph curves, a Mix of both, a Sync mirroring the CPU role, and roles on
    each — every reference resolving."""
    p = Profile(id="gsa", name="GSA")
    p.curves = [
        CurveConfig(id="cpu", name="CPU", type=CurveType.GRAPH),
        CurveConfig(id="gpu", name="GPU", type=CurveType.GRAPH),
        CurveConfig(id="mix", name="Both", type=CurveType.MIX, mix_curve_ids=["cpu", "gpu"]),
        CurveConfig(id="sync", name="Follow CPU", type=CurveType.SYNC, sync_control_id="r_cpu"),
    ]
    p.controls = [
        LogicalControl(id="r_cpu", name="CPU fans", mode=ControlMode.CURVE, curve_id="cpu"),
        LogicalControl(id="r_gpu", name="GPU fans", mode=ControlMode.CURVE, curve_id="gpu"),
        LogicalControl(id="r_case", name="Case", mode=ControlMode.CURVE, curve_id="sync"),
    ]
    assert _daemon_reference_errors(p) == [], "precondition: the fixture is valid"
    return p


def _page_on(qtbot, app_state, profile_service, profile: Profile) -> ControlsPage:
    profile_service._profiles[profile.id] = profile
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page.select_profile(profile.id)
    assert page._get_current_profile() is profile
    return page


class TestHelpers:
    def test_remove_curve_unlinks_its_roles_and_drops_it_from_every_mix(self):
        p = _profile()

        remove_curve(p, "gpu")

        assert [c.id for c in p.curves] == ["cpu", "mix", "sync"]
        role = next(c for c in p.controls if c.id == "r_gpu")
        assert role.mode == ControlMode.MANUAL and role.curve_id == ""
        assert next(c for c in p.curves if c.id == "mix").mix_curve_ids == ["cpu"]
        # The opposite branch: a role on another curve is untouched.
        assert next(c for c in p.controls if c.id == "r_cpu").curve_id == "cpu"
        assert _daemon_reference_errors(p) == []

    def test_sync_curves_targeting_finds_only_the_mirrors(self):
        p = _profile()
        assert [c.id for c in sync_curves_targeting(p, "r_cpu")] == ["sync"]
        assert sync_curves_targeting(p, "r_gpu") == []


class TestDeleteCurve:
    def test_deleting_a_used_curve_leaves_a_profile_the_daemon_accepts(
        self, qtbot, app_state, profile_service
    ):
        p = _profile()
        page = _page_on(qtbot, app_state, profile_service, p)

        page._curve_cards["gpu"].delete_requested.emit("gpu")

        assert "gpu" not in {c.id for c in p.curves}
        assert next(c for c in p.controls if c.id == "r_gpu").mode == ControlMode.MANUAL
        assert _daemon_reference_errors(p) == []
        assert page._has_unsaved is True


class TestDeleteControl:
    def _delete_button(self, page: ControlsPage, control_id: str) -> QPushButton:
        btn = page._control_cards[control_id].findChild(
            QPushButton, f"ControlCard_Btn_delete_{control_id}"
        )
        assert btn is not None
        return btn

    def test_a_role_a_sync_curve_mirrors_is_not_deleted_and_the_curve_is_named(
        self, qtbot, app_state, profile_service, monkeypatch
    ):
        p = _profile()
        page = _page_on(qtbot, app_state, profile_service, p)
        shown: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda _parent, _title, text, *a, **k: shown.append(text)
        )

        self._delete_button(page, "r_cpu").click()

        assert "r_cpu" in {c.id for c in p.controls}
        assert len(shown) == 1 and "Follow CPU" in shown[0] and "CPU fans" in shown[0]
        assert _daemon_reference_errors(p) == []

    def test_a_role_nothing_mirrors_is_deleted(self, qtbot, app_state, profile_service):
        p = _profile()
        page = _page_on(qtbot, app_state, profile_service, p)

        self._delete_button(page, "r_gpu").click()

        assert "r_gpu" not in {c.id for c in p.controls}
        assert _daemon_reference_errors(p) == []


class TestAddSyncCurve:
    def _sync_action(self, page: ControlsPage):
        menu = page._build_add_curve_menu()
        return next(a for a in menu.actions() if a.text() == "Sync Curve")

    def test_a_new_sync_curve_mirrors_the_first_role(self, qtbot, app_state, profile_service):
        p = _profile()
        page = _page_on(qtbot, app_state, profile_service, p)
        action = self._sync_action(page)
        assert action.isEnabled()

        action.trigger()

        new = p.curves[-1]
        assert new.type == CurveType.SYNC
        assert new.sync_control_id == p.controls[0].id
        assert _daemon_reference_errors(p) == []

    def test_sync_is_not_offered_without_a_role_to_mirror(self, qtbot, app_state, profile_service):
        p = Profile(id="bare", name="Bare")
        page = _page_on(qtbot, app_state, profile_service, p)
        action = self._sync_action(page)

        assert not action.isEnabled()
        assert action.toolTip()
        # Graph stays available — only Sync needs a role.
        menu = page._build_add_curve_menu()
        assert next(a for a in menu.actions() if a.text() == "Graph Curve").isEnabled()


class TestNewRole:
    def test_a_new_role_without_a_curve_to_follow_starts_in_manual(
        self, qtbot, app_state, profile_service
    ):
        p = Profile(id="bare", name="Bare")
        page = _page_on(qtbot, app_state, profile_service, p)

        page._on_new_control(name="Fans")

        assert p.controls[-1].mode == ControlMode.MANUAL
        assert _daemon_reference_errors(p) == []

    def test_a_new_role_follows_the_first_curve_when_there_is_one(
        self, qtbot, app_state, profile_service
    ):
        p = _profile()
        page = _page_on(qtbot, app_state, profile_service, p)

        page._on_new_control(name="More fans")

        assert p.controls[-1].mode == ControlMode.CURVE
        assert p.controls[-1].curve_id == p.curves[0].id


class TestFanRoleDialogWithoutCurves:
    def test_curve_based_is_not_offered_with_no_curve(self, qtbot):
        role = LogicalControl(id="r", name="R", mode=ControlMode.CURVE, curve_id="")
        dlg = FanRoleDialog(role, [])
        qtbot.addWidget(dlg)

        assert not dlg._mode_combo.model().item(0).isEnabled()
        assert dlg.get_result()["mode"] == ControlMode.MANUAL

    def test_curve_based_stays_offered_with_a_curve(self, qtbot):
        role = LogicalControl(id="r", name="R", mode=ControlMode.CURVE, curve_id="c")
        dlg = FanRoleDialog(role, [CurveConfig(id="c", name="C", type=CurveType.GRAPH)])
        qtbot.addWidget(dlg)

        assert dlg._mode_combo.model().item(0).isEnabled()
        assert dlg.get_result()["mode"] == ControlMode.CURVE
