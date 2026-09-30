"""DEC-461 (`BRD-v`, `G194`): a member whose hwmon header is absent is kept and badged.

DEC-102's runtime sweep deleted every hwmon member whose header was not writable
in the first header set of a session — including a header that was only ABSENT
that session — and saved the result to the daemon. On the reference host the
two pump headers sit on the secondary it87952, the chip DEC-332 documents
latching absent at boot, so one such boot deleted both pump members from every
profile, and their 30 % floor with them.

Now the sweep drops only a member whose header is present and read-only; a
missing header's member stays, and the Controls card and member editor mark it
"header missing" — motherboard members only, never in demo, never against an
empty header list (the user's Q2-A / Q3-A).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from control_ofc.api.models import HwmonHeader, OperationMode
from control_ofc.services.controls_view import (
    MISSING_HEADER_BADGE,
    MISSING_HEADER_TOOLTIP,
    detected_hwmon_header_ids,
    missing_header_member_ids,
)
from control_ofc.services.profile_service import (
    ControlMember,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
    control_minimum_pct,
)
from control_ofc.ui.components.labels import ElidedLabel, safe_tooltip
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.widgets.control_card import ControlCard

# The reference host's layout: the primary it8696 carries CPU_FAN, the secondary
# it87952 carries the two pumps.
CPU_FAN = "hwmon:it8696:it87.2624:pwm1:CPU_FAN"
LOCKED = "hwmon:it8696:it87.2624:pwm3:pwm3"
PUMP5 = "hwmon:it87952:it87.2656:pwm5:SYS_FAN5_PUMP"
PUMP6 = "hwmon:it87952:it87.2656:pwm6:SYS_FAN6_PUMP"
# DEC-442: an older daemon on an it87 v2.0 driver publishes CPU_FAN suffixed.
CPU_FAN_SUFFIXED = "hwmon:it8696_a008090a:it87.2624:pwm1:CPU_FAN"


def _header(id_: str, *, writable: bool = True) -> HwmonHeader:
    chip, device, pwm = id_.split(":")[1:4]
    return HwmonHeader(
        id=id_,
        label=id_.rsplit(":", 1)[1],
        chip_name=chip,
        device_id=device,
        pwm_index=int(pwm.removeprefix("pwm")),
        is_writable=writable,
    )


def _hwmon(id_: str, label: str) -> ControlMember:
    return ControlMember(source="hwmon", member_id=id_, member_label=label)


def _pump_profile() -> Profile:
    return Profile(
        id="p1",
        name="Quiet",
        controls=[
            LogicalControl(
                id="pump",
                name="Pump",
                curve_id="k1",
                members=[_hwmon(PUMP5, "SYS_FAN5_PUMP"), _hwmon(PUMP6, "SYS_FAN6_PUMP")],
            ),
            LogicalControl(
                id="cpu",
                name="CPU",
                curve_id="k1",
                members=[_hwmon(CPU_FAN, "CPU_FAN"), _hwmon(LOCKED, "Locked")],
            ),
        ],
        curves=[CurveConfig(id="k1", name="K", type=CurveType.FLAT, flat_output_pct=50.0)],
    )


# The first header set of a boot on which the secondary chip latched absent: the
# primary's headers only, one of them read-only.
LATCHED_BOOT = [_header(CPU_FAN), _header(LOCKED, writable=False)]


# ── The sweep, driven through MainWindow's call site ─────────────────────


class _Profiles:
    def __init__(self, *profiles: Profile) -> None:
        self.profiles = list(profiles)
        self.saved: list[str] = []

    def save_profile(self, profile: Profile) -> None:
        self.saved.append(profile.id)


def _sweep(headers, profiles: _Profiles, *, demo: bool = False) -> SimpleNamespace:
    window = SimpleNamespace(
        _demo_mode=demo, _headers_sanitization_done=False, _profile_service=profiles
    )
    MainWindow._sanitize_profiles_against_headers(window, headers)
    return window


class TestTheSweepKeepsAMissingHeadersMember:
    def test_pump_members_on_a_latched_secondary_chip_survive_with_their_floor(self):
        profile = _pump_profile()
        pump = profile.controls[0]
        floor_before = control_minimum_pct(pump.members)
        assert floor_before > 0, "precondition: the pump members carry the pump floor"

        profiles = _Profiles(profile)
        window = _sweep(LATCHED_BOOT, profiles)

        assert window._headers_sanitization_done is True, "precondition: the sweep ran"
        assert [m.member_id for m in pump.members] == [PUMP5, PUMP6]
        assert [m.member_label for m in pump.members] == ["SYS_FAN5_PUMP", "SYS_FAN6_PUMP"]
        assert control_minimum_pct(pump.members) == floor_before

    def test_a_present_read_only_header_is_still_dropped_and_saved(self):
        """DEC-102's own case is unchanged — the opposite branch."""
        profile = _pump_profile()
        profiles = _Profiles(profile)
        _sweep(LATCHED_BOOT, profiles)

        assert [m.member_id for m in profile.controls[1].members] == [CPU_FAN]
        assert profiles.saved == ["p1"]

    def test_a_profile_whose_only_gap_is_a_missing_header_is_not_republished(self):
        profile = _pump_profile()
        profile.controls[1].members = [_hwmon(CPU_FAN, "CPU_FAN")]
        profiles = _Profiles(profile)
        _sweep(LATCHED_BOOT, profiles)

        assert profiles.saved == []
        assert len(profile.controls[0].members) == 2


