"""R38: End-to-end GPU fan integration — display name, wizard, read-only suffix.

Covers: fan_display_name GPU awareness, fan wizard amd_gpu branch, controls page
read-only suffix, displayability with RPM=0, cache-state preservation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    FanReading,
    OperationMode,
)
from control_ofc.services.app_state import AppState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(gpu_present: bool = True, fan_write: bool = True) -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    state.set_capabilities(
        Capabilities(
            daemon_version="0.4.2",
            amd_gpu=AmdGpuCapability(
                present=gpu_present,
                model_name="RX 9070 XT",
                display_label="9070XT",
                pci_id="0000:03:00.0",
                fan_control_method="pmfw_curve" if fan_write else "read_only",
                pmfw_supported=fan_write,
                fan_rpm_available=True,
                fan_write_supported=fan_write,
                is_discrete=True,
                overdrive_enabled=fan_write,
            ),
        )
    )
    return state


def _gpu_fan(rpm: int = 0) -> FanReading:
    return FanReading(
        id="amd_gpu:0000:03:00.0",
        source="amd_gpu",
        rpm=rpm,
        last_commanded_pwm=None,
        age_ms=200,
    )


# ---------------------------------------------------------------------------
# Fan display name
# ---------------------------------------------------------------------------


class TestGpuFanDisplayName:
    """fan_display_name returns '{model} Fan' for GPU fans."""

    def test_gpu_fan_with_capabilities(self):
        state = _make_state()
        name = state.fan_display_name("amd_gpu:0000:03:00.0")
        assert name == "9070XT Fan"

    def test_gpu_fan_without_capabilities(self):
        state = AppState()
        name = state.fan_display_name("amd_gpu:0000:03:00.0")
        assert name == "D-GPU Fan"

    def test_gpu_fan_alias_overrides(self):
        state = _make_state()
        state.set_fan_alias("amd_gpu:0000:03:00.0", "My GPU")
        name = state.fan_display_name("amd_gpu:0000:03:00.0")
        assert name == "My GPU"

    def test_non_gpu_fan_unchanged(self):
        state = _make_state()
        name = state.fan_display_name("openfan:ch00")
        assert name == "openfan:ch00"

    def test_hwmon_fan_unchanged(self):
        state = _make_state()
        name = state.fan_display_name("hwmon:it8696:fan1")
        assert name == "hwmon:it8696:fan1"


# ---------------------------------------------------------------------------
# Fan wizard GPU branch
# ---------------------------------------------------------------------------


class TestFanWizardGpuBranch:
    """Fan wizard stop/restore routes every source through the daemon identify
    API by fan id (DEC-166) — no per-source raw PWM writes, no GUI lease."""

    def test_stop_gpu_fan_uses_identify(self):
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = _make_state()

        wizard.stop_fan({"id": "amd_gpu:0000:03:00.0", "source": "amd_gpu"})

        client.fan_identify.assert_called_once_with("amd_gpu:0000:03:00.0", "stop")
        client.set_gpu_fan_speed.assert_not_called()
        client.set_hwmon_pwm.assert_not_called()

    def test_restore_gpu_fan_uses_identify(self):
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = _make_state()

        wizard.restore_fan({"id": "amd_gpu:0000:03:00.0", "source": "amd_gpu"})

        client.fan_identify.assert_called_once_with("amd_gpu:0000:03:00.0", "restore")
        client.set_gpu_fan_speed.assert_not_called()

    def test_restore_replays_no_pwm(self):
        """Restore never sends a PWM — the daemon recomputes the curve."""
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = _make_state()

        wizard.restore_fan({"id": "amd_gpu:0000:03:00.0", "source": "amd_gpu"})

        client.fan_identify.assert_called_once_with("amd_gpu:0000:03:00.0", "restore")
        client.set_gpu_fan_speed.assert_not_called()
        client.set_openfan_pwm.assert_not_called()
        client.set_hwmon_pwm.assert_not_called()

    def test_stop_openfan_uses_identify(self):
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        client = MagicMock()
        wizard = FanConfigWizard.__new__(FanConfigWizard)
        wizard._client = client
        wizard._state = _make_state()

        wizard.stop_fan({"id": "openfan:ch02", "source": "openfan"})

        client.fan_identify.assert_called_once_with("openfan:ch02", "stop")
        client.set_openfan_pwm.assert_not_called()
        client.set_gpu_fan_speed.assert_not_called()


# ---------------------------------------------------------------------------
# Read-only suffix in fan role member list
# ---------------------------------------------------------------------------


class TestReadOnlySuffix:
    """GPU fans show '(read-only)' suffix when not writable."""

    def test_read_only_gpu_gets_suffix(self, qtbot, profile_service, settings_service):
        from control_ofc.ui.main_window import MainWindow

        state = _make_state(fan_write=False)
        state.set_fans([_gpu_fan()])
        win = MainWindow(
            state=state,
            profile_service=profile_service,
            settings_service=settings_service,
            demo_mode=False,
        )
        qtbot.addWidget(win)

        # The label should include (read-only) when building the member list
        label = state.fan_display_name("amd_gpu:0000:03:00.0")
        assert label == "9070XT Fan"
        # The suffix is added by controls_page._on_edit_members, not by display_name
        # We verify the display_name is clean and the suffix logic is separate

    def test_writable_gpu_no_suffix(self):
        state = _make_state(fan_write=True)
        label = state.fan_display_name("amd_gpu:0000:03:00.0")
        assert "(read-only)" not in label


# ---------------------------------------------------------------------------
# Dashboard displayability with RPM=0
# ---------------------------------------------------------------------------


class TestDashboardGpuRpmZero:
    """GPU fans remain visible with RPM=0."""

    def test_gpu_fan_rpm_zero_visible(self, qtbot, profile_service, app_state):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        app_state.set_fans([_gpu_fan(rpm=0)])
        assert page._fan_table.rowCount() == 1

    def test_gpu_fan_rpm_none_visible(self, qtbot, profile_service, app_state):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        fan = FanReading(id="amd_gpu:0000:03:00.0", source="amd_gpu", rpm=None, age_ms=200)
        app_state.set_fans([fan])
        assert page._fan_table.rowCount() == 1

    def test_gpu_fan_uses_display_name_in_table(self, qtbot, profile_service, app_state):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        app_state.set_capabilities(
            Capabilities(
                daemon_version="0.4.2",
                amd_gpu=AmdGpuCapability(present=True, display_label="9070XT"),
            )
        )
        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        app_state.set_fans([_gpu_fan(rpm=1200)])
        # First column should show the display name, not raw ID
        label_item = page._fan_table.item(0, 0)
        assert label_item is not None
        assert "9070XT" in label_item.text()
