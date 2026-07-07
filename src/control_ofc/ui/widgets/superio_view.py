"""Dedicated Super-I/O detection view (Phase 3 / DEC-202).

Renders the daemon's ``GET /inventory/superio`` report: a summary line plus one
card per detected chip. Chips that need a driver loaded (i.e. carry a
recommendation) sort to the top with an auto-expanded "How to enable" section,
so the actionable guidance is never hidden; already-bound chips follow.

Read-only diagnose-and-guide. The daemon owns every string here (chip names,
recommendations, caveats, load hints), so each is rendered as ``PlainText`` —
never interpreted as markup (defence-in-depth against markup injection through
the API). The load-hint text is selectable so the user can copy the command.

Distinct from the Troubleshooting tab (the GUI-authored report derived from
``/diagnostics/hardware``); this is the daemon's focused "which Super-I/O driver
do I need" surface.
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

from control_ofc.api.models import SuperIoChip, SuperIoReport
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

# Sort weight for a chip's confidence (higher = more confident, shown first
# within the unbound/bound grouping).
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "unknown": 0}


def _safe(name: str, idx: int) -> str:
    """A stable, unique-per-chip objectName fragment derived from a chip name."""
    frag = "".join(ch if ch.isalnum() else "_" for ch in (name or "")).strip("_")
    return frag or f"chip{idx}"


class SuperIoView(QWidget):
    """Renders a :class:`SuperIoReport` as a summary + per-chip card list.

    States: :meth:`set_status` (transient), :meth:`set_report` (data),
    :meth:`set_error`, :meth:`set_unsupported` (daemon predating the endpoint).
    """

    def __init__(self, object_name: str = "Superio_View", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._rows: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # Prominent summary verdict at the top (scanning best-practice).
        self._summary_label = QLabel("")
        self._summary_label.setObjectName("Superio_Label_summary")
        self._summary_label.setProperty("class", "SectionTitle")
        self._summary_label.setWordWrap(True)
        self._summary_label.setVisible(False)
        root.addWidget(self._summary_label)

        # Status / empty-state / unsupported line.
        self._status_label = QLabel("Loading Super-I/O detection…")
        self._status_label.setObjectName("Superio_Label_status")
        self._status_label.setProperty("class", "CardMeta")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        # Scrollable per-chip card list.
        scroll = QScrollArea()
        scroll.setObjectName("Superio_Scroll_chips")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("Superio_Container_chips")
        self._chips_layout = QVBoxLayout(container)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(8)
        self._chips_layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        # Report-level notes (incl. the "present ≠ controllable" caveat), muted.
        self._notes_label = QLabel("")
        self._notes_label.setObjectName("Superio_Label_notes")
        self._notes_label.setProperty("class", "CardMeta")
        self._notes_label.setTextFormat(Qt.TextFormat.PlainText)
        self._notes_label.setWordWrap(True)
        self._notes_label.setVisible(False)
        root.addWidget(self._notes_label)

    # -- state transitions --

    def set_status(self, message: str) -> None:
        """Show a transient status line (fetching / connecting)."""
        self._status_label.setText(message)
        self._status_label.setVisible(True)

    def set_error(self, message: str) -> None:
        self._clear()
        self._status_label.setText(message)
        self._status_label.setVisible(True)

    def set_unsupported(self) -> None:
        """Render the daemon-predates-endpoint state (route 404s)."""
        self._clear()
        self._status_label.setText(
            "Super-I/O detection is unavailable — the connected daemon predates this feature."
        )
        self._status_label.setVisible(True)

    def set_report(self, report: SuperIoReport) -> None:
        self._clear()
        if not report.arch_supported:
            self._status_label.setText(
                "Super-I/O detection is only available on x86 / x86_64 systems."
            )
            self._status_label.setVisible(True)
            return

        # Chips needing a driver (recommendation) first, then by confidence,
        # then by name — deterministic and blockers-on-top.
        chips = sorted(
            report.chips,
            key=lambda c: (
                0 if c.recommendation is not None else 1,
                -_CONFIDENCE_RANK.get((c.confidence or "").lower(), 0),
                c.chip_name,
            ),
        )
        unbound = [c for c in chips if c.recommendation is not None]

        if not chips:
            self._summary_label.setVisible(False)
            self._status_label.setText(
                "No motherboard Super-I/O chip was detected. On a system whose Super-I/O "
                "driver is not loaded, the chip is not visible without loading it."
            )
            self._status_label.setVisible(True)
        else:
            self._status_label.setVisible(False)
            if unbound:
                glyph, css = "⚠", "WarningChip"
                text = f"{len(unbound)} of {len(chips)} Super-I/O chip(s) need a driver loaded"
            else:
                glyph, css = "✓", "SuccessChip"
                text = f"All {len(chips)} detected Super-I/O chip(s) have a driver bound"
            self._summary_label.setText(f"{glyph}  {text}")
            set_chip_class(self._summary_label, css)
            self._summary_label.setVisible(True)
            for idx, chip in enumerate(chips):
                card = self._make_chip_card(chip, idx)
                self._chips_layout.insertWidget(self._chips_layout.count() - 1, card)
                self._rows.append(card)

        # Report-level notes + ACPI conflicts at the bottom.
        notes_parts = list(report.notes)
        if report.acpi_conflict_drivers:
            notes_parts.insert(
                0,
                "ACPI firmware claims the I/O ports of: "
                + ", ".join(report.acpi_conflict_drivers)
                + " — under acpi_enforce_resources=strict (the default) the driver may refuse "
                "to bind.",
            )
        if notes_parts:
            self._notes_label.setText("\n\n".join(notes_parts))
            self._notes_label.setVisible(True)

    # -- rendering helpers --

    def _clear(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        self._summary_label.setVisible(False)
        self._notes_label.setVisible(False)

    def _make_chip_card(self, chip: SuperIoChip, idx: int) -> QWidget:
        key = _safe(chip.chip_name, idx)

        card = QFrame()
        card.setObjectName(f"Superio_ChipCard_{key}")
        card.setProperty("class", "Card")
        col = QVBoxLayout(card)
        col.setSpacing(6)

        # Header: chip name + vendor + bound/unbound status chip.
        header = QHBoxLayout()
        header.setSpacing(8)
        name = QLabel(chip.chip_name or "(unknown chip)")
        name.setObjectName(f"Superio_ChipName_{key}")
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setStyleSheet("font-weight: 600;")  # weight only; size stays themed
        header.addWidget(name)

        vendor = (chip.vendor or "").strip()
        if vendor and vendor != "unknown":
            vlabel = QLabel(vendor.upper())
            vlabel.setObjectName(f"Superio_ChipVendor_{key}")
            vlabel.setProperty("class", "SmallLabel")
            vlabel.setTextFormat(Qt.TextFormat.PlainText)
            header.addWidget(vlabel)

        header.addStretch(1)

        status = QLabel("driver bound" if chip.hwmon_present else "no driver loaded")
        status.setObjectName(f"Superio_ChipStatus_{key}")
        status.setProperty("class", "SuccessChip" if chip.hwmon_present else "WarningChip")
        status.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(status)
        col.addLayout(header)

        # Meta: expected/bound driver + confidence.
        meta_bits: list[str] = []
        if chip.expected_module and chip.expected_module != "unknown":
            meta_bits.append(f"driver: {chip.expected_module}")
        if chip.bound_driver:
            meta_bits.append(f"bound by: {chip.bound_driver}")
        if chip.confidence:
            meta_bits.append(f"confidence: {chip.confidence}")
        if meta_bits:
            meta = QLabel("  ·  ".join(meta_bits))
            meta.setObjectName(f"Superio_ChipMeta_{key}")
            meta.setProperty("class", "CardMeta")
            meta.setTextFormat(Qt.TextFormat.PlainText)
            meta.setWordWrap(True)
            col.addWidget(meta)

        # Recommendation (unbound chip) — auto-expanded actionable guidance.
        rec = chip.recommendation
        if rec is not None:
            section = CollapsibleSection("How to enable", f"Superio_Section_{key}", expanded=True)
            if rec.reason:
                reason = QLabel(rec.reason)
                reason.setObjectName(f"Superio_Reason_{key}")
                reason.setTextFormat(Qt.TextFormat.PlainText)
                reason.setWordWrap(True)
                reason.setProperty("class", "CardMeta")
                section.add_widget(reason)
            if rec.load_hint:
                hint = QLabel(rec.load_hint)
                hint.setObjectName(f"Superio_LoadHint_{key}")
                hint.setTextFormat(Qt.TextFormat.PlainText)
                hint.setWordWrap(True)
                # Selectable so the user can copy the command out of the guidance.
                hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                section.add_widget(hint)
            mainline = QLabel(
                "in mainline kernel" if rec.in_mainline else "needs out-of-tree (DKMS) driver"
            )
            mainline.setObjectName(f"Superio_Mainline_{key}")
            mainline.setProperty("class", "SuccessChip" if rec.in_mainline else "CautionChip")
            mainline.setTextFormat(Qt.TextFormat.PlainText)
            section.add_widget(mainline)
            for i, note in enumerate(rec.risk_notes):
                rn = QLabel(f"⚠ {note}")
                rn.setObjectName(f"Superio_Risk_{key}_{i}")
                rn.setTextFormat(Qt.TextFormat.PlainText)
                rn.setWordWrap(True)
                rn.setProperty("class", "WarningChip")
                section.add_widget(rn)
            col.addWidget(section)

        # Non-actionable caveats (e.g. unrecognized chip).
        for i, cav in enumerate(chip.caveats):
            caveat = QLabel(cav)
            caveat.setObjectName(f"Superio_Caveat_{key}_{i}")
            caveat.setTextFormat(Qt.TextFormat.PlainText)
            caveat.setWordWrap(True)
            caveat.setProperty("class", "CardMeta")
            col.addWidget(caveat)

        return card
