"""Super-I/O detection: model parse, client method, and Diagnostics-page
integration (auto-fetch off-thread + 404-degrade) — DEC-202, P3."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtWidgets import QLabel, QWidget

from control_ofc.api.models import (
    ConnectionState,
    OperationMode,
    SuperIoChip,
    SuperIoRecommendation,
    SuperIoReport,
    parse_superio_report,
)
from control_ofc.services.app_state import AppState
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage

# ── Model parsing ────────────────────────────────────────────────────


def test_parse_superio_report_full():
    data = {
        "api_version": 1,
        "arch_supported": True,
        "chips": [
            {
                "chip_name": "it8688",
                "vendor": "ite",
                "evidence": ["dmi_board_table", "kernel_log"],
                "confidence": "high",
                "expected_module": "it87",
                "module_loaded": False,
                "hwmon_present": False,
                "recommendation": {
                    "module": "it87",
                    "in_mainline": False,
                    "load_hint": "install it87-dkms-git",
                    "reason": "board lists it8688",
                    "risk_notes": ["needs DKMS"],
                },
                "caveats": [],
                "future_field": "ignored",  # forward-compat: unknown key dropped
            }
        ],
        "acpi_conflict_drivers": ["it87"],
        "notes": ["detection is not control"],
    }
    r = parse_superio_report(data)
    assert r.arch_supported is True
    assert len(r.chips) == 1
    c = r.chips[0]
    assert c.chip_name == "it8688"
    assert c.vendor == "ite"
    assert c.evidence == ["dmi_board_table", "kernel_log"]
    assert c.recommendation is not None
    assert c.recommendation.module == "it87"
    assert c.recommendation.in_mainline is False
    assert c.recommendation.risk_notes == ["needs DKMS"]
    assert r.acpi_conflict_drivers == ["it87"]
    assert r.notes == ["detection is not control"]


def test_parse_superio_report_defaults_and_forward_compat():
    # Absent arch_supported → False (AIP-180 safe default); absent recommendation
    # → None; unknown chip keys ignored (forward compatibility).
    r = parse_superio_report({"chips": [{"chip_name": "nct6799", "new_thing": 1}]})
    assert r.api_version == 1
    assert r.arch_supported is False
    assert len(r.chips) == 1
    assert r.chips[0].chip_name == "nct6799"
    assert r.chips[0].recommendation is None


def test_parse_superio_report_non_x86():
    r = parse_superio_report(
        {"arch_supported": False, "chips": [], "notes": ["unsupported architecture"]}
    )
    assert r.arch_supported is False
    assert r.chips == []
    assert r.notes == ["unsupported architecture"]


def test_parse_superio_report_skips_non_dict_chips():
    r = parse_superio_report({"chips": ["garbage", {"chip_name": "it8628"}]})
    assert [c.chip_name for c in r.chips] == ["it8628"]


# ── Client method ────────────────────────────────────────────────────


def test_client_superio_detect_calls_get_and_parses():
    from control_ofc.api.client import DaemonClient

    client = DaemonClient.__new__(DaemonClient)
    client._get = MagicMock(
        return_value={
            "arch_supported": True,
            "chips": [{"chip_name": "it8688", "expected_module": "it87"}],
        }
    )
    report = client.superio_detect()
    client._get.assert_called_once_with("/inventory/superio")
    assert report.chips[0].chip_name == "it8688"


# ── Page integration (off-thread worker) ─────────────────────────────


def _state() -> AppState:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    return s


def _report() -> SuperIoReport:
    return SuperIoReport(
        arch_supported=True,
        chips=[
            SuperIoChip(
                chip_name="it8688",
                vendor="ite",
                confidence="medium",
                expected_module="it87",
                hwmon_present=False,
                recommendation=SuperIoRecommendation(
                    module="it87", in_mainline=False, load_hint="install it87-dkms-git"
                ),
            )
        ],
    )


class _MockClient:
    """Exposes superio_detect() + a truthy socket_path so the page spins its
    worker thread; injected into that worker to run the off-thread fetch path."""

    def __init__(self, report: SuperIoReport | None = None, error: Exception | None = None):
        self._report = report if report is not None else _report()
        self._error = error
        self.calls = 0
        self.socket_path = "/tmp/control-ofc-superio-test.sock"

    def superio_detect(self) -> SuperIoReport:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._report


def _page(qtbot, client=None) -> DiagnosticsPage:
    page = DiagnosticsPage(state=_state(), client=client)
    qtbot.addWidget(page)
    return page


def test_superio_tab_auto_fetches_once_and_renders(qtbot):
    client = _MockClient()
    page = _page(qtbot, client=client)
    assert page._ensure_superio_worker()
    page._superio_worker._client = client
    try:
        # Read the tab index from the page so a future reorder fails loudly here
        # rather than silently never triggering the auto-fetch.
        page._on_diag_tab_changed(page._superio_tab_index)
        qtbot.waitUntil(
            lambda: page._superio_view.findChild(QWidget, "Superio_ChipCard_it8688") is not None,
            timeout=3000,
        )
        assert client.calls == 1
        # Switching away and back must not re-fetch (guarded, synchronous).
        page._on_diag_tab_changed(0)
        page._on_diag_tab_changed(page._superio_tab_index)
        assert client.calls == 1
    finally:
        page.cleanup()


def test_superio_404_marks_unsupported_and_guards_refresh(qtbot):
    from control_ofc.api.errors import DaemonError

    client = _MockClient(error=DaemonError(code="not_found", message="unknown route", status=404))
    page = _page(qtbot, client=client)
    assert page._ensure_superio_worker()
    page._superio_worker._client = client
    try:
        page._on_diag_tab_changed(page._superio_tab_index)
        qtbot.waitUntil(lambda: page._superio_unsupported, timeout=3000)
        assert page._superio_unsupported is True
        # The view itself must show the unsupported message (not a stale
        # "Detecting…"), i.e. _on_superio_error also called set_unsupported().
        status = page._superio_view.findChild(QLabel, "Superio_Label_status")
        assert "predates this feature" in status.text()
        # A subsequent Refresh must short-circuit — no second daemon call.
        page._fetch_superio()
        assert client.calls == 1
    finally:
        page.cleanup()


def test_superio_timeout_shows_error_in_view(qtbot):
    from control_ofc.api.errors import DaemonTimeout

    client = _MockClient(error=DaemonTimeout("slow"))
    page = _page(qtbot, client=client)
    assert page._ensure_superio_worker()
    page._superio_worker._client = client
    try:
        page._on_diag_tab_changed(page._superio_tab_index)
        status = page._superio_view.findChild(QLabel, "Superio_Label_status")
        qtbot.waitUntil(lambda: "timed out" in status.text(), timeout=3000)
        # A non-404 failure must NOT flip the unsupported flag (that hides the
        # panel permanently) — it is a transient error.
        assert page._superio_unsupported is False
    finally:
        page.cleanup()


def test_fetch_superio_without_client_shows_error(qtbot):
    page = _page(qtbot, client=None)
    page._fetch_superio()
    status = page._superio_view.findChild(QLabel, "Superio_Label_status")
    assert "no daemon connection" in status.text()
