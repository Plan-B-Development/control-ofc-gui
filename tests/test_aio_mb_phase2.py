"""AIO-MB Phase 2 (DEC-312) — a motherboard-connected AIO is configurable.

Covers role-aware discovery, the three pump strategies, the header-role client
method, the floor-truthfulness union, the wizard's pump-protection reconstruction
and the retraction of the "a pump must run at a constant speed" copy.
Outcome-focused; no real hardware.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from control_ofc.api.models import (
    AioHwmonCapability,
    Capabilities,
    ControlCapability,
    HwmonHeader,
    SensorReading,
    parse_header_role,
)
from control_ofc.services.controls_view import build_pump_role_candidates
from control_ofc.services.profile_service import (
    AIO_PUMP_STRATEGY_AUTOMATIC,
    AIO_PUMP_STRATEGY_CUSTOM,
    AIO_PUMP_STRATEGY_FIXED,
    ControlMember,
    CurveType,
    LogicalControl,
    Profile,
    apply_role_floor,
    build_aio_controls,
    detect_aio_setup,
    infer_member_role,
    role_tagged_member_label,
)

# The it8696 validation board: five channels, zero pwmN_label files, so the
# daemon synthesises "pwmN" for every one and infers `unknown` across the board.
_MB_CHIP = "it8696"


def _mb_header(index, *, role="unknown", role_source="none", label=None, is_writable=True):
    """A motherboard header as the daemon reports it on a label-less board."""
    return HwmonHeader(
        id=f"hwmon:{_MB_CHIP}:it87.2624:pwm{index}:{label or f'pwm{index}'}",
        label=label or f"pwm{index}",
        chip_name=_MB_CHIP,
        pwm_index=index,
        is_writable=is_writable,
        is_aio=False,
        role=role,
        role_source=role_source,
    )


def _kraken_header(index, label, *, is_writable=True, role="unknown"):
    return HwmonHeader(
        id=f"hwmon:z53:d:pwm{index}:{label}",
        label=label,
        chip_name="z53",
        pwm_index=index,
        is_writable=is_writable,
        is_aio=True,
        role=role,
    )


def _sensor(sid, kind, label, chip="coretemp"):
    return SensorReading(id=sid, kind=kind, label=label, chip_name=chip)


# ---------------------------------------------------------------------------
# Discovery (item B)
# ---------------------------------------------------------------------------


class TestRoleAwareDiscovery:
    def test_assigned_pump_on_a_motherboard_header_is_found(self):
        """The headline case: five identical unknown channels, one assigned."""
        headers = [_mb_header(i) for i in (1, 2, 3, 4)]
        headers.append(_mb_header(5, role="pump", role_source="user_assigned"))
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member is not None
        assert det.pump_member.member_id.endswith(":pwm5:pwm5")

    def test_no_coolant_sensor_falls_back_to_cpu_package(self):
        """A motherboard AIO reports no coolant temperature — that is normal."""
        headers = [_mb_header(5, role="pump")]
        sensors = [
            _sensor("cpu:core0", "cpu_temp", "Core 0"),
            _sensor("cpu:pkg", "cpu_temp", "Package id 0"),
        ]
        det = detect_aio_setup(headers, sensors, {})
        assert det.coolant_sensor_id is None
        assert det.has_coolant is False
        # Package, not Core 0: a single core spikes and would make the pump chase
        # noise. Nothing about this is an error state.
        assert det.control_sensor_id == "cpu:pkg"
        assert det.monitor_only is False

    def test_coolant_still_wins_when_present(self):
        headers = [_kraken_header(1, "Pump")]
        sensors = [
            _sensor("cpu:pkg", "cpu_temp", "Package id 0"),
            _sensor("z53:coolant", "coolant_temp", "Coolant", chip="z53"),
        ]
        det = detect_aio_setup(headers, sensors, {})
        assert det.control_sensor_id == "z53:coolant"
        assert det.has_coolant is True

    def test_ambiguous_cpu_opt_header_is_never_guessed(self):
        """CPU_OPT stays `unknown` daemon-side because it is genuinely ambiguous
        (pump, or second radiator fan). The GUI mirrors that refusal."""
        headers = [_mb_header(2, label="CPU_OPT")]
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member is None

    def test_kraken_discovery_is_unchanged(self):
        """The pre-existing chip-level route must still work byte-for-byte."""
        headers = [
            _kraken_header(1, "Pump"),
            _kraken_header(2, "Fan 1"),
        ]
        sensors = [_sensor("z53:coolant", "coolant_temp", "Coolant", chip="z53")]
        det = detect_aio_setup(headers, sensors, {})
        assert det.pump_member.member_label == "Pump"
        assert [m.member_label for m in det.radiator_members] == ["Fan 1"]
        assert det.coolant_sensor_id == "z53:coolant"

    def test_kraken_lowest_index_fallback_survives(self):
        headers = [_kraken_header(2, "Fan 1"), _kraken_header(1, "Fan 0")]
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member.member_id.endswith(":pwm1:Fan 0")

    def test_read_only_assigned_pump_is_not_offered(self):
        headers = [_mb_header(5, role="pump", is_writable=False)]
        det = detect_aio_setup(headers, [], {})
        assert det.pump_member is None

    def test_radiator_roles_preselect_but_grant_nothing(self):
        headers = [
            _mb_header(5, role="pump"),
            _mb_header(1, role="radiator_fan"),
            _mb_header(2, role="cpu_fan"),
            _mb_header(3),  # unknown — offered by the picker, not preselected here
        ]
        det = detect_aio_setup(headers, [], {})
        ids = {m.member_id for m in det.radiator_members}
        assert any(i.endswith(":pwm1:pwm1") for i in ids)
        assert any(i.endswith(":pwm2:pwm2") for i in ids)
        assert not any(i.endswith(":pwm3:pwm3") for i in ids)

    def test_monitor_only_keeps_its_exact_meaning(self):
        """A real cooler with no writable header — NOT "nothing assigned yet"."""
        headers = [_kraken_header(1, "Pump", is_writable=False)]
        det = detect_aio_setup(headers, [], {})
        assert det.monitor_only is True

        # A bare motherboard with nothing assigned is an unfinished setup, which
        # the dialog answers with the role step rather than a dead end.
        det2 = detect_aio_setup([_mb_header(1)], [], {})
        assert det2.monitor_only is False
        assert det2.pump_member is None


# ---------------------------------------------------------------------------
# Floor truthfulness (item D)
# ---------------------------------------------------------------------------


class TestFloorTruthfulness:
    def test_assigned_pump_on_a_labelless_header_gets_30(self):
        """The UI lie this fixes: the daemon floors it at 30 regardless, via
        `assigned_role_is_pump`; before this the GUI stamped and displayed 20."""
        headers = [_mb_header(5, role="pump", role_source="user_assigned")]
        det = detect_aio_setup(headers, [], {})
        ctl = LogicalControl(id="c", name="AIO Pump", members=[det.pump_member])
        apply_role_floor(ctl)
        assert ctl.minimum_pct == 30.0

    def test_assigned_pump_with_a_role_less_real_label_gets_30(self):
        """The case a synthesised label hides: a REAL label carrying no role word.

        `_real_header_label` returns "SYS_FAN5" here rather than falling back to
        "Pump", so before DEC-312 this member classified as chassis at 20%.
        """
        headers = [_mb_header(5, role="pump", label="SYS_FAN5")]
        det = detect_aio_setup(headers, [], {})
        assert infer_member_role(det.pump_member) == "cpu_or_pump"
        ctl = LogicalControl(id="c", name="AIO Pump", members=[det.pump_member])
        apply_role_floor(ctl)
        assert ctl.minimum_pct == 30.0

    def test_role_tag_is_union_only_and_never_lowers(self):
        # A downgrade assignment on a hardware-labelled PUMP header must not strip
        # the floor the label already earned.
        assert role_tagged_member_label("PUMP_2", "chassis_fan") == "PUMP_2"
        assert (
            infer_member_role(
                ControlMember(
                    source="hwmon", member_id="hwmon:x:d:pwm3:PUMP_2", member_label="PUMP_2"
                )
            )
            == "cpu_or_pump"
        )

    def test_radiator_role_is_not_tagged(self):
        """`radiator_fan` is behaviourally inert daemon-side — tagging it would
        imply a guarantee that does not exist."""
        assert role_tagged_member_label("SYS_FAN1", "radiator_fan") == "SYS_FAN1"

    def test_clearing_a_role_does_not_drop_a_saved_profile_floor(self):
        """Deliberate asymmetry, and the safe direction (DEC-312).

        Once the tag is persisted, the member classifies on its own label; the
        live header's role is not consulted again. Clearing an assignment is not a
        request to lower a floor.
        """
        headers = [_mb_header(5, role="pump", label="SYS_FAN5")]
        member = detect_aio_setup(headers, [], {}).pump_member
        # The profile is saved; later the user clears the role. The persisted
        # member is unchanged, and so is its floor.
        ctl = LogicalControl(id="c", name="AIO Pump", members=[member])
        apply_role_floor(ctl)
        assert ctl.minimum_pct == 30.0
        assert infer_member_role(member) == "cpu_or_pump"


# ---------------------------------------------------------------------------
# Pump strategies (item C)
# ---------------------------------------------------------------------------


class TestPumpStrategies:
    def _members(self):
        headers = [_mb_header(5, role="pump")]
        return detect_aio_setup(headers, [], {}).pump_member

    @pytest.mark.parametrize(
        "strategy,expected_type",
        [
            (AIO_PUMP_STRATEGY_AUTOMATIC, CurveType.GRAPH),
            (AIO_PUMP_STRATEGY_FIXED, CurveType.FLAT),
            (AIO_PUMP_STRATEGY_CUSTOM, CurveType.GRAPH),
        ],
    )
    def test_each_strategy_builds_a_valid_pump_control(self, strategy, expected_type):
        profile = Profile(name="p")
        created = build_aio_controls(
            profile,
            pump_member=self._members(),
            pump_pct=80,
            radiator_members=[],
            radiator_sensor_id="cpu:pkg",
            pump_strategy=strategy,
        )
        assert len(created) == 1
        pump = created[0]
        assert pump.minimum_pct == 30.0
        curve = next(c for c in profile.curves if c.id == pump.curve_id)
        assert curve.type is expected_type

    def test_automatic_seed_never_dips_below_the_pump_floor(self):
        """Safe BY CONSTRUCTION, not by relying on the minimum_pct clamp — a seed
        that needed the clamp would look unsafe in the editor and would author an
        unsafe profile if the floor ever moved."""
        profile = Profile(name="p")
        build_aio_controls(
            profile,
            pump_member=self._members(),
            pump_pct=0,
            radiator_members=[],
            radiator_sensor_id="cpu:pkg",
            pump_strategy=AIO_PUMP_STRATEGY_AUTOMATIC,
        )
        curve = profile.curves[0]
        assert curve.points, "the automatic strategy must seed real points"
        assert min(p.output_pct for p in curve.points) >= 30.0

    def test_automatic_binds_to_the_control_sensor(self):
        profile = Profile(name="p")
        build_aio_controls(
            profile,
            pump_member=self._members(),
            pump_pct=0,
            radiator_members=[],
            radiator_sensor_id="cpu:pkg",
            pump_strategy=AIO_PUMP_STRATEGY_AUTOMATIC,
        )
        assert profile.curves[0].sensor_id == "cpu:pkg"

    def test_fixed_path_is_unchanged(self):
        """The default before this change, and it must still build byte-identically."""
        profile = Profile(name="p")
        build_aio_controls(
            profile,
            pump_member=self._members(),
            pump_pct=80,
            radiator_members=[],
            radiator_sensor_id="cpu:pkg",
            pump_strategy=AIO_PUMP_STRATEGY_FIXED,
        )
        curve = profile.curves[0]
        assert curve.type is CurveType.FLAT
        assert curve.flat_output_pct == 80.0


# ---------------------------------------------------------------------------
# Header-role client method (item A)
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Captures the POST body without touching a socket."""

    def __init__(self, response=None, raise_exc=None):
        self.calls: list[tuple[str, dict]] = []
        self._response = response or {}
        self._raise = raise_exc

    def _post(self, path, json=None, **kw):
        self.calls.append((path, json))
        if self._raise is not None:
            raise self._raise
        return self._response


