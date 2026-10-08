"""`FFA-e`: two thermal readouts that named the wrong thing.

1. A coolant emergency (DEC-443) was labelled with the CPU trip point. The
   coolant rung has its own limit (`coolant_limit_c` / `coolant_release_c` on
   `/diagnostics/hardware`), and which rung tripped is the poll's
   `emergency_causes`; the System State Safety row and the readiness report
   ignored both.
2. A sensor's `crit_alarm` / `alarm` / `fault` bits are read once, at daemon
   discovery (daemon `hwmon/types.rs`), yet a latched `crit_alarm` kept the
   sensor table showing "⚠ ALARM" on a sensor that had cooled, and the detail
   dialog called the bits "asserted" as if current.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from control_ofc.api.models import (
    ConnectionState,
    DaemonStatus,
    SensorReading,
    SensorThresholds,
    ThermalSafetyInfo,
)
from control_ofc.knowledge.sensor_knowledge import classify_reading
from control_ofc.services import overview_view as ov
from control_ofc.services.system_state_view import build_safety_gpu_vm
from control_ofc.ui.widgets.readiness_report import thermal_line
from control_ofc.ui.widgets.sensor_detail_dialog import _threshold_rows
from tests.test_system_state_health_noise import _flush, _healthy_gigabyte, _page

# Deliberately far apart, and neither the daemon's defaults, so a test that
# finds one in the text cannot be finding the other.
_CPU_LIMIT = 97.0
_COOLANT_LIMIT = 61.0
_COOLANT_RELEASE = 56.0


def _ts(state: str = "emergency", **kw) -> ThermalSafetyInfo:
    defaults = dict(
        state=state,
        cpu_sensor_found=True,
        emergency_threshold_c=_CPU_LIMIT,
        release_threshold_c=80.0,
        coolant_limit_c=_COOLANT_LIMIT,
        coolant_release_c=_COOLANT_RELEASE,
    )
    defaults.update(kw)
    return ThermalSafetyInfo(**defaults)


def _limit(causes, *, state="emergency", **ts_kw) -> str:
    diag = _healthy_gigabyte(thermal_safety=_ts(state, **ts_kw))
    return build_safety_gpu_vm(
        diag, live_thermal_state=state, live_emergency_causes=causes
    ).thermal_limit_text


# ── 1. The Safety row's limit follows `emergency_causes` ───────────────────


def test_a_coolant_emergency_names_the_coolant_limit_not_the_cpu_one():
    text = _limit(["coolant"])
    assert f"{_COOLANT_LIMIT:.0f} °C" in text
    assert "oolant" in text
    assert f"{_CPU_LIMIT:.0f}" not in text


def test_a_cpu_emergency_still_names_the_cpu_limit():
    text = _limit(["cpu"])
    assert f"{_CPU_LIMIT:.0f} °C" in text
    assert f"{_COOLANT_LIMIT:.0f}" not in text


def test_both_rungs_tripped_names_both_limits():
    text = _limit(["cpu", "coolant"])
    assert f"CPU {_CPU_LIMIT:.0f} °C" in text
    assert f"coolant {_COOLANT_LIMIT:.0f} °C" in text


def test_an_older_daemon_falls_back_to_the_cpu_limit():
    """No causes (daemon < 3.0.0) and no coolant limit: the CPU rung is the only one."""
    assert _limit([]) == f"Limit: {_CPU_LIMIT:.0f} °C"
    assert _limit(["coolant"], coolant_limit_c=None) == f"Limit: {_CPU_LIMIT:.0f} °C"


def test_outside_an_emergency_the_cpu_limit_is_shown():
    assert _limit(["coolant"], state="normal") == f"Limit: {_CPU_LIMIT:.0f} °C"


def test_the_page_renders_the_coolant_limit_and_follows_a_cause_change(qtbot):
    page, _svc = _page(qtbot, _healthy_gigabyte(thermal_safety=_ts()))

    def label() -> str:
        _flush(page)
        return page.findChild(QLabel, "SystemState_Label_thermal").text()

    page.set_thermal_state("emergency", ["cpu"])
    assert f"{_CPU_LIMIT:.0f} °C" in label(), "precondition: the CPU limit is shown"

    # Same state, different cause: the change guard must not swallow it.
    page.set_thermal_state("emergency", ["coolant"])
    assert f"{_COOLANT_LIMIT:.0f} °C" in label()
    assert f"{_CPU_LIMIT:.0f}" not in label()

    page.set_live(False)
    assert page._live_emergency_causes == ()


def test_main_window_feeds_the_page_the_polled_causes(
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
    app_state.set_status(DaemonStatus(thermal_state="emergency", emergency_causes=["coolant"]))
    assert win.system_state_page._live_thermal_state == "emergency"
    assert win.system_state_page._live_emergency_causes == ("coolant",)


# ── 1b. The readiness report names each rung's limit ───────────────────────


def test_readiness_line_names_the_coolant_limit_and_release_beside_the_cpu_one():
    line = thermal_line(_ts())
    assert f"CPU emergency {_CPU_LIMIT:.0f}°C" in line
    assert f"coolant limit {_COOLANT_LIMIT:.0f}°C" in line
    assert f"release {_COOLANT_RELEASE:.0f}°C" in line


def test_readiness_line_omits_the_coolant_rung_on_an_older_daemon():
    line = thermal_line(_ts(coolant_limit_c=None, coolant_release_c=None))
    assert f"CPU emergency {_CPU_LIMIT:.0f}°C" in line, "precondition"
    assert "coolant" not in line


# ── 2. Startup-snapshot alarm bits are not a live alarm ────────────────────


def _sensor(value_c: float, thresholds: SensorThresholds) -> SensorReading:
    return SensorReading(
        id="hwmon:nct6798:0000:sys:SYSTIN",
        kind="mb_temp",
        label="SYSTIN",
        value_c=value_c,
        chip_name="nct6798",
        source="hwmon",
        age_ms=500,
        thresholds=thresholds,
    )


def _row(s: SensorReading):
    return ov.build_sensor_rows(
        [s], classify=lambda r: classify_reading(r, board_vendor="", overrides={})
    )[0]


def test_a_latched_crit_alarm_on_a_cool_sensor_is_not_an_alarm():
    cool = _sensor(40.0, SensorThresholds(crit_c=90.0, crit_alarm=True))
    assert not ov.is_alarm_active(cool)
    assert "ALARM" not in _row(cool).value_text


def test_a_live_reading_at_crit_is_still_an_alarm():
    hot = _sensor(90.0, SensorThresholds(crit_c=90.0, crit_alarm=False))
    assert ov.is_alarm_active(hot)
    assert "⚠ ALARM" in _row(hot).value_text


def test_the_detail_dialog_labels_every_alarm_bit_as_the_startup_snapshot():
    rows = dict(
        _threshold_rows(SensorThresholds(alarm=True, max_alarm=False, crit_alarm=True, fault=False))
    )
    assert rows == {
        "Alarm (at daemon start)": "asserted",
        "Max alarm (at daemon start)": "clear",
        "Crit alarm (at daemon start)": "asserted",
        "Fault (at daemon start)": "clear",
    }
