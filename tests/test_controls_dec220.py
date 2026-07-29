"""DEC-220: manual-override take/renew/release run on a worker thread, so a slow
or half-dead daemon can never freeze the Qt main loop. These tests exercise the
REAL threaded path (``_OVERRIDE_USE_THREAD=True``, overriding the suite-wide
synchronous default from conftest) with a mock client injected into the worker."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from control_ofc.api.errors import DaemonError
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


def _grant(token: int = 7, renew_secs: int = 5) -> OverrideGrant:
    return OverrideGrant(
        control_id="lc1",
        override_token=token,
        pwm_percent=50,
        ttl_secs=15,
        renew_secs=renew_secs,
        expires_in_secs=15,
    )


def _live_page(qtbot, app_state, profile_service, client):
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


def test_override_renew_dispatched_off_the_main_thread(
    qtbot, app_state, profile_service, monkeypatch
):
    """`_renew_overrides` must return immediately even when the daemon renew is
    slow — the blocking HTTP runs on the worker thread, not the main loop."""
    monkeypatch.setattr(ControlsPage, "_OVERRIDE_USE_THREAD", True)

    renew_entered = threading.Event()
    unblock = threading.Event()
    mock_client = MagicMock()
    mock_client.override_take.return_value = _grant(token=7)

    def slow_renew(*_a, **_k):
        renew_entered.set()
        unblock.wait(2.0)  # hold the worker thread inside the renew
        return SimpleNamespace(override_token=8)

    mock_client.override_renew.side_effect = slow_renew
    monkeypatch.setattr(
        "control_ofc.ui.pages.controls_page.DaemonClient", lambda socket_path: mock_client
    )

    page = _live_page(qtbot, app_state, profile_service, mock_client)
    try:
        page._take_override("lc1", 50)
        qtbot.waitUntil(lambda: page._overrides.get("lc1") == 7, timeout=2000)

        t0 = time.monotonic()
        page._renew_overrides()  # dispatches the (slow) renew to the worker
        dispatch_elapsed = time.monotonic() - t0

        assert renew_entered.wait(1.0), "worker never started the renew"
        assert dispatch_elapsed < 0.5, (
            f"_renew_overrides blocked the main thread for {dispatch_elapsed:.2f}s — it "
            "must dispatch to the worker and return immediately (DEC-220)"
        )
    finally:
        unblock.set()
        page.cleanup()


def test_release_during_inflight_take_releases_the_orphan(
    qtbot, app_state, profile_service, monkeypatch
):
    """A take that completes AFTER the user released must release the orphan
    grant — `_manual_intent` is the source of truth, so no override outlives the
    user's intent (DEC-220)."""
    monkeypatch.setattr(ControlsPage, "_OVERRIDE_USE_THREAD", True)

    take_entered = threading.Event()
    unblock = threading.Event()
    mock_client = MagicMock()

    def slow_take(*_a, **_k):
        take_entered.set()
        unblock.wait(2.0)  # keep the take in flight until the test releases
        return _grant(token=7)

    mock_client.override_take.side_effect = slow_take
    monkeypatch.setattr(
        "control_ofc.ui.pages.controls_page.DaemonClient", lambda socket_path: mock_client
    )

    page = _live_page(qtbot, app_state, profile_service, mock_client)
    try:
        page._take_override("lc1", 50)  # take in flight (blocked in the worker)
        assert take_entered.wait(1.0), "worker never started the take"
        page._release_override("lc1")  # user releases before the take returns
        assert "lc1" not in page._manual_intent

        unblock.set()  # the take now completes; intent is already gone
        qtbot.waitUntil(lambda: mock_client.override_release.called, timeout=2000)
        assert mock_client.override_release.call_args[0][:2] == ("lc1", 7)
        assert "lc1" not in page._overrides
    finally:
        unblock.set()
        page.cleanup()


