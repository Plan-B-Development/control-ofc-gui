"""A card presenting one cooling device as an assembly (AIO-MB Phase 6 §2).

A **thin renderer** over ``services/cooling_device_view.CoolingDeviceView`` —
the view-model Phase 4 built and deliberately left unrendered ("Phase 4 builds
the model; Phase 6 owns the card" — this file is that card). Nothing here decides a floor, a role, a
strategy or whether coolant telemetry is a problem.

The point of the card is §2's: show the cooler as one thing, rather than making
the user reassemble it from hwmon channel names. Two rules it must not break:

* **Missing coolant telemetry is normal, not an error.** A motherboard-connected
  AIO has none. It renders as a neutral note in the wording the view-model
  chose, never as a warning (§18).
* **Deleting a device is destructive and confirmed.** It removes the topology,
  never the headers, and never a floor — a pump keeps its protection from its
  role, not from its membership (DEC-316).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.cooling_device_view import CoolingDeviceView, CoolingMemberRow
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import ContentSizedCard, SectionHeader
from control_ofc.ui.components.labels import ElidedLabel


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text)


class CoolingDeviceCard(ContentSizedCard):
    """One AIO / cooling assembly: pump, radiator fans, sensor, strategy."""

    view_headers_requested = Signal(str)  # device_id
    characterize_pump_requested = Signal(str)  # member_id
    start_validation_requested = Signal(str)  # device_id
    edit_requested = Signal(str)  # device_id
    forget_requested = Signal(str)  # device_id

    def __init__(self, view: CoolingDeviceView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device_id = view.device_id
        self._pump_member = view.pump.member_id if view.pump else ""
        slug = _slug(view.device_id)
        self.setObjectName(f"CoolingDeviceCard_{slug}")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._header = SectionHeader(
            view.name, self, object_name=f"CoolingDeviceCard_Header_{slug}"
        )
        self._status_pill = StatusPill(
            "Normal", "ok", object_name=f"CoolingDeviceCard_Pill_status_{slug}"
        )
        self._header.add_trailing(self._status_pill)
        root.addWidget(self._header)

        self._kind = QLabel(view.kind_label, self)
        self._kind.setObjectName(f"CoolingDeviceCard_Kind_{slug}")
        self._kind.setProperty("class", "CardMeta")
        root.addWidget(self._kind)

        self._members_host = QWidget(self)
        self._members_layout = QVBoxLayout(self._members_host)
        self._members_layout.setContentsMargins(0, 4, 0, 4)
        self._members_layout.setSpacing(6)
        root.addWidget(self._members_host)

        self._facts = QGridLayout()
        self._facts.setContentsMargins(0, 0, 0, 0)
        self._facts.setHorizontalSpacing(12)
        self._facts.setVerticalSpacing(3)
        self._facts.setColumnStretch(1, 1)
        root.addLayout(self._facts)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for text, name, signal_name, accessible in (
            (
                "View Headers",
                "viewHeaders",
                "view_headers_requested",
                "View this device's PWM headers",
            ),
            (
                "Characterise Pump",
                "charPump",
                "characterize_pump_requested",
                "Characterise the pump's PWM response",
            ),
            (
                "Start Validation",
                "validate",
                "start_validation_requested",
                "Start a validation session for this device",
            ),
            (
                "Edit Configuration",
                "edit",
                "edit_requested",
                "Edit this cooling device's configuration",
            ),
        ):
            btn = make_button(
                text,
                "secondary",
                object_name=f"CoolingDeviceCard_Btn_{name}_{slug}",
                accessible_name=accessible,
                parent=self,
            )
            btn.clicked.connect(self._emitter(signal_name))
            actions.addWidget(btn)
            setattr(self, f"_btn_{name}", btn)

        self._btn_forget = make_button(
            "Forget Device",
            "danger",
            object_name=f"CoolingDeviceCard_Btn_forget_{slug}",
            accessible_name="Forget this cooling device's topology",
            parent=self,
        )
        self._btn_forget.clicked.connect(self._confirm_forget)
        actions.addWidget(self._btn_forget)
        actions.addStretch(1)
        root.addLayout(actions)

        self.set_view(view)

    def device_id(self) -> str:
        return self._device_id

    def _emitter(self, signal_name: str):
        def emit() -> None:
            # The pump action carries the MEMBER id, because that is what the
            # characterisation endpoint addresses; everything else carries the
            # device id.
            payload = (
                self._pump_member
                if signal_name == "characterize_pump_requested"
                else self._device_id
            )
            getattr(self, signal_name).emit(payload)

        return emit

    def _confirm_forget(self) -> None:
        """Confirm before discarding the topology (closes register row AIO4-d).

        Worded to say what is and is not lost. Forgetting a device removes
        metadata only: the headers stay, and a pump keeps its 30% floor because
        that comes from its ROLE, never from device membership (DEC-316). A
        dialog implying the fans become unprotected would be actively misleading.
        """
        answer = QMessageBox.question(
            self,
            "Forget cooling device?",
            f"Forget the topology for “{self._header.title()}”?\n\n"
            "This removes only how the headers are grouped. The headers "
            "themselves, their assigned roles and the pump safety floor are "
            "unaffected, and no fan changes speed.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.forget_requested.emit(self._device_id)

    def set_view(self, view: CoolingDeviceView) -> None:
        """Re-render from a fresh view-model."""
        self._device_id = view.device_id
        self._pump_member = view.pump.member_id if view.pump else ""
        self._kind.setText(view.kind_label)

        # A missing member is the one genuinely wrong state on this card; the
        # view-model already decided that, so the pill just reflects it.
        state = view.state if view.state in ("ok", "warn", "critical") else "neutral"
        text = "Normal" if state == "ok" else "Needs attention"
        self._status_pill.set_text(text)
        self._status_pill.set_state(state)
        self._status_pill.setAccessibleName(f"Device status: {text}")

        _clear(self._members_layout)
        for row in view.all_rows:
            self._members_layout.addWidget(self._member_widget(row))

        _clear(self._facts)
        facts = [
            ("Control Sensor", f"{view.sensor_label}  {view.sensor_temp_text}".strip()),
            ("Pump Strategy", view.strategy_text),
            (
                "Coolant Telemetry",
                view.coolant_temp_text if view.coolant_available else "Unavailable",
            ),
            ("Device Policy", view.policy_label or "—"),
        ]
        for idx, (label, value) in enumerate(facts):
            lbl = QLabel(label, self)
            lbl.setProperty("class", "CardMeta")
            self._facts.addWidget(lbl, idx, 0)
            val = QLabel(value, self)
            val.setObjectName(f"CoolingDeviceCard_Fact_{_slug(label)}")
            if label == "Coolant Telemetry" and not view.coolant_available:
                # Explicitly NOT a warning: the brief calls this out twice.
                val.setToolTip(view.coolant_note)
            self._facts.addWidget(val, idx, 1)

        self._btn_charPump.setEnabled(bool(self._pump_member))
        self._btn_charPump.setToolTip(
            "" if self._pump_member else "This device has no pump member assigned."
        )

    def _member_widget(self, row: CoolingMemberRow) -> QWidget:
        host = QWidget(self._members_host)
        host.setObjectName(f"CoolingDeviceCard_Member_{_slug(row.member_id)}")
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        role = QLabel(row.role_label, host)
        role.setProperty("class", "CardMeta")
        layout.addWidget(role)

        name = ElidedLabel(row.label, host)
        name.setObjectName(f"CoolingDeviceCard_MemberName_{_slug(row.member_id)}")
        layout.addWidget(name, 1)

        # RPM, then the two PWM axes kept apart (§6). A single merged number
        # would hide precisely the mismatch this card exists to reveal.
        for text, key in (
            (row.rpm_text, "rpm"),
            (row.requested_text, "req"),
            (row.readback_text, "rb"),
        ):
            value = QLabel(text, host)
            value.setObjectName(f"CoolingDeviceCard_Member_{key}_{_slug(row.member_id)}")
            layout.addWidget(value)

        if row.missing:
            pill = StatusPill(
                "Not found",
                "warn",
                object_name=f"CoolingDeviceCard_MemberPill_{_slug(row.member_id)}",
            )
            pill.setAccessibleName(f"{row.label}: not found on this system")
            layout.addWidget(pill)
        return host


def _clear(layout: QVBoxLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear(child)
