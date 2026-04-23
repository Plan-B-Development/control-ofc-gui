"""M9: GPU fan reset on GUI close when no profile is active.

Closes the gap flagged in docs/14 §11: when the GUI quits while the daemon
keeps running and no profile is active, the GPU fan otherwise stays at the
last commanded PWM forever. With the fix, the GUI issues a best-effort
``/gpu/{pci_id}/fan/reset`` on close so the fan returns to automatic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    OperationMode,
)
from control_ofc.services.app_state import AppState


def _make_state(
    *,
    gpu_present: bool,
    gui_wrote_gpu: bool,
    active_profile: str,
) -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    state.set_capabilities(
        Capabilities(
            amd_gpu=AmdGpuCapability(
                present=gpu_present,
                model_name="RX 9070 XT",
                display_label="9070XT",
                pci_id="0000:03:00.0" if gpu_present else None,
                fan_write_supported=gpu_present,
            ),
        )
    )
    state.gui_wrote_gpu_fan = gui_wrote_gpu
    if active_profile:
        state.set_active_profile(active_profile)
    return state


def _make_window(state: AppState, client, qtbot, profile_service, settings_service):
    from control_ofc.ui.main_window import MainWindow

    win = MainWindow(
        state=state,
        profile_service=profile_service,
        settings_service=settings_service,
        client=client,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


class TestGpuResetOnClose:
    def test_reset_called_when_gui_wrote_and_no_profile(
        self, qtbot, profile_service, settings_service
    ):
        state = _make_state(gpu_present=True, gui_wrote_gpu=True, active_profile="")
        client = MagicMock()
        win = _make_window(state, client, qtbot, profile_service, settings_service)

        win._maybe_reset_gpu_on_close()

        client.reset_gpu_fan.assert_called_once_with("0000:03:00.0")

    def test_reset_skipped_when_profile_is_active(self, qtbot, profile_service, settings_service):
        """Active profile means the daemon will keep controlling the GPU."""
        state = _make_state(gpu_present=True, gui_wrote_gpu=True, active_profile="Quiet")
        client = MagicMock()
        win = _make_window(state, client, qtbot, profile_service, settings_service)

        win._maybe_reset_gpu_on_close()

        client.reset_gpu_fan.assert_not_called()

    def test_reset_skipped_when_gui_never_wrote_gpu(self, qtbot, profile_service, settings_service):
        """No-op when the GUI never drove the GPU — nothing to undo."""
        state = _make_state(gpu_present=True, gui_wrote_gpu=False, active_profile="")
        client = MagicMock()
        win = _make_window(state, client, qtbot, profile_service, settings_service)

        win._maybe_reset_gpu_on_close()

        client.reset_gpu_fan.assert_not_called()

    def test_reset_skipped_when_gpu_absent(self, qtbot, profile_service, settings_service):
        """If there's no GPU in capabilities, nothing to reset."""
        state = _make_state(gpu_present=False, gui_wrote_gpu=True, active_profile="")
        client = MagicMock()
        win = _make_window(state, client, qtbot, profile_service, settings_service)

        win._maybe_reset_gpu_on_close()

        client.reset_gpu_fan.assert_not_called()

    def test_reset_swallows_daemon_error(self, qtbot, profile_service, settings_service, caplog):
        """Reset failure on close must not propagate — worst case it's logged."""
        state = _make_state(gpu_present=True, gui_wrote_gpu=True, active_profile="")
        client = MagicMock()
        client.reset_gpu_fan.side_effect = DaemonError(code="hardware_unavailable", message="gone")
        win = _make_window(state, client, qtbot, profile_service, settings_service)

        # Must not raise.
        win._maybe_reset_gpu_on_close()

    def test_reset_skipped_in_demo_mode(self, qtbot, profile_service, settings_service):
        """Demo mode has no live daemon — never call reset."""
        state = _make_state(gpu_present=True, gui_wrote_gpu=True, active_profile="")
        from control_ofc.ui.main_window import MainWindow

        win = MainWindow(
            state=state,
            profile_service=profile_service,
            settings_service=settings_service,
            client=None,
            demo_mode=True,
        )
        qtbot.addWidget(win)

        # Demo mode sets its own demo client — force the state we want.
        win._maybe_reset_gpu_on_close()  # Should be a no-op (no client).


class TestControlLoopSetsGpuWrittenFlag:
    """ControlLoopService marks gui_wrote_gpu_fan=True on successful amd_gpu writes."""

    def test_successful_gpu_write_sets_flag(self, qtbot):
        from control_ofc.services.app_state import AppState
        from control_ofc.services.control_loop import ControlLoopService
        from control_ofc.services.profile_service import ProfileService

        state = AppState()
        profile_svc = ProfileService()
        loop = ControlLoopService(state, profile_svc)

        assert state.gui_wrote_gpu_fan is False
        loop._on_write_completed("amd_gpu:0000:03:00.0", True)
        assert state.gui_wrote_gpu_fan is True

    def test_failed_gpu_write_does_not_set_flag(self, qtbot):
        from control_ofc.services.app_state import AppState
        from control_ofc.services.control_loop import ControlLoopService
        from control_ofc.services.profile_service import ProfileService

        state = AppState()
        profile_svc = ProfileService()
        loop = ControlLoopService(state, profile_svc)

        loop._on_write_completed("amd_gpu:0000:03:00.0", False)
        assert state.gui_wrote_gpu_fan is False

    def test_openfan_write_does_not_set_gpu_flag(self, qtbot):
        from control_ofc.services.app_state import AppState
        from control_ofc.services.control_loop import ControlLoopService
        from control_ofc.services.profile_service import ProfileService

        state = AppState()
        profile_svc = ProfileService()
        loop = ControlLoopService(state, profile_svc)

        loop._on_write_completed("openfan:ch00", True)
        assert state.gui_wrote_gpu_fan is False
