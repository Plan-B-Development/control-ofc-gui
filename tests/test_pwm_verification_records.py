"""DEC-456 (`PTR-l`, `G121`): the daemon keeps each header's PWM-control verdict.

Before DEC-456 the only record of a fan-control test was the GUI's own
``last_pwm_verify_effective`` setting: one machine-wide value, blind to a test
run from anywhere else and to every characterisation sweep. A daemon advertising
``control.pwm_verification_records`` publishes the latest conclusive verdict on
each header, and the GUI derives the tri-state from those records.

Every call-site test below asserts the arm the pre-fix path CANNOT produce
(DEC-340): a daemon record that disagrees with the setting, and a report that
goes loud because of it. An arm where both sources agree would pass with the
wiring deleted.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from control_ofc.api.models import (
    Capabilities,
    DaemonStatus,
    HwmonHeader,
    PwmVerification,
    ReadinessItem,
    parse_capabilities,
    parse_hwmon_headers,
    parse_hwmon_inventory,
)
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.daemon_features import daemon_supports
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.pwm_verification import pwm_verification_tristate
from control_ofc.ui import cooling_readiness as cr
from control_ofc.ui.pages.settings_page import SettingsPage
from control_ofc.ui.pages.system_state_page import SystemStatePage
from tests.test_cooling_readiness import _hw
from tests.test_polling_service import _make_mock_client, _make_worker
from tests.test_system_state_health_noise import _healthy_gigabyte

ID = "hwmon:it8696:pci0:pwm1:SYS_FAN1"


def _header(state: str | None, *, writable: bool = True, hid: str = ID) -> HwmonHeader:
    return HwmonHeader(
        id=hid,
        is_writable=writable,
        pwm_verification=PwmVerification(state=state) if state is not None else None,
    )


def _caps(supported: bool) -> Capabilities:
    return parse_capabilities({"control": {"pwm_verification_records": supported}})


# ── The wire → the model ─────────────────────────────────────────────────


def test_a_published_record_is_parsed_onto_its_header():
    (verified, bare, malformed) = parse_hwmon_headers(
        {
            "headers": [
                {
                    "id": "a",
                    "pwm_verification": {
                        "header_id": "a",
                        "state": "verified",
                        "method": "verify",
                        "result": "effective",
                        "run_id": "",
                        "verified_unix_ms": 1,
                    },
                },
                {"id": "b"},
                {"id": "c", "pwm_verification": "verified"},
            ]
        }
    )
    assert verified.pwm_verification == PwmVerification(state="verified")
    assert bare.pwm_verification is None, "absent = not yet verified"
    assert malformed.pwm_verification is None, "a non-object record is no verdict"


def test_the_inventory_view_of_the_same_struct_builds_the_record_too():
    """`/inventory/hwmon .pwm_controls` is the same daemon struct; a bare
    `_filter_fields` there left the nested record as a raw dict."""
    inv = parse_hwmon_inventory(
        {"pwm_controls": [{"id": "a", "pwm_verification": {"state": "failed"}}]}
    )
    assert inv.pwm_controls[0].pwm_verification == PwmVerification(state="failed")


def test_the_capability_is_parsed_and_resolves_through_the_registry():
    """Asserted against the WIRE flag, the term that stays true independently
    of the registry lookup under test (DEC-334)."""
    assert _caps(True).control.pwm_verification_records is True
    assert daemon_supports("pwm_verification_records", _caps(True)) is True
    assert daemon_supports("pwm_verification_records", _caps(False)) is False
    assert daemon_supports("pwm_verification_records", Capabilities()) is False


# ── The tri-state ─────────────────────────────────────────────────────────


def test_no_verdict_anywhere_is_never_tested():
    assert pwm_verification_tristate([]) is None
    assert pwm_verification_tristate([_header(None), _header(None, hid="b")]) is None


def test_one_verified_header_is_verified():
    assert pwm_verification_tristate([_header("verified"), _header(None, hid="b")]) is True


def test_a_failure_outranks_a_pass():
    headers = [_header("verified"), _header("failed", hid="b")]
    assert pwm_verification_tristate(headers) is False
    assert pwm_verification_tristate(list(reversed(headers))) is False


def test_a_read_only_header_settles_nothing():
    """Presence first: the same record on a writable header does settle it."""
    assert pwm_verification_tristate([_header("failed")]) is False
    assert pwm_verification_tristate([_header("failed", writable=False)]) is None


def test_an_unrecognised_state_token_settles_nothing():
    assert pwm_verification_tristate([_header("inconclusive")]) is None


# ── The System State call site ───────────────────────────────────────────


def _page(qtbot, *, supported: bool, recorded: str, headers: list[HwmonHeader]):
    state = AppState()
    state.set_capabilities(_caps(supported))
    state.set_hwmon_headers(headers)
    svc = AppSettingsService()
    svc.update(last_pwm_verify_effective=recorded)
    diag_svc = DiagnosticsService(None)
    diag_svc.set_hw_diagnostics(_healthy_gigabyte())
    page = SystemStatePage(state=state, diagnostics_service=diag_svc, settings_service=svc)
    qtbot.addWidget(page)
    return page, state


def _flush(page):
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del page


def test_the_page_reads_the_daemon_records_over_the_setting(qtbot):
    """The daemon says a header failed; this GUI's setting says the last test
    it ran passed. Only a page reading the daemon's records answers False."""
    page, _state = _page(qtbot, supported=True, recorded="effective", headers=[_header("failed")])
    assert page._pwm_verified() is False
    _flush(page)


