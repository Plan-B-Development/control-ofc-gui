"""Shared builders for the PWM Test Report tests (DEC-404 Stage 4).

Everything here produces the daemon's WIRE shape — the dicts a real daemon
serialises — never a GUI model, because the report keeps and reasons over the
raw bodies. A fixture that built models instead would test a report the runner
never sees.
"""

from __future__ import annotations

from control_ofc.services.pwm_report import document as d
from control_ofc.services.pwm_report.runner import (
    Call,
    CallOutcome,
    ReportRunner,
    build_plan,
)

CPU = "hwmon:it8696:it87.2624:pwm1:CPU_FAN"
SYS = "hwmon:it8696:it87.2624:pwm2:SYS_FAN1"
PUMP = "hwmon:it8696:it87.2624:pwm3:AIO_PUMP"
EMPTY = "hwmon:it8696:it87.2624:pwm4:SYS_FAN2"
OPENFAN = "openfan:ch00"


def fan(
    fid: str,
    *,
    source: str = "hwmon",
    rpm: int | None = 900,
    readback: int | None = 40,
    commanded: int | None = 40,
    mode: int | None = 1,
    corrections: int | None = 0,
    not_holding: bool | None = False,
    stall: bool | None = False,
) -> dict:
    entry = {"id": fid, "source": source, "rpm": rpm, "age_ms": 20}
    if source == "hwmon":
        entry.update(
            {
                "pwm_readback_pct": readback,
                "pwm_commanded_pct": commanded,
                "pwm_enable_mode": mode,
                "stall_detected": stall,
                "last_commanded_pwm": commanded,
            }
        )
        if corrections is not None:
            entry["duty_corrections"] = corrections
        if not_holding is not None:
            entry["duty_not_holding"] = not_holding
    else:
        entry["last_commanded_pwm"] = commanded
    return entry


def header(
    hid: str,
    *,
    role: str = "chassis_fan",
    role_source: str = "user_assigned",
    writable: bool = True,
    floor: int | None = 0,
    stop_permitted: bool | None = True,
    rpm_available: bool = True,
) -> dict:
    return {
        "id": hid,
        "label": hid.rsplit(":", 1)[-1],
        "chip_name": "it8696",
        "device_id": "it87.2624",
        "pwm_index": int(hid.split(":pwm")[1].split(":")[0]),
        "is_writable": writable,
        "rpm_available": rpm_available,
        "role": role,
        "role_source": role_source,
        "effective_min_pwm_pct": floor,
        "stop_permitted": stop_permitted,
    }


def profile(controls: list[dict], curves: list[dict], *, pid: str = "quiet") -> dict:
    return {"id": pid, "name": "Quiet", "version": 7, "controls": controls, "curves": curves}


def control(members: list[str], curve_id: str = "c1", **tuning: object) -> dict:
    c = {
        "id": f"ctl-{curve_id}",
        "name": f"Control {curve_id}",
        "mode": "curve",
        "curve_id": curve_id,
        "members": [{"source": "hwmon", "member_id": m, "member_label": m} for m in members],
        "step_up_pct": 100.0,
        "step_down_pct": 100.0,
        "start_pct": 0.0,
        "stop_pct": 0.0,
        "offset_pct": 0.0,
        "minimum_pct": 0.0,
    }
    c.update(tuning)
    return c


def graph(cid: str, outputs: list[float]) -> dict:
    return {
        "id": cid,
        "name": cid,
        "type": "graph",
        "sensor_id": "cpu",
        "points": [{"temp_c": 30 + 10 * i, "output_pct": o} for i, o in enumerate(outputs)],
    }


