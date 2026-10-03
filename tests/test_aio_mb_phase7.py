"""AIO-MB Phase 7 (DEC-319): AIO awareness in the wizard, reservation in Controls.

Two features, one theme — **the cooling stack is one thing, and the rest of the
GUI should stop treating its members as loose fans**. The wizard learns not to
stop a pump it was never told about, and the Controls picker warns before taking
a fan out of a configured cooler.

The tests that matter most here are:

* ``TestCoolingDeviceUpsertMerges`` — ``POST /config/cooling-device`` REPLACES by
  id, so the wizard's upsert must read first or it silently destroys the name and
  advisory sensors a previous Configure AIO run stored. This is the phase's
  highest-consequence defect and the merge is the only thing standing in front of
  it.
* ``TestRoleClearIsConfirmed`` — clearing a user-assigned pump role is the ONLY
  operation in this phase that can *lower* a floor, which is what makes the diff
  ``[SAFETY]``.
* ``TestSelectAllRespectsExclusion`` — ``QCheckBox.setChecked`` works on a
  *disabled* box, so "Select All" would happily re-arm the pump the user just
  excluded. The control looks like it is doing nothing wrong.
"""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPushButton

from control_ofc.api.models import (
    Capabilities,
    ControlCapability,
    CoolingDevice,
    CoolingDeviceInventory,
    DevicePolicySummary,
    FanReading,
    HwmonHeader,
)
from control_ofc.services.controls_view import (
    ReservationNote,
    cooling_device_reservations,
)
from control_ofc.services.cooling_device_view import (
    COOLING_DEVICE_KIND_AIO,
    DEFAULT_COOLING_DEVICE_NAME,
    DETECTED_PUMP_TOOLTIP,
    CoolingMembership,
    cooling_member_index,
    find_cooling_device,
    membership_row_label,
    membership_row_tooltip,
    merge_cooling_device_payload,
)
from control_ofc.ui.widgets.fan_wizard import (
    PAGE_COOLING,
    PAGE_DISCOVERY,
    PAGE_INTRO,
    DiscoveryPage,
    FanConfigWizard,
)
from control_ofc.ui.widgets.member_editor import MemberEditorDialog

PUMP_ID = "hwmon:it8696:isa-0a40:pwm5:pwm5"
RAD_HWMON_ID = "hwmon:it8696:isa-0a40:pwm1:pwm1"
RAD_OPENFAN_ID = "openfan:ch03"
FREE_ID = "openfan:ch07"


def _caps(*, header_roles: bool = True, cooling_devices: bool = True) -> Capabilities:
    return Capabilities(
        control=ControlCapability(
            header_roles=header_roles,
            cooling_devices=cooling_devices,
        )
    )


def _header(header_id: str, *, role: str = "unknown", role_source: str = "none") -> HwmonHeader:
    return HwmonHeader(
        id=header_id,
        label=header_id.rsplit(":", 1)[-1],
        chip_name="it8696",
        is_writable=True,
        role=role,
        role_source=role_source,
    )


def _device(
    *,
    name: str = "My Loop",
    pump: str | None = PUMP_ID,
    radiators: list[str] | None = None,
    auxiliaries: list[str] | None = None,
    kind: str = COOLING_DEVICE_KIND_AIO,
) -> CoolingDevice:
    return CoolingDevice(
        id="aio-1",
        name=name,
        kind=kind,
        pump_member=pump,
        radiator_members=radiators if radiators is not None else [RAD_OPENFAN_ID],
        auxiliary_members=auxiliaries or [],
        preferred_sensor="cpu-package",
        fallback_sensor="mb-temp",
        coolant_sensor="coolant-1",
        device_policy=DevicePolicySummary(id="generic_pump", display_name="Generic pump"),
    )


# ---------------------------------------------------------------------------
# The membership index — built from the inventory, not from header fields
# ---------------------------------------------------------------------------


class TestCoolingMemberIndex:
    def test_openfan_radiator_is_indexed(self):
        """The case ``header.cooling_device_id`` structurally cannot cover.

        A radiator fan driven by an OpenFan channel has no ``HwmonHeader`` at
        all, so an index derived from header fields would silently miss it — and
        the wizard would then stop it looking for it.
        """
        index = cooling_member_index([_device()], [])
        assert RAD_OPENFAN_ID in index
        assert index[RAD_OPENFAN_ID].role == "radiator"
        assert index[PUMP_ID].role == "pump"

    def test_role_derived_membership_without_a_device(self):
        """Decision 4: a bare pump role reserves even with no device configured."""
        index = cooling_member_index([], [_header(PUMP_ID, role="pump")])
        assert index[PUMP_ID].role == "pump"
        assert index[PUMP_ID].from_device is False
        assert index[PUMP_ID].device_name == ""

    def test_a_configured_device_outranks_a_bare_role(self):
        index = cooling_member_index([_device()], [_header(PUMP_ID, role="pump")])
        assert index[PUMP_ID].from_device is True
        assert index[PUMP_ID].device_name == "My Loop"

    def test_unrelated_header_is_not_claimed(self):
        index = cooling_member_index([], [_header(RAD_HWMON_ID, role="chassis_fan")])
        assert index == {}

    def test_empty_against_a_daemon_with_no_role_model(self):
        """No capability gate is needed: the defaults produce an empty index."""
        assert cooling_member_index(None, [_header(RAD_HWMON_ID)]) == {}

    def test_auxiliary_members_are_claimed(self):
        index = cooling_member_index([_device(auxiliaries=[FREE_ID])], [])
        assert index[FREE_ID].role == "auxiliary"

    def test_unrecognised_role_token_still_renders(self):
        """273-i: a newer daemon's token must not make a member vanish."""
        index = cooling_member_index([_device(radiators=[], auxiliaries=[])], [])
        assert index[PUMP_ID].role_label == "Pump"


# ---------------------------------------------------------------------------
# THE ONE THAT MATTERS: replace-not-merge would destroy a user's setup
# ---------------------------------------------------------------------------


class TestCoolingDeviceUpsertMerges:
    def test_merge_preserves_everything_the_wizard_has_no_opinion_about(self):
        existing = _device(name="Mitch's Loop")
        payload = merge_cooling_device_payload(
            existing, pump_member=PUMP_ID, radiator_members=[RAD_HWMON_ID]
        )
        # The topology is the wizard's statement...
        assert payload["pump_member"] == PUMP_ID
        assert payload["radiator_members"] == [RAD_HWMON_ID]
        # ...and everything else survives, which is the whole point.
        assert payload["name"] == "Mitch's Loop"
        assert payload["preferred_sensor"] == "cpu-package"
        assert payload["fallback_sensor"] == "mb-temp"
        assert payload["coolant_sensor"] == "coolant-1"
        assert payload["device_policy_id"] == "generic_pump"

    def test_kind_is_preserved_not_forced_to_aio(self):
        """A user who described a custom loop does not get it relabelled."""
        payload = merge_cooling_device_payload(
            _device(kind="custom_loop"), pump_member=PUMP_ID, radiator_members=[]
        )
        assert payload["kind"] == "custom_loop"

    def test_create_supplies_defaults_and_nothing_else(self):
        payload = merge_cooling_device_payload(
            None, pump_member=PUMP_ID, radiator_members=[RAD_OPENFAN_ID]
        )
        assert payload["name"] == DEFAULT_COOLING_DEVICE_NAME
        assert payload["kind"] == COOLING_DEVICE_KIND_AIO
        # Nothing invented for fields there is no information about.
        assert "preferred_sensor" not in payload
        assert "device_policy_id" not in payload

    def test_empty_member_ids_are_dropped(self):
        payload = merge_cooling_device_payload(
            None, pump_member="", radiator_members=["", RAD_OPENFAN_ID]
        )
        assert payload["pump_member"] is None
        assert payload["radiator_members"] == [RAD_OPENFAN_ID]

    def test_find_cooling_device(self):
        assert find_cooling_device([_device()], "aio-1") is not None
        assert find_cooling_device([_device()], "nope") is None
        assert find_cooling_device(None, "aio-1") is None


