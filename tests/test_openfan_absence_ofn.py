"""OpenFan Controller absence must be invisible during normal use (OFN register).

The controller is OPTIONAL hardware. On a machine that does not have one, nothing
should imply the install is broken — no warning chip, no permanent inventory row,
no diagnostic card issuing remediation advice for a device the user does not own.

Every assertion here is a RELATIONSHIP against ``capabilities.openfan.present``
rather than against a literal string, and every rule is asserted in BOTH arms. A
literal-string test is satisfied by a build that ignores the wire field entirely,
which is the defect these lines exist to prevent (CLAUDE.md, DEC-324/DEC-334);
and a single arm is satisfied by a stuck predicate.

Visibility is read with ``isVisibleTo(...)``, never ``isVisible()``: under
``QT_QPA_PLATFORM=offscreen`` nothing is shown, so ``isVisible()`` is False for
every widget and such an assertion passes with the visibility call deleted.

The reference widget is each row's IMMEDIATE PARENT, not the page. Both surfaces
sit behind a container that is itself hidden in a headless test — the Logs card
inside a non-current ``QTabWidget`` tab, the Dashboard chip inside a
``QStackedLayout`` page the test never navigated to — so ``isVisibleTo(page)``
answers False through a hidden ancestor whatever this change did, and would
assert nothing. ``_shown_in_parent`` scopes the question to the flag under test.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel

from control_ofc.api.models import (
    Capabilities,
    DaemonStatus,
    HwmonCapability,
    OpenfanCapability,
    SubsystemStatus,
)
from control_ofc.services.app_state import AppState, ConnectionState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.pages.logs_page import LogsPage
from control_ofc.ui.pages.overview_page import OverviewPage
from control_ofc.ui.pages.settings_page import SettingsPage


def _shown_in_parent(w) -> bool:
    """Whether ``w``'s own visibility flag is set, independent of its ancestors."""
    parent = w.parentWidget()
    assert parent is not None, "widget must be parented for this to mean anything"
    return w.isVisibleTo(parent)


def _caps(present: bool) -> Capabilities:
    return Capabilities(
        openfan=OpenfanCapability(present=present, channels=4, write_support=present),
        hwmon=HwmonCapability(present=True, pwm_header_count=3, write_support=True),
    )


def _state(present: bool) -> AppState:
    """A connected AppState whose capabilities are NOT yet set.

    Pages connect to ``capabilities_updated`` in ``__init__``, so the fixtures
    below construct the page first and then publish capabilities. That exercises
    the real signal path rather than a pre-seeded attribute — which is the half
    most likely to be broken (CLAUDE.md: ``.click()``, not ``_handler()``).
    """
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    return s


# ── OFN-d: the Logs diagnostics probe card ────────────────────────────────────


@pytest.mark.parametrize("present", [True, False])
def test_logs_controller_card_tracks_the_wire_field(qtbot, present):
    """The OpenFan probe exists iff the daemon reports a controller.

    Both arms, and asserted against ``caps.openfan.present`` — not against the
    card's text, which a build that never consulted capabilities would still
    render correctly.
    """
    state = _state(present)
    page = LogsPage(diagnostics_service=DiagnosticsService(state), state=state)
    qtbot.addWidget(page)
    state.set_capabilities(_caps(present))

    # Bind and USE the page (DEC-356): `qtbot.addWidget` does not keep it alive,
    # and a dropped reference silently severs every bound-method connection made
    # in __init__, leaving the assertions below about a corpse. This precondition
    # is also what stops ruff F841 advising the binding away.
    assert page._controller_card is not None

    assert _shown_in_parent(page._controller_card) == state.capabilities.openfan.present, (
        f"controller card visibility disagreed with the wire field for {present=}"
    )


