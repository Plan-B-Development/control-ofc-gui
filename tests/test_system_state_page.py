"""DEC-211: SystemStatePage — rendering + preserved verify/GPU actions.

Constructs the page directly (like the Overview/Logs page tests) and drives the
handlers, reusing the `HardwareDiagnosticsResult` + verify-result fixtures from
the Diagnostics tests. Worker threads are avoided — the batch state machine is
driven through the public handlers and the journal-style worker paths are
monkeypatched, so nothing depends on thread timing.
"""

from __future__ import annotations

import types

from PySide6.QtGui import QDesktopServices, QShowEvent
from PySide6.QtWidgets import QFrame, QPushButton, QWidget

from control_ofc.api.models import (
    AcpiConflictInfo,
    BoardInfo,
    ConnectionState,
    GpuDiagnosticsInfo,
    GpuVerifyResult,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    HwmonHeader,
    HwmonVerifyResult,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.pages.system_state_page import SystemStatePage


def _state() -> AppState:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    return s


def _diag(**overrides) -> HardwareDiagnosticsResult:
    hwmon = overrides.pop("hwmon", None) or HwmonDiagnostics(
        chips_detected=[
            HwmonChipInfo(
                chip_name="nct6798",
                expected_driver="nct6775",
                in_mainline_kernel=True,
                header_count=5,
            )
        ],
        total_headers=5,
        writable_headers=3,
    )
    defaults = dict(
        hwmon=hwmon,
        board=BoardInfo(vendor="ASUS", name="ProArt X870E", bios_version="1234"),
        kernel_modules=[],
    )
    defaults.update(overrides)
    return HardwareDiagnosticsResult(**defaults)


def _diag_acpi() -> HardwareDiagnosticsResult:
    return _diag(
        acpi_conflicts=[
            AcpiConflictInfo(
                io_range="0x0290-0x0299", claimed_by="ACPI", conflicts_with_driver="it87"
            )
        ]
    )


def _diag_revert(header_id="hwmon:it8696:pwm1", count=996) -> HardwareDiagnosticsResult:
    return _diag(
        hwmon=HwmonDiagnostics(
            total_headers=1, writable_headers=1, enable_revert_counts={header_id: count}
        )
    )


def _page(qtbot, *, state=None, client=None, profile_service=None):
    s = state or _state()
    page = SystemStatePage(
        state=s,
        diagnostics_service=DiagnosticsService(s),
        client=client,
        profile_service=profile_service,
    )
    qtbot.addWidget(page)
    return page, s


# ── Rendering ────────────────────────────────────────────────────────────


def test_render_populates_issue_cards(qtbot):
    page, _ = _page(qtbot)
    page._render(_diag_acpi())
    cards = [
        w for w in page.findChildren(QFrame) if w.objectName().startswith("SystemState_IssueCard_")
    ]
    assert cards  # at least one issue card (acpi + no chips etc.)


def test_doc_link_button_opens_url(qtbot, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: calls.append(url.toString()))
    page, _ = _page(qtbot)
    page._render(_diag_acpi())
    btn = page.findChild(QPushButton, "SystemState_IssueDoc_acpi")
    assert btn is not None
    btn.click()
    assert calls == ["https://wiki.archlinux.org/title/Lm_sensors"]


def test_gauge_shows_high_contention(qtbot):
    page, _ = _page(qtbot)
    page._render(_diag_revert("hwmon:it8696:pwm1", 996))
    assert page._gauge.fraction() == 1.0
    assert page._gauge.state() == "crit"
    assert page._contention_title.text() == "High Contention Detected"
    assert page._header_id_label.text() == "hwmon:it8696:pwm1"


def test_registry_table_has_status_pills(qtbot):
    state = _state()
    page, _ = _page(qtbot, state=state)
    page._render(
        _diag(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(chip_name="nct6798", expected_driver="nct6775", header_count=5)
                ],
                total_headers=5,
                writable_headers=3,
            ),
            kernel_modules=[],
        )
    )
    assert page._registry_table.rowCount() == 1  # one chip, no modules
    holder = page._registry_table.cellWidget(0, 0)
    assert holder.findChild(StatusPill) is not None


