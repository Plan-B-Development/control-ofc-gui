"""AIO-MB Phase 4 (DEC-316): cooling-device topology + device capability policy.

Three surfaces, one theme — **the daemon owns the safety numbers and the GUI
displays them**. The tests that matter most here are the ones asserting that a
missing wire field reads as *unknown* rather than as zero: `effective_min_pwm_pct`
typed with a 0 default would make the GUI believe a 0% floor on a pump against
any pre-2.31.0 daemon, which is the exact failure the optional field prevents.
"""

from __future__ import annotations

import json

import httpx
import pytest
from PySide6.QtWidgets import QLineEdit

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    Capabilities,
    ControlCapability,
    CoolingDevice,
    DevicePolicySummary,
    HwmonHeader,
    parse_capabilities,
    parse_cooling_devices,
    parse_fans,
    parse_hwmon_headers,
)
from control_ofc.services.cooling_device_view import (
    NO_COOLANT_NOTE,
    UNKNOWN_TEXT,
    build_cooling_device_view,
    build_cooling_device_views,
)
from control_ofc.services.pump_protection import (
    header_effective_floor_pct,
    header_is_pump_protected,
)
from control_ofc.ui.widgets.aio_config_dialog import (
    COOLING_DEVICE_KIND_AIO,
    DEFAULT_COOLING_DEVICE_NAME,
    AioConfigDialog,
)


def _client(handler) -> DaemonClient:
    client = DaemonClient.__new__(DaemonClient)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://localhost"
    )
    return client


def _caps(**control) -> Capabilities:
    control.setdefault("header_roles", True)
    return Capabilities(control=ControlCapability(**control))


PUMP_ID = "hwmon:it8696:isa-0a40:pwm5:PUMP"
RAD_ID = "hwmon:it8696:isa-0a40:pwm1:CPU_FAN"
RAD2_ID = "hwmon:it8696:isa-0a40:pwm2:CPU_OPT"


# ---------------------------------------------------------------------------
# Models — absent is unknown, never zero
# ---------------------------------------------------------------------------


class TestHeaderCapabilityFields:
    def test_absent_fields_are_none_not_zero(self):
        """The load-bearing default. A pre-2.31.0 daemon omits every one of
        these; a 0 default on the floor would claim a pump may idle at 0%."""
        h = parse_hwmon_headers({"headers": [{"id": PUMP_ID, "label": "PUMP"}]})[0]
        assert h.effective_min_pwm_pct is None
        assert h.stop_permitted is None
        assert h.cooling_device_id is None
        assert h.pwm_freq_hz is None
        assert h.rpm_min_threshold is None
        assert h.rpm_max_threshold is None
        assert h.tach_pulses_per_rev is None
        assert h.supported_pwm_enable_modes == []

    def test_audit_fields_parse_when_present(self):
        h = parse_hwmon_headers(
            {
                "headers": [
                    {
                        "id": PUMP_ID,
                        "label": "PUMP",
                        "effective_min_pwm_pct": 30,
                        "stop_permitted": False,
                        "cooling_device_id": "aio-1",
                        "pwm_freq_hz": 23437,
                        "supported_pwm_enable_modes": [0, 1, 2],
                        "rpm_min_threshold": 300,
                    }
                ]
            }
        )[0]
        assert h.effective_min_pwm_pct == 30
        assert h.stop_permitted is False
        assert h.cooling_device_id == "aio-1"
        assert h.pwm_freq_hz == 23437
        assert h.supported_pwm_enable_modes == [0, 1, 2]
        assert h.rpm_min_threshold == 300
        # it87 exposes neither of these — absent stays absent.
        assert h.rpm_max_threshold is None
        assert h.tach_pulses_per_rev is None

    def test_the_header_carries_no_live_enable_mode(self):
        """`pwmN_enable`'s current value is STATE, not a capability — the daemon
        writes that attribute itself when it takes a header over, so a
        discovery-time snapshot would report the pre-takeover mode for the whole
        process lifetime. It rides the poll instead. `pwm_mode` (`pwmN_mode`,
        DC vs PWM) is a different attribute and does stay on the header."""
        h = parse_hwmon_headers({"headers": [{"id": PUMP_ID, "pwm_mode": 1}]})[0]
        assert h.pwm_mode == 1
        assert not hasattr(h, "pwm_enable_mode"), (
            "the live enable mode must not be reintroduced onto the header — "
            "it would be stale for the daemon's whole lifetime"
        )

    def test_live_enable_mode_rides_the_poll(self):
        assert parse_fans({"fans": [{"id": "a"}]})[0].pwm_enable_mode is None
        assert parse_fans({"fans": [{"id": "a", "pwm_enable_mode": 1}]})[0].pwm_enable_mode == 1

    def test_fan_alarm_defaults_none_and_parses(self):
        assert parse_fans({"fans": [{"id": "a"}]})[0].fan_alarm is None
        assert parse_fans({"fans": [{"id": "a", "fan_alarm": True}]})[0].fan_alarm is True

    def test_capability_flag_defaults_false(self):
        assert parse_capabilities({}).control.cooling_devices is False
        assert (
            parse_capabilities({"control": {"cooling_devices": True}}).control.cooling_devices
            is True
        )


