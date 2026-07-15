"""Tests for the Diagnostics → Hardware reclaim-count pure helpers.

Covers the GUI side of the motherboard PWM investigation (Batch C):

* ``classify_reclaim_severity`` returns the right bucket for K∈{0, 1, 5, 50}.
* ``render_reclaim_rows`` produces colour-coded rich-text rows and tolerates
  ``None``/empty payloads from older daemons (pre-1.3.x without
  ``enable_revert_counts`` in ``/diagnostics/hardware``).

The reclaim helpers now live in ``diagnostics_readiness`` (Cluster C split). The
widget-layer assertions that drove ``DiagnosticsPage._populate_hw_diagnostics``
(the reclaim card headline/colour, the Gigabyte+IT8696E VendorQuirk auto-show,
and the async hw-diag fetch) moved to the retirement's successors:
``test_system_state_view.py`` (``build_interference_vm`` / ``build_issue_cards``)
and ``test_system_state_page.py`` (worker lifecycle + fetch).
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import parse_hardware_diagnostics
from control_ofc.ui.pages.diagnostics_readiness import (
    RECLAIM_SEVERITY_HIGH,
    RECLAIM_SEVERITY_OK,
    RECLAIM_SEVERITY_WARN,
    classify_reclaim_severity,
    reclaim_severity_color,
    render_reclaim_rows,
)

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
    key on the parsing side."""

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
