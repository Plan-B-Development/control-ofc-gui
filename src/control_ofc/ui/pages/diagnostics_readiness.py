"""PWM-reclaim severity helpers (extracted from diagnostics_page.py, Cluster C).

The ``populate_hw_diagnostics`` routine that turned a ``HardwareDiagnosticsResult``
into the Diagnostics Troubleshooting tab's widgets was removed with that page in
DEC-216; the reclaim severity helpers below live on because
``services.system_state_view`` imports them for the live System State rendering.
"""

from __future__ import annotations

from html import escape

from control_ofc.ui.theme import active_theme

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


def reclaim_severity_color(severity: str) -> str:
    """Return the theme hex colour for a reclaim severity bucket.

    Mirrors ``SuccessChip`` / ``WarningChip`` / ``CriticalChip`` so the per-row
    colours line up with the rest of the diagnostics UI even when this widget
    is rendered in rich-text mode (which doesn't pick up Qt CSS class styling).

    Reads from :func:`active_theme` on every call so a theme switch picks up
    the new status colours on the next render — pre-DEC-109 this was pinned
    to a module-level Default Dark snapshot.
    """
    theme = active_theme()
    if severity == RECLAIM_SEVERITY_OK:
        return theme.status_ok
    if severity == RECLAIM_SEVERITY_HIGH:
        return theme.status_crit
    return theme.status_warn


def render_reclaim_rows(reverts: dict[str, int] | None) -> str | None:
    """Render the per-header reclaim count card body as rich-text HTML.

    Returns ``None`` when there is nothing to surface (no payload, or every
    header reports zero reclaims) so the caller can hide the card entirely.
    Returns a non-empty HTML string otherwise — each header on its own row,
    coloured by ``classify_reclaim_severity``.

    The ``None``-tolerant signature is deliberate: older daemons (pre-1.3.x)
    don't include ``enable_revert_counts`` in the diagnostics payload, and the
    GUI must not crash when the key is absent.
    """
    if not reverts:
        return None
    # Hide the card if every header is at zero — the daemon won't normally
    # emit such a payload, but defending against it keeps the UI quiet when
    # a future daemon decides to surface healthy headers in the same map.
    if not any(count > 0 for count in reverts.values()):
        return None

    rows: list[str] = []
    for header_id in sorted(reverts):
        count = reverts[header_id]
        severity = classify_reclaim_severity(count)
        color = reclaim_severity_color(severity)
        # ``severity`` is a fixed enum string so it is safe to format raw;
        # header_id and count come from the daemon JSON and are escaped so
        # quirky chip names (e.g. "it87.2624") never break the markup.
        rows.append(
            f'<span style="color: {color};">'
            f"<b>{escape(header_id)}</b>: {count} revert(s) "
            f"[{severity.upper()}]"
            "</span>"
        )
    return "<br>".join(rows)
