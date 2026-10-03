"""`ROLE-f` (DEC-475): roles on OpenFan channels, GUI side.

An OpenFan channel has no label and no chip, so a ``pump`` assignment is the
daemon's only pump evidence for it. The GUI sets it from the Hardware page's
OpenFan Channels list, reads protection back from ``stop_permitted`` on
``GET /fans/openfan/roles``, words identify for it, and never offers to
calibrate a pump channel. Call-site tests click the real controls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QMessageBox, QPushButton

from control_ofc.api.errors import DaemonError
from control_ofc.api.models import (
    Capabilities,
    ConnectionState,
    ControlCapability,
    FanReading,
    HeaderRoleResult,
    OpenFanCalibrationRun,
    OpenFanRole,
    parse_openfan_roles,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.openfan_calibration_view import (
    build_calibration_view,
    build_channel_options,
    pump_channel_refusal,
)
from control_ofc.services.openfan_role_view import (
    OPENFAN_ROLE_CHOICES,
    build_openfan_role_rows,
    openfan_outcome_message,
    openfan_role_choices,
)
from control_ofc.services.polling import _PollWorker
from control_ofc.services.pump_protection import openfan_channel_is_pump_protected
from control_ofc.ui.pages import hardware_page as hw_mod
from control_ofc.ui.pages.hardware_page import HardwarePage
from control_ofc.ui.widgets.fan_wizard import FanConfigWizard
from control_ofc.ui.widgets.openfan_calibration_dialog import OpenFanCalibrationDialog

CH0 = "openfan:ch00"
CH1 = "openfan:ch01"


def _caps(**control) -> Capabilities:
    control.setdefault("header_roles", True)
    control.setdefault("openfan_header_roles", True)
    return Capabilities(control=ControlCapability(**control))


def _role(fan_id: str = CH0, role: str = "unknown", **kw) -> OpenFanRole:
    assigned = role != "unknown"
    base = {
        "fan_id": fan_id,
        "channel": int(fan_id[-2:]),
        "role": role,
        "role_source": "user_assigned" if assigned else "none",
        "stop_permitted": role != "pump",
        "effective_min_pwm_pct": 30 if role == "pump" else 0,
    }
    base.update(kw)
    return OpenFanRole(**base)


def _fan(fan_id: str = CH0) -> FanReading:
    return FanReading(id=fan_id, source="openfan", rpm=900)


# ── model and predicate ──────────────────────────────────────────────────────


def test_the_route_parses_into_roles():
    roles = parse_openfan_roles(
        {
            "api_version": 1,
            "channels": [
                {
                    "fan_id": CH0,
                    "channel": 0,
                    "role": "pump",
                    "role_source": "user_assigned",
                    "stop_permitted": False,
                    "effective_min_pwm_pct": 30,
                    "future_field": 1,
                },
                "malformed",
            ],
        }
    )
    assert roles == [_role(CH0, "pump")]
    assert roles[0].id == CH0, "the role write key, as HwmonHeader.id"


class TestPredicate:
    def test_the_daemons_stop_permitted_decides(self):
        assert openfan_channel_is_pump_protected(_role(role="pump")) is True
        assert openfan_channel_is_pump_protected(_role()) is False

    def test_stop_permitted_outranks_the_display_role(self):
        """DEC-312: the display role never decides safety."""
        assert openfan_channel_is_pump_protected(_role(role="pump", stop_permitted=True)) is False
        assert openfan_channel_is_pump_protected(_role(stop_permitted=False)) is True

    def test_a_missing_answer_reads_protected_only_for_a_pump(self):
        assert openfan_channel_is_pump_protected(_role(role="pump", stop_permitted=None)) is True
        assert openfan_channel_is_pump_protected(_role(stop_permitted=None)) is False

    def test_an_unreported_channel_is_unprotected(self):
        """An older daemon protects no OpenFan channel; saying otherwise would lie."""
        assert openfan_channel_is_pump_protected(None) is False


# ── view-model ───────────────────────────────────────────────────────────────


class TestRows:
    def test_reporting_channels_and_assigned_absent_ones_are_listed(self):
        roles = [_role(CH0), _role(CH1, "pump"), _role("openfan:ch02")]
        rows = build_openfan_role_rows([_fan(CH0)], roles, lambda fid: f"name {fid}")
        assert [r.fan_id for r in rows] == [CH0, CH1]
        absent = rows[1]
        assert "not reporting" in absent.role_text
        assert absent.protected is True
        assert absent.tone == "success"
        assert rows[0].protected is False
        assert rows[0].tone == "neutral"

    def test_protection_follows_the_predicate_not_the_role(self):
        rows = build_openfan_role_rows(
            [_fan(CH0)], [_role(CH0, "pump", stop_permitted=True)], lambda f: f
        )
        assert rows[0].protected is openfan_channel_is_pump_protected(
            _role(CH0, "pump", stop_permitted=True)
        )
        assert rows[0].protected is False


class TestChoices:
    def test_same_tokens_as_the_header_picker(self):
        from control_ofc.services.header_role_view import ROLE_CHOICES

        assert [c.token for c in OPENFAN_ROLE_CHOICES] == [c.token for c in ROLE_CHOICES]

    def test_no_fan_only_where_the_daemon_accepts_it(self):
        with_flag = [c.token for c in openfan_role_choices(_caps(header_role_no_fan=True))]
        without = [c.token for c in openfan_role_choices(_caps())]
        assert "no_fan" in with_flag
        assert "no_fan" not in without

    def test_no_choice_promises_the_stall_probe(self):
        """There is no stall probe on an OpenFan channel."""
        assert not any("probe" in c.effect for c in OPENFAN_ROLE_CHOICES)


class TestOutcome:
    def test_protection_is_read_back(self):
        result = HeaderRoleResult(updated=True, header_id=CH0, role="pump", effective_role="pump")
        assert "protects it as a pump" in openfan_outcome_message(
            "Pump", result, _role(role="pump")
        )
        assert "protects it" not in openfan_outcome_message(
            "Pump", result, _role(role="pump", stop_permitted=True)
        )

    def test_a_failed_reread_is_admitted(self):
        result = HeaderRoleResult(updated=True, header_id=CH0, role="pump", effective_role="pump")
        assert "could not be re-read" in openfan_outcome_message("Pump", result, None)


# ── Hardware page ────────────────────────────────────────────────────────────


class _Client:
    socket_path = ""

    def __init__(self, roles: list[OpenFanRole], *, error: Exception | None = None):
        self.roles = list(roles)
        self.calls: list[tuple[str, str | None]] = []
        self._error = error

    def set_header_role(self, header_id, role):
        self.calls.append((header_id, role))
        if self._error is not None:
            raise self._error
        self.roles = [
            _role(r.fan_id, role or "unknown") if r.fan_id == header_id else r for r in self.roles
        ]
        return HeaderRoleResult(
            updated=True, header_id=header_id, role=role, effective_role=role or "unknown"
        )

    def openfan_roles(self):
        return list(self.roles)

    def hwmon_headers(self):
        return []


class _StubDialog:
    """`exec()` would block; the dialog itself is covered by the header picker tests."""

    choice: str | None = None
    accept = True
    last_choices: tuple = ()
    last_intro = ""

    def __init__(self, header, name, *, choices=(), intro_text="", **_kw):
        self.header = header
        type(self).last_choices = tuple(c.token for c in choices)
        type(self).last_intro = intro_text

    def exec(self):
        return self.accept

    def chosen_role(self):
        return type(self).choice


def _page(qtbot, monkeypatch, roles, *, caps=None, error=None, fans=(CH0,)):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_capabilities(caps or _caps())
    state.set_fans([_fan(f) for f in fans])
    state.set_openfan_roles(roles)
    client = _Client(roles, error=error)
    page = HardwarePage(state=state, diagnostics_service=DiagnosticsService(state), client=client)
    qtbot.addWidget(page)
    monkeypatch.setattr(hw_mod, "HeaderRoleDialog", _StubDialog)
    return page, state, client


def _btn(page, fan_id=CH0) -> QPushButton:
    return page.findChild(QPushButton, f"Hardware_Btn_openfanRole_{fan_id.replace(':', '_')}")


def _pill(page, fan_id=CH0) -> QLabel:
    return page.findChild(QLabel, f"Hardware_Pill_openfanRole_{fan_id.replace(':', '_')}")


def _outcome(page) -> QLabel:
    return page.findChild(QLabel, "Hardware_Label_openfanOutcome")


class TestHardwarePage:
    def test_the_section_needs_the_capability(self, qtbot, monkeypatch):
        page, _state, _client = _page(
            qtbot, monkeypatch, [_role()], caps=_caps(openfan_header_roles=False)
        )
        assert page._openfan_box.isVisibleTo(page) is False
        assert _btn(page) is None
        shown, _s, _c = _page(qtbot, monkeypatch, [_role()])
        assert shown._openfan_box.isVisibleTo(shown) is True
        assert _btn(shown) is not None

    def test_setting_pump_posts_once_and_reads_protection_back(self, qtbot, monkeypatch):
        page, state, client = _page(qtbot, monkeypatch, [_role()])
        _StubDialog.choice, _StubDialog.accept = "pump", True
        _btn(page).click()
        assert client.calls == [(CH0, "pump")]
        assert "only way the daemon knows" in _StubDialog.last_intro
        assert state.openfan_role(CH0).role == "pump", "AppState got the re-read"
        assert _pill(page).text() == "PUMP"
        assert "protects it as a pump" in _outcome(page).text()

    def test_cancel_writes_nothing(self, qtbot, monkeypatch):
        page, _state, client = _page(qtbot, monkeypatch, [_role()])
        _StubDialog.choice, _StubDialog.accept = "pump", False
        _btn(page).click()
        assert client.calls == []

    def test_removing_a_pump_asks_and_a_no_sends_nothing(self, qtbot, monkeypatch):
        page, _state, client = _page(qtbot, monkeypatch, [_role(role="pump")])
        _StubDialog.choice, _StubDialog.accept = None, True
        asked = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.No,
        )
        _btn(page).click()
        assert asked == ["Remove pump protection from a header?"]
        assert client.calls == []
        assert "kept its pump role" in _outcome(page).text()

    def test_removing_a_pump_after_yes_sends_the_clear(self, qtbot, monkeypatch):
        page, state, client = _page(qtbot, monkeypatch, [_role(role="pump")])
        _StubDialog.choice, _StubDialog.accept = None, True
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        _btn(page).click()
        assert client.calls == [(CH0, None)]
        assert openfan_channel_is_pump_protected(state.openfan_role(CH0)) is False

    def test_a_refusal_claims_nothing_changed(self, qtbot, monkeypatch):
        err = DaemonError(code="persistence_failed", message="read-only fs", status=503)
        page, state, client = _page(qtbot, monkeypatch, [_role()], error=err)
        _StubDialog.choice, _StubDialog.accept = "pump", True
        _btn(page).click()
        assert client.calls == [(CH0, "pump")]
        assert "Nothing was changed" in _outcome(page).text()
        assert state.openfan_role(CH0).role == "unknown"

    def test_an_unchanged_poll_does_not_rebuild_the_rows(self, qtbot, monkeypatch):
        """Recreating the button every second would take keyboard focus away."""
        page, state, _client = _page(qtbot, monkeypatch, [_role()])
        before = _btn(page)
        state.set_fans([_fan(CH0)])
        assert _btn(page) is before
        state.set_openfan_roles([_role(role="pump")])
        assert _pill(page).text() == "PUMP", "a changed role does re-render"


# ── Fan Wizard ───────────────────────────────────────────────────────────────


class TestWizard:
    def _state(self, roles) -> AppState:
        state = AppState()
        state.set_capabilities(
            Capabilities(
                control=ControlCapability(
                    autonomous_control=True, fan_identify=True, header_roles=True
                )
            )
        )
        state.set_openfan_roles(roles)
        return state

    def test_an_assigned_openfan_pump_is_a_pump_target(self, qtbot):
        wiz = FanConfigWizard(self._state([_role(role="pump")]))
        qtbot.addWidget(wiz)
        assert wiz.is_pump_target(CH0) is openfan_channel_is_pump_protected(_role(role="pump"))
        assert wiz.is_pump_target(CH0) is True
        assert wiz.identify_verb(CH0) == "change speed"

    def test_an_unassigned_channel_still_stops(self, qtbot):
        wiz = FanConfigWizard(self._state([_role()]))
        qtbot.addWidget(wiz)
        assert wiz.is_pump_target(CH0) is False
        assert wiz.identify_verb(CH0) == "stop"


# ── calibration ──────────────────────────────────────────────────────────────


def _dialog(qtbot, pump_ids=frozenset()):
    opts = build_channel_options(
        [_fan(CH0), _fan(CH1)], lambda fid: fid, lambda fid: fid in pump_ids
    )
    dialog = OpenFanCalibrationDialog(opts)
    qtbot.addWidget(dialog)
    return (
        dialog,
        dialog.findChild(QPushButton, "OfanCal_Btn_start"),
        dialog.findChild(QCheckBox, "OfanCal_Check_notPump"),
        dialog.findChild(QComboBox, "OfanCal_Combo_channel"),
        dialog.findChild(QLabel, "OfanCal_Label_blocked"),
    )


class TestCalibration:
    def test_options_carry_the_pump_flag(self):
        opts = build_channel_options([_fan(CH0), _fan(CH1)], lambda f: f, lambda f: f == CH1)
        assert [o.pump_protected for o in opts] == [False, True]
        assert "pump, not calibrated" in opts[1].text

    def test_a_pump_channel_cannot_be_started_or_confirmed(self, qtbot):
        dialog, start, check, combo, blocked = _dialog(qtbot, {CH1})
        combo.setCurrentIndex(0)
        check.setChecked(True)
        assert start.isEnabled() is True, "precondition: an ordinary channel can start"
        combo.setCurrentIndex(1)
        assert start.isEnabled() is False
        assert check.isChecked() is False
        assert check.isVisibleTo(dialog) is False
        assert blocked.isVisibleTo(dialog) is True
        assert blocked.text() == pump_channel_refusal(dialog.selected_option().label)

    def test_a_channel_becoming_a_pump_withdraws_the_consent(self, qtbot):
        dialog, start, check, combo, _blocked = _dialog(qtbot)
        combo.setCurrentIndex(1)
        check.setChecked(True)
        assert start.isEnabled() is True
        dialog.set_channels(
            build_channel_options([_fan(CH0), _fan(CH1)], lambda f: f, lambda f: f == CH1)
        )
        assert check.isChecked() is False
        assert start.isEnabled() is False

    def test_the_mid_run_abort_is_worded(self):
        run = OpenFanCalibrationRun(
            run_id="r",
            fan_id=CH0,
            channel=0,
            state="aborted",
            outcome="aborted",
            abort_reason="pump_protected",
            completed_unix_ms=1,
        )
        view = build_calibration_view(run, channel_label="CH0")
        text = " ".join([view.status_text, view.advice, *view.notes])
        assert "set to Pump during the test" in text


# ── polling ──────────────────────────────────────────────────────────────────


def _worker(caps: Capabilities) -> tuple[_PollWorker, MagicMock]:
    client = MagicMock()
    client.capabilities.return_value = caps
    client.hwmon_headers.return_value = []
    client.openfan_roles.return_value = [_role(role="pump")]
    client.poll.return_value = (MagicMock(), [], [])
    worker = _PollWorker(socket_path="/tmp/fake.sock")
    worker._ensure_client = MagicMock(return_value=client)
    return worker, client


def test_polling_fetches_roles_only_with_the_capability(qtbot):
    worker, client = _worker(_caps())
    got = []
    worker.openfan_roles_ready.connect(got.append)
    worker.poll()
    assert client.openfan_roles.call_count == 1
    assert got == [[_role(role="pump")]]

    older, old_client = _worker(_caps(openfan_header_roles=False))
    older.poll()
    assert old_client.openfan_roles.call_count == 0


def test_the_pre_run_warning_promises_the_refusal_only_where_the_daemon_makes_it(qtbot):
    """`UDOC-i`: a daemon without roles walks any channel it is asked to."""
    from control_ofc.services.openfan_calibration_view import PUMP_ROLE_WARNING

    opts = build_channel_options([_fan(CH0)], lambda f: f)
    with_roles = OpenFanCalibrationDialog(opts, pump_roles=True)
    without = OpenFanCalibrationDialog(opts)
    qtbot.addWidget(with_roles)
    qtbot.addWidget(without)
    assert PUMP_ROLE_WARNING in with_roles._intro.text()
    assert PUMP_ROLE_WARNING not in without._intro.text()
    assert "never calibrated" not in without._intro.text()