def test_no_issues_shows_ready(qtbot):
    page, _ = _page(qtbot)
    healthy = _diag(
        board=BoardInfo(vendor="", name="Generic"),
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="nct6779", expected_driver="nct6775", header_count=5)
            ],
            total_headers=5,
            writable_headers=5,
        ),
        kernel_modules=[],
    )
    page._render(healthy)
    assert page._issue_pill.text() == "SYSTEM READY"
    assert page.findChild(QWidget, "SystemState_Label_noIssues") is not None


# ── Verify handlers ──────────────────────────────────────────────────────


def test_show_verify_result_effective(qtbot):
    page, _ = _page(qtbot)
    page._show_verify_result(HwmonVerifyResult(header_id="pwm1", result="effective"))
    assert not page._verify_result_label.isHidden()
    assert "working correctly" in page._verify_result_label.text()
    assert page._verify_result_label.property("class") == "SuccessChip"


def test_show_verify_result_reverted_is_critical(qtbot):
    page, _ = _page(qtbot)
    page._show_verify_result(HwmonVerifyResult(header_id="pwm1", result="pwm_enable_reverted"))
    assert page._verify_result_label.property("class") == "CriticalChip"


def test_verify_all_state_machine_drains(qtbot):
    state = _state()
    state.set_hwmon_headers(
        [HwmonHeader(id="pwm1", is_writable=True), HwmonHeader(id="pwm2", is_writable=True)]
    )
    page, _ = _page(qtbot, state=state, client=object())
    page._ensure_verify_worker = lambda: True  # type: ignore[method-assign]  # no real thread
    emitted: list[str] = []
    page._verify_request.connect(emitted.append)
    page._run_pwm_verify_all()
    assert emitted == ["pwm1"]
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm1", result="effective"))
    assert emitted == ["pwm1", "pwm2"]
    page._on_verify_ok(HwmonVerifyResult(header_id="pwm2", result="pwm_enable_reverted"))
    assert page._verify_all_total == 0  # finished
    assert "2/2 tested" in page._verify_all_progress_label.text()
    assert page._verify_all_progress_label.property("class") == "CriticalChip"


def test_show_gpu_verify_result(qtbot):
    page, _ = _page(qtbot)
    page._show_gpu_verify_result(GpuVerifyResult(gpu_id="0000:03:00.0", result="effective"))
    assert page._gpu_verify_result_label.property("class") == "SuccessChip"
    page._show_gpu_verify_result(GpuVerifyResult(gpu_id="0000:03:00.0", result="write_failed"))
    assert page._gpu_verify_result_label.property("class") == "CriticalChip"


def test_gpu_verify_availability_needs_writable_gpu_and_version(qtbot):
    from control_ofc.api.models import Capabilities

    state = _state()
    state.set_capabilities(Capabilities(daemon_version="1.11.0"))
    page, _ = _page(qtbot, state=state)
    page._update_gpu_verify_availability(
        _diag(gpu=GpuDiagnosticsInfo(pci_bdf="0000:03:00.0", fan_control_method="pmfw_curve"))
    )
    assert not page._gpu_verify_btn.isHidden()  # isVisible() is False on an unshown page
    assert page._gpu_verify_bdf == "0000:03:00.0"
    # read-only GPU → hidden
    page._update_gpu_verify_availability(
        _diag(gpu=GpuDiagnosticsInfo(pci_bdf="0000:03:00.0", fan_control_method="read_only"))
    )
    assert page._gpu_verify_btn.isHidden() is True


# ── GPU-restore gate ─────────────────────────────────────────────────────


def _fake_profile_service(gpu: bool):
    member = types.SimpleNamespace(
        target_id="amd_gpu:0000:03:00.0" if gpu else "hwmon:nct6798:pwm1"
    )
    control = types.SimpleNamespace(members=[member])
    profile = types.SimpleNamespace(controls=[control])
    return types.SimpleNamespace(active_profile=profile)


def test_gpu_restore_gate_disables_when_profile_controls_gpu(qtbot):
    page, _ = _page(qtbot, profile_service=_fake_profile_service(gpu=True))
    page._update_gpu_restore_gate()
    assert page._active_profile_controls_gpu() is True
    assert page._gpu_restore_btn.isEnabled() is False


