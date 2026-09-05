"""G8 — five poll fields the GUI parsed and rendered nowhere.

`WIRE-p` (identify mode + held duty), `WIRE-q` (skip duration + the daemon's own
control name), `WIRE-r` (the readiness deep-link code), `WIRE-s` (the daemon's
AIO status token), `WIRE-af` (the readiness scan generation).
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    AioHwmonCapability,
    Capabilities,
    HardwareReadiness,
    IdentifyStatusEntry,
    ReadinessRollup,
)
from control_ofc.services.controls_view import skipped_control_feedback
from control_ofc.services.overview_view import build_device_discovery_vm

# ── WIRE-p ───────────────────────────────────────────────────────────────────


def test_a_pump_perturbation_is_not_described_as_a_stop() -> None:
    entry = IdentifyStatusEntry(
        fan_id="pump", mode="pump_perturb", identify_pwm_percent=85, expires_in_secs=8
    )
    assert entry.describe_hold() == "held at 85%"


def test_a_stop_is_described_as_a_stop() -> None:
    assert IdentifyStatusEntry(fan_id="f", mode="stop").describe_hold() == "stopped"


def test_a_pre_dec311_daemon_that_omits_the_mode_still_reads_as_a_stop() -> None:
    """Correct, not merely safe: such a daemon can only stop."""
    assert IdentifyStatusEntry(fan_id="f", mode="").describe_hold() == "stopped"


def test_an_unknown_mode_is_rendered_not_coerced_into_a_stop() -> None:
    """273-i. A future mode described as a stop would be the same lie in a new
    coat — and the coat is the reason DEC-311 put this field on the poll."""
    entry = IdentifyStatusEntry(fan_id="f", mode="ramp_pulse", identify_pwm_percent=70)
    assert "ramp_pulse" in entry.describe_hold()
    assert entry.describe_hold() != "stopped"


# ── WIRE-q ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ms", "expected"),
    [(0, "Not controlled"), (9_000, "Not controlled · 9s"), (250_000, "Not controlled · 4m")],
)
def test_the_chip_carries_a_coarse_duration(ms: int, expected: str) -> None:
    chip, _tooltip = skipped_control_feedback("sensor_unavailable", ms)
    assert chip == expected


def test_a_zero_duration_is_not_rendered_as_just_started() -> None:
    """The daemon lists a control only after three consecutive skipped ticks, so
    a 0 here means "not reported" — and "for 0s" would say the opposite."""
    chip, tooltip = skipped_control_feedback("sensor_unavailable", 0)
    assert "0s" not in chip
    assert "0s" not in tooltip


def test_the_tooltip_names_the_control_the_daemon_is_skipping() -> None:
    """The daemon's name, not the GUI's — which may come from a stale profile."""
    _chip, tooltip = skipped_control_feedback("curve_not_found", 9_000, "Radiator Pair")
    assert '"Radiator Pair"' in tooltip


def test_an_unnamed_control_still_reads_naturally() -> None:
    _chip, tooltip = skipped_control_feedback("curve_not_found", 9_000)
    assert "these fans" in tooltip
    assert '""' not in tooltip


def test_the_duration_is_coarse_enough_not_to_repaint_every_second() -> None:
    """The chip rides the 1 Hz poll. Two readings a second apart inside the same
    minute must render identically, or the page repaints every tick."""
    a, _ = skipped_control_feedback("curve_not_found", 250_000)
    b, _ = skipped_control_feedback("curve_not_found", 251_000)
    assert a == b


# ── WIRE-s ───────────────────────────────────────────────────────────────────


def _aio_line(cap: AioHwmonCapability) -> str:
    return build_device_discovery_vm(Capabilities(aio_hwmon=cap), None).aio


def test_the_daemon_status_token_drives_the_liquid_cooling_line() -> None:
    assert "pump/fan writable" in _aio_line(
        AioHwmonCapability(present=True, status="supported", pump_writable=True)
    )
    assert "monitor-only" in _aio_line(
        AioHwmonCapability(present=True, status="monitor_only", pump_writable=False)
    )


def test_the_token_outranks_the_re_derivation() -> None:
    """The point of the row: the GUI re-derived this from `present` +
    `pump_writable`. A daemon whose token and flags disagree must be believed on
    the token, or the two sides can drift apart silently.
    """
    line = _aio_line(AioHwmonCapability(present=True, status="monitor_only", pump_writable=True))
    assert "monitor-only" in line
    assert "pump/fan writable" not in line


def test_an_unknown_status_is_described_rather_than_called_undetected() -> None:
    line = _aio_line(AioHwmonCapability(present=True, status="degraded"))
    assert "Not detected" not in line
    assert "degraded" in line


def test_absent_is_still_absent() -> None:
    assert "Not detected" in _aio_line(AioHwmonCapability(present=False))


# ── WIRE-r ───────────────────────────────────────────────────────────────────


def test_the_footer_chip_emits_the_deep_link_code(qtbot, qapp) -> None:
    from control_ofc.ui.components.footer import StatusFooter

    footer = StatusFooter()
    qtbot.addWidget(footer)
    footer.set_readiness_rollup(
        ReadinessRollup(overall="warning", warning=2, top_code="no_pwm_controls")
    )
    seen: list[str] = []
    footer.readiness_clicked.connect(seen.append)
    footer._readiness_btn.click()
    assert seen == ["no_pwm_controls"]


def test_a_rollup_without_a_code_emits_empty_rather_than_a_stale_one(qtbot, qapp) -> None:
    """`top_code` is omitted when everything passes, so a retained previous code
    would deep-link to an item that is no longer the problem."""
    from control_ofc.ui.components.footer import StatusFooter

    footer = StatusFooter()
    qtbot.addWidget(footer)
    footer.set_readiness_rollup(ReadinessRollup(overall="warning", top_code="no_pwm_controls"))
    footer.set_readiness_rollup(ReadinessRollup(overall="ok"))
    seen: list[str] = []
    footer.readiness_clicked.connect(seen.append)
    footer._readiness_btn.click()
    assert seen == [""]


def test_the_hardware_page_scrolls_to_the_named_item(qtbot, qapp) -> None:
    from control_ofc.api.models import ReadinessItem
    from control_ofc.ui.pages.hardware_page import HardwarePage

    page = HardwarePage(state=None, client=None)
    qtbot.addWidget(page)
    page._on_readiness_ok(
        HardwareReadiness(
            items=[ReadinessItem(code="no_pwm_controls", severity="warning", summary="x")]
        )
    )
    assert page.focus_readiness_item("no_pwm_controls") is True
    # A miss is normal — the rollup is cached daemon-side and can name an item
    # the current report no longer holds.
    assert page.focus_readiness_item("something_else") is False


# ── WIRE-af ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def page(qtbot, qapp):
    from control_ofc.ui.pages.hardware_page import HardwarePage

    p = HardwarePage(state=None, client=None)
    qtbot.addWidget(p)
    p._on_readiness_ok(HardwareReadiness(generation=4, overall="ok"))
    return p


def test_an_advanced_generation_reports_a_real_rescan(page) -> None:
    page._awaiting_rescan = True
    page._on_readiness_ok(HardwareReadiness(generation=5, overall="ok"))
    assert "re-scanned" in page._status_label.text()


def test_an_unchanged_generation_says_so_rather_than_claiming_a_rescan(page) -> None:
    """Age cannot decide this: a cached report's age keeps climbing, and a
    genuine re-scan of unchanged hardware looks identical by every other field.
    """
    page._awaiting_rescan = True
    page._on_readiness_ok(HardwareReadiness(generation=4, overall="ok"))
    assert "No change" in page._status_label.text()
    assert "re-scanned" not in page._status_label.text()


def test_a_background_fetch_reports_nothing(page) -> None:
    """Saying "unchanged" after a fetch nobody asked for would be noise."""
    page._status_label.setText("")
    page._on_readiness_ok(HardwareReadiness(generation=9, overall="ok"))
    assert page._status_label.text() == ""


def test_a_failed_refresh_does_not_leave_the_wait_armed(page) -> None:
    """Otherwise the next background fetch reports on a rescan the user never
    asked for, and possibly with the wrong verdict."""
    page._awaiting_rescan = True
    page._on_readiness_error("transient", "socket closed")
    assert page._awaiting_rescan is False
    page._status_label.setText("")
    page._on_readiness_ok(HardwareReadiness(generation=99, overall="ok"))
    assert page._status_label.text() == ""