class TestCoolingDeviceParsing:
    def test_parses_topology_and_policies(self):
        inv = parse_cooling_devices(
            {
                "cooling_devices": [
                    {
                        "id": "aio-1",
                        "name": "AIO Cooling System",
                        "kind": "aio_liquid",
                        "pump_member": PUMP_ID,
                        "radiator_members": [RAD_ID, RAD2_ID],
                        "coolant_telemetry": "unavailable",
                        "device_policy": {
                            "id": "generic_pump",
                            "display_name": "Generic pump (unknown hardware)",
                            "minimum_safe_pwm_pct": 30,
                            "supports_stop": False,
                        },
                    }
                ],
                "available_policies": [
                    {"id": "generic_pump", "minimum_safe_pwm_pct": 30},
                    {"id": "generic_fan", "minimum_safe_pwm_pct": 0},
                ],
            }
        )
        dev = inv.cooling_devices[0]
        assert dev.id == "aio-1"
        assert dev.radiator_members == [RAD_ID, RAD2_ID]
        assert dev.device_policy.minimum_safe_pwm_pct == 30
        assert dev.device_policy.supports_stop is False
        assert [p.id for p in inv.available_policies] == ["generic_pump", "generic_fan"]

    def test_empty_and_malformed_payloads_degrade(self):
        assert parse_cooling_devices({}).cooling_devices == []
        # A non-dict element is skipped rather than crashing the poll.
        inv = parse_cooling_devices({"cooling_devices": [None, {"id": "ok"}]})
        assert [d.id for d in inv.cooling_devices] == ["ok"]

    def test_device_without_policy_block_still_parses(self):
        dev = parse_cooling_devices({"cooling_devices": [{"id": "a"}]}).cooling_devices[0]
        assert isinstance(dev.device_policy, DevicePolicySummary)
        assert dev.coolant_telemetry == "unavailable"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class TestCoolingDeviceClient:
    def test_get_hits_the_inventory_route(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(200, json={"cooling_devices": [{"id": "aio-1"}]})

        inv = _client(handler).get_cooling_devices()
        assert seen["method"] == "GET"
        assert seen["path"] == "/inventory/cooling-devices"
        assert inv.cooling_devices[0].id == "aio-1"

    def test_set_posts_only_the_fields_given(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json={"updated": True, "id": "aio-1"})

        _client(handler).set_cooling_device(
            "aio-1",
            name="AIO Cooling System",
            kind="aio_liquid",
            pump_member=PUMP_ID,
            radiator_members=[RAD_ID],
        )
        assert seen["method"] == "POST"
        assert seen["path"] == "/config/cooling-device"
        assert seen["json"] == {
            "id": "aio-1",
            "name": "AIO Cooling System",
            "kind": "aio_liquid",
            "pump_member": PUMP_ID,
            "radiator_members": [RAD_ID],
        }
        # Absent optionals are omitted, not sent as nulls — the daemon treats a
        # present key as an instruction.
        assert "coolant_sensor" not in seen["json"]

    def test_client_never_sends_a_safety_number(self):
        """There is no parameter for one. This pins the *absence*: adding a
        `minimum_safe_pwm` kwarg later would fail here rather than silently
        becoming a payload the daemon then has to reject."""
        import inspect

        params = set(inspect.signature(DaemonClient.set_cooling_device).parameters)
        for forbidden in (
            "minimum_safe_pwm",
            "minimum_safe_pwm_pct",
            "supports_stop",
            "effective_min_pwm_pct",
            "stop_permitted",
        ):
            assert forbidden not in params, f"{forbidden} must not be settable"
        assert "device_policy_id" in params, "a policy is selected by id"

    def test_delete_uses_the_id_path(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(200, json={"updated": True})

        _client(handler).delete_cooling_device("aio-1")
        assert seen["method"] == "DELETE"
        assert seen["path"] == "/config/cooling-device/aio-1"

    def test_daemon_rejection_surfaces(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "validation_error",
                        "message": "'minimum_safe_pwm' is not settable",
                        "retryable": False,
                    }
                },
            )

        with pytest.raises(DaemonError):
            _client(handler).set_cooling_device("aio-1")


