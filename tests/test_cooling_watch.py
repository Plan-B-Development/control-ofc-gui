"""DEC-443 (`W-SAFE`): the daemon's cooling-failure watch, from the GUI side.

The daemon publishes three things on ``/status`` — ``emergency_causes``,
``pump_stalls`` and ``advisories`` — and owns none of their wording. These tests
pin the parse (absent → empty, so an older daemon reads as quiet), the wording
maps against the wire vocabulary (DEC-257), the alerts the ledger raises, the
emergency reason that must not call a coolant emergency a CPU one, and the
header inspector's DC-pump note.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    ADVISORY_CODE_VALUES,
    EMERGENCY_CAUSE_VALUES,
    PUMP_STALL_STATE_VALUES,
    Capabilities,
    ControlCapability,
    CoolingAdvisory,
    FanReading,
    HwmonHeader,
    PumpStall,
    SensorReading,
    parse_status,
)
from control_ofc.services import cooling_watch
from control_ofc.services.app_state import AppState
from control_ofc.services.dashboard_view import _THERMAL_REASONS, safety_detail_text
from control_ofc.services.header_inspector_view import build_header_inspector_view

PUMP_ID = "hwmon:nct6798:nct6775.656:pwm2:PUMP"


# ── Parse ─────────────────────────────────────────────────────────────────


class TestParse:
    def test_the_three_fields_parse(self):
        st = parse_status(
            {
                "thermal_state": "emergency",
                "emergency_causes": ["cpu", "coolant"],
                "pump_stalls": [
                    {"header_id": PUMP_ID, "state": "held", "since_ms": 1200, "stall_count": 2}
                ],
                "advisories": [
                    {
                        "code": "cpu_at_ceiling_low_cooling",
                        "since_ms": 61000,
                        "cpu_temp_c": 95.5,
                        "ceiling_c": 95.0,
                        "max_duty_pct": 35,
                    }
                ],
            }
        )
        assert st.emergency_causes == ["cpu", "coolant"]
        assert st.pump_stalls == [
            PumpStall(header_id=PUMP_ID, state="held", since_ms=1200, stall_count=2)
        ]
        assert st.advisories[0].code == "cpu_at_ceiling_low_cooling"
        assert st.advisories[0].max_duty_pct == 35

    def test_an_older_daemon_reads_as_quiet(self):
        st = parse_status({"thermal_state": "emergency"})
        assert (st.emergency_causes, st.pump_stalls, st.advisories) == ([], [], [])

    @pytest.mark.parametrize("bad", ["coolant", {"a": 1}, 3, None])
    def test_a_malformed_field_reads_as_absent(self, bad):
        """A bare string would otherwise iterate as characters."""
        st = parse_status({"emergency_causes": bad, "pump_stalls": bad, "advisories": bad})
        assert (st.emergency_causes, st.pump_stalls, st.advisories) == ([], [], [])

    def test_the_capability_flag_parses(self):
        from control_ofc.api.models import parse_capabilities

        caps = parse_capabilities({"control": {"cooling_failure_detection": True}})
        assert caps.control.cooling_failure_detection is True
        assert parse_capabilities({}).control.cooling_failure_detection is False


# ── Wording maps against the wire vocabulary (DEC-257) ────────────────────


def test_every_wording_map_covers_the_wire_vocabulary():
    assert set(cooling_watch.PUMP_STALL_TEXT) == set(PUMP_STALL_STATE_VALUES)
    assert set(cooling_watch.ADVISORY_TITLES) == set(ADVISORY_CODE_VALUES)
    assert set(cooling_watch.EMERGENCY_CAUSE_PHRASES) == set(EMERGENCY_CAUSE_VALUES)
    assert set(cooling_watch.EMERGENCY_CAUSE_SUBJECTS) == set(EMERGENCY_CAUSE_VALUES)


class TestEmergencyResume:
    """What the Dashboard banner says ends an emergency (DEC-443)."""

    def test_each_cause_names_what_must_cool(self):
        subjects = cooling_watch.EMERGENCY_CAUSE_SUBJECTS
        assert cooling_watch.emergency_resume(["coolant"]) == f"once {subjects['coolant']} cools"
        assert subjects["cpu"] not in cooling_watch.emergency_resume(["coolant"])
        both = cooling_watch.emergency_resume(["cpu", "coolant"])
        assert subjects["cpu"] in both and subjects["coolant"] in both

    def test_an_older_daemon_reads_as_the_cpu(self):
        assert cooling_watch.emergency_resume([]) == cooling_watch.emergency_resume(["cpu"])

    def test_an_unknown_cause_does_not_name_a_known_one(self):
        text = cooling_watch.emergency_resume(["cpu", "future_cause"])
        assert cooling_watch.EMERGENCY_CAUSE_SUBJECTS["cpu"] not in text


def test_no_wording_restates_a_daemon_timing_or_limit():
    """The stall window, response window, advisory hold and coolant limit are
    daemon-side and not on the wire; a number in the copy would drift."""
    texts = [t for pair in cooling_watch.PUMP_STALL_TEXT.values() for t in pair]
    texts += list(cooling_watch.ADVISORY_TITLES.values())
    texts += list(cooling_watch.EMERGENCY_CAUSE_PHRASES.values())
    texts += list(cooling_watch.EMERGENCY_CAUSE_SUBJECTS.values())
    for text in texts:
        # "0 RPM" is the observation itself, not a daemon constant.
        assert not any(ch.isdigit() for ch in text.replace("0 RPM", "")), text


# ── Alerts ────────────────────────────────────────────────────────────────


class TestAlerts:
    @pytest.mark.parametrize("state", [*PUMP_STALL_STATE_VALUES, "some_future_state"])
    def test_every_pump_state_is_an_error_naming_the_pump(self, state):
        alert = cooling_watch.pump_stall_alert(
            "AIO Pump", PumpStall(header_id=PUMP_ID, state=state)
        )
        assert alert.level == "error"
        assert alert.key == f"pump_stall:{PUMP_ID}"
        assert "AIO Pump" in alert.title
        if state == "some_future_state":
            assert state in alert.title, "an unknown token must render, not vanish"

    def test_the_advisory_is_a_warning_that_says_it_forces_nothing(self):
        adv = CoolingAdvisory(
            code="cpu_at_ceiling_low_cooling", cpu_temp_c=95.5, ceiling_c=95.0, max_duty_pct=35
        )
        alert = cooling_watch.advisory_alert(adv)
        assert alert.level == "warning"
        assert "95 °C" in alert.detail and "95.5 °C" in alert.detail and "35%" in alert.detail
        assert "does not force" in alert.detail

    def test_an_unknown_advisory_code_renders(self):
        alert = cooling_watch.advisory_alert(CoolingAdvisory(code="future_code"))
        assert alert.level == "warning"
        assert "future_code" in alert.title

    def test_the_ledger_raises_and_clears_them_from_the_poll(self, qapp):
        """Through `AppState`, not the helpers: the status the poll delivers,
        then the sensors/fans setters that reconcile — the real order."""
        state = AppState()
        state.set_status(
            parse_status(
                {
                    "pump_stalls": [{"header_id": PUMP_ID, "state": "not_turning"}],
                    "advisories": [{"code": "cpu_at_ceiling_low_cooling"}],
                }
            )
        )
        state.set_sensors([SensorReading(id="cpu", kind="cpu_temp", value_c=95.0)])
        state.set_fans([FanReading(id="openfan:ch00", source="openfan", rpm=900)])
        present = {o.key: o.level for o in state.alerts.present()}
        assert present[f"pump_stall:{PUMP_ID}"] == "error"
        assert present["cooling_advisory:cpu_at_ceiling_low_cooling"] == "warning"

        state.set_status(parse_status({}))
        state.set_sensors([SensorReading(id="cpu", kind="cpu_temp", value_c=60.0)])
        active = {o.key for o in state.alerts.present() if o.recovered_at is None}
        assert f"pump_stall:{PUMP_ID}" not in active
        assert "cooling_advisory:cpu_at_ceiling_low_cooling" not in active

    def test_the_pump_is_named_by_its_display_name(self, qapp):
        state = AppState()
        state.apply_fan_rename(PUMP_ID, "Loop pump")
        state.set_status(parse_status({"pump_stalls": [{"header_id": PUMP_ID, "state": "held"}]}))
        state.set_fans([])
        titles = [o.title for o in state.alerts.present()]
        assert any("Loop pump" in t for t in titles), titles


# ── The emergency reason names its trigger ────────────────────────────────


class TestEmergencyReason:
    def _text(self, causes):
        return safety_detail_text(
            "emergency",
            "Thermal: Emergency",
            [],
            0,
            cpu_reading_is_stale=False,
            emergency_causes=causes,
        )

    def test_a_coolant_emergency_is_not_explained_as_a_cpu_one(self):
        text = self._text(["coolant"])
        assert cooling_watch.EMERGENCY_CAUSE_PHRASES["coolant"] in text
        assert cooling_watch.EMERGENCY_CAUSE_PHRASES["cpu"] not in text

    def test_both_causes_are_named(self):
        text = self._text(["cpu", "coolant"])
        assert cooling_watch.EMERGENCY_CAUSE_PHRASES["cpu"] in text
        assert "the coolant reached its configured limit" in text

    def test_an_older_daemon_keeps_the_cpu_wording(self):
        """No causes = a pre-3.0.0 daemon, whose only emergency is the CPU one."""
        assert _THERMAL_REASONS["emergency"] in self._text([])

    def test_an_unknown_cause_renders(self):
        assert "future_cause" in self._text(["future_cause"])

    def test_the_reach_is_stated_for_every_cause(self):
        for causes in ([], ["cpu"], ["coolant"], ["cpu", "coolant"]):
            assert "GPU fans are not included" in self._text(causes)


# ── Header inspector: the DC pump floor ───────────────────────────────────


def _pump(pwm_mode, floor):
    return HwmonHeader(
        id=PUMP_ID,
        label="PUMP",
        chip_name="nct6798",
        pwm_index=2,
        is_writable=True,
        role="pump",
        role_source="label",
        pwm_mode=pwm_mode,
        effective_min_pwm_pct=floor,
        stop_permitted=False,
    )


def _floor_row(header, **control):
    control.setdefault("header_roles", True)
    caps = Capabilities(control=ControlCapability(**control))
    view = build_header_inspector_view(header, capabilities=caps)
    return next(r for r in view.safety_rows if r.label == "Device safety floor")


class TestDcPumpFloor:
    def test_a_dc_pump_shows_the_daemons_floor_and_says_why(self):
        row = _floor_row(_pump(0, 70), cooling_failure_detection=True)
        assert row.value == "70%", "the daemon's number, not a GUI constant"
        assert "DC mode" in row.note

    def test_a_pwm_pump_gets_no_note(self):
        assert _floor_row(_pump(1, 30), cooling_failure_detection=True).note == ""

    def test_an_older_daemon_gets_no_note(self):
        """It floors every pump at 30 % whatever the mode — the note would
        explain a number it does not report."""
        assert _floor_row(_pump(0, 30), cooling_failure_detection=False).note == ""
