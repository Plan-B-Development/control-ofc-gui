"""DEC-212: Hardware page Qt-free view-model builders (headless).

Pins the readiness summary (verdict + counts, N/A honesty), the grouped
checklist, the recommended-action derivation, and the Super-I/O panel (real
columns only — no fabricated per-channel/address/age data).
"""

from __future__ import annotations

from control_ofc.api.models import (
    HardwareReadiness,
    ReadinessItem,
    ReadinessRollup,
    SuperIoChip,
    SuperIoRecommendation,
    SuperIoReport,
)
from control_ofc.services.hardware_view import (
    build_checklist,
    build_readiness_summary,
    build_recommended_actions,
    build_superio_panel,
)
from control_ofc.ui.cooling_readiness import build_readiness_items


def _hw(overall="warning", items=None, superio=None, **kw) -> HardwareReadiness:
    return HardwareReadiness(
        overall=overall,
        rollup=ReadinessRollup(overall=overall, top_summary=kw.pop("top", None) or None),
        items=items if items is not None else [],
        superio=superio if superio is not None else SuperIoReport(arch_supported=True),
        **kw,
    )


def _unbound_chip(name="it8688") -> SuperIoChip:
    return SuperIoChip(
        chip_name=name,
        vendor="ite",
        confidence="medium",
        expected_module="it87",
        evidence=["dmi_board_table"],
        hwmon_present=False,
        recommendation=SuperIoRecommendation(
            module="it87",
            in_mainline=False,
            load_hint="sudo modprobe it87",
            reason="Chip present but no driver bound.",
            risk_notes=["Needs it87-dkms-git"],
        ),
    )


def _bound_chip(name="nct6799") -> SuperIoChip:
    return SuperIoChip(
        chip_name=name,
        vendor="nuvoton",
        confidence="high",
        expected_module="nct6775",
        bound_driver="nct6775",
        module_loaded=True,
        hwmon_present=True,
    )


# ── Checklist grouping ──────────────────────────────────────────────────────


def test_build_checklist_groups_in_order():
    hw = _hw(
        items=[
            ReadinessItem(code="cpu_sensor_present", severity="ok"),
            ReadinessItem(code="pwm_controls_present", severity="ok"),
            ReadinessItem(code="superio_driver_unloaded", severity="warning"),
            ReadinessItem(code="unknown_sensors_present", severity="info"),
            ReadinessItem(code="a_future_unknown_code", severity="warning"),
        ]
    )
    groups = build_checklist(build_readiness_items(hw))
    assert [g.name for g in groups] == [
        "Temperature monitoring",
        "Fan monitoring and control",
        "Super-I/O and kernel support",
        "Sensor configuration",
    ]
    sensor_codes = {r.code for g in groups if g.name == "Sensor configuration" for r in g.rows}
    assert "a_future_unknown_code" in sensor_codes  # unknown → Sensor configuration
    temp_row = next(r for g in groups if g.name == "Temperature monitoring" for r in g.rows)
    assert temp_row.badge_word == "PASS"  # ok → PASS


# ── Summary counts + verdict + N/A honesty ──────────────────────────────────


def test_summary_counts_and_verdict():
    hw = _hw(
        overall="critical",
        items=[
            ReadinessItem(code="cpu_sensor_present", severity="ok"),
            ReadinessItem(code="pwm_controls_present", severity="ok"),
            ReadinessItem(code="no_pwm_controls", severity="warning"),
            ReadinessItem(code="cpu_sensor_missing", severity="critical"),
            ReadinessItem(code="cpu_default_low_confidence", severity="info"),
        ],
    )
    s = build_readiness_summary(hw)
    assert (s.pass_count, s.warn_count, s.crit_count, s.info_count) == (2, 1, 1, 1)
    assert s.to_fix == 2
    assert (s.verdict_word, s.verdict_state) == ("NEEDS ATTENTION", "crit")
    assert [seg.label for seg in s.segments] == ["PASS", "WARN", "CRIT", "INFO"]  # no N/A