# ---------------------------------------------------------------------------
# Pump protection — daemon-first, reconstruction as fallback
# ---------------------------------------------------------------------------


class TestDaemonReportedFloor:
    def test_daemon_value_is_preferred_over_reconstruction(self):
        caps = _caps()
        # A header the reconstruction would call *unprotected* (plain chassis
        # label), which the daemon nonetheless reports as unstoppable. The
        # daemon wins: it is the side that enforces.
        h = HwmonHeader(
            id="hwmon:x:y:pwm4:SYS_FAN2",
            label="SYS_FAN2",
            pwm_index=4,
            stop_permitted=False,
            effective_min_pwm_pct=30,
        )
        assert header_is_pump_protected(h, caps) is True
        assert header_effective_floor_pct(h, caps) == 30

    def test_reconstruction_runs_when_the_daemon_is_silent(self):
        caps = _caps()
        old = HwmonHeader(id=PUMP_ID, label="PUMP", pwm_index=5)
        assert old.stop_permitted is None
        assert header_is_pump_protected(old, caps) is True
        assert header_effective_floor_pct(old, caps) == 30

    def test_daemon_answer_is_not_gated_behind_the_role_capability(self):
        """`stop_permitted` is self-describing, so `docs/08` says it needs no
        capability flag. It was being read AFTER the `header_roles` gate, which
        contradicted that and failed unsafe: a header the daemon reports as
        unstoppable would read as stoppable whenever the flag was absent."""
        no_caps = Capabilities(control=ControlCapability(header_roles=False))
        pump = HwmonHeader(id=PUMP_ID, label="PUMP", pwm_index=5, stop_permitted=False)
        assert header_is_pump_protected(pump, no_caps) is True
        assert header_is_pump_protected(pump, None) is True
        # The RECONSTRUCTION still needs the gate — a pre-2.28.0 daemon has no
        # role model to reconstruct from.
        silent = HwmonHeader(id=PUMP_ID, label="PUMP", pwm_index=5)
        assert header_is_pump_protected(silent, no_caps) is False

    def test_none_is_not_false(self):
        """The failure this guards: reading an absent `stop_permitted` as
        "stoppable" would offer to stop a real pump on any older daemon."""
        caps = _caps()
        pump = HwmonHeader(id=PUMP_ID, label="PUMP", pwm_index=5, stop_permitted=None)
        assert header_is_pump_protected(pump, caps) is True

    def test_a_stoppable_header_reports_no_floor_rather_than_zero(self):
        caps = _caps()
        fan = HwmonHeader(id="hwmon:x:y:pwm2:SYS_FAN1", label="SYS_FAN1", pwm_index=2)
        assert header_is_pump_protected(fan, caps) is False
        # None, not 0 — the daemon applies no per-role floor here, and "0%"
        # would misrepresent that as "no floor at all".
        assert header_effective_floor_pct(fan, caps) is None

    def test_daemon_reported_zero_is_honoured_not_treated_as_absent(self):
        """0 is a real value and must not be conflated with None by a truthiness
        check — the classic `if not value` bug on an int field."""
        caps = _caps()
        fan = HwmonHeader(id="hwmon:x:y:pwm2:SYS_FAN1", pwm_index=2, effective_min_pwm_pct=0)
        assert header_effective_floor_pct(fan, caps) == 0

    def test_unknown_header_reports_nothing(self):
        assert header_effective_floor_pct(None, _caps()) is None


