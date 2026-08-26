"""DEC-234: consistent, discoverable section resize handles.

A `/frontend-design` pass gave every page's `QSplitter` one shared handle
treatment (a hairline that lights up in the accent on hover, in a comfortable
grab zone) and added the sections the user asked to be resizable:

* Overview — Fan Status ↕ Sensors (the top cards stay fixed above).
* System State — health overview ↕ hardware registry.
* Logs — event table ↕ diagnostic-snapshot cards (so the cards can be grown).

These are presentation-only structural facts; the polling / VM layers are
untouched, so the tests assert widget-tree outcomes, not behaviour.

DEC-284 adds the geometry half at the foot of the file: a handle is only as
useful as the height it has to trade, and both pages were handing their whole
surplus to a trailing spacer.
"""

from __future__ import annotations

import itertools

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QWidget,
)

from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.profile_service import ProfileService
from control_ofc.ui.components.cards import SectionHeader
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.pages.logs_page import LogsPage
from control_ofc.ui.pages.overview_page import OverviewPage
from control_ofc.ui.pages.system_state_page import SystemStatePage
from control_ofc.ui.qt_util import SPLITTER_HANDLE_WIDTH, style_splitter
from control_ofc.ui.theme import build_stylesheet, default_dark_theme

# ── the shared handle: helper + stylesheet ───────────────────────────────


def test_style_splitter_sets_shared_handle_width(qtbot):
    sp = QSplitter()
    qtbot.addWidget(sp)
    assert sp.handleWidth() != SPLITTER_HANDLE_WIDTH  # not our value by default
    style_splitter(sp)
    assert sp.handleWidth() == SPLITTER_HANDLE_WIDTH == 8


def test_stylesheet_defines_shared_splitter_handle():
    css = build_stylesheet(default_dark_theme())
    # The handle is styled at all (before DEC-234 it was Qt's invisible default).
    assert "QSplitter::handle {" in css
    assert "QSplitter::handle:horizontal" in css
    assert "QSplitter::handle:vertical" in css
    # Margins thin the 8px grab zone to a centred ~2px hairline per orientation;
    # dropping them would paint the full stripe instead of a divider.
    assert "margin: 2px 3px" in css  # horizontal splitter → vertical hairline
    assert "margin: 3px 2px" in css  # vertical splitter → horizontal hairline
    # Idle = the divider token; hover = the brand accent.
    t = default_dark_theme()
    idle = css[css.index("QSplitter::handle {") : css.index("QSplitter::handle:horizontal")]
    assert t.border_default in idle
    hover_at = css.index("QSplitter::handle:horizontal:hover")
    hover = css[hover_at : css.index("}", hover_at)]
    assert t.accent_primary in hover


# ── every splitter on every page uses the shared handle ──────────────────


def _build_paged_splitters(qtbot):
    """(pages, [(page-name, splitter)]) for every QSplitter the app builds.

    Returns the pages too so the caller can keep them referenced — dropping the
    page objects lets Python collect them and shiboken delete their C++ widgets,
    leaving the returned splitter handles dangling.
    """
    s2 = AppState()
    pages = [
        ("Dashboard", DashboardPage(state=AppState())),
        ("Controls", ControlsPage(state=AppState(), profile_service=ProfileService())),
        ("Logs", LogsPage(DiagnosticsService(AppState()))),
        ("Overview", OverviewPage(state=AppState())),
        ("SystemState", SystemStatePage(state=s2, diagnostics_service=DiagnosticsService(s2))),
    ]
    splitters = []
    for name, page in pages:
        qtbot.addWidget(page)
        splitters.extend((name, sp) for sp in page.findChildren(QSplitter))
    return pages, splitters


def test_every_splitter_uses_shared_handle_width(qtbot):
    """The heart of the consistency fix: no page may skip style_splitter."""
    pages, splitters = _build_paged_splitters(qtbot)  # keep `pages` alive below
    # Explicit count (Dashboard 2 + Controls 2 + Logs 1 + Overview 1 + System
    # State 2) so a future page that gains a splitter without being added to
    # _build_paged_splitters — or a dropped splitter — fails loudly instead of
    # silently escaping this consistency check.
    #
    # Logs dropped from 3 to 1 in DEC-282: the left column's table-vs-snapshots
    # handle went with the snapshots into a collapsed section, and the right
    # column's warnings-vs-inspector handle went with the permanent panels.
    assert len(splitters) == 8, [f"{n}/{s.objectName()}" for n, s in splitters]
    for name, sp in splitters:
        assert sp.handleWidth() == SPLITTER_HANDLE_WIDTH, (
            f"{name}/{sp.objectName() or '<unnamed>'} handleWidth={sp.handleWidth()}"
        )
    assert len(pages) == 5  # reference `pages` so it outlives the assertions