def test_verdict_ready_for_ok_and_info():
    assert build_readiness_summary(_hw(overall="ok")).verdict_word == "READY"
    assert build_readiness_summary(_hw(overall="info")).verdict_state == "ok"
    assert build_readiness_summary(_hw(overall="warning")).verdict_word == "NEEDS ATTENTION"


def test_summary_last_scanned_line_formats_age():
    # DEC-216: the "last scanned" phrasing (relocated from CoolingReadinessView).
    assert (
        "just now" in build_readiness_summary(_hw(overall="ok", scanned_age_ms=0)).scanned_age_line
    )
    assert (
        "2m ago"
        in build_readiness_summary(_hw(overall="ok", scanned_age_ms=125_000)).scanned_age_line
    )


# ── Recommended actions ─────────────────────────────────────────────────────


def test_recommended_action_targets():
    hw = _hw(
        items=[
            ReadinessItem(code="cpu_sensor_missing", severity="critical"),
            ReadinessItem(code="selected_mb_sensor_missing", severity="warning"),
            ReadinessItem(code="pwm_control_unverified", severity="warning"),
            ReadinessItem(code="no_pwm_controls", severity="warning"),
            ReadinessItem(code="sensors_unavailable", severity="warning"),
        ]
    )
    targets = {
        a.code: a.action_target for a in build_recommended_actions(build_readiness_items(hw))
    }
    assert targets["cpu_sensor_missing"] == "preferred_cpu"
    assert targets["selected_mb_sensor_missing"] == "preferred_mb"
    assert targets["pwm_control_unverified"] == "pwm_verify"
    assert targets["no_pwm_controls"] == "superio"
    assert targets["sensors_unavailable"] == "sensors"


def test_recommended_actions_exclude_ok_items():
    hw = _hw(
        items=[
            ReadinessItem(code="cpu_sensor_present", severity="ok"),
            ReadinessItem(code="no_pwm_controls", severity="warning"),
        ]
    )
    assert [a.code for a in build_recommended_actions(build_readiness_items(hw))] == [
        "no_pwm_controls"
    ]


def test_impact_chips():
    hw = _hw(
        items=[
            ReadinessItem(
                code="cpu_sensor_missing",
                severity="critical",
                affects_safety=True,
                blocks_control=True,
            )
        ]
    )
    action = build_recommended_actions(build_readiness_items(hw))[0]
    labels = [c.label for c in action.impact_chips]
    assert "affects thermal safety" in labels
    assert "blocks fan control" in labels


# ── Super-I/O panel (real columns only) ─────────────────────────────────────


def test_superio_real_columns_only():
    panel = build_superio_panel(SuperIoReport(arch_supported=True, chips=[_unbound_chip("it8688")]))
    assert panel.has_chips is True
    assert len(panel.rows) == 1  # one chip, NO per-channel rows
    row = panel.rows[0]
    assert row.chip == "it8688"
    assert row.vendor == "ITE"
    assert row.driver_text == "it87"  # expected_module (unbound)
    assert row.health_state == "warn"
    assert row.copy_command == "sudo modprobe it87"
    assert row.mainline_state == "warn"  # not in_mainline
    assert panel.show_liability is True
    assert "need a driver loaded" in panel.summary_text


def test_superio_bound_chip_is_healthy():
    panel = build_superio_panel(SuperIoReport(arch_supported=True, chips=[_bound_chip()]))
    row = panel.rows[0]
    assert row.health_state == "ok"
    assert row.driver_text == "nct6775"  # bound_driver
    assert row.has_recommendation is False
    assert panel.show_liability is False
    assert "have a driver bound" in panel.summary_text


def test_superio_empty_and_non_x86():
    empty = build_superio_panel(SuperIoReport(arch_supported=True, chips=[]))
    assert empty.has_chips is False and empty.empty_note
    non_x86 = build_superio_panel(SuperIoReport(arch_supported=False))
    assert non_x86.arch_supported is False and non_x86.arch_note


def test_superio_acpi_conflict_note():
    panel = build_superio_panel(
        SuperIoReport(arch_supported=True, chips=[_unbound_chip()], acpi_conflict_drivers=["it87"])
    )
    assert "ACPI firmware claims" in panel.notes_text
    assert "it87" in panel.notes_text