# ---------------------------------------------------------------------------
# Reservation notes — two shapes of claim, two shapes of copy
# ---------------------------------------------------------------------------


class TestReservations:
    def test_device_membership_names_the_device(self):
        notes = cooling_device_reservations(cooling_member_index([_device()], []))
        assert "My Loop" in notes[PUMP_ID].text
        assert "Hardware page" in notes[PUMP_ID].tooltip

    def test_bare_role_does_not_claim_a_device_that_does_not_exist(self):
        """The copy must not assert an AIO the user never created."""
        notes = cooling_device_reservations(
            cooling_member_index([], [_header(PUMP_ID, role="pump", role_source="user_assigned")])
        )
        assert notes[PUMP_ID].text == "(Pump role assigned)"
        assert "Part of" not in notes[PUMP_ID].text
        assert DEFAULT_COOLING_DEVICE_NAME not in notes[PUMP_ID].tooltip

    def test_exempt_ids_lets_a_member_be_re_added(self):
        """Without this a removed radiator fan can never be put back.

        The row returns to the Available side still reserved, and warns about a
        device the fan is being *restored* to.
        """
        index = cooling_member_index([_device()], [])
        notes = cooling_device_reservations(index, exempt_ids=[RAD_OPENFAN_ID])
        assert RAD_OPENFAN_ID not in notes
        assert PUMP_ID in notes, "exempting one member must not exempt the rest"


# ---------------------------------------------------------------------------
# Feature 2 — the picker warns, but does not block
# ---------------------------------------------------------------------------


def _outputs() -> list[dict]:
    return [
        {"id": PUMP_ID, "source": "hwmon", "label": "PUMP", "clean_label": "PUMP"},
        {"id": RAD_OPENFAN_ID, "source": "openfan", "label": "Rad", "clean_label": "Rad"},
        {"id": FREE_ID, "source": "openfan", "label": "Free", "clean_label": "Free"},
    ]


class TestMemberEditorReservation:
    def _dialog(self, qtbot, **kw):
        notes = cooling_device_reservations(cooling_member_index([_device()], []))
        dlg = MemberEditorDialog([], _outputs(), {}, role_name="CPU", reserved=notes, **kw)
        qtbot.addWidget(dlg)
        return dlg

    def test_reserved_row_is_labelled_but_stays_enabled(self, qtbot):
        """Soft, unlike ``assigned_elsewhere`` — the user is told, not stopped."""
        dlg = self._dialog(qtbot)
        rows = {
            dlg._available_list.item(i).data(Qt.ItemDataRole.UserRole)[
                "id"
            ]: dlg._available_list.item(i)
            for i in range(dlg._available_list.count())
        }
        assert "(Part of: My Loop)" in rows[PUMP_ID].text()
        assert rows[PUMP_ID].flags() & Qt.ItemFlag.ItemIsEnabled
        assert rows[PUMP_ID].flags() & Qt.ItemFlag.ItemIsSelectable
        assert "(Part of" not in rows[FREE_ID].text()

    def test_a_hard_block_outranks_the_soft_one(self, qtbot):
        """A fan owned by another control cannot be taken, so there is nothing
        to ask about and the cooling note would only add noise."""
        notes = cooling_device_reservations(cooling_member_index([_device()], []))
        dlg = MemberEditorDialog(
            [], _outputs(), {PUMP_ID: "Other Role"}, role_name="CPU", reserved=notes
        )
        qtbot.addWidget(dlg)
        item = next(
            dlg._available_list.item(i)
            for i in range(dlg._available_list.count())
            if dlg._available_list.item(i).data(Qt.ItemDataRole.UserRole)["id"] == PUMP_ID
        )
        assert "Assigned to: Other Role" in item.text()
        assert "(Part of" not in item.text()
        assert not (item.flags() & Qt.ItemFlag.ItemIsEnabled)

    def test_declining_the_confirmation_does_not_add_the_fan(self, qtbot, monkeypatch):
        dlg = self._dialog(qtbot)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        item = next(
            dlg._available_list.item(i)
            for i in range(dlg._available_list.count())
            if dlg._available_list.item(i).data(Qt.ItemDataRole.UserRole)["id"] == PUMP_ID
        )
        item.setSelected(True)
        dlg._add_btn.click()
        assert dlg._selected_list.count() == 0
        assert PUMP_ID not in {m.member_id for m in dlg.get_members()}

    def test_accepting_the_confirmation_adds_the_fan(self, qtbot, monkeypatch):
        dlg = self._dialog(qtbot)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        item = next(
            dlg._available_list.item(i)
            for i in range(dlg._available_list.count())
            if dlg._available_list.item(i).data(Qt.ItemDataRole.UserRole)["id"] == PUMP_ID
        )
        item.setSelected(True)
        dlg._add_btn.click()
        assert PUMP_ID in {m.member_id for m in dlg.get_members()}

    def test_an_unreserved_fan_is_never_asked_about(self, qtbot, monkeypatch):
        dlg = self._dialog(qtbot)
        asked = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Yes,
        )
        item = next(
            dlg._available_list.item(i)
            for i in range(dlg._available_list.count())
            if dlg._available_list.item(i).data(Qt.ItemDataRole.UserRole)["id"] == FREE_ID
        )
        item.setSelected(True)
        dlg._add_btn.click()
        assert asked == []
        assert FREE_ID in {m.member_id for m in dlg.get_members()}


# ---------------------------------------------------------------------------
# Feature 1 — the wizard's AIO step and the exclusion it produces
# ---------------------------------------------------------------------------


class _WizardClient:
    """Records role and topology writes; serves back what it was given."""

    def __init__(self, *, headers=None, devices=None, assign_error: Exception | None = None):
        self.role_calls: list[tuple] = []
        self.device_calls: list[dict] = []
        self.headers_to_return = headers or []
        self.devices_to_return = devices or []
        self._assign_error = assign_error

    def set_header_role(self, header_id, role):
        if role is not None and self._assign_error is not None:
            raise self._assign_error
        self.role_calls.append((header_id, role))
        return None

    def hwmon_headers(self):
        return list(self.headers_to_return)

    def get_cooling_devices(self):
        return CoolingDeviceInventory(cooling_devices=list(self.devices_to_return))

    def set_cooling_device(self, device_id, **kw):
        self.device_calls.append({"id": device_id, **kw})
        return {"updated": True}

    def fan_identify(self, *a, **k):  # pragma: no cover - teardown only
        return None