def test_override_renew_success_advances_token_over_the_worker_thread(
    qtbot, app_state, profile_service, monkeypatch
):
    """B1: a successful renew, delivered over the real ``renew_result``
    QueuedConnection, must advance the held token (7 -> 8). Guards the threaded
    signal handoff that the suite-wide synchronous default (conftest) bypasses —
    every other override test observes only the inline path."""
    monkeypatch.setattr(ControlsPage, "_OVERRIDE_USE_THREAD", True)

    mock_client = MagicMock()
    mock_client.override_take.return_value = _grant(token=7)
    mock_client.override_renew.return_value = SimpleNamespace(override_token=8)
    monkeypatch.setattr(
        "control_ofc.ui.pages.controls_page.DaemonClient", lambda socket_path: mock_client
    )

    page = _live_page(qtbot, app_state, profile_service, mock_client)
    try:
        page._take_override("lc1", 50)
        qtbot.waitUntil(lambda: page._overrides.get("lc1") == 7, timeout=2000)

        page._renew_overrides()  # dispatched to the worker; result arrives via signal
        qtbot.waitUntil(lambda: page._overrides.get("lc1") == 8, timeout=2000)
        assert page._overrides["lc1"] == 8
    finally:
        page.cleanup()


def test_override_renew_rejection_reverts_card_over_the_worker_thread(
    qtbot, app_state, profile_service, monkeypatch
):
    """B1: a REJECTED renew, delivered over the real ``renew_result``
    QueuedConnection, must clear the override AND visually revert the card. Drive
    the card into Manual first so ``clear_manual``'s not-checked early-return
    cannot mask the revert. (Button ``isChecked`` is asserted rather than child
    ``isVisible`` — the latter depends on the page being shown, which offscreen
    tests do not do.)"""
    monkeypatch.setattr(ControlsPage, "_OVERRIDE_USE_THREAD", True)

    mock_client = MagicMock()
    mock_client.override_take.return_value = _grant(token=7)
    mock_client.override_renew.side_effect = DaemonError(
        code="stale_fencing_token", message="override lapsed"
    )
    monkeypatch.setattr(
        "control_ofc.ui.pages.controls_page.DaemonClient", lambda socket_path: mock_client
    )

    page = _live_page(qtbot, app_state, profile_service, mock_client)
    try:
        card = page._control_cards["lc1"]
        card._manual_btn.setChecked(True)  # user takes Manual -> _take_override
        qtbot.waitUntil(lambda: page._overrides.get("lc1") == 7, timeout=2000)
        assert card._manual_btn.isChecked()  # card is in Manual

        page._renew_overrides()  # rejected renew dispatched to the worker
        qtbot.waitUntil(lambda: "lc1" not in page._overrides, timeout=2000)

        # The rejection reverted the card via the real signal handoff.
        assert not card._manual_btn.isChecked()
    finally:
        page.cleanup()


def test_take_result_reflects_granted_pwm_on_the_card(qtbot, app_state, profile_service):
    """P2-1: `_on_take_result` reflects the daemon-applied `grant.pwm_percent`
    (a floor/thermal-clamped value) onto the card, not the raw request — so the
    card's slider/label can't claim a speed the fan isn't running."""
    page = _live_page(qtbot, app_state, profile_service, MagicMock())
    try:
        card = page._control_cards["lc1"]
        # Put the card in manual + register intent WITHOUT dispatching a take
        # (blockSignals avoids the page's take flow, which a MagicMock can't serve).
        card._manual_btn.blockSignals(True)
        card._manual_btn.setChecked(True)
        card._manual_btn.blockSignals(False)
        page._manual_intent.add("lc1")

        grant = OverrideGrant(
            control_id="lc1", override_token=7, pwm_percent=30, renew_secs=5, ttl_secs=15
        )
        page._on_take_result("lc1", 10, grant, None)  # requested 10, daemon granted 30

        assert card._manual_slider.value() == 30
        assert card._manual_pct_label.text() == "30%"
    finally:
        page.cleanup()
