"""Dashboard fan card (DEC-222) — one compact card per logical control.

Renders a :class:`~control_ofc.services.fan_cards_view.FanCardVM`: the control's
name, a **read-only** state chip, the RPM / SPEED / TEMP triple, a lightweight
curve preview, and an Edit button that deep-links to the Controls page.

The card is deliberately read-only. The daemon's live-intent API is control-keyed
and its take/renew/release session (deadman + monotonic fencing, DEC-163) is
owned by ``controls_page``; duplicating that here would mean two independent
sessions racing for the same control and two implementations of the same safety
logic. Editing therefore navigates to the surface that already owns it.

The curve preview reuses :class:`~control_ofc.ui.widgets.curve_card.CurvePreview`
— an owner-drawn painter with a constant, font-derived ``sizeHint``, so the
render→hint→grant→render ratchet that plagued the old pixmap preview cannot
recur here either.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.fan_cards_view import FanCardVM, FanState
from control_ofc.ui.components.cards import Card
from control_ofc.ui.qt_util import set_chip_class
from control_ofc.ui.theme import active_theme
from control_ofc.ui.widgets.curve_card import CurvePreview

# FanState → (chip text, chip QSS class). Text pairs with colour so the state is
# never colour-only (WCAG 1.4.1). "Auto" means the daemon's curve is driving it —
# the resting, healthy case.
_STATE_CHIP: dict[FanState, tuple[str, str]] = {
    FanState.NORMAL: ("Auto", "SuccessChip"),
    FanState.OVERRIDE: ("Override active", "WarningChip"),
    FanState.LOW_RPM: ("Low RPM", "WarningChip"),
    FanState.STALE: ("Stale", "WarningChip"),
    FanState.STALL: ("Stall", "CriticalChip"),
    FanState.OFFLINE: ("Offline", "CriticalChip"),
}


def _card_slug(control_id: str) -> str:
    """objectName-safe token for a control id (the Unassigned card has none).

    Read-only fan cards are keyed by fan id, which contains ``:`` separators —
    sanitise them so every card still gets a unique, well-formed objectName.
    """
    token = control_id or "unassigned"
    return "".join(c if c.isalnum() else "_" for c in token).strip("_") or "unassigned"


class FanControlCard(Card):
    """Compact read-only status card for one logical control."""

    # control_id of the card whose Edit was clicked ("" for the Unassigned card,
    # which has no control to focus — the page just opens Controls).
    edit_requested = Signal(str)

    def __init__(self, vm: FanCardVM, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._control_id = vm.control_id
        slug = _card_slug(vm.card_key)
        self.setObjectName(f"FanCard_Root_{slug}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Row 1: name + read-only state chip.
        head = QHBoxLayout()
        head.setSpacing(6)
        self._name = QLabel(vm.label)
        self._name.setObjectName(f"FanCard_Label_name_{slug}")
        # Control names come from the profile and fan labels from user aliases —
        # untrusted text. Render verbatim so stray markup can never be
        # reinterpreted as rich text (matches warnings_view + footer).
        self._name.setTextFormat(Qt.TextFormat.PlainText)
        self._name.setStyleSheet("font-weight: bold; background: transparent;")
        head.addWidget(self._name)
        head.addStretch(1)
        self._state_chip = QLabel("")
        self._state_chip.setObjectName(f"FanCard_Chip_state_{slug}")
        head.addWidget(self._state_chip)
        layout.addLayout(head)

        # Row 2: how many fans this card actually moves — the honest blast radius
        # of anything done to this control.
        self._count = QLabel("")
        self._count.setObjectName(f"FanCard_Label_fanCount_{slug}")
        self._count.setProperty("class", "CardMeta")
        self._count.setStyleSheet("background: transparent;")
        layout.addWidget(self._count)

        # Row 3: the RPM / SPEED / TEMP triple, column-labelled above the values.
        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self._rpm_value = self._add_metric(metrics, "RPM", f"FanCard_Value_rpm_{slug}")
        self._speed_value = self._add_metric(metrics, "SPEED", f"FanCard_Value_speed_{slug}")
        self._temp_value = self._add_metric(metrics, "TEMP", f"FanCard_Value_temp_{slug}")
        metrics.addStretch(1)
        layout.addLayout(metrics)

        # Row 4: curve preview, or a muted placeholder when nothing drives it.
        self._preview = CurvePreview()
        self._preview.setObjectName(f"FanCard_Preview_curve_{slug}")
        self._preview.set_theme(active_theme())
        layout.addWidget(self._preview, 1)
        self._no_curve = QLabel("No curve assigned")
        self._no_curve.setObjectName(f"FanCard_Label_noCurve_{slug}")
        self._no_curve.setProperty("class", "CardMeta")
        self._no_curve.setStyleSheet("background: transparent;")
        self._no_curve.setVisible(False)
        layout.addWidget(self._no_curve)

        # Row 5: Edit → the Controls page, which owns every write.
        actions = QHBoxLayout()
        actions.addStretch(1)
        self._edit_btn = QPushButton("Edit")
        self._edit_btn.setObjectName(f"FanCard_Btn_edit_{slug}")
        self._edit_btn.clicked.connect(lambda: self.edit_requested.emit(self._control_id))
        actions.addWidget(self._edit_btn)
        layout.addLayout(actions)

        self.update_vm(vm)

    @staticmethod
    def _add_metric(row: QHBoxLayout, title: str, object_name: str) -> QLabel:
        """One labelled metric column; returns the value label for updating."""
        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(0)
        caption = QLabel(title)
        caption.setProperty("class", "CardMeta")
        caption.setStyleSheet("background: transparent;")
        value = QLabel("—")
        value.setObjectName(object_name)
        value.setProperty("class", "CardValue")
        value.setStyleSheet("background: transparent;")
        column_layout.addWidget(caption)
        column_layout.addWidget(value)
        row.addWidget(column)
        return value

    # ── updates ──────────────────────────────────────────────────────

    def update_vm(self, vm: FanCardVM) -> None:
        """Re-render from a fresh VM. Cheap and idempotent (called each poll)."""
        self._control_id = vm.control_id
        self._name.setText(vm.label)
        self._count.setText(
            "No fans assigned"
            if vm.fan_count == 0
            else f"{vm.fan_count} fan{'' if vm.fan_count == 1 else 's'}"
        )

        self._rpm_value.setText("—" if vm.rpm is None else str(vm.rpm))
        # Commanded PWM wins over measured duty when both exist; duty is labelled
        # so it is never misread as a value the daemon commanded (DEC-204).
        if vm.pwm_pct is not None:
            self._speed_value.setText(f"{vm.pwm_pct}%")
        elif vm.duty_pct is not None:
            self._speed_value.setText(f"{vm.duty_pct}% duty")
        else:
            self._speed_value.setText("—")
        self._temp_value.setText("—" if vm.temp_c is None else f"{vm.temp_c:.0f}°C")

        text, css = _STATE_CHIP.get(vm.state, ("Auto", "SuccessChip"))
        # "Auto" would be a lie for a fan nothing is driving — say what is
        # actually true and keep the chip informational.
        if vm.state == FanState.NORMAL:
            if vm.is_read_only:
                text, css = "Read-only", "InfoChip"
            elif vm.is_unassigned:
                text, css = "Not controlled", "InfoChip"
            elif vm.fan_count == 0:
                # A control the user just created, before assigning any fan.
                text, css = "No fans", "InfoChip"
        self._state_chip.setText(text)
        set_chip_class(self._state_chip, css, skip_if_unchanged=True)

        if vm.curve is not None:
            self._preview.set_curve(vm.curve)
            self._preview.setVisible(True)
            self._no_curve.setVisible(False)
        else:
            self._preview.setVisible(False)
            self._no_curve.setVisible(True)
            if vm.is_read_only:
                placeholder = "No fan control available for this device"
            elif vm.is_unassigned:
                placeholder = "Not assigned to a control"
            else:
                placeholder = "No curve assigned"
            self._no_curve.setText(placeholder)

        # A read-only fan cannot be assigned to a control at all (the member
        # picker refuses it, DEC-102), so offering Edit would be a dead button.
        self._edit_btn.setVisible(not vm.is_read_only)
        self._edit_btn.setText("Assign…" if vm.is_unassigned else "Edit")
        self._edit_btn.setToolTip(
            "Open the Controls page to assign these fans to a control"
            if vm.is_unassigned
            else f"Edit “{escape(vm.label)}” on the Controls page"
        )

    def set_theme(self, tokens) -> None:
        """Forward the palette to the owner-drawn preview (DEC-109)."""
        self._preview.set_theme(tokens)
