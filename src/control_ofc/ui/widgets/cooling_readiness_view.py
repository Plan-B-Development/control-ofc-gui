"""The merged **Cooling Hardware Readiness** view (DEC-207).

One progressively-disclosed page that answers "is my cooling hardware ready?" from
a single daemon assessment (``GET /inventory/hardware-readiness``), replacing the
old separate Readiness + Super-I/O tabs. Five sections, most-actionable first:

1. **Overall readiness summary** — a compact verdict banner (Hardware ready /
   Needs attention / Not ready), the single most-important next step, the last
   scan time, one "Refresh hardware assessment" action, and a read-only note.
2. **Recommended actions** — the actionable findings (critical → warning → info),
   each an actionable card: what/why/next-step + impact chips + a "Learn how"
   link, with the primary action emitting :attr:`action_requested`.
3. **Hardware checks** — the complete checklist in compact grouped rows.
4. **Super-I/O details** — per-chip driver detection + copy-paste load commands.
5. **Advanced detection** — the opt-in active port probe, collapsed and gated.

Security boundary: every daemon-supplied string (readiness summaries/details,
Super-I/O names/commands) is rendered as ``PlainText`` — never interpreted as
markup; only GUI-authored guidance (the doc links) is trusted rich text. No
hardcoded ``font-size`` — the theme owns type; only ``font-weight`` is set inline.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.models import HardwareReadiness, SuperIoChip, SuperIoReport
from control_ofc.ui.cooling_readiness import GROUP_ORDER, build_readiness_items, group_for
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.readiness_merge import (
    ACTION_NONE,
    MergedReadinessItem,
    to_fix_count,
)
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection
from control_ofc.ui.widgets.inventory_readiness_view import (
    _AUTO_EXPAND_RANK,
    _SEVERITY_BADGE_MIN_WIDTH,
    _severity_chip,
)

# Verdict severity → plain-language banner wording (the three-way user framing over
# the daemon's ok/info/warning/critical severity).
_VERDICT_TEXT = {
    "ok": "Hardware ready",
    "info": "Ready — informational notes",
    "warning": "Needs attention",
    "critical": "Not ready — action required",
}

# Measured liability wording shown next to module-load commands (brief-supplied).
_LIABILITY = (
    "Kernel-module and hardware-access changes can affect system stability. Review "
    "the guidance for your hardware before proceeding. Control-OFC does not apply "
    "these changes automatically."
)

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "unknown": 0}


def _safe(code: str, idx: int) -> str:
    frag = "".join(ch if ch.isalnum() else "_" for ch in (code or "")).strip("_")
    return frag or f"item{idx}"


def _format_age(ms: int) -> str:
    """A short human 'last scanned' phrase from a scan age in milliseconds."""
    if ms <= 0:
        return "just now"
    secs = ms // 1000
    if secs < 5:
        return "just now"
    if secs < 90:
        return f"{secs}s ago"
    return f"{secs // 60}m ago"


class CoolingReadinessView(QWidget):
    """Renders a :class:`HardwareReadiness` as the merged 5-section page.

    States: :meth:`set_status` (transient), :meth:`set_report` (data),
    :meth:`set_error`, :meth:`set_unsupported` (daemon predating the endpoint).
    :meth:`set_superio` updates only the Super-I/O section (after a port probe).
    """

    action_requested = Signal(object)  # readiness_merge.ActionSpec
    refresh_requested = Signal()
    probe_requested = Signal()

    def __init__(
        self, object_name: str = "CoolingReadiness_View", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._sections: list[QWidget] = []
        self._superio_card: QWidget | None = None
        self._port_probe_available = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        root.addWidget(self._build_summary())

        # Everything below the summary scrolls; the summary + Refresh stay visible.
        self._scroll = QScrollArea()
        self._scroll.setObjectName("CoolingReadiness_Scroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("CoolingReadiness_Container")
        self._body = QVBoxLayout(container)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(10)
        self._body.addStretch(1)
        self._scroll.setWidget(container)
        root.addWidget(self._scroll, 1)

    # ── Section 1: summary banner (persistent; hosts the Refresh action) ──

    def _build_summary(self) -> QWidget:
        card = QFrame()
        card.setObjectName("CoolingReadiness_Summary")
        card.setProperty("class", "Card")
        col = QVBoxLayout(card)
        col.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._verdict = QLabel("")
        self._verdict.setObjectName("CoolingReadiness_Label_verdict")
        self._verdict.setProperty("class", "SectionTitle")
        self._verdict.setWordWrap(True)
        self._verdict.setVisible(False)
        top.addWidget(self._verdict, 1, Qt.AlignmentFlag.AlignVCenter)
        self._refresh_btn = QPushButton("Refresh hardware assessment")
        self._refresh_btn.setObjectName("CoolingReadiness_Btn_refresh")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.refresh_requested)
        top.addWidget(self._refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(top)

        # Transient status (loading / error / unsupported). Daemon error strings can
        # land here → PlainText so a message is never interpreted as markup.
        self._status = QLabel("Loading hardware readiness…")
        self._status.setObjectName("CoolingReadiness_Label_status")
        self._status.setProperty("class", "CardMeta")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        col.addWidget(self._status)

        # One-sentence next step (the daemon's top_summary) — PlainText.
        self._next_step = QLabel("")
        self._next_step.setObjectName("CoolingReadiness_Label_nextStep")
        self._next_step.setProperty("class", "CardMeta")
        self._next_step.setTextFormat(Qt.TextFormat.PlainText)
        self._next_step.setWordWrap(True)
        self._next_step.setVisible(False)
        col.addWidget(self._next_step)

        # Last scan time + the read-only reassurance.
        self._meta = QLabel("")
        self._meta.setObjectName("CoolingReadiness_Label_meta")
        self._meta.setProperty("class", "SmallLabel")
        self._meta.setTextFormat(Qt.TextFormat.PlainText)
        self._meta.setWordWrap(True)
        self._meta.setVisible(False)
        col.addWidget(self._meta)
        return card

    # ── state transitions ──

    def set_status(self, message: str) -> None:
        self._status.setText(message)
        self._status.setVisible(True)

    def set_error(self, message: str) -> None:
        self._clear_body()
        self._verdict.setVisible(False)
        self._next_step.setVisible(False)
        self._meta.setVisible(False)
        self._status.setText(message)
        self._status.setVisible(True)

    def set_unsupported(self) -> None:
        self.set_error(
            "Hardware readiness is unavailable — the connected daemon predates this feature."
        )

    def set_report(self, hw: HardwareReadiness) -> None:
        self._clear_body()

        overall = (hw.overall or "ok").lower()
        _word, glyph, css, _rank = _severity_chip(overall)
        items = build_readiness_items(hw)
        n = to_fix_count(items)
        verdict = _VERDICT_TEXT.get(overall, "Hardware readiness")
        if n:
            verdict = f"{verdict} — {n} to fix"
        self._verdict.setText(f"{glyph}  {verdict}")
        set_chip_class(self._verdict, css)
        self._verdict.setVisible(True)
        self._status.setVisible(False)

        top = (hw.rollup.top_summary or "").strip()
        if top and overall != "ok":
            self._next_step.setText(f"Most important next step: {top}")
            self._next_step.setVisible(True)
        else:
            self._next_step.setVisible(False)

        meta = f"Last scanned {_format_age(hw.scanned_age_ms)}."
        if hw.scan_degraded or hw.sources_unavailable:
            srcs = ", ".join(hw.sources_unavailable) if hw.sources_unavailable else "some sources"
            meta += f" Partial result — could not read {srcs}."
        meta += "  This assessment is read-only and does not change the system."
        self._meta.setText(meta)
        self._meta.setVisible(True)

        actionable = [it for it in items if not it.is_ok]
        if actionable:
            self._add(self._build_recommended(actionable))
        self._add(self._build_checks(items))
        self._superio_card = self._build_superio(hw.superio)
        self._add(self._superio_card)
        self._port_probe_available = bool(hw.superio.port_probe_available)
        self._add(self._build_advanced(hw.superio))

    def set_superio(self, report: SuperIoReport) -> None:
        """Replace only the Super-I/O details section (after a port probe), leaving
        the readiness sections untouched."""
        if self._superio_card is None:
            return
        idx = self._body.indexOf(self._superio_card)
        if idx < 0:
            return
        self._superio_card.setParent(None)
        self._superio_card.deleteLater()
        if self._superio_card in self._sections:
            self._sections.remove(self._superio_card)
        self._superio_card = self._build_superio(report)
        self._body.insertWidget(idx, self._superio_card)
        self._sections.append(self._superio_card)

    def scroll_to_superio(self) -> None:
        """Bring the Super-I/O details section into view (the in-surface action
        target that replaces the retired Super-I/O tab-switch)."""
        if self._superio_card is not None:
            self._scroll.ensureWidgetVisible(self._superio_card)

    # ── section builders ──

    def _add(self, widget: QWidget) -> None:
        self._body.insertWidget(self._body.count() - 1, widget)
        self._sections.append(widget)

    def _clear_body(self) -> None:
        for w in self._sections:
            w.setParent(None)
            w.deleteLater()
        self._sections = []
        self._superio_card = None

    @staticmethod
    def _card(object_name: str, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName(object_name)
        card.setProperty("class", "Card")
        col = QVBoxLayout(card)
        col.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName(f"{object_name}_title")
        heading.setProperty("class", "PageSubtitle")
        heading.setStyleSheet("font-weight: 600;")  # weight only; size stays themed
        col.addWidget(heading)
        return card, col

    # -- Section 2: recommended actions --

    def _build_recommended(self, actionable: list[MergedReadinessItem]) -> QWidget:
        card, col = self._card("CoolingReadiness_Recommended", "Recommended actions")
        for idx, item in enumerate(actionable):
            col.addWidget(self._action_card(item, idx))
        return card

    def _action_card(self, item: MergedReadinessItem, idx: int) -> QWidget:
        word, glyph, css, rank = _severity_chip(item.severity)
        key = _safe(item.code, idx)
        card = QFrame()
        card.setObjectName(f"CoolingReadiness_Action_{key}")
        card.setProperty("class", "Card")
        row = QHBoxLayout(card)
        row.setSpacing(10)

        badge = QLabel(f"{glyph} {word}")
        badge.setObjectName(f"CoolingReadiness_Badge_{key}")
        badge.setProperty("class", css)
        badge.setMinimumWidth(_SEVERITY_BADGE_MIN_WIDTH)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(4)
        headline = QLabel(item.headline or item.code)
        headline.setObjectName(f"CoolingReadiness_Headline_{key}")
        headline.setTextFormat(Qt.TextFormat.PlainText)  # daemon string — never markup
        headline.setWordWrap(True)
        headline.setStyleSheet("font-weight: 600;")
        col.addWidget(headline)

        component = (item.component or "").strip()
        if component:
            comp = QLabel(component.upper())
            comp.setObjectName(f"CoolingReadiness_Component_{key}")
            comp.setProperty("class", "SmallLabel")
            comp.setTextFormat(Qt.TextFormat.PlainText)
            col.addWidget(comp)

        flags = self._impact_flags(item, key)
        if flags is not None:
            col.addWidget(flags)

        if item.action.kind != ACTION_NONE and item.action.label:
            btn = QPushButton(item.action.label)
            btn.setObjectName(f"CoolingReadiness_Do_{key}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, a=item.action: self.action_requested.emit(a))
            brow = QHBoxLayout()
            brow.setContentsMargins(0, 0, 0, 0)
            brow.addWidget(btn)
            brow.addStretch(1)
            col.addLayout(brow)

        if item.plain_detail or item.doc_url:
            section = CollapsibleSection(
                "Learn more",
                f"CoolingReadiness_Learn_{key}",
                expanded=rank >= _AUTO_EXPAND_RANK,
            )
            if item.plain_detail:
                lbl = QLabel(item.plain_detail)
                lbl.setObjectName(f"CoolingReadiness_Detail_{key}")
                lbl.setTextFormat(Qt.TextFormat.PlainText)  # daemon detail — PlainText
                lbl.setWordWrap(True)
                lbl.setProperty("class", "CardMeta")
                section.add_widget(lbl)
            if item.doc_url:
                section.add_widget(self._doc_link(item.doc_url, item.doc_title, key))
            col.addWidget(section)

        row.addLayout(col, 1)
        return card

    def _impact_flags(self, item: MergedReadinessItem, key: str) -> QWidget | None:
        tags: list[tuple[str, str]] = []
        if item.affects_safety:
            tags.append(("affects thermal safety", "WarningChip"))
        if item.blocks_control:
            tags.append(("blocks fan control", "WarningChip"))
        if item.blocks_monitoring:
            tags.append(("blocks monitoring", "WarningChip"))
        if item.reboot_may_be_required:
            tags.append(("reboot may be required", "InfoChip"))
        if not tags:
            return None
        holder = QWidget()
        holder.setObjectName(f"CoolingReadiness_Flags_{key}")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        for i, (label, css) in enumerate(tags):
            chip = QLabel(label)
            chip.setObjectName(f"CoolingReadiness_Flag_{key}_{i}")
            chip.setProperty("class", css)
            chip.setTextFormat(Qt.TextFormat.PlainText)
            hl.addWidget(chip)
        hl.addStretch(1)
        return holder

    def _doc_link(self, url: str, title: str, key: str) -> QLabel:
        link = QLabel(
            f'<a href="{url}" style="color:{active_theme().status_info}">'
            f"{title or 'Learn how'} ↗</a>"
        )
        link.setObjectName(f"CoolingReadiness_Doc_{key}")
        link.setTextFormat(Qt.TextFormat.RichText)  # GUI-authored URL — trusted
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        return link

    # -- Section 3: hardware checks (compact grouped rows) --

    def _build_checks(self, items: list[MergedReadinessItem]) -> QWidget:
        card, col = self._card("CoolingReadiness_Checks", "Hardware checks")
        by_group: dict[str, list[MergedReadinessItem]] = {}
        for it in items:
            by_group.setdefault(group_for(it.code), []).append(it)
        any_group = False
        for group in GROUP_ORDER:
            group_items = by_group.get(group)
            if not group_items:
                continue
            any_group = True
            gh = QLabel(group)
            gh.setObjectName(f"CoolingReadiness_Group_{_safe(group, 0)}")
            gh.setProperty("class", "SmallLabel")
            gh.setStyleSheet("font-weight: 600;")
            col.addWidget(gh)
            for idx, it in enumerate(sorted(group_items, key=lambda m: (m.is_ok, -m.rank))):
                col.addWidget(self._check_row(it, idx))
        if not any_group:
            empty = QLabel("No hardware-readiness checks were reported.")
            empty.setObjectName("CoolingReadiness_Checks_empty")
            empty.setProperty("class", "CardMeta")
            empty.setTextFormat(Qt.TextFormat.PlainText)
            col.addWidget(empty)
        return card

    def _check_row(self, item: MergedReadinessItem, idx: int) -> QWidget:
        word, glyph, css, _rank = _severity_chip(item.severity)
        key = _safe(item.code, idx)
        holder = QWidget()
        holder.setObjectName(f"CoolingReadiness_Check_{key}")
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        line = QHBoxLayout()
        line.setSpacing(8)
        chip = QLabel(f"{glyph} {word}")
        chip.setObjectName(f"CoolingReadiness_CheckBadge_{key}")
        chip.setProperty("class", css)
        chip.setTextFormat(Qt.TextFormat.PlainText)
        line.addWidget(chip, 0)
        title = QLabel(item.headline or item.code)
        title.setObjectName(f"CoolingReadiness_CheckTitle_{key}")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        line.addWidget(title, 1)
        v.addLayout(line)

        # Technical detail is expandable and collapsed (compact) — only for rows
        # that actually carry detail, so passing checks stay one calm line.
        if item.plain_detail:
            section = CollapsibleSection(
                "Details", f"CoolingReadiness_CheckDetail_{key}", expanded=False
            )
            lbl = QLabel(item.plain_detail)
            lbl.setObjectName(f"CoolingReadiness_CheckDetailText_{key}")
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setWordWrap(True)
            lbl.setProperty("class", "CardMeta")
            section.add_widget(lbl)
            v.addWidget(section)
        return holder

    # -- Section 4: Super-I/O details --

    def _build_superio(self, report: SuperIoReport) -> QWidget:
        card, col = self._card("CoolingReadiness_Superio", "Super-I/O details")

        if not report.arch_supported:
            self._superio_note(
                col, "Super-I/O detection is only available on x86 / x86_64 systems."
            )
            return card

        chips = sorted(
            report.chips,
            key=lambda c: (
                0 if c.recommendation is not None else 1,
                -_CONFIDENCE_RANK.get((c.confidence or "").lower(), 0),
                c.chip_name,
            ),
        )
        if not chips:
            self._superio_note(
                col,
                "No motherboard Super-I/O chip was detected. On a system whose "
                "Super-I/O driver is not loaded, the chip is not visible until it is.",
            )
        else:
            unbound = [c for c in chips if c.recommendation is not None]
            summary = (
                f"{len(unbound)} of {len(chips)} Super-I/O chip(s) need a driver loaded"
                if unbound
                else f"All {len(chips)} detected Super-I/O chip(s) have a driver bound"
            )
            sline = QLabel(summary)
            sline.setObjectName("CoolingReadiness_Superio_summary")
            sline.setProperty("class", "SuccessChip" if not unbound else "WarningChip")
            sline.setTextFormat(Qt.TextFormat.PlainText)
            col.addWidget(sline)
            for idx, chip in enumerate(chips):
                col.addWidget(self._chip_card(chip, idx))
            if unbound:
                liab = QLabel(_LIABILITY)
                liab.setObjectName("CoolingReadiness_Superio_liability")
                liab.setProperty("class", "CardMeta")
                liab.setTextFormat(Qt.TextFormat.PlainText)
                liab.setWordWrap(True)
                col.addWidget(liab)

        notes = list(report.notes)
        if report.acpi_conflict_drivers:
            notes.insert(
                0,
                "ACPI firmware claims the I/O ports of: "
                + ", ".join(report.acpi_conflict_drivers)
                + " — under acpi_enforce_resources=strict (the default) the driver may "
                "refuse to bind.",
            )
        if notes:
            self._superio_note(col, "\n\n".join(notes))
        return card

    def _superio_note(self, col: QVBoxLayout, text: str) -> None:
        note = QLabel(text)
        note.setObjectName("CoolingReadiness_Superio_note")
        note.setProperty("class", "CardMeta")
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        col.addWidget(note)

    def _chip_card(self, chip: SuperIoChip, idx: int) -> QWidget:
        key = _safe(chip.chip_name, idx)
        card = QFrame()
        card.setObjectName(f"CoolingReadiness_Chip_{key}")
        card.setProperty("class", "Card")
        col = QVBoxLayout(card)
        col.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        name = QLabel(chip.chip_name or "(unknown chip)")
        name.setObjectName(f"CoolingReadiness_ChipName_{key}")
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setStyleSheet("font-weight: 600;")
        header.addWidget(name)
        vendor = (chip.vendor or "").strip()
        if vendor and vendor != "unknown":
            vlabel = QLabel(vendor.upper())
            vlabel.setObjectName(f"CoolingReadiness_ChipVendor_{key}")
            vlabel.setProperty("class", "SmallLabel")
            vlabel.setTextFormat(Qt.TextFormat.PlainText)
            header.addWidget(vlabel)
        header.addStretch(1)
        # Distinguish "driver bound" (chip exposing hwmon) from "module loaded":
        # a module in /proc/modules is not proof the chip is bound.
        status = QLabel("driver bound" if chip.hwmon_present else "no driver bound")
        status.setObjectName(f"CoolingReadiness_ChipStatus_{key}")
        status.setProperty("class", "SuccessChip" if chip.hwmon_present else "WarningChip")
        status.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(status)
        col.addLayout(header)

        meta_bits: list[str] = []
        if chip.expected_module and chip.expected_module != "unknown":
            meta_bits.append(f"expected driver: {chip.expected_module}")
        if chip.bound_driver:
            meta_bits.append(f"bound by: {chip.bound_driver}")
        if chip.module_loaded:
            meta_bits.append("module loaded")
        if chip.confidence:
            meta_bits.append(f"confidence: {chip.confidence}")
        if chip.evidence:
            meta_bits.append("evidence: " + ", ".join(chip.evidence))
        if meta_bits:
            meta = QLabel("  ·  ".join(meta_bits))
            meta.setObjectName(f"CoolingReadiness_ChipMeta_{key}")
            meta.setProperty("class", "CardMeta")
            meta.setTextFormat(Qt.TextFormat.PlainText)
            meta.setWordWrap(True)
            col.addWidget(meta)

        rec = chip.recommendation
        if rec is not None:
            section = CollapsibleSection(
                "How to enable", f"CoolingReadiness_Chip_How_{key}", expanded=True
            )
            if rec.reason:
                reason = QLabel(rec.reason)
                reason.setObjectName(f"CoolingReadiness_ChipReason_{key}")
                reason.setTextFormat(Qt.TextFormat.PlainText)
                reason.setWordWrap(True)
                reason.setProperty("class", "CardMeta")
                section.add_widget(reason)
            if rec.load_hint:
                section.add_widget(self._command_row(rec.load_hint, key))
            mainline = QLabel(
                "in mainline kernel" if rec.in_mainline else "needs out-of-tree (DKMS) driver"
            )
            mainline.setObjectName(f"CoolingReadiness_ChipMainline_{key}")
            mainline.setProperty("class", "SuccessChip" if rec.in_mainline else "CautionChip")
            mainline.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(mainline)
            for i, note in enumerate(rec.risk_notes):
                rn = QLabel(f"⚠ {note}")
                rn.setObjectName(f"CoolingReadiness_ChipRisk_{key}_{i}")
                rn.setTextFormat(Qt.TextFormat.PlainText)
                rn.setWordWrap(True)
                rn.setProperty("class", "WarningChip")
                section.add_widget(rn)
            col.addWidget(section)

        for i, cav in enumerate(chip.caveats):
            caveat = QLabel(cav)
            caveat.setObjectName(f"CoolingReadiness_ChipCaveat_{key}_{i}")
            caveat.setTextFormat(Qt.TextFormat.PlainText)
            caveat.setWordWrap(True)
            caveat.setProperty("class", "CardMeta")
            col.addWidget(caveat)
        return card

    def _command_row(self, command: str, key: str) -> QWidget:
        """A selectable, monospace command shown for review + a Copy button — the
        page never runs it (read-only)."""
        holder = QWidget()
        holder.setObjectName(f"CoolingReadiness_Cmd_{key}")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        mono = QLabel(command)
        mono.setObjectName(f"CoolingReadiness_CmdText_{key}")
        mono.setProperty("class", "MonoCommand")
        mono.setTextFormat(Qt.TextFormat.PlainText)  # daemon command — never markup
        mono.setWordWrap(True)
        mono.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hl.addWidget(mono, 1)
        copy = QPushButton("Copy command")
        copy.setObjectName(f"CoolingReadiness_CmdCopy_{key}")
        copy.setCursor(Qt.CursorShape.PointingHandCursor)
        copy.clicked.connect(lambda _=False, c=command: QApplication.clipboard().setText(c))
        hl.addWidget(copy, 0, Qt.AlignmentFlag.AlignTop)
        return holder

    # -- Section 5: advanced detection (opt-in active probe) --

    def _build_advanced(self, report: SuperIoReport) -> QWidget:
        section = CollapsibleSection(
            "Advanced detection", "CoolingReadiness_Advanced", expanded=False
        )
        blurb = QLabel(
            "Passive detection above is normally sufficient. Active port probing reads "
            "the Super-I/O configuration I/O ports directly to identify a chip whose "
            "driver is not loaded. It requires the CAP_SYS_RAWIO capability and is never "
            "run automatically."
        )
        blurb.setObjectName("CoolingReadiness_Advanced_blurb")
        blurb.setProperty("class", "CardMeta")
        blurb.setTextFormat(Qt.TextFormat.PlainText)
        blurb.setWordWrap(True)
        section.add_widget(blurb)

        self._probe_btn = QPushButton("Probe ports (advanced)")
        self._probe_btn.setObjectName("CoolingReadiness_Btn_probe")
        self._probe_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._probe_btn.setEnabled(bool(report.port_probe_available))
        if not report.port_probe_available:
            self._probe_btn.setToolTip(
                report.port_probe_reason or "The active port probe is not available."
            )
        self._probe_btn.clicked.connect(self._confirm_probe)
        section.add_widget(self._probe_btn)
        return section

    def _confirm_probe(self) -> None:
        resp = QMessageBox.question(
            self,
            "Run active port probe?",
            "This performs a deliberate, one-shot read of the Super-I/O configuration "
            "I/O ports to identify an unbound chip. It accesses hardware I/O ports "
            "directly (CAP_SYS_RAWIO). Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.probe_requested.emit()
