"""DEC-357 — board/chip quirks stop being alarms.

The defect this pins: a static table match on ``(board vendor, chip prefix, CPU
vendor, board name)`` was escalated to a CRITICAL health card, so a perfectly
healthy X870E AORUS MASTER rendered a red "1 ISSUE REQUIRES ATTENTION" pill
against a HIGH advisory whose own text says the BIOS *may* override fan control.
Nothing the user did could clear it — ISA-18.2's definition of a nuisance alarm,
and the daemon's own readiness endpoint said ``overall: "info"`` at the same
instant.

Every test here is two-armed on purpose. A one-armed "it is not critical any
more" passes with the whole change deleted *and* with the feature deleted, and
it is the arm that proves promotion still works which discriminates between
them (``CLAUDE.md § Hard-won lessons``, DEC-340).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from control_ofc.api.models import (
    BoardInfo,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    KernelModuleInfo,
    ModuleCollisionInfo,
    ThermalSafetyInfo,
)
from control_ofc.services.app_settings_service import AppSettings
from control_ofc.services.system_state_view import (
    build_board_notes,
    build_condition_cards,
    build_system_state_vm,
    note_ack_key,
)
from control_ofc.ui.hwmon_guidance import (
    QUIRK_CONSEQUENCES,
    QUIRK_TRIGGERS,
    VENDOR_QUIRKS_DB,
    is_high_severity,
    quirk_key,
    severity_display,
)
from control_ofc.ui.widgets.readiness_report import (
    EVIDENCE_NOT_OBSERVED,
    EVIDENCE_OBSERVED,
    EVIDENCE_REFERENCE,
    EVIDENCE_UNVERIFIED,
    board_notes,
    detect_readiness_problems,
)

# The live machine this was reproduced on: a healthy Gigabyte X870E AORUS
# MASTER, 8/8 headers writable, no collision, no ACPI conflict, no reclaim. It
# matches the `gigabyte`/`it8696` HIGH quirk, which is the one that painted the
# card red.
_GIGABYTE = "Gigabyte Technology Co., Ltd."


def _healthy_gigabyte(**ov) -> HardwareDiagnosticsResult:
    defaults = dict(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(
                    chip_name="it8696",
                    expected_driver="it87",
                    in_mainline_kernel=False,
                    header_count=8,
                )
            ],
            total_headers=8,
            writable_headers=8,
        ),
        board=BoardInfo(vendor=_GIGABYTE, name="X870E AORUS MASTER"),
        kernel_modules=[KernelModuleInfo(name="it87", loaded=True, in_mainline=False)],
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        cpu_vendor="AMD",
    )
    defaults.update(ov)
    return HardwareDiagnosticsResult(**defaults)


def _msi_nct6798(**ov) -> HardwareDiagnosticsResult:
    """An MSI board carrying the one quirk family that risks damaging hardware."""
    defaults = dict(
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="nct6798", expected_driver="nct6775", header_count=5)
            ],
            total_headers=5,
            writable_headers=5,
        ),
        board=BoardInfo(vendor="Micro-Star International Co., Ltd.", name="MAG B450 TOMAHAWK"),
        kernel_modules=[KernelModuleInfo(name="nct6775", loaded=True, in_mainline=True)],
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        cpu_vendor="AMD",
    )
    defaults.update(ov)
    return HardwareDiagnosticsResult(**defaults)


_COLLISION = ModuleCollisionInfo(
    module_a="nct6687",
    module_b="nct6775",
    severity="critical",
    summary="Both drivers claim chip id 0xd450",
    remediation="blacklist nct6687",
)


# ── The headline: a matched quirk is not, by itself, a condition ───────────


def test_a_matched_quirk_alone_raises_no_condition_but_is_still_shown():
    """Arm 1 of the change: the healthy board goes quiet.

    The second assertion is what stops this passing vacuously if the quirk stopped
    matching altogether — the note must still be *there*, just not shouting.
    """
    diag = _healthy_gigabyte()
    notes = board_notes(diag)
    assert notes, "fixture must still match the quirk, or this proves nothing"
    assert any(is_high_severity(n.quirk.severity) for n in notes), (
        "and at least one must be a tier that used to escalate"
    )
    assert detect_readiness_problems(diag) == []


def test_the_pill_reads_ready_on_a_healthy_board_carrying_a_high_quirk():
    vm = build_system_state_vm(_healthy_gigabyte())
    assert vm.issue_count_state == "ok"
    assert vm.issue_count_label == "SYSTEM READY"
    assert vm.issue_cards == []
    assert vm.board_notes.total >= 1


def test_crit_state_requires_a_hardware_damage_mechanism():
    """Q1, stated as a relationship rather than a literal.

    `issue_count_state` must be "crit" exactly when some condition is critical —
    asserted against the conditions themselves, not against a hardcoded string,
    so a future condition that genuinely is critical keeps this honest.
    """
    for diag in (
        _healthy_gigabyte(),
        _msi_nct6798(),
        _msi_nct6798(module_collisions=[_COLLISION]),
    ):
        vm = build_system_state_vm(diag)
        any_critical = any(c.severity == "critical" for c in vm.issue_cards)
        assert (vm.issue_count_state == "crit") is any_critical
    # …and the arm that proves the predicate can fire at all.
    assert build_system_state_vm(
        _msi_nct6798(module_collisions=[_COLLISION])
    ).issue_count_state == ("crit")


# ── Evidence status ───────────────────────────────────────────────────────


def test_damage_note_is_observed_only_while_its_collision_is_present():
    """The two damage advisories say, in their own text, "if diagnostics detected
    the (nct6687, nct6775) collision". That conditional is now wired."""
    clean = {n.quirk.chip_prefix: n for n in board_notes(_msi_nct6798())}
    assert clean["nct6798"].evidence == EVIDENCE_NOT_OBSERVED

    collided = {
        n.quirk.chip_prefix: n for n in board_notes(_msi_nct6798(module_collisions=[_COLLISION]))
    }
    assert collided["nct6798"].evidence == EVIDENCE_OBSERVED
    assert collided["nct6798"].related_key == "module_collision"