# ---------------------------------------------------------------------------
# View-model
# ---------------------------------------------------------------------------


def _device(**kw) -> CoolingDevice:
    base = dict(
        id="aio-1",
        name="AIO Cooling System",
        kind="aio_liquid",
        pump_member=PUMP_ID,
        radiator_members=[RAD_ID, RAD2_ID],
        coolant_telemetry="unavailable",
    )
    base.update(kw)
    return CoolingDevice(**base)


def _headers() -> list[HwmonHeader]:
    return [
        HwmonHeader(
            id=PUMP_ID,
            label="PUMP",
            pwm_index=5,
            stop_permitted=False,
            effective_min_pwm_pct=30,
        ),
        HwmonHeader(
            id=RAD_ID, label="CPU_FAN", pwm_index=1, stop_permitted=True, effective_min_pwm_pct=0
        ),
        HwmonHeader(
            id=RAD2_ID, label="CPU_OPT", pwm_index=2, stop_permitted=True, effective_min_pwm_pct=0
        ),
    ]


class TestCoolingDeviceView:
    def test_pump_plus_multiple_radiators(self):
        """The brief's topology requirement, rendered."""
        vm = build_cooling_device_view(_device(), headers=_headers(), capabilities=_caps())
        assert vm.pump is not None
        assert vm.pump.role_label == "Pump"
        assert vm.pump.label == "PUMP"
        assert vm.pump.floor_text == "30%"
        assert vm.pump.stop_permitted is False
        assert len(vm.radiators) == 2
        assert [r.label for r in vm.radiators] == ["CPU_FAN", "CPU_OPT"]
        assert len(vm.all_rows) == 3
        assert vm.state == "ok"

    def test_missing_coolant_is_neutral_not_a_warning(self):
        vm = build_cooling_device_view(_device(), headers=_headers(), capabilities=_caps())
        assert vm.coolant_available is False
        assert vm.coolant_note == NO_COOLANT_NOTE
        # Explicitly NOT a warning state — this is the normal motherboard case.
        assert vm.state == "ok"

    def test_coolant_available_is_reported(self):
        vm = build_cooling_device_view(
            _device(coolant_telemetry="available", coolant_sensor="s-coolant"),
            headers=_headers(),
            capabilities=_caps(),
            sensor_labels={"s-coolant": "Coolant"},
        )
        assert vm.coolant_available is True
        assert vm.coolant_note == "Coolant"

    def test_unknown_floor_renders_as_unknown_not_zero(self):
        """A pre-2.31.0 daemon reports no floor for a chassis fan; the row must
        say so rather than printing 0%."""
        headers = [HwmonHeader(id=RAD_ID, label="CPU_FAN", pwm_index=1)]
        vm = build_cooling_device_view(
            _device(pump_member=None, radiator_members=[RAD_ID]),
            headers=headers,
            capabilities=_caps(),
        )
        assert vm.radiators[0].floor_text == UNKNOWN_TEXT
        assert "0" not in vm.radiators[0].floor_text

    def test_a_member_with_no_discovered_header_is_flagged(self):
        vm = build_cooling_device_view(
            _device(radiator_members=[RAD_ID, "hwmon:gone:x:pwm9:OLD"]),
            headers=_headers(),
            capabilities=_caps(),
        )
        stale = vm.radiators[1]
        assert stale.missing is True
        assert stale.state == "warn"
        assert vm.missing_members == ["hwmon:gone:x:pwm9:OLD"]
        assert vm.state == "warn"

    def test_unknown_kind_is_rendered_not_dropped(self):
        """273-i: a device type a newer daemon adds must not make the user's
        cooler vanish from their own screen."""
        vm = build_cooling_device_view(
            _device(kind="thermosiphon"), headers=_headers(), capabilities=_caps()
        )
        assert vm.kind_label == "Thermosiphon"
        assert vm.pump is not None

    def test_display_name_resolver_is_used(self):
        vm = build_cooling_device_view(
            _device(),
            headers=_headers(),
            capabilities=_caps(),
            display_name=lambda mid: {PUMP_ID: "My Pump"}.get(mid, ""),
        )
        assert vm.pump.label == "My Pump"
        # A resolver returning "" means "no alias", NOT "no name" — it must fall
        # back to the header's own label, never to the raw stable id. Asserted
        # exactly rather than as a disjunction: `x == A or x == B` would pass
        # whichever rung the code actually took, which is no assertion at all.
        assert vm.radiators[0].label == "CPU_FAN"

    def test_a_member_with_no_alias_and_no_header_shows_its_id(
        self,
    ):
        """Last rung of the ladder — better a stable id than an empty row."""
        vm = build_cooling_device_view(
            _device(pump_member=None, radiator_members=["hwmon:gone:x:pwm9:OLD"]),
            headers=_headers(),
            capabilities=_caps(),
            display_name=lambda mid: "",
        )
        assert vm.radiators[0].label == "hwmon:gone:x:pwm9:OLD"

    def test_device_with_no_members_is_still_renderable(self):
        vm = build_cooling_device_view(CoolingDevice(id="empty"), headers=[], capabilities=_caps())
        assert vm.pump is None
        assert vm.all_rows == []
        assert vm.sensor_label == UNKNOWN_TEXT
        assert vm.name  # falls back to the kind label rather than being blank

    def test_builds_many_preserving_order(self):
        vms = build_cooling_device_views(
            [_device(), _device(id="loop-1", name="Custom Loop", kind="custom_loop")],
            headers=_headers(),
            capabilities=_caps(),
        )
        assert [v.device_id for v in vms] == ["aio-1", "loop-1"]
        assert vms[1].kind_label == "Custom loop"


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


