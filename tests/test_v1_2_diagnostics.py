"""Tests for v1.2.0 diagnostics: board info, vendor quirks, revert counts,
PWM verify, and support bundle enhancements.

The Diagnostics-page widget tests (board-info / vendor-advisory / revert-count /
verify-UI rendering) were retired when ``/diagnostics/hardware`` rendering moved
to the live System State page — that rendering is now covered by
``tests/test_system_state_view.py`` + ``tests/test_system_state_page.py``. The
pure daemon-model / quirk-DB / dual-chip-helper / support-bundle tests stay here,
and the three behaviours with no live equivalent — the PWM-verify worker-thread
lifecycle, the ``pwm_value_clamped`` result, and the DEC-144 dual-chip
remediation ordering — are re-vehicled onto the live ``SystemStatePage`` /
pure ``dual_chip_warning_html`` below.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from control_ofc.api.models import (
    BoardInfo,
    ConnectionState,
    HardwareDiagnosticsResult,
    HwmonDiagnostics,
    HwmonVerifyResult,
    HwmonVerifyState,
    KernelModuleInfo,
    OperationMode,
    ThermalSafetyInfo,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.hwmon_guidance import lookup_vendor_quirks
from control_ofc.ui.pages.system_state_page import SystemStatePage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _make_diag_result(
    *,
    board_vendor: str = "",
    board_name: str = "",
    bios_version: str = "",
    revert_counts: dict[str, int] | None = None,
) -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[],
            total_headers=3,
            writable_headers=3,
            enable_revert_counts=revert_counts or {},
        ),
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        kernel_modules=[KernelModuleInfo(name="it87", loaded=True, in_mainline=False)],
        board=BoardInfo(vendor=board_vendor, name=board_name, bios_version=bios_version),
    )


def _ss_page(qtbot, state=None, client=None):
    """Construct the live SystemStatePage (which now owns the PWM-verify worker
    the retired Diagnostics page used to hold)."""
    s = state or _make_state()
    page = SystemStatePage(state=s, diagnostics_service=DiagnosticsService(s), client=client)
    qtbot.addWidget(page)
    return page, s


# ---------------------------------------------------------------------------
# Vendor quirk lookup tests
# ---------------------------------------------------------------------------


class TestVendorQuirkLookup:
    def test_gigabyte_it8689_returns_critical(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8689")
        assert len(quirks) == 1
        assert quirks[0].severity == "critical"
        assert "SmartFan" in quirks[0].summary

    def test_gigabyte_it8696_returns_high(self):
        # DEC-106 added an IT8883/STEALTH-ICE medium entry that also
        # matches it8696. The original IT8696E SmartFan-6 HIGH entry
        # must still be present.
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8696")
        assert any(q.severity == "high" for q in quirks)

    def test_gigabyte_it8688_returns_high(self):
        # DEC-106 added an IT8688E AM4 500-series INFO entry that also
        # matches. The original SmartFan-5 HIGH entry must still be present.
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8688")
        assert any(q.severity == "high" for q in quirks)

    def test_msi_nct6687_returns_medium_and_high(self):
        # Original DB had exactly one medium + one high quirk. DEC-106
        # added an INFO auto-allowlist entry and a MEDIUM AM4 500-series
        # entry. The contract is "both severities are present", not the
        # exact total count.
        quirks = lookup_vendor_quirks("Micro-Star International Co., Ltd.", "nct6687")
        severities = {q.severity for q in quirks}
        assert "medium" in severities
        assert "high" in severities

    def test_asus_nct679x_returns_medium(self):
        # DEC-106 added an ASUS+NCT6798D INFO entry that also matches.
        # The original ACPI-conflict MEDIUM entry must still be present.
        quirks = lookup_vendor_quirks("ASUSTeK COMPUTER INC.", "nct6798")
        acpi_medium = [q for q in quirks if q.severity == "medium" and "ACPI" in q.summary]
        assert acpi_medium, (
            f"Expected ASUS+NCT6798 MEDIUM ACPI quirk to still be present; "
            f"got: {[(q.severity, q.summary) for q in quirks]}"
        )

    def test_no_quirk_for_unknown_vendor(self):
        quirks = lookup_vendor_quirks("Unknown Vendor", "it8696")
        assert quirks == []

    def test_no_quirk_for_unmatched_chip(self):
        # No Gigabyte+nct6798 quirk has ever been seeded (Gigabyte boards
        # are ITE-based, not Nuvoton). DEC-106 did not change this — the
        # AM5 800-series ASRock NCT6798D quirks are vendor-keyed to
        # ASRock / ASUS, not Gigabyte.
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "nct6798")
        assert quirks == []

    def test_empty_inputs_return_empty(self):
        assert lookup_vendor_quirks("", "it8696") == []
        assert lookup_vendor_quirks("Gigabyte", "") == []

    def test_quirk_has_details(self):
        quirks = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8696")
        assert len(quirks[0].details) > 0

    def test_case_insensitive_vendor(self):
        # The contract is "case-insensitive vendor matching works": an
        # all-uppercase vendor must return the same set of quirks as a
        # mixed-case vendor. Asserting set equality on summaries is
        # strictly stronger than the original exact-count assertion AND
        # survives future quirk additions, because both sides of the
        # comparison see the same DB.
        upper = lookup_vendor_quirks("GIGABYTE TECHNOLOGY CO., LTD.", "it8696")
        canonical = lookup_vendor_quirks("Gigabyte Technology Co., Ltd.", "it8696")
        assert {q.summary for q in upper} == {q.summary for q in canonical}, (
            "Case-insensitive vendor lookup must return the SAME quirks "
            "as the canonical-cased lookup"
        )
        assert canonical, "Sanity: canonical lookup must itself return matches"


# ---------------------------------------------------------------------------
# Board info parsing tests
# ---------------------------------------------------------------------------


class TestBoardInfoParsing:
    def test_parse_board_info(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
            "board": {
                "vendor": "Gigabyte Technology Co., Ltd.",
                "name": "X870E AORUS MASTER",
                "bios_version": "F13a",
            },
        }
        result = parse_hardware_diagnostics(data)
        assert result.board.vendor == "Gigabyte Technology Co., Ltd."
        assert result.board.name == "X870E AORUS MASTER"
        assert result.board.bios_version == "F13a"

    def test_parse_board_info_missing(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.board.vendor == ""
        assert result.board.name == ""

    def test_parse_revert_counts(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {
                "chips_detected": [],
                "total_headers": 1,
                "writable_headers": 1,
                "enable_revert_counts": {"it8696-isa-0a30/pwm1": 5},
            },
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.hwmon.enable_revert_counts == {"it8696-isa-0a30/pwm1": 5}

    def test_parse_revert_counts_empty(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.hwmon.enable_revert_counts == {}


# ---------------------------------------------------------------------------
# Verify result parsing tests
# ---------------------------------------------------------------------------


class TestVerifyResultParsing:
    def test_parse_effective(self):
        from control_ofc.api.models import parse_hwmon_verify_result

        data = {
            "header_id": "it8696-isa-0a30/pwm1",
            "result": "effective",
            "initial_state": {"pwm_enable": 1, "pwm_raw": 128, "pwm_percent": 50, "rpm": 1200},
            "final_state": {"pwm_enable": 1, "pwm_raw": 178, "pwm_percent": 70, "rpm": 900},
            "test_pwm_percent": 70,
            "wait_seconds": 6,
            "details": "PWM accepted and RPM changed",
        }
        result = parse_hwmon_verify_result(data)
        assert result.result == "effective"
        assert result.initial_state.rpm == 1200
        assert result.final_state.rpm == 900
        assert result.test_pwm_percent == 70

    def test_parse_reverted(self):
        """Parse the daemon's pwm_enable_reverted payload (see
        daemon/src/api/handlers/hwmon_ctl.rs::classify_verify_result)."""
        from control_ofc.api.models import parse_hwmon_verify_result

        data = {
            "header_id": "it8696-isa-0a30/pwm1",
            "result": "pwm_enable_reverted",
            "initial_state": {"pwm_enable": 1, "pwm_raw": 128, "rpm": 1200},
            "final_state": {"pwm_enable": 2, "pwm_raw": 128, "rpm": 1200},
            "test_pwm_percent": 70,
            "wait_seconds": 6,
            "details": "BIOS reclaimed pwm_enable",
        }
        result = parse_hwmon_verify_result(data)
        assert result.result == "pwm_enable_reverted"
        assert result.initial_state.pwm_enable == 1
        assert result.final_state.pwm_enable == 2

    def test_parse_restore_failed_default_false(self):
        """Audit P2.5: ``restore_failed`` defaults to False when the daemon
        does not include the field (older daemons, or successful restores —
        the field is ``skip_serializing_if = "is_false"`` on the daemon side).
        """
        from control_ofc.api.models import parse_hwmon_verify_result

        data = {
            "header_id": "it8696-isa-0a30/pwm1",
            "result": "effective",
            "initial_state": {"pwm_enable": 1, "pwm_raw": 128, "pwm_percent": 50, "rpm": 1200},
            "final_state": {"pwm_enable": 1, "pwm_raw": 178, "pwm_percent": 70, "rpm": 900},
            "test_pwm_percent": 70,
            "wait_seconds": 6,
            "details": "PWM accepted and RPM changed",
        }
        result = parse_hwmon_verify_result(data)
        assert result.restore_failed is False

    def test_parse_restore_failed_true(self):
        """Audit P2.5 regression: when the daemon's post-verify restore PWM
        write fails, ``restore_failed: true`` reaches the GUI dataclass and
        the diagnostics page can surface the warning. Previously the daemon
        silently swallowed the error and the GUI had no signal that the
        header was left at the verify test value.
        """
        from control_ofc.api.models import parse_hwmon_verify_result

        data = {
            "header_id": "it8696-isa-0a30/pwm1",
            "result": "effective",
            "initial_state": {"pwm_enable": 1, "pwm_raw": 128, "pwm_percent": 50, "rpm": 1200},
            "final_state": {"pwm_enable": 1, "pwm_raw": 51, "pwm_percent": 20, "rpm": 700},
            "test_pwm_percent": 20,
            "wait_seconds": 6,
            "details": "PWM accepted and RPM changed",
            "restore_failed": True,
        }
        result = parse_hwmon_verify_result(data)
        assert result.restore_failed is True


# ---------------------------------------------------------------------------
# System State page — re-vehicled PWM-clamped verify result
#
# tests/test_system_state_page.py covers _show_verify_result for the effective
# (SuccessChip) and pwm_enable_reverted (CriticalChip) branches; the
# pwm_value_clamped → WarningChip branch has no live equivalent, so it is pinned
# here against the live page that now owns the verify UI.
# ---------------------------------------------------------------------------


class TestSystemStateVerifyClampedResult:
    def test_verify_shows_clamped_result(self, qtbot):
        page, _ = _ss_page(qtbot)
        result = HwmonVerifyResult(
            header_id="h1",
            result="pwm_value_clamped",
            initial_state=HwmonVerifyState(pwm_enable=1, pwm_raw=128, rpm=1200),
            final_state=HwmonVerifyState(pwm_enable=1, pwm_raw=128, rpm=1200),
            test_pwm_percent=70,
            wait_seconds=6,
            details="PWM register overridden",
        )
        page._show_verify_result(result)
        assert not page._verify_result_label.isHidden()
        assert "clamped" in page._verify_result_label.text().lower()
        assert page._verify_result_label.property("class") == "WarningChip"


# ---------------------------------------------------------------------------
# System State page — re-vehicled PWM-verify worker-thread lifecycle
#
# The verify worker (_VerifyWorker) moved with the verify UI to SystemStatePage.
# tests/test_system_state_page.py drives the verify *handlers* but never the real
# thread lifecycle, so the thread creation / socket-path guard / F-1 in-flight
# teardown / button re-enable are pinned here against the live page.
# ---------------------------------------------------------------------------


class TestSystemStateVerifyWorker:
    def test_ensure_verify_worker_requires_socket_path(self, qtbot):
        """No worker is created when there is no client (hence no socket path)."""
        page, _ = _ss_page(qtbot, client=None)
        assert page._ensure_verify_worker() is False
        assert page._verify_thread is None
        assert page._verify_worker is None

    def test_ensure_verify_worker_creates_thread(self, qtbot):
        """A client with a socket path spins the worker + thread once (idempotent),
        and cleanup() tears them down."""
        client = MagicMock()
        client.socket_path = "/tmp/fake.sock"
        page, _ = _ss_page(qtbot, client=client)

        assert page._ensure_verify_worker() is True
        assert page._verify_thread is not None
        assert page._verify_worker is not None
        assert page._verify_thread.isRunning()

        prev_thread = page._verify_thread
        assert page._ensure_verify_worker() is True
        assert page._verify_thread is prev_thread  # second call does not replace it

        page.cleanup()
        assert page._verify_thread is None
        assert page._verify_worker is None

    def test_teardown_during_inflight_call_delivers_no_result(self, qtbot):
        """F-1: tearing the worker down while a ``do_verify`` is in flight must not
        deliver a result onto the closing page.

        ``_teardown_worker`` disconnects the worker's result signals *before*
        ``worker.shutdown()`` closes the per-thread client — and here that very
        ``close()`` releases the blocked verify, so the disconnect→emit ordering
        is deterministic with no sleeps: a result emitted after teardown reaches
        no connected slot.
        """

        class _BlockingVerifyClient:
            socket_path = "/tmp/control-ofc-f1-inflight.sock"

            def __init__(self) -> None:
                self.started = threading.Event()
                self.closed = threading.Event()

            def verify_hwmon_pwm(self, header_id):
                self.started.set()
                # Block until shutdown() closes us — models an in-flight probe
                # that outlives the teardown request.
                self.closed.wait(5)
                return HwmonVerifyResult(header_id=header_id, result="effective")

            def close(self) -> None:
                self.closed.set()

        client = _BlockingVerifyClient()
        page, _ = _ss_page(qtbot, client=client)
        assert page._ensure_verify_worker()
        worker = page._verify_worker
        worker._client = client  # use the blocking fake, not a real DaemonClient

        # Spy on the worker's result signal — a proxy for the page slot it feeds.
        got: list = []
        worker.verify_ok.connect(got.append)

        # Kick do_verify onto the worker thread; it blocks in-flight.
        page._verify_request.emit("hwmon:x:pwm1")
        qtbot.waitUntil(client.started.is_set, timeout=2000)

        # Tear down while the call is in flight. cleanup() disconnects first,
        # then close() releases the blocked call, whose now-disconnected emit
        # must reach no one.
        page.cleanup()

        assert got == []  # in-flight verify_ok reached no connected slot
        assert client.closed.is_set()  # per-thread client was closed on teardown
        assert page._verify_worker is None
        assert page._verify_thread is None

    def test_on_verify_ok_re_enables_button(self, qtbot):
        """A successful verify result re-enables the button and shows the label."""
        page, _ = _ss_page(qtbot)
        page._verify_btn.setEnabled(False)
        page._verify_btn.setText("Testing...")

        page._on_verify_ok(
            HwmonVerifyResult(
                header_id="h1",
                result="effective",
                initial_state=HwmonVerifyState(pwm_enable=1, rpm=1200),
                final_state=HwmonVerifyState(pwm_enable=1, rpm=900),
                test_pwm_percent=70,
                wait_seconds=6,
                details="PWM control working",
            )
        )

        assert page._verify_btn.isEnabled()
        assert page._verify_btn.text() == "Test PWM Control"
        assert not page._verify_result_label.isHidden()


# ---------------------------------------------------------------------------
# Support bundle — hardware diagnostics inclusion
# ---------------------------------------------------------------------------


class TestSupportBundleHwDiag:
    def test_bundle_includes_board_when_hw_diag_fetched(self, tmp_path):

        state = _make_state()
        diag_svc = DiagnosticsService(state)
        diag_svc.last_hw_diagnostics = _make_diag_result(
            board_vendor="Gigabyte Technology Co., Ltd.",
            board_name="X870E AORUS MASTER",
            bios_version="F13a",
            revert_counts={"h1": 3},
        )

        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            bundle_path = tmp_path / "bundle.json"
            diag_svc.export_support_bundle(bundle_path)

        import json

        bundle = json.loads(bundle_path.read_text())
        assert "hardware_diagnostics" in bundle
        assert bundle["hardware_diagnostics"]["board"]["vendor"] == (
            "Gigabyte Technology Co., Ltd."
        )
        assert bundle["hardware_diagnostics"]["hwmon"]["enable_revert_counts"] == {"h1": 3}

    def test_bundle_excludes_hw_diag_when_not_fetched(self, tmp_path):
        state = _make_state()
        diag_svc = DiagnosticsService(state)

        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            bundle_path = tmp_path / "bundle.json"
            diag_svc.export_support_bundle(bundle_path)

        import json

        bundle = json.loads(bundle_path.read_text())
        assert "hardware_diagnostics" not in bundle


# ---------------------------------------------------------------------------
# DEC-101 — verify wait timing safety constants
# ---------------------------------------------------------------------------


class TestVerifyTimingConstantsDec101:
    """Regression: GUI's safety auto-resume and HTTP timeout must always
    stay strictly above the daemon's verify wait. The daemon was bumped
    from 3 s to 6 s in DEC-101; if the GUI side is later changed without
    updating both the safety timer and the HTTP timeout, the 1 Hz control
    loop or the HTTP layer would race the daemon's readback.
    """

    def test_http_timeout_exceeds_daemon_verify_wait_with_slack(self):
        # The verify HTTP call is hard-coded to a 12 s per-call timeout so
        # the global API_TIMEOUT_S can stay aggressive for fast endpoints.
        # We assert the literal here so a future drift to <8 s is caught.
        import inspect

        from control_ofc.api.client import DaemonClient

        src = inspect.getsource(DaemonClient.verify_hwmon_pwm)
        # Look for the explicit timeout=NN.N kwarg.
        import re

        m = re.search(r"timeout\s*=\s*(\d+(?:\.\d+)?)", src)
        assert m is not None, "verify_hwmon_pwm must pass an explicit timeout"
        timeout_value = float(m.group(1))
        assert timeout_value >= 9.0, (
            f"verify_hwmon_pwm timeout={timeout_value} s must be ≥ 9 s "
            f"(daemon verify wait 6 s + ~3 s round-trip slack). DEC-101."
        )


# ---------------------------------------------------------------------------
# DEC-144 — dual-chip remediation ordering (re-vehicled)
#
# The retired Diagnostics dual-chip warning banner moved to the System State
# issue cards, which build their detail from ``dual_chip_warning_html``. The
# banner's unique assertion — the it87-dkms-git driver update must precede the
# legacy ``mmio=on`` fallback — is pinned here on that pure builder (the
# pretty-name / None paths are covered by TestDualChipHelpersDec101).
# ---------------------------------------------------------------------------


class TestDualChipRemediationOrderingDec144:
    def test_missing_secondary_chip_remediation_order(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_warning_html

        out = dual_chip_warning_html("X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"])
        assert out is not None
        # Must name the missing chip, the board, and the remediation — driver
        # update first (DEC-144), with mmio=on retained as the legacy fallback.
        assert "IT87952E" in out or "it87952" in out.lower()
        assert "X870E AORUS MASTER" in out
        assert "it87-dkms-git" in out
        assert "mmio=on" in out
        assert out.find("it87-dkms-git") < out.find("mmio=on"), (
            "DEC-144: the driver update must precede the legacy mmio=on step"
        )
        assert "modprobe" in out.lower()


# ---------------------------------------------------------------------------
# DEC-101 — model parsing for new daemon fields
# ---------------------------------------------------------------------------


class TestHardwareDiagnosticsModelDec101:
    """The Python parser must accept and store the new
    `expected_chips` / `kernel_detected_chips` fields, default them to
    [] when absent, and tolerate non-string list members defensively.
    """

    def test_parse_expected_chips(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
            "expected_chips": ["it8696", "it87952"],
            "kernel_detected_chips": ["it8696"],
        }
        result = parse_hardware_diagnostics(data)
        assert result.expected_chips == ["it8696", "it87952"]
        assert result.kernel_detected_chips == ["it8696"]

    def test_missing_fields_default_to_empty(self):
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(data)
        assert result.expected_chips == []
        assert result.kernel_detected_chips == []

    def test_filters_falsy_entries(self):
        # Defensive: future shape drift could send None/"" entries; the
        # parser must drop them rather than propagate.
        from control_ofc.api.models import parse_hardware_diagnostics

        data = {
            "hwmon": {"chips_detected": [], "total_headers": 0, "writable_headers": 0},
            "thermal_safety": {"state": "normal", "cpu_sensor_found": True},
            "kernel_modules": [],
            "acpi_conflicts": [],
            "expected_chips": ["it8696", "", None, "it87952"],
        }
        result = parse_hardware_diagnostics(data)
        assert result.expected_chips == ["it8696", "it87952"]


# ---------------------------------------------------------------------------
# DEC-101 — dual_chip_warning_html / dual_chip_verify_hint pure functions
# ---------------------------------------------------------------------------


class TestDualChipHelpersDec101:
    def test_warning_lists_missing_chip_pretty_name(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_warning_html

        out = dual_chip_warning_html(
            "X870E AORUS MASTER",
            ["it8696", "it87952"],
            ["it8696"],
        )
        assert out is not None
        # Pretty-names render as IT8696E / IT87952E so users can match
        # the silkscreen on their motherboard.
        assert "IT87952E" in out
        assert "X870E AORUS MASTER" in out

    def test_warning_returns_none_for_unknown_board(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_warning_html

        # No expected chips → no warning regardless of detected list.
        assert dual_chip_warning_html("Some Other Board", [], []) is None
        assert dual_chip_warning_html("Some Other Board", [], ["it8696"]) is None

    def test_warning_returns_none_when_all_present(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_warning_html

        assert (
            dual_chip_warning_html(
                "X870E AORUS MASTER",
                ["it8696", "it87952"],
                ["it8696", "it87952"],
            )
            is None
        )

    def test_verify_hint_only_for_clamped_or_no_rpm(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_verify_hint

        for result in ("pwm_value_clamped", "no_rpm_effect"):
            assert dual_chip_verify_hint(result, ["it8696", "it87952"], ["it8696"]) is not None
        for result in ("effective", "pwm_enable_reverted", "rpm_unavailable"):
            assert dual_chip_verify_hint(result, ["it8696", "it87952"], ["it8696"]) is None

    def test_verify_hint_none_when_no_dual_chip(self):
        from control_ofc.ui.hwmon_guidance import dual_chip_verify_hint

        # Single-chip board → never a dual-chip hint.
        assert dual_chip_verify_hint("pwm_value_clamped", ["it8696"], ["it8696"]) is None
        # Empty expected_chips → never a hint.
        assert dual_chip_verify_hint("pwm_value_clamped", [], []) is None