# ── Overview: Fan Status ↕ Sensors, cards fixed above ────────────────────


def test_overview_sections_splitter(qtbot):
    page = OverviewPage(state=AppState())
    qtbot.addWidget(page)
    sp = page.findChild(QSplitter, "Overview_Splitter_sections")
    assert sp is not None
    assert sp.orientation() == Qt.Orientation.Vertical
    assert sp.count() == 2
    assert not sp.childrenCollapsible()

    fan_pane = page.findChild(QWidget, "Overview_Pane_fans")
    sensor_pane = page.findChild(QWidget, "Overview_Pane_sensors")
    assert fan_pane is not None and sensor_pane is not None
    assert fan_pane.findChild(QTableWidget, "Overview_Table_fans") is not None
    assert sensor_pane.findChild(QTableWidget, "Overview_Table_sensors") is not None
    # Both panes floor at a usable height so the handle retrades a bounded band.
    assert fan_pane.minimumHeight() >= 100
    assert sensor_pane.minimumHeight() >= 100
    # Order is load-bearing: Fan Status on top, Sensors below.
    assert sp.widget(0) is fan_pane
    assert sp.widget(1) is sensor_pane


def test_overview_top_cards_stay_out_of_the_splitter(qtbot):
    """The user's constraint: the Daemon + Device Discovery cards are unaffected."""
    page = OverviewPage(state=AppState())
    qtbot.addWidget(page)
    # Present on the page…
    assert page.findChild(QWidget, "Overview_Card_daemonHealth") is not None
    assert page.findChild(QWidget, "Overview_Card_deviceDiscovery") is not None
    # …but NOT inside the resize handle.
    sp = page.findChild(QSplitter, "Overview_Splitter_sections")
    assert sp.findChild(QWidget, "Overview_Card_daemonHealth") is None
    assert sp.findChild(QWidget, "Overview_Card_deviceDiscovery") is None


# ── System State: health overview ↕ hardware registry ────────────────────


def test_system_state_sections_splitter(qtbot):
    s = AppState()
    page = SystemStatePage(state=s, diagnostics_service=DiagnosticsService(s))
    qtbot.addWidget(page)
    sp = page.findChild(QSplitter, "SystemState_Splitter_sections")
    assert sp is not None
    assert sp.orientation() == Qt.Orientation.Vertical
    assert sp.count() == 2
    assert not sp.childrenCollapsible()

    overview_pane = page.findChild(QWidget, "SystemState_Pane_healthOverview")
    registry = page.findChild(QWidget, "SystemState_Card_registry")
    row2 = page.findChild(QSplitter, "SystemState_Splitter_row2")
    assert overview_pane is not None and registry is not None and row2 is not None
    # Health card lives in the top pane; row 2 is the other splitter child.
    assert overview_pane.findChild(QWidget, "SystemState_Card_health") is not None
    # Child ORDER is load-bearing (health overview above row 2) — a subtree
    # findChild would still pass if the two addWidget calls were swapped.
    assert sp.widget(0) is overview_pane
    assert sp.widget(1) is row2
    # The registry keeps its own floor (its table scrolls internally, so without
    # one the card collapses to the table's tiny natural minimum).
    assert registry.minimumHeight() >= 120
    # The health pane must NOT carry an explicit height floor. An explicit
    # minimumSize overrides minimumSizeHint rather than backstopping it, so the
    # old literal 190 capped the pane below the card's real minimum and clipped
    # every issue card to a fraction of its height. The floor is the content.
    assert overview_pane.minimumHeight() == 0
    # `minimumHeight() == 0` above IS the rule, and it is what catches a
    # reintroduced literal cap. This line only asserts the pane reports a real
    # content-derived floor at all. It is deliberately NOT `>= 60`: that was the
    # author's font stack written down, CI measured 58, and it reded all three
    # legs. A height in px is a font metric, and a font metric is not portable.
    # (Nor is it `>= health.minimumSizeHint()`, which Qt containment guarantees
    # for any non-empty card and so cannot fail.)
    assert overview_pane.minimumSizeHint().height() > 0


