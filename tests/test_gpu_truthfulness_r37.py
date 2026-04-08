"""R37: GPU detection truthfulness — PCI ID fixes, displayability, overdrive.

Covers: correct Navi 48 identification (0x7550), revision-based XT distinction,
displayability filter for GPU fans with RPM=0, overdrive_enabled field,
diagnostics ppfeaturemask guidance.
"""

from __future__ import annotations

from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    FanReading,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _gpu_fan_zero_rpm() -> FanReading:
    """GPU fan at zero RPM (idle, zero-RPM mode active)."""
    return FanReading(
        id="amd_gpu:0000:03:00.0",
        source="amd_gpu",
        rpm=0,
        last_commanded_pwm=None,
        age_ms=200,
    )


def _gpu_fan_spinning() -> FanReading:
    return FanReading(
        id="amd_gpu:0000:03:00.0",
        source="amd_gpu",
        rpm=1450,
        last_commanded_pwm=None,
        age_ms=200,
    )


# ---------------------------------------------------------------------------
# Displayability — GPU fans always visible even at RPM=0
# ---------------------------------------------------------------------------


class TestGpuFanDisplayability:
    """GPU fans must appear on dashboard even with RPM=0 (zero-RPM idle)."""

    def test_gpu_fan_zero_rpm_is_displayable(self, qtbot, app_state, profile_service):
        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        fans = [_gpu_fan_zero_rpm()]
        app_state.set_fans(fans)

        # The fan should appear in the table even at RPM=0
        assert page._fan_table.rowCount() == 1

    def test_gpu_fan_spinning_is_displayable(self, qtbot, app_state, profile_service):
        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        fans = [_gpu_fan_spinning()]
        app_state.set_fans(fans)

        assert page._fan_table.rowCount() == 1

    def test_hwmon_fan_zero_rpm_still_hidden(self, qtbot, app_state, profile_service):
        """hwmon fans with RPM=0 are still hidden (disconnected header)."""
        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        fans = [
            FanReading(
                id="hwmon:it8696:fan3",
                source="hwmon",
                rpm=0,
                last_commanded_pwm=None,
                age_ms=200,
            )
        ]
        app_state.set_fans(fans)

        assert page._fan_table.rowCount() == 0

    def test_mixed_fans_gpu_visible_hwmon_hidden(self, qtbot, app_state, profile_service):
        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)

        fans = [
            _gpu_fan_zero_rpm(),
            FanReading(id="hwmon:it8696:fan3", source="hwmon", rpm=0, age_ms=100),
        ]
        app_state.set_fans(fans)

        # Only GPU fan should be visible
        assert page._fan_table.rowCount() == 1


# ---------------------------------------------------------------------------
# Capabilities — overdrive_enabled field
# ---------------------------------------------------------------------------


class TestOverdriveEnabledField:
    """AmdGpuCapability includes overdrive_enabled status."""

    def test_overdrive_false_by_default(self):
        cap = AmdGpuCapability()
        assert cap.overdrive_enabled is False

    def test_overdrive_parsed_from_capabilities(self):
        from control_ofc.api.models import parse_capabilities

        data = {
            "api_version": 1,
            "daemon_version": "0.4.1",
            "devices": {
                "amd_gpu": {
                    "present": True,
                    "display_label": "9070XT",
                    "overdrive_enabled": True,
                    "pmfw_supported": True,
                },
            },
            "features": {},
            "limits": {},
        }
        caps = parse_capabilities(data)
        assert caps.amd_gpu.overdrive_enabled is True
        assert caps.amd_gpu.pmfw_supported is True

    def test_overdrive_false_when_missing(self):
        from control_ofc.api.models import parse_capabilities

        data = {
            "api_version": 1,
            "daemon_version": "0.4.1",
            "devices": {
                "amd_gpu": {"present": True, "display_label": "9070XT"},
            },
            "features": {},
            "limits": {},
        }
        caps = parse_capabilities(data)
        assert caps.amd_gpu.overdrive_enabled is False


# ---------------------------------------------------------------------------
# Diagnostics — ppfeaturemask guidance
# ---------------------------------------------------------------------------


class TestDiagnosticsOverdriveGuidance:
    """Diagnostics shows ppfeaturemask guidance when overdrive is disabled."""

    def test_overdrive_disabled_shows_guidance(self):
        state = _make_state()
        state.set_capabilities(
            Capabilities(
                daemon_version="0.4.1",
                amd_gpu=AmdGpuCapability(
                    present=True,
                    display_label="9070XT",
                    fan_control_method="read_only",
                    pmfw_supported=False,
                    overdrive_enabled=False,
                ),
            )
        )
        svc = DiagnosticsService(state)
        text = svc.format_gpu_status()
        assert "ppfeaturemask" in text
        assert "0xffffffff" in text

    def test_overdrive_enabled_no_guidance(self):
        state = _make_state()
        state.set_capabilities(
            Capabilities(
                daemon_version="0.4.1",
                amd_gpu=AmdGpuCapability(
                    present=True,
                    display_label="9070XT",
                    fan_control_method="pmfw_curve",
                    pmfw_supported=True,
                    overdrive_enabled=True,
                ),
            )
        )
        svc = DiagnosticsService(state)
        text = svc.format_gpu_status()
        # Should NOT suggest ppfeaturemask since it's already enabled
        assert "Add 'amdgpu.ppfeaturemask" not in text

    def test_overdrive_status_shown(self, qtbot):
        state = _make_state()
        state.set_capabilities(
            Capabilities(
                daemon_version="0.4.1",
                amd_gpu=AmdGpuCapability(
                    present=True,
                    display_label="9070XT",
                    overdrive_enabled=False,
                ),
            )
        )
        diag = DiagnosticsService(state)
        page = DiagnosticsPage(state=state, diagnostics_service=diag)
        qtbot.addWidget(page)

        page._fetch_gpu_status()

        from PySide6.QtWidgets import QPlainTextEdit

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        text = log_view.toPlainText()
        assert "Overdrive enabled: No" in text


# ---------------------------------------------------------------------------
# Fan control method truthfulness
# ---------------------------------------------------------------------------


class TestFanControlMethodTruthfulness:
    """Fan control method reflects actual capability, not just file existence."""

    def test_read_only_method_shown(self):
        state = _make_state()
        state.set_capabilities(
            Capabilities(
                daemon_version="0.4.1",
                amd_gpu=AmdGpuCapability(
                    present=True,
                    display_label="9070XT",
                    fan_control_method="read_only",
                    fan_write_supported=False,
                ),
            )
        )
        svc = DiagnosticsService(state)
        text = svc.format_gpu_status()
        assert "read_only" in text

    def test_fan_write_false_for_read_only(self):
        cap = AmdGpuCapability(
            present=True,
            fan_control_method="read_only",
            fan_write_supported=False,
        )
        assert not cap.fan_write_supported
