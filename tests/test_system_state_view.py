"""DEC-211: System State page Qt-free view-model builders (headless).

Reuses the `HardwareDiagnosticsResult` fixture shapes from
`test_diagnostics_troubleshooting_tab.py` and pins the issue unification +
severity sort + interference / safety / registry derivations.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    AcpiConflictInfo,
    BoardInfo,
    GpuDiagnosticsInfo,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    HwmonHeader,
    KernelModuleInfo,
    ThermalSafetyInfo,
)
from control_ofc.services.system_state_view import (
    build_interference_vm,
    build_issue_cards,
    build_registry_rows,
    build_safety_gpu_vm,
    build_system_state_vm,
    build_verify_headers,
    daemon_version_at_least,
    interference_gauge_fraction,
    severity_to_state,
)
from control_ofc.ui.hwmon_guidance import severity_display
from control_ofc.ui.widgets.readiness_report import advisory_rows, detect_readiness_problems


def _diag(**overrides) -> HardwareDiagnosticsResult:
    hwmon = overrides.pop("hwmon", None) or HwmonDiagnostics(
        chips_detected=[
            HwmonChipInfo(
                chip_name="nct6798",
                device_id="nct6798.656",
                expected_driver="nct6775",
                in_mainline_kernel=True,
                header_count=5,
            ),
        ],
        total_headers=5,
        writable_headers=3,
    )
    defaults = dict(
        hwmon=hwmon,
        board=BoardInfo(vendor="ASUS", name="ProArt X870E", bios_version="1234"),
        kernel_modules=[KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True)],
        thermal_safety=ThermalSafetyInfo(
            state="normal",
            cpu_sensor_found=True,
            emergency_threshold_c=105.0,
            release_threshold_c=80.0,
        ),
    )
    defaults.update(overrides)
    return HardwareDiagnosticsResult(**defaults)


def _diag_with_revert(header_id: str, count: int) -> HardwareDiagnosticsResult:
    return _diag(
        hwmon=HwmonDiagnostics(
            total_headers=1, writable_headers=1, enable_revert_counts={header_id: count}
        ),
    )


def _diag_acpi_and_revert() -> HardwareDiagnosticsResult:
    return _diag(
        hwmon=HwmonDiagnostics(
            total_headers=1, writable_headers=1, enable_revert_counts={"pwm1": 5}
        ),
        acpi_conflicts=[
            AcpiConflictInfo(
                io_range="0x0290-0x0299", claimed_by="ACPI", conflicts_with_driver="it87"
            )
        ],
    )


def _healthy_diag() -> HardwareDiagnosticsResult:
    return _diag(
        board=BoardInfo(vendor="", name="Generic"),
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="nct6779", expected_driver="nct6775", header_count=5)
            ],
            total_headers=5,
            writable_headers=5,
        ),
    )


def _diag_gigabyte_it8696() -> HardwareDiagnosticsResult:
    return _diag(
        board=BoardInfo(vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER"),
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="it8696", expected_driver="it87", header_count=5)
            ],
            total_headers=5,
            writable_headers=5,
        ),
    )


# ── Small pure maps ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "severity,state",
    [
        ("critical", "crit"),
        ("high", "warn"),
        ("warn", "warn"),
        ("medium", "warn"),
        ("info", "info"),
        ("unknown-future", "info"),
    ],
)
def test_severity_to_state(severity, state):
    assert severity_to_state(severity) == state


@pytest.mark.parametrize(
    "version,ok",
    [("1.11.0", True), ("1.11.0-rc1", True), ("1.11", True), ("1.10.9", False), ("", False)],
)
def test_daemon_version_at_least(version, ok):
    assert daemon_version_at_least(version, (1, 11, 0)) is ok


@pytest.mark.parametrize("count,fraction", [(0, 0.0), (5, 0.5), (10, 1.0), (996, 1.0), (-3, 0.0)])
def test_interference_gauge_fraction(count, fraction):
    assert interference_gauge_fraction(count) == fraction


# ── Interference ────────────────────────────────────────────────────────────


def test_build_interference_vm_high_contention():
    vm = build_interference_vm(_diag_with_revert("hwmon:it8696:it87.2624:pwm1", 996))
    assert vm.has_contention is True
    assert vm.highest_count == 996
    assert vm.header_id == "hwmon:it8696:it87.2624:pwm1"
    assert vm.severity == "high"
    assert vm.severity_state == "crit"  # HIGH revert = red (matches classify_reclaim_severity)
    assert vm.gauge_fraction == 1.0
    assert vm.title == "High Contention Detected"


def test_build_interference_vm_no_contention():
    vm = build_interference_vm(_diag())
    assert vm.has_contention is False
    assert vm.highest_count == 0
    assert vm.gauge_fraction == 0.0
    assert vm.severity_state == "ok"


# ── Issue unification + sort ────────────────────────────────────────────────


def test_build_issue_cards_unifies_problems_and_advisories():
    diag = _diag_gigabyte_it8696()
    cards = build_issue_cards(diag)
    n_problems = len(detect_readiness_problems(diag))
    n_advisories = len(advisory_rows(diag))
    assert n_problems >= 1 and n_advisories >= 1  # this board has both
    assert len(cards) == n_problems + n_advisories  # no source dropped, none double-counted away
    keys = {c.key for c in cards}
    assert any(not k.startswith("advisory_") for k in keys)  # a checklist problem card
    assert any(k.startswith("advisory_") for k in keys)  # a vendor advisory card


def test_issue_cards_are_severity_sorted_descending():
    ranks = [severity_display(c.severity).rank for c in build_issue_cards(_diag_acpi_and_revert())]
    assert ranks == sorted(ranks, reverse=True)


def test_issue_card_carries_detail_for_acpi():
    cards = {c.key: c for c in build_issue_cards(_diag_acpi_and_revert())}
    assert "acpi" in cards
    assert cards["acpi"].detail and "conflicts with it87" in cards["acpi"].detail
    assert cards["acpi"].doc_url  # doc-link button present


# ── Safety & GPU ────────────────────────────────────────────────────────────


def test_build_safety_gpu_vm_with_gpu():
    diag = _diag(
        gpu=GpuDiagnosticsInfo(
            model_name="RX 9070 XT",
            fan_control_method="pmfw_curve",
            overdrive_enabled=True,
            ppfeaturemask="0xffff3fff",
            ppfeaturemask_bit14_set=False,
            zero_rpm_available=True,
            fan_speed_min_pct=15,
            fan_speed_max_pct=100,
        )
    )
    vm = build_safety_gpu_vm(diag)
    assert vm.has_gpu is True
    assert vm.gpu_model == "RX 9070 XT"
    labels = {r.label: r for r in vm.gpu_rows}
    assert labels["Fan Control"].value == "pmfw_curve"
    assert labels["ppfeaturemask"].state == "warn"  # bit 14 not set
    assert vm.speed_min == 15 and vm.speed_max == 100 and vm.speed_bar_visible is True
    assert vm.thermal_text == "Normal"
    assert vm.thermal_state == "ok"


def test_build_safety_gpu_vm_read_only_fan_control_is_warn():
    # B5: a read-only GPU fan-control method must surface as a warn-state row
    # (_fan_method_state — only "pmfw_curve"/"hwmon_pwm" are "ok"). The existing
    # GPU test asserts the row value but never its state, leaving this arm dead.
    diag = _diag(
        gpu=GpuDiagnosticsInfo(
            model_name="RX 9070 XT",
            fan_control_method="read_only",
            overdrive_enabled=True,
            ppfeaturemask="0xffff3fff",
            ppfeaturemask_bit14_set=True,
            zero_rpm_available=True,
            fan_speed_min_pct=15,
            fan_speed_max_pct=100,
        )
    )
    labels = {r.label: r for r in build_safety_gpu_vm(diag).gpu_rows}
    assert labels["Fan Control"].value == "read_only"
    assert labels["Fan Control"].state == "warn"


def test_build_safety_gpu_vm_no_gpu():
    vm = build_safety_gpu_vm(_diag())
    assert vm.has_gpu is False
    assert vm.gpu_rows == []
    assert vm.speed_bar_visible is False


# ── Registry ────────────────────────────────────────────────────────────────


def test_build_registry_rows_unifies_chips_and_modules():
    rows = build_registry_rows(_diag())
    chips = [r for r in rows if r.kind == "chip"]
    modules = [r for r in rows if r.kind == "module"]
    assert len(chips) == 1 and len(modules) == 1
    assert chips[0].status_label == "LOADED"  # nct6775 is in loaded modules
    assert chips[0].component == "nct6798"
    assert modules[0].status_label == "MODULE"


def test_registry_marks_missing_driver():
    diag = _diag(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="it8689", expected_driver="it87", header_count=3)
            ],
            total_headers=3,
            writable_headers=0,
        ),
        kernel_modules=[],  # it87 not loaded → MISSING
    )
    chip = next(r for r in build_registry_rows(diag) if r.kind == "chip")
    assert chip.status_label == "MISSING"
    assert chip.status_state == "warn"


# ── Verify headers + top-level ──────────────────────────────────────────────


def test_build_verify_headers_writable_only():
    headers = [
        HwmonHeader(id="hwmon:nct6798:pwm1", label="CPU", is_writable=True),
        HwmonHeader(id="hwmon:nct6798:pwm2", label="", is_writable=True),
        HwmonHeader(id="hwmon:amdgpu:pwm1", label="GPU", is_writable=False),
    ]
    entries = build_verify_headers(headers)
    assert entries == [
        ("CPU (hwmon:nct6798:pwm1)", "hwmon:nct6798:pwm1"),
        ("hwmon:nct6798:pwm2 (hwmon:nct6798:pwm2)", "hwmon:nct6798:pwm2"),
    ]


def test_build_verify_headers_prefers_the_resolved_name():
    """DEC-229: the combo must name headers the way the rest of the app does.

    With the raw `HwmonHeader.label` it offered the daemon's synthesised `pwm1`
    — an id the user has seen nowhere else — so picking the right header to
    verify meant guessing. The resolver (alias, sysfs, sensors.d, board table)
    is injected rather than reached for, keeping this layer Qt- and state-free.
    """
    headers = [
        HwmonHeader(id="hwmon:it8696:pwm1", label="pwm1", pwm_index=1, is_writable=True),
        HwmonHeader(id="hwmon:it8696:pwm2", label="pwm2", pwm_index=2, is_writable=True),
    ]
    resolved = {"hwmon:it8696:pwm1": "CPU_FAN", "hwmon:it8696:pwm2": "My Rad Fans"}
    entries = build_verify_headers(headers, resolved.get)
    assert entries == [
        ("CPU_FAN (hwmon:it8696:pwm1)", "hwmon:it8696:pwm1"),
        ("My Rad Fans (hwmon:it8696:pwm2)", "hwmon:it8696:pwm2"),
    ]


def test_build_verify_headers_falls_back_when_resolver_is_silent():
    """An injected resolver that answers nothing must not blank the combo."""
    headers = [HwmonHeader(id="hwmon:nct6798:pwm1", label="CPU", is_writable=True)]
    assert build_verify_headers(headers, lambda _hid: "") == [
        ("CPU (hwmon:nct6798:pwm1)", "hwmon:nct6798:pwm1")
    ]


def test_module_collision_detail_from_daemon():
    from control_ofc.api.models import ModuleCollisionInfo
    from control_ofc.services.system_state_view import build_module_collision_detail

    diag = _diag(
        module_collisions=[
            ModuleCollisionInfo(
                module_a="nct6687",
                module_b="nct6775",
                severity="critical",
                summary="both drivers claim the chip",
                remediation="blacklist one and reboot",
            )
        ]
    )
    detail = build_module_collision_detail(diag)
    assert detail and "nct6687" in detail and "blacklist one and reboot" in detail
    assert build_module_collision_detail(_diag()) is None  # nothing → None


def test_safety_gpu_vm_kernel_warnings_and_other_gpus():
    from control_ofc.api.models import (
        AmdPciDeviceInfo,
        IntelGpuDiagnosticsInfo,
        KernelWarning,
        NvidiaGpuDiagnosticsInfo,
    )

    diag = _diag(
        gpu=GpuDiagnosticsInfo(
            pci_bdf="0000:03:00.0",
            model_name="RX 9070 XT",
            fan_control_method="pmfw_curve",
            kernel_warnings=[KernelWarning(id="w1", severity="high", message="known regression")],
        ),
        intel_gpu=IntelGpuDiagnosticsInfo(
            pci_bdf="0000:04:00.0", model_name="Arc", fan_control_method="none"
        ),
        nvidia_gpu=NvidiaGpuDiagnosticsInfo(
            pci_bdf="0000:06:00.0", model_name="RTX", driver="nouveau"
        ),
        amd_pci_devices=[
            AmdPciDeviceInfo(pci_bdf="0000:05:00.0", amdgpu_bound=False, driver="vfio-pci")
        ],
    )
    labels = [r.label for r in build_safety_gpu_vm(diag).gpu_rows]
    assert any("Advisory" in label for label in labels)
    assert any("Intel" in label for label in labels)
    assert any("NVIDIA" in label for label in labels)
    assert any("0000:05:00.0" in label for label in labels)  # unbound AMD device


def test_registry_chip_tooltip_from_guidance():
    diag = _diag(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="it8696", expected_driver="it87", header_count=5)
            ],
            total_headers=5,
            writable_headers=5,
        )
    )
    chip = next(r for r in build_registry_rows(diag) if r.kind == "chip")
    assert chip.tooltip  # the it87 family has BIOS tips / known issues in the guidance DB


def test_build_system_state_vm_counts_and_labels():
    # This fixture has no chips_detected, so it trips acpi + bios_revert +
    # no_chips = 3 warn-level problems.
    vm = build_system_state_vm(_diag_acpi_and_revert())
    assert vm.issues_requiring_attention == 3
    assert vm.issue_count_label == "3 ISSUES REQUIRE ATTENTION"
    assert vm.issue_count_state == "warn"
    assert vm.summary_line.startswith("1 PWM header")

    healthy = build_system_state_vm(_healthy_diag())
    assert healthy.issues_requiring_attention == 0
    assert healthy.issue_count_label == "SYSTEM READY"
    assert healthy.issue_count_state == "ok"
    assert healthy.verdict_state == "ok"


def test_build_system_state_vm_critical_issue_count_state():
    # B5: a critical-severity problem (bios pwm_enable reclaim ≥ _RECLAIM_HIGH=10)
    # must drive issue_count_state to "crit". The existing test only exercises the
    # "warn" and "ok" arms, leaving the crit branch dead.
    vm = build_system_state_vm(_diag_with_revert("pwm1", 10))
    assert vm.issue_count_state == "crit"


def test_thermal_state_maps_cover_the_wire_vocabulary():
    """Every surface that renders `thermal_state` must handle every value.

    DEC-257. Three maps key off this one field, in three modules, and one had
    silently drifted: it carried three values the daemon never sends and was
    missing `recovery` and `no_sensor_fallback` — the two that mean the daemon is
    actively forcing fans. Both fell through to a neutral grey pill, so a live
    thermal recovery rendered as "nothing happening".

    They cannot be collapsed into a single map (they map to different things, and
    one lives behind a Qt import while two are deliberately Qt-free), so this
    pins all of them against the wire vocabulary instead. A new map is one line
    away from the same guarantee.
    """
    from control_ofc.api.models import THERMAL_STATE_VALUES
    from control_ofc.services.dashboard_view import _THERMAL_REASONS
    from control_ofc.services.system_state_view import _THERMAL_STATE
    from control_ofc.ui.status_banner import THERMAL_STATES

    wire = set(THERMAL_STATE_VALUES)
    for name, mapping in (
        ("status_banner.THERMAL_STATES", THERMAL_STATES),
        ("dashboard_view._THERMAL_REASONS", _THERMAL_REASONS),
        ("system_state_view._THERMAL_STATE", _THERMAL_STATE),
    ):
        keys = set(mapping)
        assert not wire - keys, (
            f"{name} is missing daemon states {sorted(wire - keys)} — they will "
            "render as an unstyled fallback while the daemon forces fans"
        )
        assert not keys - wire, (
            f"{name} carries {sorted(keys - wire)}, which no daemon emits; "
            "invented keys hide the fact that a real one is unhandled"
        )