def test_the_vm_builder_resolves_a_trigger_the_same_way_the_pure_layer_does():
    """`build_board_notes` derives `condition_keys` through its own path.

    Nothing exercised that path: every other evidence test calls `board_notes`
    directly, so a defect in how the VM builder supplies the condition set would
    have been invisible. Both arms, and asserted as a relationship against the
    pure layer rather than against literals — a builder that resolved triggers
    differently from `board_notes` is the drift this pins.
    """
    for diag in (_msi_nct6798(), _msi_nct6798(module_collisions=[_COLLISION])):
        pure = {n.key: n for n in board_notes(diag)}
        built = {n.key: n for n in build_board_notes(diag).notes}
        assert built and built.keys() == pure.keys()
        for key, row in built.items():
            assert (row.evidence, row.related_key) == (pure[key].evidence, pure[key].related_key)
    # The precondition that makes the loop mean something: the two arms really
    # do resolve the trigger differently. `BoardNoteVM` carries no quirk, so the
    # note is located by the key the pure layer assigns it.
    damage_key = next(
        n.key for n in board_notes(_msi_nct6798()) if n.quirk.trigger == "module_collision"
    )
    clean = {n.key: n for n in build_board_notes(_msi_nct6798()).notes}
    dirty = {
        n.key: n for n in build_board_notes(_msi_nct6798(module_collisions=[_COLLISION])).notes
    }
    assert clean[damage_key].evidence != dirty[damage_key].evidence
    assert dirty[damage_key].related_key == "module_collision"


def test_an_observed_note_with_an_owning_condition_does_not_mint_a_second_card():
    """The collision already has its own CRITICAL card; the note explains it.

    Printing both is the duplication the old rollup produced — a card saying
    "review the quirk notes above" while sorting itself above them.
    """
    diag = _msi_nct6798(module_collisions=[_COLLISION])
    keys = [p["key"] for p in detect_readiness_problems(diag)]
    assert "module_collision" in keys
    assert not [k for k in keys if k.startswith("quirk_")]
    assert len(keys) == len(set(keys))


def test_a_reclaim_absence_is_not_treated_as_counter_evidence():
    """The presence-before-absence trap, in its live form.

    `enable_revert_counts` only gains an entry when a reclaim is counted, so an
    empty map cannot distinguish "the BIOS never reclaimed" from "nothing has
    ever been written". Reading it as the former would let the page claim a quirk
    was refuted on a machine that has never attempted fan control.
    """
    note = next(n for n in board_notes(_healthy_gigabyte()) if n.quirk.trigger == "bios_revert")
    assert note.evidence == EVIDENCE_UNVERIFIED


