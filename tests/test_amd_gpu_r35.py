"""R35: AMD dedicated GPU support — models, capabilities, display labels, diagnostics.

Covers: AmdGpuCapability parsing, GPU display label in dashboard/diagnostics,
source label "amd_gpu" handling, capabilities with/without GPU, marketing name
resolution.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from onlyfans.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    OperationMode,
)
from onlyfans.services.app_state import AppState
from onlyfans.ui.pages.dashboard_page import DashboardPage
from onlyfans.ui.pages.diagnostics_page import DiagnosticsPage
from onlyfans.ui.widgets.summary_card import SummaryCard

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _make_gpu_caps(
    present: bool = True,
    model_name: str | None = "RX 9070 XT",
    display_label: str = "9070XT",
    pci_id: str = "0000:2d:00.0",
    fan_control_method: str = "pmfw_curve",
    pmfw_supported: bool = True,
    fan_rpm_available: bool = True,
    fan_write_supported: bool = True,
) -> Capabilities:
    return Capabilities(
        daemon_version="0.3.0",
        amd_gpu=AmdGpuCapability(
            present=present,
            model_name=model_name,
            display_label=display_label,
            pci_id=pci_id,
            fan_control_method=fan_control_method,
            pmfw_supported=pmfw_supported,
            fan_rpm_available=fan_rpm_available,
            fan_write_supported=fan_write_supported,
            is_discrete=True,
        ),
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestAmdGpuCapabilityModel:
    """AmdGpuCapability dataclass has correct defaults and fields."""

    def test_default_not_present(self):
        cap = AmdGpuCapability()
        assert not cap.present
        assert cap.display_label == "AMD D-GPU"
        assert cap.fan_control_method == "none"
        assert not cap.pmfw_supported

    def test_full_gpu_capability(self):
        cap = AmdGpuCapability(
            present=True,
            model_name="RX 9070 XT",
            display_label="9070XT",
            pci_id="0000:2d:00.0",
            fan_control_method="pmfw_curve",
            pmfw_supported=True,
            fan_rpm_available=True,
            fan_write_supported=True,
            is_discrete=True,
        )
        assert cap.present
        assert cap.model_name == "RX 9070 XT"
        assert cap.display_label == "9070XT"
        assert cap.pmfw_supported


class TestCapabilitiesParsing:
    """Capabilities parsing handles amd_gpu field."""

    def test_parse_with_amd_gpu(self):
        from onlyfans.api.models import parse_capabilities

        data = {
            "api_version": 1,
            "daemon_version": "0.3.0",
            "ipc_transport": "uds/http",
            "devices": {
                "openfan": {"present": False},
                "hwmon": {"present": True, "pwm_header_count": 2},
                "amd_gpu": {
                    "present": True,
                    "model_name": "RX 9070 XT",
                    "display_label": "9070XT",
                    "pci_id": "0000:2d:00.0",
                    "fan_control_method": "pmfw_curve",
                    "pmfw_supported": True,
                    "fan_rpm_available": True,
                    "fan_write_supported": True,
                    "is_discrete": True,
                },
                "aio_hwmon": {"present": False},
                "aio_usb": {"present": False},
            },
            "features": {},
            "limits": {},
        }
        caps = parse_capabilities(data)
        assert caps.amd_gpu.present
        assert caps.amd_gpu.model_name == "RX 9070 XT"
        assert caps.amd_gpu.display_label == "9070XT"
        assert caps.amd_gpu.pmfw_supported

    def test_parse_without_amd_gpu_field(self):
        """Older daemon versions that don't include amd_gpu."""
        from onlyfans.api.models import parse_capabilities

        data = {
            "api_version": 1,
            "daemon_version": "0.2.0",
            "devices": {
                "openfan": {"present": False},
                "hwmon": {"present": True},
            },
            "features": {},
            "limits": {},
        }
        caps = parse_capabilities(data)
        assert not caps.amd_gpu.present
        assert caps.amd_gpu.display_label == "AMD D-GPU"

    def test_parse_with_unknown_gpu_fields(self):
        """Forward compat: unknown fields in amd_gpu are ignored."""
        from onlyfans.api.models import parse_capabilities

        data = {
            "api_version": 1,
            "daemon_version": "0.3.0",
            "devices": {
                "amd_gpu": {
                    "present": True,
                    "display_label": "9070XT",
                    "future_field": "ignored",
                },
            },
            "features": {},
            "limits": {},
        }
        caps = parse_capabilities(data)
        assert caps.amd_gpu.present
        assert caps.amd_gpu.display_label == "9070XT"