def test_logs_controller_card_returns_when_a_controller_is_adopted_later(qtbot):
    """DEC-265: a controller adopted after startup (rescan, or slow enumeration)
    must bring its card back without a restart.

    This is the discriminating arm for the capabilities reconnection: a page that
    only evaluated visibility once, at construction, passes every static test
    above and fails this one.
    """
    state = _state(False)
    page = LogsPage(diagnostics_service=DiagnosticsService(state), state=state)
    qtbot.addWidget(page)
    state.set_capabilities(_caps(False))
    assert _shown_in_parent(page._controller_card) is False

    state.set_capabilities(_caps(True))

    assert _shown_in_parent(page._controller_card) is True, (
        "a controller adopted after startup must restore its diagnostics card"
    )


# ── OFN-d: the probe text itself ──────────────────────────────────────────────


def test_absent_controller_text_states_a_fact_and_prescribes_no_remedy(qtbot):
    """Absence of optional hardware must not read as a misconfiguration.

    The old text was "Check USB connection and serial device permissions" — advice
    predicated on the device existing. Asserts the absence AND the replacement:
    absence alone would pass against an empty string.
    """
    absent_state = _state(False)
    absent_state.set_capabilities(_caps(False))
    text = DiagnosticsService(absent_state).format_controller_status()

    assert "Present: No" in text, "the bundle should still record that the daemon looked"
    for prescription in ("Check USB", "permissions"):
        assert prescription not in text, (
            f"absent optional hardware must not prescribe {prescription!r}: {text!r}"
        )
    assert "expected if you do not have one" in text

    # The opposite arm: a present controller still reports its detail, so this is
    # not satisfied by a build that emptied the section outright.
    present_state = _state(True)
    present_state.set_capabilities(_caps(True))
    present_text = DiagnosticsService(present_state).format_controller_status()
    assert "Present: Yes" in present_text
    assert "Channels: 4" in present_text


# ── OFN-g: the inventory row and the subsystem chip ───────────────────────────


@pytest.mark.parametrize("present", [True, False])
def test_overview_openfan_row_tracks_the_wire_field(qtbot, present):
    state = _state(present)
    page = OverviewPage(state=state)
    qtbot.addWidget(page)
    state.set_capabilities(_caps(present))

    assert _shown_in_parent(page._openfan_label) == state.capabilities.openfan.present, (
        f"overview OpenFan row visibility disagreed with the wire field for {present=}"
    )
    # The GPU rows are the deliberate contrast — OFN-g was a scoped decision, not
    # a blanket rule to hide every absent device.
    assert _shown_in_parent(page._amd_gpu_label) is True


@pytest.mark.parametrize("present", [True, False])
def test_dashboard_openfan_chip_tracks_the_wire_field(qtbot, present):
    state = _state(present)
    page = DashboardPage(state=state)
    qtbot.addWidget(page)
    state.set_capabilities(_caps(present))

    assert _shown_in_parent(page._sub_openfan_label) == state.capabilities.openfan.present, (
        f"dashboard OpenFan chip visibility disagreed with the wire field for {present=}"
    )
    # hwmon is the opposite branch of the same handler and must be unaffected;
    # without it, a page that hid every chip would pass.
    assert _shown_in_parent(page._sub_hwmon_label) is True


def test_an_unhealthy_openfan_subsystem_is_still_shown(qtbot):
    """The warning the brief asked to PRESERVE.

    Hiding the chip while no controller is present must not hide a controller
    that dropped off mid-session — that is a real fault, and it is the case the
    "do not suppress all OpenFan errors globally" instruction is about.
    """
    state = _state(False)
    page = DashboardPage(state=state)
    qtbot.addWidget(page)
    state.set_capabilities(_caps(False))
    assert _shown_in_parent(page._sub_openfan_label) is False

    state.set_status(
        DaemonStatus(
            subsystems=[SubsystemStatus(name="openfan", status="warn", reason="link down")]
        )
    )

    assert _shown_in_parent(page._sub_openfan_label) is True, (
        "an unhealthy openfan subsystem must surface even while the chip is hidden"
    )
    assert "warn" in page._sub_openfan_label.text()


