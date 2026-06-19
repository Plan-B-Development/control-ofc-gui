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