class TestSetHeaderRole:
    def _client(self, **kw):
        from control_ofc.api.client import DaemonClient

        c = DaemonClient.__new__(DaemonClient)
        rec = _RecordingClient(**kw)
        c._post = rec._post
        return c, rec

    def test_assign_sends_the_exact_token(self):
        c, rec = self._client(response={"updated": True, "role": "pump"})
        c.set_header_role("hwmon:it8696:x:pwm5:pwm5", "pump")
        path, body = rec.calls[0]
        assert path == "/config/header-role"
        assert body == {"header_id": "hwmon:it8696:x:pwm5:pwm5", "role": "pump"}

    def test_clear_sends_an_explicit_null_role(self):
        """The daemon requires the KEY to be present and treats a missing one as a
        400, distinct from a null — so the usual "drop the Nones" payload idiom
        would make clearing structurally impossible."""
        c, rec = self._client(response={"updated": True, "role": None})
        c.set_header_role("hwmon:it8696:x:pwm5:pwm5", None)
        _path, body = rec.calls[0]
        assert "role" in body
        assert body["role"] is None

    def test_token_case_is_not_normalised(self):
        """An unrecognised token must reach the daemon and surface its 400, never
        be silently coerced into something weaker."""
        c, rec = self._client(response={"updated": True})
        c.set_header_role("h", "PUMP")
        assert rec.calls[0][1]["role"] == "PUMP"

    def test_daemon_error_propagates(self):
        from control_ofc.api.errors import DaemonError

        c, _rec = self._client(
            raise_exc=DaemonError(code="validation_error", message="unknown role 'pmup'")
        )
        with pytest.raises(DaemonError):
            c.set_header_role("h", "pmup")

    def test_parse_reads_effective_role_and_keeps_a_cleared_role_none(self):
        res = parse_header_role(
            {"updated": True, "header_id": "h", "role": None, "effective_role": "cpu_fan"}
        )
        assert res.updated is True
        assert res.role is None
        assert res.effective_role == "cpu_fan"

    def test_parse_renders_an_unrecognised_effective_role(self):
        """273-i: an opaque token from a newer daemon is rendered, never dropped."""
        res = parse_header_role({"effective_role": "vrm_fan"})
        assert res.effective_role == "vrm_fan"


