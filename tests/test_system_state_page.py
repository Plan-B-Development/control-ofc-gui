"""DEC-211: SystemStatePage — rendering + preserved verify/GPU actions.

Constructs the page directly (like the Overview/Logs page tests) and drives the
handlers, reusing the `HardwareDiagnosticsResult` + verify-result fixtures from
the Diagnostics tests. Worker threads are avoided — the batch state machine is
driven through the public handlers and the journal-style worker paths are
monkeypatched, so nothing depends on thread timing.
"""

from __future__ import annotations

import types

import pytest
from PySide6.QtGui import QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QPushButton,
    QTableWidget,
    QWidget,
)

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
    KernelModuleInfo,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.gauges import RadialGauge
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


# ── Hardware-diagnostics handoff ─────────────────────────────────────────


def test_hw_diag_ok_publishes_board_to_app_state(qtbot):
    """DEC-229 regression (gap #16 blocker 2): the board must reach AppState.

    `AppState.board_info` keys the DMI-matched hwmon label fallback table, and
    from GUI v2.22.0 to v2.29.0 it had **no production writer at all** — commit
    090370e retired `DiagnosticsPage` (which pushed it) and its replacement kept
    only the cache. Nothing failed loudly because the placeholder-label
    short-circuit masked it. This test is the coverage whose absence let that
    through: it fails if a future page drops the AppState half again.
    """
    page, state = _page(qtbot)
    assert state.board_info.vendor == ""  # precondition: nothing published yet

    page._on_hw_diag_ok(_diag_acpi())

    assert state.board_info.vendor == "ASUS"
    assert state.board_info.name == "ProArt X870E"
    # …and the shared cache is still warmed — both halves, one call.
    assert page._diag.last_hw_diagnostics is not None


