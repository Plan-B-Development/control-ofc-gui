"""Qt-free view-models for the alert surfaces (DEC-282).

Mirrors ``services/logs_view.py``: all display derivation lives here so it is unit
testable without a ``QApplication``, and the widgets stay thin renderers.

``next_action_for_warning`` lives here rather than inside a widget. It used to sit in
``ui/widgets/warnings_view.py``, which was fine while exactly one surface rendered
alerts; the compact status bar and the Alert Centre are a second and third, and a rule
that lives inside one consumer is a rule the other consumers cannot follow.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from control_ofc.services.alerts import AlertOccurrence, AlertState

# Level → the pill state vocabulary in ``components/badges.py``. The alert model has
# no CRITICAL level of its own — ``error`` is the ceiling, and it already renders
# through the ``crit`` state, so the status bar can honestly say "critical" without
# inventing a severity the rest of the application does not have.
_LEVEL_STATE: dict[str, str] = {"info": "info", "warning": "warn", "error": "crit"}

# Glyph per severity, so a row never leans on colour alone (WCAG 1.4.1).
_LEVEL_GLYPH: dict[str, str] = {"info": "ⓘ", "warning": "⚠", "error": "✖"}


def next_action_for_warning(warning: dict) -> str | None:
    """Suggested next step for an alert row, or ``None``.

    Pure + unit-tested. Keyed on the ``_key`` prefix first (the most specific signal —
    a stall and a stale fan share ``source == "fan"``), then on ``source``. The
    taxonomy is the bounded set produced by ``AppState._current_conditions`` and the
    ``add_warning`` callers; anything outside it returns ``None`` rather than inventing
    remediation advice the application has no basis for.
    """
    key = warning.get("_key", "") or ""
    source = warning.get("source", "") or ""
    if key.startswith("fan_stall"):
        return (
            "Check the fan is spinning and properly connected — 0 RPM while a PWM is "
            "commanded usually means a stalled or unplugged fan."
        )
    if key.startswith("sensor_stale") or source == "sensor":
        return (
            "Sensor data is stale. Check the daemon connection, then the Hardware page — "
            "its readiness report names the driver this sensor needs."
        )
    if key.startswith("fan_stale") or source == "fan":
        return "Fan telemetry is stale. Check the fan/header connection and the daemon status."
    if source == "api":
        return "Align your control-ofc-daemon and control-ofc-gui package versions."
    return None


def level_state(level: str) -> str:
    return _LEVEL_STATE.get(level, "neutral")


def level_glyph(level: str) -> str:
    return _LEVEL_GLYPH.get(level, "⚠")


@dataclass(frozen=True)
class AlertStatusVM:
    """What the compact bar above the log table shows.

    ``headline`` is empty when nothing is active — the bar then collapses to a single
    reassuring line rather than reserving space for an empty panel.
    """

    critical_count: int
    warning_count: int
    headline: str
    recent_note: str

    @property
    def has_active(self) -> bool:
        return bool(self.critical_count or self.warning_count)

    @property
    def summary(self) -> str:
        """ "✖ 1 critical   ⚠ 2 warnings", or the all-clear."""
        if not self.has_active:
            return "✓  No active alerts"
        parts = []
        if self.critical_count:
            parts.append(f"✖ {self.critical_count} critical")
        if self.warning_count:
            noun = "warning" if self.warning_count == 1 else "warnings"
            parts.append(f"⚠ {self.warning_count} {noun}")
        return "   ".join(parts)

    @property
    def state(self) -> str:
        if self.critical_count:
            return "crit"
        if self.warning_count:
            return "warn"
        return "ok"


def build_status_vm(
    present: list[AlertOccurrence], recovered: list[AlertOccurrence]
) -> AlertStatusVM:
    """Summarise the ledger for the compact bar.

    Counts only occurrences whose condition is genuinely present — a recovered alert
    is reported through ``recent_note`` instead, so the bar does not grow loud about
    something that has already fixed itself.
    """
    crit = sum(1 for o in present if o.level == "error")
    warn = sum(1 for o in present if o.level != "error")
    headline = present[0].detail if present else ""

    note = ""
    if not present and recovered:
        newest = recovered[0]
        when = time.strftime("%H:%M:%S", time.localtime(newest.recovered_at or 0))
        note = f"Recent alert: {newest.title} recovered at {when}"
    return AlertStatusVM(
        critical_count=crit, warning_count=warn, headline=headline, recent_note=note
    )


@dataclass(frozen=True)
class AlertCardVM:
    """One expanded alert in the Alert Centre."""

    key: str
    state_label: str
    level: str
    pill_state: str
    glyph: str
    title: str
    detail: str
    first_detected: str
    last_detected: str
    source: str
    component: str
    suggested_action: str | None
    acknowledged: bool
    is_present: bool


_STATE_LABEL: dict[AlertState, str] = {
    AlertState.ACTIVE: "ACTIVE",
    AlertState.ACKNOWLEDGED: "ACKNOWLEDGED",
    AlertState.RECOVERED_UNSEEN: "RECOVERED",
    AlertState.RECOVERED: "RECOVERED",
}


def _fmt(ts: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "—"


def build_alert_card(occ: AlertOccurrence) -> AlertCardVM:
    row = {"_key": occ.key, "source": occ.source}
    return AlertCardVM(
        key=occ.key,
        state_label=_STATE_LABEL.get(occ.state, "ACTIVE"),
        level=occ.level,
        # An acknowledged-but-present alert is deliberately de-emphasised to neutral:
        # it still counts toward health, but it is no longer shouting.
        pill_state="neutral" if occ.acknowledged and occ.is_present else level_state(occ.level),
        glyph=level_glyph(occ.level),
        title=occ.title,
        detail=occ.detail,
        first_detected=_fmt(occ.activation_epoch),
        last_detected=_fmt(occ.recovered_at or occ.last_detected),
        source=occ.source or "—",
        component=occ.component or "—",
        suggested_action=next_action_for_warning(row),
        acknowledged=occ.acknowledged,
        is_present=occ.is_present,
    )


def build_active_cards(present: list[AlertOccurrence]) -> list[AlertCardVM]:
    return [build_alert_card(o) for o in present]


@dataclass(frozen=True)
class RecoveredRowVM:
    """One compact line in the Alert Centre's recovered section."""

    key: str
    title: str
    recovered_at: str
    acknowledged: bool

    @property
    def text(self) -> str:
        return f"✓  RECOVERED   {self.title}"


def build_recovered_rows(recovered: list[AlertOccurrence]) -> list[RecoveredRowVM]:
    return [
        RecoveredRowVM(
            key=o.key,
            title=o.title,
            recovered_at=_fmt(o.recovered_at),
            acknowledged=o.acknowledged,
        )
        for o in recovered
    ]