# ---------------------------------------------------------------------------
# Pump picker rows
# ---------------------------------------------------------------------------


class TestPumpRoleCandidates:
    def test_every_writable_header_is_offered(self):
        headers = [_mb_header(i) for i in (1, 2, 3)]
        headers.append(_mb_header(4, is_writable=False))
        rows = build_pump_role_candidates(headers, display_name=lambda i: f"Fan {i[-1]}")
        assert len(rows) == 3

    def test_unrecognised_role_token_is_rendered(self):
        rows = build_pump_role_candidates(
            [_mb_header(1, role="vrm_fan")], display_name=lambda i: "Fan"
        )
        assert "vrm_fan" in rows[0]["label"]

    def test_unknown_role_is_not_spelled_out(self):
        rows = build_pump_role_candidates([_mb_header(1)], display_name=lambda i: "Fan")
        assert rows[0]["label"] == "Fan"


# ---------------------------------------------------------------------------
# Copy retraction guard
# ---------------------------------------------------------------------------

# Assertive claims only. The hedged framing the dialog now uses ("Some pumps are
# designed to run at a constant speed; others are designed to be controlled
# automatically") is the correct copy and must NOT trip this.
_FORBIDDEN = (
    r"pumps?\s+runs?\s+best\s+at\s+a\s+constant",
    r"pumps?\s+cools?\s+best\s+at\s+a\s+steady",
    r"never\s+a\s+temperature\s+curve",
    r"not\s+a\s+temperature\s+curve",
    r"the\s+pump\s+runs\s+at\s+a\s+constant\s+speed",
    r"must\s+run\s+at\s+a\s+constant",
)

