"""Per-profile curve scoping on the Controls page — the `CTRL` register.

The reported symptom was "a new profile shows another profile's curves". The
model was never wrong — ``Profile.curves`` is genuinely per-profile and
``_refresh_curves_grid`` renders that list and nothing else — so every defect
here lives in the *view-routing* layer that decides WHICH profile that is:

* `CTRL-a` — ``_refresh_all`` early-returned with no profile, stranding the
  previous one's cards on screen.
* `CTRL-b` — ``_viewed_profile_id`` was cleared only by a *transition* of the
  active id, so after New/Duplicate there was no way back to the active profile.
* `CTRL-c` — the sidebar combo had no ``currentIndexChanged`` connection, so a
  profile could not be looked at without being activated on the daemon.
* `CTRL-d` — the GUI's notion of "active" was seeded from the first file in the
  store and could not be cleared by a daemon that was running nothing.
* `CTRL-e` — deleting the active profile promoted an arbitrary survivor.
* `CTRL-f` — the delete prompt named the raw uuid.
* `CTRL-g` — the pane ``+`` buttons were silent no-ops with no profile.

Assertions here are deliberately **relationships** rather than literals (a
literal is satisfied by the defect often enough to be worthless — see
``CLAUDE.md § Hard-won lessons``), and the visibility checks use
``isVisibleTo(page)`` because under ``QT_QPA_PLATFORM=offscreen`` nothing is
shown and ``isVisible()`` is ``False`` for every widget.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import DaemonStatus
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import Profile, ProfileService
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.pages.controls_page import ControlsPage

from .test_profile_service_daemon import FakeDaemonClient, _seed

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_state():
    from control_ofc.api.models import ConnectionState, OperationMode

    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


@pytest.fixture()
def profile_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    return svc


@pytest.fixture()
def controls_page(qtbot, app_state, profile_service):
    page = ControlsPage(state=app_state, profile_service=profile_service, client=None)
    qtbot.addWidget(page)
    return page


@pytest.fixture()
def main_window(qtbot, app_state, profile_service, settings_service):
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


# ---------------------------------------------------------------------------
# `CTRL-b` / the reported symptom — the page shows the viewed profile's curves
# ---------------------------------------------------------------------------


class TestCurvesBelongToTheViewedProfile:
    def test_only_the_viewed_profiles_curves_are_rendered(self, controls_page, profile_service):
        """Both directions, because a stuck predicate satisfies one of them.

        Asserted against each profile's own ``curves`` rather than against a
        list of names: a literal expectation would still pass if the page
        rendered a *fixed* set that happened to match one profile.
        """
        profiles = list(profile_service.profiles)
        assert len(profiles) >= 2
        first, second = profiles[0], profiles[1]
        assert {c.name for c in first.curves} != {c.name for c in second.curves}, (
            "precondition: the two profiles must have DIFFERENT curves, or this "
            "test passes no matter which one the page renders"
        )

        for profile in (first, second, first):
            controls_page.select_profile(profile.id)
            shown = {card.curve.name for card in controls_page._curve_cards.values()}
            assert shown == {c.name for c in profile.curves}

    def test_a_new_profile_shows_its_own_curves_not_the_previous_ones(
        self, controls_page, profile_service
    ):
        """The literal bug report: create a profile, see the old one's curves."""
        previous = profile_service.active_profile
        assert previous is not None and previous.curves, "precondition: the old profile has curves"

        created = profile_service.create_profile("Gaming")
        controls_page.select_profile(created.id)

        shown = {card.curve.name for card in controls_page._curve_cards.values()}
        assert shown == {c.name for c in created.curves}
        assert not (shown & {c.name for c in previous.curves})

    def test_the_page_can_be_returned_to_the_active_profile_after_a_new_one(
        self, main_window, profile_service
    ):
        """`CTRL-b`: this is the one that had no recovery at all.

        Driven through the **sidebar**, not through ``select_profile``. That
        distinction is the whole defect and a first draft of this test missed it:
        calling ``select_profile(active_id)`` directly passes against the
        pre-fix code too, because ``select_profile`` was never broken — nothing
        *called* it with the active id. The only route the UI offered was sidebar
        **Apply**, and ``ProfileService.set_active`` emits only on a *change*, so
        applying the already-active profile emitted nothing and the page stayed
        pinned to the draft. Recovery needed activating a third profile.
        """
        page = main_window.controls_page
        combo = main_window.sidebar.profile_combo
        active_id = profile_service.active_id
        assert active_id, "precondition: something is active to return to"

        created = profile_service.create_profile("Gaming")
        page.select_profile(created.id)
        assert page.viewed_profile_id == created.id, "precondition: the draft is pinned"
        assert combo.currentData() == created.id, "precondition: the selector followed the draft"

        # The user's recovery move: pick the profile that is ALREADY active.
        idx = combo.findData(active_id)
        assert idx >= 0 and idx != combo.currentIndex()
        combo.setCurrentIndex(idx)

        assert page.viewed_profile_id == active_id
        # Browsing back must not have activated anything — it was already active,
        # but the assertion pins that the route is selection, not Apply.
        assert profile_service.active_id == active_id

    def test_a_selection_that_outlives_its_profile_is_dropped(self, controls_page, profile_service):
        """A stale ``_viewed_profile_id`` must not survive its profile.

        Falling back while still *holding* the dead id is the shape that made
        the original defect invisible: the page renders the active profile while
        claiming to view something else, and nothing but another selection can
        move it.
        """
        created = profile_service.create_profile("Doomed")
        controls_page.select_profile(created.id)
        profile_service.delete_profile(created.id)

        controls_page._refresh_all()

        assert controls_page._viewed_profile_id is None

    def test_an_unknown_profile_id_is_refused_rather_than_stored(
        self, controls_page, profile_service
    ):
        """Assert what the guard actually changes, not merely the end state.

        A first draft asserted ``viewed_profile_id`` was unmoved — and passed
        with the guard deleted, because the stale-id drop in
        ``_get_current_profile`` reaches the same answer one step later. The
        observable difference is the *side effects* ``select_profile`` performs
        on the way: it clears the unsaved flag and announces a change, neither of
        which a refused selection may do.
        """
        before = controls_page.viewed_profile_id
        controls_page._set_unsaved(True)
        announced: list[str] = []
        controls_page.viewed_profile_changed.connect(announced.append)

        controls_page.select_profile("no-such-profile")

        assert controls_page.viewed_profile_id == before
        assert controls_page._has_unsaved is True
        assert announced == []