def test_hw_diag_ok_board_reaches_the_label_resolver(qtbot):
    """The end-to-end point of blocker 2: names change on screen.

    Asserting `board_info` alone would pass against a writer that stored the
    board somewhere the resolver never reads, so this drives the actual
    display-name path with a header whose label is the daemon's `pwm1`
    placeholder.
    """
    from control_ofc.knowledge.hwmon_label_resolver import clear_libsensors_cache

    clear_libsensors_cache()
    try:
        page, state = _page(qtbot)
        state.set_hwmon_headers(
            [
                HwmonHeader(
                    id="hwmon:it8696:it87.2624:pwm1:pwm1",
                    label="pwm1",
                    chip_name="it8696",
                    pwm_index=1,
                )
            ]
        )
        fan_id = "hwmon:it8696:it87.2624:pwm1:pwm1"
        assert state.fan_display_name(fan_id) == "pwm1"  # board unknown → degraded

        page._on_hw_diag_ok(
            _diag(
                board=BoardInfo(vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER")
            )
        )

        assert state.fan_display_name(fan_id) == "CPU_FAN"
    finally:
        clear_libsensors_cache()


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
    assert page.findChild(RadialGauge, "SystemState_Gauge_reverts").fraction() == 1.0
    assert page.findChild(RadialGauge, "SystemState_Gauge_reverts").state() == "crit"
    assert (
        page.findChild(QLabel, "SystemState_Label_contentionTitle").text()
        == "High Contention Detected"
    )
    assert page.findChild(QLabel, "SystemState_Label_headerId").text() == "hwmon:it8696:pwm1"


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
    assert (
        page.findChild(QTableWidget, "SystemState_Table_registry").rowCount() == 1
    )  # one chip, no modules
    holder = page.findChild(QTableWidget, "SystemState_Table_registry").cellWidget(0, 0)
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
    assert page.findChild(StatusPill, "SystemState_Pill_issueCount").text() == "SYSTEM READY"
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
    assert page.findChild(StatusPill, "SystemState_Pill_issueCount").text() != "—"


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
    assert page.findChild(StatusPill, "SystemState_Pill_issueCount").text() != "—"


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
    assert page.findChild(QLabel, "SystemState_Label_gpuModel").text() == "RX 9070 XT"
    assert not page.findChild(
        QWidget, "SystemState_Bar_speedRange"
    ).isHidden()  # firmware speed-range bar shown


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
    assert "no daemon connection" in page.findChild(QLabel, "SystemState_Label_summary").text()


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


def test_on_rescan_ok_reports_an_adopted_openfan_and_drops_the_restart_caveat(qtbot, monkeypatch):
    """DEC-266: the branch this release exists to deliver.

    The success line used to end "New fan-control hardware still requires a
    daemon restart" unconditionally — including right after the daemon had
    adopted an OpenFan controller *without* one, which is the single case
    DEC-265 was built for. Nothing carried a non-empty port through to the page,
    so replacing the whole branch with ``if False:`` left the suite green.
    """
    page, _state = _page(qtbot, client=object())
    monkeypatch.setattr(page, "_fetch_hardware_diagnostics", lambda: None)
    events: list[tuple] = []
    monkeypatch.setattr(
        page._diag,
        "log_event",
        lambda lvl, sub, msg, **kw: events.append((lvl, sub, msg, kw.get("fields"))),
    )

    page._on_rescan_ok([], "/dev/ttyACM1")

    text = page._rescan_result_label.text()
    assert "/dev/ttyACM1" in text, "the adopted port must be named so the user can confirm it"
    assert "adopted" in text
    assert "daemon restart" not in text, (
        "the daemon just adopted a controller without a restart — advising one "
        "here is the exact mis-direction DEC-266 removed"
    )
    assert page._rescan_result_label.property("class") == "SuccessChip"
    assert (
        "info",
        "openfan",
        "OpenFanController adopted on /dev/ttyACM1 via rescan",
        {"component": "/dev/ttyACM1"},
    ) in events, "DEC-314: the adopted port is carried as a field, not only in the sentence"


def test_on_rescan_ok_keeps_the_restart_caveat_when_nothing_was_adopted(qtbot, monkeypatch):
    """The other branch: no adoption means the hwmon caveat still applies, and
    nothing may claim an OpenFan controller appeared."""
    page, _state = _page(qtbot, client=object())
    monkeypatch.setattr(page, "_fetch_hardware_diagnostics", lambda: None)
    events: list[tuple] = []
    monkeypatch.setattr(
        page._diag,
        "log_event",
        lambda lvl, sub, msg, **kw: events.append((lvl, sub, msg, kw.get("fields"))),
    )

    page._on_rescan_ok([], "")

    text = page._rescan_result_label.text()
    assert "daemon restart" in text
    assert "adopted" not in text
    assert not [e for e in events if e[1] == "openfan"], (
        "no OpenFan event may be logged when nothing was adopted"
    )


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


# ── `ACK-q`: the registry table must fit what it renders ──────────────────


@pytest.fixture()
def restore_app_theme(qtbot):
    """Save/restore everything ``apply_theme`` mutates.

    Mirrors the fixture in ``test_theme_typography_r30.py``. The registry-layout
    tests below MUST apply the real theme: without it the app stylesheet is
    empty, so ``.DenseTable::item`` contributes no padding and the base font is
    Qt's default rather than DM Sans at the theme's size — and **both** terms of
    the `ACK-q` defect vanish. Measured: the fix-out-must-fail check passed
    green on an unthemed page, i.e. the first draft of these tests was blind in
    exactly the way `CLAUDE.md` warns about, and only applying the theme made
    them able to fail.
    """
    from PySide6.QtGui import QPalette

    from control_ofc.ui import theme as theme_mod

    app = QApplication.instance()
    saved = (QPalette(app.palette()), app.styleSheet(), app.font(), theme_mod._active_theme)
    try:
        yield app
    finally:
        app.setPalette(saved[0])
        app.setStyleSheet(saved[1])
        app.setFont(saved[2])
        theme_mod._active_theme = saved[3]


def _registry_page(qtbot):
    """A shown, THEMED page carrying a registry row whose Status pill is a word.

    Shown, and measured from the shown widget, because the defect is entirely
    in realised geometry: `sizeHintForColumn` asks the delegate about the ITEM,
    the Status column's content is a *cell widget*, and the two answers differ
    by more than the pill (DEC-314 — the unit test proves you answered, never
    that you were asked).
    """
    from control_ofc.ui.theme import apply_theme, default_dark_theme

    apply_theme(default_dark_theme())
    page, _ = _page(qtbot)
    page._render(
        _diag(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(chip_name="it8696", expected_driver="it87", header_count=5)
                ],
                total_headers=5,
                writable_headers=5,
            ),
            kernel_modules=[
                KernelModuleInfo(name="it87", loaded=True, in_mainline=False),
                KernelModuleInfo(name="k10temp", loaded=True, in_mainline=True),
            ],
        )
    )
    page.resize(1215, 760)
    page.show()
    QApplication.processEvents()
    return page


