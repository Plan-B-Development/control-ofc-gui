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
        # The renew cadence is driven by the grant's renew_secs (the GUI ignores
        # ttl_secs entirely), so the deadman is honoured by re-pinning well inside
        # the daemon's TTL. _grant() advises renew_secs=5 → a 5000 ms interval.
        assert page._override_renew_timer.interval() == 5000
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

        client.override_release.assert_called_once_with("lc1", 7, timeout=2.0)
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
        # A non-actionable code (not_found) stays SILENT on the take path — only
        # thermal_abort / stale_fencing_token surface a message.
        assert page._unsaved_label.text() == ""

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

    def test_thermal_abort_surfaces_message_take_path(self, qtbot, app_state, profile_service):
        """T1b (take path): a thermal_abort on override_take must revert the card
        AND surface the safety message on the page status chip (not a silent
        revert like a benign lapse)."""
        from unittest.mock import MagicMock

        from control_ofc.api.errors import DaemonError

        client = MagicMock()
        client.override_take.side_effect = DaemonError(
            code="thermal_abort", message="held", status=409
        )
        page = self._live_page(qtbot, app_state, profile_service, client)
        assert page._unsaved_label.text() == ""  # baseline: nothing surfaced yet

        page._control_cards["lc1"]._manual_btn.setChecked(True)

        # Card reverts to auto.
        assert "lc1" not in page._overrides
        assert not page._control_cards["lc1"]._manual_btn.isChecked()
        # Thermal message surfaced.
        assert "thermal emergency" in page._unsaved_label.text()

    def test_thermal_abort_surfaces_message_renew_path(self, qtbot, app_state, profile_service):
        """T1b (renew path): a thermal_abort on override_renew must revert the card
        AND surface the safety message."""
        from unittest.mock import MagicMock

        from control_ofc.api.errors import DaemonError

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        client.override_renew.side_effect = DaemonError(
            code="thermal_abort", message="held", status=409
        )
        page = self._live_page(qtbot, app_state, profile_service, client)
        page._control_cards["lc1"]._manual_btn.setChecked(True)
        assert page._control_cards["lc1"]._manual_btn.isChecked()

        page._renew_overrides()

        assert "lc1" not in page._overrides
        # renew_secs bookkeeping drained in lockstep on the failure path.
        assert "lc1" not in page._override_renew_secs
        assert not page._control_cards["lc1"]._manual_btn.isChecked()
        assert "thermal emergency" in page._unsaved_label.text()

    def test_stale_token_surfaces_message_and_expiry_is_silent(
        self, qtbot, app_state, profile_service
    ):
        """T1e: a stale_fencing_token renew reject shows the 'superseded' message,
        whereas an override_expired reject stays SILENT — the distinctness that
        separates client supersession from a benign lapse. Both still revert."""
        from unittest.mock import MagicMock

        from control_ofc.api.errors import DaemonError

        # stale_fencing_token → superseded message on the chip.
        stale_client = MagicMock()
        stale_client.override_take.return_value = self._grant(token=7)
        stale_client.override_renew.side_effect = DaemonError(
            code="stale_fencing_token", message="superseded", status=409
        )
        stale_page = self._live_page(qtbot, app_state, profile_service, stale_client)
        stale_page._control_cards["lc1"]._manual_btn.setChecked(True)
        stale_page._renew_overrides()
        assert "lc1" not in stale_page._overrides
        assert not stale_page._control_cards["lc1"]._manual_btn.isChecked()
        assert "superseded" in stale_page._unsaved_label.text()

        # override_expired → quiet revert, NO message (the distinctness).
        expired_client = MagicMock()
        expired_client.override_take.return_value = self._grant(token=7)
        expired_client.override_renew.side_effect = DaemonError(
            code="override_expired", message="gone", status=404
        )
        expired_page = self._live_page(qtbot, app_state, profile_service, expired_client)
        expired_page._control_cards["lc1"]._manual_btn.setChecked(True)
        expired_page._renew_overrides()
        assert "lc1" not in expired_page._overrides
        assert not expired_page._control_cards["lc1"]._manual_btn.isChecked()
        assert expired_page._unsaved_label.text() == ""
        # Compare surfaces: supersession speaks, expiry stays silent.
        assert expired_page._unsaved_label.text() != stale_page._unsaved_label.text()

    def test_activation_success_clears_live_overrides(self, qtbot, app_state, profile_service):
        """T1c (DEC-214): a successful profile activation clears all held overrides
        and stops the renew timer. Activation now runs through the shared
        ``ProfileService.activate`` path; the page follows it via the
        ``active_changed`` signal → ``_on_active_profile_changed`` → ``_refresh_all``
        → ``_refresh_controls_grid`` → ``_release_all_overrides``."""
        from unittest.mock import MagicMock

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        page = self._live_page(qtbot, app_state, profile_service, client)

        # Hold a live override.
        page._control_cards["lc1"]._manual_btn.setChecked(True)
        assert page._overrides == {"lc1": 7}
        assert page._override_renew_timer.isActive()

        # Activating a DIFFERENT profile fires active_changed (set_active is
        # edge-triggered), which the page follows to rebuild the grid + release
        # every held override. The mock client's activate_profile returns a
        # truthy result so the daemon round-trip "succeeds".
        prof = page._profile_service.create_profile("Activate-Me")
        page._profile_service.activate(prof.id, client=client)

        assert page._overrides == {}
        assert page._override_renew_timer.isActive() is False

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

        client.override_release.assert_called_once_with("lc1", 7, timeout=2.0)
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

        client.override_take.assert_called_once_with("lc1", 80, timeout=2.0)

    def test_renew_interval_is_min_across_held_grants(self, qtbot, app_state, profile_service):
        """F-2: with heterogeneous renew cadences the single shared renew timer
        fires on the MIN cadence — a later, larger renew_secs must NOT stretch
        the interval past an earlier, shorter override's TTL (the old
        last-writer-wins bug, which setInterval would compound by also resetting
        the running countdown)."""
        from unittest.mock import MagicMock

        client = MagicMock()
        page = self._live_page(qtbot, app_state, profile_service, client)

        # Take the shorter-cadence override first (5 s), then a longer one (10 s).
        client.override_take.return_value = self._grant(token=1, renew_secs=5)
        page._take_override("lc1", 50)
        assert page._override_renew_timer.interval() == 5000

        client.override_take.return_value = self._grant(token=2, renew_secs=10)
        page._take_override("lc2", 50)

        # MIN(5, 10) = 5 s — NOT the last grant's 10 s.
        assert page._override_renew_timer.interval() == 5000
        assert page._override_renew_secs == {"lc1": 5, "lc2": 10}

    def test_renew_interval_tightens_when_shorter_grant_added(
        self, qtbot, app_state, profile_service
    ):
        """F-2: adding a shorter-cadence override to an existing longer one
        tightens the running timer down to the new minimum."""
        from unittest.mock import MagicMock

        client = MagicMock()
        page = self._live_page(qtbot, app_state, profile_service, client)

        client.override_take.return_value = self._grant(token=1, renew_secs=10)
        page._take_override("lc1", 50)
        assert page._override_renew_timer.interval() == 10000

        client.override_take.return_value = self._grant(token=2, renew_secs=5)
        page._take_override("lc2", 50)
        assert page._override_renew_timer.interval() == 5000  # tightened to the min

    def test_release_drops_renew_secs_entry(self, qtbot, app_state, profile_service):
        """The per-grant renew_secs bookkeeping is dropped in lockstep with the
        override token so a stale entry can't skew a later MIN."""
        from unittest.mock import MagicMock

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7, renew_secs=5)
        page = self._live_page(qtbot, app_state, profile_service, client)

        page._take_override("lc1", 50)
        assert page._override_renew_secs == {"lc1": 5}

        page._release_override("lc1")
        assert page._override_renew_secs == {}

    def test_take_uses_bounded_http_timeout(self, qtbot, app_state, profile_service):
        """F-3: override_take passes an explicit ~2 s HTTP timeout so a slow or
        half-dead daemon can only freeze the Qt main thread for ~2 s, not the
        full 5 s client default."""
        from unittest.mock import MagicMock

        from control_ofc.ui.pages.controls_page import _OVERRIDE_HTTP_TIMEOUT_S

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        page = self._live_page(qtbot, app_state, profile_service, client)

        page._take_override("lc1", 50)

        assert client.override_take.call_args.kwargs["timeout"] == _OVERRIDE_HTTP_TIMEOUT_S
        assert _OVERRIDE_HTTP_TIMEOUT_S == 2.0

    def test_renew_and_release_use_bounded_http_timeout(self, qtbot, app_state, profile_service):
        """F-3: renew and release also pass the bounded timeout (renew loops over
        every held override, so an unbounded call there is the worst freeze)."""
        from unittest.mock import MagicMock

        from control_ofc.api.models import OverrideRenewResult
        from control_ofc.ui.pages.controls_page import _OVERRIDE_HTTP_TIMEOUT_S

        client = MagicMock()
        client.override_take.return_value = self._grant(token=7)
        client.override_renew.return_value = OverrideRenewResult(
            control_id="lc1", override_token=8, ttl_secs=15, expires_in_secs=15
        )
        page = self._live_page(qtbot, app_state, profile_service, client)

        page._take_override("lc1", 50)
        page._renew_overrides()
        page._release_override("lc1")

        assert client.override_renew.call_args.kwargs["timeout"] == _OVERRIDE_HTTP_TIMEOUT_S
        assert client.override_release.call_args.kwargs["timeout"] == _OVERRIDE_HTTP_TIMEOUT_S


