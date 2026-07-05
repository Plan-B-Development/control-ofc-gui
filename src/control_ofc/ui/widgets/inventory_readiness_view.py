"""Dedicated hardware-readiness view (Phase 4 / DEC-200).

Renders the daemon's ``GET /inventory/readiness`` report: an overall verdict
banner plus a severity-sorted list of actionable items. Each item shows a
severity chip + summary, with the technical detail, recommended action, and
impact flags tucked into a progressive-disclosure section (auto-opened for
warning/critical items so blockers are never hidden).

Read-only diagnose-and-guide. The daemon owns the wording, so every
daemon-supplied string is shown as ``PlainText`` — never interpreted as markup
(defence-in-depth against markup injection through the API).

Distinct from ``readiness_report`` (the GUI-authored report derived from
``/diagnostics/hardware``); this surfaces the daemon's own structured readiness.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import InventoryReadiness, ReadinessItem
from control_ofc.ui.hwmon_guidance import severity_display
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

_SEVERITY_BADGE_MIN_WIDTH = 104  # so "⛔ CRITICAL" never clips (matches Diagnostics)

# Human wording for the overall verdict, keyed on the daemon's rollup severity.
_VERDICT_TEXT = {
    "ok": "Hardware ready",
    "info": "Ready — informational notes",
    "warning": "Needs attention",
    "critical": "Not ready — action required",
}


def _severity_chip(severity: str) -> tuple[str, str, str, int]:
    """Map a daemon severity (``ok``/``info``/``warning``/``critical``) to the
    GUI's ``(word, glyph, css_class, rank)``.

    The daemon vocabulary differs from the GUI's ``severity_display`` (which uses
    ``warn`` not ``warning`` and has no ``ok``), so normalise here: ``warning`` →
    ``warn`` and ``ok`` → a Success chip. Unknown values degrade to the
    forgiving ``severity_display`` default (info-tier).
    """
    s = (severity or "").lower()
    if s == "ok":
        return ("OK", "✓", "SuccessChip", 0)
    disp = severity_display("warn" if s == "warning" else s)
    return (disp.word, disp.glyph, disp.css_class, disp.rank)


# Auto-expand an item's detail when it is at least warning-tier.
_AUTO_EXPAND_RANK = severity_display("warn").rank


class InventoryReadinessView(QWidget):
    """Renders an :class:`InventoryReadiness` as a verdict + item checklist.

    States: :meth:`set_status` (transient), :meth:`set_readiness` (data),
    :meth:`set_error`, :meth:`set_unsupported` (pre-2.6 daemon).
    """

    def __init__(self, object_name: str = "Readiness_View", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._rows: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # Prominent overall verdict (top of the view — best-practice scanning).
        self._verdict_label = QLabel("")
        self._verdict_label.setObjectName("Readiness_Label_verdict")
        self._verdict_label.setProperty("class", "SectionTitle")
        self._verdict_label.setWordWrap(True)
        self._verdict_label.setVisible(False)
        root.addWidget(self._verdict_label)

        # Status / empty-state / unsupported line.
        self._status_label = QLabel("Loading hardware readiness…")
        self._status_label.setObjectName("Readiness_Label_status")
        self._status_label.setProperty("class", "CardMeta")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        # Scrollable item list.
        scroll = QScrollArea()
        scroll.setObjectName("Readiness_Scroll_items")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("Readiness_Container_items")
        self._items_layout = QVBoxLayout(container)
        self._items_layout.setContentsMargins(0, 0, 0, 0)
        self._items_layout.setSpacing(8)
        self._items_layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

    # -- state transitions --

    def set_status(self, message: str) -> None:
        """Show a transient status line (fetching / connecting)."""
        self._status_label.setText(message)
        self._status_label.setVisible(True)

    def set_error(self, message: str) -> None:
        self._clear_rows()
        self._verdict_label.setVisible(False)
        self._status_label.setText(message)
        self._status_label.setVisible(True)

    def set_unsupported(self) -> None:
        """Render the pre-2.6-daemon state (endpoint absent)."""
        self._clear_rows()
        self._verdict_label.setVisible(False)
        self._status_label.setText(
            "Hardware readiness is unavailable — the connected daemon predates this feature."
        )
        self._status_label.setVisible(True)

    def set_readiness(self, readiness: InventoryReadiness) -> None:
        self._clear_rows()
        _word, glyph, css, _rank = _severity_chip(readiness.overall)
        text = _VERDICT_TEXT.get((readiness.overall or "ok").lower(), "Hardware readiness")
        self._verdict_label.setText(f"{glyph}  {text}")
        set_chip_class(self._verdict_label, css)
        self._verdict_label.setVisible(True)

        # Most severe first (blockers on top), stable within a severity.
        items = sorted(readiness.items, key=lambda it: _severity_chip(it.severity)[3], reverse=True)
        if not items:
            self._status_label.setText("✓ All hardware-readiness checks passed.")
            self._status_label.setVisible(True)
            return
        self._status_label.setVisible(False)
        for idx, item in enumerate(items):
            row = self._make_item_row(item, idx)
            self._items_layout.insertWidget(self._items_layout.count() - 1, row)
            self._rows.append(row)

    # -- rendering helpers --

    def _clear_rows(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []

    def _make_item_row(self, item: ReadinessItem, idx: int) -> QWidget:
        word, glyph, css, rank = _severity_chip(item.severity)
        safe_code = item.code or f"item{idx}"

        row = QFrame()
        row.setObjectName(f"Readiness_ItemRow_{safe_code}")
        row.setProperty("class", "Card")
        row_layout = QHBoxLayout(row)
        row_layout.setSpacing(10)

        badge = QLabel(f"{glyph} {word}")
        badge.setObjectName(f"Readiness_Badge_{safe_code}")
        badge.setProperty("class", css)
        badge.setMinimumWidth(_SEVERITY_BADGE_MIN_WIDTH)
        row_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(4)

        summary = QLabel(item.summary or item.code)
        summary.setObjectName(f"Readiness_Summary_{safe_code}")
        summary.setTextFormat(Qt.TextFormat.PlainText)  # daemon string — never markup
        summary.setWordWrap(True)
        summary.setStyleSheet("font-weight: 600;")  # weight only; size stays themed
        col.addWidget(summary)

        component = (item.component or "").strip()
        if component:
            comp_label = QLabel(component.upper())
            comp_label.setObjectName(f"Readiness_Component_{safe_code}")
            comp_label.setProperty("class", "SmallLabel")
            comp_label.setTextFormat(Qt.TextFormat.PlainText)
            col.addWidget(comp_label)

        flags = self._impact_flags(item, safe_code)
        if item.detail or item.recommended_action or flags is not None:
            section = CollapsibleSection(
                "Details", f"Readiness_Section_{safe_code}", expanded=rank >= _AUTO_EXPAND_RANK
            )
            if item.detail:
                detail = QLabel(item.detail)
                detail.setObjectName(f"Readiness_Detail_{safe_code}")
                detail.setTextFormat(Qt.TextFormat.PlainText)
                detail.setWordWrap(True)
                detail.setProperty("class", "CardMeta")
                section.add_widget(detail)
            if item.recommended_action:
                action = QLabel(f"→ {item.recommended_action}")
                action.setObjectName(f"Readiness_Action_{safe_code}")
                action.setTextFormat(Qt.TextFormat.PlainText)
                action.setWordWrap(True)
                section.add_widget(action)
            if flags is not None:
                section.add_widget(flags)
            col.addWidget(section)

        row_layout.addLayout(col, 1)
        return row

    def _impact_flags(self, item: ReadinessItem, safe_code: str) -> QWidget | None:
        tags: list[tuple[str, str]] = []
        if item.affects_safety:
            tags.append(("affects safety", "WarningChip"))
        if item.blocks_control:
            tags.append(("blocks fan control", "WarningChip"))
        if item.blocks_monitoring:
            tags.append(("blocks monitoring", "WarningChip"))
        if item.reboot_may_be_required:
            tags.append(("reboot may be required", "InfoChip"))
        if not tags:
            return None
        holder = QWidget()
        holder.setObjectName(f"Readiness_Flags_{safe_code}")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        for i, (text, css) in enumerate(tags):
            chip = QLabel(text)
            chip.setObjectName(f"Readiness_Flag_{safe_code}_{i}")
            chip.setProperty("class", css)
            chip.setTextFormat(Qt.TextFormat.PlainText)
            hl.addWidget(chip)
        hl.addStretch(1)
        return holder