_COPY_FILES = (
    "src/control_ofc/ui/widgets/aio_config_dialog.py",
    "src/control_ofc/ui/pages/controls_page.py",
    "src/control_ofc/services/profile_service.py",
    "src/control_ofc/ui/widgets/fan_wizard.py",
)


def _user_visible_strings(path: pathlib.Path):
    """Every string literal that is not a docstring.

    Docstrings are excluded deliberately: a docstring explaining WHY the claim was
    retracted is correct and must not trip the guard. Only strings that can reach
    a widget are user-visible.
    """
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def test_no_user_visible_string_asserts_a_pump_must_be_constant_speed():
    """DEC-312 retracted "a pump runs best at a constant speed" as a stated fact.

    It is a property of the cooler — vendors disagree with each other about their
    own hardware — so the GUI offers all three strategies and asserts none.
    """
    offenders = []
    for rel in _COPY_FILES:
        path = pathlib.Path(rel)
        for literal in _user_visible_strings(path):
            for pattern in _FORBIDDEN:
                if re.search(pattern, literal, re.IGNORECASE):
                    offenders.append(f"{rel}: {literal[:80]!r}")
    assert not offenders, "user-visible copy still asserts a constant-speed pump:\n" + "\n".join(
        offenders
    )


def test_the_guard_would_catch_the_retracted_copy():
    """Proves the guard above is not vacuous — the exact string it replaced trips it."""
    retracted = "Pumps run best at a constant speed, not a temperature curve."
    assert any(re.search(p, retracted, re.IGNORECASE) for p in _FORBIDDEN)


# ---------------------------------------------------------------------------
# Menu visibility (item B)
# ---------------------------------------------------------------------------


def test_configure_aio_visible_when_header_roles_supported(qtbot, app_state, profile_service):
    """A motherboard AIO is undetectable by construction, so a detection-gated
    entry point would hide the feature from exactly the users who need it."""
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)

    app_state.capabilities = Capabilities()
    page._on_capabilities_updated(app_state.capabilities)
    assert page._configure_aio_action.isVisible() is False

    app_state.capabilities = Capabilities(control=ControlCapability(header_roles=True))
    page._on_capabilities_updated(app_state.capabilities)
    assert page._configure_aio_action.isVisible() is True


def test_configure_aio_still_visible_for_a_usb_cooler_without_header_roles(
    qtbot, app_state, profile_service
):
    """The pre-existing route must not regress against an older daemon."""
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    app_state.capabilities = Capabilities(
        aio_hwmon=AioHwmonCapability(present=True, status="supported")
    )
    page._on_capabilities_updated(app_state.capabilities)
    assert page._configure_aio_action.isVisible() is True


# ---------------------------------------------------------------------------
# Fan wizard pump protection (item E)
# ---------------------------------------------------------------------------


def _wizard(qtbot, app_state, headers, *, header_roles=True):
    from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

    app_state.hwmon_headers = headers
    app_state.capabilities = Capabilities(control=ControlCapability(header_roles=header_roles))
    w = FanConfigWizard(app_state)
    qtbot.addWidget(w)
    return w


