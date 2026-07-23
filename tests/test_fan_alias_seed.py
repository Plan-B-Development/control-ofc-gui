"""DEC-228 — adopting profile member labels as fan aliases.

The bug this pins: the GUI kept two per-fan name stores that never reconciled.
Users who named their fans while building a profile wrote
``ControlMember.member_label``; every *display* surface resolves through
``fan_display_name`` (i.e. ``fan_aliases``) and so showed a fallback instead.

Most of this is Qt-free — the seeding rule is a pure function by design.
"""

from __future__ import annotations

import json

from control_ofc.api.models import FanReading, HwmonHeader
from control_ofc.services.app_state import MAX_FAN_ALIAS_LEN, AppState
from control_ofc.services.fan_alias_seed import (
    collect_member_labels,
    seed_fan_aliases_from_profiles,
    strip_member_label_decorations,
)
from control_ofc.services.profile_service import ControlMember, LogicalControl, Profile

CH0 = "openfan:ch00"
CH1 = "openfan:ch01"


def _profile(pid: str, members: list[tuple[str, str]], name: str = "P") -> Profile:
    return Profile(
        id=pid,
        name=name,
        controls=[
            LogicalControl(
                id=f"{pid}-ctl",
                name="Case",
                members=[
                    ControlMember(source="openfan", member_id=mid, member_label=label)
                    for mid, label in members
                ],
            )
        ],
    )


def _state(fan_ids: list[str]) -> AppState:
    state = AppState()
    state.fans = [FanReading(id=f, source="openfan", rpm=1000) for f in fan_ids]
    return state


# ── The regression this exists for ───────────────────────────────────


def test_user_labels_from_profiles_become_display_names():
    """The reported bug, end to end.

    Before the fix the rail rendered "OpenFan CH0" while the user's own
    "FrontBottom-In" sat unused in the profile store.
    """
    state = _state([CH0, CH1])
    profiles = [_profile("balanced", [(CH0, "FrontBottom-In"), (CH1, "FrontMid-In")])]

    assert state.fan_display_name(CH0) == "OpenFan CH0"  # the symptom

    seeded = seed_fan_aliases_from_profiles(
        profiles, state.fan_aliases, {f.id for f in state.fans}, state.fan_fallback_name
    )
    state.fan_aliases.update(seeded)

    assert state.fan_display_name(CH0) == "FrontBottom-In"
    assert state.fan_display_name(CH1) == "FrontMid-In"


# ── Selection rules ──────────────────────────────────────────────────


class TestSeedRules:
    def test_existing_alias_is_never_clobbered(self):
        state = _state([CH0])
        state.set_fan_alias(CH0, "My Own Name")
        seeded = seed_fan_aliases_from_profiles(
            [_profile("p", [(CH0, "Profile Name")])],
            state.fan_aliases,
            {CH0},
            state.fan_fallback_name,
        )
        assert seeded == {}

    def test_label_equal_to_the_fallback_is_skipped(self):
        """Adopting it would be visually a no-op but behaviourally not: an alias
        pins a fan visible under hide-unused-headers."""
        state = _state([CH0])
        seeded = seed_fan_aliases_from_profiles(
            [_profile("p", [(CH0, "OpenFan CH0")])], {}, {CH0}, state.fan_fallback_name
        )
        assert seeded == {}

    def test_unknown_fan_ids_are_skipped(self):
        """Members left behind by retired id schemes must not revive dead names."""
        state = _state([CH0])
        seeded = seed_fan_aliases_from_profiles(
            [_profile("p", [(CH0, "Front"), ("openfan:3", "Legacy")])],
            {},
            {CH0},
            state.fan_fallback_name,
        )
        assert seeded == {CH0: "Front"}

    def test_empty_labels_are_skipped(self):
        state = _state([CH0, CH1])
        seeded = seed_fan_aliases_from_profiles(
            [_profile("p", [(CH0, ""), (CH1, "   ")])], {}, {CH0, CH1}, state.fan_fallback_name
        )
        assert seeded == {}

    def test_label_is_length_capped(self):
        state = _state([CH0])
        seeded = seed_fan_aliases_from_profiles(
            [_profile("p", [(CH0, "L" * 500)])], {}, {CH0}, state.fan_fallback_name
        )
        assert len(seeded[CH0]) == MAX_FAN_ALIAS_LEN

    def test_inputs_are_not_mutated(self):
        state = _state([CH0])
        profiles = [_profile("p", [(CH0, "Front")])]
        aliases: dict[str, str] = {}
        seed_fan_aliases_from_profiles(profiles, aliases, {CH0}, state.fan_fallback_name)
        assert aliases == {}
        assert profiles[0].controls[0].members[0].member_label == "Front"


