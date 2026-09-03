"""AIO-MB Phase 5 — validation session models, view-model and serializers.

Phase 5 ships no UI, so these tests are all headless by nature. What they guard
is the part that would otherwise be invisible until Phase 6 renders it wrongly:
that unknown tokens survive, that ``unavailable`` never reads as a failure, and
that absent telemetry never becomes a zero.
"""

from __future__ import annotations

import csv
import io
import json

from control_ofc.api.models import (
    VALIDATION_RESULT_FAIL,
    VALIDATION_RESULT_NOT_TESTED,
    VALIDATION_RESULT_OBSERVED,
    VALIDATION_RESULT_PASS,
    VALIDATION_RESULT_UNAVAILABLE,
    VALIDATION_STATE_INTERRUPTED,
    VALIDATION_STATE_RECORDING,
    ValidationSession,
    parse_capabilities,
    parse_fans,
    parse_status,
    parse_validation_session,
    parse_validation_session_index,
    parse_validation_session_summary,
)
from control_ofc.services import validation_export as vexport
from control_ofc.services import validation_view as vview

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PUMP = "hwmon:it87:pwm2:PUMP"
RAD1 = "hwmon:it87:pwm3:RAD1"
RAD2 = "hwmon:it87:pwm4:RAD2"


def _member(member_id: str, kind: str) -> dict:
    return {
        "member_id": member_id,
        "label": f"{member_id} label",
        "role": "pump" if kind == "pump" else "radiator_fan",
        "member_kind": kind,
        "pump_protected": kind == "pump",
        "effective_min_pwm_pct": 30 if kind == "pump" else 20,
        "stop_permitted": kind != "pump",
        "writable": True,
    }


def _sample(elapsed: int, members: list[dict]) -> dict:
    return {
        "elapsed_ms": elapsed,
        "unix_ms": 1000 + elapsed,
        "temperature_c": 45.5,
        "temperature_sensor": "cpu:package",
        "coolant_c": None,
        "thermal_state": "normal",
        "members": members,
    }


def _member_sample(
    member_id: str,
    role: str,
    requested: int | None = 50,
    readback: int | None = 50,
    rpm: int | None = 1200,
) -> dict:
    return {
        "member_id": member_id,
        "role": role,
        "requested_pct": requested,
        "readback_pct": readback,
        "rpm": rpm,
        "pwm_enable_mode": 1,
        "alarm": False,
        "enable_revert_count": 0,
        "ownership": "daemon",
    }