@pytest.fixture
def wizard_state():
    from control_ofc.services.app_state import AppState

    state = AppState()
    state.capabilities = _caps()
    state.hwmon_headers = [_header(PUMP_ID), _header(RAD_HWMON_ID)]
    state.fans = [
        FanReading(id=PUMP_ID, source="hwmon", rpm=2163),
        FanReading(id=RAD_HWMON_ID, source="hwmon", rpm=927),
        FanReading(id=RAD_OPENFAN_ID, source="openfan", rpm=1029),
        FanReading(id=FREE_ID, source="openfan", rpm=1050),
    ]
    return state


class TestCoolingStepRouting:
    def test_step_is_skipped_without_the_role_capability(self, qtbot, wizard_state):
        """A pre-2.28.0 daemon sees exactly today's wizard."""
        wizard_state.capabilities = _caps(header_roles=False)
        wiz = FanConfigWizard(wizard_state)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_INTRO)
        wiz.restart()
        assert wiz.nextId() == PAGE_DISCOVERY
        assert wiz.excluded_reasons() == {}

    def test_step_is_shown_even_when_nothing_looks_like_an_aio(self, qtbot, wizard_state):
        """Decision 10 — the empty case is where nomination earns its keep.

        Every header on a label-less board reports ``role: unknown``, so a
        detection-gated step would be invisible on exactly the hardware that
        needs it.
        """
        wiz = FanConfigWizard(wizard_state)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_INTRO)
        wiz.restart()
        assert wiz.cooling_membership() == {}
        assert wiz.nextId() == PAGE_COOLING

    def test_cooling_step_leads_to_discovery(self, qtbot, wizard_state):
        wiz = FanConfigWizard(wizard_state)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        assert wiz.nextId() == PAGE_DISCOVERY


class TestExclusionReachesTheWizard:
    def _wizard(self, qtbot, wizard_state):
        wizard_state.cooling_devices = CoolingDeviceInventory(cooling_devices=[_device()])
        wiz = FanConfigWizard(wizard_state)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        return wiz

    def test_members_are_excluded_by_default(self, qtbot, wizard_state):
        wiz = self._wizard(qtbot, wizard_state)
        reasons = wiz.excluded_reasons()
        assert PUMP_ID in reasons
        assert RAD_OPENFAN_ID in reasons
        assert "My Loop" in reasons[PUMP_ID]

    def test_unticking_re_includes_a_member(self, qtbot, wizard_state):
        """A radiator fan is an ordinary fan and CAN safely be identified."""
        wiz = self._wizard(qtbot, wizard_state)
        cb, _ = wiz._cooling_page._exclude_rows[RAD_OPENFAN_ID]
        cb.setChecked(False)
        assert RAD_OPENFAN_ID not in wiz.excluded_reasons()
        assert PUMP_ID in wiz.excluded_reasons()

    def test_an_excluded_fan_is_not_identifiable(self, qtbot, wizard_state):
        wiz = self._wizard(qtbot, wizard_state)
        page = wiz._discovery_page
        page.initializePage()
        ids = [t["id"] for t in wiz._targets]
        assert PUMP_ID in ids, "precondition: the pump must be a target at all"
        selected = [ids[i] for i in page.selected_indices()]
        assert PUMP_ID not in selected
        assert FREE_ID in selected

    def test_excluded_row_is_shown_not_removed(self, qtbot, wizard_state):
        """Decision 9 — a fan that silently vanishes is indistinguishable from
        a fan that was never found."""
        wiz = self._wizard(qtbot, wizard_state)
        page = wiz._discovery_page
        page.initializePage()
        assert page._table.rowCount() == len(wiz._targets)
        row = next(
            i for i in range(page._table.rowCount()) if page._table.item(i, 1).text() == PUMP_ID
        )
        assert "My Loop" in page._table.item(row, 5).text()
        assert not page._checkboxes[row].isEnabled()

    def test_review_fallback_drops_excluded_targets(self, qtbot, wizard_state):
        """Reached when the user selects nothing: ``rows`` falls back to every
        target, and an excluded pump would reappear in the summary."""
        wiz = self._wizard(qtbot, wizard_state)
        wiz._selected_indices = []
        included = [t["id"] for t in wiz._review_page._included_targets()]
        assert PUMP_ID not in included
        assert FREE_ID in included


class TestSelectAllRespectsExclusion:
    def test_select_all_cannot_re_arm_an_excluded_fan(self, qtbot, wizard_state):
        """``QCheckBox.setChecked`` works on a DISABLED box.

        Without the guard in ``_set_all`` this passes silently and the pump is
        stopped anyway — the control looks like it is doing nothing wrong.
        """
        wizard_state.cooling_devices = CoolingDeviceInventory(cooling_devices=[_device()])
        wiz = FanConfigWizard(wizard_state)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        page = wiz._discovery_page
        page.initializePage()
        # `.click()` the real button, not `_set_all` — invoking the handler
        # skips the `clicked.connect` wiring, which is the thing most likely to
        # be broken, and this test guards a pump-safety property.
        btn = next(
            b for b in page.findChildren(QPushButton) if b.objectName() == "Wizard_Btn_selectAll"
        )
        btn.click()
        ids = [t["id"] for t in wiz._targets]
        assert PUMP_ID not in [ids[i] for i in page.selected_indices()]


class TestDiscoveryPageRederives:
    def test_the_table_is_rebuilt_on_re_entry(self, qtbot, wizard_state):
        """Back → change the AIO step → forward must produce a different table.

        Population used to happen in ``__init__``, which made the exclusion a
        snapshot taken before the user had made the choice.
        """
        excluded: dict[str, str] = {}
        page = DiscoveryPage(
            [{"id": PUMP_ID, "source": "hwmon", "rpm": 2163, "existing_label": ""}],
            wizard_state,
            exclusions=lambda: excluded,
        )
        qtbot.addWidget(page)
        assert page.selected_indices() == [0]

        excluded[PUMP_ID] = "Excluded — Part of My Loop"
        page.initializePage()
        assert page.selected_indices() == []
        assert page._table.item(0, 5).text() == "Excluded — Part of My Loop"

        excluded.clear()
        page.initializePage()
        assert page.selected_indices() == [0]

    def test_no_exclusions_behaves_exactly_as_before(self, qtbot, wizard_state):
        page = DiscoveryPage(
            [
                {"id": PUMP_ID, "source": "hwmon", "rpm": 1, "existing_label": ""},
                {"id": FREE_ID, "source": "openfan", "rpm": 2, "existing_label": ""},
            ],
            wizard_state,
        )
        qtbot.addWidget(page)
        assert page.selected_indices() == [0, 1]


# ---------------------------------------------------------------------------
# [SAFETY] — the role clear is the only thing here that can lower a floor
# ---------------------------------------------------------------------------