def test_device_discovery_card_does_not_mention_openfan_when_absent(qtbot):
    """The brief's goal for the inventory card, asserted end-to-end.

    **Scoped to the Device Discovery card, and the scope is the honest part.**
    The first draft of this test claimed "no page mentions OpenFan when it is
    absent" and passed — but only because it never set a `/status`, so the Daemon
    Health card's subsystem block was empty. That block renders every subsystem
    the daemon reports, verbatim, and the daemon always reports
    `openfan: ok — no OpenFanController connected`. The claim was therefore false
    and the test was blind to the one surface that could disprove it.

    A `/status` IS now published below, so the blind spot cannot come back.

    **Re-scoped by DEC-381**, and the precondition doing it is the same machinery
    pointed the other way. `OFN-q` has since been answered: a HEALTHY openfan
    subsystem line is now dropped on a machine with no controller, so the old
    precondition — "the Daemon Health block still carries the openfan line" —
    became false by design, exactly as it warned it would. It is replaced by the
    surviving `hwmon` line, which keeps this test's scoping honest for the same
    reason the old one did: the Daemon Health card is still populated and still
    excluded from the sweep below, so the Device Discovery assertion is
    describing a real page rather than an empty one.
    """
    state = _state(False)
    overview = OverviewPage(state=state)
    qtbot.addWidget(overview)
    state.set_capabilities(_caps(False))
    state.set_status(
        DaemonStatus(
            subsystems=[
                SubsystemStatus(
                    name="openfan", status="ok", reason="no OpenFanController connected"
                ),
                SubsystemStatus(name="hwmon", status="ok", age_ms=900),
            ]
        )
    )

    card = overview._openfan_label.parentWidget()
    assert card is not None

    # Precondition: the surface this test deliberately EXCLUDES is populated, so
    # the scoping below is doing real work rather than describing an empty page.
    assert "hwmon" in overview._subsystems_label.text().lower(), (
        "the Daemon Health subsystem block should still carry the daemon's other "
        "subsystems — if this fails, the block is empty and this test's scope is "
        "describing nothing"
    )

    leaked = [
        lbl.text()
        for lbl in card.findChildren(QLabel)
        if _shown_in_parent(lbl) and "openfan" in lbl.text().lower()
    ]
    assert leaked == [], f"Device Discovery mentions OpenFan on a machine without one: {leaked}"

    # Opposite arm: with a controller present the row IS mentioned, so this is not
    # satisfied by a page that renders no labels at all.
    present_state = _state(True)
    present_page = OverviewPage(state=present_state)
    qtbot.addWidget(present_page)
    present_state.set_capabilities(_caps(True))
    present_card = present_page._openfan_label.parentWidget()
    mentioned = [
        lbl.text()
        for lbl in present_card.findChildren(QLabel)
        if _shown_in_parent(lbl) and "openfan" in lbl.text().lower()
    ]
    assert mentioned, "a present controller must still be shown"


# ── OFN-q: the Overview "Daemon Health" subsystem block (DEC-381) ─────────────


def _health_status(openfan_status: str) -> DaemonStatus:
    """A `/status` carrying the openfan subsystem the daemon reports on every machine.

    `openfan: ok — no OpenFanController connected` is the daemon's own literal
    wording for "there is no controller here" (`health/staleness.rs`); the entry
    is emitted regardless of hardware so `subsystems[0]` stays openfan. `hwmon`
    rides along as the control: every assertion below pairs with one on this
    line, or a renderer that dropped every subsystem would pass.
    """
    reason = "no OpenFanController connected" if openfan_status == "ok" else "link down"
    return DaemonStatus(
        overall_status="ok" if openfan_status == "ok" else "warn",
        subsystems=[
            SubsystemStatus(name="openfan", status=openfan_status, reason=reason),
            SubsystemStatus(name="hwmon", status="ok", age_ms=900),
        ],
    )


