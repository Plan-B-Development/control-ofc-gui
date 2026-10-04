"""`BRD-j` (decision `U13`): a missing chip that carries no fan header.

The daemon's board table marks, per row, the chips that carry no fan header
(`expected_fanless_chips`, daemon >= 3.7.0 — today the B450 AORUS PRO's
IT8792E). A missing one costs temperatures and voltages, so the dual-chip alert
must not call it "missing PWM headers". Where the firmware declares more fan
headers than were found (`board_firmware_counts`), that measured deficit wins.
"""

from __future__ import annotations

from control_ofc.api.models import (
    BoardFirmwareCounts,
    BoardInfo,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    parse_hardware_diagnostics,
)
from control_ofc.ui.hwmon_guidance import dual_chip_verify_hint, dual_chip_warning_html

_B450 = "B450 AORUS PRO WIFI"
_PAIR = ["it8686", "it8792"]
_LOST = "missing PWM headers"
_KEPT = "fan headers unaffected"


def _diag(
    fanless: list[str],
    firmware: BoardFirmwareCounts | None = None,
    total_headers: int = 5,
) -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[HwmonChipInfo(chip_name="it8686")],
            total_headers=total_headers,
        ),
        board=BoardInfo(vendor="Gigabyte", name=_B450),
        expected_chips=list(_PAIR),
        expected_fanless_chips=fanless,
        board_firmware_counts=firmware,
    )


def test_fanless_chips_parse_as_a_list_and_default_to_none_claimed():
    assert parse_hardware_diagnostics({}).expected_fanless_chips == []
    assert parse_hardware_diagnostics(
        {"expected_chips": _PAIR, "expected_fanless_chips": ["it8792"]}
    ).expected_fanless_chips == ["it8792"]
    # A malformed value must not be iterated into a claim: this field only
    # ever *removes* the fan-loss wording, so the safe reading is "none".
    assert (
        parse_hardware_diagnostics({"expected_fanless_chips": "it8792"}).expected_fanless_chips
        == []
    )


def test_a_missing_fanless_chip_is_not_called_missing_pwm_headers():
    kept = dual_chip_warning_html(_B450, _PAIR, ["it8686"], fanless_chips=["it8792"])
    assert kept is not None
    assert _KEPT in kept
    assert _LOST not in kept
    assert "carries no fan header" in kept
    # The recovery ladder stays — it brings the readings back.
    assert "at the wall" in kept

    # Opposite branch: the same board without the claim (an older daemon, or a
    # chip not known to be fanless) keeps the wording it always had.
    lost = dual_chip_warning_html(_B450, _PAIR, ["it8686"], fanless_chips=[])
    assert lost is not None
    assert _LOST in lost
    assert _KEPT not in lost


def test_one_fanless_chip_does_not_clear_another_missing_chip():
    html = dual_chip_warning_html(_B450, _PAIR, [], fanless_chips=["it8792"])
    assert html is not None
    assert _LOST in html, "it8686 is missing too and carries the fans"


def test_the_firmware_count_decides_only_the_loss_direction():
    # A measured deficit beats a curated "fanless".
    html = dual_chip_warning_html(
        _B450,
        _PAIR,
        ["it8686"],
        fanless_chips=["it8792"],
        firmware_fan_count=8,
        reachable_fan_count=5,
    )
    assert html is not None and _LOST in html

    # A matching or lower count cannot clear the fans: `total_headers` counts
    # every PWM chip, an AIO or USB fan controller included, and the driver
    # accepts a zero count. With no curated claim the old wording stands.
    for firmware in (5, 4, 0):
        html = dual_chip_warning_html(
            "X870E AORUS MASTER",
            ["it8696", "it87952"],
            ["it8696"],
            fanless_chips=[],
            firmware_fan_count=firmware,
            reachable_fan_count=5,
        )
        assert html is not None
        assert _LOST in html and "No fan header is lost" not in html, firmware

    # ...and with one, the board table clears them.
    html = dual_chip_warning_html(
        _B450,
        _PAIR,
        ["it8686"],
        fanless_chips=["it8792"],
        firmware_fan_count=5,
        reachable_fan_count=5,
    )
    assert html is not None and _KEPT in html


def test_the_issue_card_and_the_condition_both_read_the_fanless_chips():
    """The wiring: both consumers gather the field, not just the helper."""
    from control_ofc.services.system_state_view import _issue_card_from_problem
    from control_ofc.ui.widgets.readiness_report import _base_conditions

    def condition(diag: HardwareDiagnosticsResult) -> dict:
        found = [c for c in _base_conditions(diag) if c["key"] == "dual_chip"]
        assert len(found) == 1
        return found[0]

    fanless, plain = _diag(["it8792"]), _diag([])

    kept = condition(fanless)
    lost = condition(plain)
    assert kept["fix"].startswith("Every fan header is unaffected")
    assert not lost["fix"].startswith("Every fan header is unaffected")
    # Still a condition with a fix, so it keeps its tier (see readiness_report).
    assert kept["severity"] == lost["severity"] == "warn"

    kept_card = _issue_card_from_problem(fanless, kept)
    lost_card = _issue_card_from_problem(plain, lost)
    assert kept_card.detail is not None and _KEPT in kept_card.detail
    assert lost_card.detail is not None and _LOST in lost_card.detail

    # The firmware count reaches the condition too, and outranks the table.
    measured = condition(_diag(["it8792"], BoardFirmwareCounts(platform=1, fan_count=8)))
    assert not measured["fix"].startswith("Every fan header is unaffected")


def test_the_verify_hint_is_not_offered_for_a_fanless_missing_chip():
    assert dual_chip_verify_hint("no_rpm_effect", _PAIR, ["it8686"], []) is not None
    assert dual_chip_verify_hint("no_rpm_effect", _PAIR, ["it8686"], ["it8792"]) is None


def test_the_verify_view_passes_the_fanless_chips():
    from control_ofc.api.models import HwmonVerifyResult
    from control_ofc.services.verify_view import build_verify_result_view

    result = HwmonVerifyResult(header_id="hwmon:it8686:isa-0a40:pwm1", result="no_rpm_effect")

    def hint_shown(diag: HardwareDiagnosticsResult) -> bool:
        view = build_verify_result_view(result, diagnostics=diag)
        return any("dual-chip notice" in line for line in view.lines)

    assert hint_shown(_diag([]))
    assert not hint_shown(_diag(["it8792"]))
    # The firmware's deficit reaches the hint too, as it does the card.
    assert hint_shown(_diag(["it8792"], BoardFirmwareCounts(platform=1, fan_count=8)))