class TestRoleClearIsConfirmed:
    def _wizard(self, qtbot, wizard_state, client):
        wizard_state.hwmon_headers = [
            _header(PUMP_ID),
            _header(RAD_HWMON_ID, role="pump", role_source="user_assigned"),
        ]
        client.headers_to_return = list(wizard_state.hwmon_headers)
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        page = wiz._cooling_page
        index = page._pump_combo.findData(PUMP_ID)
        assert index >= 0, "precondition: the new pump must be offerable"
        page._pump_combo.setCurrentIndex(index)
        return wiz, page

    def test_an_apply_that_sends_nothing_still_rereads_the_headers(self, qtbot, wizard_state):
        """The review's P3: every write a no-op is skipped by the shared service,
        which then re-reads nothing — the page must not repopulate from headers
        up to ~300 s old."""
        client = _WizardClient()
        wizard_state.hwmon_headers = [
            _header(PUMP_ID, role="pump", role_source="user_assigned"),
            _header(RAD_HWMON_ID),
        ]
        client.headers_to_return = list(wizard_state.hwmon_headers)
        fetches = []
        real = client.hwmon_headers
        client.hwmon_headers = lambda: fetches.append(1) or real()
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        page = wiz._cooling_page
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        for i in range(page._radiator_list.count()):
            page._radiator_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        page._apply_btn.click()
        assert client.role_calls == [], "precondition: the pump write was a no-op"
        assert fetches == [1]
        assert page._status.text().startswith("Saved.")

    def test_a_declined_clear_sends_no_clear(self, qtbot, wizard_state, monkeypatch):
        client = _WizardClient()
        _wiz, page = self._wizard(qtbot, wizard_state, client)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        page._apply_nomination()
        assert (RAD_HWMON_ID, None) not in client.role_calls
        assert (PUMP_ID, "pump") in client.role_calls, "the assign still happens"
        assert "kept its role" in page._status.text()

    def test_an_accepted_clear_is_sent_after_the_assign(self, qtbot, wizard_state, monkeypatch):
        """Assign-before-clear is a SAFETY property, not iteration order.

        Clear-then-assign means a failed assign leaves the old header stripped
        of its role and the new one never given it — the pump loses its
        protection and nothing has replaced it.
        """
        client = _WizardClient()
        _wiz, page = self._wizard(qtbot, wizard_state, client)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        page._apply_nomination()
        assert (PUMP_ID, "pump") in client.role_calls
        assert (RAD_HWMON_ID, None) in client.role_calls
        assert client.role_calls.index((PUMP_ID, "pump")) < client.role_calls.index(
            (RAD_HWMON_ID, None)
        )

    def test_a_failed_assign_changes_nothing_at_all(self, qtbot, wizard_state, monkeypatch):
        client = _WizardClient(assign_error=OSError("daemon busy"))
        _wiz, page = self._wizard(qtbot, wizard_state, client)
        asked = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Yes,
        )
        page._apply_nomination()
        assert client.role_calls == []
        assert client.device_calls == [], "no topology is written either"
        assert asked == [], "the user is never asked to clear after a failed assign"

    def test_only_a_user_assigned_pump_role_is_ever_cleared(self, qtbot, wizard_state):
        """A role the daemon INFERRED is not ours to remove, and clearing it
        would not remove it anyway — a clear drops the stored assignment and
        falls back to exactly that inference."""
        wizard_state.hwmon_headers = [
            _header(PUMP_ID),
            _header(RAD_HWMON_ID, role="pump", role_source="label"),
        ]
        wiz = FanConfigWizard(wizard_state, client=_WizardClient())
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        assert wiz._cooling_page._stale_user_pumps(PUMP_ID) == []


class TestNominationWrites:
    def _page(self, qtbot, wizard_state, client):
        client.headers_to_return = list(wizard_state.hwmon_headers)
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        return wiz, wiz._cooling_page

    def test_the_pump_role_is_assigned(self, qtbot, wizard_state):
        client = _WizardClient()
        _wiz, page = self._page(qtbot, wizard_state, client)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        page._apply_nomination()
        assert (PUMP_ID, "pump") in client.role_calls

    def test_the_topology_is_upserted_by_read_modify_write(self, qtbot, wizard_state):
        """Decision 5 + 6 together: the wizard creates the device AND does not
        destroy what Configure AIO stored on it."""
        client = _WizardClient(devices=[_device(name="Mitch's Loop", radiators=[])])
        _wiz, page = self._page(qtbot, wizard_state, client)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        page._apply_nomination()
        assert len(client.device_calls) == 1
        call = client.device_calls[0]
        assert call["id"] == "aio-1"
        assert call["pump_member"] == PUMP_ID
        assert call["name"] == "Mitch's Loop"
        assert call["preferred_sensor"] == "cpu-package"
        assert call["coolant_sensor"] == "coolant-1"

    def test_an_openfan_radiator_gets_no_role_but_joins_the_topology(self, qtbot, wizard_state):
        """OpenFan channels have no header, so there is no role to set — but
        they are still part of the cooler."""
        client = _WizardClient()
        _wiz, page = self._page(qtbot, wizard_state, client)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        for i in range(page._radiator_list.count()):
            item = page._radiator_list.item(i)
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole) == RAD_OPENFAN_ID
                else Qt.CheckState.Unchecked
            )
        page._apply_nomination()
        assert not any(c[0] == RAD_OPENFAN_ID for c in client.role_calls)
        assert client.device_calls[0]["radiator_members"] == [RAD_OPENFAN_ID]

    def test_nothing_selected_writes_nothing(self, qtbot, wizard_state):
        client = _WizardClient()
        _wiz, page = self._page(qtbot, wizard_state, client)
        page._pump_combo.setCurrentIndex(0)  # "— none —"
        for i in range(page._radiator_list.count()):
            page._radiator_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        page._apply_nomination()
        assert client.role_calls == []
        assert client.device_calls == []

    def test_state_is_refreshed_so_the_next_page_sees_the_write(self, qtbot, wizard_state):
        """Both the headers and the inventory otherwise refresh on the ~300 s
        capability interval — long enough that the role the user just set would
        be invisible to the Detected Fans table that follows."""
        client = _WizardClient(devices=[_device()])
        _wiz, page = self._page(qtbot, wizard_state, client)
        client.headers_to_return = [_header(PUMP_ID, role="pump", role_source="user_assigned")]
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        page._apply_nomination()
        assert wizard_state.hwmon_headers[0].role == "pump"
        assert wizard_state.cooling_devices is not None


# ---------------------------------------------------------------------------
# Shape guards
# ---------------------------------------------------------------------------


class TestShapes:
    def test_membership_is_frozen(self):
        m = CoolingMembership(member_id="x", role="pump", role_label="Pump")
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.role = "chassis_fan"  # type: ignore[misc]

    def test_reservation_note_carries_its_own_title(self):
        note = cooling_device_reservations(cooling_member_index([_device()], []))[PUMP_ID]
        assert isinstance(note, ReservationNote)
        assert note.title
        assert note.tooltip


# ---------------------------------------------------------------------------
# Remediation of the DEC-319 review findings (round 1). Each of these fails
# against the code as first written — they are the regression guards for
# defects two reviewers found, not restatements of tests above.
# ---------------------------------------------------------------------------