def test_an_older_daemon_still_reads_the_setting(qtbot):
    """The opposite arm: without the capability the headers carry no records a
    daemon vouched for, so the setting is the only evidence."""
    page, _state = _page(
        qtbot, supported=False, recorded="ineffective", headers=[_header("verified")]
    )
    assert page._pwm_verified() is False
    _flush(page)


def test_fresh_headers_re_render_an_open_report(qtbot):
    """The whole chain, from `AppState.set_hwmon_headers` to what the open
    report shows: a failed verdict arriving on the headers must make the
    report go loud without another diagnostics fetch.

    The direction is the one that discriminates (DEC-379's lesson): starting
    loud and going quiet would pass with the re-render deleted only if the
    report never refreshed at all — starting quiet, only a re-render can put
    "To fix" on screen.
    """
    page, state = _page(qtbot, supported=True, recorded="", headers=[_header("verified")])
    page._open_readiness_report()
    assert page._report_dialog is not None
    assert "To fix" not in page._report_dialog._browser.toPlainText(), "precondition: quiet"

    state.set_hwmon_headers([_header("failed")])
    assert "To fix" in page._report_dialog._browser.toPlainText()
    _flush(page)


# ── Settings: the GUI-owned record's reset ───────────────────────────────


def _settings_page(qtbot, *, supported: bool) -> SettingsPage:
    state = AppState()
    state.capabilities = _caps(supported)
    svc = AppSettingsService()
    svc.update(last_pwm_verify_effective="ineffective")
    page = SettingsPage(state=state, settings_service=svc)
    qtbot.addWidget(page)
    return page


def test_forget_result_is_offered_only_where_the_setting_is_read(qtbot):
    old = _settings_page(qtbot, supported=False)
    assert old._clear_pwm_verify_btn.isEnabled()
    assert old._clear_pwm_verify_btn.text() == "Forget result (ineffective)"
    assert old._row_sublabels["pwm_verify_result"].text() == (
        SettingsPage._PWM_VERIFY_SUBLABEL_LOCAL
    )

    new = _settings_page(qtbot, supported=True)
    assert not new._clear_pwm_verify_btn.isEnabled()
    assert new._clear_pwm_verify_btn.text() == "Forget result"
    assert new._row_sublabels["pwm_verify_result"].text() == (
        SettingsPage._PWM_VERIFY_SUBLABEL_DAEMON
    )


def test_capabilities_arriving_later_re_label_the_row(qtbot):
    page = _settings_page(qtbot, supported=False)
    assert page._clear_pwm_verify_btn.isEnabled()  # precondition
    page._state.set_capabilities(_caps(True))
    assert not page._clear_pwm_verify_btn.isEnabled()


# ── The readiness item ───────────────────────────────────────────────────


