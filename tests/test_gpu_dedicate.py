"""DEC-221 — first-class GPU zero-RPM idle ("Dedicate GPU Fan").

Covers the pure builder (``build_gpu_control``), the dialog round-trip, the
Controls-page button gate + handler, the Part C guarantee that a dedicated
GPU-only curve floors at 0% in the editor, and the corrected zero-RPM popup
copy (Part B). Outcome-focused; no real hardware.
"""

from __future__ import annotations

from control_ofc.services.controls_view import curve_min_output_floor
from control_ofc.services.profile_service import (
    ControlMember,
    CurveType,
    LogicalControl,
    Profile,
    build_gpu_control,
)

GPU_ID = "amd_gpu:0000:03:00.0"
GPU_POINTS = [(45.0, 0.0), (47.0, 20.0), (58.0, 40.0), (75.0, 60.0), (95.0, 100.0)]


def _gpu_member(member_id: str = GPU_ID) -> ControlMember:
    return ControlMember(source="amd_gpu", member_id=member_id, member_label="9070XT Fan")


# ---------------------------------------------------------------------------
# build_gpu_control (pure)
# ---------------------------------------------------------------------------


class TestBuildGpuControl:
    def test_creates_gpu_only_zero_floor_curve(self):
        profile = Profile()
        created = build_gpu_control(
            profile, gpu_member=_gpu_member(), sensor_id="gpu_edge", zero_rpm=True
        )
        assert created is not None
        assert created in profile.controls
        # GPU-only control → role floor 0 (no chassis/CPU minimum).
        assert created.minimum_pct == 0.0
        assert len(created.members) == 1
        assert created.members[0].member_id == GPU_ID
        assert created.members[0].fan_zero_rpm is True  # the real 0-RPM lever

        curve = profile.get_curve(created.curve_id)
        assert curve is not None
        assert curve.type == CurveType.GRAPH
        assert curve.sensor_id == "gpu_edge"
        assert [(p.temp_c, p.output_pct) for p in curve.points] == GPU_POINTS

    def test_curve_idles_to_zero_below_first_point(self):
        # The idle *target* is 0% (graph clamps to the first point below 45 C).
        # True 0 RPM then comes from the firmware stop (fan_zero_rpm), not this
        # value — but the target must be 0, not the ~15% firmware floor.
        profile = Profile()
        created = build_gpu_control(profile, gpu_member=_gpu_member(), sensor_id="s")
        curve = profile.get_curve(created.curve_id)
        assert curve.interpolate(30.0) == 0.0
        assert curve.interpolate(95.0) == 100.0

    def test_zero_rpm_opt_out_leaves_flag_false(self):
        profile = Profile()
        created = build_gpu_control(
            profile, gpu_member=_gpu_member(), sensor_id="s", zero_rpm=False
        )
        assert created.members[0].fan_zero_rpm is False

    def test_zero_rpm_defaults_true(self):
        profile = Profile()
        created = build_gpu_control(profile, gpu_member=_gpu_member(), sensor_id="s")
        assert created.members[0].fan_zero_rpm is True

    def test_removes_member_from_prior_control_keeps_others(self):
        profile = Profile()
        gpu = _gpu_member()
        chassis = ControlMember(source="openfan", member_id="openfan:ch00", member_label="Case")
        profile.controls.append(LogicalControl(name="All Fans", members=[gpu, chassis]))

        created = build_gpu_control(profile, gpu_member=gpu, sensor_id="s")

        all_fans = next(c for c in profile.controls if c.name == "All Fans")
        # GPU pulled out of the shared control; the chassis fan stays.
        assert [m.member_id for m in all_fans.members] == ["openfan:ch00"]
        # ...and it drives the fan exactly once (no double-writer).
        drivers = [c for c in profile.controls if any(m.member_id == GPU_ID for m in c.members)]
        assert drivers == [created]

    def test_none_or_empty_member_is_noop(self):
        profile = Profile()
        assert build_gpu_control(profile, gpu_member=None, sensor_id="s") is None
        empty = ControlMember(source="amd_gpu", member_id="")
        assert build_gpu_control(profile, gpu_member=empty, sensor_id="s") is None
        assert profile.controls == []
        assert profile.curves == []

    def test_repeat_dedicate_drops_the_vacated_control(self):
        # A second dedicate must not leave the first (now-empty) GPU control
        # behind — the vacated control is dropped, only the newest drives.
        profile = Profile()
        gpu = _gpu_member()
        first = build_gpu_control(profile, gpu_member=gpu, sensor_id="s1")
        second = build_gpu_control(profile, gpu_member=gpu, sensor_id="s2")

        drivers = [c for c in profile.controls if any(m.member_id == GPU_ID for m in c.members)]
        assert drivers == [second]  # exactly one writer, the newest
        assert first not in profile.controls  # the emptied control is gone
        assert all(c.members for c in profile.controls)  # no empty orphan left

    def test_preexisting_empty_control_is_not_pruned(self):
        # We only drop controls THIS call empties — a pre-existing empty control
        # that never held the GPU is left untouched.
        profile = Profile()
        empty = LogicalControl(name="Empty", members=[])
        profile.controls.append(empty)
        build_gpu_control(profile, gpu_member=_gpu_member(), sensor_id="s")
        assert empty in profile.controls


