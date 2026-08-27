"""R30: Theme typography system tests.

Covers: font_family and base_font_size_pt in ThemeTokens, font_sizes()
role computation, build_stylesheet() uses computed sizes, theme
save/load roundtrip for typography fields.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from control_ofc.ui.theme import (
    ThemeTokens,
    build_stylesheet,
    default_dark_theme,
    font_sizes,
)


class TestFontSizesComputation:
    """Role-based font sizes computed correctly from base."""

    def test_default_base_produces_expected_sizes(self):
        fs = font_sizes(10)
        assert fs["body"] == 10
        assert fs["title"] == 16  # 10 * 1.6
        assert fs["section"] == 13  # 10 * 1.3
        assert fs["small"] == 9  # 10 * 0.9
        assert fs["card_title"] == 11  # 10 * 1.1
        # DEC-238: 1.5x, not the old 2.2x hero size. Three of these sit side by
        # side in a ~235px fan tile; at 2.2x they outweighed the control name
        # they belong to and set the tile's height.
        assert fs["card_value"] == 15  # 10 * 1.5

    def test_larger_base_scales_proportionally(self):
        fs = font_sizes(14)
        assert fs["body"] == 14
        assert fs["title"] == 22  # 14 * 1.6 = 22.4 → 22
        assert fs["small"] == 13  # 14 * 0.9 = 12.6 → 13

    def test_minimum_base_produces_readable_sizes(self):
        fs = font_sizes(7)
        assert fs["body"] == 7
        assert fs["small"] == 6  # 7 * 0.9 = 6.3 → 6
        assert fs["title"] == 11  # 7 * 1.6 = 11.2 → 11


class TestThemeTokensTypography:
    """ThemeTokens has typography fields with sensible defaults."""

    def test_default_font_family_is_bundled(self):
        # DEC-208: defaults switched from system font ("") to the bundled
        # OFL families (body DM Sans, headings Space Grotesk).
        tokens = ThemeTokens()
        assert tokens.font_family == "DM Sans"
        assert tokens.font_family_heading == "Space Grotesk"

    def test_default_base_font_size(self):
        tokens = ThemeTokens()
        assert tokens.base_font_size_pt == 10

    def test_custom_font_family(self):
        tokens = ThemeTokens(font_family="Noto Sans")
        assert tokens.font_family == "Noto Sans"

    def test_custom_base_size(self):
        tokens = ThemeTokens(base_font_size_pt=14)
        assert tokens.base_font_size_pt == 14


class TestBuildStylesheetTypography:
    """Stylesheet uses computed font sizes from base, not hardcoded px."""

    def test_stylesheet_contains_computed_body_size(self):
        tokens = ThemeTokens(base_font_size_pt=12)
        css = build_stylesheet(tokens)
        # body = 12pt (base * 1.0)
        assert "12pt" in css

    def test_stylesheet_no_hardcoded_13px(self):
        """The old hardcoded 13px global font size is gone."""
        tokens = default_dark_theme()
        css = build_stylesheet(tokens)
        assert "font-size: 13px" not in css

    def test_stylesheet_has_card_value_class(self):
        tokens = default_dark_theme()
        css = build_stylesheet(tokens)
        assert ".CardValue" in css

    def test_stylesheet_font_sizes_change_with_base(self):
        css_10 = build_stylesheet(ThemeTokens(base_font_size_pt=10))
        css_14 = build_stylesheet(ThemeTokens(base_font_size_pt=14))
        # At base=10, title=16pt; at base=14, title=22pt
        assert "16pt" in css_10
        assert "22pt" in css_14


class TestThemeSaveLoadRoundtrip:
    """Typography fields persist through save/load cycle."""

    def test_roundtrip_preserves_font_family(self, tmp_path):
        from control_ofc.ui.theme import load_theme, save_theme

        tokens = ThemeTokens(font_family="Monospace", base_font_size_pt=14)
        path = tmp_path / "test_theme.json"
        save_theme(tokens, path)
        loaded = load_theme(path)
        assert loaded.font_family == "Monospace"
        assert loaded.base_font_size_pt == 14

    def test_load_theme_without_font_fields_uses_defaults(self, tmp_path):
        """Existing themes without typography fields get defaults."""
        import json

        path = tmp_path / "old_theme.json"
        path.write_text(json.dumps({"name": "Old Theme", "version": 2}))
        from control_ofc.ui.theme import load_theme

        loaded = load_theme(path)
        assert loaded.font_family == "DM Sans"
        assert loaded.font_family_heading == "Space Grotesk"
        assert loaded.base_font_size_pt == 10


@pytest.fixture()
def restore_app_theme(qtbot):
    """Save/restore everything ``apply_theme`` mutates (mirrors the fixture in
    test_theme_system.py — these tests scale the base font, which is global)."""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    from control_ofc.ui import theme as theme_mod

    app = QApplication.instance()
    saved = (QPalette(app.palette()), app.styleSheet(), app.font(), theme_mod._active_theme)
    try:
        yield app
    finally:
        app.setPalette(saved[0])
        app.setStyleSheet(saved[1])
        app.setFont(saved[2])
        theme_mod._active_theme = saved[3]


class TestNoWidgetIsCappedBelowItsOwnContent:
    """DEC-258: a fixed size must never be smaller than what the widget needs.

    The Controls card's "Manual" button clipping (docs/14 §16) was one instance
    of a class: a hardcoded pixel size chosen at one font size, against content
    that grows with the theme's user-adjustable 7-16pt base. Point-fixing each
    site leaves the next one to be found by a user, so this sweeps the offenders
    as a group and at the extremes where they actually break.
    """

    @staticmethod
    def _apply(pt: int):
        """Apply the theme application-wide. Costs O(every live widget).

        Only for assertions that read *realised* geometry — see `_style_tree`
        for why the cheap form cannot serve those.
        """
        from control_ofc.ui.theme import apply_theme, default_dark_theme

        tokens = default_dark_theme()
        tokens.base_font_size_pt = pt
        apply_theme(tokens)
        return tokens

    @staticmethod
    def _activate(pt: int):
        """Register the theme without touching the application.

        `set_active_theme` is a plain module-level assignment — no widget walk —
        so a widget constructed afterwards reads the right tokens (`ControlCard`
        does, via `active_theme()`) at a cost that does not scale with the suite.
        """
        from control_ofc.ui.theme import default_dark_theme, set_active_theme

        tokens = default_dark_theme()
        tokens.base_font_size_pt = pt
        set_active_theme(tokens)
        return tokens

    @staticmethod
    def _style_tree(widget, tokens) -> None:
        """Push the theme's three channels at ONE widget tree, not the application.

        `apply_theme` sets the palette, stylesheet and font on the QApplication,
        and Qt re-polishes every live widget for each of the three. Its cost
        therefore tracks the number of widgets the whole suite has left alive
        rather than the size of the thing under test — measured at this point in
        the suite: **20,412 live widgets across 420 top-level trees, 507ms per
        `apply_theme` call**. That coupling is what makes a test here get slower
        every time a feature adds widgets somewhere else, and it is what timed
        out CI at v2.49.0 after the Settings page grew (26.03s, against the 40s
        mark below, from 14.81s at v2.48.1).

        Scoping the same three channels to the widget makes the cost the
        widget's own. Verified equivalent to the application-wide push for what
        these two tests measure: identical `need`/`have` across the full 30-cell
        (font size x density) matrix, and identical size hints and maximums for
        all three capped widgets at 7/10/16pt.

        **The caller must clear the application stylesheet first.** Qt MERGES a
        widget's sheet with the application's, and the widget's does not fully
        dominate: measured, a leftover `QLabel { padding: 30px }` moves `need`
        from 161 to 289 and shifts all 30 cells. Leftover *font-size* rules and a
        leftover application font do not leak (the explicit `setFont` and the
        card's own font-size rules win), so the stylesheet is the only channel
        that needs clearing — but it does need it, or a test that stopped being
        slow starts being order-dependent instead.

        **Not equivalent for realised geometry read before a widget is shown.**
        `test_theme_editor_reset_glyphs_scale_with_the_font` measures
        `button.width()`, which is settled during construction under the
        application font; styling the tree afterwards leaves every glyph at 29px
        at both 7pt and 16pt (against 24px and 45px), which would invert its
        "grows with the font" assertion into a vacuous one. That test keeps
        `_apply` deliberately — see 285-e in `DECISIONS_OPEN_ITEMS.md`.

        The font construction below mirrors `theme.apply_theme_font`, which is
        the source of truth for it — there is no shared QFont builder to call
        today. If that function gains a channel (weight, style, DPI handling) or
        a return value, update this in lockstep: the equivalence these two tests
        rely on is equivalence *with production*, and it would go on passing
        against a drifted copy.
        """
        from PySide6.QtGui import QFont, QFontDatabase

        from control_ofc.ui.theme import build_palette, build_stylesheet

        family = tokens.font_family
        if not family:
            family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        widget.setFont(QFont(family, tokens.base_font_size_pt))
        widget.setPalette(build_palette(tokens))
        widget.setStyleSheet(build_stylesheet(tokens))

    # Well under the global 60s ini cap, so cost creep reddens here as an early
    # warning rather than at the wire. Since this test stopped applying the theme
    # application-wide its runtime is its own, not the suite's — but the fixture
    # teardown still restores three application-level channels, so the margin is
    # not infinite and this mark stays as the tripwire.
    @pytest.mark.timeout(40)
    def test_the_control_card_details_row_fits_every_density(
        self, qtbot, restore_app_theme, monkeypatch
    ):
        """The measured regression, swept across the whole tier matrix.

        The details block clipped from 11pt in the default density and 9pt in
        compact because `_WIDTH_PER_PT` was 11 where the content needs ~23.

        DEC-260: this originally iterated font sizes but pinned `comfortable`, so
        it stayed green while a third of the matrix still clipped — the same
        hand-scoped blind spot that let six focus gaps ship. It now sweeps all
        three densities and distinguishes the two outcomes:

        - `comfortable` and `large` must FIT outright: they are the densities that
          promise no elision at any supported font size.
        - `compact` is allowed to elide, and does from 9pt up. That is the honest
          consequence of a density multiplier that scales the card but not its
          content, and since DEC-258 it degrades to an ellipsis rather than a
          hard clip. Asserted as *bounded* rather than ignored, so a future change
          that turns a graceful elision into a gross one still fails here.
        """
        from control_ofc.services.profile_service import (
            ControlMember,
            ControlMode,
            CurveConfig,
            CurveType,
            LogicalControl,
        )
        from control_ofc.ui.widgets.card_metrics import (
            CARD_SIZE_COMFORTABLE,
            CARD_SIZE_COMPACT,
            CARD_SIZE_LARGE,
            card_dimensions,
        )
        from control_ofc.ui.widgets.control_card import ControlCard

        must_fit = (CARD_SIZE_COMFORTABLE, CARD_SIZE_LARGE)
        clipped: list[str] = []
        compact_deficits: list[int] = []

        # Count REAL application-wide applies, not calls to the `_apply` helper —
        # the timeout above is machine-dependent, this is not. `_apply` imports
        # `apply_theme` at call time, so patching the module attribute sees every
        # application regardless of which helper routed it.
        from control_ofc.ui import theme as theme_mod

        real_apply_theme = theme_mod.apply_theme
        applies: list[int] = []

        def _counting_apply_theme(tokens):
            applies.append(tokens.base_font_size_pt)
            return real_apply_theme(tokens)

        monkeypatch.setattr(theme_mod, "apply_theme", _counting_apply_theme)

        # Clear the APPLICATION stylesheet once, because this test no longer
        # overwrites it. `_style_tree` merges the card's sheet with whatever is
        # installed application-wide, and the merge does not fully dominate:
        # measured, a leftover `QLabel { padding: 30px }` moves `need` from 161 to
        # 289 and shifts all 30 cells, while leftover *font-size* rules and a
        # leftover app font do not leak at all. Without this the measurement would
        # depend on whichever test ran before — trading a slow test for an
        # order-dependent one. `restore_app_theme` yields the QApplication and puts
        # the sheet back afterwards. One application-wide call, against the ten this
        # test used to make.
        restore_app_theme.setStyleSheet("")

        # The theme is REGISTERED once per font size (cheap, no widget walk) and
        # PUSHED at each card's own tree (cost proportional to that card). Nothing
        # here touches the application, so this test's runtime no longer depends on
        # how many widgets the rest of the suite has left alive — which is the
        # coupling that made it slower every time a feature added widgets
        # elsewhere, and that reddened all three CI legs at v2.49.0.
        for pt in range(7, 17):
            tokens = self._activate(pt)
            for tier in (CARD_SIZE_COMPACT, *must_fit):
                curves = [CurveConfig(id="c1", name="Aggressive Ramp", type=CurveType.GRAPH)]
                control = LogicalControl(
                    id="ctl",
                    name="Radiator Loop",
                    mode=ControlMode.CURVE,
                    curve_id="c1",
                    manual_output_pct=50.0,
                    members=[
                        ControlMember(
                            source="hwmon",
                            member_id="hwmon:it8696:0:pwm1:CPU_FAN",
                            member_label="CPU_FAN",
                        )
                    ],
                )
                card = ControlCard(control, curves, card_size=tier)
                qtbot.addWidget(card)
                self._style_tree(card, tokens)
                width, _height = card_dimensions(pt, tier)
                card.setFixedWidth(width)
                card.show()
                details = card.findChild(QWidget, "ControlCard_Details_ctl")
                need = details.sizeHint().width()
                have = width - 42  # measured card padding
                if need > have:
                    if tier in must_fit:
                        clipped.append(f"{tier}@{pt}pt: needs {need}, has {have}")
                    else:
                        compact_deficits.append(need - have)
                card.hide()

        assert not clipped, (
            "the comfortable and large densities must hold their own details row "
            f"at every theme font size: {clipped}"
        )
        # Compact elides by design; keep it a trim, not a truncation.
        assert max(compact_deficits, default=0) <= 60, (
            "compact's elision has grown beyond a trim — the curve name is being "
            f"cut back too far: worst deficit {max(compact_deficits, default=0)}px"
        )
        # ZERO application-wide applies. This is the guard that keeps the fix
        # fixed: the previous version of this test asserted "ten applies, one per
        # font size", which bounded the count but left the per-apply cost growing
        # with the suite — ten cheap calls at v2.41.0 were ten 507ms calls by
        # v2.49.0. Asserting *none* pins the property that actually matters, and
        # catches a reintroduced `self._apply(pt)` on any machine, where the
        # wall-clock mark above only catches it on a slow enough one.
        assert applies == [], (
            "this test must not apply the theme application-wide — its cost would "
            "again scale with every widget the rest of the suite leaves alive. Use "
            f"_activate + _style_tree; got {len(applies)} applies: {applies}"
        )

    def test_no_fixed_size_caps_a_widget_below_its_hint(
        self, qtbot, restore_app_theme, monkeypatch
    ):
        """The sites that hardcoded a pixel size against growing content."""
        from control_ofc.ui import theme as theme_mod
        from control_ofc.ui.about_dialog import AboutDialog
        from control_ofc.ui.components.footer import StatusFooter
        from control_ofc.ui.widgets.error_banner import ErrorBanner

        # Same zero-apply guard as the details-row test. This one needs it MORE,
        # not less: it carries no `pytest.mark.timeout`, and reverting it to
        # `_apply` would add only ~1.5s (3 applies) — invisible against the 60s
        # ini cap and lost in the suite total, so the regression would land
        # silently. The sibling's wall-clock mark would at least eventually
        # notice; here the count assertion is the only thing that can.
        real_apply_theme = theme_mod.apply_theme
        applies: list[int] = []

        def _counting_apply_theme(tokens):
            applies.append(tokens.base_font_size_pt)
            return real_apply_theme(tokens)

        monkeypatch.setattr(theme_mod, "apply_theme", _counting_apply_theme)

        # Same isolation as the details-row test, and for the same measured
        # reason — see the comment there.
        restore_app_theme.setStyleSheet("")

        offenders = []
        for pt in (7, 10, 16):
            # Scoped for the same reason as the details-row test above, and
            # verified the same way: identical hints and maximums for all three
            # widgets at all three sizes.
            tokens = self._activate(pt)
            for name, widget in (
                ("ErrorBanner", ErrorBanner()),
                ("StatusFooter", StatusFooter()),
                ("AboutDialog", AboutDialog()),
            ):
                qtbot.addWidget(widget)
                self._style_tree(widget, tokens)
                hint = widget.sizeHint()
                maxi = widget.maximumSize()
                if maxi.width() < hint.width() or maxi.height() < hint.height():
                    offenders.append(
                        f"{name}@{pt}pt capped at {maxi.width()}x{maxi.height()} "
                        f"below its hint {hint.width()}x{hint.height()}"
                    )
        assert not offenders, offenders
        assert applies == [], (
            "this test must not apply the theme application-wide — use "
            f"_activate + _style_tree; got {len(applies)} applies: {applies}"
        )

    def test_theme_editor_reset_glyphs_scale_with_the_font(self, qtbot, restore_app_theme):
        """~50 of these down the page; they were a hard 24x24 and clipped at the
        shipped default, not only at large sizes.

        Keeps the application-wide `_apply` on purpose. This is the one test in
        the class that reads *realised* geometry (`b.width()`) rather than a size
        hint, and that width is settled during construction under the application
        font — under `_style_tree` every glyph measures 29px at both 7pt and
        16pt, which would turn the `sizes[16] > sizes[7]` assertion below into a
        false one. Two applies, so it is ~3s rather than ~26s, but it does still
        scale with the suite; tracked as 285-e in `DECISIONS_OPEN_ITEMS.md`.
        """
        from PySide6.QtWidgets import QPushButton

        from control_ofc.ui.widgets.theme_editor import ThemeEditorWidget

        sizes = {}
        for pt in (7, 16):
            self._apply(pt)
            editor = ThemeEditorWidget()
            qtbot.addWidget(editor)
            resets = [b for b in editor.findChildren(QPushButton) if b.text() == "↺"]
            assert resets, "expected the per-token reset buttons"
            too_small = [b for b in resets if b.width() < b.fontMetrics().height()]
            assert not too_small, (
                f"{len(too_small)} reset glyphs are narrower than their own text at {pt}pt"
            )
            sizes[pt] = resets[0].width()

        assert sizes[16] > sizes[7], (
            "the glyph button must grow with the theme font, not stay pinned at 24px"
        )
