"""Merged Cooling Hardware Readiness page (DEC-207).

Covers the pure Readiness ⊕ Super-I/O mapping, the ``HardwareReadiness`` model +
client, the ``_HardwareReadinessWorker`` (off-thread, 404-degrade), the
``CoolingReadinessView`` rendering (summaries, ordering, expansion, doc-links,
command copy, probe confirmation, no Super-I/O pollution), and the DiagnosticsPage
integration (no standalone Super-I/O tab, auto-fetch-once, action routing).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from control_ofc.api.models import (
    ConnectionState,
    HardwareReadiness,
    OperationMode,
    ReadinessItem,
    ReadinessRollup,
    SuperIoChip,
    SuperIoRecommendation,
    SuperIoReport,
    parse_hardware_readiness,
)
from control_ofc.services.app_state import AppState
from control_ofc.ui import cooling_readiness as cr
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage
from control_ofc.ui.readiness_merge import (
    ACTION_DEEP_LINK,
    ACTION_IN_SURFACE,
    ACTION_TAB_SWITCH,
    ActionSpec,
)
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.cooling_readiness_view import CoolingReadinessView

# ── Fixtures / builders ──────────────────────────────────────────────


def _hw(overall="warning", items=None, superio=None, **kw) -> HardwareReadiness:
    return HardwareReadiness(
        overall=overall,
        rollup=ReadinessRollup(overall=overall, top_summary=kw.pop("top", None) or None),
        items=items if items is not None else [],
        superio=superio if superio is not None else SuperIoReport(arch_supported=True),
        **kw,
    )


def _unbound_chip(name="it8688") -> SuperIoChip:
    return SuperIoChip(
        chip_name=name,
        vendor="ite",
        confidence="medium",
        expected_module="it87",
        evidence=["dmi_board_table"],
        hwmon_present=False,
        recommendation=SuperIoRecommendation(
            module="it87",
            in_mainline=False,
            load_hint="sudo modprobe it87",
            reason="Chip present but no driver bound.",
            risk_notes=["Needs it87-dkms-git"],
        ),
    )


# ── Pure mapping (control_ofc.ui.cooling_readiness) ──────────────────


def test_build_readiness_items_sorted_most_severe_first_ok_last():
    hw = _hw(
        items=[
            ReadinessItem(code="cpu_sensor_present", severity="ok"),
            ReadinessItem(code="no_pwm_controls", severity="warning"),
            ReadinessItem(code="cpu_sensor_missing", severity="critical"),
            ReadinessItem(code="cpu_default_low_confidence", severity="info"),
        ]
    )
    items = cr.build_readiness_items(hw)
    assert [i.code for i in items] == [
        "cpu_sensor_missing",
        "no_pwm_controls",
        "cpu_default_low_confidence",
        "cpu_sensor_present",
    ]
    assert items[-1].is_ok is True


def test_action_mapping_deep_links_and_in_surface_and_tab_switch():
    def action(code: str) -> ActionSpec:
        (item,) = cr.build_readiness_items(
            _hw(items=[ReadinessItem(code=code, severity="warning")])
        )
        return item.action

    assert action("cpu_sensor_missing") == ActionSpec(
        ACTION_DEEP_LINK, "Pick a CPU sensor", "preferred_cpu"
    )
    assert action("selected_mb_sensor_missing").target == "preferred_mb"
    assert action("pwm_control_unverified") == ActionSpec(
        ACTION_TAB_SWITCH, "Test PWM control", "pwm_verify"
    )
    # Super-I/O codes point at the on-page section, NOT a (retired) tab-switch.
    assert action("no_pwm_controls") == ActionSpec(
        ACTION_IN_SURFACE, "View Super-I/O details", "superio"
    )
    assert action("superio_driver_unloaded").target == "superio"
    assert action("sensors_unavailable") == ActionSpec(ACTION_TAB_SWITCH, "View sensors", "sensors")


def test_doc_links_present_for_problems_absent_for_ok():
    (problem,) = cr.build_readiness_items(
        _hw(items=[ReadinessItem(code="cpu_sensor_missing", severity="critical")])
    )
    assert problem.doc_url.startswith("https://github.com/Plan-B-Development/control-ofc-gui")
    assert "24_Cooling_Hardware_Readiness_Guide.md#" in problem.doc_url
    (ok,) = cr.build_readiness_items(
        _hw(items=[ReadinessItem(code="cpu_sensor_present", severity="ok")])
    )
    assert ok.doc_url == ""


def test_group_mapping_covers_the_four_groups():
    assert cr.group_for("cpu_sensor_missing") == cr.GROUP_TEMP
    assert cr.group_for("no_pwm_controls") == cr.GROUP_FANS
    assert cr.group_for("superio_driver_unloaded") == cr.GROUP_SUPERIO
    assert cr.group_for("sensors_unavailable") == cr.GROUP_SENSORS
    # An unknown code is not dropped — it lands in Sensor configuration.
    assert cr.group_for("brand_new_code") == cr.GROUP_SENSORS


def test_daemon_detail_stays_in_plain_slot_not_html():
    (item,) = cr.build_readiness_items(
        _hw(
            items=[
                ReadinessItem(
                    code="cpu_sensor_missing",
                    severity="critical",
                    detail="<b>hi</b>",
                    recommended_action="do X",
                )
            ]
        )
    )
    # The daemon detail + action are PlainText content; no GUI HTML is fabricated.
    assert "<b>hi</b>" in item.plain_detail
    assert "→ do X" in item.plain_detail
    assert item.html_detail == ""


# ── Model parse ──────────────────────────────────────────────────────


def test_parse_hardware_readiness_full():
    hw = parse_hardware_readiness(
        {
            "api_version": 1,
            "overall": "warning",
            "rollup": {"overall": "warning", "warning": 2, "top_code": "no_pwm_controls"},
            "items": [{"code": "no_pwm_controls", "severity": "warning"}],
            "superio": {"arch_supported": True, "chips": [{"chip_name": "it8688"}]},
            "scanned_age_ms": 1200,
            "generation": 7,
        }
    )
    assert hw.overall == "warning"
    assert hw.rollup.warning == 2
    assert hw.items[0].code == "no_pwm_controls"
    assert hw.superio.chips[0].chip_name == "it8688"
    assert hw.scanned_age_ms == 1200
    assert hw.generation == 7


def test_parse_hardware_readiness_defaults_and_malformed_tolerated():
    # Absent nested objects → empty defaults; malformed types don't raise.
    hw = parse_hardware_readiness({})
    assert hw.overall == "ok"
    assert hw.rollup.overall == "ok"
    assert hw.items == []
    assert hw.superio.chips == []
    hw2 = parse_hardware_readiness(
        {"rollup": "nope", "superio": 5, "scanned_age_ms": "bad", "generation": None}
    )
    assert isinstance(hw2.rollup, ReadinessRollup)
    assert isinstance(hw2.superio, SuperIoReport)
    assert hw2.scanned_age_ms == 0 and hw2.generation == 0


def test_parse_hardware_readiness_partial_failure_fields():
    hw = parse_hardware_readiness({"scan_degraded": True, "sources_unavailable": ["/dev/kmsg", 5]})
    assert hw.scan_degraded is True
    assert hw.sources_unavailable == ["/dev/kmsg"]  # non-str dropped


# ── Client ───────────────────────────────────────────────────────────


def test_client_hardware_readiness_get_and_force():
    from control_ofc.api.client import DaemonClient

    client = DaemonClient.__new__(DaemonClient)
    client._get = MagicMock(return_value={"overall": "ok", "items": []})
    client.hardware_readiness()
    client._get.assert_called_once_with("/inventory/hardware-readiness", params=None)
    client._get.reset_mock()
    client.hardware_readiness(force=True)
    client._get.assert_called_once_with("/inventory/hardware-readiness", params={"refresh": "true"})


# ── Worker (off-thread call, run synchronously here) ─────────────────


class _WorkerClient:
    def __init__(self, hw=None, superio=None, error=None, probe_error=None):
        self._hw = hw if hw is not None else _hw(overall="ok")
        self._superio = superio if superio is not None else SuperIoReport(arch_supported=True)
        self._error = error
        self._probe_error = probe_error
        self.force_calls: list[bool] = []
        self.socket_path = "/tmp/control-ofc-cr-test.sock"

    def hardware_readiness(self, force: bool = False) -> HardwareReadiness:
        self.force_calls.append(force)
        if self._error is not None:
            raise self._error
        return self._hw

    def superio_probe(self) -> SuperIoReport:
        if self._probe_error is not None:
            raise self._probe_error
        return self._superio

    def close(self) -> None:
        pass


def _worker(client):
    from control_ofc.ui.pages.diagnostics_workers import _HardwareReadinessWorker

    w = _HardwareReadinessWorker("/tmp/x.sock")
    w._client = client  # inject: _ensure_client returns this instead of building one
    return w


def test_worker_do_fetch_and_do_refresh_emit_and_flag_force():
    client = _WorkerClient(hw=_hw(overall="warning"))
    w = _worker(client)
    got = []
    w.fetch_ok.connect(got.append)
    w.do_fetch()
    w.do_refresh()
    assert [r.overall for r in got] == ["warning", "warning"]
    assert client.force_calls == [False, True]


def test_worker_404_signals_unsupported():
    from control_ofc.api.errors import DaemonError

    err = DaemonError(status=404, code="not_found", message="nope")
    w = _worker(_WorkerClient(error=err))
    cats = []
    w.fetch_error.connect(lambda cat, msg: cats.append(cat))
    w.do_fetch()
    assert cats == ["unsupported"]


def test_worker_probe_uses_dedicated_signals():
    w = _worker(_WorkerClient(superio=SuperIoReport(arch_supported=True, chips=[_unbound_chip()])))
    got = []
    w.probe_ok.connect(got.append)
    w.do_probe()
    assert got and got[0].chips[0].chip_name == "it8688"


def test_worker_probe_404_is_transient_error_not_unsupported():
    from control_ofc.api.errors import DaemonError

    w = _worker(_WorkerClient(probe_error=DaemonError(status=404, code="not_found", message="x")))
    errs = []
    w.probe_error.connect(lambda cat, msg: errs.append(cat))
    w.do_probe()
    assert errs == ["error"]  # a probe 404 must NOT flip the passive panel unsupported


# ── View (CoolingReadinessView) ──────────────────────────────────────


def _find(view, name):
    return next((w for w in view.findChildren(QLabel) if w.objectName() == name), None)


def test_view_ready_summary_has_no_recommended_actions(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(_hw(overall="ok", items=[ReadinessItem(code="cpu_sensor_present", severity="ok")]))
    assert "Hardware ready" in v._verdict.text()
    # No actionable items → no Recommended actions section.
    assert not any(w.objectName() == "CoolingReadiness_Recommended" for w in v.findChildren(object))


def test_view_needs_attention_and_not_ready_summaries(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(
        _hw(overall="warning", items=[ReadinessItem(code="no_pwm_controls", severity="warning")])
    )
    assert "Needs attention" in v._verdict.text()
    v.set_report(
        _hw(
            overall="critical",
            items=[ReadinessItem(code="cpu_sensor_missing", severity="critical")],
        )
    )
    assert "Not ready" in v._verdict.text()


def test_view_recommended_actions_severity_ordered(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(
        _hw(
            overall="critical",
            items=[
                ReadinessItem(code="no_pwm_controls", severity="warning", summary="w"),
                ReadinessItem(code="cpu_sensor_missing", severity="critical", summary="c"),
            ],
        )
    )
    headlines = [
        w.objectName()
        for w in v.findChildren(QLabel)
        if w.objectName().startswith("CoolingReadiness_Headline_")
    ]
    assert headlines.index("CoolingReadiness_Headline_cpu_sensor_missing") < headlines.index(
        "CoolingReadiness_Headline_no_pwm_controls"
    )


def test_view_learn_more_autoexpands_for_warning_collapsed_for_info(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(
        _hw(
            overall="warning",
            items=[
                ReadinessItem(code="no_pwm_controls", severity="warning", detail="d"),
                ReadinessItem(code="cpu_default_low_confidence", severity="info", detail="d"),
            ],
        )
    )
    warn = v.findChild(CollapsibleSection, "CoolingReadiness_Learn_no_pwm_controls")
    info = v.findChild(CollapsibleSection, "CoolingReadiness_Learn_cpu_default_low_confidence")
    assert warn is not None and warn.is_expanded() is True
    assert info is not None and info.is_expanded() is False


def test_view_doc_link_is_richtext_daemon_headline_is_plaintext(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(
        _hw(
            overall="critical",
            items=[ReadinessItem(code="cpu_sensor_missing", severity="critical", detail="d")],
        )
    )
    headline = _find(v, "CoolingReadiness_Headline_cpu_sensor_missing")
    doc = _find(v, "CoolingReadiness_Doc_cpu_sensor_missing")
    assert headline is not None and headline.textFormat() == Qt.TextFormat.PlainText
    assert doc is not None and doc.textFormat() == Qt.TextFormat.RichText
    assert "<a href" in doc.text()


def test_view_command_copy_writes_clipboard(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(
        _hw(overall="warning", superio=SuperIoReport(arch_supported=True, chips=[_unbound_chip()]))
    )
    btn = next(
        w
        for w in v.findChildren(QPushButton)
        if w.objectName() == "CoolingReadiness_CmdCopy_it8688"
    )
    btn.click()
    assert QApplication.clipboard().text() == "sudo modprobe it87"


def test_view_no_superio_chip_shows_concise_note_not_cards(qtbot):
    # The daemon (DEC-207) never lists amdgpu/k10temp/nvme/spd5118 as Super-I/O
    # chips, so a real fixed-daemon report has an empty chip list here — the view
    # must show ONE concise note, not a card per device.
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(_hw(overall="warning", superio=SuperIoReport(arch_supported=True, chips=[])))
    assert not any(
        w.objectName().startswith("CoolingReadiness_Chip_") for w in v.findChildren(object)
    )
    note = _find(v, "CoolingReadiness_Superio_note")
    assert note is not None and "No motherboard Super-I/O chip" in note.text()


def test_view_only_genuine_superio_chip_rendered(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(
        _hw(
            overall="warning",
            superio=SuperIoReport(arch_supported=True, chips=[_unbound_chip("nct6799")]),
        )
    )
    chip_cards = {
        w.objectName()
        for w in v.findChildren(object)
        if w.objectName().startswith("CoolingReadiness_Chip_") and "How" not in w.objectName()
    }
    assert chip_cards == {"CoolingReadiness_Chip_nct6799"}


def test_view_liability_line_shown_with_unbound_chip(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(
        _hw(overall="warning", superio=SuperIoReport(arch_supported=True, chips=[_unbound_chip()]))
    )
    liab = _find(v, "CoolingReadiness_Superio_liability")
    assert (
        liab is not None and "Control-OFC does not apply these changes automatically" in liab.text()
    )


def test_view_partial_scan_degraded_surfaced(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(_hw(overall="ok", scan_degraded=True, sources_unavailable=["/dev/kmsg"]))
    assert "could not read /dev/kmsg" in v._meta.text()
    assert "read-only" in v._meta.text()


def test_view_advanced_probe_button_gated_and_confirmed(qtbot, monkeypatch):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    # Not available → probe button disabled.
    v.set_report(
        _hw(overall="ok", superio=SuperIoReport(arch_supported=True, port_probe_available=False))
    )
    assert v._probe_btn.isEnabled() is False
    # Available → enabled; confirming (Yes) emits probe_requested.
    v.set_report(
        _hw(overall="ok", superio=SuperIoReport(arch_supported=True, port_probe_available=True))
    )
    assert v._probe_btn.isEnabled() is True
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes, raising=False
    )
    with qtbot.waitSignal(v.probe_requested, timeout=1000):
        v._probe_btn.click()


def test_view_probe_declined_does_not_emit(qtbot, monkeypatch):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(
        _hw(overall="ok", superio=SuperIoReport(arch_supported=True, port_probe_available=True))
    )
    fired = []
    v.probe_requested.connect(lambda: fired.append(1))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No, raising=False
    )
    v._probe_btn.click()
    assert fired == []


def test_view_unsupported_and_error_states(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_unsupported()
    assert "predates this feature" in v._status.text()
    v.set_error("boom")
    assert v._status.text() == "boom"
    assert v._status.textFormat() == Qt.TextFormat.PlainText  # error strings never markup


# ── DiagnosticsPage integration ──────────────────────────────────────


def _state() -> AppState:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    return s


def test_page_has_no_standalone_superio_tab(qtbot):
    page = DiagnosticsPage(state=_state())
    qtbot.addWidget(page)
    tabs = [page._tabs.tabText(i) for i in range(page._tabs.count())]
    assert "Super-I/O" not in tabs
    assert tabs == ["Overview", "Sensors", "Fans", "Troubleshooting", "Readiness", "Event Log"]
    assert isinstance(page._readiness_view, CoolingReadinessView)


def test_page_readiness_auto_fetches_once(qtbot, monkeypatch):
    page = DiagnosticsPage(state=_state())
    qtbot.addWidget(page)
    calls = []
    monkeypatch.setattr(page, "_fetch_readiness", lambda **k: calls.append(k))
    page._on_diag_tab_changed(page._readiness_tab_index)
    page._on_diag_tab_changed(page._readiness_tab_index)
    assert len(calls) == 1  # guarded by _readiness_auto_fetched


def test_page_routes_actions(qtbot):
    page = DiagnosticsPage(state=_state())
    qtbot.addWidget(page)
    roles = []
    page.open_preferred_sensors.connect(roles.append)
    page._route_readiness_action(ActionSpec(ACTION_DEEP_LINK, "", "preferred_cpu"))
    page._route_readiness_action(ActionSpec(ACTION_DEEP_LINK, "", "preferred_mb"))
    assert roles == ["cpu", "mb"]
    page._route_readiness_action(ActionSpec(ACTION_TAB_SWITCH, "", "sensors"))
    assert page._tabs.currentIndex() == page._sensors_tab_index
    page._route_readiness_action(ActionSpec(ACTION_TAB_SWITCH, "", "pwm_verify"))
    assert page._tabs.currentIndex() == page._troubleshooting_tab_index


def test_page_readiness_handlers_drive_the_view(qtbot):
    page = DiagnosticsPage(state=_state())
    qtbot.addWidget(page)
    page._on_readiness_ok(
        _hw(
            overall="critical",
            items=[ReadinessItem(code="cpu_sensor_missing", severity="critical")],
        )
    )
    assert "Not ready" in page._readiness_view._verdict.text()
    # A probe result updates ONLY the Super-I/O section (dedicated handler).
    page._on_readiness_probe_ok(SuperIoReport(arch_supported=True, chips=[_unbound_chip()]))
    assert any(
        w.objectName() == "CoolingReadiness_Chip_it8688"
        for w in page._readiness_view.findChildren(object)
    )
    # 404 marks unsupported and latches the flag.
    page._on_readiness_error("unsupported", "old")
    assert page._readiness_unsupported is True


def test_page_refresh_forces_scan(qtbot, monkeypatch):
    page = DiagnosticsPage(state=_state(), client=_WorkerClient())
    qtbot.addWidget(page)
    monkeypatch.setattr(page, "_ensure_readiness_worker", lambda: True)
    fired = {"fetch": 0, "refresh": 0}
    page._readiness_request.connect(lambda: fired.__setitem__("fetch", fired["fetch"] + 1))
    page._readiness_refresh_request.connect(
        lambda: fired.__setitem__("refresh", fired["refresh"] + 1)
    )
    page._refresh_readiness()
    assert fired == {"fetch": 0, "refresh": 1}


# ── View: hardware-checks section + misc rendering ───────────────────


def test_view_hardware_checks_grouped_rows(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(
        _hw(
            overall="warning",
            items=[
                ReadinessItem(code="cpu_sensor_present", severity="ok", summary="CPU ok"),
                ReadinessItem(
                    code="no_pwm_controls", severity="warning", summary="No PWM", detail="d"
                ),
                ReadinessItem(code="superio_acpi_conflict", severity="warning", summary="ACPI"),
                ReadinessItem(code="unknown_sensors_present", severity="info", summary="Unknown"),
            ],
        )
    )
    names = {w.objectName() for w in v.findChildren(object)}
    # Each emitted finding gets a compact check row in its group.
    for code in (
        "cpu_sensor_present",
        "no_pwm_controls",
        "superio_acpi_conflict",
        "unknown_sensors_present",
    ):
        assert f"CoolingReadiness_Check_{code}" in names
    # A row with detail is expandable; a passing row with no detail is not.
    assert (
        v.findChild(CollapsibleSection, "CoolingReadiness_CheckDetail_no_pwm_controls") is not None
    )
    assert (
        v.findChild(CollapsibleSection, "CoolingReadiness_CheckDetail_cpu_sensor_present") is None
    )


def test_view_last_scanned_time_formatting(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(_hw(overall="ok", scanned_age_ms=0))
    assert "Last scanned just now" in v._meta.text()
    v.set_report(_hw(overall="ok", scanned_age_ms=125_000))
    assert "Last scanned 2m ago" in v._meta.text()


def test_view_superio_arch_unsupported_note(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.set_report(_hw(overall="ok", superio=SuperIoReport(arch_supported=False)))
    note = _find(v, "CoolingReadiness_Superio_note")
    assert note is not None and "x86" in note.text()


def test_view_scroll_to_superio_is_safe(qtbot):
    v = CoolingReadinessView()
    qtbot.addWidget(v)
    v.scroll_to_superio()  # before any report — must not crash
    v.set_report(
        _hw(overall="warning", superio=SuperIoReport(arch_supported=True, chips=[_unbound_chip()]))
    )
    v.scroll_to_superio()  # with a superio section present
