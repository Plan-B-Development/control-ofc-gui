"""Capability gating tests — UI enables/disables based on daemon capabilities."""

from __future__ import annotations

import pytest

from control_ofc.api.models import Capabilities, FeatureFlags
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    CurveConfig,
    CurveType,
    LogicalControl,
    Profile,
)
from control_ofc.ui.main_window import MainWindow


def _profile_with_control() -> Profile:
    """Minimal profile carrying exactly one control card."""
    curve = CurveConfig(
        id="c1", name="C", type=CurveType.FLAT, sensor_id="cpu", flat_output_pct=40.0
    )
    ctrl = LogicalControl(
        id="lc1",
        name="LC",
        mode=ControlMode.CURVE,
        curve_id="c1",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    return Profile(id="p1", name="P1", controls=[ctrl], curves=[curve])


def _no_write_caps() -> Capabilities:
    return Capabilities(
        daemon_version="0.2.0",
        features=FeatureFlags(openfan_write_supported=False, hwmon_write_supported=False),
    )


def _write_caps() -> Capabilities:
    return Capabilities(
        daemon_version="0.2.0",
        features=FeatureFlags(openfan_write_supported=True),
    )


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


class TestCapabilityGating:
    def test_controls_disabled_when_no_write_support(self, qtbot, window, app_state):
        """capabilities with no write support -> control cards disabled."""
        # First ensure we have cards by selecting a profile
        window.controls_page._profile_combo.setCurrentIndex(0)

        caps = Capabilities(
            daemon_version="0.2.0",
            features=FeatureFlags(
                openfan_write_supported=False,
                hwmon_write_supported=False,
            ),
        )
        app_state.set_capabilities(caps)

        for card in window.controls_page._control_cards.values():
            assert not card.isEnabled()

    def test_controls_enabled_with_write_support(self, qtbot, window, app_state):
        """capabilities with write support -> control cards stay enabled."""
        window.controls_page._profile_combo.setCurrentIndex(0)

        caps = Capabilities(
            daemon_version="0.2.0",
            features=FeatureFlags(openfan_write_supported=True),
        )
        app_state.set_capabilities(caps)

        # Cards should remain enabled (default state)
        for card in window.controls_page._control_cards.values():
            assert card.isEnabled()

    def test_controls_reenable_after_write_returns(self, qtbot, window, app_state):
        """Cards disabled by a no-write snapshot must re-enable when write support returns.

        Regression: ``_on_capabilities_updated`` previously only ever disabled
        cards, so a transient/incomplete capabilities snapshot stranded them
        disabled for the rest of the session.
        """
        window.controls_page._profile_combo.setCurrentIndex(0)
        assert window.controls_page._control_cards  # non-vacuous

        app_state.set_capabilities(_no_write_caps())
        assert not window.controls_page._cards_writable
        for card in window.controls_page._control_cards.values():
            assert not card.isEnabled()

        app_state.set_capabilities(_write_caps())
        assert window.controls_page._cards_writable
        for card in window.controls_page._control_cards.values():
            assert card.isEnabled()

    def test_rebuild_while_disabled_keeps_cards_disabled(self, qtbot, window, app_state):
        """A grid rebuild (profile switch) must inherit the last-known write state.

        Freshly constructed cards default to enabled; without honouring the
        stored capability a rebuild would silently re-enable a non-writable
        system.
        """
        app_state.set_capabilities(_no_write_caps())
        assert not window.controls_page._cards_writable

        window.controls_page._refresh_controls_grid(_profile_with_control())

        assert window.controls_page._control_cards  # non-vacuous
        for card in window.controls_page._control_cards.values():
            assert not card.isEnabled()