def bundle(
    *,
    fans: list[dict],
    headers: list[dict],
    status: dict | None = None,
    profile_body: dict | None = None,
    hardware: dict | None = None,
    capabilities: dict | None = None,
) -> dict:
    status = status or {"thermal_state": "normal", "active_profile_id": "quiet"}
    entries = {
        "capabilities": capabilities
        or {"daemon_version": "2.54.0", "api_version": 1, "control": {"stall_probe": True}},
        "status": status,
        "fans": {"fans": fans},
        "sensors": {"sensors": []},
        "headers": {"headers": headers},
        "hardware": hardware
        or {
            "kernel_release": "6.18.2-1-cachyos",
            "board": {
                "vendor": "Gigabyte",
                "name": "X870E AORUS MASTER",
                "bios_version": "F14c",
                "bios_date": "08/14/2025",
            },
            "thermal_safety": {"emergency_threshold_c": 110.0, "release_threshold_c": 80.0},
            "hwmon": {"chips_detected": [{"chip_name": "it8696", "device_id": "it87.2624"}]},
            "kernel_modules": [
                {
                    "name": "it87",
                    "loaded": True,
                    "in_mainline": False,
                    "version": "v1.0-160",
                    "srcversion": "ABC123",
                    "out_of_tree": True,
                }
            ],
        },
        "profile_active": {"active": bool(profile_body), "profile_id": "quiet"},
    }
    out = {
        name: {"status": 200, "body": body, "error": None, "fetched_at": "2026-09-22T00:00:00Z"}
        for name, body in entries.items()
    }
    if profile_body is not None:
        out["profile"] = {
            "status": 200,
            "body": profile_body,
            "error": None,
            "fetched_at": "2026-09-22T00:00:00Z",
        }
    return out


def channel(
    cid: str,
    *,
    source: str = "hwmon",
    in_profile: bool = False,
    pump: bool = False,
    role: str = "chassis_fan",
) -> dict:
    return {
        "channel_id": cid,
        "source": source,
        "name": cid.rsplit(":", 1)[-1],
        "role": role,
        "role_source": "user_assigned",
        "writable": source == "hwmon",
        "rpm_available": True,
        "rpm": 900,
        "in_profile": in_profile,
        "pump_protected": pump,
        "effective_min_pwm_pct": 30 if pump else 0,
        "stop_permitted": not pump,
    }


def new_doc(
    channels: list[dict],
    *,
    facts: dict | None = None,
    unavailable: dict | None = None,
) -> dict:
    return d.new_document(
        report_id="20260922T000000Z-abcdef",
        created_at="2026-09-22T00:00:00Z",
        gui_facts={"gui_version": "2.81.0", "kernel": "6.18.2", "python": "3.14", "qt": "6.11"},
        channels=channels,
        user_facts={"cooler": {}, "headers": facts or {}},
        plan={"selections": {}, "unavailable": unavailable or {}},
    )


def ok(body: object, status: int = 200) -> CallOutcome:
    return CallOutcome(ok=True, status=status, body=body)


def refused(status: int, code: str, message: str = "no", *, retryable: bool = False) -> CallOutcome:
    return CallOutcome(
        ok=False,
        status=status,
        error_code=code,
        error_message=message,
        retryable=retryable,
        category="error",
    )


UNAVAILABLE = CallOutcome(ok=False, category="unavailable", error_message="connection refused")


def verify_body(result: str = "effective", *, test_pct: int = 60) -> dict:
    return {
        "header_id": CPU,
        "result": result,
        "initial_state": {"pwm_enable": 1, "pwm_raw": 102, "pwm_percent": 40, "rpm": 900},
        "final_state": {"pwm_enable": 1, "pwm_raw": 153, "pwm_percent": test_pct, "rpm": 1300},
        "test_pwm_percent": test_pct,
        "wait_seconds": 6,
        "details": "",
    }