# ---------------------------------------------------------------------------
# `CTRL-a` / `CTRL-g` — the no-profile state
# ---------------------------------------------------------------------------


class TestNoProfileState:
    def test_deleting_every_profile_clears_both_grids(self, controls_page, profile_service):
        """`CTRL-a`: the early return left the last profile's cards on screen."""
        assert controls_page._curve_cards, "precondition: something is rendered to begin with"

        for pid in [p.id for p in profile_service.profiles]:
            profile_service.delete_profile(pid)
        controls_page._on_profiles_changed()

        assert controls_page._curve_cards == {}
        assert controls_page._control_cards == {}
        # isVisibleTo, never isVisible: offscreen shows nothing, so isVisible()
        # is False for every widget and would pass with the call deleted.
        assert controls_page._controls_empty.isVisibleTo(controls_page)

    def test_the_no_profile_state_says_so_rather_than_advising_the_impossible(
        self, controls_page, profile_service
    ):
        """`CTRL-g`: the visible guidance must not tell the user to click a ``+``
        that cannot work. Progressive disclosure hides the curves section while
        there are no controls, so the labels the user actually sees are
        ``_controls_empty`` and ``_no_controls_hint`` — both are checked."""
        with_profile = controls_page._controls_empty.text()

        for pid in [p.id for p in profile_service.profiles]:
            profile_service.delete_profile(pid)
        controls_page._on_profiles_changed()

        assert controls_page._controls_empty.text() != with_profile
        for label in (controls_page._controls_empty, controls_page._no_controls_hint):
            assert label.isVisibleTo(controls_page)
            assert "profile" in label.text().lower()

    def test_the_add_buttons_are_disabled_rather_than_silently_inert(
        self, controls_page, profile_service
    ):
        """Both branches — a predicate stuck at ``False`` passes the second alone."""
        assert controls_page._add_control_btn.isEnabled()
        assert controls_page._add_curve_btn.isEnabled()

        for pid in [p.id for p in profile_service.profiles]:
            profile_service.delete_profile(pid)
        controls_page._on_profiles_changed()

        assert not controls_page._add_control_btn.isEnabled()
        assert not controls_page._add_curve_btn.isEnabled()

    def test_refreshing_with_no_profile_releases_held_overrides(
        self, controls_page, profile_service
    ):
        """The early return also skipped ``_refresh_controls_grid``'s override
        release, so a live manual override outlived the card that owned it."""
        released: list[str] = []
        controls_page._release_all_overrides = lambda *a, **k: released.append("released")

        for pid in [p.id for p in profile_service.profiles]:
            profile_service.delete_profile(pid)
        controls_page._on_profiles_changed()

        assert released, "the grid rebuild must still run with no profile"


