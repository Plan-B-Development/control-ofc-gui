"""Tests for the `runtime_config_degraded` surface (`WIRE-a`, daemon DEC-321).

**[SAFETY].** When the daemon's own `runtime.toml` fails to load it falls back to
built-in defaults, and those carry no `header_roles`. On a board whose Super-I/O
publishes no `pwmN_label` files, a user's hand-assigned `pump` role is the only
evidence a header drives a pump — so a failed *startup* load silently removes
that header's 30% floor, its stop exemption and its pump-safe identify. The
daemon's only other notification is one `warn!` in its journal.

Three layers, tested separately because each can break without the others:
the parse, the headless message, and the page wiring that connects them.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from control_ofc.api.models import DaemonStatus, RuntimeConfigDegraded, parse_status
from control_ofc.services.dashboard_view import runtime_config_degraded_message
from control_ofc.ui.pages.dashboard_page import DashboardPage

STARTUP = RuntimeConfigDegraded(
    reason="malformed", path="/etc/control-ofc/runtime.toml", detail="expected `=`", phase="startup"
)
RELOAD = RuntimeConfigDegraded(
    reason="unreadable", path="/etc/control-ofc/runtime.toml", detail="EACCES", phase="reload"
)
UNKNOWN_PHASE = RuntimeConfigDegraded(
    reason="malformed", path="/etc/control-ofc/runtime.toml", phase="something_new"
)


# ── layer 1: the parse ────────────────────────────────────────────────────────


def test_absent_key_parses_to_none():
    """An older daemon (< 2.34.0) and a clean load are deliberately
    indistinguishable, and both must read as "fine" — the safe direction."""
    assert parse_status({"api_version": 1}).runtime_config_degraded is None


def test_non_dict_parses_to_none():
    for junk in (None, [], "degraded", 7):
        assert parse_status({"runtime_config_degraded": junk}).runtime_config_degraded is None


def test_all_four_fields_are_parsed():
    st = parse_status(
        {
            "runtime_config_degraded": {
                "reason": "malformed",
                "path": "/etc/control-ofc/runtime.toml",
                "detail": "expected `=`, found `:` at line 4",
                "phase": "startup",
            }
        }
    )
    d = st.runtime_config_degraded
    assert d is not None
    assert (d.reason, d.path, d.phase) == ("malformed", "/etc/control-ofc/runtime.toml", "startup")
    assert d.detail == "expected `=`, found `:` at line 4"


def test_unknown_field_is_dropped_not_fatal():
    """Forward compatibility: a newer daemon extending the object must not break
    an older GUI."""
    st = parse_status(
        {"runtime_config_degraded": {"reason": "malformed", "phase": "startup", "future": "x"}}
    )
    assert st.runtime_config_degraded is not None
    assert st.runtime_config_degraded.reason == "malformed"


# ── layer 2: the headless message ─────────────────────────────────────────────


def test_no_degradation_yields_no_message():
    assert runtime_config_degraded_message(None) is None


def test_startup_and_reload_do_not_share_a_message():
    """The safety-critical property. Only the boot load seeds `header_roles`, so
    a `reload` failure leaves them intact — saying otherwise is a false alarm
    about pump protection, and saying nothing about `startup` hides a real loss.

    Asserted as a RELATIONSHIP between the two, so collapsing the branches into
    one message fails here however that message is worded."""
    startup = runtime_config_degraded_message(STARTUP)
    reload_ = runtime_config_degraded_message(RELOAD)
    assert startup is not None and reload_ is not None
    assert startup != reload_

    # The startup case asserts the loss outright...
    assert "NOT in effect" in startup
    # ...the reload case states the narrower true thing (a reload never restores
    # roles) without asserting either that they are gone or that they are safe.
    assert "does not restore" in reload_
    assert "NOT in effect" not in reload_


def test_no_message_ever_claims_pump_protection_is_intact():
    """**The regression for the review P1.** `phase` is latest-wins in the daemon
    (`main.rs:600-601` overwrites unconditionally), so a *failed* reload replaces
    an earlier startup record: the roles can already be gone while the record
    reads `reload`. An earlier draft of the reload message said "Fan header roles
    you assigned are unaffected", which is then a false safety reassurance inside
    the one feature that exists to prevent exactly that.

    Asserted over every phase, including the unknown one, because the GUI cannot
    distinguish the histories and so may never reassure in any of them."""
    for degraded in (STARTUP, RELOAD, UNKNOWN_PHASE):
        msg = runtime_config_degraded_message(degraded)
        assert msg is not None
        assert "unaffected" not in msg, f"{degraded.phase!r} message reassures about roles"
        assert "30%" in msg, f"{degraded.phase!r} message must name the protection at risk"


def test_unknown_phase_warns_without_asserting_the_loss():
    """Over-warning erodes the banner, under-warning hides a real one. An
    unrecognised phase names the possibility ("may not be") rather than the fact."""
    msg = runtime_config_degraded_message(
        RuntimeConfigDegraded(reason="malformed", path="/x.toml", phase="something_new")
    )
    assert msg is not None
    assert "may not be in effect" in msg
    assert "NOT in effect" not in msg


def test_unrecognised_reason_token_is_rendered_not_dropped():
    """The daemon owns the vocabulary and may extend it; "failed for a reason
    this GUI has not heard of" is still worth saying."""
    msg = runtime_config_degraded_message(
        RuntimeConfigDegraded(reason="quarantined", path="/x.toml", phase="startup")
    )
    assert msg is not None and "quarantined" in msg


