"""Pure merge of the two hardware-readiness feeds into one actionable index (DEC-206).

Fuses the daemon's structured ``GET /inventory/readiness`` items (the spine) with
the GUI-authored problems derived from ``GET /diagnostics/hardware``
(:func:`detect_readiness_problems`), deduplicated but lossless:

* daemon items form the spine — each becomes a :class:`MergedReadinessItem` with a
  code→action mapping (a deep-link or an in-surface one-click, never an inert
  string);
* a GUI problem that describes the **same condition** as a daemon item folds INTO
  that item's "Learn more" (its richer fix HTML + doc link) and is not rendered as
  a second row;
* a GUI problem with **no daemon equivalent** stays a first-class item so no
  Troubleshooting coverage is lost.

The daemon strings stay separate from the GUI-authored HTML (``plain_detail`` vs
``html_detail``) so the security boundary — daemon text is *never* interpreted as
markup — is preserved end to end. No Qt here, so this is trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from control_ofc.api.models import HardwareDiagnosticsResult, InventoryReadiness, ReadinessItem
from control_ofc.ui.widgets.readiness_report import detect_readiness_problems

# ── Action kinds — how the merged view's primary button behaves ──
ACTION_NONE = "none"  # no button (ok items, or doc-link-only via doc_url)
ACTION_IN_SURFACE = "in_surface"  # call a method on the merged tab (no navigation)
ACTION_TAB_SWITCH = "tab_switch"  # switch to a sibling Diagnostics tab
ACTION_DEEP_LINK = "deep_link"  # cross-page: main_window switches page + focuses a target

# Severity → sort rank (higher = more severe). Daemon vocabulary is
# ok|info|warning|critical; the GUI problems use "warn" (normalised below).
_SEV_RANK = {"ok": 0, "info": 1, "warning": 2, "critical": 3}


@dataclass
class ActionSpec:
    """The primary action a readiness item resolves to — a deep-link or an
    in-surface one-click. ``kind == ACTION_NONE`` means the item has no button
    (an ok item, or a doc-link-only item whose link lives in ``doc_url``)."""

    kind: str = ACTION_NONE
    label: str = ""
    # Opaque target the merged tab routes on, e.g. "pwm_verify_all" | "superio"
    # | "sensors" | "gpu_verify" | "preferred_cpu" | "preferred_mb".
    target: str = ""


@dataclass
class MergedReadinessItem:
    """One row in the merged actionable index.

    ``plain_detail`` is daemon-supplied → the view renders it as PlainText.
    ``html_detail`` is GUI-authored (folded/first-class GUI problem fix) → the
    view renders it as trusted rich text. Keeping them separate is the security
    boundary (daemon text is never treated as markup)."""

    code: str = ""
    severity: str = "info"  # ok | info | warning | critical (normalised)
    rank: int = 1
    headline: str = ""  # daemon summary or GUI label
    component: str = ""
    plain_detail: str = ""  # daemon detail (+ recommended action) — PlainText
    html_detail: str = ""  # GUI fix — trusted rich text
    doc_url: str = ""  # a "Learn more" link (GUI-authored URL, trusted)
    doc_title: str = ""
    action: ActionSpec = field(default_factory=ActionSpec)
    affects_safety: bool = False
    blocks_control: bool = False
    blocks_monitoring: bool = False
    reboot_may_be_required: bool = False
    source: str = "daemon"  # daemon | gui
    is_ok: bool = False


# ── code / key → primary action (§5.3) ────────────────────────────────────
# Daemon item code → ActionSpec. Codes not listed get no button (ok items, or
# info notes that only carry a doc link).
def _daemon_action(code: str) -> ActionSpec:
    if code in ("cpu_sensor_missing", "cpu_default_low_confidence", "selected_cpu_sensor_missing"):
        return ActionSpec(ACTION_DEEP_LINK, "Pick a CPU sensor", "preferred_cpu")
    if code == "selected_mb_sensor_missing":
        return ActionSpec(ACTION_DEEP_LINK, "Pick a motherboard sensor", "preferred_mb")
    if code in ("pwm_control_unverified", "pwm_read_only"):
        return ActionSpec(ACTION_IN_SURFACE, "Test PWM control", "pwm_verify_all")
    if code in ("no_pwm_controls", "superio_driver_unloaded", "superio_acpi_conflict"):
        return ActionSpec(ACTION_TAB_SWITCH, "Open Super-I/O guidance", "superio")
    if code in ("monitor_only_fans_present", "unknown_sensors_present", "sensors_unavailable"):
        return ActionSpec(ACTION_TAB_SWITCH, "View sensors", "sensors")
    return ActionSpec()  # ok items (cpu_sensor_present / pwm_controls_present) etc.


# GUI problem key → ActionSpec (only for GUI problems kept as first-class items).
def _gui_action(key: str) -> ActionSpec:
    if key == "all_readonly":
        return ActionSpec(ACTION_IN_SURFACE, "Test PWM control", "pwm_verify_all")
    if key in ("no_chips", "dual_chip"):
        return ActionSpec(ACTION_TAB_SWITCH, "Open Super-I/O guidance", "superio")
    if key in ("gpu_ppfeaturemask", "gpu_readonly"):
        return ActionSpec(ACTION_IN_SURFACE, "Verify GPU fan", "gpu_verify")
    # bios_revert / vendor_quirk / module_collision / module_conflict / acpi:
    # no safe one-click — the doc link (in doc_url) is the action.
    return ActionSpec()


# GUI problem key ⇒ daemon codes that, if present, absorb it (fold, don't dup §5.1).
_FOLD: dict[str, list[str]] = {
    "all_readonly": ["pwm_read_only", "no_pwm_controls"],
    "no_chips": ["no_pwm_controls", "superio_driver_unloaded"],
    "acpi": ["superio_acpi_conflict"],
    "dual_chip": ["superio_driver_unloaded"],
}


def _normalise(severity: str) -> str:
    """Normalise a severity to the daemon vocabulary. The GUI problems use
    ``"warn"``; everything else is passed through lowercased."""
    s = (severity or "info").lower()
    return "warning" if s == "warn" else s


def _from_daemon(it: ReadinessItem) -> MergedReadinessItem:
    sev = _normalise(it.severity)
    plain = it.detail or ""
    if it.recommended_action:
        plain = f"{plain}\n→ {it.recommended_action}".strip()
    return MergedReadinessItem(
        code=it.code,
        severity=sev,
        rank=_SEV_RANK.get(sev, 1),
        headline=it.summary or it.code,
        component=it.component or "",
        plain_detail=plain,
        action=_daemon_action(it.code),
        affects_safety=it.affects_safety,
        blocks_control=it.blocks_control,
        blocks_monitoring=it.blocks_monitoring,
        reboot_may_be_required=it.reboot_may_be_required,
        source="daemon",
        is_ok=(sev == "ok"),
    )


def _from_gui(p: dict) -> MergedReadinessItem:
    sev = _normalise(p.get("severity", "warn"))
    return MergedReadinessItem(
        code=p.get("key", ""),
        severity=sev,
        rank=_SEV_RANK.get(sev, 2),
        headline=p.get("label", ""),
        html_detail=p.get("fix", ""),
        doc_url=p.get("doc_url", ""),
        doc_title=p.get("doc_title", "Learn more"),
        action=_gui_action(p.get("key", "")),
        source="gui",
        is_ok=False,
    )


def _fold_gui_into(target: MergedReadinessItem, p: dict) -> None:
    """Fold a GUI problem into a matching daemon item as enrichment — its richer
    fix HTML + doc link, kept in the trusted-HTML slot so the daemon's PlainText
    detail is untouched (security boundary preserved)."""
    fix = p.get("fix", "")
    if fix:
        target.html_detail = (
            f"{target.html_detail}<br>{fix}".lstrip() if target.html_detail else fix
        )
    if not target.doc_url and p.get("doc_url"):
        target.doc_url = p["doc_url"]
        target.doc_title = p.get("doc_title", "Learn more")


def merge_readiness(
    inventory: InventoryReadiness | None,
    hw_diag: HardwareDiagnosticsResult | None,
) -> list[MergedReadinessItem]:
    """Fuse the daemon readiness items + GUI ``/diagnostics/hardware`` problems into
    one severity-ranked list. Daemon items are the spine; overlapping GUI problems
    fold into the matching item (§5.1 fold map); GUI-only problems stay first-class.
    Sorted most-severe first, ``ok`` items last, daemon-before-GUI within a tier."""
    merged: list[MergedReadinessItem] = []
    by_code: dict[str, MergedReadinessItem] = {}

    for it in inventory.items if inventory else []:
        m = _from_daemon(it)
        merged.append(m)
        by_code[it.code] = m

    for p in detect_readiness_problems(hw_diag) if hw_diag else []:
        key = p.get("key", "")
        absorber = next((by_code[c] for c in _FOLD.get(key, []) if c in by_code), None)
        if absorber is not None:
            _fold_gui_into(absorber, p)
        else:
            merged.append(_from_gui(p))

    # Non-ok first (rank desc), then ok items; stable → daemon-before-GUI in a tier.
    merged.sort(key=lambda m: (m.is_ok, -m.rank))
    return merged


def overall_severity(items: list[MergedReadinessItem]) -> str:
    """The merged verdict = the most severe item's severity (``"ok"`` when empty)."""
    if not items:
        return "ok"
    return max(items, key=lambda m: m.rank).severity


def to_fix_count(items: list[MergedReadinessItem]) -> int:
    """Number of items that need attention (warning + critical)."""
    return sum(1 for m in items if m.rank >= _SEV_RANK["warning"])