# ---------------------------------------------------------------------------
# Dashboard GPU card tests
# ---------------------------------------------------------------------------


class TestDashboardGpuCard:
    """Dashboard GPU card updates title from capabilities."""

    def test_gpu_card_default_title(self, qtbot, app_state, profile_service):
        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        assert page._gpu_card._title_label.text() == "GPU Temp"

    def test_gpu_card_updates_to_model_name(self, qtbot, app_state, profile_service):
        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        caps = _make_gpu_caps(present=True, display_label="9070XT")
        app_state.set_capabilities(caps)
        assert page._gpu_card._title_label.text() == "9070XT Temp"

    def test_gpu_card_no_gpu_keeps_default(self, qtbot, app_state, profile_service):
        page = DashboardPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        caps = _make_gpu_caps(present=False)
        app_state.set_capabilities(caps)
        # Title should NOT change when GPU is not present
        assert page._gpu_card._title_label.text() == "GPU Temp"


class TestSummaryCardSetTitle:
    """SummaryCard.set_title() updates the title label."""

    def test_set_title(self, qtbot):
        card = SummaryCard("Original", category="test")
        qtbot.addWidget(card)
        card.set_title("New Title")
        assert card._title_label.text() == "New Title"


# ---------------------------------------------------------------------------
# Diagnostics GPU display tests
# ---------------------------------------------------------------------------


class TestDiagnosticsGpuDisplay:
    """Diagnostics Overview shows GPU capabilities."""

    def test_gpu_label_exists(self, qtbot):
        state = _make_state()
        page = DiagnosticsPage(state=state)
        qtbot.addWidget(page)
        label = page.findChild(QLabel, "Diagnostics_Label_amdGpu")
        assert label is not None

    def test_gpu_label_transparent(self, qtbot):
        state = _make_state()
        page = DiagnosticsPage(state=state)
        qtbot.addWidget(page)
        label = page.findChild(QLabel, "Diagnostics_Label_amdGpu")
        assert "transparent" in label.styleSheet().lower()

    def test_gpu_detected_shows_info(self, qtbot):
        state = _make_state()
        page = DiagnosticsPage(state=state)
        qtbot.addWidget(page)
        caps = _make_gpu_caps()
        state.set_capabilities(caps)
        label = page.findChild(QLabel, "Diagnostics_Label_amdGpu")
        text = label.text()
        assert "9070XT" in text
        assert "PCI" in text
        assert "pmfw_curve" in text

    def test_gpu_not_detected_shows_message(self, qtbot):
        state = _make_state()
        page = DiagnosticsPage(state=state)
        qtbot.addWidget(page)
        caps = _make_gpu_caps(present=False)
        state.set_capabilities(caps)
        label = page.findChild(QLabel, "Diagnostics_Label_amdGpu")
        assert "Not detected" in label.text()

    def test_gpu_hwmon_pwm_method_shown(self, qtbot):
        state = _make_state()
        page = DiagnosticsPage(state=state)
        qtbot.addWidget(page)
        caps = _make_gpu_caps(
            display_label="AMD D-GPU",
            fan_control_method="hwmon_pwm",
            pmfw_supported=False,
        )
        state.set_capabilities(caps)
        label = page.findChild(QLabel, "Diagnostics_Label_amdGpu")
        assert "hwmon_pwm" in label.text()


# ---------------------------------------------------------------------------
# Source label tests
# ---------------------------------------------------------------------------


class TestSourceLabelHandling:
    """GUI models correctly handle 'amd_gpu' as a source value."""

    def test_sensor_reading_amd_gpu_source(self):
        from onlyfans.api.models import SensorReading

        reading = SensorReading(
            id="hwmon:amdgpu:0000:2d:00.0:edge",
            kind="gpu_temp",
            label="edge",
            value_c=45.0,
            source="amd_gpu",
            age_ms=100,
        )
        assert reading.source == "amd_gpu"
        assert reading.kind == "gpu_temp"

    def test_fan_reading_amd_gpu_source(self):
        from onlyfans.api.models import FanReading

        reading = FanReading(
            id="hwmon:amdgpu:0000:2d:00.0:fan1",
            source="amd_gpu",
            rpm=1200,
            age_ms=100,
        )
        assert reading.source == "amd_gpu"

    def test_sensor_freshness_works_with_amd_gpu(self):
        from onlyfans.api.models import Freshness, SensorReading

        reading = SensorReading(source="amd_gpu", age_ms=500)
        assert reading.freshness == Freshness.FRESH

        stale = SensorReading(source="amd_gpu", age_ms=5000)
        assert stale.freshness == Freshness.STALE
