"""Tests for the Super-I/O detection view (DEC-202, P3).

View-only: construct the model directly and assert rendered outcomes (objectName,
class property, PlainText, text) — no daemon client.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from control_ofc.api.models import SuperIoChip, SuperIoRecommendation, SuperIoReport
from control_ofc.ui.widgets.superio_view import SuperIoView


def _unbound_chip() -> SuperIoChip:
    return SuperIoChip(
        chip_name="it8688",
        vendor="ite",
        evidence=["dmi_board_table"],
        confidence="medium",
        expected_module="it87",
        module_loaded=False,
        hwmon_present=False,
        recommendation=SuperIoRecommendation(
            module="it87",
            in_mainline=False,
            load_hint="install it87-dkms-git, then `sudo modprobe it87`",
            reason="board lists it8688 but no it87 driver is bound",
            risk_notes=["needs the out-of-tree DKMS driver"],
        ),
    )


def test_unbound_chip_renders_recommendation(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    report = SuperIoReport(
        arch_supported=True,
        chips=[_unbound_chip()],
        notes=["detection proves a chip is present, not that fan control is available"],
    )
    view.set_report(report)

    summary = view.findChild(QLabel, "Superio_Label_summary")
    assert summary is not None and not summary.isHidden()
    assert "need a driver loaded" in summary.text()
    assert summary.property("class") == "WarningChip"

    card = view.findChild(QWidget, "Superio_ChipCard_it8688")
    assert card is not None

    status = view.findChild(QLabel, "Superio_ChipStatus_it8688")
    assert status.text() == "no driver loaded"
    assert status.property("class") == "WarningChip"

    hint = view.findChild(QLabel, "Superio_LoadHint_it8688")
    assert hint is not None and "it87-dkms-git" in hint.text()
    # Daemon-supplied string must be PlainText (defence against markup injection).
    assert hint.textFormat() == Qt.TextFormat.PlainText

    mainline = view.findChild(QLabel, "Superio_Mainline_it8688")
    assert mainline.text() == "needs out-of-tree (DKMS) driver"
    assert mainline.property("class") == "CautionChip"

    risk = view.findChild(QLabel, "Superio_Risk_it8688_0")
    assert risk is not None and "DKMS" in risk.text()
    assert risk.property("class") == "WarningChip"

    # The report-level "present != control" note is surfaced.
    notes = view.findChild(QLabel, "Superio_Label_notes")
    assert not notes.isHidden()
    assert "not that fan control" in notes.text()


def test_all_bound_shows_success_summary(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    report = SuperIoReport(
        arch_supported=True,
        chips=[
            SuperIoChip(
                chip_name="nct6799",
                vendor="nuvoton",
                confidence="high",
                expected_module="nct6775",
                bound_driver="nct6775",
                module_loaded=True,
                hwmon_present=True,
            )
        ],
    )
    view.set_report(report)
    summary = view.findChild(QLabel, "Superio_Label_summary")
    assert "have a driver bound" in summary.text()
    assert summary.property("class") == "SuccessChip"
    status = view.findChild(QLabel, "Superio_ChipStatus_nct6799")
    assert status.text() == "driver bound"
    assert status.property("class") == "SuccessChip"
    # A bound chip has no recommendation section.
    assert view.findChild(QWidget, "Superio_Section_nct6799") is None
    # No notes and no ACPI conflicts → the notes label stays hidden.
    assert view.findChild(QLabel, "Superio_Label_notes").isHidden()


def test_mainline_chip_shows_success_not_dkms(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    report = SuperIoReport(
        arch_supported=True,
        chips=[
            SuperIoChip(
                chip_name="it8628",
                vendor="ite",
                confidence="medium",
                expected_module="it87",
                hwmon_present=False,
                recommendation=SuperIoRecommendation(
                    module="it87",
                    in_mainline=True,
                    load_hint="sudo modprobe it87",
                    reason="board lists it8628",
                ),
            )
        ],
    )
    view.set_report(report)
    mainline = view.findChild(QLabel, "Superio_Mainline_it8628")
    assert mainline.text() == "in mainline kernel"
    assert mainline.property("class") == "SuccessChip"


def test_caveats_are_rendered(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    report = SuperIoReport(
        arch_supported=True,
        chips=[SuperIoChip(chip_name="xyz9000", caveats=["unrecognized Super-I/O chip"])],
    )
    view.set_report(report)
    caveat = view.findChild(QLabel, "Superio_Caveat_xyz9000_0")
    assert caveat is not None
    assert "unrecognized" in caveat.text()
    assert caveat.textFormat() == Qt.TextFormat.PlainText


def test_set_error_shows_message(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    view.set_report(SuperIoReport(arch_supported=True, chips=[_unbound_chip()]))
    view.set_error("Cannot detect Super-I/O: boom")
    status = view.findChild(QLabel, "Superio_Label_status")
    assert not status.isHidden() and "boom" in status.text()
    # set_error clears any prior cards.
    assert view.findChild(QWidget, "Superio_ChipCard_it8688") is None


def test_unbound_chips_sort_before_bound(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    bound = SuperIoChip(chip_name="nct6799", expected_module="nct6775", hwmon_present=True)
    report = SuperIoReport(arch_supported=True, chips=[bound, _unbound_chip()])
    view.set_report(report)
    cards = [
        w.objectName()
        for w in view.findChildren(QWidget)
        if w.objectName().startswith("Superio_ChipCard_")
    ]
    # The unbound (actionable) chip's card is created first.
    assert cards.index("Superio_ChipCard_it8688") < cards.index("Superio_ChipCard_nct6799")


def test_acpi_conflict_surfaced_in_notes(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    report = SuperIoReport(
        arch_supported=True,
        chips=[_unbound_chip()],
        acpi_conflict_drivers=["it87"],
    )
    view.set_report(report)
    notes = view.findChild(QLabel, "Superio_Label_notes")
    assert not notes.isHidden()
    assert "ACPI" in notes.text() and "it87" in notes.text()


def test_non_x86_shows_unsupported_arch(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    view.set_report(SuperIoReport(arch_supported=False))
    status = view.findChild(QLabel, "Superio_Label_status")
    assert not status.isHidden() and "x86" in status.text()
    assert view.findChild(QLabel, "Superio_Label_summary").isHidden()


def test_unsupported_daemon_state(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    view.set_unsupported()
    status = view.findChild(QLabel, "Superio_Label_status")
    assert not status.isHidden() and "predates this feature" in status.text()


def test_empty_report_shows_none_detected(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    view.set_report(SuperIoReport(arch_supported=True, chips=[]))
    status = view.findChild(QLabel, "Superio_Label_status")
    assert not status.isHidden() and "No motherboard Super-I/O chip" in status.text()


def test_set_report_clears_previous_cards(qtbot):
    view = SuperIoView()
    qtbot.addWidget(view)
    view.set_report(SuperIoReport(arch_supported=True, chips=[_unbound_chip()]))
    assert view.findChild(QWidget, "Superio_ChipCard_it8688") is not None
    # A second render with different chips must not leave stale cards.
    view.set_report(
        SuperIoReport(
            arch_supported=True,
            chips=[SuperIoChip(chip_name="nct6799", hwmon_present=True)],
        )
    )
    assert view.findChild(QWidget, "Superio_ChipCard_it8688") is None
    assert view.findChild(QWidget, "Superio_ChipCard_nct6799") is not None