def test_gpu_restore_gate_enabled_when_profile_does_not(qtbot):
    page, _ = _page(qtbot, profile_service=_fake_profile_service(gpu=False))
    page._update_gpu_restore_gate()
    assert page._active_profile_controls_gpu() is False
    assert page._gpu_restore_btn.isEnabled() is True


# ── showEvent / theme / leak / cleanup ───────────────────────────────────


def test_showevent_renders_from_cache_without_worker(qtbot):
    state = _state()
    diag = DiagnosticsService(state)
    diag.last_hw_diagnostics = _diag_acpi()
    page = SystemStatePage(state=state, diagnostics_service=diag)
    qtbot.addWidget(page)
    page.showEvent(QShowEvent())
    assert page._hw_diag_worker is None  # rendered from cache, never fetched
    assert page._issue_pill.text() != "—"


def test_showevent_latches_and_never_double_fetches(qtbot):
    # No cache + no client → the fetch latches but creates no worker.
    page, _ = _page(qtbot)  # client=None
    page.showEvent(QShowEvent())
    assert page._hw_diag_fetched is True
    assert page._hw_diag_worker is None
    page.showEvent(QShowEvent())  # second show is a no-op
    assert page._hw_diag_worker is None


def test_set_theme_rerenders(qtbot):
    state = _state()
    diag = DiagnosticsService(state)
    diag.last_hw_diagnostics = _diag_acpi()
    page = SystemStatePage(state=state, diagnostics_service=diag)
    qtbot.addWidget(page)
    page.set_theme(None)  # renders from cache without raising
    assert page._issue_pill.text() != "—"


def test_no_diagnostics_objectnames_leak(qtbot):
    page, _ = _page(qtbot)
    page._render(_diag_acpi())
    for child in page.findChildren(QWidget):
        assert not child.objectName().startswith("Diagnostics_"), child.objectName()
        assert not child.objectName().startswith("ReadinessReport_"), child.objectName()


def test_cleanup_is_safe_without_workers(qtbot):
    page, _ = _page(qtbot)
    page.cleanup()  # never fetched → no workers; must not raise
    assert page._verify_worker is None
    assert page._hw_diag_worker is None
    assert page._gpu_verify_worker is None


# ── Extra handler paths ──────────────────────────────────────────────────


def test_render_safety_with_gpu_shows_speed_bar(qtbot):
    page, _ = _page(qtbot)
    page._render(
        _diag(
            gpu=GpuDiagnosticsInfo(
                pci_bdf="0000:03:00.0",
                model_name="RX 9070 XT",
                fan_control_method="pmfw_curve",
                overdrive_enabled=True,
                fan_speed_min_pct=15,
                fan_speed_max_pct=100,
            )
        )
    )
    assert page._gpu_model_label.text() == "RX 9070 XT"
    assert not page._speed_bar_holder.isHidden()  # firmware speed-range bar shown


def test_run_pwm_verify_guards(qtbot):
    page, _ = _page(qtbot)  # no client, empty combo
    page._run_pwm_verify()
    assert "No writable header selected" in page._verify_result_label.text()
    page._verify_combo.addItem("CPU (pwm1)", "pwm1")
    page._run_pwm_verify()  # header selected but no client
    assert "no daemon connection" in page._verify_result_label.text()


def test_on_verify_error_shows_message(qtbot):
    page, _ = _page(qtbot)
    page._on_verify_error("unavailable", "daemon down")
    assert "daemon down" in page._verify_result_label.text()
    assert not page._verify_result_label.isHidden()


def test_run_gpu_verify_without_bdf(qtbot):
    page, _ = _page(qtbot)
    page._gpu_verify_bdf = None
    page._run_gpu_verify()
    assert "No GPU" in page._gpu_verify_result_label.text()


def test_run_gpu_restore_gated_refuses(qtbot):
    page, _ = _page(qtbot, client=object(), profile_service=_fake_profile_service(gpu=True))
    page._gpu_verify_bdf = "0000:03:00.0"
    page._run_gpu_restore()
    assert "Not restored" in page._gpu_restore_result_label.text()