# ---------------------------------------------------------------------------
# Part C — dedicated GPU curve floors at 0% in the editor (already-works pin)
# ---------------------------------------------------------------------------


def test_dedicated_gpu_curve_editor_floor_is_zero():
    profile = Profile()
    created = build_gpu_control(profile, gpu_member=_gpu_member(), sensor_id="s")
    # A GPU-only control contributes a 0% floor, so the curve editor lets the
    # user draw all the way down to 0% (Part C option a — no floor change).
    assert curve_min_output_floor(profile, created.curve_id) == 0.0


# ---------------------------------------------------------------------------
# GpuDedicateDialog
# ---------------------------------------------------------------------------


def test_gpu_dedicate_dialog_result(qtbot):
    from control_ofc.ui.widgets.gpu_dedicate_dialog import GpuDedicateDialog

    dlg = GpuDedicateDialog(
        gpu_label="9070XT Fan",
        sensor_choices=[
            {"id": "gpu_edge", "label": "edge", "preferred": True},
            {"id": "cpu", "label": "CPU", "preferred": False},
        ],
        default_sensor_id="gpu_edge",
        default_zero_rpm=True,
    )
    qtbot.addWidget(dlg)
    res = dlg.get_result()
    assert res["sensor_id"] == "gpu_edge"  # default preselected
    assert res["zero_rpm"] is True


def test_gpu_dedicate_dialog_zero_rpm_opt_out(qtbot):
    from control_ofc.ui.widgets.gpu_dedicate_dialog import GpuDedicateDialog

    dlg = GpuDedicateDialog(
        gpu_label="GPU",
        sensor_choices=[{"id": "s", "label": "edge", "preferred": True}],
        default_sensor_id="s",
        default_zero_rpm=False,
    )
    qtbot.addWidget(dlg)
    assert dlg.get_result()["zero_rpm"] is False


def test_gpu_dedicate_dialog_reads_live_widget_state(qtbot):
    # get_result() must reflect the USER's live selections, not the constructor
    # defaults — a cached-args implementation would pass the two tests above but
    # fail this one. Pick a non-default sensor and flip the checkbox.
    from control_ofc.ui.widgets.gpu_dedicate_dialog import GpuDedicateDialog

    dlg = GpuDedicateDialog(
        gpu_label="GPU",
        sensor_choices=[
            {"id": "gpu_edge", "label": "edge", "preferred": True},
            {"id": "gpu_junction", "label": "junction", "preferred": True},
        ],
        default_sensor_id="gpu_edge",
        default_zero_rpm=True,
    )
    qtbot.addWidget(dlg)
    dlg._sensor_combo.setCurrentIndex(dlg._sensor_combo.findData("gpu_junction"))
    dlg._zero_rpm_check.setChecked(False)
    res = dlg.get_result()
    assert res["sensor_id"] == "gpu_junction"  # live combo, not the default
    assert res["zero_rpm"] is False  # live checkbox, not the default


def test_gpu_dedicate_dialog_blocks_create_with_no_sensors(qtbot):
    # A GPU curve must bind to a sensor — with no choices the Create button is
    # disabled so the flow can't produce a sensorless, never-evaluated curve.
    from PySide6.QtWidgets import QPushButton

    from control_ofc.ui.widgets.gpu_dedicate_dialog import GpuDedicateDialog

    dlg = GpuDedicateDialog(gpu_label="GPU", sensor_choices=[], default_sensor_id=None)
    qtbot.addWidget(dlg)
    create = dlg.findChild(QPushButton, "GpuDedicate_Btn_create")
    assert create is not None and not create.isEnabled()
    assert dlg.get_result()["sensor_id"] == ""


# ---------------------------------------------------------------------------
# Controls-page button visibility gate
# ---------------------------------------------------------------------------


