"""R34: Diagnostics page — latency semantics, transparent labels, event log
detail retrieval, lease explanation, and theme alignment.

Covers: transparent labels on Overview/Lease/Telemetry cards, subsystem reason
display, age_ms clarification note, lease explanation card, QPlainTextEdit log,
daemon/controller/journal detail buttons, source labeling, bounded journal.
"""

from __future__ import annotations

from unittest.mock import patch

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton

from control_ofc.api.models import (
    Capabilities,
    ConnectionState,
    DaemonStatus,
    HwmonCapability,
    LeaseState,
    OpenfanCapability,
    OperationMode,
    SubsystemStatus,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


def _make_page(qtbot, state=None, diag=None):
    s = state or _make_state()
    page = DiagnosticsPage(state=s, diagnostics_service=diag)
    qtbot.addWidget(page)
    return page, s


# ---------------------------------------------------------------------------
# Transparent label tests
# ---------------------------------------------------------------------------


class TestOverviewTransparentLabels:
    """Overview card labels have transparent backgrounds for theme safety."""

    def test_daemon_version_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_daemonVersion")
        assert label is not None
        assert "transparent" in label.styleSheet().lower()

    def test_daemon_status_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_daemonStatus")
        assert "transparent" in label.styleSheet().lower()

    def test_uptime_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_daemonUptime")
        assert "transparent" in label.styleSheet().lower()

    def test_subsystems_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_subsystems")
        assert "transparent" in label.styleSheet().lower()

    def test_age_note_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_ageNote")
        assert "transparent" in label.styleSheet().lower()

    def test_device_title_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_deviceTitle")
        assert "transparent" in label.styleSheet().lower()

    def test_openfan_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_openfan")
        assert "transparent" in label.styleSheet().lower()

    def test_hwmon_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_hwmon")
        assert "transparent" in label.styleSheet().lower()

    def test_features_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_features")
        assert "transparent" in label.styleSheet().lower()


class TestLeaseTabTransparentLabels:
    """Lease tab labels have transparent backgrounds."""

    def test_lease_explanation_title_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_leaseExplainTitle")
        assert "transparent" in label.styleSheet().lower()

    def test_lease_explanation_text_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_leaseExplainText")
        assert "transparent" in label.styleSheet().lower()

    def test_lease_held_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_leaseHeld")
        assert "transparent" in label.styleSheet().lower()

    def test_lease_id_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_leaseId")
        assert "transparent" in label.styleSheet().lower()

    def test_lease_owner_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_leaseOwner")
        assert "transparent" in label.styleSheet().lower()

    def test_lease_ttl_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_leaseTtl")
        assert "transparent" in label.styleSheet().lower()

    def test_lease_required_label_transparent(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_leaseRequired")
        assert "transparent" in label.styleSheet().lower()


class TestNoInlineFontSizes:
    """No labels inside cards use hardcoded font-size px overrides."""

    def test_overview_labels_no_px_font_size(self, qtbot):
        page, _ = _make_page(qtbot)
        for name in [
            "Diagnostics_Label_daemonVersion",
            "Diagnostics_Label_daemonStatus",
            "Diagnostics_Label_daemonUptime",
            "Diagnostics_Label_subsystems",
            "Diagnostics_Label_deviceTitle",
            "Diagnostics_Label_openfan",
            "Diagnostics_Label_hwmon",
            "Diagnostics_Label_features",
        ]:
            label = page.findChild(QLabel, name)
            assert label is not None, f"Label {name} not found"
            assert "font-size" not in label.styleSheet(), (
                f"{name} has hardcoded font-size in stylesheet"
            )

    def test_lease_labels_no_px_font_size(self, qtbot):
        page, _ = _make_page(qtbot)
        for name in [
            "Diagnostics_Label_leaseExplainTitle",
            "Diagnostics_Label_leaseExplainText",
            "Diagnostics_Label_leaseHeld",
            "Diagnostics_Label_leaseId",
            "Diagnostics_Label_leaseOwner",
            "Diagnostics_Label_leaseTtl",
            "Diagnostics_Label_leaseRequired",
        ]:
            label = page.findChild(QLabel, name)
            assert "font-size" not in label.styleSheet(), (
                f"{name} has hardcoded font-size in stylesheet"
            )


# ---------------------------------------------------------------------------
# Age note and subsystem reason display
# ---------------------------------------------------------------------------


class TestSubsystemDisplay:
    """Overview shows subsystem reason text and age_ms explanation."""

    def test_age_note_present(self, qtbot):
        page, _ = _make_page(qtbot)
        label = page.findChild(QLabel, "Diagnostics_Label_ageNote")
        assert label is not None
        assert "daemon last polled" in label.text().lower()

    def test_subsystem_reason_shown(self, qtbot):
        page, state = _make_page(qtbot)
        status = DaemonStatus(
            overall_status="ok",
            subsystems=[
                SubsystemStatus(name="openfan", status="ok", age_ms=500, reason="readings fresh"),
                SubsystemStatus(name="hwmon", status="warn", age_ms=3000, reason="readings stale"),
            ],
        )
        state.set_status(status)
        label = page.findChild(QLabel, "Diagnostics_Label_subsystems")
        text = label.text()
        assert "readings fresh" in text
        assert "readings stale" in text

    def test_subsystem_age_shown(self, qtbot):
        page, state = _make_page(qtbot)
        status = DaemonStatus(
            overall_status="ok",
            subsystems=[
                SubsystemStatus(name="openfan", status="ok", age_ms=847),
            ],
        )
        state.set_status(status)
        label = page.findChild(QLabel, "Diagnostics_Label_subsystems")
        assert "847" in label.text()

    def test_uptime_displayed(self, qtbot):
        page, state = _make_page(qtbot)
        status = DaemonStatus(overall_status="ok", uptime_seconds=3661)
        state.set_status(status)
        label = page.findChild(QLabel, "Diagnostics_Label_daemonUptime")
        assert "1h 1m 1s" in label.text()


# ---------------------------------------------------------------------------
# Lease explanation
# ---------------------------------------------------------------------------


class TestLeaseExplanation:
    """Lease tab contains a truthful explanation of what a lease is."""

    def test_lease_explanation_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        title = page.findChild(QLabel, "Diagnostics_Label_leaseExplainTitle")
        assert title is not None
        assert "lease" in title.text().lower()

    def test_lease_explanation_mentions_exclusive(self, qtbot):
        page, _ = _make_page(qtbot)
        text_label = page.findChild(QLabel, "Diagnostics_Label_leaseExplainText")
        text = text_label.text().lower()
        assert "exclusive" in text

    def test_lease_explanation_mentions_hwmon(self, qtbot):
        page, _ = _make_page(qtbot)
        text_label = page.findChild(QLabel, "Diagnostics_Label_leaseExplainText")
        assert "hwmon" in text_label.text().lower()

    def test_lease_explanation_mentions_60_seconds(self, qtbot):
        page, _ = _make_page(qtbot)
        text_label = page.findChild(QLabel, "Diagnostics_Label_leaseExplainText")
        assert "60 seconds" in text_label.text()

    def test_lease_explanation_mentions_openfan_no_lease(self, qtbot):
        page, _ = _make_page(qtbot)
        text_label = page.findChild(QLabel, "Diagnostics_Label_leaseExplainText")
        assert "openfan" in text_label.text().lower()

    def test_lease_status_updates_on_signal(self, qtbot):
        page, state = _make_page(qtbot)
        state.set_lease(
            LeaseState(
                held=True,
                lease_id="abc-123",
                owner_hint="gui",
                ttl_seconds_remaining=45,
                lease_required=True,
            )
        )
        label = page.findChild(QLabel, "Diagnostics_Label_leaseHeld")
        assert "Held" in label.text()
        id_label = page.findChild(QLabel, "Diagnostics_Label_leaseId")
        assert "abc-123" in id_label.text()


# ---------------------------------------------------------------------------
# Event log — QPlainTextEdit and category buttons
# ---------------------------------------------------------------------------


class TestEventLogWidget:
    """Event log uses QPlainTextEdit with bounded line count."""

    def test_log_view_is_plain_text_edit(self, qtbot):
        page, _ = _make_page(qtbot)
        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert log_view is not None

    def test_log_view_is_read_only(self, qtbot):
        page, _ = _make_page(qtbot)
        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert log_view.isReadOnly()

    def test_log_view_has_max_block_count(self, qtbot):
        page, _ = _make_page(qtbot)
        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert log_view.maximumBlockCount() == 2000

    def test_log_view_monospace_font(self, qtbot):
        page, _ = _make_page(qtbot)
        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert "monospace" in log_view.font().family().lower()


class TestEventLogCategoryButtons:
    """Category detail buttons exist and are correctly labeled."""

    def test_daemon_status_button_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_daemonStatus")
        assert btn is not None
        assert btn.isEnabled()
        assert "daemon" in btn.text().lower()

    def test_controller_status_button_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_controllerStatus")
        assert btn is not None
        assert btn.isEnabled()
        assert "controller" in btn.text().lower()

    def test_system_journal_button_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_systemJournal")
        assert btn is not None
        assert btn.isEnabled()
        assert "journal" in btn.text().lower()


class TestDaemonStatusRetrieval:
    """Daemon Status button retrieves and displays formatted status."""

    def test_daemon_status_appends_to_log(self, qtbot):
        state = _make_state()
        state.set_status(DaemonStatus(overall_status="ok", uptime_seconds=120))
        diag = DiagnosticsService(state)
        page, _ = _make_page(qtbot, state=state, diag=diag)

        page._fetch_daemon_status()

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        text = log_view.toPlainText()
        assert "DAEMON STATUS" in text
        assert "ok" in text

    def test_daemon_status_shows_source_label(self, qtbot):
        state = _make_state()
        diag = DiagnosticsService(state)
        page, _ = _make_page(qtbot, state=state, diag=diag)

        page._fetch_daemon_status()

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        text = log_view.toPlainText()
        assert "GUI application state" in text


class TestControllerStatusRetrieval:
    """Controller Status button retrieves and displays controller info."""

    def test_controller_status_appends_to_log(self, qtbot):
        state = _make_state()
        state.set_capabilities(
            Capabilities(
                daemon_version="0.2.0",
                openfan=OpenfanCapability(
                    present=True,
                    channels=10,
                    write_support=True,
                    rpm_support=True,
                ),
                hwmon=HwmonCapability(present=True, pwm_header_count=3),
            )
        )
        diag = DiagnosticsService(state)
        page, _ = _make_page(qtbot, state=state, diag=diag)

        page._fetch_controller_status()

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        text = log_view.toPlainText()
        assert "CONTROLLER STATUS" in text
        assert "OpenFan" in text

    def test_controller_no_caps_shows_message(self, qtbot):
        state = _make_state()
        diag = DiagnosticsService(state)
        page, _ = _make_page(qtbot, state=state, diag=diag)

        page._fetch_controller_status()

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert "not yet received" in log_view.toPlainText()

    def test_controller_status_source_label(self, qtbot):
        state = _make_state()
        state.set_capabilities(Capabilities(daemon_version="0.2.0"))
        diag = DiagnosticsService(state)
        page, _ = _make_page(qtbot, state=state, diag=diag)

        page._fetch_controller_status()

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert "/capabilities" in log_view.toPlainText()


# ---------------------------------------------------------------------------
# Journal retrieval
# ---------------------------------------------------------------------------


class TestJournalRetrieval:
    """System journal retrieval is bounded and handles errors truthfully."""

    def test_journal_bounded_line_limit(self):
        """fetch_journal_entries uses --lines to bound output."""
        from control_ofc.services.diagnostics_service import JOURNAL_LINE_LIMIT

        assert JOURNAL_LINE_LIMIT > 0
        assert JOURNAL_LINE_LIMIT <= 500  # sanity upper bound

    def test_journal_success_shows_source(self, qtbot):
        state = _make_state()
        diag = DiagnosticsService(state)
        page, _ = _make_page(qtbot, state=state, diag=diag)

        fake_output = "2026-03-28T12:00:00+0000 control-ofc-daemon[1234]: Started OK\n" * 5
        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_output
            mock_run.return_value.stderr = ""

            page._fetch_journal()

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        text = log_view.toPlainText()
        assert "SYSTEM JOURNAL" in text
        assert "journalctl" in text

    def test_journal_not_found_shows_error(self, qtbot):
        state = _make_state()
        diag = DiagnosticsService(state)
        page, _ = _make_page(qtbot, state=state, diag=diag)

        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            page._fetch_journal()

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert "not found" in log_view.toPlainText().lower()

    def test_journal_timeout_shows_error(self, qtbot):
        import subprocess as sp

        state = _make_state()
        diag = DiagnosticsService(state)
        page, _ = _make_page(qtbot, state=state, diag=diag)

        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="journalctl", timeout=5),
        ):
            page._fetch_journal()

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert "timed out" in log_view.toPlainText().lower()

    def test_journal_permission_shows_hint(self, qtbot):
        state = _make_state()
        diag = DiagnosticsService(state)
        page, _ = _make_page(qtbot, state=state, diag=diag)

        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = (
                "No journal files were opened due to insufficient permissions."
            )

            page._fetch_journal()

        log_view = page.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        text = log_view.toPlainText()
        assert "systemd-journal" in text

    def test_journal_uses_correct_unit_name(self):
        """journalctl must filter by control-ofc-daemon, not .service (R51 fix)."""
        state = _make_state()
        diag = DiagnosticsService(state)

        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "some log output"
            mock_run.return_value.stderr = ""
            diag.fetch_journal_entries()

        args = mock_run.call_args[0][0]  # first positional arg = command list
        assert "control-ofc-daemon" in args
        assert "control-ofc-daemon.service" not in args


