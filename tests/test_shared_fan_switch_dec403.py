"""DEC-403 (`TS-bb`): a Dell machine's one BIOS fan switch.

On a ``dell_smm`` machine with the shared switch, a profile must control all of the
fans behind it or none. The GUI enforces it when a profile is saved, and activation
saves first, so both refuse; a profile saved earlier is flagged on the Controls page.

Assertions are relationships where the rule produces the text: the banner and the
refusals must carry exactly what ``ProfileService.shared_switch_error`` says, so a
call site that formats its own message, or skips the rule, fails.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QPushButton

from control_ofc.api.models import HwmonHeader
from control_ofc.services.profile_service import (
    ControlMember,
    LogicalControl,
    Profile,
    profile_file_path,
)
from control_ofc.services.shared_fan_switch import (
    SharedSwitchRuleError,
    SharedSwitchViolation,
    describe_shared_switch_violations,
    shared_switch_groups,
    shared_switch_violations,
)
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.pages.controls_page import ControlsPage

DEVICE = "dell_smm-isa-0000"


def _dell(index: int, *, enable: bool, writable: bool = True, label: str = "") -> HwmonHeader:
    label = label or f"Fan {index}"
    return HwmonHeader(
        id=f"hwmon:dell_smm:{DEVICE}:pwm{index}:{label}",
        label=label,
        chip_name="dell_smm",
        device_id=DEVICE,
        pwm_index=index,
        supports_enable=enable,
        is_writable=writable,
    )


# The shared kind: only pwm1 has an enable file (the driver's write-only switch).
CPU = _dell(1, enable=True, label="Processor Fan")
GPU = _dell(2, enable=False, label="Video Fan")
CASE = _dell(3, enable=False, label="Case Fan")
SHARED = [CPU, GPU, CASE]


def _profile(*headers: HwmonHeader, name: str = "Dell") -> Profile:
    return Profile(
        name=name,
        controls=[
            LogicalControl(
                name="Role",
                members=[ControlMember(source="hwmon", member_id=h.id) for h in headers],
            )
        ],
    )


# ── The rule ──────────────────────────────────────────────────────────


class TestSharedSwitchGroups:
    def test_the_shared_kind_is_one_group_in_pwm_order(self):
        assert shared_switch_groups([CASE, CPU, GPU]) == ((CPU.id, GPU.id, CASE.id),)

    def test_a_per_fan_switch_on_every_fan_couples_nothing(self):
        per_fan = [_dell(1, enable=True), _dell(2, enable=True), _dell(3, enable=True)]
        assert shared_switch_groups(per_fan) == ()

    def test_another_chip_of_the_same_shape_is_not_a_dell_switch(self):
        other = [
            HwmonHeader(
                id=h.id.replace("dell_smm", "nct6798"),
                chip_name="nct6798",
                device_id="nct6798.656",
                pwm_index=h.pwm_index,
                supports_enable=h.supports_enable,
                is_writable=True,
            )
            for h in SHARED
        ]
        assert shared_switch_groups(other) == ()

    def test_a_read_only_fan_is_not_part_of_all(self):
        read_only = _dell(4, enable=False, writable=False)
        assert shared_switch_groups([*SHARED, read_only]) == ((CPU.id, GPU.id, CASE.id),)

    def test_no_switch_on_pwm1_is_not_the_shared_kind(self):
        assert shared_switch_groups([_dell(1, enable=False), _dell(2, enable=False)]) == ()


class TestSharedSwitchViolations:
    def test_some_but_not_all_is_a_violation(self):
        assert shared_switch_violations(_profile(CPU), SHARED) == (
            SharedSwitchViolation(named=(CPU.id,), missing=(GPU.id, CASE.id)),
        )

    def test_a_fan_named_without_the_switch_header_is_a_violation_too(self):
        """The reverse direction: giving pwm1 back hands pwm2 to the BIOS."""
        assert shared_switch_violations(_profile(GPU), SHARED) == (
            SharedSwitchViolation(named=(GPU.id,), missing=(CPU.id, CASE.id)),
        )

    def test_all_or_none_is_fine(self):
        assert shared_switch_violations(_profile(CPU, GPU, CASE), SHARED) == ()
        assert shared_switch_violations(_profile(), SHARED) == ()

    def test_members_spread_over_several_roles_count_together(self):
        profile = _profile(CPU)
        profile.controls.append(
            LogicalControl(
                name="Other",
                members=[ControlMember(source="hwmon", member_id=h.id) for h in (GPU, CASE)],
            )
        )
        assert shared_switch_violations(profile, SHARED) == ()

    def test_the_message_names_what_to_add_and_what_to_remove(self):
        names = {CPU.id: "CPU", GPU.id: "GPU", CASE.id: "Case"}
        text = describe_shared_switch_violations(
            shared_switch_violations(_profile(CPU), SHARED), names.__getitem__
        )
        assert "Add GPU and Case to a fan role, or remove CPU." in text
        assert text.startswith("On this Dell, one BIOS switch controls all of its fans")


# ── The service: every save path refuses ──────────────────────────────


@pytest.fixture()
def dell_state(app_state):
    app_state.set_hwmon_headers(list(SHARED))
    return app_state


@pytest.fixture()
def gated(profile_service, dell_state):
    profile_service.attach_state(dell_state)
    return profile_service


class TestServiceGate:
    def test_a_violating_save_raises_and_writes_nothing(self, gated):
        profile = _profile(CPU)
        with pytest.raises(SharedSwitchRuleError) as exc:
            gated.save_profile(profile)
        assert exc.value.message == gated.shared_switch_error(profile).message
        assert not profile_file_path(profile.id).exists()

    def test_a_compliant_save_is_written(self, gated):
        profile = _profile(CPU, GPU, CASE)
        gated.save_profile(profile)
        assert profile_file_path(profile.id).exists()

    def test_the_message_names_fans_through_the_state(self, gated, dell_state):
        dell_state.fan_aliases[GPU.id] = "Graphics blower"
        message = gated.shared_switch_error(_profile(CPU)).message
        assert "Graphics blower" in message

    def test_activation_is_refused_before_the_daemon_is_asked(self, gated):
        profile = _profile(CPU)
        gated._profiles[profile.id] = profile
        client = Mock()
        outcome = gated.activate(profile.id, client=client)
        assert not outcome.activated
        assert outcome.refused_by_rule
        assert outcome.error == gated.shared_switch_error(profile).message
        client.activate_profile.assert_not_called()

    def test_a_compliant_profile_still_activates(self, gated):
        profile = _profile(CPU, GPU, CASE)
        gated._profiles[profile.id] = profile
        client = Mock()
        client.activate_profile.return_value = Mock(activated=True)
        outcome = gated.activate(profile.id, client=client)
        assert outcome.activated and not outcome.refused_by_rule
        client.activate_profile.assert_called_once()

    def test_a_refused_duplicate_leaves_nothing_behind(self, gated):
        profile = _profile(GPU)
        gated._profiles[profile.id] = profile
        before = {p.id for p in gated.profiles}
        with pytest.raises(SharedSwitchRuleError):
            gated.duplicate_profile(profile.id, "Copy")
        assert {p.id for p in gated.profiles} == before


# ── The wiring: the main window attaches the hardware view ────────────


@pytest.fixture()
def window(qtbot, dell_state, profile_service, settings_service):
    win = MainWindow(
        state=dell_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


class TestMainWindowWiring:
    def test_the_window_gives_the_rule_its_hardware(self, window, profile_service):
        """The call site: without `attach_state` the service cannot see a Dell."""
        assert window._profile_service is profile_service
        assert profile_service.shared_switch_error(_profile(CPU)) is not None
        assert profile_service.shared_switch_error(_profile(CPU, GPU, CASE)) is None

    def test_a_refused_sidebar_apply_shows_the_rule_message(self, window, profile_service):
        profile = _profile(CPU)
        profile_service._profiles[profile.id] = profile
        window._populate_sidebar_profiles(select_id=profile.id)
        assert window.sidebar.profile_combo.currentData() == profile.id  # precondition
        window.findChild(QPushButton, "Sidebar_Btn_applyProfile").click()
        assert window.error_banner._message_label.text() == (
            profile_service.shared_switch_error(profile).message
        )

    def test_the_dashboard_refusal_reaches_the_window_banner(self, window):
        window.dashboard_page.activation_refused.emit("the rule message")
        assert window.error_banner._message_label.text() == "the rule message"


class TestDashboardRefusal:
    def test_a_refused_apply_emits_the_rule_message(self, qtbot, window, profile_service):
        page = window.dashboard_page
        profile = _profile(GPU)
        profile_service._profiles[profile.id] = profile
        with qtbot.waitSignal(page.activation_refused, timeout=1000) as blocker:
            page._activate_profile_by_id(profile.id)
        assert blocker.args == [profile_service.shared_switch_error(profile).message]


# ── The Controls page: flag, and refuse Save / Rename ─────────────────


@pytest.fixture()
def page(qtbot, dell_state, gated):
    page = ControlsPage(state=dell_state, profile_service=gated)
    qtbot.addWidget(page)
    return page


def _show(page, profile: Profile) -> None:
    page._profile_service._profiles[profile.id] = profile
    page.select_profile(profile.id)


class TestControlsPage:
    def test_a_violating_profile_is_flagged_with_the_rule_message(self, page):
        profile = _profile(CPU)
        _show(page, profile)
        banner = page._shared_switch_banner
        assert banner.isVisibleTo(page)
        assert banner.text().startswith(page._profile_service.shared_switch_error(profile).message)

    def test_a_compliant_profile_is_not_flagged(self, page):
        _show(page, _profile(CPU, GPU, CASE))
        assert not page._shared_switch_banner.isVisibleTo(page)
        assert page._shared_switch_banner.text() == ""

    def test_the_flag_follows_the_hardware(self, page, dell_state):
        dell_state.set_hwmon_headers([])
        _show(page, _profile(CPU))
        assert not page._shared_switch_banner.isVisibleTo(page)  # precondition
        dell_state.set_hwmon_headers(list(SHARED))
        assert page._shared_switch_banner.isVisibleTo(page)

    def test_the_flag_follows_an_edit(self, page):
        """Every editor on the page reports through `_set_unsaved(True)`."""
        profile = _profile(CPU, GPU, CASE)
        _show(page, profile)
        assert not page._shared_switch_banner.isVisibleTo(page)  # precondition
        profile.controls[0].members.pop(0)
        page._set_unsaved(True)
        assert page._shared_switch_banner.isVisibleTo(page)

    def test_the_flag_follows_a_change_made_elsewhere(self, page):
        """`profiles_changed` re-renders through `_refresh_all` alone — no
        `_set_unsaved` — which is also the path a Revert takes."""
        profile = _profile(CPU, GPU, CASE)
        _show(page, profile)
        assert not page._shared_switch_banner.isVisibleTo(page)  # precondition
        profile.controls[0].members.pop()
        page._profile_service.profiles_changed.emit()
        assert page._shared_switch_banner.isVisibleTo(page)

    def test_save_refuses_and_keeps_the_edits_unsaved(self, page):
        profile = _profile(CPU)
        _show(page, profile)
        page._set_unsaved(True)
        page.findChild(QPushButton, "Controls_Btn_save").click()
        assert not profile_file_path(profile.id).exists()
        assert page._has_unsaved
        assert page._unsaved_label.text() == "Not saved — see the Dell fan note"

    def test_save_of_a_compliant_profile_still_writes(self, page):
        profile = _profile(CPU, GPU, CASE)
        _show(page, profile)
        page._set_unsaved(True)
        page.findChild(QPushButton, "Controls_Btn_save").click()
        assert profile_file_path(profile.id).exists()
        assert not page._has_unsaved

    def test_a_refused_rename_keeps_the_old_name(self, page, monkeypatch):
        profile = _profile(CPU, name="Before")
        _show(page, profile)
        monkeypatch.setattr(
            "control_ofc.ui.pages.controls_page.QInputDialog.getText",
            lambda *a, **k: ("After", True),
        )
        page._on_rename_profile()
        assert profile.name == "Before"
        assert page._unsaved_label.text() == "Not renamed — see the Dell fan note"
