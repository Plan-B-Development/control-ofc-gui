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
        two cannot co-occur today. Enforced locally anyway: if a future daemon
        ever sent both, "Not controlled" over a live override would be a lie in
        the unsafe direction."""
        page = _page(qtbot, app_state, profile_service, MagicMock())
        card = page._control_cards["lc1"]
        card.set_external_override(55)

        card.set_skipped("mix_unresolvable")

        assert card._status_chip.text() == "External 55%", (
            "an override is actively pinning these fans; the card must not claim "
            "nothing is controlling them"
        )