class TestWizardPumpProtection:
    def test_downgraded_pump_header_still_reads_as_a_pump(self, qtbot, app_state):
        """The DEC-312 defect: `role` is the DISPLAY role and a user assignment
        fully substitutes for inference, downgrades included. The daemon's safety
        predicate is a UNION, so it still refuses to stop this header — and the
        wizard promising "the fan will stop" was a promise nothing would keep.
        """
        header = _mb_header(3, label="PUMP_2", role="chassis_fan", role_source="user_assigned")
        w = _wizard(qtbot, app_state, [header])
        assert w.is_pump_target(header.id) is True
        assert w.identify_verb(header.id) == "change speed"

    def test_assigned_pump_reads_as_a_pump(self, qtbot, app_state):
        header = _mb_header(5, role="pump", role_source="user_assigned")
        w = _wizard(qtbot, app_state, [header])
        assert w.is_pump_target(header.id) is True

    def test_liquid_cooler_channel_one_reads_as_a_pump(self, qtbot, app_state):
        header = _kraken_header(1, "pwm1")  # placeholder label, no role
        w = _wizard(qtbot, app_state, [header])
        assert w.is_pump_target(header.id) is True

    def test_a_user_alias_of_pump_does_not_promise_a_perturbation(self, qtbot, app_state):
        """The UNSAFE direction, and the reason the raw daemon label is read.

        A user alias the daemon cannot see would make the GUI promise a
        perturbation the daemon will not perform — it would stop the fan while the
        UI said it would only change speed.
        """
        header = _mb_header(2)  # synthesised "pwm2" label, role unknown
        app_state_alias = app_state
        app_state_alias.set_fan_alias(header.id, "Pump")
        w = _wizard(qtbot, app_state_alias, [header])
        assert w.is_pump_target(header.id) is False
        assert w.identify_verb(header.id) == "stop"

    def test_synthesised_placeholder_label_is_not_a_pump_hint(self, qtbot, app_state):
        header = _mb_header(1)  # label "pwm1" on pwm_index 1, is_aio False
        w = _wizard(qtbot, app_state, [header])
        assert w.is_pump_target(header.id) is False

    def test_older_daemon_keeps_the_stop_wording(self, qtbot, app_state):
        header = _mb_header(5, role="pump")
        w = _wizard(qtbot, app_state, [header], header_roles=False)
        assert w.is_pump_target(header.id) is False
        assert w.identify_verb(header.id) == "stop"

    def test_daemon_reported_mode_outranks_the_prediction(self, qtbot, app_state):
        """After the call the daemon's own answer is ground truth, so the message
        the user reads at the moment of action cannot drift from its predicate."""
        from control_ofc.api.models import IdentifyResult

        header = _mb_header(5, role="pump")
        w = _wizard(qtbot, app_state, [header])

        class _Client:
            def fan_identify(self, fan_id, action, **kw):
                return IdentifyResult(fan_id=fan_id, action=action, mode="stop")

        w._client = _Client()
        assert w.is_pump_target(header.id) is True  # prediction
        w.stop_fan({"id": header.id})
        assert w.last_identify_perturbed_pump(header.id) is False  # ground truth wins

    def test_prediction_is_used_when_the_daemon_reports_no_mode(self, qtbot, app_state):
        from control_ofc.api.models import IdentifyResult

        header = _mb_header(5, role="pump")
        w = _wizard(qtbot, app_state, [header])

        class _Client:
            def fan_identify(self, fan_id, action, **kw):
                return IdentifyResult(fan_id=fan_id, action=action, mode=None)

        w._client = _Client()
        w.stop_fan({"id": header.id})
        assert w.last_identify_perturbed_pump(header.id) is True


# ---------------------------------------------------------------------------
# Flat curve floor (item F)
# ---------------------------------------------------------------------------


class TestFlatCurveFloor:
    def test_flat_pump_output_cannot_be_authored_below_the_floor(self, qtbot):
        from control_ofc.services.profile_service import CurveConfig
        from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

        curve = CurveConfig(name="AIO Pump", type=CurveType.FLAT, flat_output_pct=80.0)
        dlg = CurveEditDialog(curve, [], min_output=30.0)
        qtbot.addWidget(dlg)
        assert dlg._flat_spin.minimum() == 30.0

    def test_a_legacy_value_below_the_floor_is_raised_not_shown(self, qtbot):
        """The daemon enforces 30 at eval time regardless, so showing 10 would
        display a value that was never going to be honoured."""
        from control_ofc.services.profile_service import CurveConfig
        from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

        curve = CurveConfig(name="AIO Pump", type=CurveType.FLAT, flat_output_pct=10.0)
        dlg = CurveEditDialog(curve, [], min_output=30.0)
        qtbot.addWidget(dlg)
        assert dlg._flat_spin.value() == 30.0

    def test_no_floor_leaves_the_full_range(self, qtbot):
        from control_ofc.services.profile_service import CurveConfig
        from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog

        curve = CurveConfig(name="Case", type=CurveType.FLAT, flat_output_pct=40.0)
        dlg = CurveEditDialog(curve, [])
        qtbot.addWidget(dlg)
        assert dlg._flat_spin.minimum() == 0.0


# ---------------------------------------------------------------------------
# Dialog behaviour (items A + C)
# ---------------------------------------------------------------------------


def _dialog(qtbot, **kw):
    from control_ofc.ui.widgets.aio_config_dialog import AioConfigDialog

    defaults = dict(
        pump_label=None,
        monitor_only=False,
        fan_candidates=[],
        sensor_choices=[{"id": "cpu:pkg", "label": "Package id 0", "preferred": True}],
        default_sensor_id="cpu:pkg",
    )
    defaults.update(kw)
    dlg = AioConfigDialog(**defaults)
    qtbot.addWidget(dlg)
    return dlg


