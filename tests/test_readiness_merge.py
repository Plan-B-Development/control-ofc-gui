"""Tests for the pure readiness merge (DEC-206) — the fusion of the daemon
`/inventory/readiness` items with the GUI `/diagnostics/hardware` problems into
one severity-ranked, actionable index. Success + failure/edge paths."""

from __future__ import annotations

from control_ofc.api.models import (
    BoardInfo,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    InventoryReadiness,
    KernelModuleInfo,
    ReadinessItem,
    ThermalSafetyInfo,
)
from control_ofc.ui.readiness_merge import (
    ACTION_DEEP_LINK,
    ACTION_IN_SURFACE,
    ACTION_NONE,
    ACTION_TAB_SWITCH,
    MergedReadinessItem,
    merge_readiness,
    overall_severity,
    to_fix_count,
)


def _item(code: str, severity: str, **kw) -> ReadinessItem:
    return ReadinessItem(code=code, severity=severity, summary=kw.pop("summary", code), **kw)


def _inv(*items: ReadinessItem) -> InventoryReadiness:
    return InventoryReadiness(items=list(items))


def _diag(**ov) -> HardwareDiagnosticsResult:
    defaults = dict(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="nct6779", expected_driver="nct6775", header_count=5)
            ],
            total_headers=5,
            writable_headers=5,
        ),
        board=BoardInfo(vendor="", name="Generic"),
        kernel_modules=[KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True)],
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
    )
    defaults.update(ov)
    return HardwareDiagnosticsResult(**defaults)


def _by_code(items: list[MergedReadinessItem]) -> dict[str, MergedReadinessItem]:
    return {m.code: m for m in items}


def test_daemon_only_sorted_most_severe_first_ok_last():
    inv = _inv(
        _item("cpu_sensor_present", "ok", affects_safety=True),
        _item("pwm_control_unverified", "info"),
        _item("no_pwm_controls", "warning", blocks_control=True, reboot_may_be_required=True),
        _item("cpu_sensor_missing", "critical", affects_safety=True),
    )
    merged = merge_readiness(inv, None)
    order = [m.code for m in merged]
    # critical → warning → info → ok (ok always last).
    assert order == [
        "cpu_sensor_missing",
        "no_pwm_controls",
        "pwm_control_unverified",
        "cpu_sensor_present",
    ]
    assert merged[-1].is_ok
    assert overall_severity(merged) == "critical"
    assert to_fix_count(merged) == 2  # critical + warning; info/ok excluded


def test_daemon_action_mapping():
    inv = _inv(
        _item("cpu_sensor_missing", "critical"),
        _item("selected_mb_sensor_missing", "warning"),
        _item("pwm_control_unverified", "info"),
        _item("no_pwm_controls", "warning"),
        _item("sensors_unavailable", "warning"),
        _item("cpu_sensor_present", "ok"),
    )
    by = _by_code(merge_readiness(inv, None))
    assert by["cpu_sensor_missing"].action.kind == ACTION_DEEP_LINK
    assert by["cpu_sensor_missing"].action.target == "preferred_cpu"
    assert by["selected_mb_sensor_missing"].action.target == "preferred_mb"
    assert by["pwm_control_unverified"].action.kind == ACTION_IN_SURFACE
    assert by["pwm_control_unverified"].action.target == "pwm_verify_all"
    assert by["no_pwm_controls"].action.kind == ACTION_TAB_SWITCH
    assert by["no_pwm_controls"].action.target == "superio"
    assert by["sensors_unavailable"].action.target == "sensors"
    assert by["cpu_sensor_present"].action.kind == ACTION_NONE  # ok item, no button


def test_daemon_item_carries_plaintext_detail_not_html():
    """Security boundary: a daemon item's detail lands in plain_detail (rendered
    PlainText), never html_detail."""
    inv = _inv(
        _item(
            "no_pwm_controls",
            "warning",
            detail="No hwmon pwmN control was discovered.",
            recommended_action="Load the it87 driver.",
        )
    )
    m = merge_readiness(inv, None)[0]
    assert "No hwmon pwmN" in m.plain_detail
    assert "→ Load the it87 driver." in m.plain_detail
    assert m.html_detail == ""


def test_gui_problem_with_daemon_equivalent_folds_not_duplicated():
    """`all_readonly` (from /diagnostics/hardware) folds into the daemon
    `pwm_read_only` item — one row, and the GUI doc link attaches to it."""
    inv = _inv(_item("pwm_read_only", "warning", detail="Kernel exposes them read-only."))
    # chips present (so no_chips does NOT fire) but all headers read-only.
    diag = _diag(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="nct6779", expected_driver="nct6775", header_count=3)
            ],
            total_headers=3,
            writable_headers=0,
        )
    )
    merged = merge_readiness(inv, diag)
    codes = [m.code for m in merged]
    assert codes.count("pwm_read_only") == 1
    assert "all_readonly" not in codes  # folded, not a second row
    folded = _by_code(merged)["pwm_read_only"]
    assert folded.doc_url  # GUI doc link attached
    assert folded.html_detail  # GUI fix folded into the trusted-HTML slot
    # Security boundary: the GUI fix must NOT leak into the daemon PlainText slot.
    assert folded.plain_detail == "Kernel exposes them read-only."


def test_gui_only_problem_survives_as_first_class_item():
    """A GUI detection with no daemon equivalent (here `no_chips`, with no daemon
    superio/pwm item to absorb it) stays a first-class item — coverage not lost."""
    inv = _inv(_item("cpu_sensor_present", "ok"))  # no pwm/superio daemon items
    diag = _diag(hwmon=HwmonDiagnostics(chips_detected=[], total_headers=0, writable_headers=0))
    merged = merge_readiness(inv, diag)
    by = _by_code(merged)
    assert "no_chips" in by
    gui = by["no_chips"]
    assert gui.source == "gui"
    assert gui.html_detail  # GUI fix in the trusted-HTML slot
    assert gui.plain_detail == ""  # no daemon text on a GUI-only item


def test_empty_inputs_are_safe():
    assert merge_readiness(None, None) == []
    assert overall_severity([]) == "ok"
    assert to_fix_count([]) == 0
    assert merge_readiness(_inv(), None) == []