def _session_payload(**overrides) -> dict:
    payload = {
        "session_id": "val-1000-0",
        "kind": "validation",
        "state": "completed",
        "started_unix_ms": 1000,
        "completed_unix_ms": 9000,
        "requested_diagnostics": ["pwm_characterization"],
        "sweep_members": [PUMP],
        "metadata": {
            "cooling_device_id": "dev-1",
            "device_name": "Test AIO",
            "device_kind": "aio_liquid",
            "pump_member": PUMP,
            "radiator_members": [RAD1, RAD2],
            "auxiliary_members": [],
            "temperature_sensor": "cpu:package",
            "coolant_sensor": None,
            "coolant_telemetry": "unavailable",
            "device_policy": {
                "id": "generic_pump",
                "display_name": "Generic pump",
                "minimum_safe_pwm_pct": 30.0,
                "supports_stop": False,
                "internal_control_possible": True,
            },
            "members": [
                _member(PUMP, "pump"),
                _member(RAD1, "radiator"),
                _member(RAD2, "radiator"),
            ],
            "active_profile_id": "p1",
            "active_profile_name": "Balanced",
            "daemon_version": "2.32.0",
            "user_metadata": {"pump_mode": "Balanced"},
        },
        "samples": [
            _sample(
                0,
                [
                    _member_sample(PUMP, "pump", 40, 41, 2400),
                    _member_sample(RAD1, "radiator", 40, 40, 800),
                    _member_sample(RAD2, "radiator", 40, 40, 1000),
                ],
            )
        ],
        "events": [
            {"elapsed_ms": 0, "unix_ms": 1000, "kind": "session_started"},
            {"elapsed_ms": 500, "unix_ms": 1500, "kind": "user_marker", "detail": "pump to Quiet"},
        ],
        "evidence": [],
        "external_measurements": [],
        "findings": [
            {"id": "pwm_header_control", "state": "pass"},
            {"id": "pump_rpm_telemetry", "state": "pass", "member_id": PUMP},
        ],
        "sample_limit_reached": False,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_session_parses_with_nested_metadata_members_and_samples():
    s = parse_validation_session(_session_payload())

    assert s.session_id == "val-1000-0"
    assert s.metadata.device_name == "Test AIO"
    assert s.metadata.device_policy.minimum_safe_pwm_pct == 30.0
    assert [m.member_id for m in s.metadata.members] == [PUMP, RAD1, RAD2]
    assert s.metadata.user_metadata == {"pump_mode": "Balanced"}
    assert len(s.samples) == 1
    assert [m.member_id for m in s.samples[0].members] == [PUMP, RAD1, RAD2]
    assert s.samples[0].members[0].requested_pct == 40
    assert s.samples[0].members[0].readback_pct == 41
    assert len(s.events) == 2
    assert len(s.findings) == 2


def test_unknown_wire_keys_do_not_break_parsing():
    """A newer daemon may add fields; an older GUI must keep working."""
    payload = _session_payload()
    payload["a_field_from_the_future"] = {"nested": True}
    payload["metadata"]["also_new"] = 1
    payload["samples"][0]["members"][0]["future_axis"] = 99

    s = parse_validation_session(payload)
    assert s.session_id == "val-1000-0"
    assert s.samples[0].members[0].member_id == PUMP


def test_a_pre_phase5_daemon_reports_no_session_rather_than_an_error():
    assert parse_validation_session_summary({}) is None
    assert parse_status({}).validation_session is None
    # And the capability defaults off, so nothing calls the routes.
    assert parse_capabilities({}).control.validation_sessions is False


def test_capability_flag_is_read_from_the_wire():
    caps = parse_capabilities({"control": {"validation_sessions": True}})
    assert caps.control.validation_sessions is True


def test_session_summary_rides_the_poll():
    st = parse_status(
        {
            "validation_session": {
                "session_id": "val-9",
                "state": VALIDATION_STATE_RECORDING,
                "elapsed_ms": 61000,
                "sample_count": 61,
                "event_count": 3,
                "cooling_device_id": "dev-1",
            }
        }
    )
    assert st.validation_session is not None
    assert st.validation_session.is_recording is True
    assert st.validation_session.sample_count == 61


def test_session_index_parses_newest_first_as_sent():
    entries = parse_validation_session_index(
        {"sessions": [{"session_id": "b", "started_unix_ms": 20}, {"session_id": "a"}]}
    )
    assert [e.session_id for e in entries] == ["b", "a"]


def test_characterisation_evidence_is_parsed_by_the_phase3_parser():
    """§6: the run is the daemon's verbatim, and parsing it a second way here is
    exactly how the two representations would drift."""
    payload = _session_payload(
        evidence=[
            {
                "kind": "pwm_characterization",
                "member_id": PUMP,
                "run_id": "char-1",
                "started_unix_ms": 1000,
                "completed_unix_ms": 5000,
                "outcome": "observed",
                "characterization": {
                    "run_id": "char-1",
                    "header_id": PUMP,
                    "state": "complete",
                    "summary": {
                        "command_acceptance": "pass",
                        "pwm_readback": "pass",
                        "rpm_response": "fail",
                        "possible_device_override": True,
                    },
                    "points": [],
                },
            }
        ]
    )
    s = parse_validation_session(payload)
    ev = s.evidence[0]
    assert ev.characterization is not None
    assert ev.characterization.run_id == "char-1"
    assert ev.characterization.summary.possible_device_override is True


def test_verify_evidence_parses():
    payload = _session_payload(
        evidence=[
            {
                "kind": "pwm_verify",
                "member_id": PUMP,
                "started_unix_ms": 1000,
                "outcome": "observed",
                "verify": {
                    "header_id": PUMP,
                    "write_ok": True,
                    "readback_pct": 60,
                    "requested_pct": 60,
                    "rpm_before": 1200,
                    "rpm_after": 2000,
                },
            }
        ]
    )
    s = parse_validation_session(payload)
    assert s.evidence[0].verify is not None
    assert s.evidence[0].verify.write_ok is True
    assert s.evidence[0].verify.rpm_after == 2000


# ---------------------------------------------------------------------------
# The readback/commanded split
# ---------------------------------------------------------------------------


def test_fan_reading_carries_readback_separately_from_the_command():
    """The two axes are separate on the wire, and conflating them is the defect
    the field exists to prevent."""
    fans = parse_fans(
        {
            "fans": [
                {
                    "id": PUMP,
                    "source": "hwmon",
                    "rpm": 2400,
                    "last_commanded_pwm": 40,
                    "pwm_readback_pct": 38,
                }
            ]
        }
    )
    assert fans[0].last_commanded_pwm == 40
    assert fans[0].pwm_readback_pct == 38


def test_absent_readback_is_none_never_zero():
    """A pre-2.32 daemon, an OpenFan channel and a GPU fan all omit it. `0`
    would claim the header is sitting at 0% duty."""
    fans = parse_fans({"fans": [{"id": "openfan:0", "source": "openfan", "rpm": 900}]})
    assert fans[0].pwm_readback_pct is None


# ---------------------------------------------------------------------------
# View-model — token tolerance and tone
# ---------------------------------------------------------------------------


def test_an_unrecognised_finding_id_is_rendered_not_dropped():
    """273-i: a newer daemon adding a finding must not make it vanish."""
    s = parse_validation_session(
        _session_payload(findings=[{"id": "some_future_check", "state": "pass"}])
    )
    view = vview.build_validation_session_view(s)
    assert len(view.findings) == 1
    assert view.findings[0].label == "Some future check"


def test_an_unrecognised_result_state_renders_muted_never_bad():
    s = parse_validation_session(
        _session_payload(findings=[{"id": "pwm_readback", "state": "quantum_superposition"}])
    )
    row = vview.build_validation_session_view(s).findings[0]
    assert row.state_label == "Quantum superposition"
    assert row.tone == "muted", "an unknown token must not paint a red row"


def test_unavailable_and_not_tested_are_never_styled_as_failures():
    """§7's rule, applied to presentation: absent capability is not a fault and
    an untested item is not a failed one."""
    assert vview.result_tone(VALIDATION_RESULT_UNAVAILABLE) == "muted"
    assert vview.result_tone(VALIDATION_RESULT_NOT_TESTED) == "muted"
    assert vview.result_tone(VALIDATION_RESULT_FAIL) == "bad"
    # And the discriminating half: the tones really are different, so the
    # assertions above are not passing because everything is muted.
    assert vview.result_tone(VALIDATION_RESULT_PASS) == "ok"
    assert vview.result_tone(VALIDATION_RESULT_OBSERVED) == "info"


def test_a_device_side_override_reads_as_evidence_not_a_fault():
    s = parse_validation_session(
        _session_payload(
            findings=[
                {"id": "possible_device_override", "state": "observed"},
                {"id": "pwm_header_control", "state": "pass"},
            ]
        )
    )
    view = vview.build_validation_session_view(s)
    override = next(f for f in view.findings if f.finding_id == "possible_device_override")
    control = next(f for f in view.findings if f.finding_id == "pwm_header_control")

    assert override.tone == "info"
    assert override.tone != "bad", "a possible device override is not a failure"
    # The working half of the same run is not dragged down with it.
    assert control.tone == "ok"


def test_two_radiators_keep_two_rows_and_are_never_averaged():
    s = parse_validation_session(_session_payload())
    view = vview.build_validation_session_view(s)

    rads = [m for m in view.members if m.member_id in (RAD1, RAD2)]
    assert len(rads) == 2
    # 800 and 1000 stay distinct — an averaging bug would show 900 on both.
    rpm_texts = {m.member_id: m.rpm_range for m in rads}
    assert "800" in rpm_texts[RAD1]
    assert "1000" in rpm_texts[RAD2]
    assert rpm_texts[RAD1] != rpm_texts[RAD2]


def test_a_member_with_no_tach_reports_unavailable_not_zero_rpm():
    payload = _session_payload()
    payload["samples"] = [
        _sample(
            0,
            [
                _member_sample(PUMP, "pump", 40, 40, None),
                _member_sample(RAD1, "radiator", 40, 40, 800),
                _member_sample(RAD2, "radiator", 40, 40, 900),
            ],
        )
    ]
    view = vview.build_validation_session_view(parse_validation_session(payload))
    pump = next(m for m in view.members if m.member_id == PUMP)

    assert pump.rpm_available is False
    assert pump.rpm_range == vview.UNKNOWN_TEXT
    assert "0" not in pump.rpm_range, "absent telemetry must not render as zero"
    # The radiator that does report proves the check discriminates.
    rad = next(m for m in view.members if m.member_id == RAD1)
    assert rad.rpm_available is True


def test_a_member_that_reported_nothing_still_gets_a_row():
    """Its absence would read as 'not part of this cooler' rather than 'this
    cooler told us nothing about it'."""
    payload = _session_payload()
    payload["samples"] = [_sample(0, [_member_sample(PUMP, "pump")])]
    view = vview.build_validation_session_view(parse_validation_session(payload))

    assert {m.member_id for m in view.members} == {PUMP, RAD1, RAD2}
    silent = next(m for m in view.members if m.member_id == RAD2)
    assert silent.samples == 0
    assert silent.rpm_range == vview.UNKNOWN_TEXT


def test_a_passive_session_says_so_rather_than_leaving_a_blank():
    s = parse_validation_session(_session_payload(requested_diagnostics=[]))
    view = vview.build_validation_session_view(s)
    assert "no diagnostics" in view.diagnostics_note.lower()


def test_an_interrupted_session_explains_itself_without_implying_data_loss_is_hidden():
    s = parse_validation_session(
        _session_payload(
            state=VALIDATION_STATE_INTERRUPTED,
            interrupted_reason="daemon_restart",
            truncated_at_unix_ms=5000,
        )
    )
    view = vview.build_validation_session_view(s)

    assert view.state_tone == "warn"
    assert "Daemon restart" in view.interrupted_note
    assert "preserved" in view.interrupted_note
    assert "nothing after it was recorded" in view.interrupted_note


def test_display_name_override_is_used_for_member_labels():
    s = parse_validation_session(_session_payload())
    view = vview.build_validation_session_view(s, display_name=lambda mid: f"Nice {mid[-4:]}")
    assert view.members[0].label.startswith("Nice ")


def test_format_elapsed_covers_hours_minutes_and_seconds():
    assert vview.format_elapsed(0) == "0s"
    assert vview.format_elapsed(12_000) == "12s"
    assert vview.format_elapsed(252_000) == "4m 12s"
    assert vview.format_elapsed(3_852_000) == "1h 04m 12s"
    assert vview.format_elapsed(-1) == vview.UNKNOWN_TEXT


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_csv_has_one_row_per_member_per_sample_preserving_identity():
    s = parse_validation_session(_session_payload())
    text = vexport.session_samples_csv(s)
    rows = list(csv.DictReader(io.StringIO(text)))

    assert len(rows) == 3, "three members in one sample means three rows"
    assert [r["member_id"] for r in rows] == [PUMP, RAD1, RAD2]
    # The two axes stay separate columns.
    pump = rows[0]
    assert pump["requested_pct"] == "40"
    assert pump["readback_pct"] == "41"
    assert pump["rpm"] == "2400"
    # Sample-level context repeats on each member row so the file is analysable
    # without a join.
    assert all(r["thermal_state"] == "normal" for r in rows)


def test_csv_writes_an_empty_cell_for_absent_telemetry_never_zero():
    payload = _session_payload()
    payload["samples"] = [_sample(0, [_member_sample(PUMP, "pump", None, None, None)])]
    rows = list(
        csv.DictReader(io.StringIO(vexport.session_samples_csv(parse_validation_session(payload))))
    )

    assert rows[0]["rpm"] == ""
    assert rows[0]["requested_pct"] == ""
    assert rows[0]["readback_pct"] == ""
    assert rows[0]["rpm"] != "0", "absent must not be indistinguishable from zero"


def test_csv_columns_are_derived_from_the_model_not_hardcoded():
    """A field added to the sample model appears automatically — a hand-kept
    list is how an export quietly stops carrying a column."""
    from dataclasses import fields as dc_fields

    from control_ofc.api.models import ValidationMemberSample

    header = vexport.sample_csv_header()
    for f in dc_fields(ValidationMemberSample):
        assert f.name in header, f"member field {f.name} is missing from the CSV"


def test_csv_uses_rfc4180_line_endings():
    s = parse_validation_session(_session_payload())
    assert vexport.session_samples_csv(s).endswith("\r\n")


def test_events_and_findings_csv_carry_tokens_not_ui_wording():
    s = parse_validation_session(_session_payload())

    findings = list(csv.DictReader(io.StringIO(vexport.session_findings_csv(s))))
    assert findings[0]["finding_id"] == "pwm_header_control"
    assert findings[0]["state"] == "pass"
    # Not the human label — a file consumed by a tool must carry stable values.
    assert "PWM header control" not in vexport.session_findings_csv(s)

    events = list(csv.DictReader(io.StringIO(vexport.session_events_csv(s))))
    assert events[0]["kind"] == "session_started"
    assert events[1]["detail"] == "pump to Quiet"


def test_json_export_round_trips_the_session_the_gui_understood():
    s = parse_validation_session(_session_payload())
    doc = json.loads(vexport.session_json(s))

    assert doc["session_id"] == "val-1000-0"
    assert doc["metadata"]["device_name"] == "Test AIO"
    assert len(doc["samples"][0]["members"]) == 3
    assert doc["samples"][0]["members"][0]["readback_pct"] == 41
    assert doc["findings"][0]["id"] == "pwm_header_control"

    # And it re-parses, so the export is a valid input to the same model.
    again = parse_validation_session(doc)
    assert again.session_id == s.session_id
    assert len(again.samples[0].members) == 3


def test_json_export_carries_characterisation_evidence_verbatim():
    payload = _session_payload(
        evidence=[
            {
                "kind": "pwm_characterization",
                "member_id": PUMP,
                "run_id": "char-1",
                "started_unix_ms": 1000,
                "outcome": "observed",
                "characterization": {
                    "run_id": "char-1",
                    "header_id": PUMP,
                    "state": "complete",
                    "summary": {"possible_device_override": True, "rpm_response": "fail"},
                    "points": [],
                },
            }
        ]
    )
    doc = json.loads(vexport.session_json(parse_validation_session(payload)))
    ev = doc["evidence"][0]
    assert ev["run_id"] == "char-1"
    assert ev["characterization"]["summary"]["possible_device_override"] is True


def test_an_empty_session_serializes_without_raising():
    s = ValidationSession()
    assert vexport.session_samples_csv(s).startswith("elapsed_ms")
    assert vexport.session_events_csv(s).startswith("elapsed_ms")
    assert vexport.session_findings_csv(s).startswith("finding_id")
    assert json.loads(vexport.session_json(s))["session_id"] == ""


def test_the_export_carries_no_host_or_user_identity():
    """§3: do not collect unrelated system data. The GUI must not add any either."""
    s = parse_validation_session(_session_payload())
    doc = json.loads(vexport.session_json(s))

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)

    found = set(keys(doc))
    for forbidden in {"hostname", "username", "uid", "gid", "home", "kernel_version", "cmdline"}:
        assert forbidden not in found, f"export must not carry a '{forbidden}' key"

    text = vexport.session_json(s)
    for path in ("/home/", "/sys/", "/proc/"):
        assert path not in text


