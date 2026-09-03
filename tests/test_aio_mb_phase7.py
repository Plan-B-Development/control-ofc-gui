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
    CoolingMembership,
    cooling_member_index,
    find_cooling_device,
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
            cooling_member_index([], [_header(PUMP_ID, role="pump")])
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
