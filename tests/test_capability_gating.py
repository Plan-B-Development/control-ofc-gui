"""Capability gating tests — UI enables/disables based on daemon capabilities."""

from __future__ import annotations

import pytest

from control_ofc.api.models import Capabilities, ControlCapability, FeatureFlags
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
    # Autonomous but no writable backend: isolates the write-support dimension of
    # the card gate (cards disabled because of no write support, not non-autonomy).
    return Capabilities(
        daemon_version="2.0.0",
        features=FeatureFlags(openfan_write_supported=False, hwmon_write_supported=False),
        # A real 2.0.0+ daemon sets all three together and unconditionally
        # (daemon `api/handlers/status.rs`); the Controls page gates the
        # Manual toggle on `manual_override` rather than inferring it.
        control=ControlCapability(autonomous_control=True, manual_override=True, fan_identify=True),
    )


def _write_caps() -> Capabilities:
    # A modern autonomous daemon with a writable backend → cards enabled.
    return Capabilities(
        daemon_version="2.0.0",
        features=FeatureFlags(openfan_write_supported=True),
        # A real 2.0.0+ daemon sets all three together and unconditionally
        # (daemon `api/handlers/status.rs`); the Controls page gates the
        # Manual toggle on `manual_override` rather than inferring it.
        control=ControlCapability(autonomous_control=True, manual_override=True, fan_identify=True),
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
        """Autonomous daemon but no writable backend -> control cards disabled."""
        # DEC-214: the active profile ("quiet") already provides a control card at
        # construction — the page no longer has its own profile combo to select.
        assert window.controls_page._control_cards  # non-vacuous

        app_state.set_capabilities(_no_write_caps())

        for card in window.controls_page._control_cards.values():
            assert not card.isEnabled()

    def test_controls_enabled_with_write_support(self, qtbot, window, app_state):
        """Autonomous daemon with write support -> control cards stay enabled."""
        assert window.controls_page._control_cards  # non-vacuous (active profile)

        app_state.set_capabilities(_write_caps())

        # Cards should remain enabled (default state)
        for card in window.controls_page._control_cards.values():
            assert card.isEnabled()

    def test_controls_disabled_when_daemon_not_autonomous(self, qtbot, window, app_state):
        """Write support but no autonomous_control (pre-2.0 daemon) -> cards disabled.

        Override is a 2.0.0 daemon feature (DEC-163); against a non-autonomous
        daemon the GUI has stood down (the main-window upgrade banner), so the
        Manual override toggles must be non-interactive even though a writable
        backend is advertised. Isolates the autonomy dimension from write support.
        """
        assert window.controls_page._control_cards  # non-vacuous (active profile)

        caps = Capabilities(
            daemon_version="1.21.0",
            features=FeatureFlags(openfan_write_supported=True),
            control=ControlCapability(autonomous_control=False),
        )
        app_state.set_capabilities(caps)

        assert not window.controls_page._cards_writable
        for card in window.controls_page._control_cards.values():
            assert not card.isEnabled()

    def test_controls_reenable_after_write_returns(self, qtbot, window, app_state):
        """Cards disabled by a no-write snapshot must re-enable when write support returns.

        Regression: ``_on_capabilities_updated`` previously only ever disabled
        cards, so a transient/incomplete capabilities snapshot stranded them
        disabled for the rest of the session.
        """
        assert window.controls_page._control_cards  # non-vacuous (active profile)

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


def _autonomous_caps(version: str = "2.0.0") -> Capabilities:
    return Capabilities(
        daemon_version=version,
        control=ControlCapability(autonomous_control=True, min_supported_gui="2.0.0"),
    )


def _legacy_caps(version: str = "1.21.0") -> Capabilities:
    return Capabilities(
        daemon_version=version,
        control=ControlCapability(autonomous_control=False, min_supported_gui="2.0.0"),
    )


class TestControlCapabilityGate:
    """DEC-165: the thin GUI must refuse to control a pre-2.0 daemon (one that
    does not advertise ``control.autonomous_control``)."""

    def test_blocked_when_daemon_not_autonomous(self, qtbot, window, app_state):
        app_state.set_capabilities(_legacy_caps("1.21.0"))

        assert window._control_blocked is True
        assert not window._gate_banner.isHidden()
        text = window._gate_banner.text()
        assert "2.0.0" in text and "1.21.0" in text  # needs >= / found

    def test_not_blocked_when_daemon_autonomous(self, qtbot, window, app_state):
        app_state.set_capabilities(_autonomous_caps("2.0.0"))

        assert window._control_blocked is False
        assert window._gate_banner.isHidden()

    def test_daemon_upgrade_clears_the_block(self, qtbot, window, app_state):
        app_state.set_capabilities(_legacy_caps("1.21.0"))
        assert window._control_blocked

        app_state.set_capabilities(_autonomous_caps("2.0.0"))

        assert window._control_blocked is False
        assert window._gate_banner.isHidden()

    def test_demo_mode_is_exempt(self, qtbot, app_state, profile_service, settings_service):
        win = MainWindow(
            state=app_state,
            profile_service=profile_service,
            settings_service=settings_service,
            demo_mode=True,
        )
        qtbot.addWidget(win)

        # Even a legacy-caps signal must not engage the gate in demo mode.
        win._on_control_capability_gate(_legacy_caps("1.21.0"))

        assert win._control_blocked is False
        assert win._gate_banner.isHidden()


class TestGuiVersionFloor:
    """DEC-257: `min_supported_gui` is the floor the DAEMON places on the GUI.

    It was advertised in `/capabilities` and never compared against anything —
    and the single place it was read used it backwards, rendering it as "this GUI
    needs control-ofc-daemon >= X". That read correctly only because both numbers
    happened to be 2.0.0, so the direction error was invisible.
    """

    def test_the_comparison_runs_in_the_right_direction(self):
        from control_ofc.services.system_state_view import gui_meets_daemon_floor

        # A new GUI against a daemon with an old floor is fine...
        assert gui_meets_daemon_floor("2.41.0", "2.0.0")
        # ...and an old GUI against a daemon demanding a newer one is not.
        assert not gui_meets_daemon_floor("2.0.0", "2.41.0")
        # Equal satisfies.
        assert gui_meets_daemon_floor("2.41.0", "2.41.0")

    def test_a_source_checkout_is_not_reported_as_too_old(self):
        """Release review, 2026-08-10.

        `constants.APP_VERSION` is the literal "dev" whenever
        `importlib.metadata.version()` raises PackageNotFoundError — every run
        from a source checkout that was not pip installed, which is the
        documented PYTHONPATH=src workflow. `version_tuple("dev")` is (0, 0, 0),
        so the one-sided check raised a permanent, non-dismissible "your GUI is
        too old" banner against a perfectly current daemon.
        """
        from control_ofc.services.system_state_view import gui_meets_daemon_floor

        assert gui_meets_daemon_floor("dev", "2.0.0")
        assert gui_meets_daemon_floor("", "2.0.0")
        assert gui_meets_daemon_floor("unknown", "99.0.0")

    def test_a_real_version_is_still_compared(self):
        """The escape hatch must not swallow genuine violations: only a version
        with no leading number at all is exempt."""
        from control_ofc.services.system_state_view import gui_meets_daemon_floor

        assert not gui_meets_daemon_floor("1.9.0", "2.0.0")
        assert not gui_meets_daemon_floor("0.1.0", "2.0.0")
        # A real number with a suffix still parses, so it is still compared.
        assert not gui_meets_daemon_floor("1.9.0-rc1", "2.0.0")

    def test_an_absent_floor_is_satisfied_not_zero(self):
        """Older daemons omit the field; that is not a violation."""
        from control_ofc.services.system_state_view import gui_meets_daemon_floor

        assert gui_meets_daemon_floor("2.41.0", "")
        assert gui_meets_daemon_floor("2.41.0", "   ")

    def test_prerelease_and_short_versions_are_tolerated(self):
        from control_ofc.services.system_state_view import gui_meets_daemon_floor

        assert gui_meets_daemon_floor("2.41.0-rc1", "2.41")
        assert not gui_meets_daemon_floor("2.40", "2.41.0")

    def test_the_control_gate_message_does_not_quote_the_gui_floor(self, qtbot, window, app_state):
        """Asserted on the rendered text, not the source.

        The gate is about the daemon lacking `autonomous_control` (daemon 2.0.0).
        Quoting the GUI-version floor there was the direction error itself — and
        with a distinctive floor value the mistake becomes visible, where 2.0.0
        vs 2.0.0 hid it for the field's whole life.
        """
        app_state.set_capabilities(
            Capabilities(
                daemon_version="1.9.0",
                control=ControlCapability(autonomous_control=False, min_supported_gui="9.9.9"),
            )
        )
        text = window._gate_banner.text()
        assert not window._gate_banner.isHidden(), "the gate must have fired"
        assert "9.9.9" not in text, (
            "the control gate quoted min_supported_gui, which is the floor the "
            "daemon places on the GUI — the opposite direction (DEC-257)"
        )
        assert "2.0.0" in text, "it must name the daemon version it actually needs"

    def test_an_outdated_gui_gets_a_non_blocking_warning(self, qtbot, window, app_state):
        """The enforcement `min_supported_gui` never had.

        Non-blocking on purpose: the daemon is the sole PWM writer and is driving
        fans correctly whatever the GUI's age, so a hard gate would strand the
        user in exchange for nothing.
        """
        app_state.set_capabilities(
            Capabilities(
                daemon_version="99.0.0",
                control=ControlCapability(autonomous_control=True, min_supported_gui="99.0.0"),
            )
        )
        # isHidden() rather than isVisible(): the test window is never shown, and
        # isVisible() is False for every child of an unshown window.
        assert not window._gui_floor_banner.isHidden(), (
            "a GUI below the daemon's declared floor must be told"
        )
        assert "99.0.0" in window._gui_floor_banner.text()
        assert not window._control_blocked, "the warning must not block control"

    def test_a_current_gui_sees_no_floor_warning(self, qtbot, window, app_state):
        app_state.set_capabilities(
            Capabilities(
                daemon_version="2.17.0",
                control=ControlCapability(autonomous_control=True, min_supported_gui="1.0.0"),
            )
        )
        assert window._gui_floor_banner.isHidden()


class TestOverrideAndIdentifyCapabilityGates:
    """P3: gate Manual and the identify wizard on the flags that describe them.

    Both `control.manual_override` (DEC-163) and `control.fan_identify`
    (DEC-166) have been parsed since DEC-159/160 and never read. The Manual
    toggle rode on `autonomous_control` alone and worked purely by
    co-occurrence — the daemon sets all three together and unconditionally
    (`api/handlers/status.rs`) — so a daemon that dropped either surface would
    have offered a control that could only 404.
    """

    def _caps(self, **control_kwargs):
        from control_ofc.api.models import Capabilities, ControlCapability, FeatureFlags

        base = dict(autonomous_control=True, manual_override=True, fan_identify=True)
        base.update(control_kwargs)
        return Capabilities(
            features=FeatureFlags(openfan_write_supported=True, hwmon_write_supported=True),
            control=ControlCapability(**base),
        )

    def test_cards_stay_writable_when_the_daemon_advertises_override(
        self, qtbot, app_state, profile_service
    ):
        """Precondition for the negative below: the realistic payload works."""
        from unittest.mock import MagicMock

        from control_ofc.ui.pages.controls_page import ControlsPage

        page = ControlsPage(app_state, profile_service, client=MagicMock())
        qtbot.addWidget(page)
        page._on_capabilities_updated(self._caps())
        assert page._cards_writable

    def test_no_manual_override_capability_disables_the_cards(
        self, qtbot, app_state, profile_service
    ):
        from unittest.mock import MagicMock

        from control_ofc.ui.pages.controls_page import ControlsPage

        page = ControlsPage(app_state, profile_service, client=MagicMock())
        qtbot.addWidget(page)
        page._on_capabilities_updated(self._caps(manual_override=False))
        assert not page._cards_writable, (
            "without /control/{id}/override the Manual toggle can only 404, so it "
            "must not be offered"
        )

    def test_no_fan_identify_capability_hides_the_wizard(self, qtbot, app_state, profile_service):
        from unittest.mock import MagicMock

        from control_ofc.ui.pages.controls_page import ControlsPage

        page = ControlsPage(app_state, profile_service, client=MagicMock())
        qtbot.addWidget(page)
        page._on_capabilities_updated(self._caps())
        assert page._wizard_action.isVisible(), "precondition: shown when advertised"

        page._on_capabilities_updated(self._caps(fan_identify=False))
        assert not page._wizard_action.isVisible()

    def test_demo_mode_still_advertises_both(self):
        """Demo must mirror the daemon, or demo loses Manual and the wizard."""
        from control_ofc.services.demo_service import DemoService

        caps = DemoService().capabilities()
        assert caps.control.manual_override
        assert caps.control.fan_identify