# ---------------------------------------------------------------------------
# Client contract
# ---------------------------------------------------------------------------


class TestClientContract:
    def test_start_sends_only_what_the_caller_supplied(self, monkeypatch):
        """No client-side clamp, no client-side default. The daemon owns the
        pump floor, the thermal refusal and the sweep-member default; a copy of
        any of them here would be a second definition that then drifts."""
        from control_ofc.api.client import DaemonClient

        sent: dict = {}

        def fake_post(self, path, json=None, *, params=None, timeout=None):
            sent["path"] = path
            sent["json"] = json
            return _session_payload()

        monkeypatch.setattr(DaemonClient, "_post", fake_post)
        client = DaemonClient.__new__(DaemonClient)

        client.start_validation_session("dev-1")
        assert sent["path"] == "/validation/session"
        assert sent["json"] == {"cooling_device_id": "dev-1"}, (
            "an omitted sweep list must reach the daemon as omitted, so the "
            "daemon's pump-member default applies rather than a GUI guess"
        )

        client.start_validation_session(
            "dev-1",
            kind="lifecycle",
            diagnostics=["pwm_verify"],
            sweep_members=[PUMP],
            metadata={"pump_mode": "Quiet"},
        )
        assert sent["json"] == {
            "cooling_device_id": "dev-1",
            "kind": "lifecycle",
            "diagnostics": ["pwm_verify"],
            "sweep_members": [PUMP],
            "metadata": {"pump_mode": "Quiet"},
        }

    def test_session_returns_none_on_404_and_reraises_everything_else(self, monkeypatch):
        import pytest

        from control_ofc.api.client import DaemonClient
        from control_ofc.api.errors import DaemonError

        def not_found(self, path, *, params=None, timeout=None):
            raise DaemonError(
                code="not_found",
                message="none yet",
                retryable=False,
                source="validation",
                status=404,
                details=None,
                endpoint=path,
            )

        monkeypatch.setattr(DaemonClient, "_get", not_found)
        client = DaemonClient.__new__(DaemonClient)
        assert client.validation_session() is None
        assert client.validation_session_by_id("val-1") is None

        def boom(self, path, *, params=None, timeout=None):
            raise DaemonError(
                code="internal_error",
                message="boom",
                retryable=False,
                source="internal",
                status=500,
                details=None,
                endpoint=path,
            )

        monkeypatch.setattr(DaemonClient, "_get", boom)
        with pytest.raises(DaemonError):
            client.validation_session()

    def test_lifecycle_methods_target_the_documented_routes(self, monkeypatch):
        from control_ofc.api.client import DaemonClient

        seen: dict = {}

        def fake_post(self, path, json=None, *, params=None, timeout=None):
            seen["post"] = (path, json)
            return _session_payload()

        def fake_delete(self, path, *, params=None, timeout=None):
            seen["delete"] = path
            return _session_payload()

        def fake_get(self, path, *, params=None, timeout=None):
            seen["get"] = path
            return {"sessions": [{"session_id": "val-1"}]}

        monkeypatch.setattr(DaemonClient, "_post", fake_post)
        monkeypatch.setattr(DaemonClient, "_delete", fake_delete)
        monkeypatch.setattr(DaemonClient, "_get", fake_get)
        client = DaemonClient.__new__(DaemonClient)

        client.stop_validation_session()
        assert seen["post"][0] == "/validation/session/stop"

        client.cancel_validation_session()
        assert seen["delete"] == "/validation/session"

        client.add_validation_marker("pump to Quiet")
        assert seen["post"] == ("/validation/session/event", {"detail": "pump to Quiet"})

        client.add_validation_measurement("supply_voltage_v", 11.94, unit="V")
        assert seen["post"] == (
            "/validation/session/measurement",
            {"kind": "supply_voltage_v", "value": 11.94, "unit": "V"},
        )

        entries = client.validation_sessions()
        assert seen["get"] == "/validation/sessions"
        assert entries[0].session_id == "val-1"
