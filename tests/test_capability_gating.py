"""Capability gating tests — UI enables/disables based on daemon capabilities."""

from __future__ import annotations

import pytest

from onlyfans.api.models import Capabilities, FeatureFlags
from onlyfans.ui.main_window import MainWindow


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
