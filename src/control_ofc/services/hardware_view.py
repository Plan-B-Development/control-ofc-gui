"""Qt-free view-model layer for the Hardware page (DEC-212).

Pure builders that turn a ``HardwareReadiness`` / ``SuperIoReport`` into frozen
``…VM`` dataclasses the thin ``HardwarePage`` renderer consumes. Reuses the
existing pure mapping ``ui/cooling_readiness.py`` (``build_readiness_items`` /
``group_for`` / ``GROUP_ORDER``) so the action targets + doc links come for free,
and mirrors the ``CoolingReadinessView`` derivations (Super-I/O panel, verdict,
scanned-age) as data. Like ``system_state_view``, the imports pull PySide6
transitively but nothing here needs a ``QApplication`` — headless-testable.

Honesty (master.txt "do not invent"): there is NO numeric readiness score — the
summary is the daemon verdict + real tier counts; the Super-I/O rows carry only
real per-chip fields (no fabricated per-channel/address/poll-age columns).
"""

from __future__ import annotations

from dataclasses import dataclass

from control_ofc.api.models import HardwareReadiness, SuperIoReport
from control_ofc.ui.cooling_readiness import GROUP_ORDER, build_readiness_items, group_for
from control_ofc.ui.readiness_merge import MergedReadinessItem
from control_ofc.ui.widgets.inventory_readiness_view import _AUTO_EXPAND_RANK, _severity_chip

# Daemon severity → StatusPill state / checklist badge word / verdict.
_STATE: dict[str, str] = {"ok": "ok", "info": "info", "warning": "warn", "critical": "crit"}
_BADGE: dict[str, str] = {"ok": "PASS", "info": "INFO", "warning": "WARN", "critical": "FAIL"}
_VERDICT: dict[str, tuple[str, str]] = {
    "ok": ("READY", "ok"),
    "info": ("READY", "ok"),
    "warning": ("NEEDS ATTENTION", "warn"),
    "critical": ("NEEDS ATTENTION", "crit"),
}
_CONFIDENCE_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1, "unknown": 0}

# Copied from cooling_readiness_view.py (the old view is untouched this stage).
_LIABILITY = (
    "Kernel-module and hardware-access changes can affect system stability. Review "
    "the guidance for your hardware before proceeding. Control-OFC does not apply "
    "these changes automatically."
)


def _norm(severity: str) -> str:
    s = (severity or "info").lower()
    return "warning" if s == "warn" else s


def _format_age(ms: int) -> str:
    """A short human 'last scanned' phrase from a scan age in ms (copy of the view)."""
    if ms <= 0:
        return "just now"
    secs = ms // 1000
    if secs < 5:
        return "just now"
    if secs < 90:
        return f"{secs}s ago"
    return f"{secs // 60}m ago"


# ─── View-model dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class SummarySegmentVM:
    label: str
    count: int
    state: str


@dataclass(frozen=True)
class ReadinessSummaryVM:
    verdict_word: str
    verdict_state: str
    pass_count: int
    warn_count: int
    crit_count: int
    info_count: int
    to_fix: int
    top_summary_line: str
    scanned_age_line: str
    segments: tuple[SummarySegmentVM, ...]


@dataclass(frozen=True)
class CheckRowVM:
    code: str
    title: str
    detail: str
    severity_state: str
    badge_word: str
    glyph: str
    expandable: bool


@dataclass(frozen=True)
class CheckGroupVM:
    name: str
    rows: tuple[CheckRowVM, ...]


@dataclass(frozen=True)
class ImpactChipVM:
    label: str
    state: str


@dataclass(frozen=True)
class RecommendedActionVM:
    code: str
    headline: str
    component: str
    plain_detail: str
    severity_state: str
    badge_word: str
    glyph: str
    impact_chips: tuple[ImpactChipVM, ...]
    action_kind: str
    action_label: str
    action_target: str
    doc_url: str
    doc_title: str
    auto_expand: bool


@dataclass(frozen=True)
class SuperIoRowVM:
    chip: str
    vendor: str
    driver_text: str
    module_text: str
    confidence: str
    health_word: str
    health_state: str
    notes: str
    has_recommendation: bool
    reason: str
    copy_command: str
    mainline_text: str
    mainline_state: str
    risk_notes: tuple[str, ...]
    caveats: tuple[str, ...]
    #: How the daemon knows this chip is here, in its own words (`WIRE-w`).
    #: Empty when the daemon sent none.
    evidence_text: str = ""
    #: The raw tokens, for callers that want to branch rather than render.
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class SuperIoPanelVM:
    arch_supported: bool
    arch_note: str
    has_chips: bool
    empty_note: str
    summary_text: str
    summary_state: str
    rows: tuple[SuperIoRowVM, ...]
    show_liability: bool
    liability_text: str
    notes_text: str
    probe_available: bool
    probe_reason: str


