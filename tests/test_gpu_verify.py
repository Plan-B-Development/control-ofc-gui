"""GPU fan active verification (DEC-120) — GUI side.

Covers the model parser (forward-compat), the client method (path + timeout),
the GUI-authored readiness guidance, the daemon-version gate, the demo path,
and the diagnostics-page wiring (button gating, control-loop pause key, result
rendering). Hardware is never touched — the page tests drive synthetic results.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from control_ofc.api.client import DaemonClient
from control_ofc.api.models import (
    BoardInfo,
    Capabilities,
    GpuDiagnosticsInfo,
    GpuVerifyResult,
    GpuVerifyState,
    HardwareDiagnosticsResult,
    HwmonDiagnostics,
    ThermalSafetyInfo,
    parse_gpu_verify_result,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.demo_service import DemoService
from control_ofc.ui.pages.diagnostics_page import (
    DiagnosticsPage,
    _daemon_version_at_least,
    _GpuVerifyWorker,
)
from control_ofc.ui.widgets.readiness_report import gpu_verify_problems

# ── Parser (forward/backward compatibility) ──────────────────────────


def test_parse_full_payload():
    res = parse_gpu_verify_result(
        {
            "gpu_id": "0000:2d:00.0",
            "result": "effective",
            "initial_state": {"applied_speed_pct": None, "rpm": 0, "zero_rpm_enabled": True},
            "final_state": {"applied_speed_pct": 75, "rpm": 1600, "zero_rpm_enabled": False},
            "test_speed_pct": 75,
            "wait_seconds": 6,
            "fan_control_method": "pmfw_curve",
            "details": "ok",
            "restore_failed": True,
        }
    )
    assert res.gpu_id == "0000:2d:00.0"
    assert res.result == "effective"
    assert res.final_state.applied_speed_pct == 75
    assert res.final_state.rpm == 1600
    assert res.initial_state.zero_rpm_enabled is True
    assert res.test_speed_pct == 75
    assert res.fan_control_method == "pmfw_curve"
    assert res.restore_failed is True


def test_parse_drops_unknown_fields():
    """A newer daemon may add fields the GUI dataclass does not know — they must
    be dropped, not raise (forward compatibility, _filter_fields)."""
    res = parse_gpu_verify_result(
        {
            "result": "no_rpm_effect",
            "initial_state": {"rpm": 10, "future_field": "x"},
            "final_state": {"applied_speed_pct": 75, "brand_new": 1},
            "unknown_top_level": "y",
        }
    )
    assert res.result == "no_rpm_effect"
    assert res.initial_state.rpm == 10
    assert res.final_state.applied_speed_pct == 75


def test_parse_missing_fields_default():
    """An older daemon may omit fields entirely — defaults apply, no crash."""
    res = parse_gpu_verify_result({"result": "rpm_unavailable"})
    assert res.result == "rpm_unavailable"
    assert res.gpu_id == ""
    assert res.test_speed_pct == 0
    assert res.restore_failed is False
    assert isinstance(res.initial_state, GpuVerifyState)
    assert res.initial_state.rpm is None


def test_parse_null_states_default():
    """Explicit null states must not blow up the nested parser."""
    res = parse_gpu_verify_result(
        {"result": "effective", "initial_state": None, "final_state": None}
    )
    assert res.initial_state.applied_speed_pct is None
    assert res.final_state.rpm is None


# ── Client method ────────────────────────────────────────────────────


def test_verify_gpu_fan_posts_to_correct_path_with_timeout():
    client = DaemonClient(socket_path="/tmp/does-not-exist-gpu-verify.sock")
    client._post = MagicMock(
        return_value={"gpu_id": "0000:2d:00.0", "result": "effective", "test_speed_pct": 75}
    )
    try:
        res = client.verify_gpu_fan("0000:2d:00.0")
    finally:
        client.close()

    client._post.assert_called_once()
    args, kwargs = client._post.call_args
    assert args[0] == "/gpu/0000:2d:00.0/fan/verify"
    # Daemon sleeps 6s; the per-call timeout must clear that plus round-trip.
    assert kwargs["timeout"] >= 12.0
    assert res.result == "effective"


# ── Daemon-version gate ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "version,expected",
    [
        ("1.11.0", True),
        ("1.11", True),
        ("1.11.0-rc1", True),
        ("1.11.0+git", True),
        ("1.11.0-demo", True),
        ("1.12.3", True),
        ("2.0.0", True),
        ("1.10.0", False),
        ("1.9.9", False),
        ("0.1.0-demo", False),
        ("", False),
        ("garbage", False),
    ],
)
def test_daemon_version_gate(version, expected):
    assert _daemon_version_at_least(version, (1, 11, 0)) is expected


# ── Readiness guidance (GUI-authored) ────────────────────────────────


@pytest.mark.parametrize("verdict", ["effective", "zero_rpm_suppressed", "rpm_unavailable", ""])
def test_gpu_verify_problems_empty_for_ok(verdict):
    assert gpu_verify_problems(GpuVerifyResult(result=verdict)) == []


@pytest.mark.parametrize(
    "verdict,key,must_contain",
    [
        ("curve_not_applied", "gpu_verify_curve_not_applied", "ppfeaturemask"),
        ("no_rpm_effect", "gpu_verify_no_rpm_effect", "kernel"),
        ("pwm_enable_reverted", "gpu_verify_pwm_reverted", "Smart Fan"),
        ("write_failed", "gpu_verify_write_failed", "ppfeaturemask"),
    ],
)
def test_gpu_verify_problems_for_failures(verdict, key, must_contain):
    probs = gpu_verify_problems(GpuVerifyResult(result=verdict))
    assert len(probs) == 1
    prob = probs[0]
    assert prob["key"] == key
    assert prob["severity"] == "critical"
    assert must_contain in prob["fix"]
    assert prob["doc_url"].startswith("https://")


# ── Demo path ────────────────────────────────────────────────────────


def test_demo_verify_gpu_fan_is_deterministic_effective():
    res = DemoService().verify_gpu_fan("0000:2d:00.0")
    assert res.result == "effective"
    assert res.gpu_id == "0000:2d:00.0"
    assert res.final_state.applied_speed_pct == 75
    assert res.final_state.rpm and res.final_state.rpm > 0


# ── Diagnostics page wiring ──────────────────────────────────────────


def _state(daemon_version: str = "1.11.0") -> AppState:
    state = AppState()
    state.set_capabilities(Capabilities(daemon_version=daemon_version))
    return state


def _make_page(qtbot, state=None, client=None) -> DiagnosticsPage:
    page = DiagnosticsPage(state=state or _state(), client=client)
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


class TestGpuVerifyButtonGating:
    def test_button_exists_and_hidden_before_populate(self, qtbot):
        page = _make_page(qtbot)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_verifyGpu")
        assert btn is not None
        assert btn.isHidden()

    def test_button_shown_for_writable_gpu_and_new_daemon(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag(gpu=_writable_gpu()))
        assert not page._gpu_verify_btn.isHidden()
        assert page._gpu_verify_bdf == "0000:2d:00.0"

    def test_button_hidden_for_read_only_gpu(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(
            _diag(gpu=GpuDiagnosticsInfo(pci_bdf="0000:2d:00.0", fan_control_method="read_only"))
        )
        assert page._gpu_verify_btn.isHidden()
        assert page._gpu_verify_bdf is None

    def test_button_hidden_when_no_gpu(self, qtbot):
        page = _make_page(qtbot)
        page._populate_hw_diagnostics(_diag(gpu=None))
        assert page._gpu_verify_btn.isHidden()

    def test_button_hidden_for_old_daemon(self, qtbot):
        page = _make_page(qtbot, state=_state(daemon_version="1.10.0"))
        page._populate_hw_diagnostics(_diag(gpu=_writable_gpu()))
        assert page._gpu_verify_btn.isHidden()


class TestGpuVerifyResultRendering:
    @pytest.mark.parametrize(
        "verdict,css,needle",
        [
            ("effective", "SuccessChip", "working"),
            ("zero_rpm_suppressed", "SuccessChip", "zero-rpm"),
            ("rpm_unavailable", "WarningChip", "no"),
            ("curve_not_applied", "CriticalChip", "ignored"),
            ("no_rpm_effect", "CriticalChip", "did not respond"),
            ("pwm_enable_reverted", "CriticalChip", "reclaimed"),
            ("write_failed", "CriticalChip", "rejected"),
        ],
    )
    def test_show_result_sets_class_and_text(self, qtbot, verdict, css, needle):
        page = _make_page(qtbot)
        page._show_gpu_verify_result(
            GpuVerifyResult(
                gpu_id="0000:2d:00.0",
                result=verdict,
                initial_state=GpuVerifyState(rpm=0),
                final_state=GpuVerifyState(applied_speed_pct=75, rpm=1600),
                test_speed_pct=75,
                wait_seconds=6,
            )
        )
        label = page.findChild(QLabel, "Diagnostics_Label_verifyGpuResult")
        assert not label.isHidden()
        assert needle in label.text().lower()
        assert label.property("class") == css

    def test_failure_result_includes_fix_guidance(self, qtbot):
        page = _make_page(qtbot)
        page._show_gpu_verify_result(GpuVerifyResult(result="curve_not_applied"))
        label = page.findChild(QLabel, "Diagnostics_Label_verifyGpuResult")
        assert "ppfeaturemask" in label.text()

    def test_restore_failed_is_surfaced(self, qtbot):
        page = _make_page(qtbot)
        page._show_gpu_verify_result(GpuVerifyResult(result="effective", restore_failed=True))
        label = page.findChild(QLabel, "Diagnostics_Label_verifyGpuResult")
        assert "restore" in label.text().lower()


class _SyncClient:
    """Fake client exposing verify_gpu_fan but NO socket_path, so _run_gpu_verify
    takes the synchronous (demo/test) path instead of spinning a worker thread."""

    def __init__(self, result: GpuVerifyResult) -> None:
        self._result = result
        self.calls: list[str] = []

    def verify_gpu_fan(self, gpu_id: str) -> GpuVerifyResult:
        self.calls.append(gpu_id)
        return self._result


class TestGpuVerifyRun:
    def test_run_emits_pause_key_and_renders(self, qtbot):
        client = _SyncClient(GpuVerifyResult(gpu_id="0000:2d:00.0", result="effective"))
        page = _make_page(qtbot, client=client)
        page._gpu_verify_bdf = "0000:2d:00.0"

        started: list[str] = []
        completed: list[str] = []
        page.verify_started.connect(started.append)
        page.verify_completed.connect(completed.append)

        page._run_gpu_verify()

        # The control-loop pause/resume key must be the GPU dispatch key.
        assert started == ["amd_gpu:0000:2d:00.0"]
        assert completed == ["amd_gpu:0000:2d:00.0"]
        assert client.calls == ["0000:2d:00.0"]
        assert page._gpu_verify_active_key is None
        label = page.findChild(QLabel, "Diagnostics_Label_verifyGpuResult")
        assert "working" in label.text().lower()

    def test_run_without_bdf_shows_message(self, qtbot):
        page = _make_page(qtbot, client=_SyncClient(GpuVerifyResult(result="effective")))
        page._gpu_verify_bdf = None
        page._run_gpu_verify()
        label = page.findChild(QLabel, "Diagnostics_Label_verifyGpuResult")
        assert not label.isHidden()
        assert "no gpu" in label.text().lower()


class TestGpuVerifyWorkerLifecycle:
    def test_worker_not_created_without_socket(self, qtbot):
        page = _make_page(qtbot, client=None)
        assert page._ensure_gpu_verify_worker() is False
        assert page._gpu_verify_worker is None

    def test_worker_created_with_socket(self, qtbot):
        client = MagicMock()
        client.socket_path = "/tmp/fake-gpu.sock"
        page = _make_page(qtbot, client=client)
        try:
            assert page._ensure_gpu_verify_worker() is True
            assert page._gpu_verify_thread is not None
            assert page._gpu_verify_thread.isRunning()
            # Idempotent.
            prev = page._gpu_verify_thread
            assert page._ensure_gpu_verify_worker() is True
            assert page._gpu_verify_thread is prev
        finally:
            page.cleanup()

    def test_unsupported_error_hides_button(self, qtbot):
        page = _make_page(qtbot)
        page._gpu_verify_btn.setVisible(True)
        page._on_gpu_verify_error("unsupported", "old daemon")
        assert page._gpu_verify_btn.isHidden()
        assert page._gpu_verify_unsupported is True

    def test_worker_is_qobject_with_signals(self):
        # Constructable off-thread; signals exist for the queued wiring.
        worker = _GpuVerifyWorker("/tmp/x.sock")
        assert hasattr(worker, "verify_ok")
        assert hasattr(worker, "verify_error")