def test_system_state_advanced_actions_stay_out_of_the_splitter(qtbot):
    s = AppState()
    page = SystemStatePage(state=s, diagnostics_service=DiagnosticsService(s))
    qtbot.addWidget(page)
    sp = page.findChild(QSplitter, "SystemState_Splitter_sections")
    # Advanced actions remain fixed below the handle, not inside a resizable pane.
    assert page.findChild(QWidget, "SystemState_Section_advanced") is not None
    assert sp.findChild(QWidget, "SystemState_Section_advanced") is None


# ── Logs: event table ↕ snapshot cards (grow the cards to read more) ──────


def test_logs_left_column_splitter(qtbot):
    """DEC-282 retired this handle. It let you trade log-table height for snapshot
    height, but only because the snapshots occupied a third of the column by default;
    collapsed into "Diagnostic tools" there is nothing left to trade against, and the
    table simply gets the column. The snapshots themselves are unchanged and still
    reachable — that is what this now asserts."""
    page = LogsPage(DiagnosticsService(AppState()))
    qtbot.addWidget(page)
    assert page.findChild(QSplitter, "Logs_Splitter_leftColumn") is None

    table = page.findChild(QTableWidget, "Logs_Table_events")
    section = page.findChild(QWidget, "Logs_Section_diagnostics")
    assert table is not None and section is not None
    # Collapsed by default (brief §15) — the previews exist but claim no space.
    assert section.findChild(QPlainTextEdit, "Logs_Text_daemonStatus") is not None
    # The table keeps its own floor; there is no longer a second pane to balance
    # against it, so the paired snapshot floor went with the handle.
    assert table.minimumHeight() >= 120


def test_logs_keeps_its_main_splitter(qtbot):
    """The horizontal table|inspector handle survives; the right column does not.

    DEC-282 removed the right column entirely (warnings panel above inspector). The
    inspector it used to sit over is now the second child of the main splitter, hidden
    until a row is selected.
    """
    page = LogsPage(DiagnosticsService(AppState()))
    qtbot.addWidget(page)
    main = page.findChild(QSplitter, "Logs_Splitter")
    assert main is not None
    assert main.orientation() == Qt.Orientation.Horizontal
    assert page.findChild(QSplitter, "Logs_Splitter_rightColumn") is None


# ── DEC-284: the splitter gets the surplus height, not a trailing spacer ──
#
# Both pages used to end `_build_ui` with `layout.addStretch(1)`. A QBoxLayout
# distributes surplus by stretch factor the moment ANY factor is non-zero, so
# that spacer took every spare pixel: the band measured a frozen 430px at page
# heights of 760, 1000 AND 1400, with 744px of empty body below it at the
# tallest, and the handle retraded a 430px band instead of the viewport.
#
# Everything below is an exact layout identity read from the same widgets at
# runtime — never a measured pixel value. A height in px is a font metric, and
# a font metric is not portable (CLAUDE.md § Hard-won lessons).

FILLING_PAGES = ["overview", "system_state"]


def _scroll_and_body(page):
    scroll = page.findChild(QScrollArea)
    return scroll, scroll.widget()


def _resize(qtbot, page, height, width=1400):
    page.resize(width, height)
    qtbot.wait(20)


def _fill_page(qtbot, kind):
    """(page, sections splitter), shown and laid out, for either filling page."""
    if kind == "overview":
        page = OverviewPage(state=AppState())
        name = "Overview_Splitter_sections"
    else:
        s = AppState()
        page = SystemStatePage(state=s, diagnostics_service=DiagnosticsService(s))
        name = "SystemState_Splitter_sections"
    qtbot.addWidget(page)
    page.resize(1400, 900)
    page.show()
    qtbot.waitExposed(page)
    sp = page.findChild(QSplitter, name)
    assert sp is not None
    return page, sp


@pytest.mark.parametrize("kind", FILLING_PAGES)
def test_extra_window_height_goes_to_the_sections_splitter(qtbot, kind):
    """Every pixel the window gains lands in the band the handle trades."""
    page, sp = _fill_page(qtbot, kind)
    scroll, _body = _scroll_and_body(page)
    seen = []
    for h in (900, 1100, 1500):
        _resize(qtbot, page, h)
        # Only a height with room to spare says anything about distributing
        # surplus. On a font stack where the content is taller the page is
        # meant to scroll instead — which is its own test below.
        if scroll.verticalScrollBar().maximum() == 0:
            seen.append((h, sp.height()))
    assert len(seen) >= 2, f"{kind}: no window height had surplus to distribute ({seen})"
    for (h0, band0), (h1, band1) in itertools.pairwise(seen):
        assert band1 - band0 == h1 - h0, (
            f"{kind}: window grew {h1 - h0}px but the band grew {band1 - band0}px — "
            "a trailing spacer or a missing layout stretch is taking the rest"
        )


