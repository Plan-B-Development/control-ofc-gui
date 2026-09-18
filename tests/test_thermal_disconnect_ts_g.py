"""`TS-g`: after a disconnect, nothing shows the last thermal state as current.

The ribbon's thermal pill and the System State Safety row are refreshed only by
a successful poll, so before this change a disconnect froze both on their last
value — "Thermal OK" in the always-visible ribbon over a daemon nobody could
reach. The footer already hid its chip for exactly this reason (DEC-222); these
tests hold the other two to the same rule, at each layer and through the real
signal path, because a setter nobody calls is the trap `CLAUDE.md` names first.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from control_ofc.api.models import ConnectionState, DaemonStatus, ThermalSafetyInfo
from control_ofc.services.system_state_view import (
    THERMAL_STATE_NO_CONNECTION,
    SilenceState,
    SilenceVM,
    build_safety_gpu_vm,
)
from control_ofc.ui.status_ribbon import StatusRibbon
from tests.test_system_state_health_noise import _healthy_gigabyte, _page


def _flush() -> None:
    """Dispatch the deleteLater() a re-render posted, so findChild sees the new row."""
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# ── The ribbon ─────────────────────────────────────────────────────────────


def test_the_ribbon_hides_its_thermal_pill_on_disconnect(qtbot):
    ribbon = StatusRibbon()
    qtbot.addWidget(ribbon)
    ribbon.set_thermal_state("normal")
    assert ribbon._thermal_pill.isVisibleTo(ribbon), "precondition: a live state is shown"

    ribbon.set_live(False)
    assert not ribbon._thermal_pill.isVisibleTo(ribbon)


def test_reconnecting_alone_does_not_bring_the_old_state_back(qtbot):
    """The pill returns with the next poll's state, never with the last one."""
    ribbon = StatusRibbon()
    qtbot.addWidget(ribbon)
    ribbon.set_thermal_state("normal")
    ribbon.set_live(False)

    ribbon.set_live(True)
    assert not ribbon._thermal_pill.isVisibleTo(ribbon)

    ribbon.set_thermal_state("emergency")
    assert ribbon._thermal_pill.isVisibleTo(ribbon)
    assert ribbon._thermal_pill.text() == "THERMAL: EMERGENCY"


# ── The Safety row's view model ────────────────────────────────────────────


def _silence() -> SilenceState:
    return SilenceState(allow_acknowledge=True, allow_dismiss=True)


def test_no_connection_renders_unknown_rather_than_the_snapshot():
    """The empty string falls back to the fetched snapshot; the marker must not.

    The snapshot here says `emergency`, so a marker that fell through to it
    would render "Emergency" — the fallback is the defect this guards.
    """
    diag = _healthy_gigabyte(
        thermal_safety=ThermalSafetyInfo(state="emergency", cpu_sensor_found=True)
    )
    assert build_safety_gpu_vm(diag, live_thermal_state="").thermal_text == "Emergency", (
        "precondition: with no live state the snapshot is what renders"
    )

    vm = build_safety_gpu_vm(
        diag, live_thermal_state=THERMAL_STATE_NO_CONNECTION, silence=_silence()
    )

    assert vm.thermal_text == "Unknown — disconnected"
    assert vm.thermal_state == "neutral"
    # The limit is configuration, not state, and stays interpolated from the
    # daemon's report (DEC-308) — never a literal.
    assert vm.thermal_limit_text == (f"Limit: {diag.thermal_safety.emergency_threshold_c:.0f} °C")


def test_no_connection_offers_nothing_to_silence():
    diag = _healthy_gigabyte()
    live = build_safety_gpu_vm(diag, live_thermal_state="normal", silence=_silence())
    assert live.thermal_silence.token and live.thermal_silence.can_acknowledge, (
        "precondition: a live row offers its silence controls"
    )

    vm = build_safety_gpu_vm(
        diag, live_thermal_state=THERMAL_STATE_NO_CONNECTION, silence=_silence()
    )
    assert vm.thermal_silence == SilenceVM()


# ── The rendered page ──────────────────────────────────────────────────────


def _thermal_label(page) -> str:
    return page.findChild(QLabel, "SystemState_Label_thermal").text()


def test_the_safety_row_says_unknown_after_a_disconnect_and_recovers(qtbot):
    page, _svc = _page(qtbot)
    page.set_thermal_state("normal")
    _flush()
    assert _thermal_label(page).startswith("Normal"), "precondition"
    assert page.findChild(QPushButton, "SystemState_ThermalAckBtn_cpu") is not None
    assert page.findChild(QPushButton, "SystemState_ThermalDismissBtn_cpu") is not None

    page.set_live(False)
    _flush()
    assert _thermal_label(page).startswith("Unknown — disconnected")
    assert page.findChild(QPushButton, "SystemState_ThermalAckBtn_cpu") is None
    assert page.findChild(QPushButton, "SystemState_ThermalDismissBtn_cpu") is None

    page.set_live(True)
    _flush()
    assert _thermal_label(page).startswith("Unknown — disconnected"), (
        "the reconnect edge must not re-assert a state no poll has confirmed"
    )

    page.set_thermal_state("normal")
    _flush()
    assert _thermal_label(page).startswith("Normal")


# ── The wiring, through AppState's own signals ─────────────────────────────


def test_a_disconnect_reaches_the_ribbon_and_the_system_state_page(
    qtbot, app_state, profile_service, settings_service
):
    from control_ofc.ui.main_window import MainWindow

    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    app_state.set_connection(ConnectionState.CONNECTED)
    app_state.set_status(DaemonStatus(thermal_state="normal"))
    assert win.status_ribbon._thermal_pill.isVisibleTo(win.status_ribbon), "precondition"
    assert win.system_state_page._live_thermal_state == "normal", "precondition"

    app_state.set_connection(ConnectionState.DISCONNECTED)

    assert not win.status_ribbon._thermal_pill.isVisibleTo(win.status_ribbon)
    assert win.system_state_page._live_thermal_state == THERMAL_STATE_NO_CONNECTION
    assert not win.footer._thermal_btn.isVisibleTo(win.footer), "DEC-222 still wired"

    app_state.set_connection(ConnectionState.CONNECTED)
    app_state.set_status(DaemonStatus(thermal_state="emergency"))
    assert win.status_ribbon._thermal_pill.text() == "THERMAL: EMERGENCY"
    assert win.system_state_page._live_thermal_state == "emergency"