def test_empty_path_is_omitted_rather_than_rendered_blank():
    """Asserted as a relationship between the two messages: the separator must
    leave with the path. A bare "no em dash present" check cannot work here — the
    message body legitimately contains one."""
    with_path = runtime_config_degraded_message(
        RuntimeConfigDegraded(reason="malformed", path="/x.toml", phase="startup")
    )
    without = runtime_config_degraded_message(
        RuntimeConfigDegraded(reason="malformed", phase="startup")
    )
    assert with_path is not None and without is not None
    assert "/x.toml" in with_path
    assert with_path.replace(" — /x.toml", "") == without


def test_detail_is_not_put_in_the_banner():
    """`detail` is verbatim daemon prose and can be a multi-line TOML error; the
    page logs it instead. A banner is not a log viewer."""
    msg = runtime_config_degraded_message(STARTUP)
    assert msg is not None and STARTUP.detail not in msg


# ── layer 3: the CALL SITE ────────────────────────────────────────────────────
# Extracting the message into a testable function does NOT test the wiring. These
# drive `AppState.set_status`, the real signal the page connects to, rather than
# calling `_on_status_updated` directly — which would skip the connection, i.e.
# exactly the thing most likely to be broken.


def _banner(page: DashboardPage) -> QWidget:
    b = page.findChild(QWidget, "Dashboard_Banner_runtime_config")
    assert b is not None, "the dashboard must carry a runtime-config banner"
    return b


def test_banner_visibility_tracks_the_wire_field(qtbot, app_state):
    """Asserted as a RELATIONSHIP (`shown == (field is not None)`) over BOTH
    branches, so neither a stuck predicate nor a hardcoded literal passes.

    **`isHidden()`, not `isVisible()` and not `isVisibleTo(page)`.** `isVisible()`
    is False for every widget under `QT_QPA_PLATFORM=offscreen`, so it would pass
    with the `show_warning` call deleted. `isVisibleTo(page)` fails for the
    opposite reason: `DashboardPage` is a `QStackedWidget` and this banner lives
    on the live page, which is not the current one in a bare test — the
    "`QStackedLayout` lays out ONLY the current page" trap. `isHidden()` reads the
    widget's own visibility flag, which is meaningful headless AND still
    discriminates: `ErrorBanner.__init__` calls `setVisible(False)`, so a deleted
    show leaves it hidden and this goes red."""
    page = DashboardPage(state=app_state)
    qtbot.addWidget(page)
    banner = _banner(page)
    assert banner.isHidden(), "precondition: the banner starts hidden"

    for status in (
        DaemonStatus(runtime_config_degraded=STARTUP),
        DaemonStatus(runtime_config_degraded=None),
        DaemonStatus(runtime_config_degraded=RELOAD),
    ):
        app_state.set_status(status)
        assert banner.isHidden() == (status.runtime_config_degraded is None)


def test_banner_text_is_the_view_model_message(qtbot, app_state):
    """A relationship against the VM, not a literal string: a call site that
    invented its own wording — or read the wrong field — fails here."""
    page = DashboardPage(state=app_state)
    qtbot.addWidget(page)
    banner = _banner(page)

    for degraded in (STARTUP, RELOAD):
        app_state.set_status(DaemonStatus(runtime_config_degraded=degraded))
        assert banner._message_label.text() == runtime_config_degraded_message(degraded)


def test_keyed_warning_is_raised_and_cleared(qtbot, app_state):
    page = DashboardPage(state=app_state)
    qtbot.addWidget(page)

    app_state.set_status(DaemonStatus(runtime_config_degraded=STARTUP))
    assert any(w["source"] == "daemon" for w in app_state.unacknowledged_warnings)

    app_state.set_status(DaemonStatus(runtime_config_degraded=None))
    assert app_state.warning_count == 0, "a repaired daemon must clear the condition"


def test_a_changed_degradation_re_raises_the_banner(qtbot, app_state):
    """The poll-diff keys on the whole identity, not a bool: a daemon that
    restarts into a *different* failure must not keep showing the stale message."""
    page = DashboardPage(state=app_state)
    qtbot.addWidget(page)
    banner = _banner(page)

    app_state.set_status(DaemonStatus(runtime_config_degraded=STARTUP))
    first = banner._message_label.text()
    app_state.set_status(DaemonStatus(runtime_config_degraded=RELOAD))
    assert banner._message_label.text() != first
    assert not banner.isHidden()