class TestMergePreservesWhatTheCallerCannotSee:
    """Contract review finding 1: `None` must PRESERVE, not clear.

    `auxiliary_members` is a list no GUI surface displays, so a caller cannot
    have an opinion about it — defaulting it to `[]` silently zeroed it on the
    first wizard Apply, contradicting this function's own docstring.
    """

    def test_auxiliary_members_survive_a_topology_only_write(self):
        existing = _device(auxiliaries=["aux-1", "aux-2"])
        payload = merge_cooling_device_payload(
            existing, pump_member=PUMP_ID, radiator_members=[RAD_OPENFAN_ID]
        )
        assert payload["auxiliary_members"] == ["aux-1", "aux-2"]

    def test_an_explicit_empty_list_still_clears(self):
        """`[]` is a statement; only `None` is "no opinion"."""
        existing = _device(auxiliaries=["aux-1"])
        payload = merge_cooling_device_payload(
            existing, pump_member=PUMP_ID, radiator_members=[], auxiliary_members=[]
        )
        assert payload["auxiliary_members"] == []
        assert payload["radiator_members"] == []

    def test_radiators_are_preserved_when_the_caller_has_no_opinion(self):
        existing = _device(radiators=[RAD_OPENFAN_ID])
        payload = merge_cooling_device_payload(existing, pump_member=PUMP_ID)
        assert payload["radiator_members"] == [RAD_OPENFAN_ID]


class TestInvisibleDeviceMembersSurvive:
    """Contract review finding 2: absence from an unticked list is not a
    deselection when the row was never offered."""

    def test_a_radiator_the_picker_never_showed_is_not_dropped(self, qtbot, wizard_state):
        ghost = "openfan:ch99"  # in the device, absent from fans/headers
        client = _WizardClient(devices=[_device(radiators=[RAD_OPENFAN_ID, ghost])])
        client.headers_to_return = list(wizard_state.hwmon_headers)
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        page = wiz._cooling_page
        assert ghost not in page._offered_radiators(), "precondition: it must be invisible"
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        page._apply_nomination()
        assert ghost in client.device_calls[0]["radiator_members"]

    def test_an_offered_radiator_that_is_unticked_IS_dropped(self, qtbot, wizard_state):
        """The guard must not become 'nothing can ever be removed'."""
        client = _WizardClient(devices=[_device(radiators=[RAD_OPENFAN_ID])])
        client.headers_to_return = list(wizard_state.hwmon_headers)
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        page = wiz._cooling_page
        assert RAD_OPENFAN_ID in page._offered_radiators(), "precondition: it IS offered"
        for i in range(page._radiator_list.count()):
            page._radiator_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        page._apply_nomination()
        assert RAD_OPENFAN_ID not in client.device_calls[0]["radiator_members"]


class TestMembershipUsesTheUnionNotTheDisplayRole:
    """Contract review finding 3: the DEC-312 anti-pattern.

    Reading `role == "pump"` misses a header the hardware labels PUMP whose
    display role the user downgraded — the daemon still refuses to stop it, so
    the GUI's exclusion and reservation would silently disagree with the daemon
    about the same header.
    """

    def test_a_downgraded_but_still_protected_pump_is_claimed(self):
        header = HwmonHeader(
            id="hwmon:it8696:isa-0a40:pwm5:AIO_PUMP",
            label="AIO_PUMP",
            pwm_index=5,
            is_writable=True,
            role="chassis_fan",
            role_source="user_assigned",
        )
        from control_ofc.services.pump_protection import header_is_pump_protected

        caps = _caps()
        assert header_is_pump_protected(header, caps) is True, "precondition"
        index = cooling_member_index([], [header], caps)
        assert index[header.id].role == "pump"

    def test_an_ordinary_header_is_still_not_claimed(self):
        header = _header(RAD_HWMON_ID, role="chassis_fan", role_source="user_assigned")
        assert cooling_member_index([], [header], _caps()) == {}

    def test_the_daemons_stop_permitted_is_honoured_when_present(self):
        """`stop_permitted: False` is the daemon saying so; it outranks the role."""
        header = HwmonHeader(id=PUMP_ID, label="pwm5", pwm_index=5, is_writable=True)
        header.stop_permitted = False
        assert cooling_member_index([], [header], _caps())[PUMP_ID].role == "pump"


class TestReservationCopyFollowsTheEvidence:
    """Contract review of DEC-384: a pump the daemon protects WITHOUT an assignment.

    Since DEC-384 `stop_permitted: false` also means "the active profile names
    this fan a pump", which no role edit releases and which follows the ACTIVE
    profile rather than the one on screen. The bare-role copy said "role
    assigned … Clear the role in Configure AIO" for every non-device claim, so
    for such a header it claimed an assignment nobody made and offered a remedy
    that does nothing.

    The rule, asserted against the wire fields rather than against the index's
    own `assigned` flag (DEC-334's right-hand side): the "Configure AIO" remedy
    appears exactly when the header's role is `pump` by the user's assignment.
    """

    # Three pump claims with three different sources of evidence, and the one
    # ordinary header that must not be claimed at all.
    def _headers(self) -> list[HwmonHeader]:
        profile_named = HwmonHeader(  # no evidence of its own (DEC-384)
            id="hwmon:it8696:isa-0a40:pwm2:pwm2", label="pwm2", pwm_index=2, is_writable=True
        )
        profile_named.stop_permitted = False
        downgraded = HwmonHeader(  # DEC-312: labelled PUMP, assigned chassis_fan
            id="hwmon:it8696:isa-0a40:pwm5:AIO_PUMP",
            label="AIO_PUMP",
            pwm_index=5,
            is_writable=True,
            role="chassis_fan",
            role_source="user_assigned",
        )
        downgraded.stop_permitted = False
        assigned = _header(PUMP_ID, role="pump", role_source="user_assigned")
        assigned.stop_permitted = False
        return [profile_named, downgraded, assigned]

    def test_only_a_pump_assignment_is_offered_the_role_remedy(self):
        headers = self._headers()
        notes = cooling_device_reservations(cooling_member_index([], headers, _caps()))
        assert set(notes) == {h.id for h in headers}, "precondition: all three are claimed"
        for header in headers:
            note = notes[header.id]
            is_pump_assignment = header.role == "pump" and header.role_source == "user_assigned"
            assert ("Configure AIO" in note.tooltip) == is_pump_assignment, (header.id, note)
            assert ("assigned" in note.text) == is_pump_assignment, (header.id, note)

    def test_the_unassigned_copy_names_the_active_profile(self):
        """The DEC-384 reason must be discoverable from the note itself."""
        profile_named = self._headers()[0]
        note = cooling_device_reservations(cooling_member_index([], [profile_named], _caps()))[
            profile_named.id
        ]
        assert "active profile" in note.tooltip
        assert note.title == "Assign the pump to this curve?"


