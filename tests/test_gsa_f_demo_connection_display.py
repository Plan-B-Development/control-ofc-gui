"""`GSA-f`: demo mode never claims a daemon connection.

Demo sets ``ConnectionState.CONNECTED`` so every page renders, and that used to
raise a "Connected to daemon" toast and paint the ribbon and banner green with
"Connected" — including when demo started *because* the daemon was unreachable
(DEC-139's demo-on-disconnect fallback).
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.services.app_state import AppState
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.status_banner import (
    CONNECTION_CHIP,
    CONNECTION_LABELS,
    DEMO_CONNECTION_CHIP,
    DEMO_CONNECTION_LABEL,
    StatusBanner,
)
from control_ofc.ui.status_ribbon import StatusRibbon

CONNECTED_TOAST = "Connected to daemon"


def _window(qtbot, app_state, profile_service, settings_service, *, demo: bool) -> MainWindow:
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=demo,
    )
    qtbot.addWidget(win)
    return win


def test_live_connect_still_toasts_and_paints_the_ribbon_green(
    qtbot, app_state, profile_service, settings_service
):
    """The presence half: a real connection keeps its toast and green ribbon."""
    win = _window(qtbot, app_state, profile_service, settings_service, demo=False)
    app_state.set_connection(ConnectionState.CONNECTED)
    assert win.error_banner.isVisibleTo(win)
    assert win.error_banner._message_label.text() == CONNECTED_TOAST
    assert win.status_ribbon._daemon_label.text() == CONNECTION_LABELS[ConnectionState.CONNECTED]
    assert win.status_ribbon._daemon_label.property("class") == "SuccessChip"
    assert win.status_ribbon._daemon_led._role == "ok"


def test_starting_demo_shows_no_connected_toast_and_a_demo_ribbon(
    qtbot, profile_service, settings_service
):
    # A fresh state, as `main.py` builds it — NOT the `app_state` fixture, which
    # is already CONNECTED, so demo's own `set_connection(CONNECTED)` would not
    # emit and the toast path would never run.
    app_state = AppState()
    assert app_state.connection == ConnectionState.DISCONNECTED
    connected = []
    app_state.connection_changed.connect(connected.append)
    win = _window(qtbot, app_state, profile_service, settings_service, demo=True)
    assert connected[-1] == ConnectionState.CONNECTED, "demo start must emit CONNECTED"
    # Demo still reports CONNECTED internally — that is what keeps its pages live.
    assert app_state.mode == OperationMode.DEMO
    assert app_state.connection == ConnectionState.CONNECTED

    assert win.error_banner._message_label.text() != CONNECTED_TOAST
    assert not win.error_banner.isVisibleTo(win)

    ribbon = win.status_ribbon
    assert ribbon._daemon_label.text() == DEMO_CONNECTION_LABEL
    assert ribbon._daemon_label.property("class") == DEMO_CONNECTION_CHIP
    assert ribbon._daemon_led._role == "info"

    banner_label = win.status_banner._connection_label
    assert banner_label.text() == DEMO_CONNECTION_LABEL
    assert banner_label.property("class") == DEMO_CONNECTION_CHIP


def test_demo_chip_is_not_the_connected_chip():
    assert CONNECTION_CHIP[ConnectionState.CONNECTED] != DEMO_CONNECTION_CHIP
    assert DEMO_CONNECTION_LABEL not in CONNECTION_LABELS.values()


@pytest.mark.parametrize("mode_first", [True, False])
def test_ribbon_and_banner_show_demo_whichever_signal_arrives_first(qtbot, mode_first):
    ribbon, banner = StatusRibbon(), StatusBanner()
    qtbot.addWidget(ribbon)
    qtbot.addWidget(banner)
    for widget in (ribbon, banner):
        if mode_first:
            widget.set_operation_mode(OperationMode.DEMO)
            widget.set_connection_state(ConnectionState.CONNECTED)
        else:
            widget.set_connection_state(ConnectionState.CONNECTED)
            widget.set_operation_mode(OperationMode.DEMO)
    assert ribbon._daemon_label.text() == DEMO_CONNECTION_LABEL
    assert ribbon._daemon_led._role == "info"
    assert banner._connection_label.text() == DEMO_CONNECTION_LABEL


def test_leaving_demo_restores_the_connection_display(qtbot):
    ribbon, banner = StatusRibbon(), StatusBanner()
    qtbot.addWidget(ribbon)
    qtbot.addWidget(banner)
    for widget in (ribbon, banner):
        widget.set_operation_mode(OperationMode.DEMO)
        widget.set_connection_state(ConnectionState.DISCONNECTED)
        widget.set_operation_mode(OperationMode.READ_ONLY)
    assert ribbon._daemon_label.text() == CONNECTION_LABELS[ConnectionState.DISCONNECTED]
    assert ribbon._daemon_led._role == "crit"
    assert banner._connection_label.property("class") == "CriticalChip"
