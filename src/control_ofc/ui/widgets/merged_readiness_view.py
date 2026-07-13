"""The merged, actionable Hardware-readiness view (DEC-206).

Renders the fused readiness index (:func:`control_ofc.ui.readiness_merge.merge_readiness`):
a verdict banner, a severity-ranked list of actionable cards, and a collapsed
"Passed" group. Each card ends in a real action — a primary button (deep-link or
in-surface one-click) and/or a "Learn more" disclosure carrying the technical
detail + doc link — never an inert string (the failure mode DEC-206 set out to fix).

Security boundary: a daemon-supplied string (``headline`` / ``plain_detail``) is
rendered as ``PlainText`` and never interpreted as markup; only GUI-authored text
(``html_detail`` / ``doc_url``) is rendered as rich text. The two never mix in one
label — the ``detail_is_html`` split lives in the data (``readiness_merge``).

The widget is a dumb view: it emits :attr:`action_requested` with the item's
:class:`ActionSpec`; the Diagnostics page routes it (in-surface / tab-switch /
cross-page deep-link).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.readiness_merge import (
    ACTION_NONE,
    MergedReadinessItem,
    overall_severity,
    to_fix_count,
)
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.inventory_readiness_view import (
    _AUTO_EXPAND_RANK,
    _SEVERITY_BADGE_MIN_WIDTH,
    _severity_chip,
)

# Merged verdict severity → human wording for the banner.
_VERDICT_TEXT = {
    "ok": "Cooling hardware ready",
    "info": "Ready — informational notes",
    "warning": "Needs attention",
    "critical": "Not ready — action required",
}


def _safe(code: str, idx: int) -> str:
    frag = "".join(ch if ch.isalnum() else "_" for ch in (code or "")).strip("_")
    return frag or f"item{idx}"


class MergedReadinessView(QWidget):
    """Renders a merged readiness list as verdict + actionable cards + Passed group.

    States: :meth:`set_status` (transient), :meth:`set_items` (data),
    :meth:`set_error`, :meth:`set_unsupported` (pre-DEC-200 daemon)."""

    action_requested = Signal(object)  # readiness_merge.ActionSpec

    def __init__(
        self, object_name: str = "MergedReadiness_View", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._rows: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self._verdict_label = QLabel("")
        self._verdict_label.setObjectName("MergedReadiness_Label_verdict")
        self._verdict_label.setProperty("class", "SectionTitle")
        self._verdict_label.setWordWrap(True)
        self._verdict_label.setVisible(False)
        root.addWidget(self._verdict_label)

        self._status_label = QLabel("Loading hardware readiness…")
        self._status_label.setObjectName("MergedReadiness_Label_status")
        self._status_label.setProperty("class", "CardMeta")
        self._status_label.setWordWrap(True)
        # set_error surfaces daemon error strings — render as PlainText so an error
        # message can never be interpreted as markup (defence-in-depth).
        self._status_label.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self._status_label)

        scroll = QScrollArea()
        scroll.setObjectName("MergedReadiness_Scroll_items")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("MergedReadiness_Container_items")
        self._items_layout = QVBoxLayout(container)
        self._items_layout.setContentsMargins(0, 0, 0, 0)
        self._items_layout.setSpacing(8)
        self._items_layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

    # -- state transitions --

    def set_status(self, message: str) -> None:
        self._status_label.setText(message)
        self._status_label.setVisible(True)

    def set_error(self, message: str) -> None:
        self._clear_rows()
        self._verdict_label.setVisible(False)
        self._status_label.setText(message)
        self._status_label.setVisible(True)

    def set_unsupported(self) -> None:
        self._clear_rows()
        self._verdict_label.setVisible(False)
        self._status_label.setText(
            "Hardware readiness is unavailable — the connected daemon predates this feature."
        )
        self._status_label.setVisible(True)

    def set_items(self, items: list[MergedReadinessItem]) -> None:
        self._clear_rows()
        overall = overall_severity(items)
        _word, glyph, css, _rank = _severity_chip(overall)
        n = to_fix_count(items)
        text = _VERDICT_TEXT.get(overall, "Hardware readiness")
        if n:
            text = f"{text} — {n} to fix"
        self._verdict_label.setText(f"{glyph}  {text}")
        set_chip_class(self._verdict_label, css)
        self._verdict_label.setVisible(True)

        actionable = [it for it in items if not it.is_ok]
        passed = [it for it in items if it.is_ok]

        if not actionable and not passed:
            self._status_label.setText("✓ All hardware-readiness checks passed.")
            self._status_label.setVisible(True)
            return
        self._status_label.setVisible(False)

        for idx, item in enumerate(actionable):
            row = self._make_card(item, idx)
            self._insert(row)

        if passed:
            self._insert(self._make_passed_group(passed))

    # -- rendering --

    def _insert(self, widget: QWidget) -> None:
        self._items_layout.insertWidget(self._items_layout.count() - 1, widget)
        self._rows.append(widget)

    def _clear_rows(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []

    def _make_card(self, item: MergedReadinessItem, idx: int) -> QWidget:
        word, glyph, css, rank = _severity_chip(item.severity)
        key = _safe(item.code, idx)

        card = QFrame()
        card.setObjectName(f"MergedReadiness_Card_{key}")
        card.setProperty("class", "Card")
        row = QHBoxLayout(card)
        row.setSpacing(10)

        badge = QLabel(f"{glyph} {word}")
        badge.setObjectName(f"MergedReadiness_Badge_{key}")
        badge.setProperty("class", css)
        badge.setMinimumWidth(_SEVERITY_BADGE_MIN_WIDTH)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(4)

        headline = QLabel(item.headline or item.code)
        headline.setObjectName(f"MergedReadiness_Headline_{key}")
        headline.setTextFormat(Qt.TextFormat.PlainText)  # daemon/GUI string — never markup
        headline.setWordWrap(True)
        headline.setStyleSheet("font-weight: 600;")  # weight only; size stays themed
        col.addWidget(headline)

        component = (item.component or "").strip()
        if component:
            comp = QLabel(component.upper())
            comp.setObjectName(f"MergedReadiness_Component_{key}")
            comp.setProperty("class", "SmallLabel")
            comp.setTextFormat(Qt.TextFormat.PlainText)
            col.addWidget(comp)

        flags = self._impact_flags(item, key)
        if flags is not None:
            col.addWidget(flags)

        # Primary action button — the "actions not strings" core.
        if item.action.kind != ACTION_NONE and item.action.label:
            btn = QPushButton(item.action.label)
            btn.setObjectName(f"MergedReadiness_Action_{key}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, a=item.action: self.action_requested.emit(a))
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 0, 0, 0)
            btn_row.addWidget(btn)
            btn_row.addStretch(1)
            col.addLayout(btn_row)

        # "Learn more" progressive disclosure (auto-open for warning/critical).
        if item.plain_detail or item.html_detail or item.doc_url:
            section = CollapsibleSection(
                "Learn more", f"MergedReadiness_Section_{key}", expanded=rank >= _AUTO_EXPAND_RANK
            )
            if item.plain_detail:
                lbl = QLabel(item.plain_detail)
                lbl.setObjectName(f"MergedReadiness_Plain_{key}")
                lbl.setTextFormat(Qt.TextFormat.PlainText)  # daemon detail — PlainText
                lbl.setWordWrap(True)
                lbl.setProperty("class", "CardMeta")
                section.add_widget(lbl)
            if item.html_detail:
                rich = QLabel(item.html_detail)
                rich.setObjectName(f"MergedReadiness_Html_{key}")
                rich.setTextFormat(Qt.TextFormat.RichText)  # GUI-authored fix — trusted
                rich.setWordWrap(True)
                rich.setOpenExternalLinks(True)
                rich.setProperty("class", "CardMeta")
                section.add_widget(rich)
            if item.doc_url:
                link = QLabel(
                    f'<a href="{item.doc_url}" style="color:{active_theme().status_info}">'
                    f"{item.doc_title or 'Learn more'} ↗</a>"
                )
                link.setObjectName(f"MergedReadiness_Doc_{key}")
                link.setTextFormat(Qt.TextFormat.RichText)  # GUI-authored URL — trusted
                link.setOpenExternalLinks(True)
                link.setWordWrap(True)
                section.add_widget(link)
            col.addWidget(section)

        row.addLayout(col, 1)
        return card

    def _impact_flags(self, item: MergedReadinessItem, key: str) -> QWidget | None:
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
        holder.setObjectName(f"MergedReadiness_Flags_{key}")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        for i, (label, css) in enumerate(tags):
            chip = QLabel(label)
            chip.setObjectName(f"MergedReadiness_Flag_{key}_{i}")
            chip.setProperty("class", css)
            chip.setTextFormat(Qt.TextFormat.PlainText)
            hl.addWidget(chip)
        hl.addStretch(1)
        return holder

    def _make_passed_group(self, passed: list[MergedReadinessItem]) -> QWidget:
        # Lighthouse-style collapsed "Passed" group so the OK items don't compete
        # with what's actionable (they stay one click away for reassurance).
        section = CollapsibleSection(
            f"Passed ({len(passed)})", "MergedReadiness_Section_passed", expanded=False
        )
        for idx, item in enumerate(passed):
            _word, glyph, _css, _rank = _severity_chip(item.severity)
            lbl = QLabel(f"{glyph}  {item.headline or item.code}")
            lbl.setObjectName(f"MergedReadiness_Passed_{_safe(item.code, idx)}")
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setWordWrap(True)
            lbl.setProperty("class", "CardMeta")
            section.add_widget(lbl)
        return section