@pytest.mark.parametrize(
    "verified,expected",
    [(None, EVIDENCE_UNVERIFIED), (True, EVIDENCE_NOT_OBSERVED), (False, EVIDENCE_OBSERVED)],
)
def test_a_fan_control_test_settles_a_reclaim_quirk(verified, expected):
    note = next(
        n
        for n in board_notes(_healthy_gigabyte(), pwm_control_verified=verified)
        if n.quirk.trigger == "bios_revert"
    )
    assert note.evidence == expected


def test_a_failed_test_promotes_a_quirk_no_other_condition_owns():
    """The "if it is important, the user should be made aware" half.

    Chosen deliberately over the not-observed arm: a note that finds nothing
    reads unverified *either way*, so only this direction can distinguish the
    promotion actually running from it never having been wired (DEC-340).
    """
    diag = _healthy_gigabyte()
    assert detect_readiness_problems(diag, pwm_control_verified=True) == []
    promoted = detect_readiness_problems(diag, pwm_control_verified=False)
    assert [p["key"] for p in promoted] == [f"quirk_{quirk_key(q)}" for q in _high_quirks(diag)]
    assert all(p["severity"] == "warn" for p in promoted), (
        "losing fan control is ACTION REQUIRED, never CRITICAL (Q1)"
    )


def _high_quirks(diag):
    return [
        n.quirk
        for n in board_notes(diag, pwm_control_verified=False)
        if n.evidence == EVIDENCE_OBSERVED and not n.related_key
    ]


def test_a_reference_only_note_never_promotes():
    """The two ASUS WMI advisories are sensor enrichment and say so themselves —
    they are never the PWM write path, so no test of it can confirm or refute
    them. They must stay reference material under every verify outcome."""
    diag = HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[HwmonChipInfo(chip_name="asus_wmi_sensors", header_count=0)],
            total_headers=1,
            writable_headers=1,
        ),
        board=BoardInfo(vendor="ASUSTeK COMPUTER INC.", name="PRIME X470-PRO"),
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        cpu_vendor="AMD",
    )
    for verified in (None, True, False):
        notes = board_notes(diag, pwm_control_verified=verified)
        assert notes, "fixture must match the ASUS advisory"
        assert all(n.evidence == EVIDENCE_REFERENCE for n in notes)
        assert not [
            p
            for p in detect_readiness_problems(diag, pwm_control_verified=verified)
            if p["key"].startswith("quirk_")
        ]


# ── Acknowledgement / dismissal lifecycle ─────────────────────────────────


def test_acknowledgement_cannot_reach_the_next_occurrence():
    """ISA-18.2 via DEC-282: an ack marks an occurrence, never a key.

    This is the user's requirement expressed as code — the warning must stop
    following them once they have dealt with it, *and* must speak again if it
    later turns out to be real.
    """
    diag = _healthy_gigabyte()
    note = next(n for n in board_notes(diag) if n.quirk.trigger == "bios_revert")
    ack = note_ack_key(note.key, note.evidence)

    quiet = build_board_notes(diag, acknowledged={ack})
    assert [n.acknowledged for n in quiet.notes if n.key == note.key] == [True]
    assert quiet.acknowledged_count == 1

    # Same note, now confirmed on this machine: a different occurrence.
    loud = build_board_notes(diag, pwm_control_verified=False, acknowledged={ack})
    assert [n.acknowledged for n in loud.notes if n.key == note.key] == [False]
    assert loud.acknowledged_count == 0


def test_dismissal_hides_the_note_but_is_counted_and_recoverable():
    diag = _healthy_gigabyte()
    note = next(n for n in board_notes(diag) if n.quirk.trigger == "bios_revert")
    ack = note_ack_key(note.key, note.evidence)

    before = build_board_notes(diag)
    after = build_board_notes(diag, dismissed={ack})
    assert note.key in {n.key for n in before.notes}
    assert note.key not in {n.key for n in after.notes}
    assert after.hidden_count == before.total - len(after.notes)
    assert after.total == before.total, "the note is hidden, not forgotten"


def test_a_dismissal_cannot_hide_the_note_once_it_is_confirmed():
    """Same guarantee as acknowledgement, and the more important of the two —
    a dismissal is the stronger silence."""
    diag = _healthy_gigabyte()
    note = next(n for n in board_notes(diag) if n.quirk.trigger == "bios_revert")
    ack = note_ack_key(note.key, note.evidence)
    promoted = build_board_notes(diag, pwm_control_verified=False, dismissed={ack})
    assert note.key in {n.key for n in promoted.notes}
    assert promoted.hidden_count == 0


