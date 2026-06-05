"""DEC-128: content-driven, theme-scaled card sizing + 50/50 sections split.

Covers the Controls-page card sizing rework:
- ``card_dimensions`` scales width + minimum height with the theme font and a
  density tier (compact / comfortable / large).
- ``ControlCard`` / ``CurveCard`` set a fixed width and a *minimum* height
  (no max), so rows can't clip when the font grows (the old fixed 220x160 box
  did clip).
- the Fan Roles / Curves splitter defaults to a proportional 50/50 split.
- the ``card_size`` AppSettings field round-trips and reaches live cards.
"""

from __future__ import annotations

from PySide6.QtWidgets import QSplitter

from control_ofc.services.app_settings_service import AppSettings
from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
)
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.theme import ThemeTokens, active_theme
from control_ofc.ui.widgets.card_metrics import (
    CARD_SIZE_TIERS,
    DEFAULT_CARD_SIZE,
    card_dimensions,
)
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.curve_card import CurveCard


class TestCardDimensions:
    """The pure sizing function scales with font and tier, and is bounded."""

    def test_height_scales_with_font(self):
        for tier in CARD_SIZE_TIERS:
            assert (
                card_dimensions(16, tier)[1]
                > card_dimensions(10, tier)[1]
                > card_dimensions(7, tier)[1]
            )

    def test_width_scales_with_font(self):
        for tier in CARD_SIZE_TIERS:
            assert (
                card_dimensions(16, tier)[0]
                > card_dimensions(10, tier)[0]
                > card_dimensions(7, tier)[0]
            )

    def test_tier_multiplier_orders_sizes(self):
        compact = card_dimensions(10, "compact")
        comfortable = card_dimensions(10, "comfortable")
        large = card_dimensions(10, "large")
        assert compact[0] < comfortable[0] < large[0]
        assert compact[1] < comfortable[1] < large[1]

    def test_unknown_tier_falls_back_to_comfortable(self):
        assert card_dimensions(10, "bogus") == card_dimensions(10, "comfortable")

    def test_font_size_is_clamped(self):
        assert card_dimensions(99, "comfortable") == card_dimensions(16, "comfortable")
        assert card_dimensions(1, "comfortable") == card_dimensions(7, "comfortable")

    def test_non_integer_font_size_tolerated(self):
        # Defensive: a malformed settings value must not crash the card.
        assert card_dimensions(None, "comfortable") == card_dimensions(10, "comfortable")

    def test_default_tier_is_comfortable(self):
        assert DEFAULT_CARD_SIZE == "comfortable"
        assert DEFAULT_CARD_SIZE in CARD_SIZE_TIERS


class TestCardWidgetSizing:
    """Both card widgets apply fixed width + minimum-height-floor sizing."""

    @staticmethod
    def _control():
        return LogicalControl(id="r1", name="Role", mode=ControlMode.CURVE)

    def test_control_card_fixed_width_min_height(self, qtbot):
        card = ControlCard(self._control(), [])
        qtbot.addWidget(card)
        w, h = card_dimensions(active_theme().base_font_size_pt, DEFAULT_CARD_SIZE)
        assert card.minimumWidth() == card.maximumWidth() == w
        assert card.minimumHeight() == h
        # Regression for the old fixed 220x160 box: height is uncapped so rows
        # can never clip when the theme font grows.
        assert card.maximumHeight() > 100000

    def test_curve_card_fixed_width_min_height(self, qtbot):
        card = CurveCard(CurveConfig(id="c1", name="C", type=CurveType.FLAT))
        qtbot.addWidget(card)
        w, h = card_dimensions(active_theme().base_font_size_pt, DEFAULT_CARD_SIZE)
        assert card.minimumWidth() == card.maximumWidth() == w
        assert card.minimumHeight() == h
        assert card.maximumHeight() > 100000

    def test_cards_same_tier_share_dimensions(self, qtbot):
        cc = ControlCard(self._control(), [])
        vc = CurveCard(CurveConfig(id="c1", name="C", type=CurveType.FLAT))
        qtbot.addWidget(cc)
        qtbot.addWidget(vc)
        assert cc.minimumWidth() == vc.minimumWidth()
        assert cc.minimumHeight() == vc.minimumHeight()

    def test_apply_card_size_rescales_with_font(self, qtbot):
        card = ControlCard(self._control(), [])
        qtbot.addWidget(card)
        card.apply_card_size(10, "comfortable")
        small = (card.minimumWidth(), card.minimumHeight())
        card.apply_card_size(16, "comfortable")
        big = (card.minimumWidth(), card.minimumHeight())
        assert big[0] > small[0]
        assert big[1] > small[1]

    def test_tier_param_changes_size(self, qtbot):
        compact = ControlCard(self._control(), [], card_size="compact")
        large = ControlCard(self._control(), [], card_size="large")
        qtbot.addWidget(compact)
        qtbot.addWidget(large)
        assert large.minimumWidth() > compact.minimumWidth()
        assert large.minimumHeight() > compact.minimumHeight()

    def test_curve_card_tier_param_changes_size(self, qtbot):
        compact = CurveCard(
            CurveConfig(id="c1", name="C", type=CurveType.FLAT), card_size="compact"
        )
        large = CurveCard(CurveConfig(id="c2", name="C", type=CurveType.FLAT), card_size="large")
        qtbot.addWidget(compact)
        qtbot.addWidget(large)
        assert large.minimumWidth() > compact.minimumWidth()
        assert large.minimumHeight() > compact.minimumHeight()


