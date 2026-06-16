"""Tests for the Diagnostics → Hardware reclaim-count surfacing.

Covers the GUI side of the motherboard PWM investigation (Batch C):

* ``classify_reclaim_severity`` returns the right bucket for K∈{0, 1, 5, 50}.
* ``render_reclaim_rows`` produces colour-coded rich-text rows and tolerates
  ``None``/empty payloads from older daemons (pre-1.3.x without
  ``enable_revert_counts`` in ``/diagnostics/hardware``).
* ``DiagnosticsPage._populate_hw_diagnostics`` surfaces the reclaim card with
  a severity-coloured headline, populates the per-row body, and auto-shows
  the matching ``VendorQuirk`` card for the Gigabyte + IT8696E combination
  exercised by the original investigation report.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QLabel

from control_ofc.api.models import (
    BoardInfo,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    ThermalSafetyInfo,
    parse_hardware_diagnostics,
)
from control_ofc.ui.pages.diagnostics_page import (
    RECLAIM_SEVERITY_HIGH,
    RECLAIM_SEVERITY_OK,
    RECLAIM_SEVERITY_WARN,
    DiagnosticsPage,
    classify_reclaim_severity,
    reclaim_severity_color,
    render_reclaim_rows,
)
from control_ofc.ui.widgets.readiness_report import advisory_rows


@pytest.fixture()
def app(qapp):
    return qapp


# ---------------------------------------------------------------------------
# Pure helpers — no Qt instantiation, exercise the classifier directly.
# ---------------------------------------------------------------------------


class TestClassifyReclaimSeverity:
    """The classifier is the load-bearing decision for the colour ramp:
    every UI surface (headline, per-row HTML, tooltips) reads from it, so it
    needs to be tested independently of the Qt rendering path."""

    @pytest.mark.parametrize(
        "count, expected",
        [
            (0, RECLAIM_SEVERITY_OK),
            (1, RECLAIM_SEVERITY_WARN),
            (5, RECLAIM_SEVERITY_WARN),
            (9, RECLAIM_SEVERITY_WARN),
            (10, RECLAIM_SEVERITY_HIGH),
            (50, RECLAIM_SEVERITY_HIGH),
            (10_000, RECLAIM_SEVERITY_HIGH),
        ],
    )
    def test_buckets(self, count: int, expected: str) -> None:
        assert classify_reclaim_severity(count) == expected

    def test_negative_count_treated_as_ok(self) -> None:
        # Defensive: a malformed daemon payload should not produce a UI crash
        # or a misleading "high severity" badge for a meaningless number.
        assert classify_reclaim_severity(-1) == RECLAIM_SEVERITY_OK

    def test_color_per_bucket_is_distinct(self) -> None:
        # Operators rely on the colour to spot the hot header across many —
        # the three buckets must therefore actually map to three different
        # colours (not silently fall back to a single theme token).
        ok = reclaim_severity_color(RECLAIM_SEVERITY_OK)
        warn = reclaim_severity_color(RECLAIM_SEVERITY_WARN)
        high = reclaim_severity_color(RECLAIM_SEVERITY_HIGH)
        assert {ok, warn, high} == {ok, warn, high}
        assert len({ok, warn, high}) == 3


class TestRenderReclaimRows:
    """``render_reclaim_rows`` is the seam between the daemon payload shape
    and the Qt rich-text rendering — keeping it pure means the contract can
    be locked down without standing up a QWidget."""

    def test_none_payload_returns_none(self) -> None:
        assert render_reclaim_rows(None) is None

    def test_empty_payload_returns_none(self) -> None:
        assert render_reclaim_rows({}) is None

    def test_all_zero_payload_returns_none(self) -> None:
        # If every header is at zero we hide the card entirely — there is
        # nothing to surface and the operator should not see a "BIOS
        # interference detected" headline that contradicts the data.
        assert render_reclaim_rows({"h1": 0, "h2": 0}) is None

    def test_single_warn_row_uses_warn_color(self) -> None:
        warn_color = reclaim_severity_color(RECLAIM_SEVERITY_WARN)
        html = render_reclaim_rows({"h1": 5})
        assert html is not None
        assert warn_color in html
        assert "h1" in html
        assert "5 revert(s)" in html
        assert RECLAIM_SEVERITY_WARN.upper() in html

    def test_single_high_row_uses_high_color(self) -> None:
        high_color = reclaim_severity_color(RECLAIM_SEVERITY_HIGH)
        html = render_reclaim_rows({"h1": 50})
        assert html is not None
        assert high_color in html
        assert RECLAIM_SEVERITY_HIGH.upper() in html

    def test_mixed_headers_use_per_row_colors(self) -> None:
        warn_color = reclaim_severity_color(RECLAIM_SEVERITY_WARN)
        high_color = reclaim_severity_color(RECLAIM_SEVERITY_HIGH)
        html = render_reclaim_rows({"warn_hdr": 1, "hot_hdr": 50})
        assert html is not None
        assert warn_color in html
        assert high_color in html
        # Both headers appear in the body — neither row was dropped.
        assert "warn_hdr" in html
        assert "hot_hdr" in html

    def test_explicit_zero_among_active_renders_as_ok(self) -> None:
        # When at least one header has reverts, the card is shown — and any
        # zero-count peer should still render with the OK colour rather
        # than being silently omitted.
        ok_color = reclaim_severity_color(RECLAIM_SEVERITY_OK)
        html = render_reclaim_rows({"healthy": 0, "noisy": 5})
        assert html is not None
        assert ok_color in html
        assert "healthy" in html
        assert "noisy" in html

    def test_html_escapes_header_id(self) -> None:
        # IDs come from the daemon JSON. A maliciously-shaped (or just
        # quirky) name with HTML metacharacters must not break the markup.
        html = render_reclaim_rows({"<bad>": 5})
        assert html is not None
        assert "<bad>" not in html
        assert "&lt;bad&gt;" in html


# ---------------------------------------------------------------------------
# Forward-compat with daemons that omit ``enable_revert_counts``
# ---------------------------------------------------------------------------


class TestEnableRevertCountsForwardCompat:
    """Older daemons (pre-1.3.x) don't include ``enable_revert_counts`` in
    the ``/diagnostics/hardware`` payload. The GUI must tolerate the missing
    key on both the parsing and rendering side."""

    def test_parse_without_enable_revert_counts(self) -> None:
        payload = {
            "api_version": 1,
            "hwmon": {
                "chips_detected": [],
                "total_headers": 0,
                "writable_headers": 0,
                # NB: no ``enable_revert_counts`` key.
            },
            "thermal_safety": {
                "state": "normal",
                "cpu_sensor_found": True,
                "emergency_threshold_c": 105.0,
                "release_threshold_c": 80.0,
            },
            "kernel_modules": [],
            "acpi_conflicts": [],
        }
        result = parse_hardware_diagnostics(payload)
        # Defaults to {} so callers don't have to defend against ``None``.
        assert result.hwmon.enable_revert_counts == {}

    def test_populate_with_missing_revert_counts_hides_card(self, app) -> None:
        page = DiagnosticsPage()
        diag = HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                total_headers=1,
                writable_headers=1,
                # enable_revert_counts default = {}
            ),
            thermal_safety=ThermalSafetyInfo(state="normal"),
        )
        page._populate_hw_diagnostics(diag)
        # All three reclaim widgets stay hidden when the daemon doesn't
        # report any reverts — the card must not flash empty content.
        assert page._revert_headline_label.isHidden()
        assert page._revert_label.isHidden()
        assert page._revert_footnote_label.isHidden()


# ---------------------------------------------------------------------------
# DiagnosticsPage rendering — the parametrised K∈{0, 1, 5, 50} table that
# the investigation report explicitly called out.
# ---------------------------------------------------------------------------


def _diag_with_revert(header_id: str, count: int) -> HardwareDiagnosticsResult:
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            total_headers=1,
            writable_headers=1,
            enable_revert_counts={header_id: count},
        ),
        thermal_safety=ThermalSafetyInfo(state="normal"),
    )


class TestDiagnosticsPageReclaimRendering:
    @pytest.mark.parametrize(
        "count, expected_severity",
        [
            (0, RECLAIM_SEVERITY_OK),
            (1, RECLAIM_SEVERITY_WARN),
            (5, RECLAIM_SEVERITY_WARN),
            (50, RECLAIM_SEVERITY_HIGH),
        ],
    )
    def test_severity_class_matches_count(self, app, count: int, expected_severity: str) -> None:
        # Note: K=0 is a synthetic case — the daemon won't normally insert
        # 0-count entries — but the helper must still bucket it correctly.
        # The rendered card hides itself for an all-zero payload, which we
        # cover separately above; this case exercises the classifier in
        # isolation under the same parametrisation as the higher buckets.
        assert classify_reclaim_severity(count) == expected_severity

    @pytest.mark.parametrize("count", [1, 5, 50])
    def test_card_visible_with_per_row_color(self, app, count: int) -> None:
        page = DiagnosticsPage()
        page._populate_hw_diagnostics(_diag_with_revert("h1", count))

        assert not page._revert_headline_label.isHidden()
        assert not page._revert_label.isHidden()
        assert not page._revert_footnote_label.isHidden()

        expected_severity = classify_reclaim_severity(count)
        expected_color = reclaim_severity_color(expected_severity)
        body = page._revert_label.text()
        assert expected_color in body
        assert expected_severity.upper() in body
        assert "h1" in body
        assert f"{count} revert(s)" in body

    def test_headline_uses_max_severity(self, app) -> None:
        # When two headers are listed with different severities, the
        # headline label must take the highest — that's the one the
        # operator should react to first.
        page = DiagnosticsPage()
        diag = HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                total_headers=2,
                writable_headers=2,
                enable_revert_counts={"warn_hdr": 1, "hot_hdr": 50},
            ),
            thermal_safety=ThermalSafetyInfo(state="normal"),
        )
        page._populate_hw_diagnostics(diag)

        # The Qt class name is the rendezvous point with the stylesheet; we
        # check the property string rather than rendered pixels because
        # offscreen Qt doesn't reliably apply the polish.
        assert page._revert_headline_label.property("class") == "CriticalChip"
        headline = page._revert_headline_label.text()
        assert "highest: 50" in headline
        assert RECLAIM_SEVERITY_HIGH.upper() in headline

    def test_warn_only_headline_uses_warning_class(self, app) -> None:
        page = DiagnosticsPage()
        page._populate_hw_diagnostics(_diag_with_revert("h1", 3))
        assert page._revert_headline_label.property("class") == "WarningChip"


# ---------------------------------------------------------------------------
# Vendor quirk auto-show for the Gigabyte + IT8696E combination — the
# canonical AORUS-class symptom that motivated this batch.
# ---------------------------------------------------------------------------


class TestGigabyteIt8696QuirkAutoShow:
    def test_quirk_card_visible_for_gigabyte_it8696(self, app) -> None:
        page = DiagnosticsPage()
        diag = HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(
                        chip_name="it8696",
                        device_id="it87.2624",
                        expected_driver="it87",
                        in_mainline_kernel=False,
                        header_count=5,
                    )
                ],
                total_headers=5,
                writable_headers=5,
                enable_revert_counts={"hwmon:it8696:it87.2624:pwm1:pwm1": 65},
            ),
            board=BoardInfo(
                vendor="Gigabyte Technology Co., Ltd.",
                name="X870E AORUS MASTER",
                bios_version="F13a",
            ),
            thermal_safety=ThermalSafetyInfo(state="normal"),
        )
        page._populate_hw_diagnostics(diag)

        # DEC-158: vendor quirks now render as per-advisory rows. The container
        # is visible and there is one row per advisory; the HIGH SmartFan quirk
        # sorts first, so row 0 carries the WarningChip badge (CriticalChip is
        # reserved for the IT8689E "no software workaround" case) and a summary
        # naming the chip — keeping the test honest if the DB text is reworded.
        assert not page._advisory_container.isHidden()
        assert len(page._advisory_rows) == len(advisory_rows(diag))
        badge0 = page.findChild(QLabel, "Diagnostics_AdvisoryBadge_0")
        assert badge0.property("class") == "WarningChip"
        assert "HIGH" in badge0.text()
        summary0 = page.findChild(QLabel, "Diagnostics_AdvisorySummary_0")
        assert "IT8696E" in summary0.text() or "it8696" in summary0.text().lower()

    def test_quirk_card_hidden_for_non_gigabyte_it8696(self, app) -> None:
        # Sanity check the matcher: the same chip on a non-Gigabyte board
        # should not auto-show this specific guidance.
        page = DiagnosticsPage()
        diag = HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(
                chips_detected=[
                    HwmonChipInfo(
                        chip_name="it8696",
                        device_id="it87.2624",
                        expected_driver="it87",
                        in_mainline_kernel=False,
                        header_count=5,
                    )
                ],
                total_headers=5,
                writable_headers=5,
            ),
            board=BoardInfo(vendor="Some Other Vendor", name="X870E"),
            thermal_safety=ThermalSafetyInfo(state="normal"),
        )
        page._populate_hw_diagnostics(diag)
        assert advisory_rows(diag) == []
        assert page._advisory_container.isHidden()
        assert page._advisory_rows == []


class TestHwDiagAsyncFetch:
    """Finding D: the hardware-diagnostics fetch runs on a worker thread so the
    blocking GET never freezes the UI, with a synchronous fallback that
    preserves behaviour when no socket path is available (mock-client tests)."""

    def _diag(self) -> HardwareDiagnosticsResult:
        return HardwareDiagnosticsResult(
            hwmon=HwmonDiagnostics(total_headers=1, writable_headers=1),
            thermal_safety=ThermalSafetyInfo(state="normal"),
        )

    def test_on_hw_diag_ok_applies_result(self, app) -> None:
        page = DiagnosticsPage()
        diag = self._diag()
        page._on_hw_diag_ok(diag)
        assert page._diag.last_hw_diagnostics is diag
        assert "refreshed" in page._status_label.text().lower()

    def test_on_hw_diag_error_unavailable_sets_summary(self, app) -> None:
        page = DiagnosticsPage()
        page._on_hw_diag_error("unavailable", "Daemon unavailable — cannot fetch diagnostics")
        assert "unavailable" in page._hw_ready_summary.text().lower()

    def test_on_hw_diag_error_error_category_is_prefixed(self, app) -> None:
        page = DiagnosticsPage()
        page._on_hw_diag_error("error", "boom")
        text = page._hw_ready_summary.text().lower()
        assert "boom" in text
        assert "error" in text

    def test_fetch_uses_sync_fallback_without_socket(self, app) -> None:
        # A mock client with no socket_path must not spin up a worker thread;
        # the fetch falls back to a synchronous call and applies the result.
        page = DiagnosticsPage()
        diag = self._diag()
        client = MagicMock()
        client.socket_path = None
        client.hardware_diagnostics.return_value = diag
        page._client = client

        page._fetch_hardware_diagnostics()

        client.hardware_diagnostics.assert_called_once()
        assert page._diag.last_hw_diagnostics is diag
        assert page._hw_diag_worker is None  # no worker created on the sync path

    def test_worker_lifecycle_create_and_cleanup(self, app) -> None:
        # With a socket path the worker + thread are created and started; the
        # fetch is never triggered (do_fetch only runs on the queued signal), so
        # the thread stays idle and cleanup() stops it deterministically.
        page = DiagnosticsPage()
        client = MagicMock()
        client.socket_path = "/tmp/control-ofc-test-nonexistent.sock"
        page._client = client

        assert page._ensure_hw_diag_worker() is True
        assert page._hw_diag_worker is not None
        assert page._hw_diag_thread is not None

        page.cleanup()
        assert page._hw_diag_thread is None
        assert page._hw_diag_worker is None
