"""PWM Test Report: the store, the trace, the catalogue, "Your setup" facts, the
shared duration estimates and the in-app view model (DEC-404 Stage 4)."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from control_ofc.api.models import parse_capabilities
from control_ofc.services import diagnostic_estimates as est
from control_ofc.services.app_settings_service import (
    _DEMO_SEALED_KEYS,
    MACHINE_SPECIFIC_KEYS,
    AppSettings,
)
from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report import setup_facts as sf
from control_ofc.services.pwm_report.catalog import (
    SPECS,
    TEST_PAIRING,
    TEST_PROBE,
    TEST_SWEEP,
    TEST_VERIFY,
    Channel,
    availability,
    default_selection,
    estimate_seconds,
    settled_evidence_supported,
)
from control_ofc.services.pwm_report.store import (
    REPORT_MAX_BYTES,
    load_report,
    recover_cut_off_reports,
    report_path,
    save_report,
    serialise,
)
from control_ofc.services.pwm_report.trace import MAX_SAMPLES, TraceRecorder
from control_ofc.services.pwm_report.view import (
    build_report_view,
    evidence_text,
    probe_curve,
    result_label,
)
from tests.pwm_report_fixtures import (
    CPU,
    SYS,
    bundle,
    channel,
    fan,
    header,
    new_doc,
    probe_run,
    sweep_run,
)

# ── Capabilities from the WIRE, so a gate is asserted against the field ──────


def _caps(version: str = "2.54.0", **flags: bool):
    control = {
        "control_path_discovery": True,
        "pwm_behaviour_characterization": True,
        "stall_probe": True,
        "duty_reconciliation": True,
        "header_roles": True,
        **flags,
    }
    return parse_capabilities({"daemon_version": version, "api_version": 1, "control": control})


def _ch(**kw) -> Channel:
    base = {
        "channel_id": SYS,
        "source": "hwmon",
        "name": "SYS_FAN1",
        "role": "chassis_fan",
        "writable": True,
        "rpm_available": True,
        "rpm": 800,
    }
    base.update(kw)
    return Channel(**base)


# ── Catalogue ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("source", ["openfan", "amd_gpu"])
def test_non_motherboard_channels_are_read_only(source):
    for test in SPECS:
        a = availability(_ch(source=source), test, _caps())
        assert not a.available and "read-only" in a.reason


def test_a_read_only_header_offers_nothing():
    for test in SPECS:
        assert not availability(_ch(writable=False), test, _caps()).available


@pytest.mark.parametrize(
    ("test", "flag"),
    [
        (TEST_PAIRING, "control_path_discovery"),
        (TEST_SWEEP, "pwm_behaviour_characterization"),
        (TEST_PROBE, "stall_probe"),
    ],
)
def test_each_capability_gate_follows_its_wire_field(test, flag):
    assert availability(_ch(), test, _caps(**{flag: True})).available
    off = availability(_ch(), test, _caps(**{flag: False}))
    assert not off.available and "requires control-ofc-daemon" in off.reason


def test_the_settled_evidence_gate_is_the_daemon_version_s4_5():
    old, new = _caps("2.51.3"), _caps("2.52.0")
    assert not settled_evidence_supported(old) and settled_evidence_supported(new)
    for test in (TEST_SWEEP, TEST_PAIRING):
        a = availability(_ch(), test, old)
        assert not a.available and "2.52.0" in a.reason
        assert availability(_ch(), test, new).available
    # Verify never needed it.
    assert availability(_ch(), TEST_VERIFY, old).available
    # An unparseable version fails closed.
    assert not settled_evidence_supported(SimpleNamespace(daemon_version="dev"))


@pytest.mark.parametrize(
    ("kw", "needle"),
    [
        ({"pump_protected": True}, "pump-protected"),
        ({"role": "cpu_fan"}, "CPU fan"),
        ({"role": "unknown"}, "chassis-fan or radiator-fan role"),
        ({"rpm_available": False}, "no tach"),
    ],
)
def test_probe_eligibility_offers_only_the_daemons_envelope(kw, needle):
    a = availability(_ch(**kw), TEST_PROBE, _caps())
    assert not a.available and needle in a.reason
    assert availability(_ch(role="radiator_fan"), TEST_PROBE, _caps()).available


def test_default_selection_is_verify_and_pairing_on_detected_fans_only():
    chans = [
        _ch(channel_id=CPU, rpm=900),
        _ch(channel_id=SYS, rpm=0),  # no fan detected: selectable, not pre-selected
        _ch(channel_id="openfan:ch00", source="openfan"),
    ]
    sel = default_selection(chans, _caps())
    assert sel == {CPU: frozenset({TEST_VERIFY, TEST_PAIRING})}
    # Never the sweep or the probe, whatever the daemon supports.
    assert not any({TEST_SWEEP, TEST_PROBE} & tests for tests in sel.values())
    # And pairing drops out below the evidence fix.
    assert default_selection(chans, _caps("2.51.3")) == {CPU: frozenset({TEST_VERIFY})}


def test_estimates_sum_the_shared_figures():
    typical, worst = estimate_seconds({CPU: {TEST_VERIFY, TEST_SWEEP}})
    assert typical == est.VERIFY_SECONDS + est.characterization_seconds(
        9, bidirectional=True, stability=True
    )
    assert worst >= typical


# ── Shared estimates (the report's side; the rest is tests/test_diagnostic_estimates.py) ──


def test_report_labels_come_from_the_same_arithmetic():
    assert est.duration_text(SPECS[TEST_SWEEP].typical_s) == "~4½ min"
    assert est.duration_text(SPECS[TEST_PROBE].worst_s) == "~3½ min"


# ── "Your setup" facts and their settings keys ──────────────────────────────


def test_header_facts_are_coerced_and_blank_entries_dropped():
    raw = {
        CPU: {"connected": "fan", "fans_behind": 3, "bios_mode": "pwm", "notes": "  x  "},
        SYS: {"connected": "made-up", "fans_behind": 99, "bios_mode": 4, "notes": 5},
        "": {"connected": "fan"},
        "hwmon:bad": "not a dict",
    }
    out = sf.coerce_hardware_notes(raw)
    assert out == {CPU: {"connected": "fan", "fans_behind": 3, "bios_mode": "pwm", "notes": "x"}}
    assert sf.coerce_hardware_notes("nope") == {}
    long = sf.coerce_hardware_notes({CPU: {"notes": "n" * 5000}})
    assert len(long[CPU]["notes"]) == sf.NOTES_MAX


def test_cooler_facts_are_coerced():
    assert sf.coerce_cooler_notes({"model": "NL-LC1-36", "pump_switch": "manual"}) == {
        "model": "NL-LC1-36",
        "pump_switch": "manual",
    }
    assert sf.coerce_cooler_notes({"model": "", "pump_switch": ""}) == {}


def test_blank_reads_not_supplied_in_the_report():
    assert sf.connected_label("") == sf.NOT_SUPPLIED
    assert sf.bios_mode_label("") == sf.NOT_SUPPLIED
    assert sf.connected_label("pump_and_fans") == "Pump + fans"


def test_the_facts_are_machine_specific_and_demo_sealed():
    for key in ("hardware_notes", "cooler_notes"):
        assert key in MACHINE_SPECIFIC_KEYS
        assert key in _DEMO_SEALED_KEYS
    s = AppSettings.from_dict(
        {"hardware_notes": {CPU: {"fans_behind": 2}}, "cooler_notes": {"model": "X"}}
    )
    assert s.hardware_notes == {
        CPU: {"connected": "", "fans_behind": 2, "bios_mode": "", "notes": ""}
    }
    assert "hardware_notes" not in s.portable_dict()
    assert "cooler_notes" not in s.portable_dict()


# ── Trace ───────────────────────────────────────────────────────────────────


def _fan_obj(fid: str, rpm: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=fid, rpm=rpm, pwm_readback_pct=40, pwm_commanded_pct=40, pwm_enable_mode=1
    )


def test_the_trace_is_column_wise_and_backfills_late_channels():
    rec = TraceRecorder()
    rec.add_sample(0, thermal_state="normal", fans=[_fan_obj(CPU, 900)], sensors=[])
    rec.add_sample(
        1000,
        thermal_state="normal",
        fans=[_fan_obj(CPU, 910), _fan_obj(SYS, 700)],
        sensors=[SimpleNamespace(id="cpu", value_c=45.06)],
    )
    t = rec.to_dict()
    assert t["t_ms"] == [0, 1000]
    assert t["fans"][CPU]["rpm"] == [900, 910]
    assert t["fans"][SYS]["rpm"] == [None, 700], "a late channel is back-filled with None"
    assert t["temps_c"]["cpu"] == [None, 45.1]


def test_a_non_finite_reading_is_not_reported_not_nan():
    rec = TraceRecorder()
    rec.add_sample(
        0, thermal_state=None, fans=[], sensors=[SimpleNamespace(id="x", value_c=math.nan)]
    )
    assert rec.to_dict()["temps_c"]["x"] == [None]
    json.dumps(rec.to_dict(), allow_nan=False)  # must not raise


def test_the_trace_stops_at_its_cap_and_says_when():
    rec = TraceRecorder(max_samples=2)
    for i in range(4):
        rec.add_sample(i * 1000, thermal_state="normal", fans=[], sensors=[])
    t = rec.to_dict()
    assert t["samples"] == 2 and t["truncated"] is True and t["truncated_at_ms"] == 2000
    assert MAX_SAMPLES == 3 * 60 * 60  # S4-8


# ── Store ───────────────────────────────────────────────────────────────────


def _finished_doc() -> dict:
    doc = new_doc([channel(CPU)])
    doc["state"] = d.STATE_COMPLETE
    return doc


def test_save_and_load_round_trip(tmp_path):
    doc = _finished_doc()
    path = save_report(doc, tmp_path)
    assert path == report_path(doc["report_id"], tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    loaded, repaired = load_report(path)
    assert loaded == doc and repaired is False


def test_a_report_left_in_progress_is_repaired_as_interrupted(tmp_path):
    doc = new_doc([channel(CPU), channel(SYS)])
    doc["steps"] = [
        {"step_id": "s1", "channel_id": CPU, "test": "sweep", "status": d.STEP_RUNNING},
        {"step_id": "s2", "channel_id": SYS, "test": "verify", "status": d.STEP_PENDING},
    ]
    path = save_report(doc, tmp_path)
    loaded, repaired = load_report(path)
    assert repaired and loaded["state"] == d.STATE_INTERRUPTED
    assert [s["status"] for s in loaded["steps"]] == [d.STEP_INTERRUPTED, d.STEP_NOT_TESTED]
    assert loaded["findings"], "a repaired report has findings like any other"


def test_recovery_repairs_only_cut_off_reports(tmp_path):
    finished = _finished_doc()
    save_report(finished, tmp_path)
    cut = new_doc([channel(SYS)])
    cut["report_id"] = "20260922T010000Z-000001"
    cut_path = save_report(cut, tmp_path)
    (tmp_path / "pwm-report-garbage.json").write_text("{not json")
    assert recover_cut_off_reports(tmp_path) == [cut_path]
    assert json.loads(cut_path.read_text())["state"] == d.STATE_INTERRUPTED
    assert recover_cut_off_reports(tmp_path) == []  # idempotent


def test_the_in_progress_marker_is_where_the_cheap_check_reads_it():
    head = serialise(new_doc([channel(CPU)]))[:512]
    assert '"state":"in_progress"' in head


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda doc: doc.update(kind="something-else"), "not a Control-OFC"),
        (lambda doc: doc.update(schema_version=99), "newer Control-OFC"),
        (lambda doc: doc.update(state="exploded"), "unknown report state"),
        (lambda doc: doc.update(steps="nope"), "'steps'"),
    ],
)
def test_a_foreign_or_newer_file_is_rejected(tmp_path, mutate, needle):
    doc = _finished_doc()
    mutate(doc)
    path = tmp_path / "pwm-report-x.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(d.ReportSchemaError, match=needle):
        load_report(path)


def test_an_oversized_file_is_refused_before_it_is_parsed(tmp_path):
    path = tmp_path / "pwm-report-big.json"
    path.write_bytes(b" " * (REPORT_MAX_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds"):
        load_report(path)


def test_a_non_finite_value_can_never_be_saved(tmp_path):
    doc = _finished_doc()
    doc["plan"]["oops"] = math.inf
    with pytest.raises(ValueError):
        save_report(doc, tmp_path)


def test_a_three_hour_report_fits_its_limit_measured_on_disk(tmp_path):
    """A7 / DEC-320: assert the REALISED file, never a model of it.

    A worst-plausible run: eight headers with every test, a three-hour trace
    of 19 fans and 24 sensors (the development machine's poll), noisy RPM and
    temperatures so nothing compresses by accident.
    """
    import random

    rng = random.Random(404)
    headers_ = [f"hwmon:it8696:it87.2624:pwm{i}:FAN{i}" for i in range(1, 9)]
    doc = new_doc([channel(h) for h in headers_])
    doc["snapshots"]["baseline"] = bundle(
        fans=[fan(h) for h in headers_], headers=[header(h) for h in headers_]
    )
    doc["snapshots"]["final"] = doc["snapshots"]["baseline"]
    steps = []
    for i, h in enumerate(headers_):
        steps.append({"step_id": f"a{i}", "channel_id": h, "test": "sweep", "result": sweep_run()})
        steps.append({"step_id": f"b{i}", "channel_id": h, "test": "probe", "result": probe_run()})
    doc["steps"] = steps
    rec = TraceRecorder()
    fans_ = [f"fan{i}" for i in range(19)]
    sensors_ = [f"sensor{i}" for i in range(24)]
    for t in range(MAX_SAMPLES):
        rec.add_sample(
            t * 1000,
            thermal_state="normal",
            fans=[
                SimpleNamespace(
                    id=f,
                    rpm=rng.randint(600, 2400),
                    pwm_readback_pct=rng.randint(20, 100),
                    pwm_commanded_pct=rng.randint(20, 100),
                    pwm_enable_mode=1,
                )
                for f in fans_
            ],
            sensors=[SimpleNamespace(id=s, value_c=rng.uniform(25, 95)) for s in sensors_],
        )
    doc["trace"] = rec.to_dict()
    doc["state"] = d.STATE_COMPLETE
    path = save_report(doc, tmp_path)
    size = path.stat().st_size
    assert size < REPORT_MAX_BYTES, f"a three-hour report measured {size} bytes"
    # And it really does reload through the capped reader.
    loaded, _ = load_report(path)
    assert loaded["trace"]["samples"] == MAX_SAMPLES


# ── View ────────────────────────────────────────────────────────────────────


def test_unknown_reads_inconclusive_in_the_report_only():
    from control_ofc.services.validation_view import result_label as session_label

    assert result_label("unknown") == "Inconclusive"  # D-c
    assert session_label("unknown") == "Unknown"  # the session vocabulary is untouched
    assert result_label("a_new_token") == "a_new_token"


def test_the_probe_curve_plots_the_walk_only():
    curve = probe_curve(probe_run())
    assert curve is not None and curve.has_data
    assert [p.duty_pct for p in curve.falling] == [6, 10]
    assert [p.duty_pct for p in curve.rising] == [12]
    assert probe_curve(None) is None


def test_the_view_builds_sections_and_the_test_matrix():
    doc = new_doc(
        [channel(CPU), channel("openfan:ch00", source="openfan")],
        unavailable={CPU: {TEST_PROBE: "Only offered on a chassis-fan role"}},
    )
    doc["steps"] = [
        {
            "step_id": "s1",
            "channel_id": CPU,
            "test": TEST_SWEEP,
            "status": d.STEP_COMPLETE,
            "reason": "",
            "result": sweep_run(),
        },
    ]
    doc["state"] = d.STATE_COMPLETE
    from control_ofc.services.pwm_report.findings import derive_findings

    doc["findings"], doc["actions"] = derive_findings(doc)
    vm = build_report_view(doc)
    assert vm.state_label == "Complete"
    cpu = next(c for c in vm.channels if c.channel_id == CPU)
    matrix = {t.title: (t.status_label, t.reason) for t in cpu.tests}
    assert matrix[SPECS[TEST_SWEEP].title][0] == "Done"
    assert matrix[SPECS[TEST_PROBE].title] == ("Not offered", "Only offered on a chassis-fan role")
    assert matrix[SPECS[TEST_VERIFY].title][0] == "Not selected"
    assert cpu.sweep_curve is not None and cpu.sweep_curve.has_data
    openfan = next(c for c in vm.channels if c.channel_id == "openfan:ch00")
    assert openfan.tests == [], "a read-only channel has no test matrix"
    assert vm.not_tested, "the by-design list reaches the summary"
    assert not hasattr(vm, "verdict"), "no global verdict"
    ev = json.loads(evidence_text(doc))
    assert ev["steps"][0]["result"]["run_id"] == "char-1"