@pytest.mark.parametrize("kind", FILLING_PAGES)
def test_no_dead_space_below_the_last_section(qtbot, kind):
    """The last thing in the page bottoms out at the bottom margin, exactly."""
    page, _sp = _fill_page(qtbot, kind)
    scroll, body = _scroll_and_body(page)
    # Enter the surplus regime by construction — from the body's own natural
    # height, not from a page height that happens to be tall enough here.
    _resize(qtbot, page, body.sizeHint().height() + 400)
    assert scroll.verticalScrollBar().maximum() == 0, f"{kind}: expected room to spare"
    layout = body.layout()
    tail = layout.itemAt(layout.count() - 1)
    assert tail.widget() is not None, (
        f"{kind}: the body layout must end in a widget — a trailing addStretch() "
        "claims the surplus that belongs to the sections splitter (DEC-284)"
    )
    last = tail.widget()
    assert body.height() - (last.y() + last.height()) == layout.contentsMargins().bottom()


@pytest.mark.parametrize("kind", FILLING_PAGES)
def test_short_window_still_scrolls_the_whole_page(qtbot, kind):
    """DEC-234 decision 1 survives the fill.

    The over-correction this guards against is converting either page to a
    fill-the-viewport layout, where the band shrinks to whatever is on screen
    and the tables clip instead of the page scrolling.
    """
    page, _sp = _fill_page(qtbot, kind)
    scroll, body = _scroll_and_body(page)
    # Half the height the content asks for: constrained by construction on any
    # font stack, rather than by a literal that only holds on this one.
    _resize(qtbot, page, max(200, body.sizeHint().height() // 2))
    assert scroll.verticalScrollBar().maximum() > 0, (
        f"{kind}: a window too short for the content must scroll the whole page"
    )


def test_overview_surplus_is_shared_by_both_panes(qtbot):
    """Both Overview tables scroll internally, so both gain from extra height —
    which is why this page keeps Qt's proportional share rather than System
    State's explicit stretch factor. An equal seed therefore stays equal, and a
    dragged ratio survives a resize the same way."""
    page, sp = _fill_page(qtbot, "overview")
    scroll, body = _scroll_and_body(page)
    base = body.sizeHint().height() + 200
    _resize(qtbot, page, base)
    assert scroll.verticalScrollBar().maximum() == 0
    before = sp.sizes()
    _resize(qtbot, page, base + 400)
    assert scroll.verticalScrollBar().maximum() == 0
    after = sp.sizes()
    grown = [a - b for a, b in zip(after, before, strict=True)]
    assert all(g > 0 for g in grown), f"both panes must grow: {before} -> {after}"
    assert abs(grown[0] - grown[1]) <= 1, f"an equal seed must stay equal: {grown}"


def test_system_state_surplus_goes_to_the_registry_row(qtbot):
    """OVW-b: row 2's registry table scrolls internally, so height there is more
    visible rows; the health card ends its own layout with a stretch, so height
    there is whitespace inside a card. Without ``setStretchFactor(1, 1)`` Qt
    shares the surplus in proportion to the current sizes and the health pane
    took 65 → 196px of a 1400px window that it could not use."""
    page, sp = _fill_page(qtbot, "system_state")
    scroll, body = _scroll_and_body(page)
    base = body.sizeHint().height() + 200
    _resize(qtbot, page, base)
    assert scroll.verticalScrollBar().maximum() == 0
    before = sp.sizes()
    _resize(qtbot, page, base + 400)
    assert scroll.verticalScrollBar().maximum() == 0
    after = sp.sizes()
    assert after[0] == before[0], f"the health pane keeps its content height: {before} -> {after}"
    assert after[1] - before[1] == 400, f"row 2 takes the whole surplus: {before} -> {after}"


def test_overview_sensor_section_is_named_sensors(qtbot):
    """ "Sensor Intelligence" over-promised: the section is a table of readings."""
    page = OverviewPage(state=AppState())
    qtbot.addWidget(page)
    header = page.findChild(SectionHeader, "Overview_SectionHeader_sensors")
    assert header is not None
    assert header.title() == "SENSORS"  # SectionHeader renders its title uppercase
    assert "INTELLIGENCE" not in header.title()
