"""FFA-f: a manual override survives a renew that never reached the daemon.

The daemon keeps an override for its full TTL (three renew periods), so a renew
lost in transit — a timeout or a vanished socket — says nothing about whether
the pin is still held. Reverting the card on the first one dropped the token
while the daemon kept pinning the fan, and the next poll showed an "External"
override the user had no way to release. These tests drive the page through its
real wiring (the Manual button, the renew timer's ``timeout``, the worker's
result signal) on the inline dispatch path conftest selects, with the page's
clock pinned so the TTL window is deterministic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
from control_ofc.api.models import OverrideGrant
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
)
from control_ofc.ui.pages.controls_page import ControlsPage

# Deliberately not the page's fallback TTL, so a test that passes proves the
# page counts the grant's own TTL.
_TTL = 9


def _grant(token: int, pwm: int = 50) -> OverrideGrant:
    return OverrideGrant(
        control_id="lc1",
        override_token=token,
        pwm_percent=pwm,
        ttl_secs=_TTL,
        renew_secs=5,
        expires_in_secs=_TTL,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _manual_page(qtbot, app_state, profile_service, client):
    """A live page whose one control is in Manual under token 7 at clock 1000."""
    page = ControlsPage(state=app_state, profile_service=profile_service, client=client)
    qtbot.addWidget(page)
    clock = _Clock()
    page._clock = clock
    curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
    ctrl = LogicalControl(
        id="lc1",
        name="LC",
        mode=ControlMode.CURVE,
        curve_id="c1",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    page._refresh_controls_grid(Profile(id="p", name="P", controls=[ctrl], curves=[curve]))
    client.override_take.return_value = _grant(7)
    card = page._control_cards["lc1"]
    card._manual_btn.setChecked(True)  # the user takes Manual
    assert page._overrides == {"lc1": 7}
    assert card._manual_btn.isChecked()
    return page, card, clock


def _renew_tick(page) -> None:
    """Fire the renew timer the way Qt does."""
    page._override_renew_timer.timeout.emit()


def test_a_timed_out_renew_followed_by_a_success_keeps_the_card_in_manual(
    qtbot, app_state, profile_service
):
    client = MagicMock()
    page, card, clock = _manual_page(qtbot, app_state, profile_service, client)

    client.override_renew.side_effect = DaemonTimeout()
    clock.now += 5
    _renew_tick(page)

    assert card._manual_btn.isChecked(), "one lost renew must not revert the card"
    assert page._overrides == {"lc1": 7}
    assert page._override_renew_timer.isActive()
    assert "lc1" not in page._renew_in_flight, "the next tick must be free to retry"

    client.override_renew.side_effect = None
    client.override_renew.return_value = SimpleNamespace(override_token=7)
    clock.now += 5
    _renew_tick(page)

    assert client.override_renew.call_count == 2
    assert card._manual_btn.isChecked()
    assert page._overrides == {"lc1": 7}
    client.override_release.assert_not_called()


def test_renews_lost_until_the_ttl_has_run_out_revert_and_release(
    qtbot, app_state, profile_service
):
    client = MagicMock()
    page, card, clock = _manual_page(qtbot, app_state, profile_service, client)
    client.override_renew.side_effect = DaemonUnavailable()

    assert _TTL != ControlsPage._OVERRIDE_TTL_FALLBACK_S
    for _ in range(2):  # 4 s and 8 s after the grant: still inside its TTL
        clock.now += 4
        _renew_tick(page)
        assert card._manual_btn.isChecked()

    clock.now += 1  # 9 s: the daemon has dropped the override by now
    _renew_tick(page)

    assert not card._manual_btn.isChecked()
    assert page._overrides == {}
    assert not page._override_renew_timer.isActive()
    # A renew that timed out may have been applied late, so the token is released.
    assert client.override_release.call_args[0][:2] == ("lc1", 7)


def test_a_successful_renew_restarts_the_ttl_window(qtbot, app_state, profile_service):
    client = MagicMock()
    page, card, clock = _manual_page(qtbot, app_state, profile_service, client)

    client.override_renew.return_value = SimpleNamespace(override_token=7)
    clock.now += 5
    _renew_tick(page)  # lands at 1005: the daemon now holds it until 1014

    client.override_renew.side_effect = DaemonTimeout()
    clock.now += 8  # 1013 — past the grant's own TTL, inside the renewed one
    _renew_tick(page)

    assert card._manual_btn.isChecked()
    assert page._overrides == {"lc1": 7}

    clock.now += 1  # 1014 — the renewed TTL has run out
    _renew_tick(page)

    assert not card._manual_btn.isChecked()


def test_a_daemon_rejection_still_reverts_on_the_first_renew(qtbot, app_state, profile_service):
    """The daemon's answer is authoritative: an expired override is gone now."""
    client = MagicMock()
    page, card, clock = _manual_page(qtbot, app_state, profile_service, client)
    client.override_renew.side_effect = DaemonError(
        code="override_expired", message="no active override", status=404
    )

    clock.now += 5
    _renew_tick(page)

    assert not card._manual_btn.isChecked()
    assert page._overrides == {}
    client.override_release.assert_not_called()


def test_a_failed_re_pin_releases_the_grant_it_held(qtbot, app_state, profile_service):
    """A slider drag re-pins with a new take. When that take fails, the card
    reverts, so the grant it replaced must go too — the renew timer used to keep
    it alive with the card already showing curve control."""
    client = MagicMock()
    page, card, _clock = _manual_page(qtbot, app_state, profile_service, client)

    client.override_take.side_effect = DaemonTimeout()
    card._manual_slider.setValue(70)  # the user drags while in Manual
    page._override_value_timer.timeout.emit()  # the debounce elapses

    assert client.override_take.call_count == 2, "the drag must have re-pinned"
    assert not card._manual_btn.isChecked()
    assert page._overrides == {}
    assert not page._override_renew_timer.isActive()
    assert client.override_release.call_args[0][:2] == ("lc1", 7)