# ─── Builders ──────────────────────────────────────────────────────────────


def build_readiness_summary(hw: HardwareReadiness) -> ReadinessSummaryVM:
    items = build_readiness_items(hw)
    pass_count = sum(1 for m in items if m.is_ok)
    warn_count = sum(1 for m in items if _norm(m.severity) == "warning")
    crit_count = sum(1 for m in items if _norm(m.severity) == "critical")
    info_count = sum(1 for m in items if _norm(m.severity) == "info")
    overall = _norm(hw.overall)
    verdict_word, verdict_state = _VERDICT.get(overall, ("NEEDS ATTENTION", "warn"))

    top = (hw.rollup.top_summary or "").strip()
    top_summary_line = f"Most important next step: {top}" if (top and overall != "ok") else ""
    scanned_age_line = (
        f"Last scanned {_format_age(hw.scanned_age_ms)}. "
        "This assessment is read-only and does not change the system."
    )
    # Segments: PASS + WARN always; CRIT / INFO only when > 0 (criticals are
    # never hidden inside WARN).
    segments = [
        SummarySegmentVM("PASS", pass_count, "ok"),
        SummarySegmentVM("WARN", warn_count, "warn"),
    ]
    if crit_count:
        segments.append(SummarySegmentVM("CRIT", crit_count, "crit"))
    if info_count:
        segments.append(SummarySegmentVM("INFO", info_count, "info"))

    return ReadinessSummaryVM(
        verdict_word=verdict_word,
        verdict_state=verdict_state,
        pass_count=pass_count,
        warn_count=warn_count,
        crit_count=crit_count,
        info_count=info_count,
        to_fix=warn_count + crit_count,
        top_summary_line=top_summary_line,
        scanned_age_line=scanned_age_line,
        segments=tuple(segments),
    )


def _check_row(m: MergedReadinessItem) -> CheckRowVM:
    sev = _norm(m.severity)
    _word, glyph, _css, _rank = _severity_chip(m.severity)
    return CheckRowVM(
        code=m.code,
        title=m.headline,
        detail=m.plain_detail,
        severity_state=_STATE.get(sev, "neutral"),
        badge_word=_BADGE.get(sev, sev.upper()),
        glyph=glyph,
        expandable=bool(m.plain_detail),
    )


def build_checklist(items: list[MergedReadinessItem]) -> list[CheckGroupVM]:
    """Group the daemon's actual items (incl. ok/PASS) into GROUP_ORDER. The input
    is already most-severe-first, so within-group order is preserved."""
    buckets: dict[str, list[MergedReadinessItem]] = {name: [] for name in GROUP_ORDER}
    for m in items:
        buckets.setdefault(group_for(m.code), []).append(m)
    groups: list[CheckGroupVM] = []
    for name in GROUP_ORDER:
        rows = buckets.get(name, [])
        if rows:
            groups.append(CheckGroupVM(name, tuple(_check_row(m) for m in rows)))
    return groups


def build_recommended_actions(items: list[MergedReadinessItem]) -> list[RecommendedActionVM]:
    return [_action_vm(m) for m in items if not m.is_ok]


def _action_vm(m: MergedReadinessItem) -> RecommendedActionVM:
    sev = _norm(m.severity)
    word, glyph, _css, rank = _severity_chip(m.severity)
    chips: list[ImpactChipVM] = []
    if m.affects_safety:
        chips.append(ImpactChipVM("affects thermal safety", "crit"))
    if m.blocks_control:
        chips.append(ImpactChipVM("blocks fan control", "crit"))
    if m.blocks_monitoring:
        chips.append(ImpactChipVM("blocks monitoring", "warn"))
    if m.reboot_may_be_required:
        chips.append(ImpactChipVM("reboot may be required", "info"))
    return RecommendedActionVM(
        code=m.code,
        headline=m.headline,
        component=(m.component or "").upper(),
        plain_detail=m.plain_detail,
        severity_state=_STATE.get(sev, "neutral"),
        badge_word=word,
        glyph=glyph,
        impact_chips=tuple(chips),
        action_kind=m.action.kind,
        action_label=m.action.label,
        action_target=m.action.target,
        doc_url=m.doc_url,
        doc_title=m.doc_title,
        auto_expand=rank >= _AUTO_EXPAND_RANK,
    )


def _notes_text(report: SuperIoReport) -> str:
    notes = list(report.notes)
    if report.acpi_conflict_drivers:
        notes.insert(
            0,
            "ACPI firmware claims the I/O ports of: "
            + ", ".join(report.acpi_conflict_drivers)
            + " — under acpi_enforce_resources=strict (the default) the driver may "
            "refuse to bind.",
        )
    return "\n\n".join(notes)