class TestDialog:
    def _candidates(self):
        return [
            {"id": "h1", "label": "Fan 1", "role": "unknown", "role_source": "none"},
            {"id": "h5", "label": "Fan 5", "role": "unknown", "role_source": "none"},
        ]

    def test_picking_a_pump_produces_an_assignment(self, qtbot):
        dlg = _dialog(qtbot, pump_candidates=self._candidates())
        dlg._pump_combo.setCurrentIndex(dlg._pump_combo.findData("h5"))
        res = dlg.get_result()
        assert res["role_assignments"] == [("h5", "pump")]
        assert res["pump_member_id"] == "h5"

    def test_accepting_unchanged_writes_nothing(self, qtbot):
        cands = self._candidates()
        cands[1]["role"] = "pump"
        cands[1]["role_source"] = "user_assigned"
        dlg = _dialog(qtbot, pump_candidates=cands, detected_pump_id="h5")
        assert dlg.get_result()["role_assignments"] == []

    def test_moving_the_pump_assigns_before_it_clears(self, qtbot):
        """ORDER IS THE SAFETY PROPERTY, so it is asserted literally.

        This assertion used to call `sorted()`, which made it pass under either
        order — including the unsafe one, where a clear lands first and a failed
        assign then leaves the old pump stripped of its role with nothing put in
        its place. The candidate list here is deliberately ordered so that the
        header being CLEARED (`h5`) sorts *after* the one being assigned (`h1`)
        only if the code orders it that way, not because the input did.
        """
        cands = self._candidates()  # [h1, h5] in header order
        cands[1]["role"] = "pump"  # h5 is the current user-assigned pump
        cands[1]["role_source"] = "user_assigned"
        dlg = _dialog(qtbot, pump_candidates=cands, detected_pump_id="h5")
        dlg._pump_combo.setCurrentIndex(dlg._pump_combo.findData("h1"))
        assert dlg.get_result()["role_assignments"] == [("h1", "pump"), ("h5", None)]

    def test_assign_precedes_clear_even_when_the_old_pump_sorts_first(self, qtbot):
        """The case that motivated the fix: the cleared header comes FIRST in
        header order, so iteration order alone would emit the clear first."""
        cands = [
            {"id": "h1", "label": "Fan 1", "role": "pump", "role_source": "user_assigned"},
            {"id": "h5", "label": "Fan 5", "role": "unknown", "role_source": "none"},
        ]
        dlg = _dialog(qtbot, pump_candidates=cands, detected_pump_id="h1")
        dlg._pump_combo.setCurrentIndex(dlg._pump_combo.findData("h5"))
        assignments = dlg.get_result()["role_assignments"]
        assert assignments == [("h5", "pump"), ("h1", None)]
        # Stated as the invariant, not just this instance: no clear may precede
        # any assign, or a failed assign leaves a header stripped for nothing.
        roles = [role for _id, role in assignments]
        assert roles.index("pump") < roles.index(None)

    def test_an_inferred_pump_role_is_never_cleared(self, qtbot):
        """A role the daemon inferred from the hardware is not ours to remove —
        and clearing it would not remove it anyway, since a clear only drops the
        stored assignment and falls back to exactly that inference."""
        cands = self._candidates()
        cands[1]["role"] = "pump"
        cands[1]["role_source"] = "label"
        dlg = _dialog(qtbot, pump_candidates=cands, detected_pump_id="h5")
        dlg._pump_combo.setCurrentIndex(dlg._pump_combo.findData("h1"))
        assert dlg.get_result()["role_assignments"] == [("h1", "pump")]

    def test_automatic_is_the_default_strategy(self, qtbot):
        dlg = _dialog(qtbot, pump_label="Pump", detected_pump_id="h5")
        res = dlg.get_result()
        assert res["pump_strategy"] == AIO_PUMP_STRATEGY_AUTOMATIC
        assert res["pump_pct"] is None

    def test_fixed_strategy_returns_the_preset(self, qtbot):
        dlg = _dialog(qtbot, pump_label="Pump", detected_pump_id="h5")
        for btn in dlg._strategy_buttons.buttons():
            if btn.property("strategy") == AIO_PUMP_STRATEGY_FIXED:
                btn.setChecked(True)
        res = dlg.get_result()
        assert res["pump_strategy"] == AIO_PUMP_STRATEGY_FIXED
        assert res["pump_pct"] == 80

    def test_no_pump_selected_yields_no_strategy(self, qtbot):
        dlg = _dialog(qtbot, pump_candidates=self._candidates())
        res = dlg.get_result()
        assert res["pump_strategy"] is None
        assert res["pump_member_id"] is None

    def test_strategy_controls_follow_the_pump_selection(self, qtbot):
        dlg = _dialog(qtbot, pump_candidates=self._candidates())
        assert dlg._strategy_box.isEnabled() is False
        dlg._pump_combo.setCurrentIndex(dlg._pump_combo.findData("h5"))
        assert dlg._strategy_box.isEnabled() is True

    def test_missing_coolant_is_explained_not_flagged(self, qtbot):
        dlg = _dialog(qtbot, has_coolant=False)
        note = dlg.findChild(type(dlg._pump_hint), "AioConfig_Label_sensorNote")
        assert "normal" in note.text().lower()
        assert "coolant temperature is recommended" not in note.text().lower()

    def test_no_role_picker_without_the_capability(self, qtbot):
        dlg = _dialog(qtbot, pump_label="Pump", pump_candidates=[])
        assert dlg._pump_combo is None
        assert dlg.get_result()["role_assignments"] == []


