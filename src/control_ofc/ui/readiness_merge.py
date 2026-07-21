"""Shared model types for the merged hardware-readiness view (DEC-206/207).

:class:`MergedReadinessItem` + :class:`ActionSpec` (with the ``ACTION_*``
kinds) are the row/action vocabulary the Cooling Hardware Readiness page
renders — ``cooling_readiness`` builds them from the daemon's shared
assessment (DEC-207). ``plain_detail`` (daemon text, rendered PlainText) stays
separate from ``html_detail`` (GUI-authored, trusted rich text): the security
boundary that daemon text is never interpreted as markup.

The GUI-side Readiness ⊕ Troubleshooting ``merge_readiness()`` engine that
originally lived here (DEC-206, built-and-held) was retired in the 2026-07-21
audit dead-code sweep (DEC-224) — the daemon's DEC-207 shared assessment
superseded it permanently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Action kinds — how the merged view's primary button behaves ──
ACTION_NONE = "none"  # no button (ok items, or doc-link-only via doc_url)
ACTION_IN_SURFACE = "in_surface"  # call a method on the merged tab (no navigation)
ACTION_TAB_SWITCH = "tab_switch"  # switch to a sibling Diagnostics tab
ACTION_DEEP_LINK = "deep_link"  # cross-page: main_window switches page + focuses a target


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
