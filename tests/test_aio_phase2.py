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

    def test_synthesised_label_does_not_become_the_member_name(self):
        """DEC-229: `h.label or "Pump"` stopped producing its default.

        The daemon invents `pwmN` for a header whose chip publishes no label
        file, so `label` is never empty and the AIO pump was named `pwm1`.
        """
        headers = [
            _header("hwmon:z53:d:pwm1:pwm1", "pwm1", "z53", pwm_index=1),
            _header("hwmon:z53:d:pwm2:pwm2", "pwm2", "z53", pwm_index=2),
        ]
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member.member_label == "Pump"
        assert [m.member_label for m in det.radiator_members] == ["Radiator"]

    def test_aio_placeholder_fix_is_naming_only_the_floor_never_depended_on_it(self):
        """Scope guard: here DEC-229 renames, it does not rescue a floor.

        Every header that can reach this function is on a liquid-cooler chip —
        `detect_aio_setup` filters on `h.is_aio`, and the daemon sets that solely
        from `is_liquid_cooler_chip(chip_name)` (`pwm_discovery.rs:116`, the only
        assignment site), whose list is Kraken/Aquacomputer parts. A motherboard
        chip is therefore never `is_aio`, and constructing one here would test an
        input the system cannot produce.

        On the chips that *can* arrive, `_member_is_aio_header` already recovers
        the pump role from the chip segment of the member id, so the 30% floor
        held even when the label was the useless `pwm1`. Asserting that
        explicitly stops a future reader — or a changelog — from claiming this
        change restored a safety property that was never lost.
        """
        from control_ofc.services.profile_service import (
            LogicalControl,
            apply_role_floor,
            infer_member_role,
        )

        headers = [_header("hwmon:z53:d:pwm1:pwm1", "pwm1", "z53", pwm_index=1)]
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member.member_label == "Pump"  # the naming fix

        # …and the pre-fix label would have kept the floor anyway, via the id.
        stale = ControlMember(
            source="hwmon", member_id="hwmon:z53:d:pwm1:pwm1", member_label="pwm1"
        )
        assert infer_member_role(stale) == "cpu_or_pump"
        for member in (det.pump_member, stale):
            ctl = LogicalControl(id="c", name="Pump", members=[member])
            apply_role_floor(ctl)
            assert ctl.minimum_pct == 30

    def test_unclassified_motherboard_header_is_not_guessed_as_a_pump(self):
        """A motherboard header with no role evidence is still never proposed.

        REWRITTEN by DEC-312, and the old version is worth recording because it
        pinned the opposite rule for a good reason. It read:

            "Pins the `h.is_aio` gate itself: a motherboard header, even one the
            user has an AIO plugged into, is dropped before pump/radiator
            construction."

        That was correct while `is_aio` was the only evidence available — a
        chip-level flag meaning "this header hangs off a Kraken/Aquacomputer
        cooler". Reading a motherboard header as an AIO pump on that evidence
        would have been a guess. DEC-311 added real per-channel evidence
        (`role`), so the gate moved from the CHIP to the EVIDENCE: a motherboard
        header is proposed when something actually says it is a pump, and never
        otherwise.

        This still holds the line the old test was protecting — the case below is
        the ambiguous one (a `CPU_OPT`-style header the daemon leaves `unknown`,
        equally likely to be a pump or a second radiator fan), and it must stay
        unproposed. The daemon refuses to classify it for exactly this reason.
        """
        headers = [_header("hwmon:nct6798:x:pwm1:pwm1", "pwm1", "nct6798", is_aio=False)]
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member is None
        assert det.radiator_members == []

    def test_real_label_is_still_kept_verbatim(self):
        """The DEC-229 predicate stays narrow here too — `pwm2` on header 1
        names a *different* header and is therefore real information."""
        headers = [_header("hwmon:z53:d:pwm1:f", "pwm2", "z53", pwm_index=1)]
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member.member_label == "pwm2"


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
        assert pump_curve.flat_output_pct == 80.0  # the Fixed strategy: one level

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
    # DEC-312: the default strategy is Automatic, so no fixed percentage is
    # returned unless the user picks "Fixed speed". The High preset is still the
    # preselected fixed level — asserted in the Fixed-strategy test.
    assert res["pump_strategy"] == "automatic"
    assert res["pump_pct"] is None
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

    # DEC-233: "Configure AIO" is now an action in the "Set up ▾" menu.
    page._on_capabilities_updated(Capabilities())
    assert page._configure_aio_action.isVisible() is False
    page._on_capabilities_updated(
        Capabilities(aio_hwmon=AioHwmonCapability(present=True, status="supported"))
    )
    assert page._configure_aio_action.isVisible() is True


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
                "pump_strategy": "fixed",
                "pump_member_id": "hwmon:z53:d:pwm1:Pump",
                "role_assignments": [],
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
        def __init__(self, members, available, assigned=None, role_name="", parent=None, **_kw):
            captured["available"] = available

        def exec(self):
            return False

    monkeypatch.setattr(
        "control_ofc.ui.widgets.member_editor.MemberEditorDialog", _FakeMemberDialog
    )
    page._on_edit_members(control_id)
    labels = [a["label"] for a in captured["available"]]
    assert any("(AIO pump)" in lbl for lbl in labels)