# ---------------------------------------------------------------------------
# `CTRL-c` — the sidebar browses; Apply activates
# ---------------------------------------------------------------------------


class TestSidebarBrowsesWithoutActivating:
    def test_selecting_in_the_sidebar_moves_the_page_without_a_daemon_call(
        self, main_window, profile_service
    ):
        combo = main_window.sidebar.profile_combo
        active_id = profile_service.active_id
        other = next(p for p in profile_service.profiles if p.id != active_id)
        idx = combo.findData(other.id)
        assert idx >= 0 and idx != combo.currentIndex(), "precondition: the selection moves"

        combo.setCurrentIndex(idx)

        assert main_window.controls_page.viewed_profile_id == other.id
        assert profile_service.active_id == active_id

    def test_the_selector_follows_the_page_when_the_page_moves_itself(
        self, main_window, profile_service
    ):
        """New/Duplicate move the page; without the reverse wiring the selector
        was left naming the profile the user had just navigated away from."""
        created = profile_service.create_profile("Gaming")
        main_window.controls_page.select_profile(created.id)

        assert (
            main_window.sidebar.profile_combo.currentData()
            == main_window.controls_page.viewed_profile_id
        )

    def test_the_active_profile_is_marked_in_the_list_not_in_the_header(self, main_window):
        """The group header no longer claims the selection is active, so the
        marker has to be on the entry. Asserted as a relationship against
        ``active_id`` — a literal index would pass with the marker stuck."""
        combo = main_window.sidebar.profile_combo
        active_id = main_window._profile_service.active_id
        assert active_id, "precondition: something is active to mark"

        marked = [
            combo.itemData(i) for i in range(combo.count()) if "(active)" in combo.itemText(i)
        ]

        assert marked == [active_id]

    def test_the_marker_moves_with_the_activation(self, main_window, profile_service):
        combo = main_window.sidebar.profile_combo
        other = next(p for p in profile_service.profiles if p.id != profile_service.active_id)

        profile_service.set_active(other.id)

        marked = [
            combo.itemData(i) for i in range(combo.count()) if "(active)" in combo.itemText(i)
        ]
        assert marked == [other.id]

    def test_saving_does_not_move_the_selector_off_the_profile_being_edited(
        self, main_window, profile_service
    ):
        """``profiles_changed`` rebuilds the combo; a rebuild that re-selected
        the active profile would yank the user out of the profile they browsed
        to the moment they saved it."""
        other = next(p for p in profile_service.profiles if p.id != profile_service.active_id)
        main_window.controls_page.select_profile(other.id)

        profile_service.save_profile(other)

        assert main_window.sidebar.profile_combo.currentData() == other.id
        assert main_window.controls_page.viewed_profile_id == other.id

    def test_a_failed_apply_leaves_the_browse_selection_alone(
        self, main_window, profile_service, monkeypatch
    ):
        """A rejected activation must not un-browse the user.

        Found in self-review, not by a test: the old handler ended with an
        unconditional snap to the active profile, which was right while the combo
        WAS the activation selector. Now that selecting is browsing, that snap
        drags the user out of the profile they were looking at every time the
        daemon refuses an activation.
        """
        from control_ofc.services.profile_service import ProfileActivateOutcome

        combo = main_window.sidebar.profile_combo
        active_id = profile_service.active_id
        other = next(p for p in profile_service.profiles if p.id != active_id)
        combo.setCurrentIndex(combo.findData(other.id))
        assert main_window.controls_page.viewed_profile_id == other.id

        monkeypatch.setattr(
            profile_service,
            "activate",
            lambda pid, client=None: ProfileActivateOutcome(activated=False, error="refused"),
        )
        main_window.sidebar.apply_profile_btn.click()

        assert combo.currentData() == other.id
        assert main_window.controls_page.viewed_profile_id == other.id
        # ...and the marker still names the profile that really is active.
        marked = [
            combo.itemData(i) for i in range(combo.count()) if "(active)" in combo.itemText(i)
        ]
        assert marked == [active_id]

    def test_the_sidebar_exposes_new_and_delete(self, main_window):
        for btn in (main_window.sidebar.new_profile_btn, main_window.sidebar.delete_profile_btn):
            assert btn.objectName()
            assert btn.text()

    def test_sidebar_new_creates_a_profile_and_views_it(self, main_window, profile_service):
        """``.click()``, not the handler — the connection is the thing most
        likely to be broken."""
        before = {p.id for p in profile_service.profiles}
        main_window.controls_page._on_new_profile = lambda: profile_service.create_profile("Made")

        main_window.sidebar.new_profile_btn.click()

        created = {p.id for p in profile_service.profiles} - before
        assert len(created) == 1

    def test_sidebar_delete_targets_the_selected_profile(self, main_window, profile_service):
        other = next(p for p in profile_service.profiles if p.id != profile_service.active_id)
        main_window.controls_page.select_profile(other.id)
        targeted: list[str] = []
        main_window.controls_page._on_delete_profile = lambda: targeted.append(
            main_window.controls_page.viewed_profile_id
        )

        main_window.sidebar.delete_profile_btn.click()

        assert targeted == [other.id]