class TestSanitizeRule:
    def test_missing_header_is_kept(self):
        profile = _pump_profile()
        assert profile.sanitize_hwmon_members({CPU_FAN}, {CPU_FAN, LOCKED}) == 1
        assert [m.member_id for m in profile.controls[0].members] == [PUMP5, PUMP6]

    def test_read_only_spelled_suffixed_by_an_older_daemon_is_still_dropped(self):
        """Canonical on both sides (DEC-442): the suffixed spelling of a present
        read-only header must read as present, or its member would be kept."""
        profile = Profile(
            id="p",
            controls=[LogicalControl(id="c", members=[_hwmon(CPU_FAN, "CPU_FAN")])],
        )
        assert profile.sanitize_hwmon_members(set(), {CPU_FAN_SUFFIXED}) == 1


# ── The view-model: which members are "header missing" ───────────────────


class TestMissingHeaderView:
    def test_nothing_is_known_without_headers_or_in_demo(self):
        assert detected_hwmon_header_ids([], demo=False) is None
        assert detected_hwmon_header_ids(None, demo=False) is None
        assert detected_hwmon_header_ids(LATCHED_BOOT, demo=True) is None
        members = _pump_profile().controls[0].members
        assert missing_header_member_ids(members, None) == frozenset()

    def test_hwmon_members_only_and_compared_canonical(self):
        detected = detected_hwmon_header_ids([_header(CPU_FAN_SUFFIXED)], demo=False)
        members = [
            _hwmon(CPU_FAN, "CPU_FAN"),
            _hwmon(PUMP5, "SYS_FAN5_PUMP"),
            ControlMember(source="openfan", member_id="openfan:ch00"),
            ControlMember(source="amd_gpu", member_id="amd_gpu:0000:03:00.0"),
        ]
        assert missing_header_member_ids(members, detected) == {PUMP5}


# ── The card ──────────────────────────────────────────────────────────


def _card(qtbot, detected) -> ControlCard:
    profile = _pump_profile()
    control = profile.controls[0]
    control.members.append(ControlMember(source="openfan", member_id="openfan:ch00"))
    card = ControlCard(control, profile.curves, detected_hwmon_ids=lambda: detected())
    qtbot.addWidget(card)
    return card


def _pill(card: ControlCard, member_id: str):
    return card._member_row_missing[member_id]


class TestControlCardBadge:
    def test_the_badge_shows_only_on_the_missing_members_and_follows_a_refresh(self, qtbot):
        detected = {"ids": detected_hwmon_header_ids(LATCHED_BOOT, demo=False)}
        card = _card(qtbot, lambda: detected["ids"])

        for pump in (PUMP5, PUMP6):
            assert _pill(card, pump).isVisibleTo(card)
            assert _pill(card, pump).text() == MISSING_HEADER_BADGE.upper()
            assert _pill(card, pump).toolTip() == MISSING_HEADER_TOOLTIP
        assert not _pill(card, "openfan:ch00").isVisibleTo(card)

        # The header comes back (the next boot, or a rescan): the badge goes.
        detected["ids"] = detected_hwmon_header_ids(
            [*LATCHED_BOOT, _header(PUMP5), _header(PUMP6)], demo=False
        )
        card.refresh_member_presence()
        assert not _pill(card, PUMP5).isVisibleTo(card)
        assert not _pill(card, PUMP6).isVisibleTo(card)

    def test_a_card_without_a_resolver_badges_nothing(self, qtbot):
        profile = _pump_profile()
        card = ControlCard(profile.controls[0], profile.curves)
        qtbot.addWidget(card)
        assert not any(p.isVisibleTo(card) for p in card._member_row_missing.values())


# ── The Controls page wires the live headers and the mode ────────────────


def _page(qtbot, app_state, profile_service) -> tuple[ControlsPage, ControlCard]:
    profile = _pump_profile()
    profile_service._profiles[profile.id] = profile
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page.select_profile(profile.id)
    return page, page._control_cards["pump"]