def test_dedicate_gpu_button_visibility_tracks_capability(qtbot, app_state, profile_service):
    from control_ofc.api.models import AmdGpuCapability, Capabilities
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)

    # No GPU → hidden.
    page._on_capabilities_updated(Capabilities())
    assert page._dedicate_gpu_btn.isHidden()

    # Present but read-only → hidden.
    page._on_capabilities_updated(Capabilities(amd_gpu=AmdGpuCapability(present=True)))
    assert page._dedicate_gpu_btn.isHidden()

    # Writable but no zero-RPM support → hidden (nothing to dedicate for).
    page._on_capabilities_updated(
        Capabilities(amd_gpu=AmdGpuCapability(present=True, fan_write_supported=True))
    )
    assert page._dedicate_gpu_btn.isHidden()

    # Present + writable + zero-RPM capable → shown.
    page._on_capabilities_updated(
        Capabilities(
            amd_gpu=AmdGpuCapability(
                present=True, fan_write_supported=True, gpu_zero_rpm_available=True
            )
        )
    )
    assert not page._dedicate_gpu_btn.isHidden()


# ---------------------------------------------------------------------------
# Controls-page handler
# ---------------------------------------------------------------------------


def _install_fake_dialog(monkeypatch, *, result, accept=True, captured=None):
    class _FakeDialog:
        def __init__(self, **kwargs):
            if captured is not None:
                captured.update(kwargs)

        def exec(self):
            return accept

        def get_result(self):
            return result

    monkeypatch.setattr("control_ofc.ui.widgets.gpu_dedicate_dialog.GpuDedicateDialog", _FakeDialog)


def test_dedicate_gpu_handler_creates_control_and_marks_unsaved(
    qtbot, app_state, profile_service, monkeypatch
):
    from control_ofc.api.models import FanReading, SensorReading
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    profile = page._get_current_profile()
    before = len(profile.controls)

    app_state.fans = [FanReading(id=GPU_ID, source="amd_gpu")]
    app_state.sensors = [
        SensorReading(id="gpu_edge", kind="GpuTemp", label="edge", source="amd_gpu"),
    ]
    _install_fake_dialog(monkeypatch, result={"sensor_id": "gpu_edge", "zero_rpm": True})

    page._on_dedicate_gpu()

    assert len(profile.controls) == before + 1
    ctrl = next(c for c in profile.controls if c.name == "GPU Fan")
    assert ctrl.minimum_pct == 0.0
    assert ctrl.members[0].member_id == GPU_ID
    assert ctrl.members[0].fan_zero_rpm is True
    assert profile.get_curve(ctrl.curve_id).sensor_id == "gpu_edge"
    assert page._has_unsaved is True


def test_dedicate_gpu_handler_cancel_is_noop(qtbot, app_state, profile_service, monkeypatch):
    from control_ofc.api.models import FanReading, SensorReading
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    profile = page._get_current_profile()
    before = len(profile.controls)

    app_state.fans = [FanReading(id=GPU_ID, source="amd_gpu")]
    app_state.sensors = [
        SensorReading(id="gpu_edge", kind="GpuTemp", label="edge", source="amd_gpu")
    ]
    _install_fake_dialog(monkeypatch, result={}, accept=False)

    page._on_dedicate_gpu()

    assert len(profile.controls) == before  # cancelled → nothing created
    assert page._has_unsaved is False


def test_dedicate_gpu_handler_no_gpu_fan_is_noop(qtbot, app_state, profile_service, monkeypatch):
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    profile = page._get_current_profile()
    before = len(profile.controls)

    app_state.fans = []  # no writable GPU fan present
    # Sentinel: the fake records its kwargs on construction, so an empty dict
    # proves the handler early-returned BEFORE building the dialog.
    constructed: dict = {}
    _install_fake_dialog(
        monkeypatch, result={"sensor_id": "x", "zero_rpm": True}, captured=constructed
    )

    page._on_dedicate_gpu()
    assert len(profile.controls) == before
    assert constructed == {}  # dialog never constructed


def test_dedicate_gpu_handler_defaults_to_junction_when_no_edge(
    qtbot, app_state, profile_service, monkeypatch
):
    from control_ofc.api.models import FanReading, SensorReading
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    app_state.fans = [FanReading(id=GPU_ID, source="amd_gpu")]
    app_state.sensors = [
        SensorReading(id="cpu", kind="CpuTemp", label="CPU", source="hwmon"),
        SensorReading(id="gpu_junction", kind="GpuTemp", label="junction", source="amd_gpu"),
    ]
    captured: dict = {}
    _install_fake_dialog(monkeypatch, result={}, accept=False, captured=captured)

    page._on_dedicate_gpu()
    # No "edge" sensor present → the first GPU temp (junction) is the default.
    assert captured["default_sensor_id"] == "gpu_junction"


