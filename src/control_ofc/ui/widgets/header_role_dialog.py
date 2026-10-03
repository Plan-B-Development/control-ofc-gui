"""Set a header's role (DEC-444) — a thin dialog over ``services/header_role_view``.

Opened from the Hardware page's header card and from the PWM Test Report's scope
page; both route the result through ``HardwarePage.change_header_role``, so there
is one write path, one confirmation and one outcome message.

A dialog rather than an inline combo box: the card re-renders every second, and
a role write must come from a deliberate choice, never from a programmatic index
change (``QComboBox.currentIndexChanged`` fires for both).

Also home to :func:`confirm_remove_pump_protection`, the one confirmation every
flow that can remove a user-assigned pump role asks — this picker, the Fan
Wizard's Liquid Cooling step and Configure AIO. A rule living inside one
consumer is a rule the others cannot follow (DEC-276).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QLabel,
    QMessageBox,
    QRadioButton,
    QWidget,
)

from control_ofc.api.models import HwmonHeader, OpenFanRole
from control_ofc.services.header_inspector_view import role_label, role_source_label
from control_ofc.services.header_role_view import (
    ROLE_CHOICES,
    RoleChoice,
    current_choice,
    plan_role_change,
)
from control_ofc.ui.components.dialog import ModalDialog


def _token_slug(token: str | None) -> str:
    return token or "none"


class HeaderRoleDialog(ModalDialog):
    """Radio choices with their effect; Apply only when something would change."""

    def __init__(
        self,
        header: HwmonHeader | OpenFanRole,
        display_name: str,
        *,
        label_caveat: str = "",
        choices: tuple[RoleChoice, ...] = ROLE_CHOICES,
        intro_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(f"Set role — {display_name}", parent)
        self.setObjectName("HeaderRole_Dialog")
        self._header = header
        body = self.body_layout()
        body.setSpacing(8)

        # `ROLE-f`: an OpenFan channel passes its own intro — it has no hardware
        # label for an assignment to sit on top of.
        intro = QLabel(
            intro_text
            or (
                f"What does {display_name} drive? This sets your assignment only: a role "
                "that comes from the hardware label or the chip stays underneath it, and "
                "the daemon keeps protecting a header its label names a pump."
            ),
            self,
        )
        intro.setObjectName("HeaderRole_Label_intro")
        intro.setWordWrap(True)
        body.addWidget(intro)

        now = QLabel(
            f"Now: {role_label(header.role)} ({role_source_label(header.role_source)}).",
            self,
        )
        now.setObjectName("HeaderRole_Label_current")
        now.setProperty("class", "CardMeta")
        body.addWidget(now)

        if label_caveat:
            caveat = QLabel(label_caveat, self)
            caveat.setObjectName("HeaderRole_Label_caveat")
            caveat.setProperty("class", "WarningChip")
            caveat.setWordWrap(True)
            body.addWidget(caveat)

        self._group = QButtonGroup(self)
        self._radios: dict[str | None, QRadioButton] = {}
        selected = current_choice(header)
        for choice in choices:
            slug = _token_slug(choice.token)
            radio = QRadioButton(choice.label, self)
            radio.setObjectName(f"HeaderRole_Radio_{slug}")
            radio.setAccessibleName(f"{choice.label}: {choice.effect}")
            radio.setChecked(choice.token == selected)
            self._group.addButton(radio)
            self._radios[choice.token] = radio
            body.addWidget(radio)
            effect = QLabel(choice.effect, self)
            effect.setObjectName(f"HeaderRole_Label_effect_{slug}")
            effect.setProperty("class", "CardMeta")
            effect.setWordWrap(True)
            effect.setContentsMargins(24, 0, 0, 4)
            body.addWidget(effect)
        body.addStretch(1)

        cancel = self.add_footer_button("Cancel", "ghost", object_name="HeaderRole_Btn_cancel")
        cancel.clicked.connect(self.reject)
        self._apply_btn = self.add_footer_button(
            "Apply", "primary", object_name="HeaderRole_Btn_apply"
        )
        self._apply_btn.clicked.connect(self.accept)
        self._group.buttonToggled.connect(self._sync_apply)
        self._sync_apply()
        self.resize(560, 520)

    def chosen_role(self) -> str | None:
        for token, radio in self._radios.items():
            if radio.isChecked():
                return token
        return current_choice(self._header)

    def _sync_apply(self, *_args) -> None:
        noop = plan_role_change(self._header, self.chosen_role()).noop
        self._apply_btn.setEnabled(not noop)
        self._apply_btn.setToolTip("Nothing would change." if noop else "")


def confirm_remove_pump_protection(parent: QWidget | None, names: list[str]) -> bool:
    """Ask before removing a pump role the user assigned (DEC-312 Decision 11).

    The ONLY role write that can lower a floor, so the only one that asks. The
    copy names the protection being removed rather than implying it: a user who
    reads "clear the pump role" does not necessarily know that this is what
    stops the fan wizard driving it to 0.
    """
    listed = "\n".join(f"• {n}" for n in names)
    answer = QMessageBox.question(
        parent,
        "Remove pump protection from a header?",
        "You previously named this as the pump:\n\n"
        f"{listed}\n\n"
        "Clearing that role removes its pump protection — the daemon will no "
        "longer hold it above the 30% pump floor, and it may be stopped "
        "during fan identification.\n\n"
        "Clear it?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes
