"""AIO Phase 8 Batch 1 (DEC-333): safety preflight, control-path discovery, provenance.

The GUI half of `AIO-Phase7-Batch1.md` §7. Its daemon half lives in the paired
repo's `daemon/tests/discovery_phase8.rs`.

Three disciplines from `CLAUDE.md § Hard-won lessons` run through this file:

* **Assert a RELATIONSHIP, never a literal.** The pump fixture is a header the
  hardware labels ``AIO_PUMP`` that the user has downgraded to ``chassis_fan``.
  A test passing ``is_pump=True`` as a literal is satisfied by a call site that
  reads the wire ``role`` — the exact DEC-312 defect the line exists to prevent.
* **``isVisibleTo(parent)``, never ``isVisible()``.** Under
  ``QT_QPA_PLATFORM=offscreen`` nothing is shown, so ``isVisible()`` is ``False``
  for every widget and an assertion on it passes with ``setVisible(...)``
  deleted.
* **Test the call site, not just the extracted rule.** A view-model with
  thorough unit tests and nothing asserting the production path calls it is an
  untested rule.
"""

from __future__ import annotations

import json

from PySide6.QtWidgets import QCheckBox, QPushButton

from control_ofc.api.models import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_UNKNOWN,
    CONTROL_PATH_CONFIRMED,
    CONTROL_PATH_MULTIPLE,
    CONTROL_PATH_NO_RESPONSE,
    DIAGNOSTIC_CONTROL_PATH,
    PREFLIGHT_BLOCKED,
    PREFLIGHT_FAIL,
    PREFLIGHT_NOT_APPLICABLE,
    PREFLIGHT_PASS,
    PREFLIGHT_READY,
    PREFLIGHT_UNKNOWN,
    PREFLIGHT_WARN,
    PROVENANCE_COMMANDED,
    PROVENANCE_DERIVED,
    PROVENANCE_OBSERVED,
    VALIDATION_DIAG_CONTROL_PATH,
    Capabilities,
    ConnectionState,
    ControlCapability,
    ControlPathRecord,
    FanReading,
    HwmonHeader,
    SensorReading,
    ValidationEvidence,
    ValidationSession,
    parse_control_path_run,
    parse_control_path_status,
    parse_preflight_report,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.control_path_view import (
    build_control_path_view,
    relationship_summary_line,
    restore_note,
)
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.header_inspector_view import build_header_inspector_view
from control_ofc.services.preflight_view import build_preflight_view
from control_ofc.services.provenance import (
    UNVERIFIABLE,
    classified_rows,
    classify,
    from_envelope,
)
from control_ofc.services.pump_protection import header_is_pump_protected
from control_ofc.services.validation_export import session_json
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.widgets.control_path_dialog import ControlPathDiscoveryDialog
from control_ofc.ui.widgets.pwm_header_card import PwmHeaderCard
from control_ofc.ui.widgets.validation_session_dialog import ValidationSessionDialog

# ── fixtures ─────────────────────────────────────────────────────────────────


def _caps(**control) -> Capabilities:
    control.setdefault("header_roles", True)
    control.setdefault("pwm_characterization", True)
    control.setdefault("cooling_devices", True)
    control.setdefault("validation_sessions", True)
    control.setdefault("control_path_discovery", True)
    control.setdefault("diagnostic_preflight", True)
    return Capabilities(control=ControlCapability(**control))


def _downgraded_pump(**kw) -> HwmonHeader:
    """A header the HARDWARE labels ``AIO_PUMP`` that the USER set to ``chassis_fan``.

    The whole point of this fixture (DEC-312). The wire ``role`` reads
    ``chassis_fan`` because a user assignment fully substitutes for inference in
    the *display* role — while the daemon's safety predicate is a UNION and
    still refuses to stop it. Any call site reading ``role == "pump"`` therefore
    gets the wrong answer here, and only here.
    """
    base = {
        "id": "hwmon:nct6799:isa-0a20:pwm5:AIO_PUMP",
        "label": "AIO_PUMP",
        "chip_name": "nct6799",
        "device_id": "isa-0a20",
        "pwm_index": 5,
        "supports_enable": True,
        "rpm_available": True,
        "is_writable": True,
        "role": "chassis_fan",
        "role_source": "user_assigned",
        "effective_min_pwm_pct": 30,
        "stop_permitted": False,
    }
    base.update(kw)
    return HwmonHeader(**base)


def _plain_fan(**kw) -> HwmonHeader:
    base = {
        "id": "hwmon:nct6799:isa-0a20:pwm1:CHA_FAN1",
        "label": "CHA_FAN1",
        "chip_name": "nct6799",
        "device_id": "isa-0a20",
        "pwm_index": 1,
        "supports_enable": True,
        "rpm_available": True,
        "is_writable": True,
        "role": "chassis_fan",
        "role_source": "label",
        "effective_min_pwm_pct": 0,
        "stop_permitted": True,
    }
    base.update(kw)
    return HwmonHeader(**base)


def _preflight_payload(**overrides) -> dict:
    checks = [
        {"check_id": "target_discoverable", "state": PREFLIGHT_PASS, "detail": "Header found"},
        {"check_id": "header_role", "state": PREFLIGHT_PASS, "detail": "Role 'pump'"},
        {"check_id": "pwm_writable", "state": PREFLIGHT_PASS, "detail": "PWM is writable"},
        {"check_id": "pwm_readback", "state": PREFLIGHT_PASS, "detail": "Reads back 45%"},
        {"check_id": "control_ownership", "state": PREFLIGHT_PASS, "detail": "Available"},
        {"check_id": "safe_minimum", "state": PREFLIGHT_PASS, "detail": "30%-100%"},
        {"check_id": "temperature_source", "state": PREFLIGHT_PASS, "detail": "cpu · 300 ms"},
        {"check_id": "thermal_state", "state": PREFLIGHT_PASS, "detail": "No failsafe"},
        {"check_id": "reclaim_state", "state": PREFLIGHT_PASS, "detail": "No reclaim"},
        {"check_id": "original_state", "state": PREFLIGHT_PASS, "detail": "Captured 45%"},
        {"check_id": "supporting_cooling", "state": PREFLIGHT_PASS, "detail": "2 of 2 running"},
    ]
    payload = {
        "header_id": _downgraded_pump().id,
        "diagnostic": DIAGNOSTIC_CONTROL_PATH,
        "verdict": PREFLIGHT_READY,
        "checks": checks,
        "blocking": [],
    }
    payload.update(overrides)
    return payload


def _run_payload(**overrides) -> dict:
    payload = {
        "run_id": "path-1",
        "header_id": _downgraded_pump().id,
        "state": "complete",
        "delta_pct": 25,
        "requested_cycles": 2,
        "window_seconds": 6,
        "baseline_pct": 40,
        "perturbed_pct": 65,
        "direction": "up",
        "channels": [
            {"tach_id": "pump", "label": "AIO_PUMP", "is_target_header": True},
            {"tach_id": "fan2", "label": "fan2", "monitor_only": True},
            {"tach_id": "fan3", "label": "fan3", "monitor_only": True},
        ],
        "cycles": [
            {
                "cycle": 1,
                "baseline_pct": 40,
                "perturbed_pct": 65,
                "direction": "up",
                "observations": [
                    {
                        "tach_id": "pump",
                        "baseline_rpm": 1350,
                        "perturbed_rpm": 1775,
                        "delta_rpm": 425,
                        "noise_floor_rpm": 50,
                        "responded": True,
                    },
                    {
                        "tach_id": "fan2",
                        "baseline_rpm": 900,
                        "perturbed_rpm": 905,
                        "delta_rpm": 5,
                        "noise_floor_rpm": 50,
                        "responded": False,
                    },
                    {
                        "tach_id": "fan3",
                        "baseline_rpm": 700,
                        "perturbed_rpm": 700,
                        "delta_rpm": 0,
                        "noise_floor_rpm": 50,
                        "responded": False,
                    },
                ],
            }
        ],
        "summary": {
            "relationship": CONTROL_PATH_CONFIRMED,
            "confidence": CONFIDENCE_HIGH,
            "candidates": [
                {
                    "tach_id": "pump",
                    "label": "AIO_PUMP",
                    "monitor_only": False,
                    "confidence": CONFIDENCE_HIGH,
                    "direction": "positive",
                    "baseline_rpm": 1350,
                    "perturbed_rpm": 1775,
                    "change_pct": 31.4,
                    "cycles_responded": 2,
                    "cycles_total": 2,
                }
            ],
            "measurement_resolution_ms": 1000,
            "sample_interval_ms": 500,
            "sample_count": 24,
            "confidence_notes": [],
        },
        "original_pct": 40,
        "restore_failed": False,
        "restore_outcome": "restored",
        "detail": None,
        "completed_unix_ms": 1_700_000_000_000,
    }
    payload.update(overrides)
    return payload


def _page(qtbot, *, client=None, caps=None):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_capabilities(caps if caps is not None else _caps())
    pump, fan = _downgraded_pump(), _plain_fan()
    state.set_hwmon_headers([pump, fan])
    state.set_fans([FanReading(id=pump.id, rpm=1350), FanReading(id=fan.id, rpm=900)])
    state.set_sensors([SensorReading(id="cpu:pkg", label="CPU Package", value_c=54.0)])
    page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state), client=client)
    qtbot.addWidget(page)
    return page, state


# ── §7: model parsing and confidence serialisation ──────────────────────────


class TestModelParsing:
    def test_a_run_parses_with_its_confidence_and_candidates(self):
        run = parse_control_path_run(_run_payload())
        assert run.run_id == "path-1"
        assert run.summary is not None
        assert run.summary.relationship == CONTROL_PATH_CONFIRMED
        assert run.summary.confidence == CONFIDENCE_HIGH
        assert run.summary.candidates[0].change_pct == 31.4
        assert run.summary.measurement_resolution_ms == 1000
        assert run.channels[1].monitor_only is True

    def test_an_unknown_relationship_token_survives_parsing(self):
        """273-i: render an unrecognised token, never drop it."""
        run = parse_control_path_run(
            _run_payload(summary={"relationship": "future_shape", "confidence": "very_high"})
        )
        assert run.summary.relationship == "future_shape"
        view = build_control_path_view(run, header_label="AIO Pump")
        # Visible verbatim, and toned neutrally rather than as a failure.
        assert "future_shape" in view.relationship_word.lower().replace(" ", "_")
        assert view.relationship_tone == "muted"

    def test_missing_measurement_resolution_parses_as_unknown_not_zero(self):
        """§4: UNKNOWN rather than a guess — and `0 ms` is a guess."""
        payload = _run_payload()
        del payload["summary"]["measurement_resolution_ms"]
        run = parse_control_path_run(payload)
        assert run.summary.measurement_resolution_ms is None
        view = build_control_path_view(run, header_label="AIO Pump")
        assert "unknown" in view.resolution_text.lower()

    def test_a_status_carries_persisted_records(self):
        status = parse_control_path_status(
            {
                "run": _run_payload(),
                "records": [
                    {
                        "header_id": _downgraded_pump().id,
                        "relationship": CONTROL_PATH_CONFIRMED,
                        "confidence": CONFIDENCE_HIGH,
                        "tach_labels": ["fan5"],
                        "validated_unix_ms": 1_700_000_000_000,
                    }
                ],
            }
        )
        assert status.run is not None
        assert status.record_for(_downgraded_pump().id).confidence == CONFIDENCE_HIGH
        assert status.record_for("nope") is None

    def test_an_empty_status_is_not_an_error(self):
        status = parse_control_path_status({})
        assert status.run is None
        assert status.records == []


# ── §7: multiple / no-response / not-a-failure ──────────────────────────────


class TestRelationshipRendering:
    def test_multiple_responding_tachs_are_represented(self):
        payload = _run_payload()
        payload["summary"]["relationship"] = CONTROL_PATH_MULTIPLE
        payload["summary"]["candidates"].append(
            {
                "tach_id": "fan2",
                "label": "fan2",
                "monitor_only": True,
                "confidence": CONFIDENCE_HIGH,
                "direction": "positive",
                "baseline_rpm": 900,
                "perturbed_rpm": 1400,
                "change_pct": 55.5,
                "cycles_responded": 2,
                "cycles_total": 2,
            }
        )
        view = build_control_path_view(parse_control_path_run(payload), header_label="Pump")
        assert len(view.candidates) == 2
        assert {c.tach_id for c in view.candidates} == {"pump", "fan2"}
        # A monitor-only channel is labelled as such — it has no PWM of its own,
        # which is the §2 case the whole monitor-only read path exists for.
        assert any(c.monitor_only for c in view.candidates)

    def test_no_response_is_informational_never_a_failure(self):
        """The Overview forbids calling an unexpected RPM response a hardware fault."""
        payload = _run_payload()
        payload["summary"]["relationship"] = CONTROL_PATH_NO_RESPONSE
        payload["summary"]["confidence"] = CONFIDENCE_LOW
        payload["summary"]["candidates"] = []
        view = build_control_path_view(parse_control_path_run(payload), header_label="Pump")
        assert view.relationship_tone == "info"
        assert view.relationship_tone not in {"crit", "bad"}
        assert view.candidates == []
        # ...and it still says what it looked at, so "no response" is evidence
        # rather than an empty screen.
        assert len(view.quiet) == 3

    def test_unreadable_tachs_are_unknown_not_a_low_confidence_answer(self):
        payload = _run_payload()
        payload["summary"]["relationship"] = CONTROL_PATH_NO_RESPONSE
        payload["summary"]["confidence"] = CONFIDENCE_UNKNOWN
        payload["summary"]["candidates"] = []
        view = build_control_path_view(parse_control_path_run(payload), header_label="Pump")
        assert view.confidence_word == "Unknown"
        assert view.confidence_tone == "muted"

    def test_a_failed_restore_is_surfaced_prominently(self):
        """§1: "Restoration failure must be surfaced prominently"."""
        run = parse_control_path_run(
            _run_payload(restore_failed=True, restore_outcome="write_failed")
        )
        assert "could not be returned" in restore_note(run)
        view = build_control_path_view(run, header_label="Pump")
        assert view.restore_warning

    def test_a_thermal_skip_says_do_not_touch_it(self):
        """The header is high because the ladder put it there — say so, and say
        not to override it. `skipped_thermal_force` must never read as a fault."""
        run = parse_control_path_run(
            _run_payload(restore_failed=True, restore_outcome="skipped_thermal_force")
        )
        note = restore_note(run)
        assert "Thermal safety" in note
        assert "Do not set it manually" in note

    def test_a_clean_run_raises_no_restore_warning(self):
        """The opposite branch — without it a note stuck on "always warn" passes."""
        assert restore_note(parse_control_path_run(_run_payload())) == ""

    def test_an_unrecognised_restore_outcome_still_warns(self):
        run = parse_control_path_run(
            _run_payload(restore_failed=True, restore_outcome="something_new")
        )
        assert "something_new" in restore_note(run)


# ── §7: GUI displays a blocked preflight reason ─────────────────────────────


class TestPreflightView:
    def test_a_ready_preflight_permits_the_run(self):
        view = build_preflight_view(parse_preflight_report(_preflight_payload()))
        assert view.verdict == PREFLIGHT_READY
        assert view.can_start is True
        assert view.blocked is False
        assert len(view.rows) == 11
        assert all(r.tone == "ok" for r in view.rows)

    def test_a_blocked_preflight_names_its_reason_and_stops_the_run(self):
        checks = _preflight_payload()["checks"]
        checks[7] = {
            "check_id": "thermal_state",
            "state": PREFLIGHT_FAIL,
            "detail": "Thermal safety is forcing fan output (emergency)",
        }
        view = build_preflight_view(
            parse_preflight_report(
                _preflight_payload(
                    verdict=PREFLIGHT_BLOCKED, checks=checks, blocking=["thermal_state"]
                )
            )
        )
        assert view.blocked is True
        assert view.can_start is False
        assert view.blocking_reasons == ["Thermal safety is forcing fan output (emergency)"]
        row = next(r for r in view.rows if r.check_id == "thermal_state")
        assert row.is_blocking is True
        assert row.tone == "crit"

    def test_a_blocker_the_daemon_did_not_publish_as_a_check_still_gets_a_reason(self):
        """`P8-aj`: the one case where the reason matters most.

        `blocking_reasons` was built only from rows, and rows come only from
        `checks` — so a daemon naming a blocker it did not also publish rendered
        "Cannot run this test:" followed by a bullet and nothing. Start was
        correctly refused and the user was told nothing.

        The discriminating arm is a blocker with NO matching check. A blocker
        that does have one is the pre-fix answer by construction and cannot show
        the fallback fired.
        """
        view = build_preflight_view(
            parse_preflight_report(
                _preflight_payload(
                    verdict=PREFLIGHT_BLOCKED,
                    blocking=["some_future_guard"],
                )
            )
        )
        assert view.blocked is True
        assert view.can_start is False
        # Precondition: the daemon really did not publish it as a check, or this
        # asserts nothing about the fallback.
        assert all(r.check_id != "some_future_guard" for r in view.rows)
        assert view.blocking_reasons == ["Some future guard"], (
            "a named blocker with no matching check must still produce a reason; "
            f"got {view.blocking_reasons}"
        )

    def test_a_published_blocker_still_uses_its_own_detail_not_the_fallback(self):
        """The complement: the fallback must not displace a real detail, and must
        not duplicate a reason that already came from a row."""
        checks = _preflight_payload()["checks"]
        checks[7] = {
            "check_id": "thermal_state",
            "state": PREFLIGHT_FAIL,
            "detail": "Thermal safety is forcing fan output (emergency)",
        }
        view = build_preflight_view(
            parse_preflight_report(
                _preflight_payload(
                    verdict=PREFLIGHT_BLOCKED, checks=checks, blocking=["thermal_state"]
                )
            )
        )
        assert view.blocking_reasons == ["Thermal safety is forcing fan output (emergency)"], (
            "a published blocker must keep its detail and must not be listed twice"
        )

    def test_the_view_reads_the_daemon_verdict_rather_than_re_deriving_it(self):
        """§6.1: "the GUI reflects daemon decisions".

        A row marked ``fail`` with the daemon reporting ``ready`` is a contract
        the GUI must not overrule — if it re-derived the roll-up, the two would
        disagree and the user would see the GUI's answer. This asserts the
        deliberate direction of that disagreement.
        """
        checks = _preflight_payload()["checks"]
        checks[0] = {"check_id": "target_discoverable", "state": PREFLIGHT_FAIL, "detail": "x"}
        view = build_preflight_view(
            parse_preflight_report(
                _preflight_payload(verdict=PREFLIGHT_READY, checks=checks, blocking=[])
            )
        )
        assert view.blocked is False
        assert view.can_start is True

    def test_an_unknown_verdict_with_named_blockers_still_blocks(self):
        """`verdict` is matched exactly, so a future token outside
        {ready, warn, blocked} would fail OPEN on the one field that gates a
        hardware-perturbing action. Reading the daemon's own `blocking[]` as
        well — a second daemon-authored field, not a re-derivation — makes that
        case fail safe instead of by luck."""
        checks = _preflight_payload()["checks"]
        checks[7] = {"check_id": "thermal_state", "state": PREFLIGHT_FAIL, "detail": "hot"}
        view = build_preflight_view(
            parse_preflight_report(
                _preflight_payload(
                    verdict="some_future_verdict",
                    checks=checks,
                    blocking=["thermal_state"],
                )
            )
        )
        assert view.blocked is True
        assert view.can_start is False
        # The token itself is still rendered verbatim (273-i), not swallowed.
        assert "future verdict" in view.verdict_word.lower()

    def test_unknown_and_not_applicable_are_muted_never_bad(self):
        """§5's mirror rule: lack of evidence is not a failure either."""
        checks = [
            {"check_id": "supporting_cooling", "state": PREFLIGHT_NOT_APPLICABLE, "detail": "n/a"},
            {"check_id": "pwm_readback", "state": PREFLIGHT_UNKNOWN, "detail": "?"},
        ]
        view = build_preflight_view(
            parse_preflight_report(_preflight_payload(checks=checks, verdict=PREFLIGHT_READY))
        )
        assert [r.tone for r in view.rows] == ["muted", "muted"]
        assert view.can_start is True

    def test_a_warning_preflight_still_permits_the_run(self):
        checks = _preflight_payload()["checks"]
        checks[6] = {
            "check_id": "temperature_source",
            "state": PREFLIGHT_WARN,
            "detail": "Every temperature reading is stale",
        }
        view = build_preflight_view(
            parse_preflight_report(_preflight_payload(verdict="warn", checks=checks))
        )
        assert view.can_start is True
        assert view.blocked is False

    def test_an_unavailable_preflight_does_not_block(self):
        """An older daemon has no preflight route. Refusing the diagnostic on
        that basis would make the feature less usable than before it existed —
        and the daemon still runs its own guards on the POST."""
        view = build_preflight_view(None, unavailable_reason="no route")
        assert view.unavailable is True
        assert view.blocked is False
        assert view.can_start is True

    def test_an_unrecognised_check_id_is_rendered_not_dropped(self):
        view = build_preflight_view(
            parse_preflight_report(
                _preflight_payload(
                    checks=[{"check_id": "future_check", "state": "pass", "detail": "d"}]
                )
            )
        )
        assert len(view.rows) == 1
        assert view.rows[0].label == "Future check"


# ── §7: GUI exposes Discover Control Path ───────────────────────────────────


class TestHeaderCardExposesDiscovery:
    def test_the_card_has_a_discover_button_wired_to_the_page(self, qtbot):
        page, _ = _page(qtbot)
        card = next(
            c for c in page.findChildren(PwmHeaderCard) if c.header_id() == _downgraded_pump().id
        )
        btn = card.findChild(QPushButton, f"HeaderCard_Btn_discover_{_slug(card.header_id())}")
        assert btn is not None
        assert btn.text() == "Discover Control Path"
        assert btn.isEnabled()

        seen: list[str] = []
        card.discover_requested.connect(seen.append)
        # `.click()`, not the handler: invoking the handler skips the connection,
        # which is the thing most likely to be broken.
        btn.click()
        assert seen == [_downgraded_pump().id]

    def test_the_button_is_disabled_with_a_reason_on_an_older_daemon(self, qtbot):
        page, _ = _page(qtbot, caps=_caps(control_path_discovery=False))
        card = next(iter(page.findChildren(PwmHeaderCard)))
        btn = card.findChild(QPushButton, f"HeaderCard_Btn_discover_{_slug(card.header_id())}")
        assert btn.isEnabled() is False
        # A greyed control with no reason is indistinguishable from a broken one.
        assert "2.39.0" in btn.toolTip()

    def test_a_read_only_header_cannot_be_discovered(self):
        view = build_header_inspector_view(_plain_fan(is_writable=False), capabilities=_caps())
        assert view.can_discover is False
        assert "read-only" in view.discover_disabled_reason

    def test_a_header_with_no_tach_of_its_own_can_still_be_discovered(self):
        """Degraded, not blocked: discovery watches every OTHER tach too, and
        "this header drives fan3" is exactly what such a header most needs."""
        view = build_header_inspector_view(_plain_fan(rpm_available=False), capabilities=_caps())
        assert view.can_discover is True
        assert view.discover_disabled_reason == ""


# ── §6.3: the relationship row on the card ──────────────────────────────────


class TestRelationshipRow:
    def test_the_row_is_absent_until_something_has_been_discovered(self, qtbot):
        page, _ = _page(qtbot)
        card = next(iter(page.findChildren(PwmHeaderCard)))
        _expand_details(card)
        label = card.findChild(object, f"HeaderCard_Relationship_{_slug(card.header_id())}")
        assert label is not None
        # `isVisibleTo(parent)`, NOT `isVisible()`: offscreen makes the latter
        # False for every widget, so an assertion on it passes with the
        # setVisible call deleted (measured).
        #
        # The Details disclosure is EXPANDED first, or this would read False
        # because an ancestor is collapsed — passing whether or not the row's own
        # visibility rule works, which is no test at all.
        assert label.isVisibleTo(card) is False
        assert label.text() == ""

    def test_a_discovered_relationship_appears_with_its_confidence_and_stamp(self, qtbot):
        page, _ = _page(qtbot)
        page._control_paths = {
            _downgraded_pump().id: ControlPathRecord(
                header_id=_downgraded_pump().id,
                relationship=CONTROL_PATH_CONFIRMED,
                confidence=CONFIDENCE_HIGH,
                tach_ids=["fan5"],
                tach_labels=["fan5_input"],
                validated_unix_ms=1_700_000_000_000,
            )
        }
        page._refresh_cooling_section()
        card = next(
            c for c in page.findChildren(PwmHeaderCard) if c.header_id() == _downgraded_pump().id
        )
        # §6.3 puts the row inside the "Details" disclosure, so it is only
        # visible once that is open — which is the behaviour being asserted.
        _expand_details(card)
        label = card.findChild(object, f"HeaderCard_Relationship_{_slug(card.header_id())}")
        assert label.isVisibleTo(card) is True
        text = label.text()
        assert "fan5_input" in text
        assert "HIGH" in text
        assert "Last validated:" in text

    def test_unchanged_records_do_not_re_render_the_cards(self, qtbot):
        """While the dialog polls at 1 Hz the records are identical every tick,
        and the cooling section is already re-rendered by `fans_updated`. An
        unconditional refresh here would double the card work every second to
        draw bytes that had not changed."""
        from control_ofc.api.models import ControlPathStatus

        page, _ = _page(qtbot)
        record = ControlPathRecord(
            header_id=_downgraded_pump().id,
            relationship=CONTROL_PATH_CONFIRMED,
            confidence=CONFIDENCE_HIGH,
            tach_labels=["fan5"],
            validated_unix_ms=1_700_000_000_000,
        )
        calls: list[int] = []
        original = page._refresh_cooling_section
        page._refresh_cooling_section = lambda *a, **k: (calls.append(1), original())[1]

        def _get(records):
            # Only a GET carries `records` — a POST/DELETE reply is a bare run
            # the client wraps, and the two must not be confused.
            return ControlPathStatus(records=records, from_status_endpoint=True)

        page._on_discover_update(_get([record]))
        assert len(calls) == 1, "the first, changed batch must render"
        page._on_discover_update(_get([record]))
        assert len(calls) == 1, "an identical batch must not render again"

        moved = ControlPathRecord(**{**record.__dict__, "confidence": "low"})
        page._on_discover_update(_get([moved]))
        assert len(calls) == 2, "a genuinely changed batch must render"

        # A legitimately EMPTY GET clears the cache: the daemon holds no
        # relationships, and a card must not keep asserting one it dropped.
        page._on_discover_update(_get([]))
        assert len(calls) == 3
        assert page._control_paths == {}

        # ...but a POST/DELETE reply, which carries no records by construction,
        # must leave the cache alone rather than reading as "none".
        page._control_paths = {record.header_id: record}
        page._on_discover_update(ControlPathStatus(run=None, records=[]))
        assert page._control_paths == {record.header_id: record}
        assert len(calls) == 3, "a bare run reply must not re-render"

    def test_the_summary_line_names_extra_channels_rather_than_hiding_them(self):
        record = ControlPathRecord(
            relationship=CONTROL_PATH_MULTIPLE,
            tach_labels=["fan2", "fan3", "fan4"],
        )
        line = relationship_summary_line(record)
        assert "fan2" in line
        assert "+2 more" in line

    def test_no_record_yields_no_line(self):
        assert relationship_summary_line(None) == ""
        assert relationship_summary_line(ControlPathRecord()) == ""


# ── §7: the dialog's first state is the preflight ───────────────────────────


class TestBackgroundRefreshIsolation:
    """The page's background refresh and the dialog share one worker, and
    therefore one error signal. A failure belonging to the background request
    must never be delivered to the dialog as though its own poll had failed —
    that would stop the dialog polling a run still perturbing the header, and it
    would never render `restore_failed`."""

    def test_the_background_refresh_stands_down_while_the_dialog_is_open(self, qtbot):
        page, _ = _page(qtbot)
        # No thread is started: the worker-ensure is stubbed, because what is
        # under test is the suppression rule, not the worker lifecycle.
        page._ensure_discover_worker = lambda: True
        emitted: list[int] = []
        page._discover_poll_request.connect(lambda: emitted.append(1))

        page._refresh_control_paths()
        before = len(emitted)
        assert before == 1, "precondition: the refresh polls when nothing blocks it"

        page._discover_dialog = object()  # a dialog owns the worker
        try:
            page._refresh_control_paths()
            assert len(emitted) == before, (
                "the background refresh must not poll while the dialog does"
            )
        finally:
            page._discover_dialog = None

        # ...and it resumes once the dialog has closed. Without this branch a
        # guard that suppressed the refresh unconditionally would pass above.
        page._refresh_control_paths()
        assert len(emitted) == before + 1

    def test_the_refresh_is_gated_on_the_capability(self, qtbot):
        page, _ = _page(qtbot, caps=_caps(control_path_discovery=False))
        page._ensure_discover_worker = lambda: True
        emitted: list[int] = []
        page._discover_poll_request.connect(lambda: emitted.append(1))
        page._refresh_control_paths()
        assert emitted == []


class TestDialogPreflightGate:
    def test_a_blocked_preflight_disables_start_and_shows_why(self, qtbot):
        dialog = ControlPathDiscoveryDialog("h1", "AIO Pump", is_pump=True)
        qtbot.addWidget(dialog)
        checks = _preflight_payload()["checks"]
        checks[7] = {
            "check_id": "thermal_state",
            "state": PREFLIGHT_FAIL,
            "detail": "Thermal safety is forcing fan output (emergency)",
        }
        dialog.apply_preflight(
            parse_preflight_report(
                _preflight_payload(
                    verdict=PREFLIGHT_BLOCKED, checks=checks, blocking=["thermal_state"]
                )
            )
        )
        assert dialog._start_btn.isEnabled() is False
        assert "Thermal safety is forcing" in dialog._blocked_lbl.text()
        assert dialog._blocked_lbl.isVisibleTo(dialog) is True

    def test_a_ready_preflight_enables_start(self, qtbot):
        dialog = ControlPathDiscoveryDialog("h1", "AIO Pump", is_pump=True)
        qtbot.addWidget(dialog)
        dialog.apply_preflight(parse_preflight_report(_preflight_payload()))
        assert dialog._start_btn.isEnabled() is True
        assert dialog._blocked_lbl.isVisibleTo(dialog) is False

    def test_a_failed_preflight_fetch_leaves_start_available(self, qtbot):
        dialog = ControlPathDiscoveryDialog("h1", "AIO Pump", is_pump=True)
        qtbot.addWidget(dialog)
        dialog.apply_preflight_error("unavailable", "This daemon has no preflight.")
        assert dialog._start_btn.isEnabled() is True
        assert "no preflight" in dialog._blocked_lbl.text()

    def test_a_snapshot_for_another_header_is_ignored(self, qtbot):
        """One process-global slot serves this route, so a poll can legitimately
        return a different header's run. Rendering it under this dialog's label
        would attribute another header's relationship to this one."""
        dialog = ControlPathDiscoveryDialog("h1", "AIO Pump", is_pump=True)
        qtbot.addWidget(dialog)
        before = dialog._status_lbl.text()
        dialog.apply_run(parse_control_path_status({"run": _run_payload(header_id="other")}))
        assert dialog._status_lbl.text() == before

    def test_a_pump_dialog_promises_the_floor_a_non_pump_one_does_not(self, qtbot):
        pump = ControlPathDiscoveryDialog("h1", "AIO Pump", is_pump=True)
        fan = ControlPathDiscoveryDialog("h2", "Chassis", is_pump=False)
        qtbot.addWidget(pump)
        qtbot.addWidget(fan)
        assert "never be stopped" in pump._warnings.text()
        # The opposite branch, or a predicate stuck on True passes the first.
        assert "never be stopped" not in fan._warnings.text()


# ── call sites: the buttons invoke the EXISTING implementation ──────────────


class TestDiscoveryCallSite:
    def test_the_page_opens_the_dialog_with_the_UNION_pump_predicate(self, qtbot, monkeypatch):
        """The relationship, not the literal.

        ``_downgraded_pump`` reads ``role == "chassis_fan"`` on the wire while
        the daemon still refuses to stop it. Asserting ``is_pump is True`` would
        also pass for a call site reading the wire ``role`` and getting it wrong
        on a *different* header — so this asserts equality with the predicate,
        and the opposite branch below proves the assertion can fail.
        """
        page, _ = _page(qtbot)
        captured = {}

        class _FakeDialog:
            preflight_requested = _Sig()
            start_requested = _Sig()
            poll_requested = _Sig()
            cancel_requested = _Sig()

            def __init__(self, header_id, label, *, is_pump, parent=None):
                captured["header_id"] = header_id
                captured["is_pump"] = is_pump

            def request_preflight(self):
                captured["preflight_requested"] = True

            def exec(self):
                return 0

            def stop_polling(self):
                pass

        monkeypatch.setattr(
            "control_ofc.ui.pages.hardware_page.ControlPathDiscoveryDialog", _FakeDialog
        )
        monkeypatch.setattr(page, "_ensure_discover_worker", lambda: True)

        pump = _downgraded_pump()
        page._open_control_path_discovery(pump.id)
        assert captured["header_id"] == pump.id
        assert captured["is_pump"] == header_is_pump_protected(pump, _caps())
        assert captured["is_pump"] is True

        fan = _plain_fan()
        page._open_control_path_discovery(fan.id)
        assert captured["is_pump"] == header_is_pump_protected(fan, _caps())
        assert captured["is_pump"] is False

    def test_the_preflight_is_requested_before_the_modal_blocks(self, qtbot, monkeypatch):
        """§6.1 wants the preflight as the dialog's FIRST state. Asking after
        ``exec()`` returns would show "Ready" until the user had already read it.
        """
        page, _ = _page(qtbot)
        order: list[str] = []

        class _FakeDialog:
            preflight_requested = _Sig()
            start_requested = _Sig()
            poll_requested = _Sig()
            cancel_requested = _Sig()

            def __init__(self, *a, **kw):
                pass

            def request_preflight(self):
                order.append("preflight")

            def exec(self):
                order.append("exec")
                return 0

            def stop_polling(self):
                pass

        monkeypatch.setattr(
            "control_ofc.ui.pages.hardware_page.ControlPathDiscoveryDialog", _FakeDialog
        )
        monkeypatch.setattr(page, "_ensure_discover_worker", lambda: True)
        page._open_control_path_discovery(_downgraded_pump().id)
        assert order == ["preflight", "exec"]


class TestWorkerWiring:
    """Every request signal must actually reach the worker, and the worker's
    results must reach the open dialog.

    Asserted end to end through the real queued connections rather than by
    calling the slots — the connection is the thing most likely to be broken,
    and the page's `_ensure_discover_worker` closure is where it is made.
    """

    class _Client:
        """No socket is ever opened — the worker's lazily-built client is
        replaced with a fake after the thread starts, exactly as the Phase 6
        worker tests do. Every test tears the thread down in a `finally`."""

        socket_path = "/nonexistent/control-ofc-worker.sock"

    def test_requests_reach_the_worker_and_results_reach_the_dialog(self, qtbot):
        page, _ = _page(qtbot, client=self._Client())
        try:
            assert page._ensure_discover_worker() is True
            worker = page._discover_worker
            assert worker is not None
            calls: list[tuple] = []
            status = parse_control_path_status({"run": _run_payload(), "records": []})

            class _Fake:
                def diagnostic_preflight(self, header_id, diagnostic):
                    calls.append(("preflight", header_id, diagnostic))
                    return parse_preflight_report(_preflight_payload())

                def start_control_path_discovery(self, header_id, **kw):
                    calls.append(("start", header_id))
                    return status.run

                def control_path_status(self):
                    calls.append(("poll",))
                    return status

                def cancel_control_path_discovery(self):
                    calls.append(("cancel",))
                    return status.run

            worker._client = _Fake()

            dialog = ControlPathDiscoveryDialog(_downgraded_pump().id, "AIO_PUMP", is_pump=True)
            qtbot.addWidget(dialog)
            dialog._started = True
            page._discover_dialog = dialog

            page._discover_preflight_request.emit(_downgraded_pump().id, DIAGNOSTIC_CONTROL_PATH)
            qtbot.waitUntil(lambda: any(c[0] == "preflight" for c in calls), timeout=3000)
            assert calls[0][1] == _downgraded_pump().id
            assert calls[0][2] == DIAGNOSTIC_CONTROL_PATH

            page._discover_start_request.emit(_downgraded_pump().id)
            qtbot.waitUntil(lambda: any(c[0] == "start" for c in calls), timeout=3000)

            page._discover_poll_request.emit()
            qtbot.waitUntil(lambda: any(c[0] == "poll" for c in calls), timeout=3000)

            page._discover_cancel_request.emit()
            qtbot.waitUntil(lambda: any(c[0] == "cancel" for c in calls), timeout=3000)

            # ...and back: the worker's results reach the open dialog through the
            # page's slots, which is the other half of the same closure.
            qtbot.waitUntil(lambda: dialog._table.rowCount() > 0, timeout=3000)
            qtbot.waitUntil(lambda: len(dialog._preflight.rows) == 11, timeout=3000)
        finally:
            page._discover_dialog = None
            page.cleanup()
        # The new worker must be in `cleanup`'s teardown loop, or its thread
        # outlives the page.
        assert page._discover_worker is None and page._discover_thread is None

    def test_a_worker_error_reaches_the_dialog(self, qtbot):
        from control_ofc.api.errors import DaemonError

        page, _ = _page(qtbot, client=self._Client())
        try:
            assert page._ensure_discover_worker() is True
            worker = page._discover_worker

            class _Fake:
                def control_path_status(self):
                    raise DaemonError(
                        status=409, code="thermal_abort", message="Cannot run while hot"
                    )

            worker._client = _Fake()
            dialog = ControlPathDiscoveryDialog("h1", "AIO_PUMP", is_pump=True)
            qtbot.addWidget(dialog)
            page._discover_dialog = dialog

            page._discover_poll_request.emit()
            # A safety refusal is protection, not failure: the daemon's own words
            # are shown, undressed.
            qtbot.waitUntil(
                lambda: "Cannot run while hot" in dialog._status_lbl.text(), timeout=3000
            )
            assert "error" not in dialog._status_lbl.text().lower()
        finally:
            page._discover_dialog = None
            page.cleanup()


class TestClientContract:
    def test_the_client_calls_the_agreed_routes(self, monkeypatch):
        from control_ofc.api.client import DaemonClient

        client = DaemonClient.__new__(DaemonClient)
        calls: list[tuple[str, str, object]] = []

        def fake_get(path, **kw):
            calls.append(("GET", path, None))
            return _preflight_payload() if "preflight" in path else {"run": None, "records": []}

        def fake_post(path, json=None, **kw):
            calls.append(("POST", path, json))
            return _run_payload()

        def fake_delete(path, **kw):
            calls.append(("DELETE", path, None))
            return _run_payload()

        monkeypatch.setattr(client, "_get", fake_get, raising=False)
        monkeypatch.setattr(client, "_post", fake_post, raising=False)
        monkeypatch.setattr(client, "_delete", fake_delete, raising=False)

        client.diagnostic_preflight("hwmon:a:b:pwm1:X", DIAGNOSTIC_CONTROL_PATH)
        client.start_control_path_discovery("hwmon:a:b:pwm1:X")
        client.control_path_status()
        client.cancel_control_path_discovery()

        methods_paths = [(m, p) for m, p, _ in calls]
        assert methods_paths[1] == (
            "POST",
            "/hwmon/hwmon:a:b:pwm1:X/discover-control-path",
        )
        assert methods_paths[2] == ("GET", "/diagnostics/control-path")
        assert methods_paths[3] == ("DELETE", "/diagnostics/control-path")
        assert methods_paths[0][0] == "GET"
        assert methods_paths[0][1].startswith("/diagnostics/preflight?header=")
        assert f"diagnostic={DIAGNOSTIC_CONTROL_PATH}" in methods_paths[0][1]

    def test_the_start_call_sends_no_client_side_duty(self, monkeypatch):
        """The daemon owns the perturbation, the direction and the floor. A duty
        computed here would be a second copy of a safety rule."""
        from control_ofc.api.client import DaemonClient

        client = DaemonClient.__new__(DaemonClient)
        sent: dict = {}

        def fake_post(path, json=None, **kw):
            sent.update({"path": path, "body": json})
            return _run_payload()

        monkeypatch.setattr(client, "_post", fake_post, raising=False)
        client.start_control_path_discovery("h1")
        assert sent["body"] == {}


# ── §7: evidence provenance survives the export round-trip ──────────────────


