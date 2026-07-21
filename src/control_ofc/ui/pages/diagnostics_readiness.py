"""PWM-reclaim severity helpers (extracted from diagnostics_page.py, Cluster C).

The ``populate_hw_diagnostics`` routine that turned a ``HardwareDiagnosticsResult``
into the Diagnostics Troubleshooting tab's widgets was removed with that page in
DEC-216; the reclaim severity helpers below live on because
``services.system_state_view`` imports them for the live System State rendering.
"""

from __future__ import annotations

# Severity buckets for the per-header pwm_enable reclaim count surfaced from
# ``HardwareDiagnosticsResult.hwmon.enable_revert_counts``. Tuned to match the
# operator's mental model on AORUS-class boards: zero events means the daemon
# watchdog has nothing to do, occasional reverts mean BIOS interference is
# recoverable, and ≥10 events on a single header indicates a continuous
# tug-of-war between Linux and the EC firmware that BIOS configuration should
# resolve.
RECLAIM_SEVERITY_OK = "ok"
RECLAIM_SEVERITY_WARN = "warn"
RECLAIM_SEVERITY_HIGH = "high"


def classify_reclaim_severity(count: int) -> str:
    """Return the severity bucket for a pwm_enable reclaim count.

    Buckets:
      - ``"ok"``    → ``count <= 0`` (header is healthy, no BIOS interference).
      - ``"warn"``  → ``1 <= count < 10`` (occasional reclaim — daemon is
        recovering but the operator may want to check BIOS Smart Fan settings).
      - ``"high"``  → ``count >= 10`` (continuous reclaim — BIOS is fighting
        the daemon; recommend disabling Smart Fan or using a degenerate curve).

    Negative counts are treated as ``ok`` so callers do not have to defend
    against malformed daemon payloads. The buckets are deliberately coarse so
    the operator's eye is drawn to the *hot* header, not to small fluctuations.
    """
    if count <= 0:
        return RECLAIM_SEVERITY_OK
    if count < 10:
        return RECLAIM_SEVERITY_WARN
    return RECLAIM_SEVERITY_HIGH
