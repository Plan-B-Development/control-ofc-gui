"""System State page layout: the two-row composition and the anti-squash rules.

The page used to put the health card at stretch 2 of 3 with the interference and
safety cards beside it, under a vertical splitter whose top pane carried an
explicit ``setMinimumHeight(190)``. Both halves of that squashed the page's
densest content: two thirds of the width forced every finding to wrap, and the
literal height floor OVERRODE the card's real minimum (an explicit
``minimumSize`` wins over ``minimumSizeHint``), so the issue cards were handed
~40px each against a 118-202px need and their detail boxes were clipped flat.

These tests pin the new composition and — more importantly — the measured
outcome: nothing on the page is allocated less height than its own content needs
at the width it actually has. They assert against ``totalHeightForWidth``, not
``sizeHint``, because ``sizeHint`` over-reports for word-wrapped labels (QLabel
computes it at a heuristic width) and would pass while the page clipped.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QSplitter, QWidget

from control_ofc.api.models import (
    AcpiConflictInfo,
    BoardInfo,
    ConnectionState,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.layout_state import clamp_restored_sizes
from control_ofc.ui.components.gauges import RadialGauge
from control_ofc.ui.pages.system_state_page import SystemStatePage

# A realistic contended header id: the stable hwmon id shape, rendered monospace.
# Its length is the point — it is user-invisible content that used to set the
# whole status column's minimum width.
LONG_HEADER_ID = "hwmon:nct6687:platform-nct6687.2592:pwm3:CPU_FAN"


def _state() -> AppState:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    return s


def _page(qtbot):
    s = _state()
    page = SystemStatePage(state=s, diagnostics_service=DiagnosticsService(s))
    qtbot.addWidget(page)
    return page


def _busy_diag() -> HardwareDiagnosticsResult:
    """Several findings AND contention — the state the screenshot was taken in."""
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="nct6687", expected_driver="nct6687d", header_count=8)
            ],
            total_headers=8,
            writable_headers=0,
            enable_revert_counts={LONG_HEADER_ID: 996},
        ),
        board=BoardInfo(vendor="ASUS", name="ProArt X870E-CREATOR WIFI", bios_version="1234"),
        kernel_modules=[],
        acpi_conflicts=[
            AcpiConflictInfo(
                io_range="0x0290-0x0299", claimed_by="ACPI", conflicts_with_driver="it87"
            )
        ],
    )


def _shown(qtbot, width: int):
    """A page rendered with busy data at *width*, laid out and settled.

    Two ``waitExposed``-free layout passes: the content-sized minimum reads the
    width from the previous pass, so a narrowing resize settles on the second.
    """
    page = _page(qtbot)
    page.show()
    qtbot.waitExposed(page)
    page._render(_busy_diag())
    page.resize(width, 900)
    qtbot.wait(1)
    page.resize(width, 900)
    qtbot.wait(1)
    return page


def _row2(page) -> QSplitter:
    return page.findChild(QSplitter, "SystemState_Splitter_row2")


def _sidebar(page) -> QWidget:
    return page.findChild(QWidget, "SystemState_Pane_statusSidebar")


def _effective_min_width(widget) -> int:
    """The width Qt will actually refuse to shrink *widget* below.

    Mirrors Qt's own ``qSmartMinSize``: an explicit ``minimumWidth`` REPLACES
    the propagated content hint, it does not raise a floor under it — the same
    override that made ``setMinimumHeight(190)`` a cap rather than a backstop and
    clipped this page's findings for four releases. A first cut wrote ``max(...)``
    of the two, which is right only while no explicit minimum sits *below* its
    widget's content hint.

    Reading just one of the two is how a floor becomes invisible to the check
    meant to notice it: the sidebar deliberately sets no explicit minimum, so
    there the hint is the floor, while a test that forces one needs the forced
    value. Saying that once keeps it an observation rather than an assumption
    every call site re-makes.
    """
    explicit = widget.minimumWidth()
    return explicit if explicit else widget.minimumSizeHint().width()


# ── Row composition: the acceptance criteria, asserted structurally ──────


def test_health_is_alone_in_the_top_row(qtbot):
    page = _page(qtbot)
    pane = page.findChild(QWidget, "SystemState_Pane_healthOverview")
    assert pane.findChild(QWidget, "SystemState_Card_health") is not None
    # The literal acceptance criterion: the two status cards no longer sit
    # beside the health card. A subtree search, so a re-parent anywhere under
    # the pane fails this, not just a direct child.
    assert pane.findChild(QWidget, "SystemState_Card_interference") is None
    assert pane.findChild(QWidget, "SystemState_Card_safety") is None


def test_row2_is_registry_then_status_sidebar(qtbot):
    page = _page(qtbot)
    row2 = _row2(page)
    assert (
        row2.orientation()
        == __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Orientation.Horizontal
    )
    assert not row2.childrenCollapsible()
    # Order is load-bearing: registry left, sidebar right. A findChild would
    # still pass with the two addWidget calls swapped.
    assert row2.count() == 2
    assert row2.widget(0) is page.findChild(QWidget, "SystemState_Card_registry")
    assert row2.widget(1) is _sidebar(page)


def test_sidebar_stacks_interference_above_safety(qtbot):
    page = _page(qtbot)
    layout = _sidebar(page).layout()
    order = [
        layout.itemAt(i).widget().objectName()
        for i in range(layout.count())
        if layout.itemAt(i).widget() is not None
    ]
    assert order == ["SystemState_Card_interference", "SystemState_Card_safety"]


# ── Floors are derived, not written down ─────────────────────────────────


def test_registry_floor_is_derived_from_its_own_columns(qtbot):
    page = _page(qtbot)
    registry = page.findChild(QWidget, "SystemState_Card_registry")
    header = registry._registry_table.horizontalHeader()
    columns = sum(header.sectionSizeHint(c) for c in range(header.count()))
    # The floor holds every column header, plus the card's chrome — never less
    # than the columns themselves, and never the wildly inflated
    # QHeaderView.length() (which setStretchLastSection pads with free space).
    assert registry.minimumWidth() >= columns
    # Asserted as the EXACT decomposition, recomputed from the same widgets, so
    # nothing here is a pixel value that only holds on one font stack. A first
    # cut allowed `columns + 80` as a slack chrome band — a tuned literal that
    # would have gone quietly wrong rather than red.
    margins = registry.layout().contentsMargins()
    chrome = margins.left() + margins.right() + 2 * registry._registry_table.frameWidth()
    # The scrollbar allowance comes from the real scrollbar, not a literal: the
    # theme sets its width through a `QScrollBar:vertical` QSS rule, and asking
    # the card's or the table's style for PM_ScrollBarExtent instead returns
    # Qt's unstyled default and silently ignores the theme.
    scrollbar = registry._registry_table.verticalScrollBar().sizeHint().width()
    assert scrollbar > 0
    assert registry.content_min_width() == columns + chrome + scrollbar
    # And never the wildly inflated QHeaderView.length(), which
    # setStretchLastSection pads with whatever free space the table happens to
    # have (measured 638 against a true 540 on an otherwise identical table).
    assert registry.content_min_width() < header.length()


def test_registry_floor_tracks_a_column_being_added(qtbot):
    """Proves the floor is DERIVED. A literal would not move when the table does."""
    page = _page(qtbot)
    registry = page.findChild(QWidget, "SystemState_Card_registry")
    before = registry.content_min_width()
    table = registry._registry_table
    table.setColumnCount(table.columnCount() + 1)
    table.setHorizontalHeaderItem(
        table.columnCount() - 1,
        type(table.horizontalHeaderItem(0))("A Wide New Column Header"),
    )
    assert registry.content_min_width() > before


def test_set_theme_rederives_the_registry_floor(qtbot):
    """DEC-260's lesson, applied to this floor: it is derived from column size
    hints, which scale with the theme's base font. Derived once at construction
    it pins the pane to startup while the columns inside it keep growing.

    Tests the CALL SITE, not just ``content_min_width`` — a correct helper that
    nothing re-invokes is an unenforced rule.
    """
    page = _page(qtbot)
    registry = page.findChild(QWidget, "SystemState_Card_registry")
    before = registry.minimumWidth()
    table = registry._registry_table
    table.setColumnCount(table.columnCount() + 1)
    table.setHorizontalHeaderItem(
        table.columnCount() - 1,
        type(table.horizontalHeaderItem(0))("A Very Wide New Column Header"),
    )
    assert registry.minimumWidth() == before, "stale until something re-derives it"
    page.set_theme(None)
    assert registry.minimumWidth() > before


def test_sidebar_floor_is_propagated_from_the_gauge_not_hardcoded(qtbot):
    page = _page(qtbot)
    sidebar = _sidebar(page)
    gauge = page.findChild(RadialGauge, "SystemState_Gauge_reverts")
    # Nothing sets an explicit width on the sidebar — Qt derives it from the
    # content, so it re-derives itself when the gauge or the theme font moves.
    assert sidebar.minimumWidth() == 0
    assert sidebar.minimumSizeHint().width() >= gauge.minimumWidth() > 0


def test_row2_keeps_the_registry_dominant_at_every_width(qtbot):
    """Registry dominant everywhere; the exact 3:1 only where the floors allow it.

    The floors are derived from font metrics, so the width at which they start
    binding is font-dependent — and once one binds, the realised ratio departs
    from the requested 3:1 *correctly*. The first cut asserted a flat
    ``0.70 <= share <= 0.80`` at every width and CI measured 0.6987 at the
    narrowest one: the assertion had encoded the author's font stack, and the
    behaviour it flagged was right. So the band is checked only when neither
    floor is binding, and dominance — which is the actual promise — everywhere.
    """
    page = _shown(qtbot, 1400)
    registry = page.findChild(QWidget, "SystemState_Card_registry")
    sidebar = _sidebar(page)
    # A width DERIVED to sit inside the binding regime on any host: at a row-2
    # width near the sum of the two floors there is nothing left to share, so
    # the ratio must depart from 3:1. Without a binding width in the sweep the
    # `floors_bind` guard below is unreachable locally — deleting it would leave
    # the whole suite green here while reding CI, which is the exact flake this
    # file caused. Measured on this host: 0.7176 at 1168 (in band, guard
    # unpinned) against 0.6674 at 1000 (out of band, guard pinned).
    binding = _effective_min_width(registry) + _effective_min_width(sidebar) + 40
    unbound_seen = 0
    bound_seen = 0
    for width in (binding, 1400, 1920, 2560):
        page.resize(width, 900)
        qtbot.wait(1)
        registry_w, sidebar_w = _row2(page).sizes()
        assert registry_w > sidebar_w, f"at {width}: registry not dominant"
        floors_bind = registry_w <= _effective_min_width(
            registry
        ) or sidebar_w <= _effective_min_width(sidebar)
        if floors_bind:
            bound_seen += 1
            continue
        unbound_seen += 1
        share = registry_w / (registry_w + sidebar_w)
        assert 0.70 <= share <= 0.80, f"at {width}: registry share {share:.2f}"
    # Both branches must be exercised, or this passes by asserting nothing:
    # without an unbound width the band is never checked, and without a bound
    # one the guard that skips it is never taken.
    assert unbound_seen, "no tested width left both floors slack; band never checked"
    assert bound_seen, "no tested width bound a floor; the skip branch is unpinned"


def test_dominance_survives_an_explicit_sidebar_floor(qtbot):
    """The one test that exercises an EXPLICIT minimum rather than a propagated one.

    The sibling sweep above pins the ``floors_bind`` guard for the *propagated*
    case, which is how the sidebar actually behaves (it sets no explicit
    minimum). This forces an explicit one, so it is the only cover for
    ``_effective_min_width``'s explicit branch — the branch that mirrors Qt's
    ``qSmartMinSize`` REPLACING the content hint rather than raising a floor
    under it.

    Its ratio assertions are close to arithmetic — a 40% floor cannot yield a
    majority sidebar — so read them as documenting the shape of the CI failure
    (a floor binding, the ratio legitimately leaving 3:1) rather than as the
    thing that catches a regression. The sweep above is what catches it.
    """
    page = _shown(qtbot, 1400)
    registry = page.findChild(QWidget, "SystemState_Card_registry")
    sidebar = _sidebar(page)
    # A floor far too wide for a 25% share — the CI condition, host-independently.
    sidebar.setMinimumWidth(int(1168 * 0.40))
    page.resize(1168, 900)
    qtbot.wait(1)
    registry_w, sidebar_w = _row2(page).sizes()

    floors_bind = registry_w <= _effective_min_width(registry) or sidebar_w <= _effective_min_width(
        sidebar
    )
    assert floors_bind, "forced floor did not bind — reproduction is not exercising the case"
    # The promise that must survive a binding floor:
    assert registry_w > sidebar_w, "registry must stay dominant even when a floor binds"
    # ...and the promise that must NOT be asserted there, which is the whole bug:
    share = registry_w / (registry_w + sidebar_w)
    assert not (0.70 <= share <= 0.80), (
        f"share {share:.4f} stayed in band — this no longer reproduces the CI failure"
    )


# ── The anti-squash rule: measured outcome, not "something changed" ──────


def _too_short(widget) -> list[str]:
    """Descendants allocated less height than their content needs at their width."""
    short = []
    for child in widget.findChildren(QWidget):
        if not child.objectName() or not child.isVisible():
            continue
        if not child.hasHeightForWidth():
            continue
        needed = child.heightForWidth(child.width())
        if needed > 0 and child.height() < needed:
            short.append(f"{child.objectName()}: {child.height()} < {needed}")
    return short


def test_issue_cards_are_not_clipped_at_any_width(qtbot):
    """The defect this layout pass exists to remove.

    Before: every issue card was allocated ~40px against a 118-202px need, and
    the detail boxes rendered as the flattened grey bars in the report.
    """
    for width in (1168, 1400, 1920):
        page = _shown(qtbot, width)
        health = page.findChild(QWidget, "SystemState_Card_health")
        # Guard against passing vacuously on an empty card (the absence rule):
        # there must BE issue cards for "none are clipped" to mean anything.
        cards = [
            c
            for c in page.findChildren(QWidget)
            if c.objectName().startswith("SystemState_IssueCard_")
        ]
        assert len(cards) >= 2, f"fixture produced {len(cards)} issue cards"
        for card in cards:
            needed = card.layout().totalHeightForWidth(card.width())
            assert card.height() >= needed, (
                f"{card.objectName()} at page {width}: {card.height()} < {needed}"
            )
        assert _too_short(health) == []


def test_sidebar_cards_are_not_clipped(qtbot):
    """The interference card's explanation paragraph wraps harder in the narrower
    sidebar than it did in the old third-width column — it was 69px short."""
    for width in (1168, 1400, 1920):
        page = _shown(qtbot, width)
        for name in ("SystemState_Card_interference", "SystemState_Card_safety"):
            card = page.findChild(QWidget, name)
            needed = card.layout().totalHeightForWidth(card.width())
            assert card.height() >= needed, f"{name} at page {width}: {card.height()} < {needed}"


def test_a_contended_header_id_does_not_blow_out_the_sidebar(qtbot):
    """A long monospace id with no wrap sets an UNBOUNDED minimum width.

    Measured before the wrap: the card's minimum went 205 -> 367 and the whole
    status column's 205 -> 419 the moment contention appeared, stealing width
    from the card beside it.
    """
    page = _shown(qtbot, 1400)
    label = page.findChild(QLabel, "SystemState_Label_headerId")
    assert label.text() == LONG_HEADER_ID  # assert the presence before the bound
    assert label.wordWrap()
    interference = page.findChild(QWidget, "SystemState_Card_interference")
    registry = page.findChild(QWidget, "SystemState_Card_registry")
    # Measured against what this same label would demand UNWRAPPED, rather than
    # against a pixel ceiling: the whole point is that the id no longer sets the
    # column's width, and only the counterfactual states that. A `< 300` literal
    # here would just be this machine's fonts again.
    wrapped = label.minimumSizeHint().width()
    label.setWordWrap(False)
    unwrapped = label.minimumSizeHint().width()
    label.setWordWrap(True)  # restore before any later assertion reads the page
    assert unwrapped > wrapped, "fixture id is too short to demonstrate the bound"
    assert interference.minimumSizeHint().width() < unwrapped
    assert _sidebar(page).minimumSizeHint().width() < registry.minimumWidth()


def test_health_card_minimum_tracks_its_content_at_its_width(qtbot):
    """The rule that makes the page scroll instead of clip.

    Neither QSplitter nor QScrollArea propagates heightForWidth, so the honest
    height has to arrive through minimumSizeHint — the one channel they consult.
    """
    page = _shown(qtbot, 1400)
    health = page.findChild(QWidget, "SystemState_Card_health")
    # The anti-vacuous guard is the fixture's CARD COUNT, not a pixel height: a
    # `needed > 200` threshold is a font metric wearing a guard's clothing, and
    # would fail on a small font stack while proving nothing extra on a large one.
    cards = [
        c for c in page.findChildren(QWidget) if c.objectName().startswith("SystemState_IssueCard_")
    ]
    assert len(cards) >= 2, f"fixture produced {len(cards)} issue cards"
    needed = health.layout().totalHeightForWidth(health.width())
    # That the rule DOES something (rather than merely holding) is proven
    # portably in test_components_library, where a plain Card under-reports the
    # same content. Here the assertion is the rule itself, both sides measured
    # from the same widget so no font metric can move them apart.
    assert health.minimumSizeHint().height() >= needed


def test_health_gets_more_width_than_it_used_to(qtbot):
    """It spanned two thirds; it now spans the row. Asserted as a ratio of the
    pane so it holds at any window size."""
    page = _shown(qtbot, 1400)
    health = page.findChild(QWidget, "SystemState_Card_health")
    pane = page.findChild(QWidget, "SystemState_Pane_healthOverview")
    assert health.width() == pane.width()
    # Comfortably past the old stretch-2-of-3 share, so a regression to the
    # two-column row fails here rather than passing as "wide enough".
    assert health.width() > pane.width() * 0.9


def test_a_restored_layout_never_clips_the_health_pane(qtbot):
    """Register row 284-a, guarded at the property that actually matters.

    DEC-245 restores a saved *ratio*, and DEC-284 made this page's fresh
    distribution non-proportional, so a layout dragged before that release comes
    back as a share of a much larger band. That mismatch is recorded and
    deliberately unfixed: it costs whitespace, which is cosmetic, and the
    correction is a design choice on DEC-245's shared surface.

    What must hold whichever way that decision goes is this — no saved value may
    squeeze the health pane below the height its findings need, because that is
    exactly the clipping DEC-281 was written to end. Asserted against the pane's
    own runtime minimum with the busy fixture rendered, so the floor is large
    enough for the assertion to be capable of failing: with an empty page the
    content minimum is ~64px and no plausible saved layout can go under it, so
    the same test on an unpopulated page would pass vacuously.
    """
    page = _shown(qtbot, 1400)
    splitter = page.findChild(QSplitter, "SystemState_Splitter_sections")
    pane = page.findChild(QWidget, "SystemState_Pane_healthOverview")
    floor = pane.minimumSizeHint().height()
    assert floor > 200, f"fixture floor {floor}px is too small for this test to fail"
    # Saved layouts far past anything a real drag could produce, both directions.
    for saved in ([48, 10_000], [64, 358], [190, 240], [10_000, 48]):
        restored = clamp_restored_sizes(saved, splitter.sizes())
        assert restored is not None, saved
        splitter.setSizes(restored)
        qtbot.wait(1)
        assert splitter.sizes()[0] >= floor, (
            f"saved {saved} restored to {splitter.sizes()}, clipping the health "
            f"pane below the {floor}px its content needs"
        )