class TestCurveEditorSensorLabel:
    """Curve-editor sensor-combo label formatting (`_sensor_combo_label`)."""

    def test_renders_zero_celsius_and_hides_missing(self, qtbot, app_state, profile_service):
        """B2: a real 0.0 °C reading must render in the combo label (regression —
        a falsy `if s.value_c` dropped 0.0). A genuinely absent reading (None) stays
        hidden, and a normal value is unaffected."""
        from control_ofc.api.models import SensorReading

        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        zero = SensorReading(
            id="cpu", kind="CpuTemp", label="Tctl", value_c=0.0, chip_name="k10temp"
        )
        warm = SensorReading(
            id="cpu", kind="CpuTemp", label="Tctl", value_c=42.5, chip_name="k10temp"
        )
        missing = SensorReading(
            id="cpu", kind="CpuTemp", label="Tctl", value_c=None, chip_name="k10temp"
        )  # type: ignore[arg-type]
        assert "0.0" in page._sensor_combo_label(zero)  # was absent before the fix
        assert "42.5" in page._sensor_combo_label(warm)  # format unchanged
        assert "°C" not in page._sensor_combo_label(missing)  # None stays hidden


class TestOfflineDraftUX:
    """Offline Save/Activate UX (slice 6) built on the 6b daemon-backed
    persistence accessors (offline / unpublished_ids / is_published)."""

    # DEC-214: the combo "(draft)" badge tests (test_draft_badge_for_unpublished_profile,
    # test_no_draft_badge_in_local_mode) were deleted with the page profile combo —
    # the draft state still lives in ProfileService but is no longer shown on this page.
    # Their `_daemon_ps` helper went with them (no remaining caller).

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

    def test_save_active_published_reapplies(self, qtbot, app_state):
        """DEC-188: saving the ACTIVE published profile re-applies it to the
        daemon so the edited curve takes effect immediately, not on the next
        manual Activate."""
        from unittest.mock import MagicMock

        from control_ofc.api.models import ProfileActivateResult
        from control_ofc.services.profile_service import ProfileService

        client = MagicMock()
        client.create_profile.return_value = {"created": "p1"}
        client.activate_profile.return_value = ProfileActivateResult(
            activated=True, profile_id="p1", profile_name="P1"
        )
        ps = ProfileService(client=client)
        ps._profiles["p1"] = Profile(id="p1", name="P1")
        ps.set_active("p1")
        page = ControlsPage(state=app_state, profile_service=ps, client=client)
        qtbot.addWidget(page)

        page._on_save_profile()

        client.activate_profile.assert_called_once()
        assert page._unsaved_label.text() == "Saved & reapplied to daemon"
        assert page._unsaved_label.property("class") == "SuccessChip"

    def test_save_inactive_published_does_not_reapply(self, qtbot, app_state):
        """Saving a published but NON-active profile must not re-apply — that
        would wrongly switch the daemon's running profile (DEC-188)."""
        from unittest.mock import MagicMock

        from control_ofc.services.profile_service import ProfileService

        client = MagicMock()
        client.create_profile.return_value = {"created": "p1"}
        ps = ProfileService(client=client)
        ps._profiles["p1"] = Profile(id="p1", name="P1")
        ps._profiles["p2"] = Profile(id="p2", name="P2")
        ps.set_active("p2")  # p2 is active; we save the non-active p1
        page = ControlsPage(state=app_state, profile_service=ps, client=client)
        qtbot.addWidget(page)
        page.select_profile("p1")  # DEC-214: view p1 (replaces the removed combo)

        page._on_save_profile()

        client.activate_profile.assert_not_called()
        assert page._unsaved_label.text() == "Settings saved"
        assert page._unsaved_label.property("class") == "SuccessChip"

    def test_save_active_reapply_failure_warns(self, qtbot, app_state):
        """DEC-188: a failed re-apply warns but the local save is preserved, so
        the edit is never lost."""
        from unittest.mock import MagicMock

        from control_ofc.api.errors import DaemonError
        from control_ofc.services.profile_service import ProfileService

        client = MagicMock()
        client.create_profile.return_value = {"created": "p1"}
        client.activate_profile.side_effect = DaemonError(
            code="validation_error",
            message="boom",
            retryable=False,
            source="validation",
            status=400,
        )
        ps = ProfileService(client=client)
        ps._profiles["p1"] = Profile(id="p1", name="P1")
        ps.set_active("p1")
        page = ControlsPage(state=app_state, profile_service=ps, client=client)
        qtbot.addWidget(page)

        page._on_save_profile()

        client.activate_profile.assert_called_once()
        assert "reapply failed" in page._unsaved_label.text().lower()
        assert page._unsaved_label.property("class") == "WarningChip"
        # The local save still succeeded (published), so the edit is not lost.
        assert ps.is_published("p1")

    def test_save_active_reapply_soft_rejection_warns(self, qtbot, app_state):
        """DEC-188: a daemon that returns ``activated=False`` (no exception) is
        treated as a failed re-apply — warn, and keep the local save."""
        from unittest.mock import MagicMock

        from control_ofc.api.models import ProfileActivateResult
        from control_ofc.services.profile_service import ProfileService

        client = MagicMock()
        client.create_profile.return_value = {"created": "p1"}
        client.activate_profile.return_value = ProfileActivateResult(
            activated=False, profile_id="p1", profile_name="P1"
        )
        ps = ProfileService(client=client)
        ps._profiles["p1"] = Profile(id="p1", name="P1")
        ps.set_active("p1")
        page = ControlsPage(state=app_state, profile_service=ps, client=client)
        qtbot.addWidget(page)

        page._on_save_profile()

        assert "reapply failed" in page._unsaved_label.text().lower()
        assert page._unsaved_label.property("class") == "WarningChip"

    # DEC-214: the Activate-button enable/disable tests (test_activate_disabled_when_offline_live,
    # test_activate_stays_enabled_in_demo) were deleted with the page Activate button —
    # activation moved to the sidebar Apply flow, and `_on_connection_changed` no longer
    # gates any button (it only clears stale foreign-override chips on disconnect, DEC-169).
