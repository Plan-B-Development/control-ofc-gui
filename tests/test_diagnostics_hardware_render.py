"""Tests for the Diagnostics → Hardware reclaim-count pure helpers.

Covers the GUI side of the motherboard PWM investigation (Batch C):

* ``classify_reclaim_severity`` returns the right bucket for K∈{0, 1, 5, 50}.
* forward-compat: parsing tolerates older daemons (pre-1.3.x) without
  ``enable_revert_counts`` in ``/diagnostics/hardware``.
  (``render_reclaim_rows`` + its colour helper were removed unused in the
  2026-07-21 audit sweep with the retired Troubleshooting rendering.)

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
