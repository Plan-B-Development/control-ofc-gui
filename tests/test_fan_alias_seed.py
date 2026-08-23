"""DEC-228 — adopting profile member labels as fan aliases.

The bug this pins: the GUI kept two per-fan name stores that never reconciled.
Users who named their fans while building a profile wrote
``ControlMember.member_label``; every *display* surface resolves through
``fan_display_name`` (i.e. ``fan_aliases``) and so showed a fallback instead.

Most of this is Qt-free — the seeding rule is a pure function by design.
"""

from __future__ import annotations

import json

import pytest

from control_ofc.api.models import FanReading, HwmonHeader
from control_ofc.services.app_state import MAX_FAN_ALIAS_LEN, AppState
from control_ofc.services.fan_alias_seed import (
    collect_member_labels,
    seed_fan_aliases_from_profiles,
    strip_member_label_decorations,
)
from control_ofc.services.profile_service import ControlMember, LogicalControl, Profile
from control_ofc.ui.fan_display import filter_displayable_fans

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


def _profile_hwmon(pid: str, member_id: str, label: str) -> Profile:
    """A profile whose single member is an hwmon header (not OpenFan)."""
    return Profile(
        id=pid,
        name="P",
        controls=[
            LogicalControl(
                id=f"{pid}-ctl",
                name="Case",
                members=[ControlMember(source="hwmon", member_id=member_id, member_label=label)],
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


class TestRawIdLabelsAreNotAdopted:
    """Profiles authored on <= v2.27.1 cached the raw id as `member_label`,
    because that is what `fan_display_name` returned back then. Adopting those
    would invert this release's purpose and — since an alias pins a fan visible —
    permanently pin every idle channel."""

    def test_label_equal_to_its_own_id_is_skipped(self):
        state = _state([CH0])
        seeded = seed_fan_aliases_from_profiles(
            [_profile("old", [(CH0, CH0)])], {}, {CH0}, state.fan_fallback_name
        )
        assert seeded == {}
        state.fan_aliases.update(seeded)
        assert state.fan_display_name(CH0) == "OpenFan CH0"

    def test_idle_fan_is_not_pinned_visible_by_a_raw_id_label(self):
        state = _state([CH0])
        seeded = seed_fan_aliases_from_profiles(
            [_profile("old", [(CH0, CH0)])], {}, {CH0}, state.fan_fallback_name
        )
        idle = [FanReading(id=CH0, source="openfan", rpm=0)]
        assert filter_displayable_fans(idle, seeded, hide_unused=True) == []

    def test_hwmon_shaped_id_label_is_skipped(self):
        hid = "hwmon:nct6799:isa-0290:pwm3:"
        state = AppState()
        state.fans = [FanReading(id=hid, source="hwmon", rpm=800)]
        seeded = seed_fan_aliases_from_profiles(
            [_profile("old", [(hid, hid)])], {}, {hid}, state.fan_fallback_name
        )
        assert seeded == {}

    def test_a_label_carrying_another_fans_id_is_skipped(self):
        state = _state([CH0])
        seeded = seed_fan_aliases_from_profiles(
            [_profile("old", [(CH0, "openfan:ch07")])], {}, {CH0}, state.fan_fallback_name
        )
        assert seeded == {}

    def test_a_normal_name_containing_a_colon_still_works(self):
        state = _state([CH0])
        seeded = seed_fan_aliases_from_profiles(
            [_profile("p", [(CH0, "Front: top")])], {}, {CH0}, state.fan_fallback_name
        )
        assert seeded == {CH0: "Front: top"}

    @staticmethod
    def _x870e_state(hid: str) -> AppState:
        from control_ofc.api.models import BoardInfo

        state = AppState()
        state.board_info = BoardInfo(
            vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER"
        )
        state.set_hwmon_headers(
            [HwmonHeader(id=hid, label="pwm1", chip_name="it8696", pwm_index=1)]
        )
        state.fans = [FanReading(id=hid, source="hwmon", rpm=900)]
        return state

    def test_a_bare_pwm_node_label_is_skipped(self):
        """DEC-229 would otherwise defeat itself through this seed.

        Profiles authored before DEC-229 cached the then-displayed `"pwm1"` as
        `member_label`. The "equal to the fallback ⇒ skip" guard used to catch
        that, because the fallback *was* `"pwm1"` — but the moment DEC-229 made
        the fallback resolve to `CPU_FAN`, the two stopped matching and
        `_is_raw_id` did not recognise the bare form either. The seed would then
        promote the stale placeholder to a **user alias**, which outranks the
        resolver, and since the seed is one-shot the board table could never be
        consulted again: `pwm1` baked in permanently, on exactly the machines
        this release exists to fix. Verified to reproduce before the fix.
        """
        hid = "hwmon:it8696:it87.2624:pwm1:pwm1"
        state = self._x870e_state(hid)
        assert state.fan_fallback_name(hid) == "CPU_FAN"  # precondition

        seeded = seed_fan_aliases_from_profiles(
            [_profile_hwmon("old", hid, "pwm1")], {}, {hid}, state.fan_fallback_name
        )
        assert seeded == {}
        state.fan_aliases.update(seeded)
        assert state.fan_display_name(hid) == "CPU_FAN"

    def test_a_real_board_label_is_still_adopted(self):
        """The guard must stay narrow — it compares the id's own pwmN segment,
        not a general `pwm\\d+` pattern, so a genuine name is untouched."""
        hid = "hwmon:it8696:it87.2624:pwm1:pwm1"
        state = self._x870e_state(hid)
        seeded = seed_fan_aliases_from_profiles(
            [_profile_hwmon("p", hid, "My Cooler")], {}, {hid}, state.fan_fallback_name
        )
        assert seeded == {hid: "My Cooler"}


class TestStripIsBounded:
    """The badge regex is superlinear and profiles are untrusted (file import, or
    the 0666 daemon store). An unclamped label froze the GUI on every launch,
    unrecoverably — the one-shot flag is only written after the seed completes."""

    def test_a_pathological_label_returns_promptly(self):
        import time

        hostile = ("  " * 50 + "(AIO pump)") * 400  # ~60 kB
        start = time.monotonic()
        strip_member_label_decorations(hostile)
        assert time.monotonic() - start < 1.0

    def test_input_is_clamped_before_stripping(self):
        assert len(strip_member_label_decorations("x" * 10_000)) <= 512

    @pytest.mark.parametrize("bad", [123, ["a"], {"a": 1}, True, None])
    def test_non_string_labels_do_not_raise(self, bad):
        assert strip_member_label_decorations(bad) == ""

    def test_a_non_string_label_in_a_profile_does_not_break_the_poll(self):
        state = _state([CH0])
        profile = _profile("p", [(CH0, "ok")])
        profile.controls[0].members[0].member_label = 123  # hand-edited profile
        assert seed_fan_aliases_from_profiles([profile], {}, {CH0}, state.fan_fallback_name) == {}


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


class TestPersistedLabelKeepsItsRole:
    """The picker's ``clean_label`` must drop hardware-state badges but KEEP the
    "(AIO …)" tag.

    ``member_label`` is not only a name: ``infer_member_role`` and the daemon's
    ``member_is_pump_or_cpu`` both match "cpu"/"pump"/"aio" against it to apply
    the DEC-095/162 30% pump floor. An AIO pump the user called "Loop" reaches
    that floor *only* via the badge, so stripping it would silently drop the
    member to the 20% chassis floor — on both sides, since the daemon mirrors
    this classification rather than detecting pumps independently.
    """

    @staticmethod
    def _role(label: str) -> str:
        from control_ofc.services.profile_service import infer_member_role

        return infer_member_role(
            ControlMember(source="hwmon", member_id="hwmon:nct6799:x:pwm2:pwm2", member_label=label)
        )

    def test_aio_tag_preserves_the_pump_role(self):
        assert self._role("Loop (AIO pump)") == "cpu_or_pump"
        # The regression: the same member without its tag falls to chassis.
        assert self._role("Loop") == "chassis"

    @staticmethod
    def _picker_entries(qtbot, monkeypatch, app_state, profile_service, header, alias, fans):
        """Drive the real picker and capture the entries it hands the dialog."""
        from control_ofc.ui.pages.controls_page import ControlsPage

        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        page._on_new_control(name="Role")
        control_id = page._get_current_profile().controls[-1].id

        app_state.hwmon_headers = [header]
        app_state.fans = fans
        app_state.set_fan_alias(header.id, alias)

        captured: dict = {}

        class _FakeMemberDialog:
            def __init__(self, members, available, assigned=None, role_name="", parent=None, **_kw):
                captured["available"] = available

            def exec(self):
                return False

        monkeypatch.setattr(
            "control_ofc.ui.widgets.member_editor.MemberEditorDialog", _FakeMemberDialog
        )
        page._on_edit_members(control_id)
        return {e["id"]: e for e in captured["available"]}

    def test_picker_clean_label_keeps_the_aio_tag(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        hid = "hwmon:nct6799:x:pwm2:pwm2"
        header = HwmonHeader(
            id=hid, label="", chip_name="nct6799", pwm_index=2, is_writable=True, is_aio=True
        )
        entries = self._picker_entries(
            qtbot,
            monkeypatch,
            app_state,
            profile_service,
            header,
            "Loop",
            [FanReading(id=hid, source="hwmon", rpm=1400)],
        )
        # Which AIO variant is chosen keys off the name ("Loop" reads as a
        # radiator), but either tag carries "aio" — the role is what must hold.
        assert "(AIO" in entries[hid]["clean_label"]
        assert self._role(entries[hid]["clean_label"]) == "cpu_or_pump"
        # And it would NOT survive without the tag — this is the regression.
        assert self._role("Loop") == "chassis"

    def test_aliased_cpu_header_keeps_its_pump_floor(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        """Renaming a CPU header must not cost it the 30% floor.

        `fan_display_name` prefers the user alias, so a header the user called
        "My Fan" would persist that as member_label — losing the "cpu" keyword
        the sysfs label ("CPU_OPT") carries, and baking a new control at the 20%
        chassis floor instead of 30%. The daemon mirrors this classification, so
        it would agree on the lower floor rather than catch it.
        """
        hid = "hwmon:nct6798:x:pwm2:CPU_OPT"
        header = HwmonHeader(
            id=hid, label="CPU_OPT", chip_name="nct6798", pwm_index=2, is_writable=True
        )
        entries = self._picker_entries(
            qtbot,
            monkeypatch,
            app_state,
            profile_service,
            header,
            "My Fan",
            [FanReading(id=hid, source="hwmon", rpm=1200)],
        )
        # The user sees their name...
        assert "My Fan" in entries[hid]["label"]
        # ...but what gets stored keeps the role keyword.
        assert entries[hid]["clean_label"] == "CPU_OPT"
        assert self._role(entries[hid]["clean_label"]) == "cpu_or_pump"
        assert self._role("My Fan") == "chassis"  # the regression, if stored

    def test_user_named_pump_on_an_unlabelled_header_keeps_its_floor(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        """The mirror case of the CPU_OPT test — and the one a naive fix breaks.

        The daemon's read_label never returns empty: with no pwmN_label/fanN_label
        in sysfs it synthesises "pwm7". So on a typical board the *alias* is the
        only way a user can assert pump-ness, and preferring the sysfs label would
        stamp "pwm7" and drop the floor to 20%.
        """
        hid = "hwmon:nct6798:x:pwm7:pwm7"
        header = HwmonHeader(
            id=hid, label="pwm7", chip_name="nct6798", pwm_index=7, is_writable=True
        )
        entries = self._picker_entries(
            qtbot,
            monkeypatch,
            app_state,
            profile_service,
            header,
            "Pump",
            [FanReading(id=hid, source="hwmon", rpm=1800)],
        )
        assert entries[hid]["clean_label"] == "Pump"
        assert self._role(entries[hid]["clean_label"]) == "cpu_or_pump"
        assert self._role("pwm7") == "chassis"  # what a sysfs-label-first rule stores

    def test_a_role_bearing_fallback_outranks_an_alias_that_lacks_one(self):
        """Documents the *raise* direction of "never lower the inferred role".

        The rule is deliberately asymmetric, so the mirror case needs pinning
        too: a user who aliases the X870E's unverified `SYS_FAN5_PUMP` header to
        "Radiator Fan" still gets the 30% floor, because the resolved label
        carries "pump" and the alias does not. That is the intended outcome —
        the floor is a safety minimum, so resolving a genuine ambiguity upward
        is the correct bias — but it is a real behaviour change on a mapping the
        table itself marks unverified, and it should fail loudly if reversed.
        """
        from control_ofc.api.models import BoardInfo
        from control_ofc.ui.pages.controls_page import _role_preserving_label

        hid = "hwmon:it87952:it87.2656:pwm1:pwm1"
        state = AppState()
        state.board_info = BoardInfo(
            vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER"
        )
        state.set_hwmon_headers(
            [HwmonHeader(id=hid, label="pwm1", chip_name="it87952", pwm_index=1)]
        )
        state.set_fan_alias(hid, "Radiator Fan")

        fallback = state.fan_fallback_name(hid)
        assert fallback.startswith("SYS_FAN5_PUMP")
        persisted = _role_preserving_label("Radiator Fan", fallback, "hwmon")
        assert persisted == fallback
        assert self._role(persisted) == "cpu_or_pump"

    def test_aio_radiator_members_keep_their_role(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        """DEC-229: the Configure-AIO radiator list persisted an unguarded name.

        This was the one member-persisting path in `controls_page.py` that did
        not route through `_role_preserving_label`, so it stored the alias-first
        display name verbatim. A header whose real label carries a role word
        (`PUMP_2` — common on boards exposing two pump headers) that the user had
        renamed to "Front Rad" therefore persisted a role-less `member_label` and
        dropped from the 30% pump floor to the 20% chassis one, on both sides.

        Drives the real `_on_configure_aio` with a faked dialog rather than
        asserting the helper: a first version of this test called
        `_role_preserving_label` directly, and reverting the production fix left
        it green — the whole defect was that the *call site* skipped the helper,
        so only the call site is worth pinning. `_role_preserving_label` had no
        call-site coverage at all before this.
        """
        from control_ofc.api.models import SensorReading
        from control_ofc.ui.pages.controls_page import ControlsPage

        pump_id = "hwmon:z53:d:pwm1:Pump"
        rad_id = "hwmon:nct6798:x:pwm3:PUMP_2"
        app_state.hwmon_headers = [
            HwmonHeader(
                id=pump_id,
                label="Pump",
                chip_name="z53",
                pwm_index=1,
                is_writable=True,
                is_aio=True,
            ),
            HwmonHeader(
                id=rad_id, label="PUMP_2", chip_name="nct6798", pwm_index=3, is_writable=True
            ),
        ]
        app_state.sensors = [
            SensorReading(id="hwmon:z53:d:Coolant", kind="coolant_temp", label="Coolant")
        ]
        app_state.fans = [FanReading(id=rad_id, source="hwmon", rpm=1100)]
        # The user renamed the second pump header to something role-less.
        app_state.set_fan_alias(rad_id, "Front Rad")

        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        profile = page._get_current_profile()

        captured: dict = {}

        class _FakeDialog:
            def __init__(self, **kwargs):
                captured["candidates"] = kwargs["fan_candidates"]

            def exec(self):
                return True

            def get_result(self):
                rad = next(c for c in captured["candidates"] if c["id"] == rad_id)
                return {
                    "pump_pct": 80,
                    "radiator_members": [rad],  # what the user picked
                    "radiator_sensor_id": "hwmon:z53:d:Coolant",
                }

        monkeypatch.setattr("control_ofc.ui.widgets.aio_config_dialog.AioConfigDialog", _FakeDialog)
        page._on_configure_aio()

        rad_control = next(c for c in profile.controls if "Radiator" in c.name)
        member = rad_control.members[0]
        assert member.member_id == rad_id
        assert member.member_label == "PUMP_2"  # role preserved, not "Front Rad"
        assert self._role(member.member_label) == "cpu_or_pump"
        assert self._role("Front Rad") == "chassis"  # the regression, if stored raw
        assert rad_control.minimum_pct == 30

    def test_aliased_cpu_header_keeps_its_floor_on_a_label_less_chip(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        """DEC-229: the CPU_OPT case, on the far more common board.

        `test_aliased_cpu_header_keeps_its_pump_floor` above only works because
        that chip publishes a real `CPU_OPT` label. On the reporter's IT8696E
        the chip publishes nothing, the daemon synthesises `pwm1`, and the role
        lives *only* in the board fallback table — so comparing against the raw
        `HwmonHeader.label` found no role to preserve and a renamed CPU header
        was still baked at the 20% chassis floor. Comparing against the resolved
        fallback (`CPU_FAN`) is what closes it.
        """
        from control_ofc.api.models import BoardInfo
        from control_ofc.knowledge.hwmon_label_resolver import (
            clear_libsensors_cache,
        )
        from control_ofc.services.profile_service import apply_role_floor

        clear_libsensors_cache()
        try:
            hid = "hwmon:it8696:it87.2624:pwm1:pwm1"
            app_state.board_info = BoardInfo(
                vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER"
            )
            header = HwmonHeader(
                id=hid, label="pwm1", chip_name="it8696", pwm_index=1, is_writable=True
            )
            entries = self._picker_entries(
                qtbot,
                monkeypatch,
                app_state,
                profile_service,
                header,
                "My Fan",
                [FanReading(id=hid, source="hwmon", rpm=1200)],
            )
            assert "My Fan" in entries[hid]["label"]  # the user still sees their name
            assert entries[hid]["clean_label"] == "CPU_FAN"  # …but the role is persisted
            assert self._role(entries[hid]["clean_label"]) == "cpu_or_pump"

            # The safety consequence, spelled out: 30% floor, not 20%.
            control = LogicalControl(
                id="c1",
                name="CPU",
                members=[
                    ControlMember(
                        source="hwmon", member_id=hid, member_label=entries[hid]["clean_label"]
                    )
                ],
            )
            apply_role_floor(control)
            assert control.minimum_pct == 30
        finally:
            clear_libsensors_cache()

    def test_picker_clean_label_still_drops_state_badges(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        hid = "hwmon:nct6799:x:pwm4:pwm4"
        header = HwmonHeader(
            id=hid, label="", chip_name="nct6799", pwm_index=4, is_writable=True, is_aio=False
        )
        entries = self._picker_entries(
            qtbot,
            monkeypatch,
            app_state,
            profile_service,
            header,
            "Front",
            [FanReading(id=hid, source="hwmon", rpm=0)],
        )
        # A transient hardware-state badge is shown to the user but never stored.
        assert entries[hid]["label"] != entries[hid]["clean_label"]
        assert "(" in entries[hid]["label"]
        assert entries[hid]["clean_label"] == "Front"


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


def test_controls_page_repaints_member_rows_on_rename(qtbot, app_state, profile_service):
    """The DEC-228 defect-2 wiring, end to end.

    ControlCard holds its member names in QLabels built at construction, so
    without the fan_alias_changed -> _refresh_all connection every rename (and
    every alias the seed adopts) would leave the Controls page showing the stale
    cached member_label. Card-level and AppState-level tests both pass without it.
    """
    from PySide6.QtWidgets import QLabel

    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page._on_new_control(name="Case")
    control = page._get_current_profile().controls[-1]
    control.members = [ControlMember(source="openfan", member_id=CH0, member_label="Stale Cached")]
    page._refresh_all()

    def _member_texts() -> list[str]:
        card = page._control_cards[control.id]
        # 277-e: exclude the hidden `*_A11yLabel` proxies (DEC-276) — real
        # text, but invisible, so a "must NOT appear" assertion here would
        # otherwise be checking text no user can see.
        return [
            w.text() for w in card.findChildren(QLabel) if not w.objectName().endswith("_A11yLabel")
        ]

    assert any("Stale Cached" in t for t in _member_texts())

    app_state.apply_fan_rename(CH0, "Renamed Fan")

    texts = _member_texts()
    assert any("Renamed Fan" in t for t in texts)
    assert not any("Stale Cached" in t for t in texts)


def test_rename_does_not_release_a_live_override(qtbot, app_state, profile_service, monkeypatch):
    """Renaming a fan must not cost the user a manual override they are holding.

    ControlsPage._refresh_controls_grid deliberately releases every live override
    before destroying cards (DEC-163), so routing a rename through _refresh_all
    would silently revert the control to Curve.
    """
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page._on_new_control(name="Case")
    control = page._get_current_profile().controls[-1]
    control.members = [ControlMember(source="openfan", member_id=CH0, member_label="Cached")]
    page._refresh_all()

    released: list[str] = []
    monkeypatch.setattr(page, "_release_all_overrides", lambda: released.append("released"))

    app_state.apply_fan_rename(CH0, "Renamed Fan")

    assert released == []
    card = page._control_cards[control.id]
    assert "Renamed Fan" in card._members_text(control)


def test_fan_role_dialog_resolves_member_names(qtbot):
    """FanRoleDialog's chips must use the resolver, not the cached label.

    Every pre-existing test constructs the dialog without a resolver, so the
    default lambda is all they exercise — a broken argument order or a dropped
    call would go unnoticed there.
    """
    from PySide6.QtWidgets import QLabel

    from control_ofc.ui.widgets.fan_role_dialog import FanRoleDialog

    state = _state([CH0])
    state.set_fan_alias(CH0, "Renamed Fan")
    control = LogicalControl(
        id="c",
        name="Case",
        members=[ControlMember(source="openfan", member_id=CH0, member_label="Stale Cached")],
    )
    dlg = FanRoleDialog(control, [], display_name=state.member_display_name)
    qtbot.addWidget(dlg)

    # 277-e: exclude the hidden `*_A11yLabel` proxies (DEC-276).
    texts = [
        w.text() for w in dlg.findChildren(QLabel) if not w.objectName().endswith("_A11yLabel")
    ]
    assert any("Renamed Fan" in t for t in texts)
    assert not any("Stale Cached" in t for t in texts)


def test_member_editor_selected_list_resolves_names(qtbot):
    """The already-selected side of the editor resolves too, not just available."""
    from control_ofc.ui.widgets.member_editor import MemberEditorDialog

    state = _state([CH0])
    state.set_fan_alias(CH0, "Renamed Fan")
    dlg = MemberEditorDialog(
        current_members=[
            ControlMember(source="openfan", member_id=CH0, member_label="Stale Cached")
        ],
        available_outputs=[],
        display_name=state.member_display_name,
    )
    qtbot.addWidget(dlg)
    assert "Renamed Fan" in dlg._selected_list.item(0).text()


def test_seeding_emits_fan_alias_changed(qtbot, tmp_path, monkeypatch):
    """Surfaces that repaint on the signal must learn about seeded names."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.profile_service import ProfileService
    from control_ofc.ui.main_window import MainWindow

    svc = AppSettingsService()
    svc.load()
    profiles = ProfileService()
    profiles._profiles = {"p": _profile("p", [(CH0, "FrontBottom-In")])}
    profiles._active_id = "p"
    window = MainWindow(settings_service=svc, profile_service=profiles)
    qtbot.addWidget(window)

    seen: list[tuple[str, str]] = []
    window._state.fan_alias_changed.connect(lambda fid, name: seen.append((fid, name)))
    window._state.set_fans([FanReading(id=CH0, source="openfan", rpm=1000)])

    assert (CH0, "FrontBottom-In") in seen


def test_seeding_an_empty_profile_store_still_consumes_the_one_shot(qtbot, tmp_path, monkeypatch):
    """Otherwise the migration would re-scan on every launch forever."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.profile_service import ProfileService
    from control_ofc.ui.main_window import MainWindow

    svc = AppSettingsService()
    svc.load()
    profiles = ProfileService()
    profiles._profiles = {}
    window = MainWindow(settings_service=svc, profile_service=profiles)
    qtbot.addWidget(window)
    window._state.set_fans([FanReading(id=CH0, source="openfan", rpm=1000)])

    assert svc.settings.fan_aliases_seeded is True


def test_seeded_flag_is_machine_specific():
    """If it ever became portable, importing a bundle would mark a fresh machine
    as already-seeded and silently skip the migration there."""
    from control_ofc.services.app_settings_service import MACHINE_SPECIFIC_KEYS, AppSettings

    assert "fan_aliases_seeded" in MACHINE_SPECIFIC_KEYS
    assert "fan_aliases_seeded" not in AppSettings().portable_dict()


def test_hwmon_header_picker_entry_uses_the_resolver(qtbot):
    """A renamed PWM-only header shows its alias in the picker, not the raw
    sysfs label."""
    state = AppState()
    header = HwmonHeader(id="hwmon:x:pwm2", chip_name="x", pwm_index=2, label="pwm2")
    state.hwmon_headers = [header]
    state.set_fan_alias("hwmon:x:pwm2", "Rear Exhaust")
    assert state.fan_display_name("hwmon:x:pwm2") == "Rear Exhaust"
