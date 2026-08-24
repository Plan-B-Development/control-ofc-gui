"""DEC-234: consistent, discoverable section resize handles.

A `/frontend-design` pass gave every page's `QSplitter` one shared handle
treatment (a hairline that lights up in the accent on hover, in a comfortable
grab zone) and added the sections the user asked to be resizable:

* Overview — Fan Status ↕ Sensor Intelligence (the top cards stay fixed above).
* System State — health overview ↕ hardware registry.
* Logs — event table ↕ diagnostic-snapshot cards (so the cards can be grown).

These are presentation-only structural facts; the polling / VM layers are
untouched, so the tests assert widget-tree outcomes, not behaviour.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QSplitter, QTableWidget, QWidget

from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.profile_service import ProfileService
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
    # Explicit count (Dashboard 2 + Controls 2 + Logs 3 + Overview 1 + System
    # State 2) so a future page that gains a splitter without being added to
    # _build_paged_splitters — or a dropped splitter — fails loudly instead of
    # silently escaping this consistency check.
    assert len(splitters) == 10, [f"{n}/{s.objectName()}" for n, s in splitters]
    for name, sp in splitters:
        assert sp.handleWidth() == SPLITTER_HANDLE_WIDTH, (
            f"{name}/{sp.objectName() or '<unnamed>'} handleWidth={sp.handleWidth()}"
        )
    assert len(pages) == 5  # reference `pages` so it outlives the assertions


# ── Overview: Fan Status ↕ Sensor Intelligence, cards fixed above ─────────


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
    # Order is load-bearing: Fan Status on top, Sensor Intelligence below.
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
    assert overview_pane.minimumSizeHint().height() >= 60


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
    page = LogsPage(DiagnosticsService(AppState()))
    qtbot.addWidget(page)
    sp = page.findChild(QSplitter, "Logs_Splitter_leftColumn")
    assert sp is not None
    assert sp.orientation() == Qt.Orientation.Vertical
    assert sp.count() == 2
    assert not sp.childrenCollapsible()

    # Event table above; snapshot cards below — order is load-bearing.
    table = sp.findChild(QTableWidget, "Logs_Table_events")
    snap = page.findChild(QWidget, "Logs_Pane_snapshots")
    assert table is not None and snap is not None
    assert sp.widget(0) is table
    assert sp.widget(1) is snap
    # A snapshot preview (which grows as the pane grows) lives in the lower pane.
    assert snap.findChild(QPlainTextEdit, "Logs_Text_daemonStatus") is not None
    # Floors keep both usable when the handle is dragged toward one end.
    assert table.minimumHeight() >= 120
    assert snap.minimumHeight() >= 120


def test_logs_keeps_its_existing_splitters(qtbot):
    """The horizontal (events|inspector) and right-column handles still exist."""
    page = LogsPage(DiagnosticsService(AppState()))
    qtbot.addWidget(page)
    main = page.findChild(QSplitter, "Logs_Splitter")
    right = page.findChild(QSplitter, "Logs_Splitter_rightColumn")
    assert main is not None and right is not None
    assert main.orientation() == Qt.Orientation.Horizontal
    assert right.orientation() == Qt.Orientation.Vertical
