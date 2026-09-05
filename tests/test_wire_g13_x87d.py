"""Wave-3 `G13` + `X87-d`: the daemon says more, and the GUI reads it.

Five rows, one contract window:

* ``WIRE-k`` — five features that shipped before ``GET /capabilities`` had keys
  for them, so the GUI detected them by comparing a daemon **version string** or
  by reading a ``404`` off the route.
* ``WIRE-n`` — no wire field said the engine was *evaluating but not applying*,
  so the Controls cards painted "Applied" over a duty nothing was writing.
* ``WIRE-ac`` — three numbers claimed to be the GUI pairing floor.
* ``WIRE-ao`` — daemon-side only (a failed reload overwriting a startup
  degradation); pinned in the daemon's own suite, nothing to assert here.
* ``X87-d`` — the board's firmware-declared header count, so a deficit is a
  measurement rather than an inference from a curated DMI table.

Every assertion here is on a **call site**. The parsers and the tri-state helper
have their own unit coverage below, but a rule that is only unit-tested is a rule
with no consumer — ``CLAUDE.md``'s most-repeated failure in this project.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    BoardFirmwareCounts,
    Capabilities,
    ControlCapability,
    HardwareDiagnosticsResult,
    HwmonDiagnostics,
    parse_capabilities,
    parse_hardware_diagnostics,
    parse_status,
)
from control_ofc.services.daemon_features import (
    DAEMON_FEATURE_CAPABILITY_FLAGS,
    DAEMON_FEATURE_LABELS,
    DAEMON_FEATURE_MINIMUMS,
    daemon_supports,
)

# The five `WIRE-k` ids, named once so every test below asks about the same set.
WIRE_K_FEATURES = (
    "gpu_fan_verify",
    "hardware_readiness",
    "superio_port_probe",
    "preferred_sensors",
    "daemon_config_report",
)


# ── WIRE-k: the capability contract ──────────────────────────────────────


def test_every_flag_maps_to_a_known_feature_id():
    """The three registries describe one vocabulary, so they cannot drift apart.

    Asserted as a relationship between the maps rather than as a literal list: a
    hardcoded expectation would need editing every time a feature is added, which
    is exactly how the registries came to disagree in the first place.
    """
    assert set(DAEMON_FEATURE_CAPABILITY_FLAGS) <= set(DAEMON_FEATURE_MINIMUMS)
    assert set(DAEMON_FEATURE_CAPABILITY_FLAGS) <= set(DAEMON_FEATURE_LABELS)


def test_every_mapped_flag_is_a_real_capability_field():
    """A typo in the map would fail SILENTLY and permanently.

    `daemon_supports` reads the flag with `getattr(control, flag, None)`, so a
    misspelled name yields `None` for every daemon forever — the feature would
    quietly stay on its fallback and no test that only exercised the fallback
    would notice. The name is the whole contract with the daemon here, so it is
    asserted against the dataclass rather than against a second hardcoded list.
    """
    import dataclasses

    fields = {f.name for f in dataclasses.fields(ControlCapability)}
    for feature_id, flag in DAEMON_FEATURE_CAPABILITY_FLAGS.items():
        assert flag in fields, f"{feature_id} maps to unknown ControlCapability field {flag!r}"


@pytest.mark.parametrize("feature", WIRE_K_FEATURES)
def test_a_daemon_that_advertises_the_flag_is_believed(feature):
    caps = parse_capabilities({"control": {flag: True for flag in WIRE_K_FEATURES}})
    assert daemon_supports(feature, caps) is True


@pytest.mark.parametrize("feature", WIRE_K_FEATURES)
def test_a_daemon_that_denies_the_flag_is_believed(feature):
    caps = parse_capabilities({"control": {flag: False for flag in WIRE_K_FEATURES}})
    assert daemon_supports(feature, caps) is False


@pytest.mark.parametrize("feature", WIRE_K_FEATURES)
def test_a_daemon_that_does_not_say_is_not_read_as_a_denial(feature):
    """The whole reason the answer is tri-state.

    These flags exist only from daemon 2.36.0, while the features behind them
    shipped between 1.11.0 and 2.16.0 and the published pairing floor is lower
    still. Collapsing "absent" into False would hide five WORKING features on
    every daemon in that range — a regression wearing the shape of a
    simplification.
    """
    caps = parse_capabilities({"control": {"autonomous_control": True}})
    assert daemon_supports(feature, caps) is None


def test_an_unknown_feature_id_and_a_missing_daemon_both_read_as_no_answer():
    caps = parse_capabilities({"control": {"gpu_fan_verify": True}})
    assert daemon_supports("no_such_feature", caps) is None
    assert daemon_supports("gpu_fan_verify", None) is None
    # A capabilities object with no `control` block at all (a pre-1.20 daemon).
    assert daemon_supports("gpu_fan_verify", object()) is None


def test_a_non_boolean_flag_reads_as_no_answer():
    """A malformed payload must not be read as a denial.

    `False` would stand a working feature down on the strength of bad JSON; the
    tri-state `None` keeps the caller's existing fallback in charge, which is the
    behaviour the GUI already had.
    """
    caps = Capabilities(control=ControlCapability())
    caps.control.gpu_fan_verify = "yes"  # type: ignore[assignment]
    assert daemon_supports("gpu_fan_verify", caps) is None


def test_the_five_flags_default_to_no_answer_and_the_siblings_default_to_false():
    """The two defaults are different ON PURPOSE, and this pins the difference.

    For `header_roles` and friends, absent and false mean the same thing to the
    GUI (hide the button, keep the older wording), so `False` is right. For these
    five it is not: absence means "this daemon predates the flag", and the
    feature very probably works.
    """
    control = ControlCapability()
    for flag in WIRE_K_FEATURES:
        assert getattr(control, flag) is None, flag
    assert control.header_roles is False
    assert control.validation_sessions is False


# ── WIRE-k: the call sites ───────────────────────────────────────────────


def _caps(**flags) -> Capabilities:
    return parse_capabilities({"daemon_version": "2.36.0", "control": flags})


def test_gpu_verify_button_follows_the_capability_over_the_version(qtbot):
    """The one live version gate this row removed.

    The fixture is the case a version comparison gets WRONG: a daemon whose
    version is far past 1.11.0 but which says it does not serve the route. A test
    that only checked a supporting daemon would pass with the capability check
    deleted, because the version alone already answers True there.
    """
    from control_ofc.api.models import GpuDiagnosticsInfo
    from tests.test_system_state_page import _diag, _page, _state

    state = _state()
    state.set_capabilities(_caps(gpu_fan_verify=False))
    page, _ = _page(qtbot, state=state)
    page._update_gpu_verify_availability(
        _diag(gpu=GpuDiagnosticsInfo(pci_bdf="0000:03:00.0", fan_control_method="pmfw_curve"))
    )
    assert page._gpu_verify_btn.isHidden() is True

    # The opposite branch, or a stuck predicate passes: the same page, the same
    # writable GPU, with the daemon advertising the route.
    state.set_capabilities(_caps(gpu_fan_verify=True))
    page._update_gpu_verify_availability(
        _diag(gpu=GpuDiagnosticsInfo(pci_bdf="0000:03:00.0", fan_control_method="pmfw_curve"))
    )
    assert page._gpu_verify_btn.isHidden() is False


# ── WIRE-n: the engine is evaluating but not writing ─────────────────────


def test_verify_active_is_parsed_and_defaults_to_writing():
    assert parse_status({}).verify_active is False
    assert parse_status({"verify_active": True}).verify_active is True
    assert parse_status({"verify_active": False}).verify_active is False
    # A malformed value must read as "the engine is writing" — the state that
    # shows the daemon's figures rather than suppressing them, so a bad payload
    # cannot blank a live Controls page.
    assert parse_status({"verify_active": "yes"}).verify_active is False
    assert parse_status({"verify_active": 1}).verify_active is False


def _control(control_id: str = "c1"):
    from control_ofc.services.profile_service import ControlMember, ControlMode, LogicalControl

    return LogicalControl(
        id=control_id,
        name="CPU",
        mode=ControlMode.CURVE,
        curve_id="curve1",
        members=[ControlMember(source="hwmon", member_id="hwmon:x:pwm1", member_label="CPU_FAN")],
    )


def _card(qtbot, control_id: str = "c1"):
    from control_ofc.services.profile_service import CurveConfig, CurveType
    from control_ofc.ui.widgets.control_card import ControlCard

    card = ControlCard(
        _control(control_id),
        [CurveConfig(id="curve1", name="Balanced", type=CurveType.GRAPH)],
    )
    qtbot.addWidget(card)
    return card


def test_a_paused_write_phase_stops_the_card_claiming_applied(qtbot):
    """`WIRE-n` at the card.

    Both branches are asserted from the same card, because a card that could only
    ever say "Not writing" would pass a one-sided test just as well as a correct
    one.
    """
    card = _card(qtbot)

    card.set_output(42.0)
    assert card._status_chip.text() == "Applied"

    card.set_write_paused(True)
    card.set_output(42.0)
    assert card._status_chip.text() == "Not writing"
    assert "not applying it" in card._status_chip.toolTip()
    # The evaluated figure is TRUE and is kept — the chip qualifies it. Blanking
    # it would discard a real value to fix a false claim.
    assert "42" in card._output_label.text()

    card.set_write_paused(False)
    card.set_output(42.0)
    assert card._status_chip.text() == "Applied"


def test_a_skip_still_outranks_a_write_pause(qtbot):
    """A control the daemon did not evaluate at all is the more specific fact.

    Without this the pause could paint over `set_skipped`'s chip, which carries
    the daemon's own reason token and duration — strictly more information.
    """
    card = _card(qtbot)
    card.set_skipped("curve_not_found", 5000, "CPU")
    skipped_text = card._status_chip.text()
    card.set_write_paused(True)
    card.set_output(42.0)
    assert card._status_chip.text() == skipped_text
    assert card._status_chip.text() != "Not writing"


def test_the_controls_page_pushes_the_pause_from_the_poll(qtbot):
    """The wiring, not the card — `CLAUDE.md`'s "test the call site" rule.

    Asserted as a RELATIONSHIP against the status the page was handed, so a page
    that hardcoded either value fails. The `False` leg matters as much as the
    `True` one: a page that never cleared the flag would leave every card
    permanently marked after one diagnostic run.
    """
    from control_ofc.api.models import DaemonStatus
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage()
    qtbot.addWidget(page)
    card = _card(qtbot)
    page._control_cards["c1"] = card

    for paused in (True, False, True):
        status = DaemonStatus(verify_active=paused)
        page._apply_live_outputs(status)
        assert card._write_paused is status.verify_active, f"paused={paused}"


# ── X87-d: the board's own firmware-declared counts ──────────────────────


def test_board_firmware_counts_are_absent_rather_than_zero():
    """`None`, never a zeroed instance.

    A defaulted ``fan_count`` of 0 claims the board has no fan headers — the
    opposite of "the firmware did not say", and it would show a phantom deficit
    on every machine that publishes no descriptor.
    """
    assert parse_hardware_diagnostics({}).board_firmware_counts is None
    assert parse_hardware_diagnostics({"board_firmware_counts": None}).board_firmware_counts is None
    assert (
        parse_hardware_diagnostics({"board_firmware_counts": []}).board_firmware_counts is None
    ), "a malformed non-object must read as 'did not say', not raise"

    parsed = parse_hardware_diagnostics(
        {
            "board_firmware_counts": {
                "platform": 10,
                "special": 0,
                "fan_count": 8,
                "temp_count": 9,
                "volt_count": 10,
                "a_field_a_newer_daemon_added": 1,
            }
        }
    )
    assert parsed.board_firmware_counts == BoardFirmwareCounts(
        platform=10, special=0, fan_count=8, temp_count=9, volt_count=10
    ), "unknown fields are dropped so a newer daemon cannot break an older GUI"


def _diag_with(firmware: BoardFirmwareCounts | None, total_headers: int):
    from control_ofc.api.models import BoardInfo, HwmonChipInfo

    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[HwmonChipInfo(chip_name="it8696")],
            total_headers=total_headers,
        ),
        board=BoardInfo(vendor="Gigabyte", name="X870E AORUS MASTER"),
        expected_chips=["it8696", "it87952"],
        board_firmware_counts=firmware,
    )


def test_the_dual_chip_warning_states_a_measurement_when_the_firmware_gave_one():
    """`X87-d` at the call site: the view-model must pass the counts through.

    The warning's own text is unit-tested in ``test_hwmon_guidance``; what this
    asserts is that ``_issue_card_from_problem`` actually hands it the daemon's
    measurement, which is the half that was missing.
    """
    from control_ofc.services.system_state_view import _issue_card_from_problem

    problem = {
        "key": "dual_chip",
        "label": "Missing chip",
        "fix": "See details",
        "severity": "warning",
    }

    measured = _issue_card_from_problem(
        _diag_with(BoardFirmwareCounts(platform=10, fan_count=8), total_headers=5), problem
    )
    assert measured.detail is not None
    assert "8" in measured.detail and "5" in measured.detail
    assert "not from a lookup table" in measured.detail
    # The sentence must say what it COUNTED. `hwmon.total_headers` is
    # `pwmN`-capable headers only; monitor-only tachometers are a disjoint set on
    # a different endpoint this path never fetches, so calling the difference
    # "reachable" would overstate the deficit on a board with tach-only headers
    # on a detected chip. Asserted as an absence *and* a presence, because the
    # absence alone would pass on a sentence that said nothing at all.
    assert "expose a controllable fan header" in measured.detail
    assert "are reachable" not in measured.detail

    # No descriptor → the warning renders exactly as it did before this row, so
    # the sentence is additive and cannot appear from nowhere.
    inferred = _issue_card_from_problem(_diag_with(None, total_headers=5), problem)
    assert inferred.detail is not None
    assert "not from a lookup table" not in inferred.detail


def test_no_measurement_is_claimed_when_the_firmware_count_is_not_a_deficit():
    """Equal or lower counts say nothing useful, so they say nothing.

    A missing chip that carries no fan headers is a real configuration, and
    "declares 5 and 5 are reachable" printed beside a missing-chip warning reads
    as a contradiction rather than as information.
    """
    from control_ofc.ui.hwmon_guidance import dual_chip_warning_html

    for firmware_count in (5, 4):
        html = dual_chip_warning_html(
            "X870E AORUS MASTER",
            ["it8696", "it87952"],
            ["it8696"],
            firmware_fan_count=firmware_count,
            reachable_fan_count=5,
        )
        assert html is not None
        assert "not from a lookup table" not in html, firmware_count


# ── WIRE-k: the four probe-then-recover call sites ───────────────────────
#
# Each of these used to send the request first and infer the feature's absence
# from a `404` coming back — a status the route fallback and a handler's own
# unknown-id branch both return, so the probe could not tell them apart. The
# assertion in every case is that **no request goes out** when the daemon has
# already said no, and that one still does when the daemon has not said.


def test_readiness_stands_down_without_a_request_when_the_daemon_says_no(qtbot):
    from control_ofc.api.models import ConnectionState
    from control_ofc.services.app_state import AppState
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.hardware_page import HardwarePage

    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_capabilities(_caps(hardware_readiness=False))
    page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state), client=object())
    qtbot.addWidget(page)

    emitted: list[int] = []
    page._readiness_request.connect(lambda: emitted.append(1))
    page._ensure_readiness_worker = lambda: True  # type: ignore[method-assign]

    page._fetch_readiness()
    assert emitted == [], "a denied capability must not produce a request"
    assert page._readiness_unsupported is True

    # The opposite branch on a FRESH page — the latch above is sticky by design,
    # so reusing this one would prove nothing.
    state2 = AppState()
    state2.set_connection(ConnectionState.CONNECTED)
    state2.set_capabilities(_caps(hardware_readiness=True))
    page2 = HardwarePage(
        state=state2, diagnostics_service=DiagnosticsService(state2), client=object()
    )
    qtbot.addWidget(page2)
    emitted2: list[int] = []
    page2._readiness_request.connect(lambda: emitted2.append(1))
    page2._ensure_readiness_worker = lambda: True  # type: ignore[method-assign]
    page2._fetch_readiness()
    assert emitted2 == [1]


def test_the_port_probe_button_is_down_when_the_route_is_absent(qtbot):
    """Two independent reasons, and they must not be conflated.

    `probe_available` is the daemon saying probing is *disabled or unprivileged*
    on this machine; the capability is about whether the route exists at all.
    Asserted with `probe_available=True` so only the capability can be doing the
    work — a fixture where both were false would pass with the new check deleted.
    """
    from control_ofc.api.models import ConnectionState
    from control_ofc.services.app_state import AppState
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.hardware_page import HardwarePage

    def _probe_btn(flag_value):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_capabilities(_caps(superio_port_probe=flag_value))
        page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state), client=None)
        qtbot.addWidget(page)
        panel = type("P", (), {"probe_available": True, "probe_reason": ""})()
        # The section owns the button; hold it, or shiboken reaps the whole tree
        # before the assertion runs.
        section = page._build_advanced(panel)
        qtbot.addWidget(section)
        return section, page._probe_btn

    _keep_false, disabled = _probe_btn(False)
    assert disabled.isEnabled() is False
    _keep_true, enabled = _probe_btn(True)
    assert enabled.isEnabled() is True


def test_preferred_sensors_stands_down_without_a_request(qapp, app_state, settings_service):
    from control_ofc.ui.pages.settings_page import SettingsPage

    calls: list[int] = []

    class _Client:
        def inventory_hwmon(self):
            calls.append(1)
            raise AssertionError("must not be called when the daemon says no")

    app_state.set_capabilities(_caps(preferred_sensors=False))
    page = SettingsPage(state=app_state, settings_service=settings_service, client=_Client())
    page._refresh_preferred_sensors()
    assert calls == []


def test_daemon_config_stands_down_without_a_request(qapp, app_state, settings_service):
    from control_ofc.ui.pages.settings_page import SettingsPage

    calls: list[int] = []

    class _Client:
        def get_daemon_config(self):
            calls.append(1)
            raise AssertionError("must not be called when the daemon says no")

    app_state.set_capabilities(_caps(daemon_config_report=False))
    page = SettingsPage(state=app_state, settings_service=settings_service, client=_Client())
    page._refresh_daemon_config()
    assert calls == []
    assert page._daemon_config_unsupported is True
