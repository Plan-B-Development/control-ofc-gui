"""G4 — the Settings page consumes what the daemon publishes (`WIRE-d`/`g`/`x`).

Three fields the daemon sends for a client to *act* on, which the GUI either
hardcoded past or dropped. Each test asserts a relationship against the wire
value rather than a literal: a literal is satisfied by the hardcoded value these
rows are about, which is exactly how `WIRE-d` survived — the GUI's 8 and the
daemon's `STOP_TIMEOUT` agreed by coincidence.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    Capabilities,
    ControlCapability,
    DaemonConfig,
    DaemonConfigKey,
    DefaultCpuSensor,
    Limits,
    OpenfanCapability,
    parse_capabilities,
)
from control_ofc.ui.pages.settings_page import (
    WIZARD_SPINDOWN_MAX_S,
    WIZARD_SPINDOWN_MIN_S,
    SettingsPage,
)


@pytest.fixture()
def page(qapp, app_state, settings_service):
    return SettingsPage(state=app_state, settings_service=settings_service, client=None)


# ── WIRE-d ───────────────────────────────────────────────────────────────────


def test_limits_block_is_parsed_from_the_wire() -> None:
    caps = parse_capabilities({"limits": {"openfan_stop_timeout_s": 6}})
    assert caps.limits.openfan_stop_timeout_s == 6


@pytest.mark.parametrize("advertised", [5, 6, 8])
def test_spindown_ceiling_follows_the_advertised_stop_timeout(page, app_state, advertised) -> None:
    """The ceiling must equal what the daemon said, not a constant.

    Asserted as `== advertised` over several values, so a call site that
    reverted to the literal 8 fails on 5 and 6 — the shape a single-value test
    would miss.
    """
    app_state.capabilities = Capabilities(
        openfan=OpenfanCapability(present=True),
        limits=Limits(openfan_stop_timeout_s=advertised),
        control=ControlCapability(autonomous_control=True),
    )
    page._apply_wizard_spindown_limit()
    assert page._wizard_spindown_spin.maximum() == advertised


def test_spindown_value_is_clamped_down_to_the_advertised_ceiling(page, app_state) -> None:
    """A stored 12 s must not survive against a daemon that restores at 6 s."""
    page._wizard_spindown_spin.setValue(WIZARD_SPINDOWN_MAX_S)
    app_state.capabilities = Capabilities(
        openfan=OpenfanCapability(present=True),
        limits=Limits(openfan_stop_timeout_s=6),
    )
    page._apply_wizard_spindown_limit()
    assert page._wizard_spindown_spin.value() == 6


@pytest.mark.parametrize(
    ("caps", "why"),
    [
        (None, "no capabilities yet"),
        (Capabilities(openfan=OpenfanCapability(present=False)), "no OpenFan controller"),
        (
            Capabilities(openfan=OpenfanCapability(present=True), limits=Limits()),
            "daemon advertised nothing (0)",
        ),
    ],
)
def test_spindown_ceiling_falls_back_to_the_static_band(page, app_state, caps, why) -> None:
    """Narrowing on an unknown limit would silently shorten every user's timer.

    The opposite branch of the test above — without it, a `_apply` that always
    returned the floor would pass the ceiling tests for `advertised=5`.
    """
    app_state.capabilities = caps
    page._apply_wizard_spindown_limit()
    assert page._wizard_spindown_spin.maximum() == WIZARD_SPINDOWN_MAX_S, why


def test_spindown_ceiling_never_falls_below_the_floor(page, app_state) -> None:
    """An implausible advertised value must not collapse the control."""
    app_state.capabilities = Capabilities(
        openfan=OpenfanCapability(present=True), limits=Limits(openfan_stop_timeout_s=1)
    )
    page._apply_wizard_spindown_limit()
    assert page._wizard_spindown_spin.maximum() == WIZARD_SPINDOWN_MIN_S


def test_showing_the_page_applies_the_ceiling(page, app_state) -> None:
    """The call site, not the helper.

    Capabilities arrive *after* construction, so a correct
    `_apply_wizard_spindown_limit` that nothing re-runs leaves the static
    fallback in place for the whole session — the helper's own tests cannot see
    that, which is `CLAUDE.md`'s extracting-a-rule lesson exactly.
    """
    assert page._wizard_spindown_spin.maximum() == WIZARD_SPINDOWN_MAX_S
    app_state.capabilities = Capabilities(
        openfan=OpenfanCapability(present=True), limits=Limits(openfan_stop_timeout_s=6)
    )
    page.show()
    try:
        assert page._wizard_spindown_spin.maximum() == 6
    finally:
        page.hide()


# ── WIRE-g ───────────────────────────────────────────────────────────────────


def _cfg(**mutability: bool) -> DaemonConfig:
    return DaemonConfig(
        keys=[DaemonConfigKey(key=k.replace("__", "."), mutable=m) for k, m in mutability.items()]
    )


def test_an_immutable_key_disables_its_editor(page) -> None:
    widget = page._daemon_key_widgets["serial.port"]
    widget.setEnabled(True)
    page._apply_daemon_key_mutability(_cfg(serial__port=False))
    assert widget.isEnabled() is False


def test_a_mutable_key_keeps_its_editor(page) -> None:
    """The opposite branch — without it, an unconditional disable passes above."""
    widget = page._daemon_key_widgets["serial.port"]
    widget.setEnabled(True)
    page._apply_daemon_key_mutability(_cfg(serial__port=True))
    assert widget.isEnabled() is True


def test_a_key_that_becomes_mutable_again_stops_claiming_to_be_read_only(page) -> None:
    """`_set_daemon_config_available(True)` re-enables the widget but never
    touches tooltips, so without an explicit restore the control would be
    editable while still saying it is read-only — a claim that outlives its
    reason, which is the failure class this whole wave is about.
    """
    widget = page._daemon_key_widgets["serial.port"]
    original = page._daemon_key_tooltips["serial.port"]
    page._apply_daemon_key_mutability(_cfg(serial__port=False))
    assert "read-only" in widget.toolTip()
    page._apply_daemon_key_mutability(_cfg(serial__port=True))
    assert widget.toolTip() == original
    assert "read-only" not in widget.toolTip()


def test_a_key_the_daemon_never_mentions_is_left_alone(page) -> None:
    """Absence means "older daemon", not "locked".

    Greying a control on the strength of a truncated response is the same
    over-reach the row is about, pointed the other way.
    """
    widget = page._daemon_key_widgets["serial.port"]
    widget.setEnabled(True)
    page._apply_daemon_key_mutability(DaemonConfig(keys=[]))
    assert widget.isEnabled() is True


def test_every_editable_key_is_subject_to_the_mutability_check(page) -> None:
    """The map the check iterates must cover every editor, or a key drifts free.

    Relationship, not a list: compares against the module's declared widget map
    rather than restating today's six keys.
    """
    from control_ofc.ui.pages.settings_page import DAEMON_CONFIG_WIDGETS

    checked = set(page._daemon_key_widgets)
    declared = set(DAEMON_CONFIG_WIDGETS) - {"profiles.search_dirs"}
    assert declared <= checked, f"not mutability-checked: {sorted(declared - checked)}"


def test_immutable_search_dirs_disables_the_add_and_remove_buttons(page) -> None:
    for widget in (page._add_search_dir_btn, page._remove_search_dir_btn):
        widget.setEnabled(True)
    page._apply_daemon_key_mutability(_cfg(profiles__search_dirs=False))
    assert page._add_search_dir_btn.isEnabled() is False
    assert page._remove_search_dir_btn.isEnabled() is False


def test_only_one_readiness_request_is_ever_outstanding(qtbot, qapp) -> None:
    """The invariant that makes `_awaiting_rescan` attributable.

    Two independent triggers — the first-show auto-fetch and the Re-scan button —
    land on the same `_on_readiness_ok`, and the replies carry nothing that says
    which request they answer. With both in flight the auto-fetch's answer is
    read as the rescan's: the wrong verdict shown, and the user's real re-scan
    then reporting nothing. Serialising is what fixes it, so this asserts the
    serialisation rather than the symptom.
    """
    from control_ofc.api.models import HardwareReadiness
    from control_ofc.ui.pages.hardware_page import HardwarePage

    page = HardwarePage(state=None, client=object())
    qtbot.addWidget(page)
    page._ensure_readiness_worker = lambda: True

    page._fetch_readiness()  # the first-show auto-fetch: not a rescan
    assert page._readiness_in_flight is True
    assert page._refresh_btn.isEnabled() is False
    assert page._awaiting_rescan is False

    page._refresh_readiness()  # the user presses Re-scan mid-flight
    assert page._awaiting_rescan is False, "the pending auto-fetch must not be armed as a rescan"

    page._on_readiness_ok(HardwareReadiness(generation=1, overall="ok"))
    # Assert on the CLAIM, not on emptiness: `_on_readiness_ok` hides the status
    # label without clearing it, so the in-flight text is still there.
    text = page._status_label.text()
    assert "re-scanned" not in text and "No change" not in text, (
        f"an auto-fetch reply must not report on a re-scan: {text!r}"
    )
    assert page._readiness_in_flight is False
    assert page._refresh_btn.isEnabled() is True

    # And a refresh started when nothing is outstanding still works.
    page._refresh_readiness()
    assert page._awaiting_rescan is True
    page._on_readiness_ok(HardwareReadiness(generation=2, overall="ok"))
    assert "re-scanned" in page._status_label.text()


def test_a_failed_fetch_re_enables_the_button(qtbot, qapp) -> None:
    """Otherwise one transient error disables Re-scan for the whole session,
    with no way back — the same trap `_refresh_daemon_config` documents."""
    from control_ofc.ui.pages.hardware_page import HardwarePage

    page = HardwarePage(state=None, client=object())
    qtbot.addWidget(page)
    page._ensure_readiness_worker = lambda: True
    page._fetch_readiness(force=True)
    assert page._refresh_btn.isEnabled() is False
    page._on_readiness_error("transient", "socket closed")
    assert page._refresh_btn.isEnabled() is True
    assert page._readiness_in_flight is False


# ── WIRE-x ───────────────────────────────────────────────────────────────────


def test_auto_recommendation_shows_the_daemon_rationale_and_confidence(page) -> None:
    page._render_default_cpu_rationale(
        DefaultCpuSensor(
            sensor_id="hwmon:k10temp:pci0:Tctl",
            confidence="high",
            rationale="k10temp Tctl is the CPU die sensor",
            source="auto",
        )
    )
    text = page._pref_cpu_rationale.text()
    assert "k10temp Tctl is the CPU die sensor" in text
    assert "high confidence" in text
    assert "Recommended by the daemon" in text
    # `isVisibleTo`, never `isVisible`: under offscreen Qt nothing is shown, so
    # `isVisible()` is False for every widget and the assertion would pass with
    # the setVisible call deleted.
    assert page._pref_cpu_rationale.isVisibleTo(page._pref_cpu_rationale.parentWidget())


def test_a_user_pinned_sensor_is_not_described_as_a_recommendation(page) -> None:
    """`source: "user"` means the star echoes a choice already made; calling
    that a recommendation is circular."""
    page._render_default_cpu_rationale(
        DefaultCpuSensor(sensor_id="x", rationale="pinned", source="user", confidence="high")
    )
    text = page._pref_cpu_rationale.text()
    assert "pinned CPU sensor" in text
    assert "Recommended by the daemon" not in text


def test_unknown_confidence_is_suppressed_rather_than_printed(page) -> None:
    page._render_default_cpu_rationale(
        DefaultCpuSensor(sensor_id="x", rationale="why", confidence="unknown", source="auto")
    )
    assert "unknown" not in page._pref_cpu_rationale.text()


def test_no_recommendation_hides_the_line(page) -> None:
    page._render_default_cpu_rationale(None)
    assert page._pref_cpu_rationale.text() == ""
    assert not page._pref_cpu_rationale.isVisibleTo(page._pref_cpu_rationale.parentWidget())