def test_the_registry_status_pills_are_not_clipped(qtbot, restore_app_theme):
    """`LOADED` and `MODULE` rendered as `LOADE` and `MODU` on the shipped
    release screenshot, and every test stayed green because none of them looked
    at a realised width.

    Asserted as a relationship against each pill's own `sizeHint`, never
    against a pixel count — the required width is a font metric, and the only
    portable form of that assertion is "the widget got at least what it asked
    for" (`CLAUDE.md`, DEC-303/DEC-258).
    """
    page = _registry_page(qtbot)
    table = page.findChild(QTableWidget, "SystemState_Table_registry")
    pills = []
    for row in range(table.rowCount()):
        holder = table.cellWidget(row, 0)
        assert holder is not None
        pills.append(holder.findChild(StatusPill))
    assert pills, "precondition: the table must have rendered at least one status pill"
    assert any(p.sizeHint().width() > table.horizontalHeader().sectionSizeHint(0) for p in pills), (
        "precondition: at least one pill must need more room than the 'Status' header itself, "
        "or the column is wide enough by accident and the test proves nothing"
    )
    for pill in pills:
        assert pill.width() >= pill.sizeHint().width(), (
            f"the {pill.text()!r} pill was clipped to {pill.width()}px "
            f"of the {pill.sizeHint().width()}px it asked for"
        )


def test_the_registry_columns_fit_the_pane_they_are_given(qtbot, restore_app_theme):
    """The `Headers` column was cut off mid-word by 59px of overflow.

    One long `Driver Status` value sized the whole table under
    `ResizeToContents`. Asserted against the realised viewport rather than a
    width, and with a precondition that the long value is actually present —
    without it the row set could shrink and the test would pass by having
    nothing to overflow with.
    """
    page = _registry_page(qtbot)
    table = page.findChild(QTableWidget, "SystemState_Table_registry")
    driver_status = [table.item(r, 3).text() for r in range(table.rowCount())]
    assert max((len(t) for t in driver_status), default=0) > len("Driver Status"), (
        "precondition: a value longer than its own header must be present"
    )
    total = sum(table.columnWidth(c) for c in range(table.columnCount()))
    assert total <= table.viewport().width(), (
        f"the columns need {total}px in a {table.viewport().width()}px viewport, "
        "so the last one is clipped"
    )
    assert table.columnWidth(5) >= table.horizontalHeader().sectionSizeHint(5), (
        "the 'Headers' column is narrower than its own header text"
    )


def test_an_elided_driver_status_stays_readable_in_the_tooltip(qtbot, restore_app_theme):
    """Eliding is only honest if the full text is recoverable."""
    page = _registry_page(qtbot)
    table = page.findChild(QTableWidget, "SystemState_Table_registry")
    row = max(range(table.rowCount()), key=lambda r: len(table.item(r, 3).text()))
    full = table.item(row, 3).text()
    assert full, "precondition: the row must carry a driver status"
    assert full in table.item(row, 3).toolTip()


def test_the_driver_status_tooltip_does_not_grow_on_re_render(qtbot, restore_app_theme):
    """`_ensure_items` reuses items, so a read-then-prepend accumulates.

    The first draft composed this tooltip by reading the item's own previous
    value. The reset loop above it only overwrites when the row has chip
    guidance — and a kernel-module row never does — so every re-render added
    another copy, and this page re-renders on every acknowledgement, dismissal
    and theme change. Raised by `ofc:python-gui-reviewer`; measured at four
    copies after four renders.

    Asserted as a relationship between two renders rather than against a
    literal string, and on the row that has NO chip guidance — the row with
    guidance was stable even with the defect, so sampling it would prove
    nothing (`CLAUDE.md`: pick the sample that can move).
    """
    page = _registry_page(qtbot)
    table = page.findChild(QTableWidget, "SystemState_Table_registry")
    plain = [r for r in range(table.rowCount()) if not table.item(r, 1).toolTip()]
    assert plain, "precondition: a row without chip guidance is what accumulates"
    row = plain[0]
    first = table.item(row, 3).toolTip()
    assert first, "precondition: the row must carry a driver-status tooltip at all"

    for _ in range(3):
        page._render(page._last_rendered_diag)
    assert table.item(row, 3).toolTip() == first, (
        "the tooltip grew across re-renders — it is being composed from itself"
    )