@pytest.mark.parametrize("present", [True, False])
def test_daemon_health_openfan_line_tracks_the_wire_field(qtbot, present):
    """A HEALTHY openfan subsystem is listed iff the daemon reports a controller.

    This is the arm the pre-fix path cannot produce. Before DEC-381 the renderer
    listed every subsystem unconditionally, so `openfan` was in this text for
    every value of `present`; only a build that actually consulted capabilities
    can leave it out. The `present=True` arm is what stops a blanket filter
    passing, and the `hwmon` assertion is what stops an empty block passing.
    """
    state = _state(present)
    page = OverviewPage(state=state)
    qtbot.addWidget(page)
    state.set_capabilities(_caps(present))
    state.set_status(_health_status("ok"))

    text = page._subsystems_label.text().lower()
    assert ("openfan" in text) == state.capabilities.openfan.present, (
        f"daemon-health openfan line disagreed with the wire field for {present=}: {text!r}"
    )
    assert "hwmon" in text, "the other subsystems must be untouched"


@pytest.mark.parametrize("wire_status", ["warn", "crit"])
def test_daemon_health_keeps_an_unhealthy_openfan_line_when_absent(qtbot, wire_status):
    """The warning the filter must NOT swallow — and the arm that bounds it.

    `OFN-q`'s literal fix shape was an unconditional `name == "openfan"` filter;
    this test is what distinguishes DEC-381's from it, because an unconditional
    filter passes the test above and fails this one. Two reasons it matters.

    First, the posture is already project policy one surface over: DEC-361 hides
    the Dashboard's OpenFan chip while no controller is present and re-raises it
    the moment the subsystem goes unhealthy (`dashboard_page.py`), and
    `test_an_unhealthy_openfan_subsystem_is_still_shown` above pins it there.

    Second, it is what covers the poll-skew window. `/capabilities` refreshes
    every 300 s against `/status`'s 1 Hz, so a controller unplugged mid-session
    can be `warn` here while `present` is still a stale `False` — and that
    warning feeds `overall_status`, so the `Status:` pill would degrade with its
    explanation hidden for up to five minutes.
    """
    state = _state(False)
    page = OverviewPage(state=state)
    qtbot.addWidget(page)
    state.set_capabilities(_caps(False))
    assert state.capabilities.openfan.present is False  # precondition: the filter is armed
    state.set_status(_health_status(wire_status))

    text = page._subsystems_label.text().lower()
    assert "openfan" in text, (
        f"an openfan subsystem reporting {wire_status!r} must survive the absence filter"
    )
    assert wire_status in text and "link down" in text, "the fault itself must reach the user"


# ── OFN-e: the two OpenFan rows in Settings ▸ Daemon Configuration (DEC-381) ──

#: Lower-cased fragment of the annotation, matched rather than compared whole so
#: the test is not a second copy of the production wording.
_ABSENT_NOTE = "no controller detected"

#: (config key, a fragment of the row's own permanent sublabel). The second half
#: is asserted in BOTH arms: annotating must ADD a sentence, never replace the
#: description of what the field does.
_OPENFAN_SETTING_ROWS = (
    ("serial.port", "openfan device path"),
    ("serial.timeout_ms", "read timeout for the openfan device"),
)


@pytest.mark.parametrize("present", [True, False])
def test_settings_openfan_rows_track_the_wire_field(qtbot, settings_service, present):
    """The daemon emits `serial.*` on every machine, so the GUI says whose it is.

    `api/handlers/config.rs` publishes `serial.port` and `serial.timeout_ms` with
    no hardware gate, so two of this card's six rows describe hardware the user
    may not own — worded as though they do. Asserted as a relationship against
    `capabilities.openfan.present`, in both arms: a literal-string test is
    satisfied by a build that annotates unconditionally, which would tell every
    OpenFan owner their controller is missing.
    """
    state = _state(present)
    page = SettingsPage(state=state, settings_service=settings_service)
    qtbot.addWidget(page)
    state.set_capabilities(_caps(present))

    for key, own_words in _OPENFAN_SETTING_ROWS:
        label = page._row_sublabels[key]
        text = label.text().lower()
        assert (_ABSENT_NOTE in text) == (not state.capabilities.openfan.present), (
            f"settings row {key!r} annotation disagreed with the wire field for "
            f"{present=}: {text!r}"
        )
        assert own_words in text, (
            f"settings row {key!r} lost its own description — the annotation is "
            f"appended, not substituted: {text!r}"
        )
        # Demoted, never hidden: `isVisibleTo(parent)`, because under
        # QT_QPA_PLATFORM=offscreen `isVisible()` is False for everything and the
        # assertion would pass with the row deleted.
        assert _shown_in_parent(label) is True, f"settings row {key!r} must stay visible"

    # The point of annotating rather than hiding. `serial.port` is how a user
    # pins a controller that is attached but not being detected — which is
    # precisely when `present` is False, so a disabled field would lock the user
    # out of the one control that recovers their hardware.
    assert page._serial_port_edit.isEnabled() is True
    assert page._serial_timeout_spin.isEnabled() is True


