"""DEC-204: NVIDIA discrete GPU support — models, parsing, display, and
read-only safety.

Covers: NvidiaGpuCapability parsing (present/absent/forward-compat/pci coalesce +
driver_version), NvidiaGpuDiagnosticsInfo parsing, fan displayability + dedup,
fan display name, sensor classification (nouveau/nvml), diagnostics
control-method, the new measured ``duty_pct`` field (parse + tile display),
member-role classification, demo mode, the fan wizard exclusion, the dashboard
GPU card title, and the read-only guarantee (NVIDIA GPU fans are never written).

The daemon's ``driver`` field is the kernel module name (``"nouveau"``/
``"nvidia"``) — not the ``nvml`` userspace library (DEC-204, contract Finding 2).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import (
    Capabilities,
    FanReading,
    NvidiaGpuCapability,
    parse_capabilities,
    parse_fans,
    parse_hardware_diagnostics,
)
from control_ofc.knowledge.sensor_knowledge import classify_sensor
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_service import DemoService
from control_ofc.services.fan_cards_view import build_fan_card_vms
from control_ofc.services.overview_view import fan_control_method
from control_ofc.services.profile_service import CONTROL_ROLE_GPU, ControlMember, infer_member_role
from control_ofc.ui.fan_display import filter_displayable_fans
from control_ofc.ui.widgets.fan_control_card import FanControlCard


def _fan_control_method(fan: FanReading, state: AppState | None) -> str:
    """Local shim (Diagnostics-page retirement): the pure fn now lives in
    ``services.overview_view.fan_control_method``; this ``(fan, state)`` wrapper
    keeps the existing call sites unchanged."""
    return fan_control_method(
        fan,
        state.hwmon_headers if state else [],
        state.capabilities if state else None,
    )


def _make_nvidia_caps(
    *,
    present: bool = True,
    display_label: str = "NVIDIA GeForce RTX 4080",
    fan_control_method: str = "read_only",
) -> Capabilities:
    return Capabilities(
        daemon_version="2.8.0",
        nvidia_gpu=NvidiaGpuCapability(
            present=present,
            model_name="NVIDIA GeForce RTX 4080" if present else None,
            display_label=display_label if present else "NVIDIA D-GPU",
            pci_id="0000:01:00.0" if present else None,
            driver="nvidia" if present else None,
            driver_version="565.77" if present else None,
            fan_control_method=fan_control_method,
            fan_rpm_available=present,
            is_discrete=present,
        ),
    )


# ---------------------------------------------------------------------------
# Capability model + parsing
# ---------------------------------------------------------------------------


class TestNvidiaGpuCapabilityModel:
    def test_default_not_present(self):
        cap = NvidiaGpuCapability()
        assert not cap.present
        assert cap.display_label == "NVIDIA D-GPU"
        assert cap.fan_control_method == "none"
        # Read-only contract: there is no fan_write_supported field at all.
        assert not hasattr(cap, "fan_write_supported")

    def test_full_capability(self):
        cap = NvidiaGpuCapability(
            present=True,
            model_name="NVIDIA GeForce RTX 4080",
            display_label="NVIDIA GeForce RTX 4080",
            pci_id="0000:01:00.0",
            driver="nvidia",
            driver_version="565.77",
            fan_control_method="read_only",
            fan_rpm_available=True,
            is_discrete=True,
        )
        assert cap.present
        # Kernel module name, not the "nvml" library.
        assert cap.driver == "nvidia"
        assert cap.driver_version == "565.77"
        assert cap.fan_control_method == "read_only"


class TestCapabilitiesParsing:
    def _payload(self, nvidia: dict | None) -> dict:
        devices: dict = {"openfan": {"present": False}, "hwmon": {"present": True}}
        if nvidia is not None:
            devices["nvidia_gpu"] = nvidia
        return {"api_version": 1, "daemon_version": "2.8.0", "devices": devices}

    def test_parse_with_nvidia_gpu(self):
        caps = parse_capabilities(
            self._payload(
                {
                    "present": True,
                    "model_name": "NVIDIA GeForce RTX 4080",
                    "display_label": "NVIDIA GeForce RTX 4080",
                    "pci_bdf": "0000:01:00.0",
                    "driver": "nvidia",
                    "driver_version": "565.77",
                    "fan_control_method": "read_only",
                    "fan_rpm_available": True,
                    "is_discrete": True,
                }
            )
        )
        assert caps.nvidia_gpu.present
        assert caps.nvidia_gpu.display_label == "NVIDIA GeForce RTX 4080"
        assert caps.nvidia_gpu.driver == "nvidia"
        assert caps.nvidia_gpu.driver_version == "565.77"
        # pci_bdf on the wire is coalesced into pci_id (like amd_gpu/intel_gpu).
        assert caps.nvidia_gpu.pci_id == "0000:01:00.0"

    def test_parse_nouveau_backed(self):
        """The open nouveau leg: generic label, driver 'nouveau', no NVML fields."""
        caps = parse_capabilities(
            self._payload(
                {
                    "present": True,
                    "display_label": "NVIDIA D-GPU",
                    "pci_bdf": "0000:01:00.0",
                    "driver": "nouveau",
                    "fan_control_method": "read_only",
                    "fan_rpm_available": True,
                    "is_discrete": True,
                }
            )
        )
        assert caps.nvidia_gpu.present
        assert caps.nvidia_gpu.driver == "nouveau"
        assert caps.nvidia_gpu.model_name is None
        assert caps.nvidia_gpu.driver_version is None
        assert caps.nvidia_gpu.display_label == "NVIDIA D-GPU"

    def test_parse_without_nvidia_gpu_field(self):
        """Older daemons omit nvidia_gpu entirely → present defaults False."""
        caps = parse_capabilities(self._payload(None))
        assert not caps.nvidia_gpu.present
        assert caps.nvidia_gpu.display_label == "NVIDIA D-GPU"

    def test_parse_with_unknown_nvidia_fields(self):
        """Forward compat: unknown fields are dropped, not fatal."""
        caps = parse_capabilities(
            self._payload({"present": True, "display_label": "RTX 4080", "future_field": "ignored"})
        )
        assert caps.nvidia_gpu.present
        assert caps.nvidia_gpu.display_label == "RTX 4080"


class TestDiagnosticsParsing:
    def test_parse_nvidia_gpu_diagnostics(self):
        data = {
            "api_version": 1,
            "hwmon": {},
            "nvidia_gpu": {
                "pci_bdf": "0000:01:00.0",
                "pci_id": "0000:01:00.0",  # daemon emits both (alias); GUI keeps pci_bdf
                "model_name": "NVIDIA GeForce RTX 4080",
                "driver": "nvidia",
                "driver_version": "565.77",
                "fan_control_method": "read_only",
                "fan_rpm_available": True,
                "fan_control_note": "read-only telemetry",
            },
        }
        result = parse_hardware_diagnostics(data)
        assert result.nvidia_gpu is not None
        assert result.nvidia_gpu.driver == "nvidia"
        assert result.nvidia_gpu.driver_version == "565.77"
        assert result.nvidia_gpu.fan_control_method == "read_only"
        assert "read-only" in result.nvidia_gpu.fan_control_note

    def test_parse_without_nvidia_gpu_diagnostics(self):
        result = parse_hardware_diagnostics({"api_version": 1, "hwmon": {}})
        assert result.nvidia_gpu is None


# ---------------------------------------------------------------------------
# Display: always-show, dedup, name
# ---------------------------------------------------------------------------


class TestFanDisplay:
    def test_nvidia_gpu_fan_always_displayable_at_zero_rpm(self):
        # The DEC-047 fix: an idle (0 RPM, no commanded PWM) NVIDIA fan must not
        # be hidden by the auto-hide filter.
        fans = [FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=0)]
        out = filter_displayable_fans(fans, aliases={}, hide_unused=True)
        assert len(out) == 1

    def test_nvidia_gpu_fan_dedups_hwmon_shadow(self):
        fans = [
            FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1500),
            FanReading(id="hwmon:nouveau:0000:01:00.0:fan1", source="hwmon", rpm=1500),
        ]
        out = filter_displayable_fans(fans, aliases={}, hide_unused=False)
        ids = {f.id for f in out}
        assert "nvidia_gpu:0000:01:00.0" in ids
        assert "hwmon:nouveau:0000:01:00.0:fan1" not in ids


class TestFanDisplayName:
    def test_name_uses_capability_label(self):
        state = AppState()
        state.set_capabilities(_make_nvidia_caps())
        assert state.fan_display_name("nvidia_gpu:0000:01:00.0") == "NVIDIA GeForce RTX 4080 Fan"

    def test_name_fallback_without_capability(self):
        state = AppState()
        assert state.fan_display_name("nvidia_gpu:0000:01:00.0") == "NVIDIA D-GPU Fan"


# ---------------------------------------------------------------------------
# Read-only safety
# ---------------------------------------------------------------------------


class TestReadOnlySafety:
    def test_diagnostics_fan_control_method_is_read_only(self):
        state = AppState()
        state.set_capabilities(_make_nvidia_caps())
        fan = FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1500)
        assert _fan_control_method(fan, state) == "read-only"

    def test_diagnostics_fan_control_method_read_only_without_caps(self):
        # Source alone is authoritative — read-only even if caps absent.
        fan = FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1500)
        assert _fan_control_method(fan, None) == "read-only"

    def test_member_role_is_gpu(self):
        # A hand-edited/legacy profile member with an nvidia_gpu source still
        # classifies as GPU (0% floor, harmless — the control loop no-ops it).
        member = ControlMember(source="nvidia_gpu", member_id="nvidia_gpu:0000:01:00.0")
        assert infer_member_role(member) == CONTROL_ROLE_GPU


# ---------------------------------------------------------------------------
# Measured duty_pct (DEC-204) — distinct from commanded PWM
# ---------------------------------------------------------------------------


class TestDutyPct:
    def test_parse_fans_carries_duty_pct(self):
        fans = parse_fans(
            {
                "fans": [
                    {
                        "id": "nvidia_gpu:0000:01:00.0",
                        "source": "nvidia_gpu",
                        "rpm": 1400,
                        "duty_pct": 55,
                    }
                ]
            }
        )
        assert len(fans) == 1
        assert fans[0].duty_pct == 55
        # Read-only: no commanded PWM.
        assert fans[0].last_commanded_pwm is None

    def test_duty_pct_defaults_none_and_absent_on_wire(self):
        # Old daemons omit duty_pct → parses to None.
        fans = parse_fans({"fans": [{"id": "openfan:ch00", "source": "openfan", "rpm": 1200}]})
        assert fans[0].duty_pct is None

    def test_duty_pct_flows_into_the_card_vm(self):
        """A read-only NVIDIA fan gets its own card carrying the measured duty —
        it has no commanded PWM, so duty is the only speed signal it has."""
        fan = FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1400, duty_pct=55)
        cards = build_fan_card_vms([fan], active_profile=None, overrides=[])
        card = next(c for c in cards if c.member_fan_ids == ("nvidia_gpu:0000:01:00.0",))
        assert card.is_read_only is True
        assert card.duty_pct == 55
        assert card.pwm_pct is None

    def test_card_shows_duty_not_pwm(self, qtbot):
        fan = FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1400, duty_pct=55)
        vm = build_fan_card_vms([fan], active_profile=None, overrides=[])[0]
        card = FanControlCard(vm)
        qtbot.addWidget(card)
        # Measured duty is labelled "duty" so it is never read as commanded PWM.
        assert card._speed_value.text() == "55% duty"
        assert card._rpm_value.text() == "1400"

    def test_card_shows_zero_duty(self, qtbot):
        # Guard the 0-vs-None falsy trap: duty_pct=0 must render "0% duty", not be
        # dropped as if absent (the card gates on `is not None`, not truthiness). A
        # genuinely-stopped NVIDIA fan reads 0 and must still show a duty value.
        fan = FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=0, duty_pct=0)
        vm = build_fan_card_vms([fan], active_profile=None, overrides=[])[0]
        card = FanControlCard(vm)
        qtbot.addWidget(card)
        assert card._speed_value.text() == "0% duty"

    def test_commanded_pwm_wins_over_duty(self, qtbot):
        # Precedence contract: when both are present, the daemon-commanded PWM
        # is shown (not the measured duty) — a hypothetical future source with
        # both must never render duty in place of the commanded value.
        from control_ofc.services.fan_cards_view import FanCardVM, FanState

        vm = FanCardVM(
            control_id="c1",
            label="Fan",
            is_unassigned=False,
            is_read_only=False,
            fan_count=1,
            member_fan_ids=("hwmon:x:pwm1",),
            rpm=1000,
            pwm_pct=40,
            duty_pct=55,
            temp_c=None,
            state=FanState.NORMAL,
            overridden=False,
            curve=None,
        )
        card = FanControlCard(vm)
        qtbot.addWidget(card)
        assert card._speed_value.text() == "40%"
        assert "duty" not in card._speed_value.text()

    def test_read_only_card_offers_no_edit(self, qtbot):
        """A read-only fan cannot be assigned to a control (DEC-102), so an Edit
        button would be dead. It is hidden, and the chip says why."""
        fan = FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1400, duty_pct=55)
        vm = build_fan_card_vms([fan], active_profile=None, overrides=[])[0]
        card = FanControlCard(vm)
        qtbot.addWidget(card)
        card.show()
        assert card._state_chip.text() == "Read-only"
        assert not card._edit_btn.isVisible()


# ---------------------------------------------------------------------------
# Sensor classification (nouveau / nvml)
# ---------------------------------------------------------------------------


class TestSensorClassification:
    def test_nouveau_temp1_is_gpu(self):
        c = classify_sensor("nouveau", "temp1")
        assert c.source_class == "gpu_package"
        assert "NVIDIA GPU" in c.display_description

    def test_nvml_temp_is_gpu(self):
        c = classify_sensor("nvml", "GPU")
        assert c.source_class == "gpu_package"


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------


class TestDemoMode:
    def test_demo_capabilities_includes_nvidia_gpu(self):
        caps = DemoService().capabilities()
        assert caps.nvidia_gpu.present
        assert caps.nvidia_gpu.fan_control_method == "read_only"
        assert caps.nvidia_gpu.driver == "nvidia"

    def test_demo_nvidia_fan_is_read_only_with_duty(self):
        fans = DemoService().fans()
        nvidia = [f for f in fans if f.source == "nvidia_gpu"]
        assert len(nvidia) == 1
        # Read-only: no commanded PWM, but a firmware-reported measured duty.
        assert nvidia[0].last_commanded_pwm is None
        assert nvidia[0].duty_pct is not None

    def test_demo_sensors_include_nvidia_gpu(self):
        sensors = DemoService().sensors()
        nvidia = [s for s in sensors if s.source == "nvidia_gpu"]
        assert len(nvidia) >= 1
        assert all(s.chip_name == "nouveau" for s in nvidia)

    def test_demo_diagnostics_include_nvidia_gpu(self):
        diag = DemoService().hardware_diagnostics()
        assert diag.nvidia_gpu is not None
        assert diag.nvidia_gpu.fan_control_method == "read_only"


# ---------------------------------------------------------------------------
# Fan wizard excludes read-only NVIDIA fans
# ---------------------------------------------------------------------------


class TestFanWizardExcludesNvidia:
    def test_build_targets_skips_nvidia_gpu(self, qtbot):
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        state = AppState()
        state.fans = [
            FanReading(id="openfan:ch00", source="openfan", rpm=1200),
            FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1500),
        ]
        wizard = FanConfigWizard(state=state, client=MagicMock())
        qtbot.addWidget(wizard)
        target_ids = {t["id"] for t in wizard._targets}
        assert "openfan:ch00" in target_ids
        assert "nvidia_gpu:0000:01:00.0" not in target_ids


# ---------------------------------------------------------------------------
# Dashboard GPU card
# ---------------------------------------------------------------------------


class TestDashboardGpuCard:
    def test_read_only_gpu_card_is_labelled_with_the_model(self, qtbot, app_state):
        """DEC-222: the summary cards (and their GPU-titled face) were removed, so
        the GPU is now identified on its own read-only fan card. The label comes
        from AppState.fan_display_name, which resolves the model from capabilities
        — a bare fan id here would mean the user cannot tell which GPU it is."""
        app_state.set_capabilities(_make_nvidia_caps())
        fan = FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1500, duty_pct=42)
        vm = build_fan_card_vms(
            [fan],
            active_profile=None,
            overrides=[],
            caps=app_state.capabilities,
            display_name=app_state.fan_display_name,
        )[0]
        card = FanControlCard(vm)
        qtbot.addWidget(card)
        assert vm.is_read_only is True
        assert card._name.text() == "NVIDIA GeForce RTX 4080 Fan"
        assert card._speed_value.text() == "42% duty"


# ---------------------------------------------------------------------------
# Support-bundle GPU status text
# ---------------------------------------------------------------------------


class TestFormatGpuStatus:
    def test_nvidia_section_present(self):
        from control_ofc.services.diagnostics_service import DiagnosticsService

        state = AppState()
        state.set_capabilities(_make_nvidia_caps())
        state.fans = [
            FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1400, duty_pct=55)
        ]
        text = DiagnosticsService(state=state).format_gpu_status()
        assert "NVIDIA GPU:" in text
        assert "NVIDIA GeForce RTX 4080" in text
        assert "565.77" in text  # driver version line
        assert "Fan write supported: No" in text

    def test_nvidia_section_absent(self):
        from control_ofc.services.diagnostics_service import DiagnosticsService

        state = AppState()
        state.set_capabilities(Capabilities())
        text = DiagnosticsService(state=state).format_gpu_status()
        assert "No NVIDIA discrete GPU detected" in text


# ---------------------------------------------------------------------------
# Controls curve-member picker excludes read-only NVIDIA fans (safety)
# ---------------------------------------------------------------------------


class TestControlsMemberPickerExcludesNvidia:
    """DEC-204: a read-only NVIDIA fan must never be offered as a curve member."""

    def test_nvidia_fan_omitted_from_member_picker(self, qtbot, monkeypatch):
        from control_ofc.services.profile_service import (
            ControlMode,
            LogicalControl,
            Profile,
            ProfileService,
        )
        from control_ofc.ui.pages import controls_page as cp_module
        from control_ofc.ui.widgets import member_editor as me_module

        captured: dict = {"available": None}

        class _StubDialog:
            def __init__(self, _current, available, _assigned=None, role_name="", parent=None):
                captured["available"] = available

            def exec(self):
                return 0  # Cancel — the picker must not mutate state.

            def get_members(self):
                return []

        monkeypatch.setattr(me_module, "MemberEditorDialog", _StubDialog)

        state = AppState()
        state.set_capabilities(_make_nvidia_caps())
        state.fans = [
            FanReading(id="openfan:ch00", source="openfan", rpm=1200),
            FanReading(id="nvidia_gpu:0000:01:00.0", source="nvidia_gpu", rpm=1500),
        ]

        profile_service = ProfileService()
        profile = Profile(id="p1", name="Test")
        profile.controls = [LogicalControl(id="c1", name="CPU", mode=ControlMode.CURVE)]
        profile_service._profiles = {"p1": profile}
        profile_service._active_id = "p1"

        page = cp_module.ControlsPage(state=state, profile_service=profile_service)
        qtbot.addWidget(page)
        page._on_edit_members("c1")

        ids = {entry["id"] for entry in (captured["available"] or [])}
        assert "openfan:ch00" in ids
        assert "nvidia_gpu:0000:01:00.0" not in ids