class TestSupportBundleContents:
    """Support bundle includes journal, fan state, and missing_sections (R51)."""

    def test_bundle_includes_journal(self, tmp_path):
        state = _make_state()
        diag = DiagnosticsService(state)

        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "daemon log output here"
            mock_run.return_value.stderr = ""
            bundle_path = tmp_path / "bundle.json"
            diag.export_support_bundle(bundle_path)

        import json

        bundle = json.loads(bundle_path.read_text())
        assert "journal" in bundle
        assert "daemon log output" in bundle["journal"]

    def test_bundle_includes_fan_state(self, tmp_path):
        state = _make_state()
        diag = DiagnosticsService(state)

        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            bundle_path = tmp_path / "bundle.json"
            diag.export_support_bundle(bundle_path)

        import json

        bundle = json.loads(bundle_path.read_text())
        assert "fan_state" in bundle
        assert isinstance(bundle["fan_state"], list)

    def test_bundle_missing_sections_when_no_daemon(self, tmp_path):
        diag = DiagnosticsService(state=None)

        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            bundle_path = tmp_path / "bundle.json"
            diag.export_support_bundle(bundle_path)

        import json

        bundle = json.loads(bundle_path.read_text())
        assert "missing_sections" in bundle
        assert any("AppState" in s for s in bundle["missing_sections"])