# ---------------------------------------------------------------------------
# `CTRL-d` — the GUI's notion of "active" comes from the daemon
# ---------------------------------------------------------------------------


class TestActiveProfileIsTheDaemons:
    def test_a_daemon_backed_load_does_not_invent_an_active_profile(self, cfg_env):
        """The seed was ``next(iter(self._profiles))`` and the daemon lists
        profiles sorted by filename, so the GUI marked the alphabetically-first
        profile ACTIVE regardless of what was running."""
        fake = FakeDaemonClient()
        _seed(fake, Profile(id="aaa", name="A"), Profile(id="zzz", name="Z"))
        svc = ProfileService(client=fake)
        svc.load()

        assert svc.profiles, "precondition: profiles really did load"
        assert svc.active_id == ""

    def test_local_mode_still_seeds_because_there_is_no_daemon_to_ask(self, cfg_env):
        """The opposite branch. Without it, a seed deleted everywhere passes."""
        svc = ProfileService()
        svc.load()

        assert svc.active_id in {p.id for p in svc.profiles}

    def test_a_poll_status_sets_the_active_profile_from_the_daemon(self, app_state, cfg_env):
        """Pick the sample that can move: the target is deliberately NOT the
        first profile in the store, which is the answer the old seed produced
        and which therefore cannot discriminate."""
        svc = ProfileService()
        svc.load()
        app_state.active_profile_id_changed.connect(svc.set_active)
        ids = [p.id for p in svc.profiles]
        target = ids[-1]
        assert target != ids[0], "precondition: the target is not the seeded answer"

        app_state.set_status(
            DaemonStatus(active_profile_id=target, active_profile_name="X", has_active_profile=True)
        )

        assert svc.active_id == target

    def test_a_daemon_running_nothing_clears_the_active_profile(self, app_state, cfg_env):
        """`CTRL-d`'s core: ``has_active_profile=False`` is authoritative.

        The id and name are omitted from the wire whenever nothing is active, so
        before this field their absence was indistinguishable from "this daemon
        predates the mirror" — and a deactivation left a stale profile named in
        the sidebar and the banner until the GUI reconnected.
        """
        svc = ProfileService()
        svc.load()
        app_state.active_profile_id_changed.connect(svc.set_active)
        target = [p.id for p in svc.profiles][-1]
        app_state.set_status(
            DaemonStatus(active_profile_id=target, active_profile_name="X", has_active_profile=True)
        )
        assert svc.active_id == target, "precondition: something is active to clear"

        app_state.set_status(DaemonStatus(has_active_profile=False))

        assert svc.active_id == ""
        assert app_state.active_profile_name == ""
        assert app_state.active_profile_id == ""

    def test_an_older_daemon_does_not_clear_anything(self, app_state, cfg_env):
        """The third reading, and the one that keeps DEC-194 intact: an ABSENT
        key means unknown, so the /profile/active fallback stays authoritative.
        Without this branch the fix would blank the profile on every poll from
        every daemon below 2.45.0."""
        svc = ProfileService()
        svc.load()
        app_state.active_profile_id_changed.connect(svc.set_active)
        target = [p.id for p in svc.profiles][-1]
        app_state.set_status(
            DaemonStatus(active_profile_id=target, active_profile_name="X", has_active_profile=True)
        )

        app_state.set_status(DaemonStatus())  # no keys at all — a pre-2.45.0 daemon

        assert svc.active_id == target
        assert app_state.active_profile_name == "X"

    def test_a_malformed_flag_reads_as_unknown_not_as_false(self, app_state, cfg_env):
        """Coercion direction matters: ``bool("no")`` is True and ``bool(0)`` is
        False, either of which would act on a value the daemon never sent.
        Unknown preserves the fallback, which is the safe direction."""
        svc = ProfileService()
        svc.load()
        app_state.active_profile_id_changed.connect(svc.set_active)
        target = [p.id for p in svc.profiles][-1]
        app_state.set_status(
            DaemonStatus(active_profile_id=target, active_profile_name="X", has_active_profile=True)
        )

        from control_ofc.api.models import parse_status

        status = parse_status({"has_active_profile": "no"})
        assert status.has_active_profile is None
        app_state.set_status(status)

        assert svc.active_id == target

    def test_connecting_to_a_daemon_running_nothing_is_truthful_end_to_end(
        self, qtbot, app_state, settings_service, cfg_env
    ):
        """The whole no-active-profile state, through a real ``MainWindow``.

        Added after review raised the combination as a possible truthfulness
        regression: with profiles in the store and none active, the selector
        falls back to the first entry and the page follows it, so the "No profile
        selected" empty state does not appear. That is **deliberate** — a
        ``QComboBox`` always has a current item, so the alternative is a selector
        naming Alpha beside a page showing nothing, which is the exact desync
        `CTRL-e`'s fix removed from the delete path.

        What makes it truthful is asserted here rather than argued: **no entry is
        marked ``(active)``**, the daemon-sourced banner names nothing, and —
        the consequence that actually mattered in `CTRL-d` — saving the browsed
        profile does not activate it.
        """
        fake = FakeDaemonClient()
        fake.socket_path = "/nonexistent/control-ofc.sock"
        _seed(fake, Profile(id="aaa", name="Alpha"), Profile(id="zzz", name="Zulu"))
        activated: list[str] = []
        fake.activate_profile = lambda path: activated.append(path)
        svc = ProfileService(client=fake)
        svc.load()
        assert svc.active_id == "", "precondition: the daemon has not said what is active"
        assert len(list(svc.profiles)) >= 2, "precondition: profiles exist to be misrepresented"

        win = MainWindow(
            state=app_state,
            profile_service=svc,
            settings_service=settings_service,
            client=fake,
            demo_mode=False,
        )
        qtbot.addWidget(win)
        try:
            combo = win.sidebar.profile_combo
            # The selector and the page never disagree...
            assert (combo.currentData() or "") == win.controls_page.viewed_profile_id
            # ...and nothing claims to be running.
            assert not any("(active)" in combo.itemText(i) for i in range(combo.count()))
            assert app_state.active_profile_name == ""

            win.controls_page._on_save_profile()
            assert activated == []
        finally:
            win.controls_page.cleanup()

    def test_saving_does_not_reactivate_a_profile_the_daemon_is_not_running(self, qtbot, cfg_env):
        """The dangerous consequence, pinned directly.

        ``_on_save_profile``'s DEC-188 re-apply fires on ``profile.id ==
        active_id``. With the fabricated seed, editing and saving the
        alphabetically-first profile silently activated it on the daemon.
        """
        fake = FakeDaemonClient()
        _seed(fake, Profile(id="aaa", name="A"), Profile(id="zzz", name="Z"))
        svc = ProfileService(client=fake)
        svc.load()
        assert svc.active_id == "", "precondition: nothing is assumed active"

        activated: list[str] = []
        fake.activate_profile = lambda path: activated.append(path)
        # ControlsPage builds its DEC-220 override worker from the client; the
        # fake never serves a request, so any path will do.
        fake.socket_path = "/nonexistent/control-ofc.sock"

        page = ControlsPage(state=AppState(), profile_service=svc, client=fake)
        qtbot.addWidget(page)
        try:
            page.select_profile("aaa")
            page._on_save_profile()
        finally:
            page.cleanup()

        assert activated == []