def test_settings_toggles_gate_the_affordances():
    diag = _healthy_gigabyte()
    both = build_board_notes(diag)
    assert all(n.can_acknowledge and n.can_dismiss for n in both.notes)
    neither = build_board_notes(diag, allow_acknowledge=False, allow_dismiss=False)
    assert not any(n.can_acknowledge or n.can_dismiss for n in neither.notes)


# ── Presentation rules DEC-211 dropped ────────────────────────────────────


def test_notes_carry_the_four_hue_separation_not_the_pill_vocabulary():
    """DEC-158's hue map, restored. Asserted as a relationship against
    `severity_display`, so it stays true if a tier is re-coloured."""
    diag = _healthy_gigabyte()
    notes = build_board_notes(diag).notes
    assert notes
    for n in notes:
        assert n.severity_css == severity_display(n.severity).css_class
    # And the map really does separate them — a MEDIUM/LOW tier must not borrow
    # HIGH's class, which is what `_STATE_BY_CSS` collapsing CautionChip did.
    assert severity_display("medium").css_class != severity_display("high").css_class
    assert severity_display("info").css_class != severity_display("high").css_class


def test_detail_expansion_follows_the_severity_tier():
    """`SeverityDisplay.default_expanded` regains a production consumer.

    Both arms are needed: a stuck-open predicate passes the first alone.
    """
    diag = _healthy_gigabyte()
    notes = {n.severity: n for n in build_board_notes(diag).notes}
    assert notes, "fixture must produce notes"
    for severity, note in notes.items():
        assert note.default_expanded == severity_display(severity).default_expanded
    assert {severity_display(s).default_expanded for s in notes} == {True, False}, (
        "the fixture must exercise both arms, or this asserts nothing"
    )


def test_an_observed_note_opens_regardless_of_tier():
    diag = _msi_nct6798(module_collisions=[_COLLISION])
    note = next(n for n in build_board_notes(diag).notes if n.evidence == EVIDENCE_OBSERVED)
    assert note.default_expanded is True


# ── Registry integrity (DEC-334: sweep every entry, weight the silent ones) ─


def test_every_quirk_declares_a_registered_consequence_and_trigger():
    """An unregistered token fails *silently* — it simply never promotes, with
    no exception and no log line. That is the failure mode DEC-334 says to
    weight highest, so it is swept rather than trusted."""
    for q in VENDOR_QUIRKS_DB:
        assert q.consequence in QUIRK_CONSEQUENCES, f"{quirk_key(q)}: {q.consequence!r}"
        assert q.trigger in QUIRK_TRIGGERS, f"{quirk_key(q)}: {q.trigger!r}"


def test_every_trigger_names_a_condition_key_that_can_actually_be_emitted():
    """A trigger that no condition ever emits is a note that can never be
    promoted — the same silent failure one layer along. Asserted against the
    keys the detector really produces, not against a second hand-kept list."""
    emitted = set()
    for diag in (
        _msi_nct6798(module_collisions=[_COLLISION]),
        _healthy_gigabyte(
            hwmon=HwmonDiagnostics(
                chips_detected=[HwmonChipInfo(chip_name="it8696", header_count=8)],
                total_headers=8,
                writable_headers=8,
                enable_revert_counts={"pwm1": 3},
            ),
            expected_chips=["it8696", "it87952"],
        ),
    ):
        emitted |= {p["key"] for p in detect_readiness_problems(diag)}
    declared = {q.trigger for q in VENDOR_QUIRKS_DB if q.trigger}
    assert declared <= emitted, f"unreachable trigger(s): {declared - emitted}"


def test_every_quirk_declares_a_unique_stable_id():
    """The key an acknowledgement is stored against must identify exactly one note.

    This found a live defect when it was written: the obvious derivation — the
    four scope fields — collapses 36 entries to 30, because one board/chip pair
    legitimately carries several unrelated notes (four for MSI + nct6687 alone).
    Dismissing one would have silenced another.
    """
    assert all(q.id for q in VENDOR_QUIRKS_DB), (
        f"undeclared id: {[q.summary for q in VENDOR_QUIRKS_DB if not q.id]}"
    )
    keys = [quirk_key(q) for q in VENDOR_QUIRKS_DB]
    assert len(keys) == len(set(keys))
    # The arm that proves the collision is real, so the guard above is not
    # asserting something that was true anyway.
    scoped = [
        (q.vendor_pattern, q.chip_prefix, q.platform, q.board_pattern) for q in VENDOR_QUIRKS_DB
    ]
    assert len(set(scoped)) < len(scoped), "scope tuples must still collide, or this proves nothing"


