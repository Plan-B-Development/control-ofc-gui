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

    A `/status` IS now published below, so the blind spot cannot come back: if
    someone widens the production change to filter that line, the precondition
    here fails loudly and tells them to re-scope this test. The remaining mention
    is deliberate and recorded as `OFN-q` — it is the daemon's health model, from
    a different endpoint, and suppressing one subsystem inside a generic renderer
    is a decision the user has not been asked for.
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
                )
            ]
        )
    )

    card = overview._openfan_label.parentWidget()
    assert card is not None

    # Precondition: the surface this test deliberately EXCLUDES is populated, so
    # the scoping below is doing real work rather than describing an empty page.
    assert "openfan" in overview._subsystems_label.text().lower(), (
        "OFN-q: the Daemon Health subsystem block should still carry the daemon's "
        "openfan line — if this fails, that line was filtered and this test's scope "
        "is now wrong"
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