# ---------------------------------------------------------------------------
# Header re-fetch and failure handling (item A)
# ---------------------------------------------------------------------------


class _RoleClient:
    def __init__(self, *, fail_on=None):
        self.role_calls: list[tuple[str, str | None]] = []
        self.header_fetches = 0
        self._fail_on = fail_on
        self.headers_to_return: list = []

    def set_header_role(self, header_id, role):
        self.role_calls.append((header_id, role))
        if self._fail_on is not None and role == self._fail_on:
            from control_ofc.api.errors import DaemonError

            raise DaemonError(code="persistence_failed", message="read-only filesystem")
        return parse_header_role({"updated": True, "header_id": header_id, "role": role})

    def hwmon_headers(self):
        self.header_fetches += 1
        return self.headers_to_return


class TestApplyHeaderRoles:
    def _page(self, qtbot, app_state, profile_service, client):
        from control_ofc.ui.pages.controls_page import ControlsPage

        page = ControlsPage(state=app_state, profile_service=profile_service)
        qtbot.addWidget(page)
        page._client = client
        return page

    def test_headers_are_refetched_immediately(self, qtbot, app_state, profile_service):
        """Headers otherwise refresh every 300 s — long enough that the role the
        user just set would be invisible to the rest of the flow."""
        client = _RoleClient()
        assigned = _mb_header(5, role="pump", role_source="user_assigned")
        client.headers_to_return = [assigned]
        page = self._page(qtbot, app_state, profile_service, client)

        assert page._apply_header_roles([("h5", "pump")]) is True
        assert client.role_calls == [("h5", "pump")]
        assert client.header_fetches == 1
        assert app_state.hwmon_headers == [assigned]

    def test_nothing_is_written_or_refetched_when_there_is_no_change(
        self, qtbot, app_state, profile_service
    ):
        client = _RoleClient()
        page = self._page(qtbot, app_state, profile_service, client)
        assert page._apply_header_roles([]) is True
        assert client.role_calls == []
        assert client.header_fetches == 0

    def test_a_failed_assignment_aborts(self, qtbot, app_state, profile_service, monkeypatch):
        """Without the assignment the daemon does not know the header drives a
        pump, so it would neither floor it at 30% nor refuse to stop it — creating
        a profile that claims otherwise would be a lie about hardware safety."""
        import control_ofc.ui.pages.controls_page as cp

        warned = {}
        monkeypatch.setattr(
            cp.QMessageBox, "warning", lambda *a, **k: warned.setdefault("shown", True)
        )
        client = _RoleClient(fail_on="pump")
        page = self._page(qtbot, app_state, profile_service, client)

        assert page._apply_header_roles([("h5", "pump")]) is False
        assert warned.get("shown") is True
        assert client.header_fetches == 0

    def test_a_failed_clear_is_tolerated(self, qtbot, app_state, profile_service):
        """A stale assignment only ever adds a floor, so it is not worth aborting."""
        client = _RoleClient(fail_on=None)

        def _fail_clear(header_id, role):
            client.role_calls.append((header_id, role))
            if role is None:
                from control_ofc.api.errors import DaemonError

                raise DaemonError(code="internal_error", message="nope")
            return parse_header_role({"updated": True})

        client.set_header_role = _fail_clear
        page = self._page(qtbot, app_state, profile_service, client)
        assert page._apply_header_roles([("old", None), ("h5", "pump")]) is True
        assert client.header_fetches == 1


# ---------------------------------------------------------------------------
# Remediation coverage (DEC-312 review round 1)
# ---------------------------------------------------------------------------