def test_settings_openfan_rows_are_silent_before_capabilities_arrive(qtbot, settings_service):
    """The third state a two-arm test misses: the daemon has not answered yet.

    `capabilities is None` means "not asked", not "no controller". Annotating
    there would make every startup briefly claim the user's hardware is missing,
    and would also mislabel a daemon too old to publish the field.
    """
    state = _state(False)
    page = SettingsPage(state=state, settings_service=settings_service)
    qtbot.addWidget(page)

    assert state.capabilities is None, "precondition: no capabilities published"
    for key, own_words in _OPENFAN_SETTING_ROWS:
        text = page._row_sublabels[key].text().lower()
        assert _ABSENT_NOTE not in text, (
            f"settings row {key!r} claimed hardware was missing before the daemon said: {text!r}"
        )
        assert own_words in text


def test_settings_openfan_rows_are_annotated_when_capabilities_already_landed(
    qtbot, settings_service
):
    """The other half of the wiring, which the signal path cannot cover.

    Every test above publishes capabilities AFTER constructing the page, so all
    of them exercise `capabilities_updated` and none of them exercise the direct
    call in `__init__`. In the real app the page is built when the window is —
    routinely after the first `/capabilities` has landed — and nothing re-emits
    for a late subscriber, so without that call the rows would read as though a
    controller were present until the next refresh 300 s later
    (`constants.CAPABILITIES_REFRESH_INTERVAL_S`).

    Deleting either half alone leaves the other's tests green, which is why they
    are mutated separately (CLAUDE.md, DEC-379: widening a signature — or adding
    a second way to reach a rule — is a call-site sweep).
    """
    state = _state(False)
    state.set_capabilities(_caps(False))  # before the page exists
    page = SettingsPage(state=state, settings_service=settings_service)
    qtbot.addWidget(page)

    assert state.capabilities.openfan.present is False  # precondition
    for key, _own_words in _OPENFAN_SETTING_ROWS:
        assert _ABSENT_NOTE in page._row_sublabels[key].text().lower(), (
            f"settings row {key!r} ignored capabilities that landed before construction"
        )


def test_daemon_health_an_emptied_list_reads_all_ok_not_absent(qtbot):
    """`OFN-ap`: when the filter removes the ONLY line — a healthy openfan on a
    machine with none — the card must not show "—", the placeholder for "no
    `/status` at all". Driven through the page, like the tests above.

    Unreachable on every current daemon (hwmon and engine are always reported),
    so the status here is hand-built with openfan alone.
    """
    from control_ofc.services.overview_view import build_daemon_health_vm

    state = _state(False)
    page = OverviewPage(state=state)
    qtbot.addWidget(page)
    state.set_capabilities(_caps(False))
    only_openfan = DaemonStatus(
        overall_status="ok",
        subsystems=[
            SubsystemStatus(name="openfan", status="ok", reason="no OpenFanController connected")
        ],
    )
    state.set_status(only_openfan)
    assert page._subsystems_label.text() == "Subsystems: all ok"
    # The two other empty cases keep "—": no status, and a status with none.
    assert build_daemon_health_vm(_caps(False), None).subsystems_text == "Subsystems: —"
    none_reported = DaemonStatus(overall_status="ok", subsystems=[])
    assert build_daemon_health_vm(_caps(False), none_reported).subsystems_text == "Subsystems: —"