def test_a_failed_verdict_routes_to_the_test_and_its_own_guide_section():
    """Through the builder the Hardware page renders from: a re-test is the
    remedy, so it shares the unverified item's action — never an inert row."""
    failed, unverified = cr.build_readiness_items(
        _hw(
            items=[
                ReadinessItem(code="pwm_control_failed", severity="warning"),
                ReadinessItem(code="pwm_control_unverified", severity="info"),
            ]
        )
    )
    assert failed.action.target == "pwm_verify"
    assert failed.action == unverified.action
    assert cr.group_for("pwm_control_failed") == cr.GROUP_FANS
    assert failed.doc_url.endswith("#pwm-control-failed-verification")


# ── The poll worker re-reads the headers after a diagnostic ──────────────


def _worker():
    client = _make_mock_client()
    worker = _make_worker(client)
    worker._caps_interval = 1000
    return worker, client


def _poll(worker, client, *, verify_active: bool) -> None:
    client.poll.return_value = (
        DaemonStatus(overall_status="ok", verify_active=verify_active),
        [],
        [],
    )
    worker.poll()


def test_the_end_of_a_diagnostic_re_reads_the_headers_one_cycle_later():
    """A verify or sweep persists its verdict AFTER it releases the write
    pause, so the re-read waits one cycle past the `verify_active` true → false
    edge."""
    worker, client = _worker()
    _poll(worker, client, verify_active=False)
    _poll(worker, client, verify_active=True)
    _poll(worker, client, verify_active=True)
    assert client.hwmon_headers.call_count == 1  # precondition: the caps cycle only
    _poll(worker, client, verify_active=False)
    assert client.hwmon_headers.call_count == 1, "not on the edge itself"
    _poll(worker, client, verify_active=False)
    assert client.hwmon_headers.call_count == 2, "one cycle after the edge"
    _poll(worker, client, verify_active=False)
    assert client.hwmon_headers.call_count == 2, "once per edge"


def test_a_diagnostic_starting_is_not_an_edge():
    worker, client = _worker()
    for active in (False, False, True, True):
        _poll(worker, client, verify_active=active)
    assert client.hwmon_headers.call_count == 1


# ── The Hardware page's checklist follows the verdicts ───────────────────


def _hardware_page(qtbot):
    from control_ofc.ui.pages.hardware_page import HardwarePage

    state = AppState()
    page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state))
    qtbot.addWidget(page)
    fetches: list[bool] = []
    # The handler's call site is what is under test, so the fetch itself is
    # recorded rather than sent (there is no daemon here).
    page._fetch_readiness = lambda *, force=False: fetches.append(force)
    page._readiness_auto_fetched = True  # the checklist has been shown once
    return page, state, fetches


def test_a_changed_verdict_re_fetches_the_checklist(qtbot):
    page, state, fetches = _hardware_page(qtbot)
    state.set_hwmon_headers([_header(None)])
    state.set_hwmon_headers([_header(None)])
    assert fetches == [], "the first headers are the baseline; an unchanged set is not news"
    state.set_hwmon_headers([_header("verified")])
    assert fetches == [False], "a background fetch, never a user-visible rescan"
    state.set_hwmon_headers([_header("verified")])
    assert fetches == [False]
    state.set_hwmon_headers([_header("failed")])
    assert page._verification_seen == frozenset({(ID, "failed")})
    assert fetches == [False, False]


def test_a_checklist_never_shown_is_not_fetched_early(qtbot):
    """The first show fetches it fresh; fetching before would pre-empt that."""
    page, state, fetches = _hardware_page(qtbot)
    page._readiness_auto_fetched = False
    state.set_hwmon_headers([_header(None)])
    state.set_hwmon_headers([_header("verified")])
    assert fetches == []


def test_a_change_during_a_fetch_waits_for_its_answer(qtbot):
    """Never a second request over an outstanding one: the reply handler reads
    `_awaiting_rescan`, which a second request would overwrite."""
    page, state, fetches = _hardware_page(qtbot)
    state.set_hwmon_headers([_header(None)])
    page._readiness_in_flight = True
    state.set_hwmon_headers([_header("verified")])
    assert fetches == [], "precondition: nothing sent while one is outstanding"
    page._on_readiness_error("error", "gone")
    assert fetches == [False], "the pending re-fetch runs once the answer lands"
