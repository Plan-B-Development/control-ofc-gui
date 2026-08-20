"""DEC-269: the thermal-detail wiring inside MainWindow, tested at the call site.

`safety_detail_text` and `cpu_values_for_display` are covered thoroughly in
test_dashboard_view — and that is exactly the trap CLAUDE.md records as having
recurred five times: extracting a rule into a testable function does NOT test
the call site. Before this file, nothing in the suite referenced
`MainWindow._safety_detail_text`, so the whole DEC-269 hedge could be deleted
from the production path with all tests still green.

These tests drive the real widget and assert on the dialog text it would show.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from control_ofc.api.models import DaemonStatus, SensorReading
from control_ofc.ui.main_window import MainWindow


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


def _cpu(value_c: float, age_ms: int, id: str = "cpu", kind: str = "CpuTemp") -> SensorReading:
    # age_ms >= 2000 is past Freshness.FRESH, which is the GUI's own threshold.
    return SensorReading(id=id, kind=kind, value_c=value_c, age_ms=age_ms)


# The window matches both spellings the daemon has used for this kind. Pinning
# only one leaves the other arm free to be dropped with the suite still green,
# and the label would then vanish on whichever daemon emits it.
@pytest.mark.parametrize("kind", ["CpuTemp", "cpu_temp"])
def test_a_stale_cpu_reading_is_hedged_through_the_real_window(window, kind):
    """The headline behaviour of the release, asserted on the production path."""
    window._state.sensors = [_cpu(94.0, age_ms=8000, kind=kind)]
    window._state.daemon_status = DaemonStatus(thermal_state="emergency")

    text = window._safety_detail_text()

    assert "Last known CPU sensor: 94.0" in text
    assert "Hottest CPU sensor" not in text, (
        "a reading the daemon has stopped trusting must not be presented as current"
    )


def test_a_fresh_cpu_reading_is_not_hedged_through_the_real_window(window):
    window._state.sensors = [_cpu(61.0, age_ms=100)]
    window._state.daemon_status = DaemonStatus(thermal_state="normal")

    text = window._safety_detail_text()

    assert "Hottest CPU sensor: 61.0" in text
    assert "Last known" not in text


def test_a_stale_hotter_die_does_not_print_under_the_confident_label(window):
    """The multi-CCD Ryzen case: `max()` across every CPU sensor while the flag
    required *all* of them to be stale printed the stale 94 C under "Hottest CPU
    sensor". The window must show the fresh value it is actually being cooled to."""
    window._state.sensors = [
        _cpu(61.0, age_ms=100, id="cpu_ccd0"),
        _cpu(94.0, age_ms=8000, id="cpu_ccd1"),
    ]
    window._state.daemon_status = DaemonStatus(thermal_state="normal")

    text = window._safety_detail_text()

    assert "Hottest CPU sensor: 61.0" in text
    assert "94.0" not in text


def test_clicking_the_thermal_chip_shows_the_hedged_text(qtbot, window, monkeypatch):
    """`.click()`, not `_handler()` — CLAUDE.md names this exactly.

    Every other test here calls `_safety_detail_text()` directly, which is one
    level short of the wiring: delete `footer.thermal_clicked.connect(...)` or
    break `box.setText(...)` in `_open_safety_detail`, and they all stay green
    while the dialog shows nothing. This drives the real button.
    """
    from PySide6.QtWidgets import QMessageBox

    shown: list[str] = []
    # The dialog is modal; `exec()` would block the test. Capture instead.
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))

    window._state.sensors = [_cpu(94.0, age_ms=8000)]
    window._state.daemon_status = DaemonStatus(thermal_state="emergency")

    btn = window.footer.findChild(QPushButton, "StatusFooter_Chip_thermal")
    assert btn is not None, "the thermal chip must exist for the detail to be reachable"
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)

    assert shown, "clicking the thermal chip must open the safety detail"
    assert "Last known CPU sensor: 94.0" in shown[0]


def test_the_override_count_comes_from_the_real_daemon_status(window):
    """`_safety_detail_text` resolves `len(ds.overrides)` itself. Proven only
    against the pure helper, that resolution is free to be dropped."""
    window._state.sensors = [_cpu(61.0, age_ms=100)]
    window._state.daemon_status = DaemonStatus(thermal_state="normal", overrides=["fan1", "fan2"])

    text = window._safety_detail_text()

    assert "2 manual overrides active" in text


def test_the_hedge_survives_a_daemon_that_reports_no_thermal_state(window):
    """`thermal_state` defaults to "normal"; the hedge keys on the reading's age,
    so it must still appear."""
    window._state.sensors = [_cpu(70.0, age_ms=9000)]
    window._state.daemon_status = None

    text = window._safety_detail_text()

    assert "Last known CPU sensor: 70.0" in text
