"""DEC-169: the Controls page reconciles daemon-held overrides from `/status`.

A foreign override (one this GUI session did not create — another client, or
this GUI restarted within the TTL) carries no fencing token on the poll surface,
so it can only be *displayed* read-only ("External" chip), never renewed or
released. GUI-owned overrides stay owned by the renew timer; the two authorities
must never collide. These tests assert outcomes (card chip + tracking state),
not clicks.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import (
    ConnectionState,
    DaemonStatus,
    OverrideGrant,
    OverrideStatusEntry,
    SkippedControl,
    parse_status,
)
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
)
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.widgets.control_card import ControlCard


def _grant(token=7, renew_secs=5):
    return OverrideGrant(
        control_id="lc1",
        override_token=token,
        pwm_percent=50,
        ttl_secs=15,
        renew_secs=renew_secs,
        expires_in_secs=15,
    )


def _status(*overrides: tuple[str, int]) -> DaemonStatus:
    """Build a DaemonStatus carrying the given (control_id, pwm) overrides."""
    return DaemonStatus(
        overrides=[
            OverrideStatusEntry(control_id=cid, pwm_percent=pwm, expires_in_secs=10)
            for cid, pwm in overrides
        ]
    )


def _skipped(*entries: tuple[str, str]) -> DaemonStatus:
    """Build a DaemonStatus carrying the given (control_id, reason) skips."""
    return DaemonStatus(
        skipped_controls=[
            SkippedControl(
                control_id=cid,
                control_name=cid.upper(),
                reason=reason,
                skipped_for_ms=9000,
            )
            for cid, reason in entries
        ]
    )


def _page(qtbot, app_state, profile_service, client):
    page = ControlsPage(state=app_state, profile_service=profile_service, client=client)
    qtbot.addWidget(page)
    curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
    ctrl = LogicalControl(
        id="lc1",
        name="LC",
        mode=ControlMode.CURVE,
        curve_id="c1",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    page._refresh_controls_grid(Profile(id="p", name="P", controls=[ctrl], curves=[curve]))
    return page


class TestForeignOverrideReconcile:
    def test_foreign_override_marks_card_external(self, qtbot, app_state, profile_service):
        client = MagicMock()
        page = _page(qtbot, app_state, profile_service, client)

        page._on_status_reconcile(_status(("lc1", 45)))

        assert page._external_overrides == {"lc1": 45}
        assert page._control_cards["lc1"]._status_chip.text() == "External 45%"
        # Display-only: never renew/release a token-less override.
        client.override_renew.assert_not_called()
        client.override_release.assert_not_called()

    def test_foreign_override_vanishes_reverts_card(self, qtbot, app_state, profile_service):
        client = MagicMock()
        page = _page(qtbot, app_state, profile_service, client)
        page._on_status_reconcile(_status(("lc1", 45)))

        # Daemon no longer reports it (expired / released by its owner).
        page._on_status_reconcile(_status())

        assert page._external_overrides == {}
        assert page._control_cards["lc1"]._external_pct is None

    def test_pwm_change_updates_chip(self, qtbot, app_state, profile_service):
        client = MagicMock()
        page = _page(qtbot, app_state, profile_service, client)
        page._on_status_reconcile(_status(("lc1", 45)))

        page._on_status_reconcile(_status(("lc1", 70)))

        assert page._external_overrides == {"lc1": 70}
        assert page._control_cards["lc1"]._status_chip.text() == "External 70%"

    def test_gui_owned_override_not_display_adopted(self, qtbot, app_state, profile_service):
        """A control the GUI owns (in `_overrides`) is reported by `/status` too,
        but reconcile must never display-adopt it — the renew timer owns it."""
        client = MagicMock()
        client.override_take.return_value = _grant(token=7)
        page = _page(qtbot, app_state, profile_service, client)
        page._control_cards["lc1"]._manual_btn.setChecked(True)  # GUI takes lc1
        assert page._overrides == {"lc1": 7}
        renewing = page._override_renew_timer.isActive()

        # The daemon reports lc1 (it reports ALL active overrides).
        page._on_status_reconcile(_status(("lc1", 50)))

        assert "lc1" not in page._external_overrides
        assert page._control_cards["lc1"]._status_chip.text() == "Manual"
        assert page._override_renew_timer.isActive() == renewing

    def test_takeover_moves_to_owned_and_clears_external(self, qtbot, app_state, profile_service):
        client = MagicMock()
        client.override_take.return_value = _grant(token=7)
        page = _page(qtbot, app_state, profile_service, client)
        page._on_status_reconcile(_status(("lc1", 45)))  # foreign first
        assert page._external_overrides == {"lc1": 45}

        # User takes over: clicking Manual mints a fresh, owned override.
        page._control_cards["lc1"]._manual_btn.setChecked(True)
        client.override_take.assert_called_once()
        assert page._overrides == {"lc1": 7}

        # Next poll still reports lc1, but it is now GUI-owned → dropped as foreign.
        page._on_status_reconcile(_status(("lc1", 45)))
        assert "lc1" not in page._external_overrides

    def test_disconnect_clears_external(self, qtbot, app_state, profile_service):
        client = MagicMock()
        page = _page(qtbot, app_state, profile_service, client)
        page._on_status_reconcile(_status(("lc1", 45)))
        assert page._external_overrides == {"lc1": 45}

        page._on_connection_changed(ConnectionState.DISCONNECTED)

        assert page._external_overrides == {}
        assert page._control_cards["lc1"]._external_pct is None

    def test_grid_rebuild_clears_external_tracking(self, qtbot, app_state, profile_service):
        client = MagicMock()
        page = _page(qtbot, app_state, profile_service, client)
        page._on_status_reconcile(_status(("lc1", 45)))
        assert page._external_overrides == {"lc1": 45}

        curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
        page._refresh_controls_grid(Profile(id="p", name="P", controls=[], curves=[curve]))

        assert page._external_overrides == {}

    def test_grid_rebuild_clears_skipped_tracking(self, qtbot, app_state, profile_service):
        """The mirror of the test above, for 273-i — and it was unpinned.

        `_refresh_controls_grid` destroys every card and builds fresh ones, so the
        per-card `_skipped_reason` goes with them. If the page's own
        `_skipped_controls` delta cache is NOT cleared alongside, the next poll
        reporting the same reason is a no-op — `set_skipped` is never called again
        and the rebuilt card never gets its "Not controlled" chip back. The card
        then sits quiet about a fan the daemon is not commanding, which is the
        silence 273-i exists to end.

        Deleting the `self._skipped_controls.clear()` line left the whole suite
        green before this test existed; the external-override half of the same
        pair has been pinned since DEC-169.
        """
        client = MagicMock()
        page = _page(qtbot, app_state, profile_service, client)
        page._on_status_reconcile(_skipped(("lc1", "mix_unresolvable")))
        assert page._skipped_controls == {"lc1": "mix_unresolvable"}
        assert page._control_cards["lc1"]._status_chip.text() == "Not controlled"

        curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
        control = LogicalControl(
            id="lc1",
            name="LC",
            mode=ControlMode.CURVE,
            curve_id="c1",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        page._refresh_controls_grid(Profile(id="p", name="P", controls=[control], curves=[curve]))
        assert page._skipped_controls == {}, "the delta cache must not outlive the cards"
        assert page._control_cards["lc1"]._status_chip.text() == "", (
            "the rebuilt card must start chip-less — without this the outcome "
            "assertion below could pass on a stale chip if cards were ever reused"
        )

        # The same reason again: only a cleared cache makes this a fresh delta.
        page._on_status_reconcile(_skipped(("lc1", "mix_unresolvable")))

        assert page._control_cards["lc1"]._status_chip.text() == "Not controlled", (
            "the rebuilt card must get its chip back on the next poll — otherwise "
            "the page goes quiet about a fan nothing is driving"
        )

    def test_own_in_flight_take_is_not_adopted_as_foreign(self, qtbot, app_state, profile_service):
        """A poll landing mid-take must not mistake this session's own override
        for someone else's.

        `_take_override` records `_manual_intent` synchronously, but the grant only
        reaches `_overrides` when the worker returns — a queued cross-thread hop in
        production. A poll inside that window used to classify the control as
        foreign and stamp `_external_pct` from it, so releasing Manual painted
        "External N%" for an override the user owns.

        The window cannot be reproduced through the worker here: `conftest` forces
        `_OVERRIDE_USE_THREAD=False`, so a take completes inline and the window
        closes by construction. That is exactly why this seeds the intent directly
        — the state, not the timing, is what the guard reads.
        """
        page = _page(qtbot, app_state, profile_service, MagicMock())
        page._manual_intent.add("lc1")
        assert "lc1" not in page._overrides, "precondition: the grant has not landed yet"

        page._on_status_reconcile(_status(("lc1", 50)))

        assert page._external_overrides == {}, (
            "this session's own in-flight override must not be adopted as foreign"
        )
        assert page._control_cards["lc1"]._external_pct is None

    def test_reconcile_noop_in_demo_mode(self, qtbot, app_state, profile_service):
        """Demo mode (no daemon client) owns its own simulated manual state — the
        poll reconcile must be inert."""
        page = _page(qtbot, app_state, profile_service, client=None)

        page._on_status_reconcile(_status(("lc1", 45)))

        assert page._external_overrides == {}
        assert page._control_cards["lc1"]._external_pct is None

    def test_unknown_control_id_ignored(self, qtbot, app_state, profile_service):
        """A foreign override for a control with no card (a different profile is
        loaded) is skipped without error and not tracked."""
        client = MagicMock()
        page = _page(qtbot, app_state, profile_service, client)

        page._on_status_reconcile(_status(("ghost", 30)))

        assert page._external_overrides == {}


class TestSkippedControlReconcile:
    """273-i: a control the daemon cannot resolve is commanding nothing, and the
    card must say so instead of showing a reassuring "Applied".

    Before this the fan simply stopped responding with no signal anywhere — no
    log at the daemon's shipped level, nothing on the API, nothing in the UI.
    """

    def test_skipped_control_marks_card_not_controlled(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service, MagicMock())

        page._on_status_reconcile(_skipped(("lc1", "mix_unresolvable")))

        assert page._skipped_controls == {"lc1": "mix_unresolvable"}
        card = page._control_cards["lc1"]
        assert card._status_chip.text() == "Not controlled"
        # The reason reaches the user somewhere — terse chip, detail on hover.
        assert "combined inputs" in card._status_chip.toolTip()

    def test_skipped_control_vanishing_clears_the_chip(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service, MagicMock())
        page._on_status_reconcile(_skipped(("lc1", "mix_unresolvable")))

        # The profile was fixed / the sensor came back.
        page._on_status_reconcile(_skipped())

        assert page._skipped_controls == {}
        assert page._control_cards["lc1"]._skipped_reason is None
        assert page._control_cards["lc1"]._status_chip.text() == ""

    def test_a_changed_reason_updates_the_tooltip(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service, MagicMock())
        page._on_status_reconcile(_skipped(("lc1", "mix_unresolvable")))

        page._on_status_reconcile(_skipped(("lc1", "curve_not_found")))

        assert page._skipped_controls == {"lc1": "curve_not_found"}
        assert "curve is missing" in page._control_cards["lc1"]._status_chip.toolTip()

    def test_live_output_does_not_repaint_applied_over_a_skip(
        self, qtbot, app_state, profile_service
    ):
        """The 1 Hz poll keeps calling set_output. If that repainted "Applied"
        the warning would flash and vanish every second — worse than useless,
        because it would read as a healthy control."""
        page = _page(qtbot, app_state, profile_service, MagicMock())
        page._on_status_reconcile(_skipped(("lc1", "sensor_unavailable")))
        card = page._control_cards["lc1"]

        card.set_output(42.0, "CPU", 55.0)

        assert card._status_chip.text() == "Not controlled"
        # The output label still tracks the last commanded value — that IS what
        # the fans are holding, so hiding it would be the opposite error.
        assert "42" in card._output_label.text()

    def test_disconnect_clears_skipped_chips(self, qtbot, app_state, profile_service):
        """Polling stops while offline, so nothing would ever clear the chip —
        and a disconnected GUI does not know whether it is still true."""
        page = _page(qtbot, app_state, profile_service, MagicMock())
        page._on_status_reconcile(_skipped(("lc1", "mix_unresolvable")))

        page._on_connection_changed(ConnectionState.DISCONNECTED)

        assert page._skipped_controls == {}
        assert page._control_cards["lc1"]._skipped_reason is None

    def test_an_unknown_reason_still_renders(self, qtbot, app_state, profile_service):
        """A newer daemon may add a reason this build has never heard of.
        Rendering nothing would restore exactly the silence 273-i removes."""
        page = _page(qtbot, app_state, profile_service, MagicMock())

        page._on_status_reconcile(_skipped(("lc1", "some_future_reason")))

        card = page._control_cards["lc1"]
        assert card._status_chip.text() == "Not controlled"
        assert card._status_chip.toolTip() != ""

    def test_a_malformed_control_id_does_not_crash_the_poll_path(
        self, qtbot, app_state, profile_service
    ):
        """A non-string `control_id` on the wire must not break the 1 Hz poll.

        The original defect: `control_id` keys a dict straight off the wire, so
        `"control_id": []` reached an unhashable-key `TypeError` inside the
        `status_updated` slot, on the main thread and outside the poll worker's
        own `except`.

        **This now goes through `parse_status`, which is the change 277-h made.**
        The first version of this test hand-built a `SkippedControl` with a list
        id and asserted a local `isinstance` guard on the page. That guard closed
        one of about six identical doors — the sibling `overrides` comprehension,
        `fan_identify`, `unavailable_sensors` and `services/session_stats.py` all
        hash wire ids the same way — and its one-sidedness made the asymmetry read
        as deliberate. The posture is now decided once at the parse boundary:
        `_filter_fields` coerces every identity field to `str`, so nothing
        downstream needs a guard and no future consumer can forget one.

        Driving the real entry point is also what makes this test honest — a
        hand-built dataclass could never reach the page in production, so the old
        test pinned a guard against an input the parser would have caught anyway
        once the parser learned to.
        """
        page = _page(qtbot, app_state, profile_service, MagicMock())
        raw = {
            "skipped_controls": [
                {
                    "control_id": "lc1",
                    "control_name": "LC1",
                    "reason": "mix_unresolvable",
                    "skipped_for_ms": 1000,
                },
                {
                    "control_id": [],
                    "control_name": "bad",
                    "reason": "curve_not_found",
                    "skipped_for_ms": 1000,
                },
            ]
        }
        status = parse_status(raw)

        # The coercion happened at the boundary, so the page never sees a list.
        bad = next(e for e in status.skipped_controls if e.control_name == "bad")
        assert isinstance(bad.control_id, str), (
            "a non-string wire id must be coerced at the parse boundary, so every "
            "downstream consumer can hash it without its own guard (277-h)"
        )
        assert bad.control_id not in page._control_cards, (
            "a coerced malformed id must not collide with a real control"
        )

        page._on_status_reconcile(status)

        assert page._control_cards["lc1"]._status_chip.text() == "Not controlled", (
            "a malformed sibling entry must not cost the well-formed one its chip"
        )

    def test_older_daemon_reports_nothing_skipped(self, qtbot, app_state, profile_service):
        """A daemon predating 2.21.0 omits the key entirely. The GUI must read
        that as "nothing skipped", not crash and not warn."""
        page = _page(qtbot, app_state, profile_service, MagicMock())

        page._on_status_reconcile(DaemonStatus())

        assert page._skipped_controls == {}
        assert page._control_cards["lc1"]._skipped_reason is None


class TestSkipAndManualInteraction:
    """The state machine where a user-held Manual meets a daemon-reported skip.

    Nothing covered this until two independent reviewers found the same defect
    in it. `set_skipped` and `set_external_override` deliberately SUPPRESS the
    chip while Manual is held rather than discarding the state — so leaving
    Manual has to put it back. Blanking instead left a live "Now: N%" with no
    chip at all, permanently: `set_output` early-returns while `_skipped_reason`
    is set, and the page reconciles only on a reason *delta*, so nothing ever
    repainted it. Exactly the silence 273-i exists to end.
    """

    def test_leaving_manual_restores_the_not_controlled_chip(
        self, qtbot, app_state, profile_service
    ):
        client = MagicMock()
        client.override_take.return_value = _grant(token=7)
        page = _page(qtbot, app_state, profile_service, client)
        page._on_status_reconcile(_skipped(("lc1", "mix_unresolvable")))
        card = page._control_cards["lc1"]
        assert card._status_chip.text() == "Not controlled"

        card._manual_btn.setChecked(True)  # user takes over
        assert card._status_chip.text() == "Manual", "Manual wins while it is held"
        card._manual_btn.setChecked(False)  # ...and changes their mind

        assert card._status_chip.text() == "Not controlled", (
            "leaving Manual must restore what the daemon still reports — the "
            "reconcile acts only on a delta, so nothing else will"
        )
        # And the 1 Hz poll must not undo it.
        card.set_output(42.0, "CPU", 55.0)
        assert card._status_chip.text() == "Not controlled"

    def test_leaving_manual_restores_an_external_chip(self, qtbot, app_state, profile_service):
        """The card's own state machine, exercised directly — NOT via the page.

        Through the page this combination cannot arise: reconcile excludes
        GUI-owned controls from `foreign`, and taking over an external override
        makes it yours, so the page clears `_external_pct` on takeover. That is
        correct and this test does not contradict it.

        What is pinned here is the card-level invariant that the same restore
        path covers both suppressed states, so the `_external_pct` arm cannot rot
        while only the `_skipped_reason` arm is exercised. It is a latent
        inconsistency rather than a live defect, and it predates 273-i.
        """
        client = MagicMock()
        page = _page(qtbot, app_state, profile_service, client)
        card = page._control_cards["lc1"]

        card.set_external_override(55)
        card._manual_btn.blockSignals(True)  # card only — no page takeover
        card._manual_btn.setChecked(True)
        card._manual_btn.blockSignals(False)
        card._apply_chip("Manual", "WarningChip")

        card.clear_manual()

        assert card._status_chip.text() == "External 55%"

    def test_leaving_manual_with_nothing_outstanding_clears_the_chip(
        self, qtbot, app_state, profile_service
    ):
        """The control case. Without it, always repainting something would pass
        the two tests above while a healthy card kept a stale badge."""
        client = MagicMock()
        client.override_take.return_value = _grant(token=7)
        page = _page(qtbot, app_state, profile_service, client)
        card = page._control_cards["lc1"]

        card._manual_btn.setChecked(True)
        card._manual_btn.setChecked(False)

        assert card._status_chip.text() == ""

    def test_the_tooltip_does_not_outlive_the_chip_that_set_it(
        self, qtbot, app_state, profile_service
    ):
        """Qt maps toolTip to QAccessible::Text::Description, so a stale one is
        announced, not merely hoverable. A card reading "Manual" once carried
        "the daemon is not commanding these fans" — the opposite of true while
        the user is commanding them."""
        client = MagicMock()
        client.override_take.return_value = _grant(token=7)
        page = _page(qtbot, app_state, profile_service, client)
        page._on_status_reconcile(_skipped(("lc1", "mix_unresolvable")))
        card = page._control_cards["lc1"]
        assert card._status_chip.toolTip() != ""

        card._manual_btn.setChecked(True)

        assert card._status_chip.text() == "Manual"
        assert card._status_chip.toolTip() == "", (
            "the skip tooltip must not survive onto the Manual chip"
        )

    def test_a_skip_does_not_paint_over_an_active_external_override(
        self, qtbot, app_state, profile_service
    ):
        """The daemon short-circuits an override before curve resolution, so the
        two cannot co-occur within one evaluation TICK — but that is not the same
        as within one response. `/status` composes `overrides[]` live and
        `skipped_controls[]` from the last COMPLETED tick, so the shipping daemon
        really does send both for up to a tick. `docs/08` states the precedence
        this asserts: the override wins, because it is actively pinning those
        fans and "Not controlled" over one is a lie in the unsafe direction.

        An earlier version of this docstring said the pair "cannot co-occur
        today", which invited exactly the simplification that broke the mirror
        case below."""
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        card.set_external_override(55)

        card.set_skipped("mix_unresolvable")

        assert card._status_chip.text() == "External 55%", (
            "an override is actively pinning these fans; the card must not claim "
            "nothing is controlling them"
        )

    def test_leaving_manual_over_both_states_restores_the_override_not_the_skip(self, qtbot):
        """`_restore_daemon_chip` must apply the SAME precedence `set_skipped` does.

        Both `_external_pct` and `_skipped_reason` can be set at once — the
        shipping daemon sends the pair for up to a tick, and `set_external_override`
        records the value even while Manual is held, only skipping the paint. The
        first version of `_restore_daemon_chip` ranked skip above external while
        `set_skipped` and `docs/08` rank external above skip, and the disagreement
        is visible exactly here: release Manual over a card carrying both.

        Getting it wrong paints "Not controlled" over an override that is actively
        pinning those fans — a lie in the unsafe direction, as `set_skipped`'s own
        comment says. Nothing asserted it before, so swapping the arms back left
        the whole suite green.

        Card-level on purpose: this is a `ControlCard` invariant, and a bare card
        lets `_manual_btn.click()` drive the real `toggled` connection rather than
        calling the handler (which is the part most likely to be broken).
        """
        control = LogicalControl(
            id="lc1",
            name="LC",
            mode=ControlMode.CURVE,
            curve_id="c1",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        card = ControlCard(control, [CurveConfig(id="c1", name="C", type=CurveType.FLAT)])
        qtbot.addWidget(card)

        card._manual_btn.click()
        assert card._status_chip.text() == "Manual"
        # Both arrive while Manual holds the chip: each records its state and
        # defers the paint, which is what makes the restore order decidable.
        card.set_external_override(55)
        card.set_skipped("mix_unresolvable")
        assert card._status_chip.text() == "Manual"

        card._manual_btn.click()

        assert card._status_chip.text() == "External 55%", (
            "an override still pins these fans; restoring must not rank the "
            "suppressed skip above it"
        )

    def test_an_external_override_lapsing_restores_the_skip_chip(
        self, qtbot, app_state, profile_service
    ):
        """The mirror of the test above — and the direction that was broken.

        `set_skipped` SUPPRESSES its chip while an external override is displayed
        rather than discarding the reason, so when the override lapses the chip has
        to come back. `clear_external_override` blanked it instead, and nothing
        could ever repaint it: `set_output` early-returns while `_skipped_reason` is
        set, and the page reconciles only on a reason *delta*. The card was left
        showing a live "Now: N%" with no chip at all, permanently — the same silence
        273-i exists to end, one interleaving over.

        Reachable rather than theoretical: `/status` reads `overrides[]` live but
        `skipped_controls[]` from the last COMPLETED tick, so the two genuinely
        co-occur on the wire for up to a tick. Found by four reviewers independently
        in the v2.44.0 pre-release review; the invariant was asserted in one
        direction (above) and violated in the other.
        """
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        card.set_external_override(55)
        card.set_skipped("mix_unresolvable")
        assert card._status_chip.text() == "External 55%"

        card.clear_external_override()

        assert card._status_chip.text() == "Not controlled", (
            "the suppressed skip must be repainted when the override lapses, not "
            "blanked — nothing else will ever repaint it"
        )
        assert card._status_chip.toolTip() != "", "the reason must return with the chip"

        # And it must survive the next poll's output update, which is what made the
        # old bug permanent rather than a single-frame flicker.
        card.set_output(42.0, "CPU", 55.0)
        assert card._status_chip.text() == "Not controlled"

    def test_the_no_members_chip_does_not_inherit_a_stale_tooltip(
        self, qtbot, app_state, profile_service
    ):
        """`_apply_chip` owns the tooltip; writing the chip directly bypasses that.

        Qt maps `toolTip` to `QAccessible::Text::Description`, so a screen reader
        would announce "No members" described as "the daemon is not commanding
        these fans" — a leftover from the state before.
        """
        page = _page(qtbot, app_state, profile_service, MagicMock())
        page._on_status_reconcile(_skipped(("lc1", "sync_unresolvable")))
        card = page._control_cards["lc1"]
        assert card._status_chip.toolTip() != ""

        stripped = LogicalControl(
            id="lc1", name="LC", mode=ControlMode.CURVE, curve_id="c1", members=[]
        )
        card._update_no_members_state(stripped)

        assert card._status_chip.text() == "No members"
        assert card._status_chip.toolTip() == "", (
            "the skip tooltip must not survive onto the No members chip"
        )

    def test_clearing_a_skip_on_a_memberless_control_restores_no_members(
        self, qtbot, app_state, profile_service
    ):
        """The "No members" chip is a TERMINAL state, not a write-once label.

        The daemon records a skip BEFORE its own member-less short-circuit, so a
        role with no outputs and an unresolvable curve really is listed in
        `skipped_controls[]`. When that skip clears, the chip used to fall through
        to blank — and `set_output` early-returns for a member-less control, so
        nothing ever repainted it. The card was left with no chip at all for the
        rest of the session.
        """
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        empty = LogicalControl(
            id="lc1", name="LC", mode=ControlMode.CURVE, curve_id="c1", members=[]
        )
        card.update_control(empty, [CurveConfig(id="c1", name="C", type=CurveType.FLAT)])
        assert card._status_chip.text() == "No members"

        card.set_skipped("mix_unresolvable")
        card.clear_skipped()

        assert card._status_chip.text() == "No members", (
            "a member-less card must fall back to its terminal state, not to a "
            "blank chip nothing will ever repaint"
        )

    def test_assigning_members_takes_the_no_members_chip_down(
        self, qtbot, app_state, profile_service
    ):
        """The mirror: nothing else clears it, so it pinned the card forever.

        `set_output` repaints only once it is past its own member guard, and the
        page reconciles skips on a *delta*, so a control that gained outputs kept
        advertising "No members" for the rest of the session.
        """
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        curves = [CurveConfig(id="c1", name="C", type=CurveType.FLAT)]
        empty = LogicalControl(
            id="lc1", name="LC", mode=ControlMode.CURVE, curve_id="c1", members=[]
        )
        card.update_control(empty, curves)
        assert card._status_chip.text() == "No members"

        filled = LogicalControl(
            id="lc1",
            name="LC",
            mode=ControlMode.CURVE,
            curve_id="c1",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        card.update_control(filled, curves)

        assert card._status_chip.text() != "No members", (
            "the card now has outputs; the terminal chip must come down"
        )


class TestLiveControlOutputs:
    """277-k: the live Controls page shows what each control is actually applying.

    Until daemon 2.22.0 published `control_outputs[]` there was no live feed at
    all. `ControlsPage.update_control_outputs` had exactly one production caller,
    wired to `DemoController.outputs_changed`, so in live mode a card's output
    label sat at "—" for the entire session — a real gap against
    `CLAUDE.md § UX standards → Controls` ("what are the fans doing?"), and the
    reason several code comments and changelog lines described demo-only
    behaviour as if it were live.

    These assert the CALL SITE, not the renderer. `update_control_outputs` and
    `ControlCard.set_output` already existed and were already correct; deleting
    the one line that feeds them from the poll would leave every renderer test
    green while the feature published nothing — the "extracting a rule does not
    test the call site" trap this project has hit five times.
    """

    def test_a_poll_drives_the_card_output_label(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        assert card._output_label.text() == "—", (
            "precondition: the card starts with no live figure, which is exactly "
            "the state that persisted forever before this feed existed"
        )

        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 42.0}]})
        )

        assert "42%" in card._output_label.text(), (
            f"the poll must reach the card, got {card._output_label.text()!r}"
        )

    def test_an_absent_control_reverts_to_unknown_not_a_stale_figure(
        self, qtbot, app_state, profile_service
    ):
        """Absence means "no value", not "the last value" and not "zero".

        `docs/08` is explicit: a client "must not carry a previous value forward
        ... Render absence as 'unknown' (the reference GUI's '—'), never as 0".

        **The first version of this test asserted the opposite** and shipped the
        violation it was meant to prevent: it required the card to KEEP its last
        figure, on the strawman reasoning that the only alternative was rendering
        0. The contract never asked for 0 — it asked for "—". A card left reading
        "Now: 42%" through a 105 °C event, while the thermal force drives the fans at
        100%, is exactly the "confidently display a duty nothing is applying"
        failure that clause exists to forbid.
        """
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 42.0}]})
        )
        assert "42%" in card._output_label.text(), "precondition: a real figure is shown"

        # A thermal event: the daemon evaluates nothing, so the array is empty.
        page._on_status_reconcile(parse_status({"control_outputs": []}))

        assert card._output_label.text() == "\u2014", (
            f"an omitted control must revert to an em dash, not keep a duty "
            f"nothing is applying; got {card._output_label.text()!r}"
        )

    def test_a_control_omitted_from_a_non_empty_list_also_reverts(
        self, qtbot, app_state, profile_service
    ):
        """The partial-omission path, which the empty-list case cannot reach.

        `_apply_live_outputs` returns early once the per-card reset is done and
        the list is empty, so a test that only sends `[]` exercises one branch.
        The shape that actually occurs is a NON-empty list omitting one control —
        a per-control skip while its siblings keep evaluating. Without this, a
        regression that reset only on the empty case would ship green.
        """
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 42.0}]})
        )
        assert "42%" in card._output_label.text(), "precondition: a real figure is shown"

        # Another control is still evaluating; lc1 is not in the list.
        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "other", "output_pct": 77.0}]})
        )

        assert card._output_label.text() == "\u2014", (
            f"a control omitted from a NON-empty list must revert too; got "
            f"{card._output_label.text()!r}"
        )

    def test_an_older_daemon_leaves_the_card_alone(self, qtbot, app_state, profile_service):
        """Pre-2.22.0 daemons omit the key entirely — read that as 'no feed'.

        Asserts the PRESENCE first. Without that it passed against the card's
        construction default and would have stayed green with the whole feed
        deleted — the vacuous-absence trap (DEC-272).
        """
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 42.0}]})
        )
        assert "42%" in card._output_label.text(), "precondition: the feed works at all"

        page._on_status_reconcile(DaemonStatus())

        assert card._output_label.text() == "\u2014"


class TestLiveOutputMemberAssembly:
    """277-k: the per-member half of the live feed, which the helper tests miss.

    `divergent_gpu_output` is unit-tested with a hand-built dict, and
    `set_output(gpu_output_pct=...)` is tested directly — but nothing populated
    `AppState.fans`, so the loop in `_apply_live_outputs` that builds
    `member_outputs` from `last_commanded_pwm` always ran with an empty mapping.
    A wrong key there would silently drop the advertised "(GPU N%)" suffix with
    every existing test green: the project's five-times "extracting a rule does
    not test the call site" trap.
    """

    def _mixed_page(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service, client=MagicMock())
        qtbot.addWidget(page)
        curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
        # Mixed control: a GPU grouped with a chassis fan is the ONLY shape that
        # can diverge (DEC-119) — a GPU-only control's headline already is the
        # GPU value, so `divergent_gpu_output` returns None for it by design.
        ctrl = LogicalControl(
            id="lc1",
            name="LC",
            mode=ControlMode.CURVE,
            curve_id="c1",
            members=[
                ControlMember(source="openfan", member_id="openfan:ch00"),
                ControlMember(source="amd_gpu", member_id="amd_gpu:0000:03:00.0"),
            ],
        )
        page._refresh_controls_grid(Profile(id="p", name="P", controls=[ctrl], curves=[curve]))
        return page

    def test_a_diverging_gpu_member_reaches_the_card(self, qtbot, app_state, profile_service):
        from control_ofc.api.models import FanReading

        page = self._mixed_page(qtbot, app_state, profile_service)
        card = page._control_cards["lc1"]
        # The GPU is idling well below the control-wide value.
        app_state.fans = [
            FanReading(id="openfan:ch00", source="openfan", last_commanded_pwm=60),
            FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", last_commanded_pwm=12),
        ]

        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 60.0}]})
        )

        text = card._output_label.text()
        assert "60%" in text, f"the control-wide figure must show: {text!r}"
        assert "GPU 12%" in text, (
            f"the diverging GPU member must be annotated — this is the half fed "
            f"from AppState.fans, and nothing else exercises it: {text!r}"
        )

    def test_a_gpu_tracking_the_control_is_not_annotated(self, qtbot, app_state, profile_service):
        """Guard against over-fixing: the suffix is for DIVERGENCE only."""
        from control_ofc.api.models import FanReading

        page = self._mixed_page(qtbot, app_state, profile_service)
        card = page._control_cards["lc1"]
        app_state.fans = [
            FanReading(id="openfan:ch00", source="openfan", last_commanded_pwm=60),
            FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", last_commanded_pwm=60),
        ]

        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 60.0}]})
        )

        assert "GPU" not in card._output_label.text(), (
            "a GPU running at the control-wide value adds noise, not information"
        )


class TestLiveOutputDoesNotClobberThePreview:
    """277-k round 2: the 1 Hz feed must not overwrite a live curve-edit preview.

    `_update_card_previews` writes "Preview: N%" into the same `_output_label`
    every time the user drags a point, so that the card shows what the edit WOULD
    do. Wiring `set_output` to the poll put a 1 Hz writer on that label — the
    preview survived under a second per drag, silently degrading a feature that
    predates this one.

    Note this was already true in DEMO mode, where `DemoController.outputs_changed`
    drives the same path at 1 Hz; 277-k extended it to every live user, which is
    what made it worth fixing rather than recording.
    """

    def test_the_card_being_edited_keeps_its_preview(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
        page._curve_editor.set_curve(curve)
        # `_set_curve_editing` is what the page's own open path calls, and it is
        # what `_close_editor` clears — so it, not the editor's never-reset
        # `_curve`, is the state the exemption reads.
        page._set_curve_editing("c1")
        card.update_output_preview("C", "cpu", 55.0, 40.0)
        assert card._output_label.text().startswith("Preview:"), "precondition"

        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 99.0}]})
        )

        assert card._output_label.text().startswith("Preview:"), (
            f"the poll must not stamp over a live preview; got {card._output_label.text()!r}"
        )

    def test_a_card_not_being_edited_still_updates(self, qtbot, app_state, profile_service):
        """Guard against over-fixing: only the edited curve's cards are exempt."""
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        other = CurveConfig(id="c-other", name="Other", type=CurveType.FLAT, flat_output_pct=10.0)
        page._curve_editor.set_curve(other)
        page._set_curve_editing("c-other")

        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 99.0}]})
        )

        assert "99%" in card._output_label.text(), (
            "a card whose curve is NOT on the workbench must still track the poll"
        )