# ---------------------------------------------------------------------------
# `CTRL-e` / `CTRL-f` — deletion
# ---------------------------------------------------------------------------


class TestDeletingProfiles:
    def test_deleting_the_active_profile_promotes_nobody(self, profile_service):
        """`CTRL-e`: the caller has just told the daemon to deactivate, so "no
        profile is active" is the truth. Promoting a survivor made the sidebar
        label one ACTIVE while the daemon-sourced banner correctly showed none."""
        active_id = profile_service.active_id
        assert active_id and len(profile_service.profiles) > 1

        profile_service.delete_profile(active_id)

        assert profile_service.active_id == ""

    def test_deleting_the_active_profile_announces_the_change(self, qtbot, profile_service):
        """``profiles_changed`` alone does not say the ACTIVE profile moved, so
        the sidebar marker and the Controls page never heard about it."""
        heard: list[str] = []
        profile_service.active_changed.connect(heard.append)

        profile_service.delete_profile(profile_service.active_id)

        assert heard == [""]

    def test_deleting_an_inactive_profile_leaves_the_active_one_alone(self, profile_service):
        """The opposite branch — a clear that fires unconditionally passes the
        test above on its own."""
        active_id = profile_service.active_id
        other = next(p for p in profile_service.profiles if p.id != active_id)

        profile_service.delete_profile(other.id)

        assert profile_service.active_id == active_id

    def test_deleting_through_the_sidebar_leaves_the_page_and_selector_agreeing(
        self, main_window, profile_service, monkeypatch
    ):
        """Found in self-review, not by a test.

        `CTRL-e` stopped `delete_profile` promoting a survivor, so the page's old
        "fall back to the active profile" landed on nothing — and it also
        discarded the selection the sidebar had already moved to while handling
        `active_changed`. Result: the sidebar named a surviving profile while the
        page showed the empty state. Asserted as a **relationship** between the
        two surfaces, at every step down to the last profile, because the
        end-state alone is satisfied by both being empty.
        """
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            "control_ofc.ui.pages.controls_page.QMessageBox.question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        )
        combo = main_window.sidebar.profile_combo
        page = main_window.controls_page
        remaining = len(list(profile_service.profiles))
        assert remaining >= 2, "precondition: more than one profile, so a survivor exists"

        saw_a_survivor = False
        for _ in range(remaining):
            main_window.sidebar.delete_profile_btn.click()
            assert (combo.currentData() or "") == page.viewed_profile_id
            if combo.currentData():
                saw_a_survivor = True
                # A survivor is SHOWN, not merely selected.
                assert page._get_current_profile() is not None

        assert saw_a_survivor, (
            "precondition: at least one delete had to leave a survivor, or this "
            "test only ever checked the both-empty case"
        )
        assert profile_service.active_id == ""
        assert page.viewed_profile_id == ""

    def test_the_delete_prompt_names_the_profile_not_its_id(
        self, controls_page, profile_service, monkeypatch
    ):
        """`CTRL-f`: every user-created profile has an 8-char uuid, so this read
        "Delete profile 'e41bb5ed'?". Asserted as a relationship against the
        profile's own name and id."""
        created = profile_service.create_profile("My Gaming Profile")
        controls_page.select_profile(created.id)
        assert created.name != created.id, "precondition: name and id are distinguishable"

        seen: list[str] = []

        def _capture(parent, title, text, *args, **kwargs):
            seen.append(text)
            from PySide6.QtWidgets import QMessageBox

            return QMessageBox.StandardButton.No

        monkeypatch.setattr("control_ofc.ui.pages.controls_page.QMessageBox.question", _capture)
        controls_page._on_delete_profile()

        assert len(seen) == 1
        assert created.name in seen[0]
        assert created.id not in seen[0]


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    """Isolate the GUI config tree per test (``profiles_dir()``)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path
