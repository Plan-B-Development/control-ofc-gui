"""R36: AMD dGPU fan parity — control loop, dashboard, diagnostics, source routing.

Covers: control loop GPU write routing, sensor panel GPU fan group, diagnostics
GPU fan entries, GPU Status event log button, fan role source compatibility.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtWidgets import QPushButton

from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    FanReading,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _gpu_fan() -> FanReading:
    return FanReading(
        id="amd_gpu:0000:2d:00.0",
        source="amd_gpu",
        rpm=1450,
        last_commanded_pwm=65,
        age_ms=200,
    )


def _gpu_caps(present: bool = True) -> Capabilities:
    return Capabilities(
        daemon_version="0.3.0",
        amd_gpu=AmdGpuCapability(
            present=present,
            model_name="RX 9070 XT",
            display_label="9070XT",
            pci_id="0000:2d:00.0",
            fan_control_method="pmfw_curve",
            pmfw_supported=True,
            fan_rpm_available=True,
            fan_write_supported=True,
            is_discrete=True,
        ),
    )


# ---------------------------------------------------------------------------
# Control loop GPU write routing
# ---------------------------------------------------------------------------


class TestControlLoopGpuRouting:
    """Control loop routes amd_gpu fan writes through set_gpu_fan_speed."""

    def test_gpu_target_calls_set_gpu_fan_speed(self):
        from control_ofc.services.control_loop import ControlLoopService

        state = _make_state()
        client = MagicMock()
        profile_svc = MagicMock()
        svc = ControlLoopService(state=state, profile_service=profile_svc, client=client)

        result = svc._write_target("amd_gpu:0000:2d:00.0", 75.0)

        client.set_gpu_fan_speed.assert_called_once_with("0000:2d:00.0", 75)
        assert result is True

    def test_gpu_target_strips_prefix(self):
        from control_ofc.services.control_loop import ControlLoopService

        state = _make_state()
        client = MagicMock()
        profile_svc = MagicMock()
        svc = ControlLoopService(state=state, profile_service=profile_svc, client=client)

        svc._write_target("amd_gpu:0000:05:00.0", 50.0)

        client.set_gpu_fan_speed.assert_called_once_with("0000:05:00.0", 50)

    def test_openfan_still_works(self):
        from control_ofc.services.control_loop import ControlLoopService

        state = _make_state()
        client = MagicMock()
        profile_svc = MagicMock()
        svc = ControlLoopService(state=state, profile_service=profile_svc, client=client)

        result = svc._write_target("openfan:ch00", 80.0)

        client.set_openfan_pwm.assert_called_once_with(0, 80)
        assert result is True


# ---------------------------------------------------------------------------
# Sensor panel GPU fan group
# ---------------------------------------------------------------------------


class TestSensorPanelGpuFanGroup:
    """Sensor series panel groups GPU fans under 'Fans — D-GPU'."""

    def test_gpu_fan_group_label_exists(self):
        from control_ofc.ui.widgets.sensor_series_panel import _GROUP_LABELS

        assert "fans_gpu" in _GROUP_LABELS
        assert "D-GPU" in _GROUP_LABELS["fans_gpu"]

    def test_gpu_fan_group_in_order(self):
        from control_ofc.ui.widgets.sensor_series_panel import _GROUP_ORDER

        assert "fans_gpu" in _GROUP_ORDER
        # GPU fans should appear before hwmon fans
        gpu_idx = _GROUP_ORDER.index("fans_gpu")
        hwmon_idx = _GROUP_ORDER.index("fans_hwmon")
        assert gpu_idx < hwmon_idx


# ---------------------------------------------------------------------------
# Diagnostics GPU integration
# ---------------------------------------------------------------------------


class TestDiagnosticsGpuFanTable:
    """Diagnostics fan table shows GPU fan entries."""

    def test_gpu_fan_in_fan_table(self, qtbot):
        state = _make_state()
        page = DiagnosticsPage(state=state)
        qtbot.addWidget(page)

        state.set_fans([_gpu_fan()])

        # Check the fan table has an entry
        table = page._fan_table
        assert table.rowCount() == 1
        assert table.item(0, 1).text() == "amd_gpu"


class TestDiagnosticsGpuStatusButton:
    """GPU Status button exists and shows GPU info."""

    def test_gpu_status_button_exists(self, qtbot):
        state = _make_state()
        page = DiagnosticsPage(state=state)
        qtbot.addWidget(page)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_gpuStatus")
        assert btn is not None
        assert btn.isEnabled()
        assert "gpu" in btn.text().lower()

    def test_gpu_status_appends_to_log(self, qtbot):
        state = _make_state()
        state.set_capabilities(_gpu_caps())
        diag = DiagnosticsService(state)
        page = DiagnosticsPage(state=state, diagnostics_service=diag)
        qtbot.addWidget(page)

        page._fetch_gpu_status()

        from PySide6.QtWidgets import QPlainTextEdit

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        text = log_view.toPlainText()
        assert "GPU STATUS" in text
        assert "9070XT" in text or "RX 9070 XT" in text

    def test_gpu_status_no_gpu_detected(self, qtbot):
        state = _make_state()
        state.set_capabilities(_gpu_caps(present=False))
        diag = DiagnosticsService(state)
        page = DiagnosticsPage(state=state, diagnostics_service=diag)
        qtbot.addWidget(page)

        page._fetch_gpu_status()

        from PySide6.QtWidgets import QPlainTextEdit

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert "No AMD" in log_view.toPlainText()


# ---------------------------------------------------------------------------
# Diagnostics service GPU formatting
# ---------------------------------------------------------------------------


class TestDiagnosticsServiceGpuFormat:
    """format_gpu_status produces truthful, labeled output."""

    def test_gpu_present_shows_details(self):
        state = _make_state()
        state.set_capabilities(_gpu_caps())
        svc = DiagnosticsService(state)
        text = svc.format_gpu_status()
        assert "Detected: Yes" in text
        assert "RX 9070 XT" in text
        assert "pmfw_curve" in text
        assert "PCI ID: 0000:2d:00.0" in text

    def test_gpu_absent_shows_message(self):
        state = _make_state()
        state.set_capabilities(_gpu_caps(present=False))
        svc = DiagnosticsService(state)
        text = svc.format_gpu_status()
        assert "Detected: No" in text

    def test_gpu_fan_state_shown(self):
        state = _make_state()
        state.set_capabilities(_gpu_caps())
        state.set_fans([_gpu_fan()])
        svc = DiagnosticsService(state)
        text = svc.format_gpu_status()
        assert "1450 RPM" in text
        assert "65%" in text

    def test_no_state_returns_message(self):
        svc = DiagnosticsService(state=None)
        text = svc.format_gpu_status()
        assert "no application state" in text.lower()

    def test_includes_source_attribution(self):
        state = _make_state()
        state.set_capabilities(_gpu_caps())
        svc = DiagnosticsService(state)
        text = svc.format_gpu_status()
        assert "source:" in text.lower()


# ---------------------------------------------------------------------------
# Fan source compatibility
# ---------------------------------------------------------------------------


class TestFanSourceCompatibility:
    """GPU fan entries work with existing fan infrastructure."""

    def test_gpu_fan_reading_freshness(self):
        from control_ofc.api.models import Freshness

        fan = _gpu_fan()
        assert fan.freshness == Freshness.FRESH

    def test_gpu_fan_stale(self):
        from control_ofc.api.models import Freshness

        fan = FanReading(
            id="amd_gpu:0000:2d:00.0",
            source="amd_gpu",
            rpm=0,
            age_ms=5000,
        )
        assert fan.freshness == Freshness.STALE

    def test_gpu_fan_id_format(self):
        fan = _gpu_fan()
        assert fan.id.startswith("amd_gpu:")
        # PCI BDF follows the prefix
        bdf = fan.id.removeprefix("amd_gpu:")
        parts = bdf.split(":")
        assert len(parts) == 3  # domain:bus:dev.func

    def test_gpu_fan_source_is_amd_gpu(self):
        fan = _gpu_fan()
        assert fan.source == "amd_gpu"