class TestPrecedence:
    def test_active_profile_wins(self):
        state = _state([CH0])
        profiles = [
            _profile("other", [(CH0, "From Other")]),
            _profile("active", [(CH0, "From Active")]),
        ]
        seeded = seed_fan_aliases_from_profiles(
            profiles, {}, {CH0}, state.fan_fallback_name, active_profile_id="active"
        )
        assert seeded == {CH0: "From Active"}

    def test_another_profile_supplies_a_name_the_active_one_lacks(self):
        state = _state([CH0, CH1])
        profiles = [
            _profile("active", [(CH0, "From Active"), (CH1, "")]),
            _profile("other", [(CH1, "From Other")]),
        ]
        seeded = seed_fan_aliases_from_profiles(
            profiles, {}, {CH0, CH1}, state.fan_fallback_name, active_profile_id="active"
        )
        assert seeded == {CH0: "From Active", CH1: "From Other"}

    def test_collect_is_order_stable_without_an_active_profile(self):
        labels = collect_member_labels(
            [_profile("a", [(CH0, "First")]), _profile("b", [(CH0, "Second")])]
        )
        assert labels == {CH0: "First"}


# ── Decoration stripping (defect 1's cleanup half) ───────────────────


class TestStripDecorations:
    def test_known_badges_are_removed(self):
        assert strip_member_label_decorations("OpenFan CH2 (no fan detected)") == "OpenFan CH2"
        assert strip_member_label_decorations("9070XT Fan (read-only)") == "9070XT Fan"
        assert strip_member_label_decorations("Pump (AIO pump)") == "Pump"
        assert strip_member_label_decorations("Rad (AIO radiator)") == "Rad"
        assert strip_member_label_decorations("H1 (PWM only — no RPM)") == "H1"

    def test_stacked_badges_are_removed(self):
        assert strip_member_label_decorations("Pump (read-only) (AIO pump)") == "Pump"

    def test_a_users_own_parenthetical_is_preserved(self):
        assert strip_member_label_decorations("Front (top)") == "Front (top)"

    def test_decorated_label_is_sanitised_before_seeding(self):
        state = _state([CH0])
        seeded = seed_fan_aliases_from_profiles(
            [_profile("p", [(CH0, "Front Intake (no fan detected)")])],
            {},
            {CH0},
            state.fan_fallback_name,
        )
        assert seeded == {CH0: "Front Intake"}


# ── member_display_name (defect 2) ───────────────────────────────────


class TestMemberDisplayName:
    def test_live_alias_beats_the_cached_label(self):
        state = _state([CH0])
        state.set_fan_alias(CH0, "Renamed")
        assert state.member_display_name(CH0, "Stale Cached") == "Renamed"

    def test_cached_label_used_when_no_alias(self):
        state = _state([CH0])
        assert state.member_display_name(CH0, "Cached") == "Cached"

    def test_falls_back_when_both_absent(self):
        """Must not collapse to fan_display_name-or-label: the fallback is never
        empty, so that ordering would make the cached label unreachable."""
        state = _state([CH0])
        assert state.member_display_name(CH0, "") == "OpenFan CH0"


# ── The safety path stays untouched ──────────────────────────────────


def test_seeding_does_not_disturb_role_inference():
    """member_label feeds infer_member_role, which sets the DEC-095/162 CPU/pump
    PWM floor. Seeding must leave it byte-identical."""
    from control_ofc.services.profile_service import infer_member_role

    state = _state([CH0])
    pump_id = "hwmon:x:pwm1"
    state.fans = [
        FanReading(id=CH0, source="openfan", rpm=900),
        FanReading(id=pump_id, source="hwmon", rpm=1400),
    ]
    profile = Profile(
        id="p",
        controls=[
            LogicalControl(
                id="c",
                name="Pump",
                members=[
                    ControlMember(source="hwmon", member_id=pump_id, member_label="CPU OPT / Pump")
                ],
            )
        ],
    )
    before = infer_member_role(profile.controls[0].members[0])
    seed_fan_aliases_from_profiles([profile], {}, {CH0, pump_id}, state.fan_fallback_name)
    assert profile.controls[0].members[0].member_label == "CPU OPT / Pump"
    assert infer_member_role(profile.controls[0].members[0]) == before


# ── Startup wiring: once, and not in demo ────────────────────────────


def _aliases_on_disk(tmp_path) -> dict:
    path = tmp_path / "control-ofc" / "app_settings.json"
    return json.loads(path.read_text()).get("fan_aliases", {}) if path.exists() else {}