class TestProvenance:
    def test_fixed_fields_classify_by_definition(self):
        assert classify("requested_pct") == PROVENANCE_COMMANDED
        assert classify("readback_pct") == PROVENANCE_OBSERVED
        assert classify("confidence") == PROVENANCE_DERIVED

    def test_an_unclassifiable_field_says_so_rather_than_guessing(self):
        assert classify("some_field_nobody_classified") == ""

    def test_a_daemon_envelope_wins_over_the_static_table(self):
        """The GUI must never overrule an observation the daemon actually made —
        that is the "silent promotion" the design principle forbids."""
        rows = classified_rows(
            {
                "requested_pct": 40,
                "rpm": {"value": 1284, "provenance": PROVENANCE_DERIVED},
            }
        )
        by_field = {f: (v, p) for f, v, p in rows}
        assert by_field["requested_pct"] == (40, PROVENANCE_COMMANDED)
        # Table says OBSERVED; the wire said DERIVED, and the wire wins.
        assert by_field["rpm"] == (1284, PROVENANCE_DERIVED)

    def test_a_plain_scalar_is_not_mistaken_for_an_envelope(self):
        assert from_envelope(1284) is None
        assert from_envelope({"value": 1}) is None
        assert from_envelope({"value": 1, "provenance": "OBSERVED"}) is not None

    def test_the_export_carries_the_provenance_legend(self):
        doc = json.loads(session_json(ValidationSession(session_id="v1")))
        assert "provenance" in doc
        assert doc["provenance"]["fields"]["requested_pct"] == PROVENANCE_COMMANDED
        assert doc["provenance"]["fields"]["readback_pct"] == PROVENANCE_OBSERVED
        assert set(doc["provenance"]["classifications"]) >= {
            PROVENANCE_COMMANDED,
            PROVENANCE_OBSERVED,
            PROVENANCE_DERIVED,
        }

    def test_the_export_names_what_software_cannot_establish(self):
        """§5: never let an untested item read as a pass — and an omitted row
        reads as a pass to most people."""
        doc = json.loads(session_json(ValidationSession(session_id="v1")))
        listed = {row["property"] for row in doc["provenance"]["unverified"]}
        assert listed == {key for key, _ in UNVERIFIABLE}
        assert "coolant_flow" in listed
        assert "physical_rpm" in listed

    def test_the_export_tags_each_discovery_value_with_its_provenance(self):
        """§3's worked example: field / value / provenance, attached to the values
        the entry really carries — not just a legend the reader must cross-apply.

        This is also what gives `classified_rows` (and through it `from_envelope`
        and `classify`) a PRODUCTION call site. A classifier exercised only by its
        own unit tests is decoration that looks covered — the DEC-301 trap.
        """
        session = ValidationSession(
            session_id="v1",
            evidence=[
                ValidationEvidence(
                    kind=VALIDATION_DIAG_CONTROL_PATH,
                    control_path=parse_control_path_run(_run_payload()),
                )
            ],
        )
        doc = json.loads(session_json(session))
        rows = {r["field"]: r["provenance"] for r in doc["evidence"][0]["provenance_rows"]}
        assert rows["relationship"] == PROVENANCE_DERIVED
        assert rows["confidence"] == PROVENANCE_DERIVED
        assert rows["measurement_resolution_ms"] == PROVENANCE_DERIVED
        # An unclassifiable field is omitted, not tagged UNKNOWN.
        assert "candidates" not in rows

    def test_an_evidence_entry_with_no_run_carries_no_provenance_rows(self):
        """The opposite branch — without it, code that always emitted the key
        would pass the test above."""
        session = ValidationSession(
            session_id="v1",
            evidence=[ValidationEvidence(kind="pwm_verify")],
        )
        doc = json.loads(session_json(session))
        assert "provenance_rows" not in doc["evidence"][0]

    def test_a_discovery_run_survives_the_export_round_trip(self):
        """§7: "evidence provenance survives API/export round-trip"."""
        session = ValidationSession(
            session_id="v1",
            evidence=[
                ValidationEvidence(
                    kind=VALIDATION_DIAG_CONTROL_PATH,
                    member_id=_downgraded_pump().id,
                    outcome="observed",
                    control_path=parse_control_path_run(_run_payload()),
                )
            ],
        )
        doc = json.loads(session_json(session))
        evidence = doc["evidence"][0]
        assert evidence["kind"] == VALIDATION_DIAG_CONTROL_PATH
        run = evidence["control_path"]
        # Verbatim: every verdict is the daemon's, recomputed nowhere.
        assert run["summary"]["relationship"] == CONTROL_PATH_CONFIRMED
        assert run["summary"]["confidence"] == CONFIDENCE_HIGH
        assert run["summary"]["measurement_resolution_ms"] == 1000
        assert run["summary"]["candidates"][0]["change_pct"] == 31.4


# ── the session dialog offers discovery only where the daemon has it ────────


class TestSessionDiagnosticGating:
    def test_the_choice_appears_when_the_daemon_supports_it(self, qtbot):
        dialog = ValidationSessionDialog(
            "aio0",
            "AIO",
            supported_diagnostics={
                "pwm_verify",
                "pwm_characterization",
                VALIDATION_DIAG_CONTROL_PATH,
            },
        )
        qtbot.addWidget(dialog)
        assert dialog.findChild(QCheckBox, f"Validation_Check_{VALIDATION_DIAG_CONTROL_PATH}")

    def test_the_choice_is_absent_on_an_older_daemon(self, qtbot):
        """An unknown token makes the daemon reject the WHOLE session, so an
        unfilterable box would break validation rather than degrade it."""
        dialog = ValidationSessionDialog(
            "aio0", "AIO", supported_diagnostics={"pwm_verify", "pwm_characterization"}
        )
        qtbot.addWidget(dialog)
        assert (
            dialog.findChild(QCheckBox, f"Validation_Check_{VALIDATION_DIAG_CONTROL_PATH}") is None
        )
        # ...and the two it does support are still offered.
        assert dialog.findChild(QCheckBox, "Validation_Check_pwm_verify") is not None

    def test_the_page_gates_the_choice_on_the_capability(self, qtbot):
        page, _ = _page(qtbot)
        assert VALIDATION_DIAG_CONTROL_PATH in page._supported_session_diagnostics()
        older, _ = _page(qtbot, caps=_caps(control_path_discovery=False))
        assert VALIDATION_DIAG_CONTROL_PATH not in older._supported_session_diagnostics()

    def test_the_evidence_disclosure_exists_and_starts_collapsed(self, qtbot):
        """§6.4: expose evidence and confidence "without overwhelming the
        primary view" — so it is disclosure, not a fourth always-open panel."""
        dialog = ValidationSessionDialog("aio0", "AIO")
        qtbot.addWidget(dialog)
        section = dialog.findChild(object, "Validation_Section_evidence")
        assert section is not None
        content = dialog.findChild(object, "Validation_Section_evidence_Content")
        assert content is not None
        assert content.isVisibleTo(section) is False


# ── helpers ─────────────────────────────────────────────────────────────────


def _slug(header_id: str) -> str:
    from control_ofc.ui.widgets.pwm_header_card import _slug as real_slug

    return real_slug(header_id)


def _expand_details(card) -> None:
    """Open a header card's "Details" disclosure.

    Clicks the section's own header button rather than reaching into private
    state, so the test exercises the same path a user does.
    """
    section = card.findChild(object, f"HeaderCard_Details_{_slug(card.header_id())}")
    assert section is not None
    button = card.findChild(QPushButton, f"HeaderCard_Details_{_slug(card.header_id())}_Header")
    assert button is not None
    if not button.isChecked():
        button.click()


class _Sig:
    """A stand-in for a Qt signal on a fake dialog."""

    def connect(self, *_a, **_kw):
        return None

    def emit(self, *_a, **_kw):
        return None
