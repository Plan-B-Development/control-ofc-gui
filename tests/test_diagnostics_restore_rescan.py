"""GPU restore-to-automatic + hwmon rescan buttons (DEC-147) — GUI side.

Covers the restored ``hwmon_rescan`` client wrapper (path + parsing), the
control loop's ``manages_gpu_target()`` gate query, the diagnostics-page
wiring for both buttons (visibility, D2 gating, click paths, result
rendering, state side-effects), the demo stubs, and the failure paths.
Hardware is never touched — page tests drive synthetic results through fake
clients injected into the real worker thread (``_drive_via_worker``), so the
daemon-owns-writes worker path is exercised without a real socket.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from control_ofc.api.client import DaemonClient
from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.api.models import (
    BoardInfo,
    Capabilities,
    ConnectionState,
    GpuDiagnosticsInfo,
    GpuFanResetResult,
    HardwareDiagnosticsResult,
    HwmonDiagnostics,
    HwmonHeader,
    OperationMode,
    ThermalSafetyInfo,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_service import DemoService
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    LogicalControl,
    Profile,
    ProfileService,
)
from control_ofc.ui.pages.diagnostics_page import (
    _GPU_RESTORE_TOOLTIP_GATED,
    _GPU_RESTORE_TOOLTIP_READY,
    DiagnosticsPage,
    _GpuVerifyWorker,
    _HwDiagWorker,
)

# ── Client wrapper ───────────────────────────────────────────────────


def test_hwmon_rescan_posts_and_parses_headers():
    client = DaemonClient.__new__(DaemonClient)
    client._post = MagicMock(
        return_value={
            "api_version": 1,
            "headers": [
                {"id": "hwmon:nct6775:pwm1", "label": "CPU_FAN", "is_writable": True},
                {"id": "hwmon:nct6775:pwm2", "label": "SYS_FAN1", "is_writable": False},
            ],
            "count": 2,
        }
    )

    headers = client.hwmon_rescan()

    client._post.assert_called_once_with("/hwmon/rescan")
    assert [h.id for h in headers] == ["hwmon:nct6775:pwm1", "hwmon:nct6775:pwm2"]
    assert headers[0].is_writable is True
    assert headers[1].is_writable is False


def test_hwmon_rescan_empty_headers():
    client = DaemonClient.__new__(DaemonClient)
    client._post = MagicMock(return_value={"api_version": 1, "headers": [], "count": 0})
    assert client.hwmon_rescan() == []


# ── Control loop gate query ──────────────────────────────────────────


@pytest.fixture()
def state(qtbot):
    s = AppState()
    s.connection = ConnectionState.CONNECTED
    s.mode = OperationMode.AUTOMATIC
    return s


@pytest.fixture()
def profile_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = ProfileService()
    svc.load()
    return svc


def _profile_with_member(target_id: str, source: str) -> Profile:
    control = LogicalControl(
        id="ctl",
        name="Ctl",
        mode=ControlMode.MANUAL,
        manual_output_pct=50.0,
        members=[ControlMember(source=source, member_id=target_id)],
    )
    return Profile(id="test", name="Test", controls=[control])


# ── Page fixtures (mirroring test_gpu_verify) ────────────────────────


def _page_state(daemon_version: str = "1.11.0") -> AppState:
    s = AppState()
    s.set_capabilities(Capabilities(daemon_version=daemon_version))
    return s


def _make_page(qtbot, state=None, client=None, profile_service=None):
    page = DiagnosticsPage(
        state=state or _page_state(),
        client=client,
        profile_service=profile_service,
    )
    qtbot.addWidget(page)
    return page


def _diag(*, gpu: GpuDiagnosticsInfo | None) -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(total_headers=0, writable_headers=0),
        gpu=gpu,
        board=BoardInfo(vendor="", name="Generic"),
        thermal_safety=ThermalSafetyInfo(
            state="normal",
            cpu_sensor_found=True,
            emergency_threshold_c=105.0,
            release_threshold_c=80.0,
        ),
    )


def _writable_gpu() -> GpuDiagnosticsInfo:
    return GpuDiagnosticsInfo(
        pci_bdf="0000:2d:00.0",
        model_name="Radeon RX 7900 XTX",
        fan_control_method="pmfw_curve",
    )


def _gpu_controlling_ps() -> ProfileService:
    """A profile service whose active profile owns an ``amd_gpu:`` member."""
    ps = ProfileService()
    prof = _profile_with_member("amd_gpu:0000:2d:00.0", "amd_gpu")
    ps._profiles[prof.id] = prof
    ps.set_active(prof.id)
    return ps


def _non_gpu_ps() -> ProfileService:
    """A profile service whose active profile owns only a non-GPU member."""
    ps = ProfileService()
    prof = _profile_with_member("openfan:ch00", "openfan")
    ps._profiles[prof.id] = prof
    ps.set_active(prof.id)
    return ps


def _drive_via_worker(qtbot, page, ensure, worker_attr, fake, trigger, done, timeout=3000):
    """Run a diagnostics action through its real worker thread.

    Creates the worker via *ensure*, injects *fake* as the worker's client (so
    it is used instead of a real ``DaemonClient``), fires *trigger*, then spins
    the event loop until *done* holds — i.e. until the page has rendered the
    worker's result. The caller is responsible for ``page.cleanup()``.
    """
    assert ensure()
    getattr(page, worker_attr)._client = fake
    trigger()
    qtbot.waitUntil(done, timeout=timeout)


# ── GPU restore: visibility ──────────────────────────────────────────


class TestGpuRestoreVisibility:
    def test_button_exists_and_hidden_before_populate(self, qtbot):
        page = _make_page(qtbot)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_restoreGpu")
        assert btn is not None
        assert btn.isHidden()

    def test_shown_for_writable_gpu(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag(gpu=_writable_gpu()))
        assert not page._gpu_restore_btn.isHidden()

    def test_shown_even_for_old_daemon(self, qtbot):
        """D7: the reset route predates every supported daemon — unlike the
        verify button there is no 1.11.0 version floor."""
        page = _make_page(qtbot, state=_page_state(daemon_version="1.10.0"))
        page._populate_hw_diagnostics(_diag(gpu=_writable_gpu()))
        assert page._gpu_verify_btn.isHidden()  # verify keeps its floor
        assert not page._gpu_restore_btn.isHidden()

    def test_hidden_for_read_only_gpu(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(
            _diag(gpu=GpuDiagnosticsInfo(pci_bdf="0000:2d:00.0", fan_control_method="read_only"))
        )
        assert page._gpu_restore_btn.isHidden()

    def test_hidden_when_no_gpu(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag(gpu=None))
        assert page._gpu_restore_btn.isHidden()


# ── GPU restore: D2 gate ─────────────────────────────────────────────


class TestGpuRestoreGate:
    """The page gate is loop-independent now (DEC-165): disabled while the
    active profile owns the GPU, since the daemon would re-apply the curve."""

    def test_disabled_while_active_profile_controls_gpu(self, qtbot):
        page = _make_page(qtbot, profile_service=_gpu_controlling_ps())
        page._populate_hw_diagnostics(_diag(gpu=_writable_gpu()))
        assert not page._gpu_restore_btn.isEnabled()
        assert page._gpu_restore_btn.toolTip() == _GPU_RESTORE_TOOLTIP_GATED

    def test_enabled_when_profile_has_no_gpu_member(self, qtbot):
        page = _make_page(qtbot, profile_service=_non_gpu_ps())
        page._populate_hw_diagnostics(_diag(gpu=_writable_gpu()))
        assert page._gpu_restore_btn.isEnabled()
        assert page._gpu_restore_btn.toolTip() == _GPU_RESTORE_TOOLTIP_READY

    def test_enabled_without_profile_service(self, qtbot):
        """Pages built outside main_window (unit tests) may have no profile
        service — the gate must not block the button."""
        page = _make_page(qtbot, profile_service=None)
        page._populate_hw_diagnostics(_diag(gpu=_writable_gpu()))
        assert page._gpu_restore_btn.isEnabled()

    def test_gate_flips_on_active_profile_changed(self, qtbot):
        ps = _non_gpu_ps()
        state = _page_state()
        page = _make_page(qtbot, state=state, profile_service=ps)
        page._populate_hw_diagnostics(_diag(gpu=_writable_gpu()))
        assert page._gpu_restore_btn.isEnabled()

        # Switch the active profile to one that owns the GPU, then fire the
        # signal the gate listens on (active_profile_changed).
        gpu_prof = _profile_with_member("amd_gpu:0000:2d:00.0", "amd_gpu")
        ps._profiles[gpu_prof.id] = gpu_prof
        ps.set_active(gpu_prof.id)
        state.set_active_profile("gaming")

        assert not page._gpu_restore_btn.isEnabled()
        assert page._gpu_restore_btn.toolTip() == _GPU_RESTORE_TOOLTIP_GATED

    def test_click_recheck_refuses_when_managed(self, qtbot):
        """A stale-enabled button must not slip a restore through: the click
        handler re-checks the gate and refuses without calling the client."""
        client = _RestoreFakeClient(GpuFanResetResult(gpu_id="0000:2d:00.0", reset=True))
        page = _make_page(qtbot, client=client, profile_service=_gpu_controlling_ps())
        page._gpu_verify_bdf = "0000:2d:00.0"

        page._run_gpu_restore()

        assert client.calls == []
        label = page.findChild(QLabel, "Diagnostics_Label_restoreGpuResult")
        assert not label.isHidden()
        assert "not restored" in label.text().lower()
        assert not page._gpu_restore_btn.isEnabled()


# ── GPU restore: run paths ───────────────────────────────────────────


class _RestoreFakeClient:
    """Fake client exposing reset_gpu_fan with a truthy ``socket_path`` so the
    page spins its GPU worker; the test injects this instance into the worker
    (see ``_drive_via_worker``) so the canned result/error flows without a real
    DaemonClient / socket."""

    socket_path = "/tmp/control-ofc-gpu-restore-test.sock"

    def __init__(self, result: GpuFanResetResult | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls: list[str] = []

    def reset_gpu_fan(self, gpu_id: str) -> GpuFanResetResult:
        self.calls.append(gpu_id)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class TestGpuRestoreRun:
    def test_success_renders_and_logs(self, qtbot):
        client = _RestoreFakeClient(GpuFanResetResult(gpu_id="0000:2d:00.0", reset=True))
        state = _page_state()
        page = _make_page(qtbot, state=state, client=client)
        page._gpu_verify_bdf = "0000:2d:00.0"
        label = page.findChild(QLabel, "Diagnostics_Label_restoreGpuResult")

        try:
            _drive_via_worker(
                qtbot,
                page,
                page._ensure_gpu_verify_worker,
                "_gpu_verify_worker",
                client,
                page._run_gpu_restore,
                lambda: "restored to automatic" in label.text().lower(),
            )

            assert client.calls == ["0000:2d:00.0"]
            assert not label.isHidden()
            assert label.property("class") == "SuccessChip"
            # The action lands in the event log.
            assert any(
                e.source == "gpu" and "restored" in e.message.lower() for e in page._diag.events
            )
            assert page._gpu_restore_btn.isEnabled()
            assert page._gpu_restore_btn.text() == "Restore GPU Fan to Automatic"
        finally:
            page.cleanup()

    def test_daemon_noop_shows_warning(self, qtbot):
        client = _RestoreFakeClient(GpuFanResetResult(gpu_id="0000:2d:00.0", reset=False))
        state = _page_state()
        page = _make_page(qtbot, state=state, client=client)
        page._gpu_verify_bdf = "0000:2d:00.0"
        label = page.findChild(QLabel, "Diagnostics_Label_restoreGpuResult")

        try:
            _drive_via_worker(
                qtbot,
                page,
                page._ensure_gpu_verify_worker,
                "_gpu_verify_worker",
                client,
                page._run_gpu_restore,
                lambda: label.property("class") == "WarningChip",
            )
        finally:
            page.cleanup()

    def test_daemon_error_shows_critical(self, qtbot):
        client = _RestoreFakeClient(
            error=DaemonError(code="hardware_unavailable", message="sysfs gone", status=503)
        )
        state = _page_state()
        page = _make_page(qtbot, state=state, client=client)
        page._gpu_verify_bdf = "0000:2d:00.0"
        label = page.findChild(QLabel, "Diagnostics_Label_restoreGpuResult")

        try:
            _drive_via_worker(
                qtbot,
                page,
                page._ensure_gpu_verify_worker,
                "_gpu_verify_worker",
                client,
                page._run_gpu_restore,
                lambda: "sysfs gone" in label.text(),
            )

            assert label.property("class") == "CriticalChip"
            assert page._gpu_restore_btn.isEnabled()  # never stuck disabled
            assert any(e.level == "error" and e.source == "gpu" for e in page._diag.events)
        finally:
            page.cleanup()

    def test_without_bdf_shows_message(self, qtbot):
        # No BDF → the method returns before any worker is created.
        page = _make_page(qtbot, client=_RestoreFakeClient())
        page._gpu_verify_bdf = None
        page._run_gpu_restore()
        label = page.findChild(QLabel, "Diagnostics_Label_restoreGpuResult")
        assert not label.isHidden()
        assert "no gpu" in label.text().lower()
        assert page._gpu_verify_worker is None

    def test_without_client_shows_message(self, qtbot):
        page = _make_page(qtbot, client=None)
        page._gpu_verify_bdf = "0000:2d:00.0"
        page._run_gpu_restore()
        label = page.findChild(QLabel, "Diagnostics_Label_restoreGpuResult")
        assert "no daemon connection" in label.text().lower()


# ── Hwmon rescan: run paths ──────────────────────────────────────────


class _RescanFakeClient:
    """Fake client exposing hwmon_rescan + hardware_diagnostics with a truthy
    ``socket_path`` so the page spins its hardware-diagnostics worker; injected
    into that worker by the test so the rescan and its chained diagnostics
    refetch both flow through the real worker thread without a socket."""

    socket_path = "/tmp/control-ofc-rescan-test.sock"

    def __init__(self, headers: list[HwmonHeader] | None = None, error: Exception | None = None):
        self._headers = headers if headers is not None else []
        self._error = error
        self.rescan_calls = 0
        self.diag_calls = 0

    def hwmon_rescan(self) -> list[HwmonHeader]:
        self.rescan_calls += 1
        if self._error is not None:
            raise self._error
        return self._headers

    def hardware_diagnostics(self) -> HardwareDiagnosticsResult:
        self.diag_calls += 1
        return _diag(gpu=None)


class TestHwmonRescanRun:
    def test_success_pushes_headers_and_chains_refetch(self, qtbot):
        fresh = [
            HwmonHeader(id="hwmon:nct6775:pwm1", label="CPU_FAN", is_writable=True),
            HwmonHeader(id="hwmon:nct6775:pwm2", label="SYS_FAN1", is_writable=True),
        ]
        client = _RescanFakeClient(headers=fresh)
        state = _page_state()
        page = _make_page(qtbot, state=state, client=client)

        emitted: list[list] = []
        state.headers_updated.connect(emitted.append)
        label = page.findChild(QLabel, "Diagnostics_Label_rescanResult")

        try:
            # rescan → rescan_ok → chained diagnostics refetch → fetch_ok, all on
            # the one _HwDiagWorker; wait for the chained refetch to land.
            _drive_via_worker(
                qtbot,
                page,
                page._ensure_hw_diag_worker,
                "_hw_diag_worker",
                client,
                page._run_hwmon_rescan,
                lambda: client.diag_calls == 1,
            )

            assert client.rescan_calls == 1
            # Headers flow through AppState so every consumer sees them.
            assert state.hwmon_headers == fresh
            assert emitted == [fresh]
            assert not label.isHidden()
            assert "2 PWM header(s)" in label.text()
            assert "daemon restart" in label.text()  # honest control-hardware caveat
            assert label.property("class") == "SuccessChip"
            assert page._rescan_btn.isEnabled()
            assert page._rescan_btn.text() == "Rescan Hardware"
            assert any(
                e.source == "hwmon" and "rescan" in e.message.lower() for e in page._diag.events
            )
        finally:
            page.cleanup()

    def test_failure_keeps_existing_headers(self, qtbot):
        old = [HwmonHeader(id="hwmon:it8696:pwm1", label="CHA_FAN1", is_writable=True)]
        client = _RescanFakeClient(
            error=DaemonError(code="internal_error", message="scan failed", status=500)
        )
        state = _page_state()
        state.set_hwmon_headers(old)
        page = _make_page(qtbot, state=state, client=client)
        label = page.findChild(QLabel, "Diagnostics_Label_rescanResult")

        try:
            _drive_via_worker(
                qtbot,
                page,
                page._ensure_hw_diag_worker,
                "_hw_diag_worker",
                client,
                page._run_hwmon_rescan,
                lambda: "scan failed" in label.text(),
            )

            assert state.hwmon_headers == old  # never clobbered on failure
            assert label.property("class") == "CriticalChip"
            assert client.diag_calls == 0  # no refetch chained on failure
            assert page._rescan_btn.isEnabled()
            assert any(e.level == "error" and e.source == "hwmon" for e in page._diag.events)
        finally:
            page.cleanup()

    def test_unavailable_shows_message(self, qtbot):
        client = _RescanFakeClient(error=DaemonUnavailable())
        page = _make_page(qtbot, client=client)
        label = page.findChild(QLabel, "Diagnostics_Label_rescanResult")

        try:
            _drive_via_worker(
                qtbot,
                page,
                page._ensure_hw_diag_worker,
                "_hw_diag_worker",
                client,
                page._run_hwmon_rescan,
                lambda: "daemon unavailable" in label.text().lower(),
            )
            assert page._rescan_btn.isEnabled()
        finally:
            page.cleanup()

    def test_without_client_shows_message(self, qtbot):
        page = _make_page(qtbot, client=None)
        page._run_hwmon_rescan()
        label = page.findChild(QLabel, "Diagnostics_Label_rescanResult")
        assert "no daemon connection" in label.text().lower()


# ── Worker signal surface ────────────────────────────────────────────


def test_gpu_worker_has_reset_slot_and_signals():
    worker = _GpuVerifyWorker("/tmp/x.sock")
    assert hasattr(worker, "reset_ok")
    assert hasattr(worker, "reset_error")
    assert callable(worker.do_reset)


def test_hw_diag_worker_has_rescan_slot_and_signals():
    worker = _HwDiagWorker("/tmp/x.sock")
    assert hasattr(worker, "rescan_ok")
    assert hasattr(worker, "rescan_error")
    assert callable(worker.do_rescan)


# ── Demo stubs ───────────────────────────────────────────────────────


def test_demo_reset_gpu_fan_reports_success():
    res = DemoService().reset_gpu_fan("0000:2d:00.0")
    assert res.gpu_id == "0000:2d:00.0"
    assert res.reset is True


def test_demo_hwmon_rescan_returns_demo_headers():
    demo = DemoService()
    assert [h.id for h in demo.hwmon_rescan()] == [h.id for h in demo.hwmon_headers()]
