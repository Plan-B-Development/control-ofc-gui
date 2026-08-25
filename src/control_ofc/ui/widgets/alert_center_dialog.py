"""Alert Centre (DEC-282, brief §8) — on-demand detail for the Logs page.

Built on :class:`ModalDialog`, the scrim-backed pattern this application already uses
for sensor detail, curve editing and fan roles. Deliberately not a new navigation
model, and deliberately not a permanent pane: the point of DEC-282's page work is that
alert detail stops consuming screen space while nothing is wrong.

Two sections, because they answer different questions:

* **ACTIVE** — conditions that exist right now, acknowledged or not, with everything
  the alert model actually knows: when it was first and last seen, where it came from,
  and a suggested next step *only* where the bounded condition taxonomy supports one.
  Nothing is invented.
* **RECENT / RECOVERED** — conditions that have cleared. This section is the reason
  the page exists: an alert that flashed past can still be read here afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.alerts_view import (
    AlertCardVM,
    build_active_cards,
    build_recovered_rows,
)
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import SectionHeader
from control_ofc.ui.components.dialog import ModalDialog

if TYPE_CHECKING:
    from control_ofc.services.app_state import AppState


class AlertCenterDialog(ModalDialog):
    """Active and recovered alerts, with acknowledgement and a route to the logs."""

    # (source, component) — the Logs page narrows its table to this.
    show_related_logs = Signal(str, str)

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__("Alert Centre", parent)
        self.setObjectName("AlertCenterDialog")
        self._state = state
        self.resize(560, 620)

        body = self.body_layout()
        scroll = QScrollArea()
        scroll.setObjectName("AlertCenter_Scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._container = QWidget()
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(0, 0, 0, 0)
        self._vbox.setSpacing(10)
        self._vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._container)
        body.addWidget(scroll, 1)

        self._ack_all_btn = self.add_footer_button(
            "Acknowledge all", "secondary", object_name="AlertCenter_Btn_acknowledgeAll"
        )
        self._ack_all_btn.clicked.connect(self._on_acknowledge_all)
        close_btn = self.add_footer_button("Close", "primary", object_name="AlertCenter_Btn_close")
        close_btn.clicked.connect(self.accept)

        self._state.warnings_changed.connect(self.refresh)
        self.refresh()

    # ── Rendering ────────────────────────────────────────────────────

    def refresh(self) -> None:
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        active = build_active_cards(self._state.alerts.present())
        recovered = build_recovered_rows(self._state.alerts.recovered())

        self._vbox.addWidget(SectionHeader("Active", object_name="AlertCenter_Header_active"))
        if active:
            for i, vm in enumerate(active):
                self._vbox.addWidget(self._build_card(i, vm))
        else:
            self._vbox.addWidget(
                _muted("Nothing is currently wrong.", "AlertCenter_Label_noActive")
            )

        self._vbox.addWidget(
            SectionHeader("Recent / Recovered", object_name="AlertCenter_Header_recovered")
        )
        if recovered:
            for i, row in enumerate(recovered):
                line = QWidget()
                line.setObjectName(f"AlertCenter_Recovered_{i}")
                h = QHBoxLayout(line)
                h.setContentsMargins(4, 2, 4, 2)
                text = QLabel(row.text)
                text.setObjectName(f"AlertCenter_Recovered_{i}_text")
                text.setTextFormat(Qt.TextFormat.PlainText)
                h.addWidget(text)
                h.addStretch(1)
                when = QLabel(row.recovered_at)
                when.setObjectName(f"AlertCenter_Recovered_{i}_time")
                when.setProperty("class", "CardMeta")
                h.addWidget(when)
                self._vbox.addWidget(line)
        else:
            self._vbox.addWidget(
                _muted("No recent alerts this session.", "AlertCenter_Label_noRecovered")
            )

        self._ack_all_btn.setEnabled(self._state.unacknowledged_count > 0)

    def _build_card(self, idx: int, vm: AlertCardVM) -> QWidget:
        card = QFrame()
        card.setObjectName(f"AlertCenter_Card_{idx}")
        card.setProperty("class", "Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        head = QHBoxLayout()
        pill = StatusPill(vm.state_label, vm.pill_state, object_name=f"AlertCenter_Pill_{idx}")
        head.addWidget(pill)
        # Glyph alongside the word so severity never depends on colour alone.
        glyph = QLabel(vm.glyph)
        glyph.setObjectName(f"AlertCenter_Card_{idx}_glyph")
        head.addWidget(glyph)
        head.addStretch(1)
        v.addLayout(head)

        title = QLabel(vm.title)
        title.setObjectName(f"AlertCenter_Card_{idx}_title")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        v.addWidget(title)

        # Alert strings embed daemon-derived sensor labels and fan ids — plain text
        # only, so stray markup can never be reinterpreted as rich text (DEC-231).
        detail = QLabel(vm.detail)
        detail.setObjectName(f"AlertCenter_Card_{idx}_detail")
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        detail.setProperty("class", "CardMeta")
        v.addWidget(detail)

        for caption, value in (
            ("First detected", vm.first_detected),
            ("Last detected", vm.last_detected),
            ("Source", vm.source),
            ("Component", vm.component),
        ):
            row = QHBoxLayout()
            cap = QLabel(caption)
            cap.setProperty("class", "CardMeta")
            row.addWidget(cap)
            row.addStretch(1)
            val = QLabel(value)
            val.setObjectName(f"AlertCenter_Card_{idx}_{caption.split()[0].lower()}")
            val.setTextFormat(Qt.TextFormat.PlainText)
            row.addWidget(val)
            v.addLayout(row)

        if vm.suggested_action:
            act = QLabel(f"→ {vm.suggested_action}")
            act.setObjectName(f"AlertCenter_Card_{idx}_action")
            act.setWordWrap(True)
            act.setProperty("class", "CardMeta")
            v.addWidget(act)

        buttons = QHBoxLayout()
        ack = make_button("Acknowledge", "secondary", object_name=f"AlertCenter_Card_{idx}_ack")
        ack.setEnabled(not vm.acknowledged)
        ack.clicked.connect(lambda _checked=False, key=vm.key: self._state.acknowledge(key))
        buttons.addWidget(ack)
        related = make_button(
            "Show related logs", "ghost", object_name=f"AlertCenter_Card_{idx}_related"
        )
        related.clicked.connect(
            lambda _checked=False, s=vm.source, c=vm.component: self._emit_related(s, c)
        )
        buttons.addWidget(related)
        buttons.addStretch(1)
        v.addLayout(buttons)
        return card

    # ── Actions ──────────────────────────────────────────────────────

    def _emit_related(self, source: str, component: str) -> None:
        self.show_related_logs.emit(source, component)
        self.accept()

    def _on_acknowledge_all(self) -> None:
        self._state.acknowledge_all()


def _muted(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setProperty("class", "CardMeta")
    return label
