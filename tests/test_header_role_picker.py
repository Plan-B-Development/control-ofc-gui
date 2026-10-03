"""DEC-444: set a header's role where it is shown, and flag nct6687d's MSI labels.

Covers register rows `PTA-a` (no GUI route to a chassis/radiator role, so the
stall probe was unreachable on a label-less board) and `BRD-h` (nct6687d's
"Pump Fan" on a non-MSI board). The call-site tests click the real controls —
the connection is what is most likely to be broken, and a handler call skips it.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QRadioButton

from control_ofc.api.errors import DaemonError, DaemonTimeout
from control_ofc.api.models import (
    BoardInfo,
    Capabilities,
    ConnectionState,
    ControlCapability,
    HeaderRoleResult,
    HwmonHeader,
)
from control_ofc.services import app_settings_service as settings_mod
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.header_inspector_view import build_header_inspector_view
from control_ofc.services.header_role_view import (
    ROLE_CHOICES,
    current_choice,
    failure_message,
    nct6687_label_prompt,
    nct6687_label_unverified,
    outcome_message,
    plan_role_change,
    role_editable,
)
from control_ofc.services.pwm_report.catalog import TEST_PROBE, availability
from control_ofc.ui.pages import hardware_page as hw_mod
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.widgets.header_role_dialog import HeaderRoleDialog
from control_ofc.ui.widgets.pwm_header_card import PwmHeaderCard, _slug

ASROCK = "ASRock"
MSI = "Micro-Star International Co., Ltd."
UNLABELLED = "hwmon:it8696:it87.2624:pwm2:pwm2"
USER_PUMP = "hwmon:it8696:it87.2624:pwm5:pwm5"
LABEL_PUMP = "hwmon:nct6799:isa-0290:pwm7:AIO_PUMP"
NCT_PUMP = "hwmon:nct6687:nct6687.2592:pwm2:Pump Fan"


def _caps(**control) -> Capabilities:
    control.setdefault("header_roles", True)
    return Capabilities(control=ControlCapability(**control))


def _hdr(hid: str = UNLABELLED, **kw) -> HwmonHeader:
    index = int(hid.split(":pwm")[1].split(":")[0])
    base = {
        "id": hid,
        "label": hid.rsplit(":", 1)[-1],
        "chip_name": hid.split(":")[1],
        "device_id": hid.split(":")[2],
        "pwm_index": index,
        "is_writable": True,
        "rpm_available": True,
        "role": "unknown",
        "role_source": "none",
        "stop_permitted": True,
        "effective_min_pwm_pct": 0,
    }
    base.update(kw)
    return HwmonHeader(**base)


def _user_pump() -> HwmonHeader:
    return _hdr(
        USER_PUMP,
        role="pump",
        role_source="user_assigned",
        stop_permitted=False,
        effective_min_pwm_pct=30,
    )


# ── the view-model ───────────────────────────────────────────────────────────


class TestChoices:
    def test_offers_every_assignable_role_and_a_clear_never_an_explicit_unknown(self):
        tokens = [c.token for c in ROLE_CHOICES]
        assert tokens == ["pump", "chassis_fan", "radiator_fan", "cpu_fan", None]
        assert "unknown" not in tokens

    def test_cpu_fan_says_it_adds_no_floor(self):
        """An assigned `cpu_fan` feeds no floor daemon-side (only `pump` does);
        copy implying protection would lie about hardware safety."""
        cpu = next(c for c in ROLE_CHOICES if c.token == "cpu_fan")
        assert "adds no floor" in cpu.effect

    def test_preselection_is_the_users_own_assignment_only(self):
        assert current_choice(_user_pump()) == "pump"
        assert current_choice(_hdr(LABEL_PUMP, role="pump", role_source="label")) is None


class TestPlan:
    @pytest.mark.parametrize(
        ("header", "new", "noop", "removes"),
        [
            (_hdr(), None, True, False),  # nothing to clear
            (_hdr(), "chassis_fan", False, False),
            (_user_pump(), "pump", True, False),  # restates the assignment
            (_user_pump(), None, False, True),  # the clear that lowers a floor
            (_user_pump(), "chassis_fan", False, True),  # a downgrade does too
            (_hdr(role="radiator_fan", role_source="user_assigned"), None, False, False),
            # Storing a pump assignment over a label-derived pump is a change,
            # and removes nothing.
            (_hdr(LABEL_PUMP, role="pump", role_source="label"), "pump", False, False),
            (_hdr(LABEL_PUMP, role="pump", role_source="label"), "chassis_fan", False, False),
        ],
    )
    def test_plan(self, header, new, noop, removes):
        plan = plan_role_change(header, new)
        assert (plan.noop, plan.removes_user_pump) == (noop, removes)
        assert plan.new_role == new


class TestEditable:
    def test_writable_header_on_a_role_daemon(self):
        assert role_editable(_hdr(), _caps()) == (True, "")

    def test_read_only_header_is_refused_with_a_reason(self):
        ok, reason = role_editable(_hdr(is_writable=False), _caps())
        assert not ok and "read-only" in reason

    def test_a_daemon_without_roles_is_refused_with_the_upgrade_message(self):
        ok, reason = role_editable(_hdr(), _caps(header_roles=False))
        assert not ok and "2.28.0" in reason


class TestOutcome:
    def test_a_label_pump_downgraded_still_says_it_is_protected(self):
        """The union: `role` reads chassis_fan, the daemon still refuses to stop it."""
        after = _hdr(
            LABEL_PUMP, role="chassis_fan", role_source="user_assigned", stop_permitted=False
        )
        result = HeaderRoleResult(
            updated=True, header_id=LABEL_PUMP, role="chassis_fan", effective_role="chassis_fan"
        )
        text = outcome_message("AIO_PUMP", result, after, _caps())
        assert "Chassis fan" in text and "still protects it as a pump" in text

    def test_a_clear_reports_what_the_daemon_fell_back_to(self):
        after = _hdr(LABEL_PUMP, role="pump", role_source="label", stop_permitted=False)
        result = HeaderRoleResult(
            updated=True, header_id=LABEL_PUMP, role=None, effective_role="pump"
        )
        text = outcome_message("AIO_PUMP", result, after, _caps())
        assert "removed" in text and "Pump" in text
        assert "still protects" not in text, "a pump that IS a pump needs no caveat"

    def test_cpu_fan_outcome_repeats_that_no_floor_was_added(self):
        result = HeaderRoleResult(role="cpu_fan", effective_role="cpu_fan")
        assert "adds no floor" in outcome_message("X", result, _hdr(role="cpu_fan"), _caps())

    def test_a_failed_reread_is_admitted(self):
        result = HeaderRoleResult(role="chassis_fan", effective_role="chassis_fan")
        assert "could not be re-read" in outcome_message("X", result, None, _caps())

    def test_only_an_error_envelope_claims_nothing_changed(self):
        refused = DaemonError(code="persistence_failed", message="ro fs", status=503)
        assert "Nothing was changed" in failure_message("X", "pump", refused)
        timed_out = DaemonTimeout(message="timed out")
        assert "Nothing was changed" not in failure_message("X", "pump", timed_out)
        assert "did not confirm" in failure_message("X", "pump", timed_out)


# ── BRD-h ────────────────────────────────────────────────────────────────────


class TestNct6687Labels:
    @pytest.mark.parametrize(
        ("vendor", "chip", "label", "flagged"),
        [
            (ASROCK, "nct6687", "Pump Fan", True),
            (ASROCK, "nct6686", "System Fan #3", True),
            (ASROCK, "nct6683", "CPU Fan", True),
            (MSI, "nct6687", "Pump Fan", False),  # MSI's own labels are MSI's
            ("", "nct6687", "Pump Fan", False),  # no evidence it is not MSI
            (ASROCK, "nct6687", "pwm2", False),  # the daemon's placeholder
            (ASROCK, "nct6799", "Pump Fan", False),  # not an nct668x chip
            (ASROCK, "nct6687", "AIO_PUMP", False),  # not one of nct6687d's strings
        ],
    )
    def test_detection(self, vendor, chip, label, flagged):
        header = _hdr(f"hwmon:{chip}:{chip}.2592:pwm2:{label}", label=label, chip_name=chip)
        assert nct6687_label_unverified(header, vendor) is flagged

    def test_the_card_carries_the_caveat_on_the_role_source_row(self):
        view = build_header_inspector_view(
            _hdr(NCT_PUMP, label="Pump Fan", role="pump", role_source="label"),
            capabilities=_caps(),
            board_vendor=ASROCK,
        )
        assert "Label unverified" in view.label_caveat and "Set role" in view.label_caveat
        row = next(r for r in view.safety_rows if r.label == "Role source")
        assert row.note == view.label_caveat

    def test_prompt_shows_hides_on_a_user_pump_and_on_confirmation(self):
        nct = _hdr(NCT_PUMP, label="Pump Fan", role="pump", role_source="label")
        prompt = nct6687_label_prompt([nct], ASROCK, set())
        assert prompt is not None and "Labels are correct" in prompt.text
        assert nct6687_label_prompt([nct, _user_pump()], ASROCK, set()) is None
        assert nct6687_label_prompt([nct], ASROCK, {prompt.key}) is None
        assert nct6687_label_prompt([nct], MSI, set()) is None

    def test_prompt_key_follows_the_hardware(self):
        a = nct6687_label_prompt([_hdr(NCT_PUMP, label="Pump Fan")], ASROCK, set())
        other = NCT_PUMP.replace("nct6687.2592", "nct6687.2624")
        b = nct6687_label_prompt([_hdr(other, label="Pump Fan")], ASROCK, set())
        assert a.key != b.key


# ── the dialog ───────────────────────────────────────────────────────────────


class TestDialog:
    def test_apply_is_disabled_until_the_choice_changes_something(self, qtbot):
        dialog = HeaderRoleDialog(_user_pump(), "pwm5")
        qtbot.addWidget(dialog)
        apply = dialog.findChild(QPushButton, "HeaderRole_Btn_apply")
        assert dialog.chosen_role() == "pump"
        assert not apply.isEnabled(), "re-stating the assignment writes nothing"
        dialog.findChild(QRadioButton, "HeaderRole_Radio_chassis_fan").click()
        assert dialog.chosen_role() == "chassis_fan" and apply.isEnabled()
        dialog.findChild(QRadioButton, "HeaderRole_Radio_none").click()
        assert dialog.chosen_role() is None and apply.isEnabled()

    def test_every_radio_has_a_unique_name_and_an_accessible_name(self, qtbot):
        dialog = HeaderRoleDialog(_hdr(), "pwm2")
        qtbot.addWidget(dialog)
        radios = dialog.findChildren(QRadioButton)
        assert len(radios) == len(ROLE_CHOICES)
        assert len({r.objectName() for r in radios}) == len(radios)
        assert all(r.accessibleName() for r in radios)


# ── the card ─────────────────────────────────────────────────────────────────


def _card(qtbot, header, caps=None, vendor=""):
    view = build_header_inspector_view(header, capabilities=caps or _caps(), board_vendor=vendor)
    card = PwmHeaderCard(view)
    qtbot.addWidget(card)
    return card


class TestCard:
    def test_the_button_emits_the_header_id(self, qtbot):
        card = _card(qtbot, _hdr())
        btn = card.findChild(QPushButton, f"HeaderCard_Btn_role_{_slug(UNLABELLED)}")
        assert btn.isEnabled()
        with qtbot.waitSignal(card.role_change_requested) as sig:
            btn.click()
        assert sig.args == [UNLABELLED]

    def test_read_only_header_disables_it_with_the_reason(self, qtbot):
        card = _card(qtbot, _hdr(is_writable=False))
        btn = card.findChild(QPushButton, f"HeaderCard_Btn_role_{_slug(UNLABELLED)}")
        assert not btn.isEnabled() and "read-only" in btn.toolTip()

    def test_a_running_report_does_not_disable_it(self, qtbot):
        """The user's Q4: only read-only headers disable it."""
        card = _card(qtbot, _hdr())
        card.set_diagnostics_blocked("A PWM Test Report is running.")
        btn = card.findChild(QPushButton, f"HeaderCard_Btn_role_{_slug(UNLABELLED)}")
        test_btn = card.findChild(QPushButton, f"HeaderCard_Btn_test_{_slug(UNLABELLED)}")
        assert not test_btn.isEnabled(), "precondition: the run did block the tests"
        assert btn.isEnabled()

    def test_the_brd_h_caveat_is_visible_on_the_card(self, qtbot):
        card = _card(qtbot, _hdr(NCT_PUMP, label="Pump Fan"), vendor=ASROCK)
        lbl = card.findChild(QLabel, f"HeaderCard_Label_caveat_{_slug(NCT_PUMP)}")
        assert not lbl.isHidden() and "Label unverified" in lbl.text()


