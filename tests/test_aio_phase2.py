"""AIO Phase 2 (DEC-157) — guided setup detection/creation, dialog, settings,
and dashboard grouping. Outcome-focused; no real hardware."""

from __future__ import annotations

from control_ofc.api.models import HwmonHeader, SensorReading
from control_ofc.services.app_settings_service import (
    MACHINE_SPECIFIC_KEYS,
    AppSettings,
    AppSettingsService,
)
from control_ofc.services.profile_service import (
    AIO_PUMP_DEFAULT_PCT,
    AIO_PUMP_PRESETS,
    ControlMember,
    CurveType,
    Profile,
    build_aio_controls,
    detect_aio_setup,
)


def _header(hid, label, chip, *, is_aio=True, is_writable=True, pwm_index=1):
    return HwmonHeader(
        id=hid,
        label=label,
        chip_name=chip,
        is_aio=is_aio,
        is_writable=is_writable,
        pwm_index=pwm_index,
    )


def _sensor(sid, kind, label, chip):
    return SensorReading(id=sid, kind=kind, label=label, chip_name=chip)


# ---------------------------------------------------------------------------
# detect_aio_setup
# ---------------------------------------------------------------------------


class TestDetectAioSetup:
    def test_detects_pump_radiator_and_coolant(self):
        headers = [
            _header("hwmon:z53:d:pwm1:Pump", "Pump", "z53", pwm_index=1),
            _header("hwmon:z53:d:pwm2:Fan", "Fan", "z53", pwm_index=2),
        ]
        sensors = [_sensor("hwmon:z53:d:Coolant", "coolant_temp", "Coolant", "z53")]
        det = detect_aio_setup(headers, sensors, {})
        assert det.pump_member is not None
        assert det.pump_member.member_id == "hwmon:z53:d:pwm1:Pump"
        assert det.coolant_sensor_id == "hwmon:z53:d:Coolant"
        assert not det.monitor_only
        assert [m.member_id for m in det.radiator_members] == ["hwmon:z53:d:pwm2:Fan"]

    def test_monitor_only_when_no_writable_pump(self):
        # NZXT Kraken2: coolant sensor present, no writable pwm header at all.
        sensors = [_sensor("hwmon:kraken2:d:Coolant", "coolant_temp", "Coolant", "kraken2")]
        det = detect_aio_setup([], sensors, {})
        assert det.pump_member is None
        assert det.monitor_only

    def test_pump_falls_back_to_lowest_pwm_index(self):
        headers = [
            _header("hwmon:z53:d:pwm2:f", "pwm2", "z53", pwm_index=2),
            _header("hwmon:z53:d:pwm1:f", "pwm1", "z53", pwm_index=1),
        ]
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member.member_id == "hwmon:z53:d:pwm1:f"

    def test_read_only_aio_header_ignored_for_pump(self):
        headers = [_header("hwmon:z53:d:pwm1:Pump", "Pump", "z53", is_writable=False)]
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member is None
        assert det.monitor_only


# ---------------------------------------------------------------------------
# build_aio_controls
# ---------------------------------------------------------------------------


class TestBuildAioControls:
    def test_creates_pump_flat_and_radiator_curve(self):
        profile = Profile()
        pump = ControlMember(source="hwmon", member_id="hwmon:z53:d:pwm1:Pump", member_label="Pump")
        rad = [
            ControlMember(
                source="hwmon", member_id="hwmon:it8696:d:pwm2:CHA", member_label="Radiator Top"
            )
        ]
        created = build_aio_controls(
            profile,
            pump_member=pump,
            pump_pct=80,
            radiator_members=rad,
            radiator_sensor_id="coolant1",
        )
        assert len(created) == 2

        pump_ctrl = next(c for c in created if c.name == "AIO Pump")
        assert pump_ctrl.minimum_pct == 30.0  # pump floor (DEC-095)
        pump_curve = profile.get_curve(pump_ctrl.curve_id)
        assert pump_curve.type == CurveType.FLAT
        assert pump_curve.flat_output_pct == 80.0  # constant, not a temp curve

        rad_ctrl = next(c for c in created if c.name == "AIO Radiator")
        assert rad_ctrl.minimum_pct == 20.0  # chassis floor
        rad_curve = profile.get_curve(rad_ctrl.curve_id)
        assert rad_curve.type == CurveType.GRAPH
        assert rad_curve.sensor_id == "coolant1"
        assert rad_curve.points  # coolant-range points seeded

    def test_monitor_only_skips_pump_creates_radiator(self):
        profile = Profile()
        rad = [ControlMember(source="hwmon", member_id="x", member_label="Rad")]
        created = build_aio_controls(
            profile,
            pump_member=None,
            pump_pct=0,
            radiator_members=rad,
            radiator_sensor_id="c",
        )
        assert [c.name for c in created] == ["AIO Radiator"]

    def test_pump_preset_constants(self):
        assert AIO_PUMP_DEFAULT_PCT == 80
        as_map = dict(AIO_PUMP_PRESETS)
        assert as_map == {"Low": 30, "Mid": 60, "High": 80, "Max": 100}


# ---------------------------------------------------------------------------
# show_aio_pump_info setting
# ---------------------------------------------------------------------------