class TestControlsPageWiring:
    def test_the_badge_follows_the_headers_and_the_mode(self, qtbot, app_state, profile_service):
        app_state.set_hwmon_headers([])
        page, card = _page(qtbot, app_state, profile_service)
        assert page._control_cards["pump"] is card  # keep the page bound (DEC-356)
        assert not _pill(card, PUMP5).isVisibleTo(card), "no headers: nothing is known"

        app_state.set_hwmon_headers(LATCHED_BOOT)
        assert _pill(card, PUMP5).isVisibleTo(card)

        app_state.set_mode(OperationMode.DEMO)
        assert not _pill(card, PUMP5).isVisibleTo(card), "demo judges nothing"

        app_state.set_mode(OperationMode.AUTOMATIC)
        app_state.set_hwmon_headers([*LATCHED_BOOT, _header(PUMP5), _header(PUMP6)])
        assert not _pill(card, PUMP5).isVisibleTo(card)

    def test_the_member_editor_marks_the_missing_member_and_saves_it_undecorated(
        self, qtbot, app_state, profile_service, monkeypatch
    ):
        from control_ofc.ui.widgets import member_editor

        app_state.set_hwmon_headers(LATCHED_BOOT)
        page, _card = _page(qtbot, app_state, profile_service)
        seen: dict = {}

        def _exec(dialog):
            lst = dialog._selected_list
            seen["rows"] = [(lst.item(i).text(), lst.item(i).toolTip()) for i in range(lst.count())]
            seen["members"] = dialog.get_members()
            return 0

        monkeypatch.setattr(member_editor.MemberEditorDialog, "exec", _exec)
        page._on_edit_members("pump")

        texts = [text for text, _tip in seen["rows"]]
        assert all(f"({MISSING_HEADER_BADGE})" in t for t in texts), texts
        assert all(tip == MISSING_HEADER_TOOLTIP for _t, tip in seen["rows"])
        assert [m.member_label for m in seen["members"]] == ["SYS_FAN5_PUMP", "SYS_FAN6_PUMP"]

        # The opposite branch: a present header's member carries no badge.
        page._on_edit_members("cpu")
        assert all(MISSING_HEADER_BADGE not in text for text, _tip in seen["rows"])


# ── Review P3 (the user chose to fix it here): a long name next to the pill ──


@pytest.fixture()
def restore_app_theme(qtbot):
    """Save/restore everything ``apply_theme`` mutates (mirrors
    test_theme_typography_r30.py) — realised row geometry needs the theme's
    padding and font, or the squeeze being tested never happens."""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    from control_ofc.ui import theme as theme_mod

    app = QApplication.instance()
    saved = (QPalette(app.palette()), app.styleSheet(), app.font(), theme_mod._active_theme)
    try:
        yield app
    finally:
        app.setPalette(saved[0])
        app.setStyleSheet(saved[1])
        app.setFont(saved[2])
        theme_mod._active_theme = saved[3]


LONG_ALIAS = "Front Intake Top Left <b>&</b>"


class TestElidedLabelTooltipWhenElided:
    def test_the_full_name_is_the_tooltip_exactly_while_it_is_elided(self, qtbot):
        label = ElidedLabel(LONG_ALIAS, tooltip_when_elided=True)
        qtbot.addWidget(label)
        # Shown: Qt defers a hidden widget's resize event until it is shown, and
        # the label only ever needs this inside a shown card.
        label.show()
        label.resize(40, 20)
        assert label.elided_text() != LONG_ALIAS, "precondition: it is elided"
        assert label.toolTip() == safe_tooltip(LONG_ALIAS)
        assert "<b>" not in label.toolTip(), "the untrusted name stays escaped"

        label.resize(label.sizeHint().width() + 20, 20)
        assert label.elided_text() == LONG_ALIAS, "precondition: it fits"
        assert label.toolTip() == ""

        label.resize(40, 20)
        label.setText("Another long enough name")
        assert label.toolTip() == safe_tooltip("Another long enough name")

    def test_off_by_default(self, qtbot):
        label = ElidedLabel(LONG_ALIAS)
        qtbot.addWidget(label)
        label.show()
        label.resize(40, 20)
        assert label.elided_text() != LONG_ALIAS
        assert label.toolTip() == ""


class TestLongNameBesideThePill:
    def test_a_compact_card_elides_the_name_and_keeps_the_rpm_inside_the_row(
        self, qtbot, restore_app_theme
    ):
        from control_ofc.ui.theme import active_theme, apply_theme
        from control_ofc.ui.widgets.card_metrics import CARD_SIZE_COMPACT, MIN_USER_CARD_WIDTH_PX

        apply_theme(active_theme())
        profile = _pump_profile()
        detected = detected_hwmon_header_ids(LATCHED_BOOT, demo=False)
        card = ControlCard(
            profile.controls[0],
            profile.curves,
            card_size=CARD_SIZE_COMPACT,
            user_size=(MIN_USER_CARD_WIDTH_PX, 10),
            display_name=lambda _mid, _label: LONG_ALIAS,
            detected_hwmon_ids=lambda: detected,
        )
        qtbot.addWidget(card)
        card.apply_card_size(
            active_theme().base_font_size_pt, CARD_SIZE_COMPACT, (MIN_USER_CARD_WIDTH_PX, 10)
        )
        card.show()
        card.set_member_rpms({PUMP5: 1234})
        qtbot.waitExposed(card)

        name = card._member_row_name[PUMP5]
        rpm = card._member_row_rpm[PUMP5]
        row = name.parentWidget()
        assert _pill(card, PUMP5).isVisibleTo(card), "precondition: the pill is shown"
        assert name.width() < name.sizeHint().width(), "precondition: the name is squeezed"

        assert name.text() == LONG_ALIAS, "the stored text stays verbatim (DEC-231)"
        assert name.elided_text().endswith("…")
        assert name.toolTip() == safe_tooltip(LONG_ALIAS)
        assert rpm.geometry().right() < row.width()
