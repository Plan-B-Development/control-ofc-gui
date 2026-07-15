"""DEC-212: HardwarePage — rendering + re-pointed action routing + probe.

Constructs the page directly and drives `_on_readiness_ok` (no worker threads);
the probe/worker paths are monkeypatched. Mirrors `test_system_state_page.py`.
"""

from __future__ import annotations

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

from control_ofc.api.models import (
    ConnectionState,
    HardwareReadiness,
    ReadinessItem,
    ReadinessRollup,
    SuperIoChip,
    SuperIoRecommendation,
    SuperIoReport,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.pages.hardware_page import HardwarePage


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
        hwmon_present=False,
        recommendation=SuperIoRecommendation(
            module="it87",
            in_mainline=False,
            load_hint="sudo modprobe it87",
            reason="No driver bound.",
        ),
    )


def _page(qtbot, *, client=None):
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    page = HardwarePage(state=s, diagnostics_service=DiagnosticsService(s), client=client)
    qtbot.addWidget(page)
    return page, s


# ── Rendering ────────────────────────────────────────────────────────────


def test_checklist_renders_grouped_rows(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(
        _hw(
            items=[
                ReadinessItem(code="cpu_sensor_present", severity="ok", summary="CPU temp OK"),
                ReadinessItem(
                    code="no_pwm_controls",
                    severity="warning",
                    summary="No writable PWM headers",
                    detail="Load a Super-I/O driver.",
                ),
            ]
        )
    )
    assert page.findChild(QWidget, "Hardware_Check_cpu_sensor_present") is not None
    assert page.findChild(QWidget, "Hardware_Check_no_pwm_controls") is not None
    # a detail-bearing row has a Details collapsible; a plain ok row does not
    assert page.findChild(QWidget, "Hardware_CheckDetail_no_pwm_controls") is not None
    assert page.findChild(QWidget, "Hardware_CheckDetail_cpu_sensor_present") is None


def test_verdict_and_summary_bar(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(
        _hw(
            overall="warning",
            items=[
                ReadinessItem(code="cpu_sensor_present", severity="ok"),
                ReadinessItem(code="no_pwm_controls", severity="warning", summary="x"),
            ],
        )
    )
    assert page._verdict_pill.text() == "NEEDS ATTENTION"
    pills = page._summary_bar.findChildren(StatusPill)
    texts = [p.text() for p in pills]
    assert any("PASS" in t for t in texts) and any("WARN" in t for t in texts)


# ── Action routing (re-pointed to the migrated pages) ──────────────────────


def test_cpu_action_emits_preferred_sensors(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(
        _hw(items=[ReadinessItem(code="cpu_sensor_missing", severity="critical", summary="x")])
    )
    btn = page.findChild(QPushButton, "Hardware_Do_cpu_sensor_missing")
    with qtbot.waitSignal(page.open_preferred_sensors) as blocker:
        btn.click()
    assert blocker.args == ["cpu"]


def test_pwm_action_emits_open_system_state(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(
        _hw(items=[ReadinessItem(code="pwm_control_unverified", severity="warning", summary="x")])
    )
    btn = page.findChild(QPushButton, "Hardware_Do_pwm_control_unverified")
    with qtbot.waitSignal(page.open_system_state, timeout=1000):
        btn.click()


def test_sensors_action_emits_open_overview(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(
        _hw(items=[ReadinessItem(code="sensors_unavailable", severity="warning", summary="x")])
    )
    btn = page.findChild(QPushButton, "Hardware_Do_sensors_unavailable")
    with qtbot.waitSignal(page.open_overview, timeout=1000):
        btn.click()


def test_superio_action_scrolls_without_crash(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(
        _hw(items=[ReadinessItem(code="no_pwm_controls", severity="warning", summary="x")])
    )
    btn = page.findChild(QPushButton, "Hardware_Do_no_pwm_controls")
    btn.click()  # target "superio" → in-surface scroll, must not raise


# ── Super-I/O table ────────────────────────────────────────────────────────


def test_superio_table_real_columns(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(
        _hw(superio=SuperIoReport(arch_supported=True, chips=[_unbound_chip("it8696")]))
    )
    table = page.findChild(QWidget, "Hardware_Table_superio")
    assert table is not None
    assert table.rowCount() == 1  # one chip, no fabricated per-channel rows
    holder = table.cellWidget(0, 5)  # Health column
    assert holder.findChild(StatusPill) is not None
    assert page.findChild(QWidget, "Hardware_ChipHow_it8696") is not None


def test_copy_command_to_clipboard(qtbot):
    from PySide6.QtWidgets import QApplication

    page, _ = _page(qtbot)
    page._on_readiness_ok(
        _hw(superio=SuperIoReport(arch_supported=True, chips=[_unbound_chip("it8696")]))
    )
    btn = page.findChild(QPushButton, "Hardware_CmdCopy_it8696")
    btn.click()
    assert QApplication.clipboard().text() == "sudo modprobe it87"


# ── Probe / lifecycle ──────────────────────────────────────────────────────


def test_probe_confirm_emits_only_on_yes(qtbot, monkeypatch):
    page, _ = _page(qtbot, client=object())
    page._ensure_readiness_worker = lambda: True  # type: ignore[method-assign]  # no real thread
    fired: list[int] = []
    page._readiness_probe_request.connect(lambda: fired.append(1))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page._confirm_probe()
    assert fired == [1]
    fired.clear()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    page._confirm_probe()
    assert fired == []


def test_showevent_lazy_fetch_once(qtbot):
    page, _ = _page(qtbot)
    calls: list[int] = []
    page._fetch_readiness = lambda *a, **k: calls.append(1)  # type: ignore[method-assign]
    page.showEvent(QShowEvent())
    page.showEvent(QShowEvent())
    assert calls == [1]  # latched → single fetch


def test_showevent_skips_when_unsupported(qtbot):
    page, _ = _page(qtbot)
    page._readiness_unsupported = True
    calls: list[int] = []
    page._fetch_readiness = lambda *a, **k: calls.append(1)  # type: ignore[method-assign]
    page.showEvent(QShowEvent())
    assert calls == []


def test_unsupported_error_latches(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_error("unsupported", "old daemon")
    assert page._readiness_unsupported is True
    assert not page._status_label.isHidden()


def test_probe_ok_rerenders_superio(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(_hw(superio=SuperIoReport(arch_supported=True, chips=[])))
    page._on_readiness_probe_ok(SuperIoReport(arch_supported=True, chips=[_unbound_chip("it8696")]))
    assert page.findChild(QWidget, "Hardware_Table_superio") is not None


def test_set_theme_rerenders(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(
        _hw(items=[ReadinessItem(code="no_pwm_controls", severity="warning", summary="x")])
    )
    page.set_theme(None)
    assert page._verdict_pill.text() == "NEEDS ATTENTION"


def test_no_objectname_leak(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(_hw(superio=SuperIoReport(arch_supported=True, chips=[_unbound_chip()])))
    for child in page.findChildren(QWidget):
        name = child.objectName()
        assert not name.startswith("Diagnostics_"), name
        assert not name.startswith("CoolingReadiness_"), name


def test_cleanup_safe_without_worker(qtbot):
    page, _ = _page(qtbot)
    page.cleanup()  # never fetched → no worker; must not raise
    assert page._readiness_worker is None


def test_fetch_without_client_sets_status(qtbot):
    page, _ = _page(qtbot)  # client None
    page._fetch_readiness()
    assert "no daemon connection" in page._status_label.text()


def test_refresh_forces_fetch(qtbot):
    page, _ = _page(qtbot)
    calls: list = []
    page._fetch_readiness = lambda *a, **k: calls.append(k.get("force"))  # type: ignore[method-assign]
    page._refresh_readiness()
    assert calls == [True]


def test_generic_error_shows_message_without_latching(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_error("error", "boom")
    assert "boom" in page._status_label.text()
    assert page._readiness_unsupported is False


def test_superio_non_x86_and_empty_render(qtbot):
    page, _ = _page(qtbot)
    page._on_readiness_ok(_hw(superio=SuperIoReport(arch_supported=False)))
    assert page.findChild(QWidget, "Hardware_Label_superioNote") is not None
    page._on_readiness_ok(
        _hw(superio=SuperIoReport(arch_supported=True, chips=[], notes=["a note"]))
    )
    assert page.findChild(QWidget, "Hardware_Label_superioNote") is not None
    assert page.findChild(QWidget, "Hardware_Label_superioNotes") is not None