def test_a_quirk_key_survives_prose_edits():
    from dataclasses import replace

    q = VENDOR_QUIRKS_DB[0]
    assert quirk_key(replace(q, summary="reworded", details=["changed"])) == quirk_key(q)


def test_the_new_settings_fields_round_trip_and_stay_machine_specific():
    s = AppSettings.from_dict(
        {
            "acknowledged_board_notes": ["a@unverified"],
            "dismissed_board_notes": ["b@reference"],
            "board_notes_allow_dismiss": False,
            "last_pwm_verify_effective": "effective",
        }
    )
    assert s.acknowledged_board_notes == ["a@unverified"]
    assert s.board_notes_allow_dismiss is False
    assert s.last_pwm_verify_effective == "effective"
    portable = s.portable_dict()
    for key in (
        "acknowledged_board_notes",
        "dismissed_board_notes",
        "last_pwm_verify_effective",
    ):
        assert key not in portable
    # An out-of-vocabulary outcome must not be read as evidence.
    assert (
        AppSettings.from_dict({"last_pwm_verify_effective": "probably"}).last_pwm_verify_effective
        == ""
    )


def test_condition_cards_and_notes_are_disjoint():
    """The merge DEC-211 introduced is gone, asserted on a board with both."""
    diag = _msi_nct6798(module_collisions=[_COLLISION])
    cards = build_condition_cards(diag)
    notes = build_board_notes(diag)
    assert cards and notes.notes
    assert not ({c.title for c in cards} & {n.title for n in notes.notes})


# ── Widget layer ──────────────────────────────────────────────────────────
#
# A default-constructed AppSettingsService is deliberate and safe: it is
# *unloaded*, and an unloaded service refuses to write to disk while still
# updating in memory (see its class docstring — three tests once wiped the
# developer's real config through exactly this path). So these exercise the
# real persistence call sites without touching ~/.config.


def _page(qtbot, diag=None):
    from control_ofc.services.app_settings_service import AppSettingsService
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.system_state_page import SystemStatePage

    svc = AppSettingsService()
    page = SystemStatePage(diagnostics_service=DiagnosticsService(None), settings_service=svc)
    qtbot.addWidget(page)
    page._render(diag if diag is not None else _healthy_gigabyte())
    return page, svc


def _flush(page):
    """Dispatch the DeferredDelete events a re-render posted.

    ``_clear_layout`` removes the old rows with ``deleteLater()``, and
    ``processEvents()`` deliberately does **not** dispatch DeferredDelete — so
    without this, ``findChild`` keeps returning the *previous* render's widget
    and an assertion about the new one is about a corpse. The live app never
    sees it because control returns to the event loop between the click and the
    next paint; only a synchronous test can observe the gap. Same mechanism
    `tests/conftest.py::_flush_deferred_deletes` uses at teardown (DEC-230).
    """
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del page


def _note_widgets(page, cls, prefix):
    return [w for w in page.findChildren(cls) if w.objectName().startswith(prefix)]


def test_notes_render_as_rows_and_not_as_issue_cards(qtbot):
    from PySide6.QtWidgets import QFrame

    page, _ = _page(qtbot)
    # DEC-356: the binding above is load-bearing — a dropped page reference lets
    # shiboken collect the wrapper and every __init__ connection with it. This
    # precondition uses it, so the lint cannot advise deleting the name.
    assert page.findChild(QFrame, "SystemState_Card_health") is not None
    assert _note_widgets(page, QFrame, "SystemState_BoardNote_")
    assert not _note_widgets(page, QFrame, "SystemState_IssueCard_")


def test_the_notes_section_is_collapsed_and_its_header_carries_the_count(qtbot):
    from PySide6.QtWidgets import QPushButton

    from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

    page, _ = _page(qtbot)
    section = page.findChild(CollapsibleSection, "SystemState_Section_boardNotes")
    assert section is not None
    header = page.findChild(QPushButton, "SystemState_Section_boardNotes_Header")
    total = build_board_notes(_healthy_gigabyte()).total
    assert total > 0
    assert str(total) in header.text(), (
        "progressive disclosure needs the count on the header — the reader "
        "decides whether to open it before opening it"
    )
    # Collapsed by default. Asserted on the header's checked state and the
    # content's own hidden flag, never `isVisible()`: under
    # QT_QPA_PLATFORM=offscreen that is False for every widget, so an assertion
    # on it passes with the collapse deleted (DEC-324).
    assert header.isChecked() is False
    assert section._content.isHidden() is True
    header.click()
    assert section._content.isHidden() is False