# ---------------------------------------------------------------------------
# Service layer tests
# ---------------------------------------------------------------------------


class TestDiagnosticsServiceFormatDaemonStatus:
    """format_daemon_status produces truthful, labeled output."""

    def test_no_state_returns_message(self):
        svc = DiagnosticsService(state=None)
        text = svc.format_daemon_status()
        assert "no application state" in text.lower()

    def test_includes_connection_and_mode(self):
        state = _make_state()
        svc = DiagnosticsService(state)
        text = svc.format_daemon_status()
        assert "connected" in text.lower()
        assert "automatic" in text.lower()

    def test_includes_subsystem_detail(self):
        state = _make_state()
        state.set_status(
            DaemonStatus(
                overall_status="warn",
                subsystems=[
                    SubsystemStatus(
                        name="openfan", status="ok", age_ms=500, reason="readings fresh"
                    ),
                ],
            )
        )
        svc = DiagnosticsService(state)
        text = svc.format_daemon_status()
        assert "openfan" in text
        assert "500" in text
        assert "readings fresh" in text

    def test_includes_source_attribution(self):
        state = _make_state()
        svc = DiagnosticsService(state)
        text = svc.format_daemon_status()
        assert "source:" in text.lower()


class TestDiagnosticsServiceFormatControllerStatus:
    """format_controller_status produces truthful, labeled output."""

    def test_no_state_returns_message(self):
        svc = DiagnosticsService(state=None)
        text = svc.format_controller_status()
        assert "no application state" in text.lower()

    def test_no_caps_returns_message(self):
        state = _make_state()
        svc = DiagnosticsService(state)
        text = svc.format_controller_status()
        assert "not yet received" in text.lower()

    def test_openfan_present_details(self):
        state = _make_state()
        state.set_capabilities(
            Capabilities(
                daemon_version="0.2.0",
                openfan=OpenfanCapability(
                    present=True,
                    channels=10,
                    write_support=True,
                    rpm_support=True,
                ),
            )
        )
        svc = DiagnosticsService(state)
        text = svc.format_controller_status()
        assert "Present: Yes" in text
        assert "Channels: 10" in text

    def test_openfan_absent_guidance(self):
        state = _make_state()
        state.set_capabilities(
            Capabilities(
                daemon_version="0.2.0",
                openfan=OpenfanCapability(present=False),
            )
        )
        svc = DiagnosticsService(state)
        text = svc.format_controller_status()
        assert "Present: No" in text
        assert "USB" in text or "serial" in text.lower()

    def test_includes_source_attribution(self):
        state = _make_state()
        state.set_capabilities(Capabilities(daemon_version="0.2.0"))
        svc = DiagnosticsService(state)
        text = svc.format_controller_status()
        assert "source:" in text.lower()