def test_show_aio_pump_info_default_true():
    assert AppSettings().show_aio_pump_info is True


def test_show_aio_pump_info_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    svc.update(show_aio_pump_info=False)
    svc2 = AppSettingsService()
    svc2.load()
    assert svc2.settings.show_aio_pump_info is False


def test_show_aio_pump_info_travels_with_export():
    # Mirrors show_gpu_zero_rpm_warning — a behaviour pref, NOT machine-specific.
    assert "show_aio_pump_info" not in MACHINE_SPECIFIC_KEYS
    assert AppSettings(show_aio_pump_info=False).portable_dict()["show_aio_pump_info"] is False


# ---------------------------------------------------------------------------
# Dashboard grouping
# ---------------------------------------------------------------------------


def test_coolant_kind_groups_as_aio_liquid():
    from control_ofc.ui.widgets.sensor_series_panel import _GROUP_ORDER, _SENSOR_KIND_GROUPS

    assert _SENSOR_KIND_GROUPS["coolant_temp"] == ("aio", "AIO / Liquid")
    assert _SENSOR_KIND_GROUPS["CoolantTemp"][0] == "aio"
    assert "aio" in _GROUP_ORDER


# ---------------------------------------------------------------------------
# AioConfigDialog
# ---------------------------------------------------------------------------


def test_aio_dialog_result_with_pump(qtbot):
    from control_ofc.ui.widgets.aio_config_dialog import AioConfigDialog

    dlg = AioConfigDialog(
        pump_label="Pump",
        monitor_only=False,
        fan_candidates=[
            {"id": "f1", "source": "hwmon", "label": "Radiator Top", "preselect": True},
            {"id": "f2", "source": "openfan", "label": "Case", "preselect": False},
        ],
        sensor_choices=[{"id": "c1", "label": "Coolant", "preferred": True}],
        default_sensor_id="c1",
    )
    qtbot.addWidget(dlg)
    res = dlg.get_result()
    assert res["pump_pct"] == AIO_PUMP_DEFAULT_PCT  # High default checked
    assert res["radiator_sensor_id"] == "c1"
    ids = {m["id"] for m in res["radiator_members"]}
    assert ids == {"f1"}  # only the preselected fan is checked


def test_aio_dialog_monitor_only_has_no_pump(qtbot):
    from control_ofc.ui.widgets.aio_config_dialog import AioConfigDialog

    dlg = AioConfigDialog(
        pump_label=None,
        monitor_only=True,
        fan_candidates=[],
        sensor_choices=[{"id": "c1", "label": "Coolant", "preferred": True}],
        default_sensor_id="c1",
    )
    qtbot.addWidget(dlg)
    res = dlg.get_result()
    assert res["pump_pct"] is None


# ---------------------------------------------------------------------------
# Controls page integration
# ---------------------------------------------------------------------------


def test_configure_aio_button_visibility_tracks_capability(qtbot, app_state, profile_service):
    from control_ofc.api.models import AioHwmonCapability, Capabilities
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)

    page._on_capabilities_updated(Capabilities())
    assert page._configure_aio_btn.isHidden()
    page._on_capabilities_updated(
        Capabilities(aio_hwmon=AioHwmonCapability(present=True, status="supported"))
    )
    assert not page._configure_aio_btn.isHidden()


def test_configure_aio_creates_pump_control(qtbot, app_state, profile_service, monkeypatch):
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    profile = page._get_current_profile()
    before = len(profile.controls)

    app_state.hwmon_headers = [
        _header("hwmon:z53:d:pwm1:Pump", "Pump", "z53", pwm_index=1),
    ]
    app_state.sensors = [_sensor("hwmon:z53:d:Coolant", "coolant_temp", "Coolant", "z53")]
    app_state.fans = []

    class _FakeDialog:
        def __init__(self, **kwargs):
            pass

        def exec(self):
            return True

        def get_result(self):
            return {
                "pump_pct": 80,
                "radiator_members": [],
                "radiator_sensor_id": "hwmon:z53:d:Coolant",
            }

    monkeypatch.setattr("control_ofc.ui.widgets.aio_config_dialog.AioConfigDialog", _FakeDialog)

    page._on_configure_aio()

    assert len(profile.controls) == before + 1
    pump = next(c for c in profile.controls if c.name == "AIO Pump")
    assert pump.minimum_pct == 30.0


def test_member_picker_tags_aio_pump(qtbot, app_state, profile_service, monkeypatch):
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page._on_new_control(name="AIO Test")
    profile = page._get_current_profile()
    control_id = profile.controls[-1].id

    app_state.hwmon_headers = [_header("hwmon:z53:d:pwm1:Pump", "Pump", "z53")]
    app_state.fans = []

    captured: dict = {}

    class _FakeMemberDialog:
        def __init__(self, members, available, assigned=None, parent=None):
            captured["available"] = available

        def exec(self):
            return False

    monkeypatch.setattr(
        "control_ofc.ui.widgets.member_editor.MemberEditorDialog", _FakeMemberDialog
    )
    page._on_edit_members(control_id)
    labels = [a["label"] for a in captured["available"]]
    assert any("(AIO pump)" in lbl for lbl in labels)
