"""`WIRE-ag` — board voltage rails: wire → model → view-model → Hardware page.

The register row was "the daemon reads no voltages at all". The risk in fixing
it is not that a number fails to appear, it is that the **wrong kind** of number
appears with unearned authority: 7 of 10 channels on the reference board are
unlabelled raw ADC pins whose reading is not the rail voltage, because boards
divide rails through resistor networks the driver knows nothing about.

So every test here asserts the *distinction* survives each layer, as a
relationship between the two cases rather than as a literal — a call site that
hardcoded either answer, or dropped the flag, must fail.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTableWidget

from control_ofc.api.models import (
    ConnectionState,
    HardwareDiagnosticsResult,
    VoltageRail,
    parse_hardware_diagnostics,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.hardware_view import build_voltage_panel
from control_ofc.ui.pages.hardware_page import (
    _VOLT_CHIP,
    _VOLT_IDENT,
    _VOLT_NAME,
    _VOLT_VALUE,
    HardwarePage,
)

# The reference host's real shape (Gigabyte X870E AORUS MASTER, it8696):
# ten channels, three of which the driver names.
_WIRE_RAILS = [
    {
        "id": "hwmon:it8696:it87.2624:in0",
        "chip_name": "it8696",
        "channel": 0,
        "label": "in0",
        "value_v": 1.236,
        "identified": False,
    },
    {
        "id": "hwmon:it8696:it87.2624:in7",
        "chip_name": "it8696",
        "channel": 7,
        "label": "3VSB",
        "value_v": 3.288,
        "identified": True,
    },
]


def _rails() -> list[VoltageRail]:
    return parse_hardware_diagnostics({"voltages": _WIRE_RAILS}).voltages


# ── Model ────────────────────────────────────────────────────────────────


def test_rails_parse_off_the_wire_with_their_identification_intact():
    rails = _rails()
    assert len(rails) == 2
    raw = next(r for r in rails if r.channel == 0)
    named = next(r for r in rails if r.channel == 7)
    assert raw.label == "in0"
    assert named.label == "3VSB"
    assert named.value_v == 3.288
    # The relationship, not two literals: a parser that hardcoded either answer
    # (or dropped the key) collapses these to the same value.
    assert raw.identified != named.identified
    assert named.identified and not raw.identified


def test_an_older_daemon_sending_no_rails_yields_an_empty_list_not_an_error():
    # The daemon omits the key entirely when it has nothing (skip_serializing_if),
    # so absence is the normal case, not a malformed response.
    assert parse_hardware_diagnostics({}).voltages == []


def test_identified_defaults_false_when_the_flag_is_missing():
    """The safe direction: an entry that omits the flag must not be presented as
    a named rail, because ``identified`` is precisely the claim being withheld."""
    rails = parse_hardware_diagnostics(
        {"voltages": [{"id": "x", "chip_name": "it8696", "channel": 1, "value_v": 2.0}]}
    ).voltages
    assert len(rails) == 1
    assert rails[0].identified is False


def test_a_malformed_rail_is_dropped_without_taking_its_siblings():
    rails = parse_hardware_diagnostics(
        {"voltages": [{"id": "bad", "value_v": "not-a-number"}, _WIRE_RAILS[1]]}
    ).voltages
    assert [r.channel for r in rails] == [7]


# ── View-model ───────────────────────────────────────────────────────────


def test_panel_carries_the_distinction_and_counts_only_identified_rails():
    panel = build_voltage_panel(_rails())
    assert panel.has_rails
    assert panel.summary_text == "2 channels · 1 identified"
    raw = next(r for r in panel.rows if r.name == "in0")
    named = next(r for r in panel.rows if r.name == "3VSB")
    assert named.value_text == "3.288 V"
    assert raw.identified != named.identified
    # Only the unidentified row explains itself; a caveat on both would be noise
    # and a caveat on neither is the defect.
    assert raw.caveat and not named.caveat


def test_footnote_appears_only_while_some_channel_is_unnamed():
    mixed = build_voltage_panel(_rails())
    all_named = build_voltage_panel([r for r in _rails() if r.identified])
    assert mixed.footnote
    assert not all_named.footnote
    # Precondition: the two panels really do differ in the way under test,
    # otherwise this asserts nothing.
    assert len(all_named.rows) < len(mixed.rows)


def test_a_populated_panel_says_the_readings_are_a_connect_time_snapshot():
    """The GUI fetches `/diagnostics/hardware` once per connection, so a panel
    that said nothing would let a user read hours-old millivolts as current."""
    panel = build_voltage_panel(_rails())
    assert "connected" in panel.provenance_text
    # And the empty panel must not claim a provenance it does not have.
    assert build_voltage_panel([]).provenance_text == ""


def test_rows_are_ordered_by_chip_then_numeric_channel():
    """`in10` sorts before `in2` as a string, so the sample must span ten —
    single-digit channels pass under either rule and prove nothing."""
    rails = [
        VoltageRail(id="b", chip_name="nct6799", channel=10, label="in10", value_v=1.0),
        VoltageRail(id="a", chip_name="nct6799", channel=2, label="in2", value_v=2.0),
        VoltageRail(id="c", chip_name="it8696", channel=1, label="in1", value_v=3.0),
    ]
    panel = build_voltage_panel(rails)
    assert [(r.chip, r.name) for r in panel.rows] == [
        ("it8696", "in1"),
        ("nct6799", "in2"),
        ("nct6799", "in10"),
    ]


def test_the_row_vm_carries_no_unrendered_id():
    """`WIRE-ak`: a VM field no renderer reads is invisible decoration, and
    having it in the type is what hides the gap."""
    row = build_voltage_panel(_rails()).rows[0]
    assert not hasattr(row, "id")


def test_empty_panel_explains_itself_rather_than_rendering_an_empty_table():
    panel = build_voltage_panel([])
    assert not panel.has_rails
    assert panel.rows == ()
    assert "2.37.0" in panel.empty_note


# ── Call site (the Hardware page) ────────────────────────────────────────


def _page(qtbot, diag_result):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    diag = DiagnosticsService(state)
    diag.last_hw_diagnostics = diag_result
    page = HardwarePage(state=state, diagnostics_service=diag, client=None)
    qtbot.addWidget(page)
    return page


def test_the_page_renders_a_row_per_rail_from_the_cached_diagnostics(qtbot):
    """Test the wiring, not just the builder.

    ``CLAUDE.md`` records twelve recurrences of *extracting a rule does not test
    the call site*: the view-model tests above pass even if the page never calls
    ``build_voltage_panel``.
    """
    rails = _rails()
    page = _page(qtbot, HardwareDiagnosticsResult(voltages=rails))
    page._render_voltages()

    table = page.findChild(QTableWidget, "Hardware_Table_voltages")
    assert table is not None
    assert table.rowCount() == len(rails)

    by_name = {table.item(i, _VOLT_NAME).text(): i for i in range(table.rowCount())}
    assert set(by_name) == {"in0", "3VSB"}

    for rail in rails:
        row = by_name[rail.label]
        assert table.item(row, _VOLT_CHIP).text() == rail.chip_name
        # Asserted against the rail, not a hardcoded string: a call site reading
        # the wrong field, or rounding differently, fails here.
        assert table.item(row, _VOLT_VALUE).text() == f"{rail.value_v:.3f} V"
        # And the honesty column must track the flag in BOTH directions — with
        # only the positive case a stuck predicate passes.
        expected = "Identified rail" if rail.identified else "Unnamed channel"
        assert table.item(row, _VOLT_IDENT).text() == expected


def test_only_the_unnamed_channel_carries_the_caveat_tooltip(qtbot):
    page = _page(qtbot, HardwareDiagnosticsResult(voltages=_rails()))
    page._render_voltages()
    table = page.findChild(QTableWidget, "Hardware_Table_voltages")

    tips = {
        table.item(i, _VOLT_NAME).text(): table.item(i, _VOLT_NAME).toolTip()
        for i in range(table.rowCount())
    }
    assert tips["3VSB"] == ""
    assert "not a known rail" in tips["in0"]


def test_the_page_shows_the_empty_note_and_no_table_when_no_rails_are_known(qtbot):
    page = _page(qtbot, HardwareDiagnosticsResult(voltages=[]))
    page._render_voltages()
    assert page.findChild(QTableWidget, "Hardware_Table_voltages") is None


def test_a_page_with_no_diagnostics_yet_renders_the_empty_note_not_a_crash(qtbot):
    """The snapshot lands one poll after connect, so "not fetched yet" is a real
    state the page is opened in, not a defensive hypothetical."""
    page = _page(qtbot, None)
    page._render_voltages()
    assert page.findChild(QTableWidget, "Hardware_Table_voltages") is None


def test_showing_the_page_after_diagnostics_land_populates_the_panel(qtbot):
    """The bug this prevents: the panel is built before the first poll delivers
    diagnostics, so rendering only at construction leaves it empty all session."""
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    diag = DiagnosticsService(state)
    page = HardwarePage(state=state, diagnostics_service=diag, client=None)
    qtbot.addWidget(page)

    # Precondition: nothing to show yet, so a later pass is what must fill it.
    page._render_voltages()
    assert page.findChild(QTableWidget, "Hardware_Table_voltages") is None

    diag.last_hw_diagnostics = HardwareDiagnosticsResult(voltages=_rails())
    page.show()

    table = page.findChild(QTableWidget, "Hardware_Table_voltages")
    assert table is not None
    assert table.rowCount() == 2