class TestSeedCurveCalibration:
    """A coolant curve and a CPU curve are not interchangeable.

    CPU package runs 20-30 C above coolant for the same thermal state, so the
    coolant-calibrated radiator seed (100% at 55 C) would have pinned the fans at
    maximum during ordinary desktop use on exactly the motherboard AIO this
    feature exists for.
    """

    def _pump(self):
        return detect_aio_setup([_mb_header(5, role="pump")], [], {}).pump_member

    def _build(self, *, coolant):
        profile = Profile(name="p")
        build_aio_controls(
            profile,
            pump_member=self._pump(),
            pump_pct=0,
            radiator_members=[
                ControlMember(source="hwmon", member_id="r1", member_label="Radiator")
            ],
            radiator_sensor_id="s",
            pump_strategy=AIO_PUMP_STRATEGY_AUTOMATIC,
            sensor_is_coolant=coolant,
        )
        by_name = {c.name: c for c in profile.curves}
        return by_name["AIO Pump"], by_name["AIO Radiator"]

    def test_cpu_bound_curves_do_not_saturate_at_desktop_temperatures(self):
        _pump, rad = self._build(coolant=False)
        # 55 C is an ordinary browsing temperature for a desktop CPU; the coolant
        # calibration reached 100% there.
        at_55 = next(p.output_pct for p in rad.points if p.temp_c == 55.0)
        assert at_55 < 50.0
        assert max(p.temp_c for p in rad.points) >= 85.0

    def test_coolant_bound_curves_are_unchanged(self):
        """The pre-existing calibration must not move for a USB cooler."""
        _pump, rad = self._build(coolant=True)
        assert [(p.temp_c, p.output_pct) for p in rad.points] == [
            (30.0, 20.0),
            (40.0, 40.0),
            (50.0, 75.0),
            (55.0, 100.0),
        ]

    def test_both_pump_calibrations_respect_the_floor(self):
        for coolant in (True, False):
            pump, _rad = self._build(coolant=coolant)
            assert min(p.output_pct for p in pump.points) >= 30.0, coolant

    def test_the_two_pump_calibrations_actually_differ(self):
        """Guards against the flag being wired to the same constant twice."""
        cpu_pump, _ = self._build(coolant=False)
        coolant_pump, _ = self._build(coolant=True)
        assert [p.temp_c for p in cpu_pump.points] != [p.temp_c for p in coolant_pump.points]


class TestIdentifyModeIsKeyedByFan:
    def test_a_stale_mode_from_another_fan_is_not_reported(self, qtbot, app_state):
        """`last_identify_perturbed_pump` takes a fan_id, so it must answer for
        THAT fan — a single wizard-wide mode made the parameter decoration."""
        from control_ofc.api.models import IdentifyResult

        pump = _mb_header(5, role="pump")
        plain = _mb_header(2)
        w = _wizard(qtbot, app_state, [pump, plain])

        class _Client:
            def fan_identify(self, fan_id, action, **kw):
                return IdentifyResult(fan_id=fan_id, action=action, mode="pump_perturb")

        w._client = _Client()
        w.stop_fan({"id": pump.id})
        # Asking about a DIFFERENT fan must fall back to that fan's own
        # prediction, not report the pump's perturbation.
        assert w.last_identify_perturbed_pump(plain.id) is False
        assert w.last_identify_perturbed_pump(pump.id) is True

    def test_a_failed_take_does_not_leave_a_stale_mode(self, qtbot, app_state):
        from control_ofc.api.errors import DaemonError
        from control_ofc.api.models import IdentifyResult

        pump = _mb_header(5, role="pump")
        w = _wizard(qtbot, app_state, [pump])

        calls = {"n": 0}

        class _Client:
            def fan_identify(self, fan_id, action, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    return IdentifyResult(fan_id=fan_id, action=action, mode="stop")
                raise DaemonError(code="hardware_unavailable", message="gone")

        w._client = _Client()
        w.stop_fan({"id": pump.id})
        assert w.last_identify_perturbed_pump(pump.id) is False  # daemon said stop
        assert w.stop_fan({"id": pump.id}) is not None  # second take fails
        # The stale "stop" must not survive the failure and contradict the
        # prediction for a header the daemon does protect.
        assert w.last_identify_perturbed_pump(pump.id) is True


class TestLabelOutranksChipMapping:
    """The daemon consults the label BEFORE chip mapping, so a cooler channel 1
    carrying a recognised non-pump label infers `cpu_fan` and IS stopped."""

    def test_cooler_channel_one_labelled_cpu_fan_is_not_promised_a_perturbation(
        self, qtbot, app_state
    ):
        header = _kraken_header(1, "CPU_FAN1")
        w = _wizard(qtbot, app_state, [header])
        assert w.is_pump_target(header.id) is False
        assert w.identify_verb(header.id) == "stop"

    def test_cooler_channel_one_labelled_chassis_is_not_a_pump(self, qtbot, app_state):
        header = _kraken_header(1, "SYS_FAN1")
        w = _wizard(qtbot, app_state, [header])
        assert w.is_pump_target(header.id) is False

    def test_a_pump_label_still_wins_over_a_chassis_hint(self, qtbot, app_state):
        """The daemon tests the pump substring FIRST, so `SYS_FAN5_PUMP` is a pump."""
        header = _mb_header(5, label="SYS_FAN5_PUMP")
        w = _wizard(qtbot, app_state, [header])
        assert w.is_pump_target(header.id) is True

    def test_unlabelled_cooler_channel_one_is_still_a_pump(self, qtbot, app_state):
        """The chip-mapping fallback must survive — it is the Kraken case."""
        header = _kraken_header(1, "pwm1")
        w = _wizard(qtbot, app_state, [header])
        assert w.is_pump_target(header.id) is True


def test_user_assigned_pump_outranks_an_inferred_one_for_selection():
    """Both are floored at 30% either way; this is about preselecting what the
    user actually chose rather than whichever header sorts first."""
    inferred = _mb_header(1, label="PUMP_2", role="pump", role_source="label")
    assigned = _mb_header(5, role="pump", role_source="user_assigned")
    det = detect_aio_setup([inferred, assigned], [], {})
    assert det.pump_member.member_id.endswith(":pwm5:pwm5")