class TestPartialAssignIsReportedHonestly:
    """GUI review finding 1: a later assign failing does not mean nothing landed."""

    class _FailSecondClient(_WizardClient):
        def set_header_role(self, header_id, role):
            if role is not None and len(self.role_calls) >= 1:
                raise OSError("daemon busy")
            return super().set_header_role(header_id, role)

    def test_the_message_does_not_claim_nothing_changed(self, qtbot, wizard_state):
        client = self._FailSecondClient()
        client.headers_to_return = list(wizard_state.hwmon_headers)
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        page = wiz._cooling_page
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        # Tick an hwmon radiator so there are >= 2 assigns.
        for i in range(page._radiator_list.count()):
            item = page._radiator_list.item(i)
            item.setCheckState(
                Qt.CheckState.Checked
                if item.data(Qt.ItemDataRole.UserRole) == RAD_HWMON_ID
                else Qt.CheckState.Unchecked
            )
        page._apply_nomination()
        assert len(client.role_calls) == 1, "precondition: exactly one assign landed"
        text = page._status.text()
        assert "No roles were changed" not in text
        assert "already been saved" in text
        assert client.device_calls == [], "topology must not be written after a failure"

    def test_a_first_assign_failure_still_says_nothing_changed(self, qtbot, wizard_state):
        client = _WizardClient(assign_error=OSError("nope"))
        client.headers_to_return = list(wizard_state.hwmon_headers)
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        page = wiz._cooling_page
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        page._apply_nomination()
        assert "No roles were changed" in page._status.text()


# ---------------------------------------------------------------------------
# The Apply button itself (register row `AUD3-p`)
# ---------------------------------------------------------------------------


class TestApplyButtonIsWired:
    """[SAFETY] The only UI path that writes a pump-role nomination.

    Every test in `TestNominationWrites` above calls `page._apply_nomination()`
    directly, and `grep -n "_apply_btn" tests/test_aio_mb_phase7.py` returned
    nothing — so a broken, dropped or stale `clicked.connect` on `_apply_btn`
    left all of them green while the Apply button in the running app silently
    did nothing, i.e. pump nominations stopped being saved with no test failing.

    `TestSelectAllRespectsExclusion` in this same file already knows better and
    says why: "invoking the handler skips the `clicked.connect` wiring, which is
    the thing most likely to be broken". The class guarding the ROLE WRITES did
    not apply it.
    """

    def _page(self, qtbot, wizard_state, client):
        client.headers_to_return = list(wizard_state.hwmon_headers)
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        return wiz, wiz._cooling_page

    def test_clicking_apply_writes_the_pump_role(self, qtbot, wizard_state):
        client = _WizardClient()
        _wiz, page = self._page(qtbot, wizard_state, client)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))

        assert page._apply_btn.isEnabled()
        page._apply_btn.click()

        assert (PUMP_ID, "pump") in client.role_calls, (
            "the real button must reach the real role write"
        )
        assert page._status.text() == "Saved."

    def test_clicking_apply_writes_the_topology(self, qtbot, wizard_state):
        client = _WizardClient(devices=[_device(name="Mitch's Loop", radiators=[])])
        _wiz, page = self._page(qtbot, wizard_state, client)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        page._apply_btn.click()
        assert len(client.device_calls) == 1
        assert client.device_calls[0]["pump_member"] == PUMP_ID

    def test_clicking_apply_performs_the_confirmed_clear_after_the_assign(
        self, qtbot, wizard_state, monkeypatch
    ):
        """The clear path is the one that can REMOVE pump protection (`AIO7-c`),
        so it is the one that most needs driving through its real control."""
        # A stale user-assigned pump role on another header is what makes a
        # clear reachable at all.
        wizard_state.hwmon_headers = [
            _header(PUMP_ID),
            _header(RAD_HWMON_ID, role="pump", role_source="user_assigned"),
        ]
        client = _WizardClient()
        _wiz, page = self._page(qtbot, wizard_state, client)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        page._apply_btn.click()
        assert (PUMP_ID, "pump") in client.role_calls
        assert (RAD_HWMON_ID, None) in client.role_calls
        assert client.role_calls.index((PUMP_ID, "pump")) < client.role_calls.index(
            (RAD_HWMON_ID, None)
        ), "assign-before-clear is the safety property, and it must hold via the button too"

    def test_clicking_apply_with_nothing_selected_writes_nothing(self, qtbot, wizard_state):
        client = _WizardClient()
        _wiz, page = self._page(qtbot, wizard_state, client)
        page._pump_combo.setCurrentIndex(0)  # "— none —"
        for i in range(page._radiator_list.count()):
            page._radiator_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        page._apply_btn.click()
        assert client.role_calls == []
        assert client.device_calls == []
        assert "Nothing selected" in page._status.text()

    def test_clicking_apply_without_a_daemon_changes_nothing_and_says_so(self, qtbot, wizard_state):
        wiz = FanConfigWizard(wizard_state)  # no client
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        page = wiz._cooling_page
        page._apply_btn.click()
        assert "nothing was changed" in page._status.text()


# ---------------------------------------------------------------------------
# TS-ah / TS-ai: membership wording keyed on `assigned`
# ---------------------------------------------------------------------------

LABEL_PUMP_ID = "hwmon:it8696:isa-0a40:pwm2:AIO_PUMP"
KRAKEN_RAD_ID = "hwmon:kraken2023:usb-1:pwm2:pwm2"


def _labelled_pump() -> HwmonHeader:
    """A pump the daemon protects on its LABEL — nothing assigned."""
    h = _header(LABEL_PUMP_ID, role="pump", role_source="label")
    h.label = "AIO_PUMP"
    h.stop_permitted = False
    return h


def _assigned_pump() -> HwmonHeader:
    h = _header(PUMP_ID, role="pump", role_source="user_assigned")
    h.stop_permitted = False
    return h


class TestWizardRowsTellAssignedFromDetected:
    """`TS-ah`: an assigned pump and a labelled one used to read identically
    ("Pump"), so the user could not see why a header was pre-ticked."""

    def test_the_helper_names_how_each_pump_was_claimed(self):
        index = cooling_member_index([], [_assigned_pump(), _labelled_pump()], _caps())
        assert membership_row_label(index[PUMP_ID]) == "Pump (you assigned)"
        assert membership_row_label(index[LABEL_PUMP_ID]) == "Pump (detected)"
        assert membership_row_tooltip(index[PUMP_ID]) == ""
        assert membership_row_tooltip(index[LABEL_PUMP_ID]) == DETECTED_PUMP_TOOLTIP

    def test_device_and_radiator_rows_keep_their_role_label(self):
        index = cooling_member_index(
            [_device()], [_header(RAD_HWMON_ID, role="radiator_fan")], _caps()
        )
        for member_id in (PUMP_ID, RAD_OPENFAN_ID, RAD_HWMON_ID):
            member = index[member_id]
            assert membership_row_label(member) == member.role_label, member_id
            assert membership_row_tooltip(member) == "", member_id

    def test_the_wizard_renders_the_helpers_answer(self, qtbot, wizard_state):
        """The call site: the checkbox, its tooltip and the discovery reason all
        carry the helper's text — asserted against the helper, not a literal."""
        wizard_state.hwmon_headers = [_assigned_pump(), _labelled_pump()]
        wizard_state.cooling_devices = CoolingDeviceInventory(cooling_devices=[])
        wiz = FanConfigWizard(wizard_state)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        index = wiz.cooling_membership()
        assert set(index) == {PUMP_ID, LABEL_PUMP_ID}  # precondition
        reasons = wiz.excluded_reasons()
        for member_id, member in index.items():
            cb, _ = wiz._cooling_page._exclude_rows[member_id]
            label = membership_row_label(member)
            assert cb.text().endswith(f" · {label}"), cb.text()
            assert cb.toolTip() == membership_row_tooltip(member)
            assert reasons[member_id] == f"Excluded — {label}"
        # The opposite branches, so a stuck helper cannot pass the loop above.
        assert reasons[PUMP_ID] != reasons[LABEL_PUMP_ID]
        assert wiz._cooling_page._exclude_rows[LABEL_PUMP_ID][0].toolTip()


