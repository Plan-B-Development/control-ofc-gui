"""DEC-129: per-card user resize with snap + bounded curve preview.

Covers:
- ``snap_size``: lattice rounding, clamping, idempotence, and the
  equal-sizes affordance (nearby drags land on the same lattice point).
- ``CardResizeGrip``: drag resizes the card live on the lattice, emits
  ``resize_finished`` on release, and never reaches the flow container's
  reorder filter or the card's click-to-select.
- Card override API: ``apply_card_size(user_size=...)`` fixes both
  dimensions; theme/tier re-apply clamps but never clears; reset restores
  the DEC-128 contract (fixed width, uncapped height).
- ``AppSettings.controls_card_sizes`` round-trip + page wiring (persist on
  grip release, delete on reset, prune orphans across all profiles).
- ``CurvePreview``: constant size hint (pixmap-ratchet regression) and the
  tightened card spacing.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from control_ofc.services.app_settings_service import AppSettings
from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurvePoint,
    CurveType,
    LogicalControl,
)
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.theme import ThemeTokens, active_theme
from control_ofc.ui.widgets.card_metrics import (
    MIN_USER_CARD_WIDTH_PX,
    SNAP_STEP_PX,
    card_dimensions,
)
from control_ofc.ui.widgets.card_resize import snap_size
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.curve_card import CurveCard, CurvePreview
from control_ofc.ui.widgets.draggable_flow import DraggableFlowContainer


def _control(cid: str = "r1") -> LogicalControl:
    return LogicalControl(id=cid, name="Role", mode=ControlMode.CURVE)


def _graph_curve(cid: str = "g1") -> CurveConfig:
    return CurveConfig(
        id=cid,
        name="Graph",
        type=CurveType.GRAPH,
        points=[CurvePoint(30.0, 20.0), CurvePoint(50.0, 50.0), CurvePoint(80.0, 100.0)],
    )


def _send_mouse(widget, etype, local: QPoint, button=Qt.MouseButton.LeftButton) -> None:
    """Deliver a synthesized mouse event (deterministic — no WM cursor moves)."""
    if etype == QEvent.Type.MouseMove:
        button_arg, buttons = Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton
    elif etype == QEvent.Type.MouseButtonRelease:
        button_arg, buttons = button, Qt.MouseButton.NoButton
    else:
        button_arg, buttons = button, button
    local_f = QPointF(local)
    global_f = QPointF(widget.mapToGlobal(local))
    event = QMouseEvent(
        etype, local_f, local_f, global_f, button_arg, buttons, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(widget, event)


class TestSnapSize:
    """Pure lattice math: round half-up, clamp to a lattice-aligned floor."""

    def test_rounds_to_nearest_step(self):
        assert snap_size(283, 167, 0, 0) == (280, 160)
        assert snap_size(291, 173, 0, 0) == (300, 180)

    def test_half_rounds_up(self):
        assert snap_size(290, 170, 0, 0) == (300, 180)

    def test_clamps_to_lattice_aligned_floor(self):
        # Floor 113 is off-lattice: it rounds *up* to 120 so the result is
        # both on-lattice and never below the content minimum.
        width, height = snap_size(50, 50, MIN_USER_CARD_WIDTH_PX, 113)
        assert (width, height) == (MIN_USER_CARD_WIDTH_PX, 120)
        assert width % SNAP_STEP_PX == 0
        assert height % SNAP_STEP_PX == 0

    def test_idempotent(self):
        once = snap_size(347, 291, 220, 113)
        assert snap_size(*once, 220, 113) == once

    def test_nearby_drags_land_on_equal_sizes(self):
        # The point of the absolute lattice: two cards resized "close enough"
        # end up *exactly* equal.
        assert snap_size(295, 175, 0, 0) == snap_size(305, 185, 0, 0) == (300, 180)


class TestCardOverrideApi:
    """set_user_size / apply_card_size(user_size=...) / clear_user_size."""

    def test_set_user_size_snaps_and_clamps(self, qtbot):
        card = ControlCard(_control(), [])
        qtbot.addWidget(card)
        applied = card.set_user_size(100, 10)
        assert applied[0] == MIN_USER_CARD_WIDTH_PX
        assert applied[1] >= card.layout().minimumSize().height()
        assert applied[0] % SNAP_STEP_PX == 0
        assert applied[1] % SNAP_STEP_PX == 0
        assert card.user_size == applied

    def test_override_fixes_both_dimensions(self, qtbot):
        card = CurveCard(_graph_curve(), user_size=(340, 220))
        qtbot.addWidget(card)
        assert card.minimumWidth() == card.maximumWidth() == 340
        assert card.minimumHeight() == card.maximumHeight() == 220

    def test_theme_reapply_keeps_override(self, qtbot):
        card = ControlCard(_control(), [], user_size=(340, 220))
        qtbot.addWidget(card)
        card.apply_card_size(16, "large")
        # Tier/theme change clamps but never clears (DEC-129).
        assert card.user_size == (340, 220)
        assert card.minimumHeight() == card.maximumHeight() == 220

    def test_clear_restores_dec128_contract(self, qtbot):
        card = ControlCard(_control(), [], user_size=(340, 220))
        qtbot.addWidget(card)
        card.clear_user_size()
        w, h = card_dimensions(active_theme().base_font_size_pt, "comfortable")
        assert card.user_size is None
        assert card.minimumWidth() == card.maximumWidth() == w
        assert card.minimumHeight() == h
        assert card.maximumHeight() > 100000  # height floor, no cap

    def test_update_control_reclamps_override(self, qtbot):
        card = ControlCard(_control(), [], user_size=(340, 220))
        qtbot.addWidget(card)
        floor = card.layout().minimumSize().height()
        card.update_control(_control(), [])
        # Still fixed, still on-lattice, still >= the content minimum.
        assert card.minimumHeight() == card.maximumHeight()
        assert card.minimumHeight() % SNAP_STEP_PX == 0
        assert card.minimumHeight() >= floor


class TestGripGestures:
    """Grip drags resize on the lattice and never leak into reorder/select."""

    def _drag(self, qtbot, container, card):
        grip = card._grip
        start = QPoint(grip.width() // 2, grip.height() // 2)
        _send_mouse(grip, QEvent.Type.MouseButtonPress, start)
        _send_mouse(grip, QEvent.Type.MouseMove, start + QPoint(45, 45))
        _send_mouse(grip, QEvent.Type.MouseButtonRelease, start + QPoint(45, 45))

    def test_drag_resizes_live_on_lattice(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        card = ControlCard(_control("r1"), [])
        container.add_card(card, "r1")
        container.resize(900, 600)
        container.show()
        start_w, start_h = card.width(), card.height()

        resized = []
        card.resized.connect(lambda cid, w, h: resized.append((cid, w, h)))
        self._drag(qtbot, container, card)

        assert card.width() % SNAP_STEP_PX == 0
        assert card.height() % SNAP_STEP_PX == 0
        assert card.width() > start_w
        assert card.height() > start_h
        assert resized == [("r1", card.width(), card.height())]

    def test_grip_drag_never_starts_reorder_or_select(self, qtbot, monkeypatch):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        card = ControlCard(_control("r1"), [])
        other = ControlCard(_control("r2"), [])
        container.add_card(card, "r1")
        container.add_card(other, "r2")
        container.resize(900, 600)
        container.show()

        reorder_calls = []
        monkeypatch.setattr(container, "_start_drag", lambda w: reorder_calls.append(w))
        selections = []
        card.selected.connect(selections.append)
        order_changes = []
        container.order_changed.connect(order_changes.append)

        self._drag(qtbot, container, card)

        # 45px exceeds the 10px reorder threshold — if grip events leaked to
        # the card, the container's event filter would have started a drag.
        assert reorder_calls == []
        assert selections == []
        assert order_changes == []

    def test_double_click_resets_to_theme_size(self, qtbot):
        card = ControlCard(_control("r1"), [], user_size=(340, 220))
        qtbot.addWidget(card)
        resets = []
        card.size_reset.connect(resets.append)

        grip = card._grip
        center = QPoint(grip.width() // 2, grip.height() // 2)
        _send_mouse(grip, QEvent.Type.MouseButtonDblClick, center)

        w, h = card_dimensions(active_theme().base_font_size_pt, "comfortable")
        assert resets == ["r1"]
        assert card.user_size is None
        assert card.minimumWidth() == card.maximumWidth() == w
        assert card.minimumHeight() == h
        assert card.maximumHeight() > 100000


class TestSizePersistence:
    """controls_card_sizes round-trips and reaches rebuilt cards."""

    def test_settings_roundtrip(self):
        s = AppSettings(controls_card_sizes={"r1": [340, 220]})
        assert AppSettings.from_dict(s.to_dict()).controls_card_sizes == {"r1": [340, 220]}

    def test_missing_key_defaults_empty(self):
        assert AppSettings.from_dict({}).controls_card_sizes == {}

    def _page(self, qtbot, app_state, profile_service, settings_service) -> ControlsPage:
        page = ControlsPage(
            state=app_state,
            profile_service=profile_service,
            settings_service=settings_service,
        )
        qtbot.addWidget(page)
        page._on_new_control(single=True, name="R")
        assert page._control_cards
        return page

    def test_grip_release_persists_size(self, qtbot, app_state, profile_service, settings_service):
        page = self._page(qtbot, app_state, profile_service, settings_service)
        cid, card = next(iter(page._control_cards.items()))
        card._grip.resize_finished.emit(340, 220)
        assert settings_service.settings.controls_card_sizes[cid] == [340, 220]

    def test_stored_size_reaches_rebuilt_cards(
        self, qtbot, app_state, profile_service, settings_service
    ):
        page = self._page(qtbot, app_state, profile_service, settings_service)
        cid = next(iter(page._control_cards))
        settings_service.settings.controls_card_sizes = {cid: [340, 220]}
        page._refresh_all()
        card = page._control_cards[cid]
        assert card.minimumWidth() == card.maximumWidth() == 340
        assert card.minimumHeight() == card.maximumHeight() == 220

    def test_override_survives_set_theme(self, qtbot, app_state, profile_service, settings_service):
        page = self._page(qtbot, app_state, profile_service, settings_service)
        cid = next(iter(page._control_cards))
        settings_service.settings.controls_card_sizes = {cid: [340, 220]}
        page._refresh_all()
        settings_service.settings.card_size = "large"
        page.set_theme(ThemeTokens(base_font_size_pt=14))
        card = page._control_cards[cid]
        assert card.minimumWidth() == card.maximumWidth() == 340
        assert card.minimumHeight() == card.maximumHeight() == 220

    def test_reset_deletes_stored_size(self, qtbot, app_state, profile_service, settings_service):
        page = self._page(qtbot, app_state, profile_service, settings_service)
        cid, card = next(iter(page._control_cards.items()))
        settings_service.settings.controls_card_sizes = {cid: [340, 220]}
        card._grip.reset_requested.emit()
        assert cid not in settings_service.settings.controls_card_sizes
        w, _h = card_dimensions(active_theme().base_font_size_pt, "comfortable")
        assert card.minimumWidth() == card.maximumWidth() == w
        assert card.maximumHeight() > 100000

    def test_prune_drops_orphans_keeps_all_profiles(
        self, qtbot, app_state, profile_service, settings_service
    ):
        page = self._page(qtbot, app_state, profile_service, settings_service)
        cid = next(iter(page._control_cards))
        # A control in a *different* (inactive) profile must survive pruning.
        other_profile = profile_service.create_profile("Other")
        other_profile.controls.append(_control("other_ctl"))
        settings_service.settings.controls_card_sizes = {
            "ghost": [300, 300],
            "other_ctl": [320, 200],
        }
        page._on_card_user_resized(cid, 340, 220)
        sizes = settings_service.settings.controls_card_sizes
        assert "ghost" not in sizes
        assert sizes["other_ctl"] == [320, 200]
        assert sizes[cid] == [340, 220]

    def test_malformed_stored_size_ignored(
        self, qtbot, app_state, profile_service, settings_service
    ):
        page = self._page(qtbot, app_state, profile_service, settings_service)
        cid = next(iter(page._control_cards))
        settings_service.settings.controls_card_sizes = {cid: ["wide", None]}
        page._refresh_all()  # must not raise
        card = page._control_cards[cid]
        # Fell back to DEC-128 theme sizing.
        assert card.maximumHeight() > 100000


class TestCurvePreviewBounded:
    """The owner-drawn preview can never re-inflate the card (DEC-129)."""

    def test_size_hint_constant_across_resizes(self, qtbot):
        card = CurveCard(_graph_curve())
        qtbot.addWidget(card)
        hint = card._preview.sizeHint()
        for size in ((500, 400), (700, 600), (300, 160)):
            card.set_user_size(*size)
            card._preview.grab()  # force a paint pass at the new size
            assert card._preview.sizeHint() == hint

    def test_default_graph_card_is_not_a_tower(self, qtbot):
        # Regression: the old QLabel+pixmap preview ratcheted graph cards to
        # 500+px size hints (render→hint→grant→render). The owner-drawn
        # preview keeps the default card near the theme floor.
        card = CurveCard(_graph_curve())
        qtbot.addWidget(card)
        assert card.sizeHint().height() < 300

    def test_preview_repaints_after_update_curve(self, qtbot):
        card = CurveCard(_graph_curve())
        qtbot.addWidget(card)
        flat = CurveConfig(id="g1", name="Now flat", type=CurveType.FLAT, flat_output_pct=42.0)
        card.update_curve(flat)
        assert card._preview.curve is flat
        assert "42" in card._preview.summary_text()

    def test_preview_paints_without_crash(self, qtbot):
        # Paint all three branches (graph / degenerate graph / text).
        for curve in (
            _graph_curve(),
            CurveConfig(id="c1", name="One", type=CurveType.GRAPH, points=[CurvePoint(50, 50)]),
            CurveConfig(id="c2", name="Flat", type=CurveType.FLAT, flat_output_pct=50.0),
        ):
            preview = CurvePreview()
            qtbot.addWidget(preview)
            preview.set_curve(curve)
            assert not preview.grab().isNull()


class TestTightenedSpacing:
    """Cards pack their rows instead of spreading surplus between them."""

    def test_card_layouts_use_tight_metrics(self, qtbot):
        control_card = ControlCard(_control(), [])
        curve_card = CurveCard(_graph_curve())
        qtbot.addWidget(control_card)
        qtbot.addWidget(curve_card)
        for card in (control_card, curve_card):
            margins = card.layout().contentsMargins()
            assert card.layout().spacing() == 2
            assert (margins.top(), margins.bottom()) == (4, 4)

    def test_control_card_hint_below_old_floor(self, qtbot):
        # Pre-DEC-129 the comfortable floor was 188px while content needed
        # ~133px — the surplus padded out the text rows. The retuned floor +
        # tighter rows keep the whole card under the old floor at default pt.
        card = ControlCard(_control(), [])
        qtbot.addWidget(card)
        assert card.sizeHint().height() < 188
