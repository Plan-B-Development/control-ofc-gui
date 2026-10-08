"""R35: AMD dedicated GPU support — models, capabilities, display labels, diagnostics.

Covers: AmdGpuCapability parsing, GPU display label in dashboard/diagnostics,
source label "amd_gpu" handling, capabilities with/without GPU, marketing name
resolution.
"""

from __future__ import annotations

from control_ofc.api.models import (
    DEFAULT_DAEMON_POLL_INTERVAL_MS,
    AmdGpuCapability,
    Capabilities,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        from control_ofc.api.models import parse_capabilities

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
        from control_ofc.api.models import parse_capabilities

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
        from control_ofc.api.models import parse_capabilities

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

    def test_every_amd_gpu_is_parsed_and_found_by_its_own_card(self):
        """`GPU-b`: ``devices.amd_gpus`` describes each card, so a lookup by a
        secondary card's fan id answers with that card, not the primary."""
        from control_ofc.api.models import parse_capabilities

        primary = {
            "present": True,
            "display_label": "6900XT",
            "pci_id": "0000:03:00.0",
            "pci_bdf": "0000:03:00.0",
            "fan_control_method": "hwmon_pwm",
            "fan_write_supported": False,
        }
        caps = parse_capabilities(
            {
                "devices": {
                    "amd_gpu": {**primary, "kernel_warnings": [{"id": "k", "severity": "high"}]},
                    "amd_gpus": [
                        primary,
                        # canonical name only: coalesced like `amd_gpu`
                        {
                            "present": True,
                            "display_label": "9070XT",
                            "pci_bdf": "0000:2d:00.0",
                            "fan_control_method": "pmfw_curve",
                            "fan_write_supported": True,
                            "kernel_warnings": [{"id": "k"}],
                        },
                        "not-an-object",
                    ],
                },
            }
        )
        assert caps.amd_gpus is not None and len(caps.amd_gpus) == 2
        second = caps.amd_gpu_for_fan("amd_gpu:0000:2d:00.0")
        assert second is not None
        assert second.display_label == "9070XT"
        assert second.profile_writable
        assert second.kernel_warnings == [], "advisories live on amd_gpu"
        first = caps.amd_gpu_for_fan("amd_gpu:0000:03:00.0")
        assert first is not None and not first.profile_writable
        assert caps.amd_gpu_for_fan("amd_gpu:0000:99:00.0") is None
        assert [w.id for w in caps.amd_gpu.kernel_warnings] == ["k"]

    def test_an_older_daemon_describes_only_the_primary_card(self):
        """Without ``devices.amd_gpus`` only the primary card is known; a second
        card's fan is not judged by the primary's answer."""
        from control_ofc.api.models import parse_capabilities

        caps = parse_capabilities(
            {
                "devices": {
                    "amd_gpu": {
                        "present": True,
                        "pci_id": "0000:03:00.0",
                        "fan_control_method": "hwmon_pwm",
                    },
                },
            }
        )
        assert caps.amd_gpus is None
        assert caps.amd_gpu_for_fan("amd_gpu:0000:03:00.0") is caps.amd_gpu
        assert caps.amd_gpu_for_fan("amd_gpu:0000:2d:00.0") is None


# ---------------------------------------------------------------------------
# Dashboard GPU card tests
# ---------------------------------------------------------------------------


class TestSourceLabelHandling:
    """GUI models correctly handle 'amd_gpu' as a source value."""

    def test_sensor_reading_amd_gpu_source(self):
        from control_ofc.api.models import SensorReading

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
        from control_ofc.api.models import FanReading

        reading = FanReading(
            id="hwmon:amdgpu:0000:2d:00.0:fan1",
            source="amd_gpu",
            rpm=1200,
            age_ms=100,
        )
        assert reading.source == "amd_gpu"

    def test_sensor_freshness_works_with_amd_gpu(self):
        from control_ofc.api.models import Freshness, SensorReading

        reading = SensorReading(source="amd_gpu", age_ms=500)
        assert reading.freshness_at(DEFAULT_DAEMON_POLL_INTERVAL_MS) == Freshness.FRESH

        stale = SensorReading(source="amd_gpu", age_ms=5000)
        assert stale.freshness_at(DEFAULT_DAEMON_POLL_INTERVAL_MS) == Freshness.STALE
