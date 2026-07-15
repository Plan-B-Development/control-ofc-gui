"""Advisory-ordering pure test (formerly the Diagnostics ▸ Troubleshooting tab).

DEC-124's flattened Hardware-Readiness widget tests were retired when the
``/diagnostics/hardware`` rendering moved to the live System State page over a
Qt-free view-model. That rendering — the verdict/issue checklist, vendor
advisories, chip/module registry, BIOS-interference gauge, and the
verify/GPU/safety blocks — is now covered by ``tests/test_system_state_view.py``
(``build_issue_cards`` / ``build_registry_rows`` / ``build_interference_vm`` /
``build_safety_gpu_vm``) and ``tests/test_system_state_page.py``.

The one page-independent assertion those files do not pin is that
``advisory_rows()`` returns vendor advisories most-severe-first, so it is kept
here as a pure test.
"""

from __future__ import annotations

from control_ofc.api.models import (
    BoardInfo,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
)
from control_ofc.ui.hwmon_guidance import severity_display
from control_ofc.ui.widgets.readiness_report import advisory_rows


def _diag_gigabyte_it8696() -> HardwareDiagnosticsResult:
    """A Gigabyte IT8696E board matches both a HIGH SmartFan quirk and a MEDIUM
    IT8883 quirk — so the returned advisories must come back HIGH-first."""
    return HardwareDiagnosticsResult(
        board=BoardInfo(vendor="Gigabyte Technology Co., Ltd.", name="X870E AORUS MASTER"),
        hwmon=HwmonDiagnostics(
            chips_detected=[
                HwmonChipInfo(chip_name="it8696", expected_driver="it87", header_count=5)
            ],
            total_headers=5,
            writable_headers=5,
        ),
    )


def test_advisories_sorted_most_severe_first():
    diag = _diag_gigabyte_it8696()
    ranks = [severity_display(q.severity).rank for q in advisory_rows(diag)]
    assert ranks == sorted(ranks, reverse=True)