def _superio_row(c) -> SuperIoRowVM:
    vendor = (c.vendor or "").strip()
    vendor_text = vendor.upper() if vendor and vendor != "unknown" else ""
    rec = c.recommendation
    mainline_text = ""
    mainline_state = "neutral"
    risk: tuple[str, ...] = ()
    if rec is not None:
        mainline_text = (
            "in mainline kernel" if rec.in_mainline else "needs out-of-tree (DKMS) driver"
        )
        mainline_state = "ok" if rec.in_mainline else "warn"
        risk = tuple(rec.risk_notes)
    return SuperIoRowVM(
        evidence_text=_evidence_text(c.evidence),
        evidence=tuple(c.evidence),
        chip=c.chip_name or "(unknown chip)",
        vendor=vendor_text,
        driver_text=c.bound_driver or c.expected_module or "—",
        module_text="Yes" if c.module_loaded else "No",
        confidence=(c.confidence or "").lower() or "—",
        health_word="HEALTHY" if c.hwmon_present else "UNBOUND",
        health_state="ok" if c.hwmon_present else "warn",
        notes="; ".join(c.caveats) if c.caveats else "",
        has_recommendation=rec is not None,
        reason=rec.reason if rec else "",
        copy_command=rec.load_hint if rec else "",
        mainline_text=mainline_text,
        mainline_state=mainline_state,
        risk_notes=risk,
        caveats=tuple(c.caveats),
    )


#: `SuperIoChip.evidence` tokens in the daemon's discovery order, mapped to
#: words a user can act on. Deliberately a *display* map and not a filter: an
#: unrecognised token is rendered verbatim rather than dropped (the 273-i rule),
#: because the daemon may add an evidence source before the GUI knows about it
#: and "we found this chip somehow" is still worth more than silence.
_EVIDENCE_LABELS = {
    "dmi_board_table": "board table",
    "kernel_log": "kernel log",
    "bound_hwmon": "bound driver",
    "port_probe": "port probe",
}


def _evidence_text(tokens: list[str]) -> str:
    """Render `SuperIoChip.evidence` for display (`WIRE-w`).

    The field was parsed and never shown, so after a user ran the opt-in
    `/dev/port` probe nothing on screen said the newly-found chip came *from*
    the probe — which is the whole reason the probe is worth running, and the
    one evidence source the user themselves caused.
    """
    return ", ".join(_EVIDENCE_LABELS.get(tok, tok) for tok in tokens if tok)


def build_superio_panel(report: SuperIoReport) -> SuperIoPanelVM:
    if not report.arch_supported:
        return SuperIoPanelVM(
            arch_supported=False,
            arch_note="Super-I/O detection is only available on x86 / x86_64 systems.",
            has_chips=False,
            empty_note="",
            summary_text="",
            summary_state="neutral",
            rows=(),
            show_liability=False,
            liability_text="",
            notes_text="",
            probe_available=report.port_probe_available,
            probe_reason=report.port_probe_reason,
        )
    chips = sorted(
        report.chips,
        key=lambda c: (
            0 if c.recommendation is not None else 1,
            -_CONFIDENCE_RANK.get((c.confidence or "").lower(), 0),
            c.chip_name,
        ),
    )
    if not chips:
        return SuperIoPanelVM(
            arch_supported=True,
            arch_note="",
            has_chips=False,
            empty_note=(
                "No motherboard Super-I/O chip was detected. On a system whose Super-I/O "
                "driver is not loaded, the chip is not visible until it is."
            ),
            summary_text="",
            summary_state="neutral",
            rows=(),
            show_liability=False,
            liability_text="",
            notes_text=_notes_text(report),
            probe_available=report.port_probe_available,
            probe_reason=report.port_probe_reason,
        )
    unbound = [c for c in chips if c.recommendation is not None]
    summary_text = (
        f"{len(unbound)} of {len(chips)} Super-I/O chip(s) need a driver loaded"
        if unbound
        else f"All {len(chips)} detected Super-I/O chip(s) have a driver bound"
    )
    return SuperIoPanelVM(
        arch_supported=True,
        arch_note="",
        has_chips=True,
        empty_note="",
        summary_text=summary_text,
        summary_state="warn" if unbound else "ok",
        rows=tuple(_superio_row(c) for c in chips),
        show_liability=bool(unbound),
        liability_text=_LIABILITY if unbound else "",
        notes_text=_notes_text(report),
        probe_available=report.port_probe_available,
        probe_reason=report.port_probe_reason,
    )