def test_on_gpu_restore_ok_logs_and_messages(qtbot):
    from control_ofc.api.models import GpuFanResetResult

    page, _ = _page(qtbot)
    page._on_gpu_restore_ok(GpuFanResetResult(gpu_id="0000:03:00.0", reset=True))
    assert "restored to automatic" in page._gpu_restore_result_label.text()
    assert page._gpu_restore_result_label.property("class") == "SuccessChip"


def test_open_readiness_report_creates_dialog(qtbot):
    state = _state()
    diag = DiagnosticsService(state)
    diag.last_hw_diagnostics = _diag_acpi()
    page = SystemStatePage(state=state, diagnostics_service=diag)
    qtbot.addWidget(page)
    page._open_readiness_report()
    assert page._report_dialog is not None
    page.cleanup()  # closes + drops the dialog
    assert page._report_dialog is None


def test_fetch_without_client_sets_message(qtbot):
    page, _ = _page(qtbot)  # client None
    page._fetch_hardware_diagnostics()
    assert "no daemon connection" in page._summary_label.text()


# ── hwmon rescan (footer action relocated from Diagnostics — DEC-216) ──


def test_run_hwmon_rescan_no_client_shows_error(qtbot):
    page, _ = _page(qtbot, client=None)
    page.run_hwmon_rescan()
    assert page._rescan_result_label.text() == "Cannot rescan: no daemon connection"
    assert page._rescan_result_label.property("class") == "CriticalChip"
    assert page._rescan_in_flight is False


def test_run_hwmon_rescan_emits_request(qtbot, monkeypatch):
    page, _ = _page(qtbot, client=object())
    monkeypatch.setattr(page, "_ensure_hw_diag_worker", lambda: True)
    fired: list[bool] = []
    page._rescan_request.connect(lambda: fired.append(True))
    page.run_hwmon_rescan()
    assert fired == [True]
    assert page._rescan_in_flight is True
    assert "Rescanning" in page._rescan_result_label.text()


def test_run_hwmon_rescan_in_flight_guard(qtbot, monkeypatch):
    page, _ = _page(qtbot, client=object())
    monkeypatch.setattr(page, "_ensure_hw_diag_worker", lambda: True)
    fired: list[bool] = []
    page._rescan_request.connect(lambda: fired.append(True))
    page._rescan_in_flight = True
    page.run_hwmon_rescan()
    assert fired == []  # a re-entrant call while one is pending is ignored


def test_on_rescan_ok_pushes_headers_clears_cache_and_refetches(qtbot, monkeypatch):
    page, state = _page(qtbot, client=object())
    page._rescan_in_flight = True
    cleared: list[bool] = []
    monkeypatch.setattr(
        "control_ofc.ui.pages.system_state_page.clear_libsensors_cache",
        lambda: cleared.append(True),
    )
    pushed: list[object] = []
    monkeypatch.setattr(state, "set_hwmon_headers", lambda h: pushed.append(h))
    refetched: list[bool] = []
    monkeypatch.setattr(page, "_fetch_hardware_diagnostics", lambda: refetched.append(True))

    headers = [object(), object()]
    page._on_rescan_ok(headers)

    assert cleared == [True]
    assert pushed == [headers]
    assert refetched == [True]
    assert page._rescan_in_flight is False
    assert "2 PWM header(s) found" in page._rescan_result_label.text()
    assert page._rescan_result_label.property("class") == "SuccessChip"


def test_on_rescan_error_surfaces_and_resets(qtbot):
    page, _ = _page(qtbot, client=object())
    page._rescan_in_flight = True
    page._on_rescan_error("error", "boom")
    assert page._rescan_result_label.text() == "Rescan error: boom"
    assert page._rescan_result_label.property("class") == "CriticalChip"
    assert page._rescan_in_flight is False


def test_on_rescan_error_unavailable_uses_message(qtbot):
    page, _ = _page(qtbot, client=object())
    page._on_rescan_error("unavailable", "")
    assert "Daemon unavailable" in page._rescan_result_label.text()
    assert page._rescan_result_label.property("class") == "CriticalChip"