class TestDiagnosticsServiceFetchJournal:
    """fetch_journal_entries is bounded and handles failure gracefully."""

    def test_successful_fetch(self):
        svc = DiagnosticsService()
        fake_output = "2026-03-28T12:00:00 test line"
        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_output
            mock_run.return_value.stderr = ""
            text = svc.fetch_journal_entries()
        assert "test line" in text
        assert "journalctl" in text

    def test_file_not_found(self):
        svc = DiagnosticsService()
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            text = svc.fetch_journal_entries()
        assert "not found" in text.lower()

    def test_timeout(self):
        import subprocess as sp

        svc = DiagnosticsService()
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="journalctl", timeout=5),
        ):
            text = svc.fetch_journal_entries()
        assert "timed out" in text.lower()

    def test_permission_hint(self):
        svc = DiagnosticsService()
        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "insufficient permissions"
            text = svc.fetch_journal_entries()
        assert "systemd-journal" in text

    def test_empty_no_permission_hint(self):
        svc = DiagnosticsService()
        with patch("control_ofc.services.diagnostics_service.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            text = svc.fetch_journal_entries()
        assert "No journal entries" in text

    def test_os_error(self):
        svc = DiagnosticsService()
        with patch(
            "control_ofc.services.diagnostics_service.subprocess.run",
            side_effect=OSError("broken pipe"),
        ):
            text = svc.fetch_journal_entries()
        assert "broken pipe" in text.lower()


class TestCopyLastErrors:
    """Copy Last Errors button gathers error/warning events to clipboard (R55)."""

    def test_copy_errors_button_exists(self, qtbot):
        page, _ = _make_page(qtbot)
        btn = page.findChild(QPushButton, "Diagnostics_Btn_copyErrors")
        assert btn is not None
        assert btn.isEnabled()

    def test_copy_errors_filters_events(self, qtbot):
        state = _make_state()
        diag = DiagnosticsService(state)
        diag.log_event("error", "gpu", "PMFW write failed")
        diag.log_event("info", "polling", "sensors polled ok")
        diag.log_event("warning", "serial", "transport timeout")

        page, _ = _make_page(qtbot, state=state, diag=diag)
        page._copy_last_errors()

        from PySide6.QtWidgets import QApplication

        text = QApplication.clipboard().text()
        assert "PMFW write failed" in text
        assert "transport timeout" in text
        assert "sensors polled ok" not in text

    def test_copy_errors_empty_shows_message(self, qtbot):
        state = _make_state()
        diag = DiagnosticsService(state)
        # No error events — only info
        diag.log_event("info", "polling", "all good")

        page, _ = _make_page(qtbot, state=state, diag=diag)
        page._copy_last_errors()

        assert "No recent errors" in page._status_label.text()


# ---------------------------------------------------------------------------
# Label content tests (T6 audit finding — verify text, not just CSS)
# ---------------------------------------------------------------------------


class TestLabelContent:
    """Labels display correct content when state signals are emitted."""

    def test_daemon_version_label_shows_version_on_capabilities(self, qtbot):
        state = _make_state()
        page, _ = _make_page(qtbot, state=state)

        state.set_capabilities(Capabilities(daemon_version="1.2.3", api_version=2))
        label = page.findChild(QLabel, "Diagnostics_Label_daemonVersion")
        assert label is not None
        assert "1.2.3" in label.text()

    def test_daemon_status_label_shows_overall_status(self, qtbot):
        state = _make_state()
        page, _ = _make_page(qtbot, state=state)

        state.set_status(DaemonStatus(overall_status="healthy", daemon_version="1.0.0"))
        label = page.findChild(QLabel, "Diagnostics_Label_daemonStatus")
        assert label is not None
        assert "healthy" in label.text()