class TestSeedingWiring:
    @staticmethod
    def _window(tmp_path, monkeypatch, qtbot, demo=False):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from control_ofc.services.app_settings_service import AppSettingsService
        from control_ofc.services.profile_service import ProfileService
        from control_ofc.ui.main_window import MainWindow

        svc = AppSettingsService()
        svc.load()  # as main.py does — __init__ alone leaves defaults in place
        profiles = ProfileService()
        profiles._profiles = {"p": _profile("p", [(CH0, "FrontBottom-In")])}
        profiles._active_id = "p"
        window = MainWindow(settings_service=svc, profile_service=profiles, demo_mode=demo)
        qtbot.addWidget(window)
        return window, svc

    def test_seeds_on_the_first_fan_poll(self, qtbot, tmp_path, monkeypatch):
        window, svc = self._window(tmp_path, monkeypatch, qtbot)
        window._state.set_fans([FanReading(id=CH0, source="openfan", rpm=1000)])

        assert window._state.fan_display_name(CH0) == "FrontBottom-In"
        assert _aliases_on_disk(tmp_path) == {CH0: "FrontBottom-In"}
        assert svc.settings.fan_aliases_seeded is True

    def test_an_empty_fan_list_does_not_consume_the_one_shot(self, qtbot, tmp_path, monkeypatch):
        """A poll before hardware is known must not mark the seed done."""
        window, svc = self._window(tmp_path, monkeypatch, qtbot)
        window._state.set_fans([])
        assert svc.settings.fan_aliases_seeded is False

        window._state.set_fans([FanReading(id=CH0, source="openfan", rpm=1000)])
        assert window._state.fan_display_name(CH0) == "FrontBottom-In"

    def test_a_cleared_alias_is_not_resurrected(self, qtbot, tmp_path, monkeypatch):
        """The reason the seeded flag exists: without it the profile label would
        come back on every launch and could never be removed."""
        window, _svc = self._window(tmp_path, monkeypatch, qtbot)
        window._state.set_fans([FanReading(id=CH0, source="openfan", rpm=1000)])
        window._state.apply_fan_rename(CH0, "")
        assert _aliases_on_disk(tmp_path) == {}

        window2, _ = self._window(tmp_path, monkeypatch, qtbot)
        window2._state.set_fans([FanReading(id=CH0, source="openfan", rpm=1000)])
        assert window2._state.fan_display_name(CH0) == "OpenFan CH0"

    def test_demo_mode_never_seeds(self, qtbot, tmp_path, monkeypatch):
        window, svc = self._window(tmp_path, monkeypatch, qtbot, demo=True)
        window._state.set_fans([FanReading(id=CH0, source="openfan", rpm=1000)])
        assert svc.settings.fan_aliases_seeded is False
        assert _aliases_on_disk(tmp_path) == {}


# ── Defect 1: no badge reaches member_label ──────────────────────────


def test_member_editor_persists_the_undecorated_label(qtbot):
    from control_ofc.ui.widgets.member_editor import MemberEditorDialog

    dlg = MemberEditorDialog(
        current_members=[],
        available_outputs=[
            {
                "id": CH0,
                "source": "openfan",
                "label": "Front Intake (no fan detected)",
                "clean_label": "Front Intake",
            }
        ],
    )
    qtbot.addWidget(dlg)
    dlg._available_list.selectAll()
    dlg._on_add()

    members = dlg.get_members()
    assert [m.member_label for m in members] == ["Front Intake"]


def test_member_editor_falls_back_to_label_without_a_clean_one(qtbot):
    """Older callers that supply no clean_label keep working."""
    from control_ofc.ui.widgets.member_editor import MemberEditorDialog

    dlg = MemberEditorDialog(
        current_members=[],
        available_outputs=[{"id": CH0, "source": "openfan", "label": "Front Intake"}],
    )
    qtbot.addWidget(dlg)
    dlg._available_list.selectAll()
    dlg._on_add()
    assert dlg.get_members()[0].member_label == "Front Intake"


# ── Defect 2: control surfaces follow a rename ───────────────────────


def test_control_card_member_row_follows_a_rename(qtbot):
    from control_ofc.ui.widgets.control_card import ControlCard

    state = _state([CH0])
    state.set_fan_alias(CH0, "Renamed Fan")
    control = LogicalControl(
        id="c",
        name="Case",
        members=[ControlMember(source="openfan", member_id=CH0, member_label="Stale Cached")],
    )
    card = ControlCard(control, [], display_name=state.member_display_name)
    qtbot.addWidget(card)

    assert "Renamed Fan" in card._members_text(control)
    assert "Stale Cached" not in card._members_text(control)


def test_hwmon_header_picker_entry_uses_the_resolver(qtbot):
    """A renamed PWM-only header shows its alias in the picker, not the raw
    sysfs label."""
    state = AppState()
    header = HwmonHeader(id="hwmon:x:pwm2", chip_name="x", pwm_index=2, label="pwm2")
    state.hwmon_headers = [header]
    state.set_fan_alias("hwmon:x:pwm2", "Rear Exhaust")
    assert state.fan_display_name("hwmon:x:pwm2") == "Rear Exhaust"