class TestRadiatorReservationOffersNoFalseRemedy:
    """`TS-ai`, narrowed by DEC-444: only an ASSIGNED radiator role can be
    removed (Set role… or the Fan Wizard), so only that note names a remedy —
    never Configure AIO, which clears only a pump — and an inferred one must
    not say it was assigned."""

    def _notes(self):
        inferred = _header(KRAKEN_RAD_ID, role="radiator_fan", role_source="chip_mapping")
        assigned = _header(RAD_HWMON_ID, role="radiator_fan", role_source="user_assigned")
        index = cooling_member_index([], [inferred, assigned], _caps())
        assert index[KRAKEN_RAD_ID].role == "radiator"  # precondition
        assert not index[KRAKEN_RAD_ID].assigned and index[RAD_HWMON_ID].assigned
        return cooling_device_reservations(index)

    def test_an_inferred_radiator_is_not_called_assigned(self):
        note = self._notes()[KRAKEN_RAD_ID]
        assert note.text == "(Radiator fan)"
        assert "assigned" not in note.text and "You assigned" not in note.tooltip
        assert "hardware" in note.tooltip

    def test_an_assigned_radiator_names_the_routes_that_remove_it(self):
        note = self._notes()[RAD_HWMON_ID]
        assert note.text == "(Radiator fan role assigned)"
        assert "Set role… ▸ Not set" in note.tooltip and "Fan Wizard" in note.tooltip

    def test_an_inferred_radiator_names_no_remedy(self):
        """A clear only drops an assignment; the hardware's role stays."""
        note = self._notes()[KRAKEN_RAD_ID]
        assert "Set role" not in note.tooltip and "release" not in note.tooltip

    def test_neither_radiator_note_names_a_remedy_that_does_not_exist(self):
        for note in self._notes().values():
            assert "Configure AIO" not in note.tooltip
            assert "Clear" not in note.tooltip
            assert note.title == "Assign the radiator fan to this curve?"


class TestRemovalInTheWizard:
    """DEC-444 (the user's Q3): where a role is chosen, it can be removed."""

    def _page(self, qtbot, wizard_state, client, headers):
        wizard_state.hwmon_headers = headers
        client.headers_to_return = list(headers)
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        return wiz, wiz._cooling_page

    @staticmethod
    def _untick_all(page):
        for i in range(page._radiator_list.count()):
            page._radiator_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def test_none_clears_a_user_pump_after_the_confirmation(self, qtbot, wizard_state, monkeypatch):
        client = _WizardClient()
        headers = [
            _header(PUMP_ID, role="pump", role_source="user_assigned"),
            _header(RAD_HWMON_ID),
        ]
        _wiz, page = self._page(qtbot, wizard_state, client, headers)
        assert page._pump_combo.currentData() == PUMP_ID, "precondition: preselected"
        page._pump_combo.setCurrentIndex(0)  # "— none —"
        self._untick_all(page)
        asked = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.Yes,
        )
        page._apply_btn.click()
        assert asked == ["Remove pump protection from a header?"]
        assert client.role_calls == [(PUMP_ID, None)]
        assert "Nothing selected" not in page._status.text()

    def test_declining_keeps_the_pump_role_and_the_devices_pump(
        self, qtbot, wizard_state, monkeypatch
    ):
        client = _WizardClient(devices=[_device(pump=PUMP_ID, radiators=[])])
        headers = [
            _header(PUMP_ID, role="pump", role_source="user_assigned"),
            _header(RAD_HWMON_ID),
        ]
        _wiz, page = self._page(qtbot, wizard_state, client, headers)
        page._pump_combo.setCurrentIndex(0)
        self._untick_all(page)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        page._apply_btn.click()
        assert client.role_calls == []
        assert "kept its role" in page._status.text()
        assert client.device_calls and client.device_calls[0]["pump_member"] == PUMP_ID, (
            "the topology must not drop a pump whose role is still in force"
        )

    def test_an_accepted_none_drops_the_devices_pump(self, qtbot, wizard_state, monkeypatch):
        client = _WizardClient(devices=[_device(pump=PUMP_ID, radiators=[])])
        headers = [
            _header(PUMP_ID, role="pump", role_source="user_assigned"),
            _header(RAD_HWMON_ID),
        ]
        _wiz, page = self._page(qtbot, wizard_state, client, headers)
        page._pump_combo.setCurrentIndex(0)
        self._untick_all(page)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        page._apply_btn.click()
        assert client.device_calls[0]["pump_member"] is None

    def test_unticking_a_user_radiator_clears_it_without_asking_after_the_assign(
        self, qtbot, wizard_state, monkeypatch
    ):
        client = _WizardClient()
        headers = [
            _header(PUMP_ID),
            _header(RAD_HWMON_ID, role="radiator_fan", role_source="user_assigned"),
        ]
        _wiz, page = self._page(qtbot, wizard_state, client, headers)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        rows = {
            page._radiator_list.item(i).data(Qt.ItemDataRole.UserRole): page._radiator_list.item(i)
            for i in range(page._radiator_list.count())
        }
        assert rows[RAD_HWMON_ID].checkState() == Qt.CheckState.Checked, "precondition"
        rows[RAD_HWMON_ID].setCheckState(Qt.CheckState.Unchecked)
        asked = []
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(1))
        page._apply_btn.click()
        assert asked == [], "a radiator role carries no floor, so nothing is asked"
        assert client.role_calls == [(PUMP_ID, "pump"), (RAD_HWMON_ID, None)]

    def test_a_label_derived_radiator_is_never_cleared(self, qtbot, wizard_state):
        client = _WizardClient()
        headers = [
            _header(PUMP_ID),
            _header(RAD_HWMON_ID, role="radiator_fan", role_source="label"),
        ]
        _wiz, page = self._page(qtbot, wizard_state, client, headers)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        self._untick_all(page)
        page._apply_btn.click()
        assert (RAD_HWMON_ID, None) not in client.role_calls
        assert (PUMP_ID, "pump") in client.role_calls, "precondition: Apply ran"