# ── the Hardware page: one write path ────────────────────────────────────────


class _Client:
    socket_path = ""

    def __init__(self, state: AppState, *, error: Exception | None = None):
        self._state = state
        self.calls: list[tuple[str, str | None]] = []
        self._error = error

    def set_header_role(self, header_id, role):
        self.calls.append((header_id, role))
        if self._error is not None:
            raise self._error
        return HeaderRoleResult(
            updated=True, header_id=header_id, role=role, effective_role=role or "unknown"
        )

    def hwmon_headers(self):
        out = []
        for h in self._state.hwmon_headers:
            for hid, role in self.calls:
                if hid == h.id and self._error is None:
                    h = HwmonHeader(
                        **{
                            **h.__dict__,
                            "role": role or "unknown",
                            "role_source": "user_assigned" if role else "none",
                            "stop_permitted": role != "pump",
                        }
                    )
            out.append(h)
        return out


class _StubDialog:
    """`exec()` would block; the dialog's own behaviour is tested above."""

    choice: str | None = None
    accept = True

    def __init__(self, header, name, *, label_caveat="", parent=None):
        self.header = header

    def exec(self):
        return self.accept

    def chosen_role(self):
        return type(self).choice


def _page(qtbot, monkeypatch, headers, *, error=None, settings_service=None, vendor=""):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_capabilities(_caps())
    state.board_info = BoardInfo(vendor=vendor)
    state.set_hwmon_headers(headers)
    client = _Client(state, error=error)
    page = HardwarePage(
        state=state,
        diagnostics_service=DiagnosticsService(state),
        client=client,
        settings_service=settings_service,
    )
    qtbot.addWidget(page)
    monkeypatch.setattr(hw_mod, "HeaderRoleDialog", _StubDialog)
    return page, state, client