def _dialog(qtbot, **kw) -> AioConfigDialog:
    kw.setdefault("pump_label", "PUMP")
    kw.setdefault("monitor_only", False)
    kw.setdefault(
        "fan_candidates",
        [{"id": RAD_ID, "label": "CPU_FAN", "source": "hwmon", "preselect": True}],
    )
    kw.setdefault("sensor_choices", [{"id": "s1", "label": "Tctl", "preferred": True}])
    dlg = AioConfigDialog(**kw)
    qtbot.addWidget(dlg)
    return dlg


class TestAioDialogTopology:
    def test_name_field_exists_and_is_accessible(self, qtbot):
        dlg = _dialog(qtbot)
        edit = dlg.findChild(QLineEdit, "AioConfig_Edit_deviceName")
        assert edit is not None
        assert edit.text() == DEFAULT_COOLING_DEVICE_NAME
        # DEC-251: a bare input needs an explicit accessible name.
        assert edit.accessibleName()

    def test_result_carries_the_topology(self, qtbot):
        dlg = _dialog(qtbot, has_coolant=False)
        spec = dlg.get_result()["cooling_device"]
        assert spec["name"] == DEFAULT_COOLING_DEVICE_NAME
        assert spec["kind"] == COOLING_DEVICE_KIND_AIO
        assert spec["radiator_members"] == [RAD_ID]
        assert spec["preferred_sensor"] == "s1"

    def test_a_typed_name_is_used_and_trimmed(self, qtbot):
        dlg = _dialog(qtbot)
        dlg.findChild(QLineEdit, "AioConfig_Edit_deviceName").setText("  Loop A  ")
        assert dlg.get_result()["cooling_device"]["name"] == "Loop A"

    def test_a_blank_name_falls_back_to_the_default(self, qtbot):
        dlg = _dialog(qtbot)
        dlg.findChild(QLineEdit, "AioConfig_Edit_deviceName").setText("   ")
        assert dlg.get_result()["cooling_device"]["name"] == DEFAULT_COOLING_DEVICE_NAME

    def test_coolant_sensor_claimed_only_when_the_machine_has_one(self, qtbot):
        """A CPU-package fallback must not be recorded as coolant telemetry the
        machine does not have — that would make the topology lie."""
        assert (
            _dialog(qtbot, has_coolant=False).get_result()["cooling_device"]["coolant_sensor"]
            is None
        )
        assert (
            _dialog(qtbot, has_coolant=True).get_result()["cooling_device"]["coolant_sensor"]
            == "s1"
        )

    def test_existing_result_keys_are_unchanged(self, qtbot):
        """The topology is additive — the Phase 2/3 contract this dialog already
        had must keep working."""
        res = _dialog(qtbot).get_result()
        for key in (
            "pump_pct",
            "pump_strategy",
            "pump_member_id",
            "role_assignments",
            "radiator_members",
            "radiator_sensor_id",
        ):
            assert key in res