class TestCardSizeSetting:
    """The card_size AppSettings field defaults sanely and round-trips."""

    def test_default_is_comfortable(self):
        assert AppSettings().card_size == "comfortable"

    def test_roundtrip(self):
        s = AppSettings(card_size="large")
        assert AppSettings.from_dict(s.to_dict()).card_size == "large"

    def test_missing_key_defaults(self):
        assert AppSettings.from_dict({}).card_size == "comfortable"


class TestControlsPageTierPropagation:
    """The Controls page reads the tier from settings and applies it to cards."""

    def test_new_cards_use_settings_tier(self, qtbot, app_state, profile_service, settings_service):
        settings_service.settings.card_size = "large"
        page = ControlsPage(
            state=app_state,
            profile_service=profile_service,
            settings_service=settings_service,
        )
        qtbot.addWidget(page)
        page._on_new_control(single=True, name="R")
        assert page._control_cards
        w, _h = card_dimensions(active_theme().base_font_size_pt, "large")
        for card in page._control_cards.values():
            assert card.minimumWidth() == w

    def test_set_theme_reapplies_tier_and_font(
        self, qtbot, app_state, profile_service, settings_service
    ):
        page = ControlsPage(
            state=app_state,
            profile_service=profile_service,
            settings_service=settings_service,
        )
        qtbot.addWidget(page)
        page._on_new_control(single=True, name="R")
        assert page._control_cards
        settings_service.settings.card_size = "large"
        page.set_theme(ThemeTokens(base_font_size_pt=10))
        w, h = card_dimensions(10, "large")
        for card in page._control_cards.values():
            assert card.minimumWidth() == w
            assert card.minimumHeight() == h

    def test_default_tier_without_settings_service(self, qtbot, app_state, profile_service):
        # No settings service → comfortable default, no crash.
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        page._on_new_control(single=True, name="R")
        w, _h = card_dimensions(active_theme().base_font_size_pt, DEFAULT_CARD_SIZE)
        for card in page._control_cards.values():
            assert card.minimumWidth() == w


class TestSectionsSplitter5050:
    """The Fan Roles / Curves splitter defaults to a proportional 50/50 split."""

    def test_sections_balanced_on_resize(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        # A control must exist for the curves section to be visible.
        page._on_new_control(single=True, name="R")
        page.resize(900, 700)
        page.show()
        qtbot.waitExposed(page)
        splitter = page.findChild(QSplitter, "Controls_Splitter_sections")
        assert splitter is not None
        sizes = splitter.sizes()
        assert len(sizes) == 2
        assert min(sizes) > 0
        # Equal stretch + equal seed → ~50/50 (divider still user-draggable).
        assert abs(sizes[0] - sizes[1]) <= 16
