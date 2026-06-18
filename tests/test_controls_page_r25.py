"""Tests for Refinement 25: Card ordering, fixed sizing, flow layout, syslog fields."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from control_ofc.services.profile_service import (
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
)
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.draggable_flow import DraggableFlowContainer
from control_ofc.ui.widgets.flow_layout import FlowLayout


class TestFlowLayoutInvalidation:
    """FlowLayout.addItem() must trigger invalidation so cards are positioned."""

    def test_add_item_invalidates(self, qtbot):
        container = DraggableFlowContainer()
        qtbot.addWidget(container)
        btn = QPushButton("Test")
        btn.setFixedSize(100, 50)
        container.add_card(btn, "t1")
        # After add, layout should have the item
        assert container.flow_layout().count() == 1

    def test_add_item_calls_invalidate(self, qtbot):
        """FlowLayout.addItem() must call invalidate() so layout recalculates.

        Verify by showing the container and checking positions after Qt processes events.
        """
        from PySide6.QtWidgets import QWidget

        container = QWidget()
        layout = FlowLayout(container, margin=4, h_spacing=6, v_spacing=6)
        qtbot.addWidget(container)
        container.resize(500, 200)
        container.show()
        qtbot.waitExposed(container)

        for i in range(3):
            btn = QPushButton(f"Card {i}")
            btn.setFixedSize(100, 50)
            layout.addWidget(btn)

        # Process events so Qt triggers setGeometry → _do_layout
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()

        # Cards should now have been positioned at different x offsets
        positions = set()
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item and item.geometry().width() > 0:
                positions.add(item.geometry().x())
        assert len(positions) > 1, "Cards must have different x positions after layout"

    def test_cards_positioned_in_hidden_parent(self, qtbot):
        """Cards must be positioned even when parent is not yet shown.

        This is the root cause of R27: FlowLayout used isVisible() which
        checks the entire parent chain. Before the window is shown, all
        children report isVisible()=False, so _do_layout() skipped them
        all, leaving cards stacked at (0,0). The fix uses isHidden()
        which only checks if the widget itself was explicitly hidden.
        """
        from PySide6.QtCore import QRect
        from PySide6.QtWidgets import QWidget

        container = QWidget()  # NOT shown — simulates pre-show construction
        layout = FlowLayout(container, margin=4, h_spacing=6, v_spacing=6)
        qtbot.addWidget(container)

        for i in range(3):
            btn = QPushButton(f"Card {i}")
            btn.setFixedSize(100, 50)
            layout.addWidget(btn)

        # Force layout calculation at width=500 — simulates what Qt does on resize
        layout._do_layout(QRect(0, 0, 500, 200), test_only=False)

        # Cards must have different x positions, not all stacked at (0,0)
        positions = set()
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item and item.geometry().width() > 0:
                positions.add(item.geometry().x())
        assert len(positions) > 1, "Cards stacked — layout skipped items in hidden parent"


class TestCurveCardAppend:
    """New curve cards append to the end of the sequence."""

    def test_new_curve_appends_to_end(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        profile = page._get_current_profile()
        initial_count = len(profile.curves)

        # Add a new curve
        page._on_add_curve(CurveType.FLAT)

        profile = page._get_current_profile()
        assert len(profile.curves) == initial_count + 1
        assert profile.curves[-1].name.startswith("New Flat")

    def test_existing_curve_order_preserved_after_add(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        profile = page._get_current_profile()
        original_ids = [c.id for c in profile.curves]

        page._on_add_curve(CurveType.LINEAR)

        profile = page._get_current_profile()
        new_ids = [c.id for c in profile.curves]
        # Original IDs should appear at the same positions
        assert new_ids[: len(original_ids)] == original_ids


class TestCurveOrderStability:
    """Curve card order survives refresh/rebuild cycles."""

    def test_order_survives_refresh(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        profile = page._get_current_profile()
        if len(profile.curves) < 2:
            page._on_add_curve(CurveType.FLAT)
            page._on_add_curve(CurveType.LINEAR)
            profile = page._get_current_profile()

        ids_before = [c.id for c in profile.curves]
        page._refresh_all()
        ids_after = [c.id for c in profile.curves]
        assert ids_before == ids_after

    def test_reorder_persists_through_refresh(self):
        """Model-level: reordering profile.curves survives iteration."""
        c1 = CurveConfig(id="c1", name="A", type=CurveType.FLAT)
        c2 = CurveConfig(id="c2", name="B", type=CurveType.LINEAR)
        c3 = CurveConfig(id="c3", name="C", type=CurveType.GRAPH)
        profile = Profile(id="test", name="Test", curves=[c1, c2, c3])

        # Simulate drag reorder: c3, c1, c2
        curve_map = {c.id: c for c in profile.curves}
        profile.curves = [curve_map["c3"], curve_map["c1"], curve_map["c2"]]

        # Simulate refresh: iterate profile.curves
        rebuilt = [c.id for c in profile.curves]
        assert rebuilt == ["c3", "c1", "c2"]


class TestControlCardSizing:
    """Fan Role cards: fixed width, minimum-height floor (DEC-128)."""

    def test_control_card_width_fixed_height_floored(self, qtbot):
        control = LogicalControl(name="Test Role", mode=ControlMode.CURVE)
        card = ControlCard(control, [])
        qtbot.addWidget(card)
        from control_ofc.ui.theme import active_theme
        from control_ofc.ui.widgets.card_metrics import DEFAULT_CARD_SIZE, card_dimensions

        w, h = card_dimensions(active_theme().base_font_size_pt, DEFAULT_CARD_SIZE)
        assert card.minimumWidth() == w
        assert card.maximumWidth() == w
        assert card.minimumHeight() == h
        # Height is a floor, not a cap — content can grow the card taller.
        assert card.maximumHeight() > h


class TestControlCardFlowContainer:
    """Fan Role cards use DraggableFlowContainer like Curve cards."""

    def test_controls_use_flow_container(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        assert hasattr(page, "_controls_flow")
        assert isinstance(page._controls_flow, DraggableFlowContainer)

    def test_new_control_appends_to_end(self, qtbot, app_state, profile_service):
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        profile = page._get_current_profile()
        initial_count = len(profile.controls)

        page._on_new_control(single=True, name="Appended Role")

        profile = page._get_current_profile()
        assert len(profile.controls) == initial_count + 1
        assert profile.controls[-1].name == "Appended Role"

    def test_control_reorder_syncs_model(self):
        """Reordering controls updates profile.controls list."""
        c1 = LogicalControl(id="r1", name="First", mode=ControlMode.CURVE)
        c2 = LogicalControl(id="r2", name="Second", mode=ControlMode.CURVE)
        c3 = LogicalControl(id="r3", name="Third", mode=ControlMode.CURVE)
        profile = Profile(id="test", name="Test", controls=[c1, c2, c3])

        # Simulate reorder: r3, r1, r2
        control_map = {c.id: c for c in profile.controls}
        profile.controls = [control_map["r3"], control_map["r1"], control_map["r2"]]

        assert profile.controls[0].name == "Third"
        assert profile.controls[1].name == "First"
        assert profile.controls[2].name == "Second"


class TestManualOverrideWiring:
    """A card's Manual toggle must drive the control loop's per-control API."""

    @staticmethod
    def _page_with_one_control(qtbot, app_state, profile_service, mock_loop):
        from control_ofc.services.profile_service import ControlMember

        page = ControlsPage(
            state=app_state, profile_service=profile_service, demo_controller=mock_loop
        )
        qtbot.addWidget(page)
        curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
        ctrl = LogicalControl(
            id="lc1",
            name="LC",
            mode=ControlMode.CURVE,
            curve_id="c1",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        page._refresh_controls_grid(Profile(id="p", name="P", controls=[ctrl], curves=[curve]))
        return page

    def test_toggle_on_calls_set_control_manual(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        mock_loop = MagicMock()
        page = self._page_with_one_control(qtbot, app_state, profile_service, mock_loop)

        page._control_cards["lc1"]._manual_btn.setChecked(True)

        mock_loop.set_control_manual.assert_called_once()
        assert mock_loop.set_control_manual.call_args[0][0] == "lc1"

    def test_toggle_off_calls_clear_control_manual(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        mock_loop = MagicMock()
        page = self._page_with_one_control(qtbot, app_state, profile_service, mock_loop)

        btn = page._control_cards["lc1"]._manual_btn
        btn.setChecked(True)
        btn.setChecked(False)

        mock_loop.clear_control_manual.assert_called_once_with("lc1")


class TestManualOverrideLiveWiring:
    """In live (daemon-connected) mode the Manual toggle drives the daemon
    override API (DEC-163), not the local loop — with renew + fail-safe revert."""

    @staticmethod
    def _grant(token=1, renew_secs=5):
        from control_ofc.api.models import OverrideGrant

        return OverrideGrant(
            control_id="lc1",
            override_token=token,
            pwm_percent=50,
            ttl_secs=15,
            renew_secs=renew_secs,
            expires_in_secs=15,
        )

    @classmethod
    def _live_page(cls, qtbot, app_state, profile_service, client):
        from control_ofc.services.profile_service import ControlMember

        page = ControlsPage(state=app_state, profile_service=profile_service, client=client)
        qtbot.addWidget(page)
        curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
        ctrl = LogicalControl(
            id="lc1",
            name="LC",
            mode=ControlMode.CURVE,
            curve_id="c1",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        page._refresh_controls_grid(Profile(id="p", name="P", controls=[ctrl], curves=[curve]))
        return page

    def test_toggle_on_takes_override_and_renews(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        page = self._live_page(qtbot, app_state, profile_service, client)

        page._control_cards["lc1"]._manual_btn.setChecked(True)

        client.override_take.assert_called_once()
        assert client.override_take.call_args[0][0] == "lc1"
        assert page._overrides["lc1"] == 7
        assert page._override_renew_timer.isActive()
        # The demo controller is never present in live mode.
        assert page._demo_controller is None

    def test_toggle_off_releases_override(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        page = self._live_page(qtbot, app_state, profile_service, client)

        btn = page._control_cards["lc1"]._manual_btn
        btn.setChecked(True)
        btn.setChecked(False)

        client.override_release.assert_called_once_with("lc1", 7)
        assert "lc1" not in page._overrides
        assert not page._override_renew_timer.isActive()

    def test_take_failure_reverts_card(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        from control_ofc.api.errors import DaemonError

        client = MagicMock()
        client.override_take.side_effect = DaemonError(code="not_found", message="x", status=404)
        page = self._live_page(qtbot, app_state, profile_service, client)

        page._control_cards["lc1"]._manual_btn.setChecked(True)

        assert "lc1" not in page._overrides
        assert not page._control_cards["lc1"]._manual_btn.isChecked()

    def test_renew_failure_reverts_card(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        from control_ofc.api.errors import DaemonError

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        client.override_renew.side_effect = DaemonError(
            code="override_expired", message="gone", status=404
        )
        page = self._live_page(qtbot, app_state, profile_service, client)
        page._control_cards["lc1"]._manual_btn.setChecked(True)
        assert page._control_cards["lc1"]._manual_btn.isChecked()

        page._renew_overrides()

        assert "lc1" not in page._overrides
        assert not page._control_cards["lc1"]._manual_btn.isChecked()

    def test_renew_updates_token(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        from control_ofc.api.models import OverrideRenewResult

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        client.override_renew.return_value = OverrideRenewResult(
            control_id="lc1", override_token=8, ttl_secs=15, expires_in_secs=15
        )
        page = self._live_page(qtbot, app_state, profile_service, client)
        page._control_cards["lc1"]._manual_btn.setChecked(True)

        page._renew_overrides()

        assert page._overrides["lc1"] == 8

    def test_rebuild_releases_held_overrides(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        page = self._live_page(qtbot, app_state, profile_service, client)
        page._control_cards["lc1"]._manual_btn.setChecked(True)
        assert page._overrides

        # Rebuilding the grid (e.g. a profile switch) must release the override
        # so card state never diverges from the daemon.
        page._refresh_controls_grid(Profile(id="p", name="P", controls=[], curves=[]))

        client.override_release.assert_called_once_with("lc1", 7)
        assert not page._overrides

    def test_slider_drag_debounces_into_one_repin(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        page = self._live_page(qtbot, app_state, profile_service, client)
        page._control_cards["lc1"]._manual_btn.setChecked(True)
        client.override_take.reset_mock()

        # Several rapid drag values queue; one flush re-pins with only the last.
        page._on_card_manual_value("lc1", 60)
        page._on_card_manual_value("lc1", 70)
        page._on_card_manual_value("lc1", 80)
        page._flush_override_values()

        client.override_take.assert_called_once_with("lc1", 80)


class TestOfflineDraftUX:
    """Offline Save/Activate UX (slice 6) built on the 6b daemon-backed
    persistence accessors (offline / unpublished_ids / is_published)."""

    @staticmethod
    def _daemon_ps(*, published=(), local=()):
        from unittest.mock import MagicMock

        from control_ofc.services.profile_service import ProfileService

        ps = ProfileService(client=MagicMock())
        for pid in published:
            ps._profiles[pid] = Profile(id=pid, name=pid.title())
            ps._daemon_ids.add(pid)
        for pid in local:
            ps._profiles[pid] = Profile(id=pid, name=pid.title())
        return ps

    def test_draft_badge_for_unpublished_profile(self, qtbot, app_state):
        from unittest.mock import MagicMock

        ps = self._daemon_ps(published=["pub"], local=["drf"])
        page = ControlsPage(state=app_state, profile_service=ps, client=MagicMock())
        qtbot.addWidget(page)

        labels = [page._profile_combo.itemText(i) for i in range(page._profile_combo.count())]
        pub_label = next(label for label in labels if "Pub" in label)
        drf_label = next(label for label in labels if "Drf" in label)
        assert "(draft)" not in pub_label
        assert "(draft)" in drf_label

    def test_no_draft_badge_in_local_mode(self, qtbot, app_state):
        from control_ofc.services.profile_service import ProfileService

        ps = ProfileService()  # no client -> pure local, no daemon concept
        ps._profiles["p1"] = Profile(id="p1", name="Local")
        page = ControlsPage(state=app_state, profile_service=ps, client=None)
        qtbot.addWidget(page)

        labels = [page._profile_combo.itemText(i) for i in range(page._profile_combo.count())]
        assert all("(draft)" not in label for label in labels)

    def test_save_offline_marks_draft(self, qtbot, app_state):
        from unittest.mock import MagicMock

        from control_ofc.api.errors import DaemonTimeout
        from control_ofc.services.profile_service import ProfileService

        client = MagicMock()
        client.create_profile.side_effect = DaemonTimeout()
        ps = ProfileService(client=client)
        ps._profiles["p1"] = Profile(id="p1", name="P1")
        ps.set_active("p1")
        page = ControlsPage(state=app_state, profile_service=ps, client=client)
        qtbot.addWidget(page)

        page._on_save_profile()

        assert "not published" in page._unsaved_label.text().lower()
        assert page._unsaved_label.property("class") == "WarningChip"

    def test_save_published_shows_saved(self, qtbot, app_state):
        from unittest.mock import MagicMock

        from control_ofc.services.profile_service import ProfileService

        client = MagicMock()
        client.create_profile.return_value = {"created": "p1"}
        ps = ProfileService(client=client)
        ps._profiles["p1"] = Profile(id="p1", name="P1")
        ps.set_active("p1")
        page = ControlsPage(state=app_state, profile_service=ps, client=client)
        qtbot.addWidget(page)

        page._on_save_profile()

        assert page._unsaved_label.text() == "Settings saved"
        assert page._unsaved_label.property("class") == "SuccessChip"

    def test_activate_disabled_when_offline_live(self, qtbot, app_state):
        from unittest.mock import MagicMock

        from control_ofc.api.models import ConnectionState

        page = ControlsPage(state=app_state, client=MagicMock())
        qtbot.addWidget(page)

        page._on_connection_changed(ConnectionState.DISCONNECTED)
        assert not page._activate_btn.isEnabled()

        page._on_connection_changed(ConnectionState.CONNECTED)
        assert page._activate_btn.isEnabled()

    def test_activate_stays_enabled_in_demo(self, qtbot, app_state):
        from control_ofc.api.models import ConnectionState

        page = ControlsPage(state=app_state, client=None)  # demo / local
        qtbot.addWidget(page)

        page._on_connection_changed(ConnectionState.DISCONNECTED)
        assert page._activate_btn.isEnabled()  # local activation is never gated
