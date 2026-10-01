"""`VOLT-b` / DEC-464 — board-catalogue rail names: wire → model → view-model → page.

The daemon now names unlabelled ADC inputs from the it87 project's Gigabyte
catalogue, with each board's divider, and marks inputs the board's config does
not map. The risks are the same ones `WIRE-ag` guarded, one layer on:

- a catalogue name shown beside the **unscaled** pin voltage (a "+12V" rail
  reading 1.992 V) — so a name never survives without its multiplier;
- an unmapped input presented as more than it is — upstream's `ignore` means
  "not mapped by this configuration", not "unconnected" (the review finding
  behind DEC-464's wording), so the row keeps its pin reading, claims no rail,
  and claims nothing about wiring;
- a catalogue entry overriding the driver's own label — so the driver wins.

Assertions are relationships against the rail where possible, so a call site
reading the wrong field fails.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QTableWidget

from control_ofc.api.models import (
    ConnectionState,
    HardwareDiagnosticsResult,
    parse_hardware_diagnostics,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.hardware_view import RailKind, build_voltage_panel
from control_ofc.ui.pages.hardware_page import (
    _VOLT_IDENT,
    _VOLT_NAME,
    _VOLT_VALUE,
    HardwarePage,
)


def _wire(channel: int, value_v: float, **extra) -> dict:
    return {
        "id": f"hwmon:it8696:it87.2624:in{channel}",
        "chip_name": "it8696",
        "channel": channel,
        "label": extra.pop("label", f"in{channel}"),
        "value_v": value_v,
        "identified": extra.pop("identified", False),
        **extra,
    }


#: The reference board as a current daemon reports it: one input of each kind.
_BOARD = [
    _wire(2, 1.992, board_label="+12V", board_multiplier=6.0),
    _wire(4, 1.236, board_label="CPU VCORE SOC", board_multiplier=1.0),
    _wire(0, 1.804, board_unmapped=True),
    _wire(7, 3.288, label="3VSB", identified=True),
    _wire(5, 1.116),
]


def _rails(entries=None):
    return parse_hardware_diagnostics({"voltages": entries or _BOARD}).voltages


def _panel(rails):
    return build_voltage_panel(rails, read_at=None, now=0.0, refresh_error="")


def _row(panel, chip_channel_name: str):
    return next(r for r in panel.rows if r.name == chip_channel_name)


# ── Model ────────────────────────────────────────────────────────────────


def test_catalogue_fields_parse_off_the_wire():
    rails = {r.channel: r for r in _rails()}
    assert rails[2].board_label == "+12V"
    assert rails[2].board_multiplier == 6.0
    assert rails[0].board_unmapped is True
    assert rails[0].board_label == ""
    # The driver's own fields are untouched: the pin value stays the pin value.
    assert rails[2].value_v == 1.992
    assert rails[2].label == "in2"


def test_an_older_daemon_without_the_fields_parses_exactly_as_before():
    (rail,) = _rails([_wire(2, 1.992)])
    assert (rail.board_label, rail.board_multiplier, rail.board_unmapped) == ("", None, False)


@pytest.mark.parametrize("bad", [None, "six", float("nan"), float("inf"), 0, -6.0])
def test_a_name_without_a_usable_multiplier_is_dropped_with_it(bad):
    """Both or neither: a named rail at its unscaled pin voltage is the lie."""
    entry = _wire(2, 1.992, board_label="+12V")
    if bad is not None:
        entry["board_multiplier"] = bad
    (rail,) = _rails([entry])
    assert rail.board_label == ""
    assert rail.board_multiplier is None
    # Precondition for the arm that matters: the same entry WITH a good
    # multiplier keeps its name, so the drop above is the guard, not a parse miss.
    (good,) = _rails([_wire(2, 1.992, board_label="+12V", board_multiplier=6.0)])
    assert good.board_label == "+12V"


# ── View-model ───────────────────────────────────────────────────────────


def test_a_catalogue_row_shows_the_rail_voltage_and_says_how_it_got_it():
    rails = {r.channel: r for r in _rails()}
    row = _row(_panel(_rails()), "+12V")
    assert row.kind is RailKind.CATALOGUE
    # Relationship, not a literal: pin x multiplier, to the panel's precision.
    rail = rails[2]
    assert row.value_text == f"{rail.value_v * rail.board_multiplier:.3f} V"
    assert row.value_text != f"{rail.value_v:.3f} V", "the unscaled pin must not show"
    assert f"{rail.value_v:.3f} V" in row.caveat
    assert "\N{MULTIPLICATION SIGN} 6" in row.caveat
    assert row.identification_text == "Named by the board catalogue"


def test_a_catalogue_row_with_no_divider_says_it_reads_at_the_pin():
    row = _row(_panel(_rails()), "CPU VCORE SOC")
    assert row.value_text == "1.236 V"
    assert "directly at the pin" in row.caveat
    assert "\N{MULTIPLICATION SIGN}" not in row.caveat


def test_an_unmapped_row_keeps_its_pin_reading_and_claims_no_rail():
    rails = {r.channel: r for r in _rails()}
    row = next(r for r in _panel(_rails()).rows if r.kind is RailKind.UNMAPPED)
    assert row.name == "in0"
    assert row.value_text == f"{rails[0].value_v:.3f} V"
    assert "not a known rail" in row.caveat
    # It must not claim the input is unconnected — that is what upstream's
    # `ignore` does NOT say.
    assert "may be unconnected or unused" in row.caveat
    assert row.identification_text == "Not used by this board"


def test_the_driver_label_wins_over_a_catalogue_entry():
    """The daemon never sends both (DEC-464 Q2), so this is the client holding the
    line if one ever did: the driver's internal, already-scaled input must not be
    renamed or scaled a second time."""
    (rail,) = _rails(
        [_wire(7, 3.288, label="3VSB", identified=True, board_label="X", board_multiplier=2.0)]
    )
    (row,) = _panel([rail]).rows
    assert row.kind is RailKind.DRIVER
    assert (row.name, row.value_text, row.caveat) == ("3VSB", "3.288 V", "")


def test_unmapped_wins_over_a_catalogue_name_so_no_rail_is_claimed():
    (rail,) = _rails([_wire(0, 1.804, board_label="X", board_multiplier=2.0, board_unmapped=True)])
    (row,) = _panel([rail]).rows
    assert row.kind is RailKind.UNMAPPED
    assert (row.name, row.value_text) == ("in0", "1.804 V")


def test_the_summary_counts_every_kind():
    panel = _panel(_rails())
    assert panel.summary_text == (
        "5 channels · 1 named by the driver · 2 by the board catalogue · 1 not used"
    )


def test_the_footnotes_follow_the_kinds_present():
    raw_only = _panel(_rails([_wire(5, 1.116)])).footnote
    catalogue_only = _panel(
        _rails([_wire(2, 1.992, board_label="+12V", board_multiplier=6.0)])
    ).footnote
    both = _panel(_rails()).footnote
    driver_only = _panel(_rails([_wire(7, 3.288, label="3VSB", identified=True)])).footnote

    assert "input pin" in raw_only and "GPL-2.0" not in raw_only
    # A catalogue-named row is not "measured at the chip's input pin", so the
    # raw warning must not appear for it — and the credit must.
    assert "GPL-2.0" in catalogue_only and "input pin" not in catalogue_only
    assert "input pin" in both and "GPL-2.0" in both
    assert driver_only == ""


def test_an_unmapped_row_alone_gets_both_the_pin_warning_and_the_credit():
    """It shows an unscaled pin reading, so the raw warning applies; it came from
    the catalogue, so the credit does too."""
    footnote = _panel(_rails([_wire(0, 1.804, board_unmapped=True)])).footnote
    assert "input pin" in footnote
    assert "GPL-2.0" in footnote


# ── Page (the call site) ─────────────────────────────────────────────────


def _page(qtbot, diag_result):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    diag = DiagnosticsService(state)
    diag.last_hw_diagnostics = diag_result
    page = HardwarePage(state=state, diagnostics_service=diag, client=None)
    qtbot.addWidget(page)
    return page


def test_the_page_renders_each_kind_from_the_cached_diagnostics(qtbot):
    """The view-model tests above pass even if the page never shows the
    catalogue's name, scaled value, identification or caveat."""
    rails = _rails()
    page = _page(qtbot, HardwareDiagnosticsResult(voltages=rails))
    page._render_voltages()
    table = page.findChild(QTableWidget, "Hardware_Table_voltages")
    assert table is not None
    assert table.rowCount() == len(rails)

    cells = {
        table.item(i, _VOLT_NAME).text(): (
            table.item(i, _VOLT_VALUE).text(),
            table.item(i, _VOLT_IDENT).text(),
            table.item(i, _VOLT_VALUE).toolTip(),
        )
        for i in range(table.rowCount())
    }
    for row in _panel(rails).rows:
        assert cells[row.name] == (row.value_text, row.identification_text, row.caveat)
    # And the arms only a wired page can produce, against the wire values.
    twelve = next(r for r in rails if r.channel == 2)
    assert cells["+12V"][0] == f"{twelve.value_v * twelve.board_multiplier:.3f} V"
    unmapped = next(r for r in rails if r.channel == 0)
    assert cells["in0"][:2] == (f"{unmapped.value_v:.3f} V", "Not used by this board")