def test_acknowledging_a_note_persists_and_re_renders(qtbot):
    from PySide6.QtWidgets import QPushButton

    page, svc = _page(qtbot)
    note = next(n for n in build_board_notes(_healthy_gigabyte()).notes if n.can_acknowledge)
    btn = page.findChild(QPushButton, f"SystemState_BoardNoteAckBtn_{note.key}")
    assert btn is not None and btn.text() == "Acknowledge"
    # `.click()`, not the handler: the connection is the thing most likely broken.
    btn.click()
    assert svc.settings.acknowledged_board_notes == [note.ack_key]
    # The page re-rendered from the cached payload, so the button flipped.
    _flush(page)
    flipped = page.findChild(QPushButton, f"SystemState_BoardNoteAckBtn_{note.key}")
    assert flipped.text() == "Unacknowledge"
    flipped.click()
    assert svc.settings.acknowledged_board_notes == []


def test_dismissing_a_note_removes_its_row(qtbot):
    from PySide6.QtWidgets import QFrame, QPushButton

    page, svc = _page(qtbot)
    note = next(n for n in build_board_notes(_healthy_gigabyte()).notes if n.can_dismiss)
    before = len(_note_widgets(page, QFrame, "SystemState_BoardNote_"))
    page.findChild(QPushButton, f"SystemState_BoardNoteDismissBtn_{note.key}").click()
    assert svc.settings.dismissed_board_notes == [note.ack_key]
    _flush(page)
    assert page.findChild(QFrame, f"SystemState_BoardNote_{note.key}") is None
    assert len(_note_widgets(page, QFrame, "SystemState_BoardNote_")) == before - 1


def test_the_verify_row_appears_only_while_something_is_unverified(qtbot):
    """Q4: offer the one-click test only when it would settle something.

    Asserted on the row's own hidden flag, not `isVisibleTo` — the row sits
    inside a collapsed CollapsibleSection, whose content is genuinely hidden, so
    `isVisibleTo(card)` is False in both arms and would pass with
    `setVisible(...)` deleted. `isHidden()` reflects the widget's *own* explicit
    show/hide flag, which is exactly what `_rebuild_board_notes` sets.
    """
    from PySide6.QtWidgets import QWidget

    page, svc = _page(qtbot)
    row = page.findChild(QWidget, "SystemState_Row_verifyBoardNotes")
    assert row is not None
    assert build_board_notes(_healthy_gigabyte()).unverified_count > 0
    assert row.isHidden() is False

    svc.update(last_pwm_verify_effective="effective")
    page._render(_healthy_gigabyte())
    _flush(page)
    assert build_board_notes(_healthy_gigabyte(), pwm_control_verified=True).unverified_count == 0
    assert page.findChild(QWidget, "SystemState_Row_verifyBoardNotes").isHidden() is True


def test_a_mixed_verify_sweep_is_not_recorded_as_clean(qtbot):
    """One header that did not take the write is the case the note is about."""
    page, svc = _page(qtbot)
    page._record_verify_outcome([("pwm1", "effective"), ("pwm2", "no_rpm_effect")])
    assert svc.settings.last_pwm_verify_effective == "ineffective"
    page._record_verify_outcome([("pwm1", "effective"), ("pwm2", "effective")])
    assert svc.settings.last_pwm_verify_effective == "effective"


def test_a_failed_sweep_raises_a_condition_on_the_page(qtbot):
    from PySide6.QtWidgets import QFrame

    from control_ofc.ui.components.badges import StatusPill

    page, svc = _page(qtbot)
    assert page.findChild(StatusPill, "SystemState_Pill_issueCount").text() == "SYSTEM READY"
    page._record_verify_outcome([("pwm1", "pwm_enable_reverted")])
    pill = page.findChild(StatusPill, "SystemState_Pill_issueCount")
    assert pill.state() == "warn", "action required, never critical (Q1)"
    assert _note_widgets(page, QFrame, "SystemState_IssueCard_quirk_")
    del svc