def sweep_run(state: str = "complete", *, run_id: str = "char-1", header_id: str = CPU) -> dict:
    points = []
    for i, duty in enumerate([100, 90, 80, 70, 60, 50, 40, 30, 20, 30, 40]):
        points.append(
            {
                "requested_pct": duty,
                "command_accepted": True,
                "readback_pct": duty,
                "rpm_after": 400 + 12 * duty,
                "direction": "falling" if i < 9 else "rising",
                "step_index": i,
            }
        )
    return {
        "run_id": run_id,
        "header_id": header_id,
        "state": state,
        "requested_points_pct": [20, 30, 40, 50, 60, 70, 80, 90, 100],
        "settle_seconds": 12,
        "bidirectional": True,
        "stability_seconds": 20,
        "points": points if state != "running" else points[:3],
        "summary": None
        if state == "running"
        else {
            "command_acceptance": "pass",
            "pwm_readback": "pass",
            "rpm_response": "responsive",
            "min_tested_pct": 20,
            "max_tested_pct": 100,
            "monotonic": True,
            "monotonic_falling": True,
            "monotonic_rising": True,
            "min_responsive_pct": 20,
            "max_responsive_pct": 100,
            "hysteresis_verdict": "none",
            "hysteresis_compared_points": 2,
            "stability_verdict": "stable",
            "worst_cv_pct": 0.8,
            "possible_device_override": False,
            "interference_detected": False,
        },
        "original_pct": 40,
        "restore_failed": False,
        "restore_outcome": "restored" if state != "running" else "pending",
    }


def probe_run(
    outcome: str | None = "stall_and_restart_found",
    *,
    state: str = "complete",
    stall: int | None = 6,
    restart: int | None = 12,
    run_id: str = "probe-1",
) -> dict:
    return {
        "run_id": run_id,
        "header_id": SYS,
        "state": state,
        "outcome": outcome,
        "abort_reason": None,
        "detail": None,
        "stall_duty_pct": stall,
        "restart_duty_pct": restart,
        "hysteresis_pct": None if stall is None or restart is None else restart - stall,
        "lowest_commanded_pct": stall,
        "time_below_floor_ms": 90_000,
        "baseline_rpm": 700,
        "baseline_settled": True,
        "refresh_ms": 2000,
        "refresh_source": "observed",
        "dwell_ms": 6000,
        "confirm_ms": 4000,
        "budget_ms": 129_500,
        "start_cpu_temp_c": 45.0,
        "max_cpu_temp_c": 46.5,
        "rise_limit_c": 5.0,
        "restart_failed_at_full": False,
        "points": [
            {
                "phase": "descent",
                "step_index": 1,
                "commanded_pct": 10,
                "command_accepted": True,
                "rpm_after": 300,
                "held_ms": 6000,
                "samples": 12,
                "zero_samples": 0,
                "observation": "spinning",
            },
            {
                "phase": "descent",
                "step_index": 2,
                "commanded_pct": 6,
                "command_accepted": True,
                "rpm_after": 0,
                "held_ms": 6000,
                "samples": 12,
                "zero_samples": 8,
                "observation": "stalled",
            },
            {
                "phase": "ascent",
                "step_index": 3,
                "commanded_pct": 12,
                "command_accepted": True,
                "rpm_after": 350,
                "held_ms": 6000,
                "samples": 12,
                "zero_samples": 2,
                "observation": "restarted",
            },
        ],
        "original_pct": 40,
        "restore_failed": False,
        "restore_outcome": "restored",
        "completed_unix_ms": 1,
        "provenance": {},
    }


def one(calls: list[Call], kind: str | None = None) -> Call:
    assert len(calls) == 1, f"expected exactly one call, got {calls}"
    if kind is not None:
        assert calls[0].kind == kind, f"expected {kind}, got {calls[0].kind}"
    return calls[0]


def runner_for(
    selection: dict[str, set[str]],
    channels: list[dict],
    *,
    consent: set[str] | frozenset[str] = frozenset(),
    facts: dict | None = None,
) -> ReportRunner:
    doc = new_doc(channels, facts=facts)
    return ReportRunner(doc, build_plan(selection), probe_consent=consent)