def test_on_rescan_ok_end_to_end_pushes_headers_and_caveat(qtbot, monkeypatch):
    """End-to-end (unspied): fresh headers flow through AppState (so every
    ``headers_updated`` consumer sees them) and the honest control-hardware
    caveat is surfaced. Re-vehicled from the retired Diagnostics page (DEC-216)."""
    page, state = _page(qtbot, client=object())
    monkeypatch.setattr(page, "_fetch_hardware_diagnostics", lambda: None)
    fresh = [
        HwmonHeader(id="hwmon:nct6775:pwm1", label="CPU_FAN", is_writable=True),
        HwmonHeader(id="hwmon:nct6775:pwm2", label="SYS_FAN1", is_writable=True),
    ]
    emitted: list[list] = []
    state.headers_updated.connect(emitted.append)

    page._on_rescan_ok(fresh)

    assert state.hwmon_headers == fresh
    assert emitted == [fresh]
    assert "2 PWM header(s)" in page._rescan_result_label.text()
    assert "daemon restart" in page._rescan_result_label.text()
    assert page._rescan_result_label.property("class") == "SuccessChip"


def test_on_rescan_error_keeps_existing_headers_and_skips_refetch(qtbot, monkeypatch):
    old = [HwmonHeader(id="hwmon:it8696:pwm1", label="CHA_FAN1", is_writable=True)]
    page, state = _page(qtbot, client=object())
    state.set_hwmon_headers(old)
    refetched: list[bool] = []
    monkeypatch.setattr(page, "_fetch_hardware_diagnostics", lambda: refetched.append(True))

    page._on_rescan_error("error", "scan failed")

    assert state.hwmon_headers == old  # a failed re-enumeration never clobbers
    assert refetched == []  # no diagnostics refetch chained on failure
    assert page._rescan_result_label.text() == "Rescan error: scan failed"


def test_on_rescan_ok_clears_real_libsensors_cache(qtbot, monkeypatch):
    """A rescan may follow an /etc/sensors.d relabel, so _on_rescan_ok must drop
    the module-global libsensors cache — relabelled headers then re-resolve
    without a GUI restart. Re-vehicled from the retired Diagnostics page."""
    from control_ofc.knowledge import hwmon_label_resolver as hlr

    page, _ = _page(qtbot, client=object())
    monkeypatch.setattr(page, "_fetch_hardware_diagnostics", lambda: None)
    monkeypatch.setattr(hlr, "_libsensors_cache", ["sentinel"], raising=False)
    assert hlr._libsensors_cache is not None

    page._on_rescan_ok([])

    assert hlr._libsensors_cache is None


# ── GPU restore run paths (re-vehicled from the retired Diagnostics page) ──


def test_run_gpu_restore_without_bdf_shows_message(qtbot):
    page, _ = _page(qtbot)
    page._gpu_verify_bdf = None
    page._run_gpu_restore()
    assert "No GPU" in page._gpu_restore_result_label.text()


def test_run_gpu_restore_without_client_shows_message(qtbot):
    page, _ = _page(qtbot, client=None)
    page._gpu_verify_bdf = "0000:03:00.0"
    page._run_gpu_restore()
    assert "no daemon connection" in page._gpu_restore_result_label.text().lower()


def test_on_gpu_restore_ok_noop_warns(qtbot):
    from control_ofc.api.models import GpuFanResetResult

    page, _ = _page(qtbot)
    page._on_gpu_restore_ok(GpuFanResetResult(gpu_id="0000:03:00.0", reset=False))
    assert page._gpu_restore_result_label.property("class") == "WarningChip"
    assert "no restore" in page._gpu_restore_result_label.text().lower()


def test_on_gpu_restore_error_shows_critical(qtbot):
    page, _ = _page(qtbot)
    page._on_gpu_restore_error("error", "sysfs gone")
    assert page._gpu_restore_result_label.property("class") == "CriticalChip"
    assert "sysfs gone" in page._gpu_restore_result_label.text()
    assert any(e.level == "error" and e.source == "gpu" for e in page._diag.events)