# ---------------------------------------------------------------------------
# Call site — the topology POST is wired into the real flow
# ---------------------------------------------------------------------------


class _TopologyClient:
    """Records the cooling-device write, optionally failing it."""

    def __init__(self, *, fail: Exception | None = None):
        self.device_calls: list[dict] = []
        self.role_calls: list[tuple] = []
        self._fail = fail
        self.headers_to_return: list = []
        # DEC-319: the page re-reads the inventory after a successful write so
        # the Controls picker's reservation is not stale for ~300 s.
        self.devices_to_return: list = []
        self.get_device_calls = 0

    def get_cooling_devices(self):
        from control_ofc.api.models import CoolingDeviceInventory

        self.get_device_calls += 1
        return CoolingDeviceInventory(cooling_devices=list(self.devices_to_return))

    def set_cooling_device(self, device_id, **kw):
        self.device_calls.append({"id": device_id, **kw})
        if self._fail is not None:
            raise self._fail
        return {"updated": True, "id": device_id}

    def set_header_role(self, header_id, role):
        from control_ofc.api.models import parse_header_role

        self.role_calls.append((header_id, role))
        return parse_header_role({"updated": True, "header_id": header_id, "role": role})

    def hwmon_headers(self):
        return self.headers_to_return


class TestConfigureAioSavesTopology:
    """**The call site, not the helper.** `CLAUDE.md` records "extracting a rule
    into a testable function does NOT test the call site" as a six-time repeat
    failure here, so these drive the real `_on_configure_aio` with a faked
    dialog rather than calling `_save_cooling_device` directly.
    """

    def _page(self, qtbot, app_state, profile_service, client, *, supports=True):
        from control_ofc.ui.pages.controls_page import ControlsPage

        app_state.capabilities = _caps(cooling_devices=supports, autonomous_control=True)
        app_state.hwmon_headers = _headers()
        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        page._client = client
        return page

    def _fake_dialog(self, monkeypatch, *, with_topology=True):
        spec = {
            "name": "AIO Cooling System",
            "kind": COOLING_DEVICE_KIND_AIO,
            "pump_member": PUMP_ID,
            "radiator_members": [RAD_ID],
            "preferred_sensor": "s1",
            "coolant_sensor": None,
        }

        class _FakeDialog:
            def __init__(self, **kwargs):
                pass

            def exec(self):
                return True

            def get_result(self):
                res = {
                    "pump_pct": 80,
                    "pump_strategy": "fixed",
                    "pump_member_id": PUMP_ID,
                    "role_assignments": [],
                    "radiator_members": [{"id": RAD_ID, "label": "CPU_FAN", "source": "hwmon"}],
                    "radiator_sensor_id": "s1",
                }
                if with_topology:
                    res["cooling_device"] = spec
                return res

        monkeypatch.setattr("control_ofc.ui.widgets.aio_config_dialog.AioConfigDialog", _FakeDialog)

    def test_topology_is_posted_by_the_real_flow(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        client = _TopologyClient()
        page = self._page(qtbot, app_state, profile_service, client)
        self._fake_dialog(monkeypatch)

        page._on_configure_aio()

        assert len(client.device_calls) == 1, "the flow must save the topology"
        call = client.device_calls[0]
        assert call["id"] == "aio-1"
        assert call["pump_member"] == PUMP_ID
        assert call["radiator_members"] == [RAD_ID]
        assert call["kind"] == COOLING_DEVICE_KIND_AIO

    def test_the_profile_is_still_built_when_the_topology_write_fails(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        """Non-fatal, and this is the assertion that proves it. Topology is
        metadata the engine never reads, so losing it must not cost the user
        the AIO controls they actually asked for."""
        client = _TopologyClient(
            fail=DaemonError(code="persistence_failed", message="read-only filesystem")
        )
        page = self._page(qtbot, app_state, profile_service, client)
        self._fake_dialog(monkeypatch)

        page._on_configure_aio()  # must not raise

        assert len(client.device_calls) == 1, "it was attempted"
        profile = page._get_current_profile()
        names = [c.name for c in profile.controls]
        assert any("Pump" in n for n in names), f"controls must survive: {names}"
        assert any("Radiator" in n for n in names), f"controls must survive: {names}"

    def test_the_controls_exist_before_the_topology_is_written(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        """Ordering: the POST runs LAST. If it ever moved ahead of
        `build_aio_controls`, a slow or hanging daemon would delay — or a raise
        would abort — the part the user actually asked for."""
        client = _TopologyClient()
        page = self._page(qtbot, app_state, profile_service, client)
        self._fake_dialog(monkeypatch)

        seen: dict = {}
        original = client.set_cooling_device

        def _spy(device_id, **kw):
            profile = page._get_current_profile()
            seen["controls_at_post_time"] = [c.name for c in profile.controls]
            return original(device_id, **kw)

        client.set_cooling_device = _spy
        page._on_configure_aio()

        assert any("Pump" in n for n in seen["controls_at_post_time"]), (
            f"controls must already exist when the topology is posted: {seen}"
        )

    def test_nothing_is_posted_against_an_older_daemon(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        """A pre-2.31.0 daemon 404s the route; calling it would surface a
        spurious error on a flow that otherwise succeeded."""
        client = _TopologyClient()
        page = self._page(qtbot, app_state, profile_service, client, supports=False)
        self._fake_dialog(monkeypatch)

        page._on_configure_aio()

        assert client.device_calls == []
        profile = page._get_current_profile()
        assert profile.controls, "the AIO controls are still created"

    def test_a_dialog_without_topology_posts_nothing(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        """Defensive: the key is read with `.get`, so an older dialog result (or
        a test double) that omits it is a no-op rather than a crash."""
        client = _TopologyClient()
        page = self._page(qtbot, app_state, profile_service, client)
        self._fake_dialog(monkeypatch, with_topology=False)

        page._on_configure_aio()

        assert client.device_calls == []