def _role_btn(page, header_id):
    return page.findChild(QPushButton, f"HeaderCard_Btn_role_{_slug(header_id)}")


def _outcome(page, header_id) -> QLabel:
    """`ROLE-d`: the card's own outcome line."""
    return page.findChild(QLabel, f"HeaderCard_Label_roleOutcome_{_slug(header_id)}")


class TestHardwarePage:
    def test_choosing_chassis_fan_posts_once_and_rereads_the_headers(self, qtbot, monkeypatch):
        page, state, client = _page(qtbot, monkeypatch, [_hdr()])
        _StubDialog.choice, _StubDialog.accept = "chassis_fan", True
        _role_btn(page, UNLABELLED).click()
        assert client.calls == [(UNLABELLED, "chassis_fan")]
        assert state.hwmon_headers[0].role == "chassis_fan", "AppState got the re-read"
        pill = page.findChild(QLabel, f"HeaderCard_Pill_role_{_slug(UNLABELLED)}")
        assert pill.accessibleName() == "Role: Chassis fan"
        assert "is now set to Chassis fan" in _outcome(page, UNLABELLED).text()

    def test_cancel_writes_nothing(self, qtbot, monkeypatch):
        page, _state, client = _page(qtbot, monkeypatch, [_hdr()])
        _StubDialog.choice, _StubDialog.accept = "chassis_fan", False
        _role_btn(page, UNLABELLED).click()
        assert client.calls == []

    def test_declining_the_pump_confirmation_sends_nothing(self, qtbot, monkeypatch):
        page, _state, client = _page(qtbot, monkeypatch, [_user_pump()])
        _StubDialog.choice, _StubDialog.accept = None, True
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        _role_btn(page, USER_PUMP).click()
        assert client.calls == []
        assert "kept its pump role" in _outcome(page, USER_PUMP).text()

    def test_accepting_it_sends_the_null_clear(self, qtbot, monkeypatch):
        page, _state, client = _page(qtbot, monkeypatch, [_user_pump()])
        _StubDialog.choice, _StubDialog.accept = None, True
        asked = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.Yes,
        )
        _role_btn(page, USER_PUMP).click()
        assert asked == ["Remove pump protection from a header?"]
        assert client.calls == [(USER_PUMP, None)]

    def test_a_refusal_is_reported_and_claims_nothing(self, qtbot, monkeypatch):
        err = DaemonError(code="persistence_failed", message="read-only fs", status=503)
        page, state, client = _page(qtbot, monkeypatch, [_hdr()], error=err)
        _StubDialog.choice, _StubDialog.accept = "radiator_fan", True
        _role_btn(page, UNLABELLED).click()
        assert client.calls == [(UNLABELLED, "radiator_fan")]
        assert "Nothing was changed" in _outcome(page, UNLABELLED).text()
        assert state.hwmon_headers[0].role == "unknown"

    def test_a_timeout_with_a_failed_reread_does_not_claim_the_card_is_current(
        self, qtbot, monkeypatch
    ):
        page, _state, client = _page(
            qtbot, monkeypatch, [_hdr()], error=DaemonTimeout(message="timed out")
        )

        def reread_fails():
            raise ConnectionError("gone")

        monkeypatch.setattr(client, "hwmon_headers", reread_fails)
        _StubDialog.choice, _StubDialog.accept = "chassis_fan", True
        _role_btn(page, UNLABELLED).click()
        assert client.calls == [(UNLABELLED, "chassis_fan")], "precondition: the write was tried"
        text = _outcome(page, UNLABELLED).text()
        assert "could not be re-read" in text
        assert "now shows what the daemon reports" not in text

    def test_the_outcome_shows_on_the_card_that_asked_one_at_a_time(self, qtbot, monkeypatch):
        """`ROLE-d`: beside the pill it changed, not in the Diagnostics line, and
        the next change on another card replaces it."""
        other = "hwmon:it8696:it87.2624:pwm3:pwm3"
        page, _state, _client = _page(qtbot, monkeypatch, [_hdr(), _hdr(other)])
        _StubDialog.choice, _StubDialog.accept = "chassis_fan", True
        _role_btn(page, UNLABELLED).click()
        first = _outcome(page, UNLABELLED)
        assert first.isVisibleTo(first.parentWidget()) and "Chassis fan" in first.text()
        assert page._diag_result.text() == "", "the Diagnostics line is not used"

        _StubDialog.choice = "radiator_fan"
        _role_btn(page, other).click()
        assert "Radiator fan" in _outcome(page, other).text()
        assert first.text() == "" and not first.isVisibleTo(first.parentWidget())

    def test_the_poll_does_not_wipe_the_outcome(self, qtbot, monkeypatch):
        page, state, _client = _page(qtbot, monkeypatch, [_hdr()])
        _StubDialog.choice, _StubDialog.accept = "chassis_fan", True
        _role_btn(page, UNLABELLED).click()
        assert _outcome(page, UNLABELLED).text(), "precondition: a message was shown"
        state.set_hwmon_headers(list(state.hwmon_headers))  # re-renders every card
        assert "Chassis fan" in _outcome(page, UNLABELLED).text()

    def test_a_role_change_made_elsewhere_clears_the_cards_outcome(self, qtbot, monkeypatch):
        """The review's P2: a role set from the report, Configure AIO, the wizard
        or another client arrives only as new headers, and the card's past-tense
        message would contradict its own pill."""
        page, state, _client = _page(qtbot, monkeypatch, [_hdr()])
        _StubDialog.choice, _StubDialog.accept = "chassis_fan", True
        _role_btn(page, UNLABELLED).click()
        label = _outcome(page, UNLABELLED)
        assert "Chassis fan" in label.text(), "precondition: a message was shown"
        state.set_hwmon_headers([_hdr(role="radiator_fan", role_source="user_assigned")])
        assert label.text() == "" and not label.isVisibleTo(label.parentWidget())

    def test_a_card_that_has_gone_falls_back_to_the_diagnostics_line(self, qtbot, monkeypatch):
        page, _state, _client = _page(qtbot, monkeypatch, [_hdr()])
        page._show_role_outcome("hwmon:gone:x:pwm9:pwm9", "That header is no longer reported.")
        assert page._diag_result.text() == "That header is no longer reported."

    def test_a_role_change_does_not_scroll_the_card_away(self, qtbot, monkeypatch):
        """`ROLE-d`, measured: reaching the Diagnostics line scrolled the page to
        it, and with eight or more headers the card that asked left the screen."""
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QApplication

        headers = [_hdr(f"hwmon:it8696:it87.2624:pwm{i}:pwm{i}") for i in range(1, 17)]
        page, _state, _client = _page(qtbot, monkeypatch, headers)
        page.resize(1280, 800)
        page.show()
        QApplication.processEvents()
        first = headers[0].id
        page._scroll.ensureWidgetVisible(_role_btn(page, first))
        QApplication.processEvents()
        viewport = page._scroll.viewport()

        def on_screen(widget) -> bool:
            rect = widget.rect().translated(widget.mapTo(viewport, QPoint(0, 0)))
            return viewport.rect().intersects(rect)

        assert not on_screen(page._diag_result.parentWidget()), (
            "precondition: the Diagnostics card is off-screen, so a scroll to it would move"
        )
        bar = page._scroll.verticalScrollBar()
        before = bar.value()
        _StubDialog.choice, _StubDialog.accept = "chassis_fan", True
        _role_btn(page, first).click()
        QApplication.processEvents()
        assert bar.value() == before
        assert on_screen(_role_btn(page, first)) and on_screen(_outcome(page, first))

    def test_the_report_opened_here_can_set_a_role(
        self, qtbot, monkeypatch, tmp_path, settings_service
    ):
        """The report gets its role route only from `_open_pwm_report`; without
        it every "Set…" is disabled with a tooltip that is false there."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        page, state, client = _page(qtbot, monkeypatch, [_hdr()], settings_service=settings_service)
        client.socket_path = "/tmp/fake.sock"
        page._open_pwm_report()
        window = page._report_window
        assert window is not None, "precondition: the report opened"
        try:
            window.findChild(QPushButton, "PwmReport_Btn_new").click()
            slug = "".join(c if c.isalnum() else "_" for c in UNLABELLED)
            button = window.findChild(QPushButton, f"PwmReport_Btn_role_{slug}")
            assert button is not None and button.isEnabled(), button and button.toolTip()
            _StubDialog.choice, _StubDialog.accept = "chassis_fan", True
            button.click()
            assert client.calls == [(UNLABELLED, "chassis_fan")]
            assert state.hwmon_headers[0].role == "chassis_fan"
        finally:
            window.close()
            page._report_controller.shutdown()


class TestLabelPrompt:
    def test_prompt_is_shown_and_labels_are_correct_is_remembered(
        self, qtbot, monkeypatch, settings_service
    ):
        nct = _hdr(NCT_PUMP, label="Pump Fan", role="pump", role_source="label")
        page, _state, _client = _page(
            qtbot, monkeypatch, [nct], settings_service=settings_service, vendor=ASROCK
        )
        box = page.findChild(QLabel, "Hardware_Label_labelPrompt").parentWidget()
        assert not box.isHidden()
        page.findChild(QPushButton, "Hardware_Btn_labelsCorrect").click()
        assert box.isHidden()
        assert settings_service.settings.confirmed_label_prompts == ["nct6687-labels:nct6687.2592"]

    def test_prompt_hides_once_a_pump_is_assigned(self, qtbot, monkeypatch, settings_service):
        nct = _hdr(NCT_PUMP, label="Pump Fan", role="pump", role_source="label")
        page, _state, _client = _page(
            qtbot,
            monkeypatch,
            [nct, _hdr(UNLABELLED)],
            settings_service=settings_service,
            vendor=ASROCK,
        )
        box = page.findChild(QLabel, "Hardware_Label_labelPrompt").parentWidget()
        assert not box.isHidden()
        _StubDialog.choice, _StubDialog.accept = "pump", True
        _role_btn(page, UNLABELLED).click()
        assert box.isHidden()

    def test_the_answer_is_machine_specific_and_never_exported(self):
        assert "confirmed_label_prompts" in settings_mod.MACHINE_SPECIFIC_KEYS
        s = settings_mod.AppSettings.from_dict({"confirmed_label_prompts": ["a", "a", 3, "b"]})
        assert s.confirmed_label_prompts == ["a", "b"]
        assert "confirmed_label_prompts" not in s.portable_dict()


# ── the PWM Test Report's scope page (Q1-C) ──────────────────────────────────


def test_the_probe_reason_points_at_a_route_that_exists():
    """`PTA-a`: the tooltip sent users to the Controls page, whose *Assign
    Roles* pane creates profile controls and cannot set a header role."""
    from tests.test_pwm_report_store_and_catalog import _caps as cat_caps
    from tests.test_pwm_report_store_and_catalog import _ch

    reason = availability(_ch(role="unknown"), TEST_PROBE, cat_caps()).reason
    assert "Controls page" not in reason
    assert "Set…" in reason and "Hardware page" in reason


# ── Configure AIO asks the same question (the user's Q3 follow-up) ───────────


class TestConfigureAioConfirmsTheClear:
    NEW = "hwmon:it8696:it87.2624:pwm4:pwm4"

    def _run(self, qtbot, monkeypatch, app_state, profile_service, answer, *, error=None):
        from control_ofc.services.profile_service import AIO_PUMP_STRATEGY_AUTOMATIC
        from control_ofc.ui.pages.controls_page import ControlsPage
        from control_ofc.ui.widgets import aio_config_dialog as dlg_mod

        app_state.set_capabilities(_caps())
        app_state.set_hwmon_headers([_user_pump(), _hdr(self.NEW)])
        client = _Client(app_state, error=error)
        page = ControlsPage(state=app_state, profile_service=profile_service, client=client)
        qtbot.addWidget(page)
        result = {
            "pump_strategy": AIO_PUMP_STRATEGY_AUTOMATIC,
            "pump_member_id": self.NEW,
            "pump_pct": 0,
            "radiator_members": [],
            "radiator_sensor_id": "",
            # What `AioConfigDialog._role_assignments` emits for "move the pump".
            "role_assignments": [(self.NEW, "pump"), (USER_PUMP, None)],
            "cooling_device": None,
        }
        monkeypatch.setattr(dlg_mod.AioConfigDialog, "exec", lambda self: 1)
        monkeypatch.setattr(dlg_mod.AioConfigDialog, "get_result", lambda self: result)
        asked = []
        # Each question records the writes already sent when it was asked.
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: asked.append((a[1], list(client.calls))) or answer,
        )
        page._on_configure_aio()
        assert page._state is app_state  # binds the page (DASH-h)
        return client, asked

    def test_declining_keeps_the_old_pump_and_still_assigns_the_new(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        client, asked = self._run(
            qtbot, monkeypatch, app_state, profile_service, QMessageBox.StandardButton.No
        )
        assert asked == [("Remove pump protection from a header?", [(self.NEW, "pump")])], (
            "asked once, after the new pump's assign landed (`ROLE-c`)"
        )
        assert client.calls == [(self.NEW, "pump")]

    def test_accepting_clears_after_the_assign(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        client, _asked = self._run(
            qtbot, monkeypatch, app_state, profile_service, QMessageBox.StandardButton.Yes
        )
        assert client.calls == [(self.NEW, "pump"), (USER_PUMP, None)]

    def test_a_failed_assign_never_asks_and_clears_nothing(
        self, qtbot, monkeypatch, app_state, profile_service
    ):
        err = DaemonError(code="persistence_failed", message="read-only fs", status=503)
        client, asked = self._run(
            qtbot,
            monkeypatch,
            app_state,
            profile_service,
            QMessageBox.StandardButton.Yes,
            error=err,
        )
        assert client.calls == [(self.NEW, "pump")], "precondition: the assign was tried"
        assert asked == []