class TestPreviewExemptionIsReleasedOnClose:
    """277-k round 3: the preview exemption must end when the editor does.

    `CurveEditor._curve` is assigned only in `__init__` and `set_curve` and is
    never reset, so `get_curve()` keeps returning the last-edited curve forever.
    Deriving the exemption from it permanently excluded the last-edited control
    from the live feed — frozen on a stale "Preview: N%", including through a
    thermal event. That is the same contract violation the reset loop exists to
    prevent, reintroduced for one control by the exemption meant to protect it.

    The page already tracks `_editing_curve_id`, which `_close_editor` clears.
    """

    def test_the_card_updates_again_after_the_editor_closes(
        self, qtbot, app_state, profile_service
    ):
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
        page._curve_editor.set_curve(curve)
        page._set_curve_editing("c1")
        card.update_output_preview("C", "cpu", 55.0, 40.0)
        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 99.0}]})
        )
        assert card._output_label.text().startswith("Preview:"), (
            "precondition: the exemption holds while the editor is open"
        )

        page._close_editor()

        page._on_status_reconcile(
            parse_status({"control_outputs": [{"control_id": "lc1", "output_pct": 99.0}]})
        )
        assert "99%" in card._output_label.text(), (
            f"once the editor is closed the card must track the poll again; got "
            f"{card._output_label.text()!r}"
        )

    def test_a_closed_editor_does_not_freeze_the_card_through_absence(
        self, qtbot, app_state, profile_service
    ):
        """The thermal-event shape, which is what makes the leak matter."""
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
        page._curve_editor.set_curve(curve)
        page._set_curve_editing("c1")
        card.update_output_preview("C", "cpu", 55.0, 40.0)
        page._close_editor()

        # A thermal event: the daemon publishes no per-control output, so it
        # reports no control output at all.
        page._on_status_reconcile(parse_status({"control_outputs": []}))

        assert card._output_label.text() == "—", (
            f"a closed editor must not leave a control permanently exempt from "
            f"the absence reset; got {card._output_label.text()!r}"
        )