class TestAFormerPumpTickedAsARadiator:
    """DEC-444 review: ticking a pump you named as a radiator removes its pump
    protection, so it must wait for the confirmation — never land first.

    Reachable with two pumps the user named (a dual-pump loop): the picker
    preselects one, so the other is offered in the radiator list."""

    def _page(self, qtbot, wizard_state, client):
        headers = [
            _header(PUMP_ID, role="pump", role_source="user_assigned"),
            _header(RAD_HWMON_ID, role="pump", role_source="user_assigned"),
        ]
        wizard_state.hwmon_headers = headers
        client.headers_to_return = list(headers)
        wiz = FanConfigWizard(wizard_state, client=client)
        qtbot.addWidget(wiz)
        wiz.setStartId(PAGE_COOLING)
        wiz.restart()
        page = wiz._cooling_page
        rows = {
            page._radiator_list.item(i).data(Qt.ItemDataRole.UserRole): page._radiator_list.item(i)
            for i in range(page._radiator_list.count())
        }
        offered = [h for h in (PUMP_ID, RAD_HWMON_ID) if h in rows]
        assert len(offered) == 1, "precondition: exactly one named pump is offered as a radiator"
        other = offered[0]
        kept = page._pump_combo.currentData()
        assert kept and kept != other, "precondition: the other pump is preselected"
        for rid, item in rows.items():
            item.setCheckState(Qt.CheckState.Checked if rid == other else Qt.CheckState.Unchecked)
        return page, other, kept

    def test_accepting_makes_it_a_radiator_only_after_asking(
        self, qtbot, wizard_state, monkeypatch
    ):
        client = _WizardClient()
        page, other, kept = self._page(qtbot, wizard_state, client)
        order = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: order.append("asked") or QMessageBox.StandardButton.Yes,
        )
        original = client.set_header_role
        client.set_header_role = lambda h, r: order.append((h, r)) or original(h, r)
        page._apply_btn.click()
        asked_at = order.index("asked")
        assert not [w for w in order[:asked_at] if w[0] == other], "no downgrade before the answer"
        assert order[asked_at + 1 :] == [(other, "radiator_fan")], "then it becomes the radiator"
        assert (kept, None) not in order, "the preselected pump is never cleared"

    def test_declining_leaves_it_a_pump_and_out_of_the_radiators(
        self, qtbot, wizard_state, monkeypatch
    ):
        client = _WizardClient()
        page, other, _kept = self._page(qtbot, wizard_state, client)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        page._apply_btn.click()
        assert (other, "radiator_fan") not in client.role_calls
        assert (other, None) not in client.role_calls
        assert "kept its role" in page._status.text()
        assert other not in client.device_calls[0]["radiator_members"]


class _FailingOn(_WizardClient):
    """Fails exactly the named (header, role) writes; records the rest."""

    def __init__(self, fail: set[tuple], **kw):
        super().__init__(**kw)
        self._fail = fail

    def set_header_role(self, header_id, role):
        if (header_id, role) in self._fail:
            raise ConnectionError("daemon went away")
        return super().set_header_role(header_id, role)


class TestAFailedClearIsNotReportedAsSaved:
    """DEC-444 review: a clear that fails leaves the role in force, so the
    topology must not describe it otherwise and the status must say so."""

    def test_a_failed_downgrade_stays_out_of_the_radiators(self, qtbot, wizard_state, monkeypatch):
        # Either named pump may be the one offered as a radiator; fail both.
        client = _FailingOn({(PUMP_ID, "radiator_fan"), (RAD_HWMON_ID, "radiator_fan")})
        page, other, _kept = TestAFormerPumpTickedAsARadiator()._page(qtbot, wizard_state, client)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        page._apply_btn.click()
        assert (other, "radiator_fan") not in client.role_calls, "precondition: the write failed"
        assert other not in client.device_calls[0]["radiator_members"], (
            "a header still protected as a pump must not be saved as a radiator member"
        )
        assert "still a pump" in page._status.text()

    def test_only_the_pump_that_failed_keeps_its_place_in_the_device(
        self, qtbot, wizard_state, monkeypatch
    ):
        headers = [
            _header(PUMP_ID, role="pump", role_source="user_assigned"),
            _header(RAD_HWMON_ID, role="pump", role_source="user_assigned"),
        ]
        # The device's pump clears; the OTHER named pump's clear fails.
        client = _FailingOn({(RAD_HWMON_ID, None)}, devices=[_device(pump=PUMP_ID, radiators=[])])
        _wiz, page = TestRemovalInTheWizard()._page(qtbot, wizard_state, client, headers)
        page._pump_combo.setCurrentIndex(0)
        TestRemovalInTheWizard._untick_all(page)
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        page._apply_btn.click()
        assert (PUMP_ID, None) in client.role_calls, "precondition: the device's pump cleared"
        assert client.device_calls[0]["pump_member"] is None, (
            "one failure must not keep a pump that was cleared as the device's pump"
        )
        assert "still a pump" in page._status.text()

    def test_a_failed_radiator_clear_is_reported(self, qtbot, wizard_state):
        headers = [
            _header(PUMP_ID),
            _header(RAD_HWMON_ID, role="radiator_fan", role_source="user_assigned"),
        ]
        client = _FailingOn({(RAD_HWMON_ID, None)})
        _wiz, page = TestRemovalInTheWizard()._page(qtbot, wizard_state, client, headers)
        page._pump_combo.setCurrentIndex(page._pump_combo.findData(PUMP_ID))
        TestRemovalInTheWizard._untick_all(page)
        page._apply_btn.click()
        assert (PUMP_ID, "pump") in client.role_calls, "precondition: Apply ran"
        assert "Could not remove the radiator fan role" in page._status.text()


class TestAPumpThePickerNeverListed:
    """DEC-444 review: a pump the picker did not list cannot have been
    deselected, so it is neither cleared nor dropped from the device."""

    def test_none_leaves_an_unlisted_user_pump_alone(self, qtbot, wizard_state, monkeypatch):
        unlisted = dataclasses.replace(
            _header(PUMP_ID, role="pump", role_source="user_assigned"), is_writable=False
        )
        headers = [unlisted, _header(RAD_HWMON_ID)]
        client = _WizardClient(devices=[_device(pump=PUMP_ID, radiators=[])])
        _wiz, page = TestRemovalInTheWizard()._page(qtbot, wizard_state, client, headers)
        assert page._pump_combo.findData(PUMP_ID) < 0, "precondition: not listed"
        page._pump_combo.setCurrentIndex(0)
        rows = {
            page._radiator_list.item(i).data(Qt.ItemDataRole.UserRole): page._radiator_list.item(i)
            for i in range(page._radiator_list.count())
        }
        rows[RAD_HWMON_ID].setCheckState(Qt.CheckState.Checked)
        asked = []
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(1))
        page._apply_btn.click()
        assert (RAD_HWMON_ID, "radiator_fan") in client.role_calls, "precondition: Apply ran"
        assert asked == [] and (PUMP_ID, None) not in client.role_calls
        assert client.device_calls[0]["pump_member"] == PUMP_ID

    def test_the_users_pump_is_preselected_over_an_inferred_one(self, qtbot, wizard_state):
        """BRD-h: nct6687d labels a case-fan header "Pump Fan" on a non-MSI
        board, so the inferred pump listed first is the wrong default."""
        headers = [
            _header(RAD_HWMON_ID, role="pump", role_source="label"),
            _header(PUMP_ID, role="pump", role_source="user_assigned"),
        ]
        _wiz, page = TestRemovalInTheWizard()._page(qtbot, wizard_state, _WizardClient(), headers)
        assert page._pump_combo.findData(RAD_HWMON_ID) < page._pump_combo.findData(PUMP_ID), (
            "precondition: the inferred pump is listed first"
        )
        assert page._pump_combo.currentData() == PUMP_ID