def test_dedicate_gpu_handler_empty_sensor_id_is_noop(
    qtbot, app_state, profile_service, monkeypatch
):
    # Belt-and-braces: even if a dialog returned an empty sensor_id (e.g. no
    # sensors), the handler must not build a sensorless GPU curve.
    from control_ofc.api.models import FanReading
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    profile = page._get_current_profile()
    before = len(profile.controls)

    app_state.fans = [FanReading(id=GPU_ID, source="amd_gpu")]
    app_state.sensors = []
    _install_fake_dialog(monkeypatch, result={"sensor_id": "", "zero_rpm": True})

    page._on_dedicate_gpu()
    assert len(profile.controls) == before  # nothing built
    assert page._has_unsaved is False


def test_dedicate_gpu_twice_leaves_single_gpu_control(
    qtbot, app_state, profile_service, monkeypatch
):
    from control_ofc.api.models import FanReading, SensorReading
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    app_state.fans = [FanReading(id=GPU_ID, source="amd_gpu")]
    app_state.sensors = [
        SensorReading(id="gpu_edge", kind="GpuTemp", label="edge", source="amd_gpu")
    ]
    _install_fake_dialog(monkeypatch, result={"sensor_id": "gpu_edge", "zero_rpm": True})

    page._on_dedicate_gpu()
    page._on_dedicate_gpu()  # re-dedicate must not accumulate controls

    profile = page._get_current_profile()
    drivers = [c for c in profile.controls if any(m.member_id == GPU_ID for m in c.members)]
    assert len(drivers) == 1  # single writer, no duplicate
    assert [c for c in profile.controls if c.name == "GPU Fan" and not c.members] == []


def test_dedicate_gpu_handler_filters_ineligible_and_prefers_gpu_edge(
    qtbot, app_state, profile_service, monkeypatch
):
    from control_ofc.api.models import FanReading, SensorReading
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)

    app_state.fans = [FanReading(id=GPU_ID, source="amd_gpu")]
    app_state.sensors = [
        SensorReading(id="cpu", kind="CpuTemp", label="CPU", source="hwmon"),
        SensorReading(id="gpu_junction", kind="GpuTemp", label="junction", source="amd_gpu"),
        SensorReading(id="gpu_edge", kind="GpuTemp", label="edge", source="amd_gpu"),
        # DEC-193: a WiFi-PHY temp must not be offered as a curve source.
        SensorReading(
            id="wifi", kind="CpuTemp", label="ath12k", source="hwmon", control_eligible=False
        ),
    ]
    captured: dict = {}
    _install_fake_dialog(monkeypatch, result={}, accept=False, captured=captured)

    page._on_dedicate_gpu()

    ids = {c["id"] for c in captured["sensor_choices"]}
    assert "wifi" not in ids  # ineligible sensor dropped
    assert {"cpu", "gpu_junction", "gpu_edge"} <= ids
    # GPU temps flagged preferred; the "edge" die temp wins the default.
    preferred = {c["id"] for c in captured["sensor_choices"] if c["preferred"]}
    assert preferred == {"gpu_junction", "gpu_edge"}
    assert captured["default_sensor_id"] == "gpu_edge"


# ---------------------------------------------------------------------------
# Part B — corrected zero-RPM popup copy (no longer contradicts fan_zero_rpm)
# ---------------------------------------------------------------------------


def test_gpu_zero_rpm_popup_copy_is_truthful(qtbot, app_state, profile_service, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from control_ofc.ui.pages.controls_page import ControlsPage

    captured: list[str] = []
    monkeypatch.setattr(QMessageBox, "setInformativeText", lambda self, text: captured.append(text))

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    page._show_gpu_zero_rpm_info()

    assert captured, "informative text should be set"
    text = captured[0]
    # The old copy falsely claimed the daemon ALWAYS disables zero-RPM — untrue
    # once a member opts in via fan_zero_rpm (DEC-221 dedicate flow).
    assert "automatically disables" not in text
    # Truthful now: default keeps spinning AND the popup points at how to enable
    # the idle stop. Require both halves so a copy rewrite can't drop one silently.
    assert "keeps spinning" in text
    assert "always responds" in text
    assert "Dedicate GPU Fan" in text
    assert "zero-rpm" in text.lower()
