"""G6 — the daemon published the evidence for a verdict already on screen.

Three rows, one theme (`WIRE-u`, `WIRE-v`, `WIRE-w`): the GUI showed a
conclusion and discarded the observation the daemon sent to justify it. `WIRE-f`
(a verify evidence panel) is the fourth and was split out to `/ofc:new-feature`
as feature work.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    AmdPciDeviceInfo,
    CharacterizationRun,
    CharPoint,
    GpuDiagnosticsInfo,
    HardwareDiagnosticsResult,
    SuperIoChip,
    SuperIoReport,
)
from control_ofc.services.characterization_view import (
    build_characterization_view,
    restore_note,
)
from control_ofc.services.hardware_view import build_superio_panel
from control_ofc.services.system_state_view import build_safety_gpu_vm

# ── WIRE-v: the amdgpu diagnostic trio ───────────────────────────────────────


def _diag(*, module_loaded: bool) -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        amd_pci_devices=[AmdPciDeviceInfo(pci_bdf="0000:03:00.0", amdgpu_bound=False)],
        amdgpu_module_loaded=module_loaded,
    )


def _texts(diag) -> str:
    return " | ".join(f"{r.label}: {r.value}" for r in build_safety_gpu_vm(diag).gpu_rows)


def test_module_loaded_but_unbound_reads_as_a_bind_failure() -> None:
    text = _texts(_diag(module_loaded=True))
    assert "bind failure" in text
    assert "blacklisted" not in text


def test_module_not_loaded_reads_as_blacklisted_or_missing() -> None:
    """The opposite branch. Without it, a single hardcoded string passes above,
    and the whole point of the row is that one wording cannot serve both — they
    are different problems with different fixes.
    """
    text = _texts(_diag(module_loaded=False))
    assert "not loaded" in text
    assert "bind failure" not in text


def test_the_two_cases_actually_differ() -> None:
    """Precondition: the wording moved. An assertion pair can both pass against
    a string that happens to contain neither token."""
    assert _texts(_diag(module_loaded=True)) != _texts(_diag(module_loaded=False))


def test_a_bound_device_produces_no_row() -> None:
    diag = HardwareDiagnosticsResult(
        amd_pci_devices=[AmdPciDeviceInfo(pci_bdf="0000:03:00.0", amdgpu_bound=True)],
        amdgpu_module_loaded=True,
    )
    assert "0000:03:00.0" not in _texts(diag)


def test_an_unbound_driver_on_a_present_gpu_is_surfaced() -> None:
    """The third of the trio, and the one with zero references before this."""
    diag = HardwareDiagnosticsResult(gpu=GpuDiagnosticsInfo(amdgpu_driver_bound=False))
    assert "amdgpu binding" in _texts(diag)


def test_a_bound_driver_says_nothing() -> None:
    """It defaults True, so an unconditional row would fire on every machine."""
    diag = HardwareDiagnosticsResult(gpu=GpuDiagnosticsInfo(amdgpu_driver_bound=True))
    assert "amdgpu binding" not in _texts(diag)


# ── WIRE-w: Super-I/O evidence ───────────────────────────────────────────────


def _panel(evidence: list[str]):
    report = SuperIoReport(
        arch_supported=True,
        chips=[SuperIoChip(chip_name="it8696", evidence=evidence, confidence="high")],
    )
    return build_superio_panel(report)


def test_a_port_probe_finding_says_it_came_from_the_probe() -> None:
    """The probe is opt-in and touches hardware; nothing on screen previously
    said which chip it actually found."""
    (row,) = _panel(["port_probe"]).rows
    assert "port probe" in row.evidence_text


def test_evidence_sources_are_all_rendered_in_order() -> None:
    (row,) = _panel(["dmi_board_table", "kernel_log", "bound_hwmon"]).rows
    assert row.evidence_text == "board table, kernel log, bound driver"


def test_an_unrecognised_evidence_token_is_rendered_not_dropped() -> None:
    """273-i: a newer daemon may add a source, and dropping it would silently
    understate how the chip was found."""
    (row,) = _panel(["acpi_table"]).rows
    assert "acpi_table" in row.evidence_text


def test_no_evidence_renders_empty_rather_than_inventing_one() -> None:
    (row,) = _panel([]).rows
    assert row.evidence_text == ""


def test_the_evidence_column_is_populated_in_the_table(qtbot, qapp) -> None:
    """The call site, not the view model — the VM can be right while the table
    addresses the wrong column, which is what the index constants now prevent.
    """
    from control_ofc.ui.pages.hardware_page import _SIO_EVIDENCE, HardwarePage

    page = HardwarePage(state=None, client=None)
    qtbot.addWidget(page)
    page._render_superio(_panel(["port_probe"]))
    from PySide6.QtWidgets import QWidget

    table = page.findChild(QWidget, "Hardware_Table_superio")
    assert table is not None
    assert "port probe" in table.item(0, _SIO_EVIDENCE).text()


# ── WIRE-u: characterisation evidence ────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(0, "no control"), (1, "manual"), (2, "automatic"), (5, "mode 5")],
)
def test_each_point_reports_its_pwm_enable_mode(mode: int, expected: str) -> None:
    run = CharacterizationRun(points=[CharPoint(requested_pct=40, pwm_enable=mode)])
    (row,) = build_characterization_view(run, header_label="x").rows
    assert row.control_mode == expected


def test_an_unreported_mode_is_not_rendered_as_no_control() -> None:
    """``None`` means "the daemon did not say"; 0 means "no control". Collapsing
    them would assert interference that was never observed."""
    run = CharacterizationRun(points=[CharPoint(requested_pct=40, pwm_enable=None)])
    (row,) = build_characterization_view(run, header_label="x").rows
    assert row.control_mode != "no control"
    assert row.control_mode == "—"


def test_a_failed_restore_names_the_duty_to_put_the_header_back_to() -> None:
    note = restore_note(
        CharacterizationRun(restore_failed=True, restore_outcome="write_failed", original_pct=45)
    )
    assert "45%" in note
    # The per-reason advice must survive the addition.
    assert "Re-activate your profile" in note


def test_the_thermal_force_note_keeps_its_own_advice() -> None:
    """`AUD2-c`: under a thermal force "re-activate your profile" is the one
    thing the user must not do, so the appended duty must not overwrite it."""
    note = restore_note(
        CharacterizationRun(
            restore_failed=True, restore_outcome="skipped_thermal_force", original_pct=45
        )
    )
    assert "Thermal safety is forcing fan output" in note
    assert "Re-activate your profile" not in note
    assert "45%" in note


def test_no_original_duty_does_not_claim_one() -> None:
    """Claiming a figure here would contradict the note it is appended to."""
    note = restore_note(
        CharacterizationRun(
            restore_failed=True, restore_outcome="no_original_duty", original_pct=None
        )
    )
    assert "nothing to restore it to" in note
    assert "%" not in note.split("speed could not be read")[-1].split(".")[0]


def test_a_successful_restore_still_says_nothing() -> None:
    assert restore_note(CharacterizationRun(restore_outcome="restored")) == ""
