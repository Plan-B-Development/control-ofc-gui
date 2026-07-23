"""Member editor dialog — assign physical fan outputs to a logical control.

DEC-214 restyle: built over the shared ``ModalDialog`` frame with the mockup's
Available Outputs ↔ Selected Members transfer list (source-prefixed rows with live
RPM + "Assigned" dimming + →/← buttons), live counts, and a Cancel / Apply Changes
footer. The constructor signature, objectNames, and ``get_members()`` are preserved.
Per-fan RPM is shown only when the caller supplies it (an ``rpm`` key per output) —
never fabricated.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from control_ofc.services.profile_service import ControlMember
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.dialog import ModalDialog


class MemberEditorDialog(ModalDialog):
    """Dialog for editing which physical outputs belong to a logical control."""

    def __init__(
        self,
        current_members: list[ControlMember],
        available_outputs: list[dict],  # [{id, source, label, rpm?}, ...]
        assigned_elsewhere: dict[str, str] | None = None,  # fan_id -> role_name
        role_name: str = "",
        parent=None,
        display_name: Callable[[str, str], str] | None = None,
    ) -> None:
        super().__init__(f"Edit Role: {role_name}" if role_name else "Edit Members", parent)
        self.setMinimumSize(560, 400)

        self._result_members: list[ControlMember] = []
        # DEC-228: (member_id, cached member_label) -> name to show.
        self._display_name = display_name or (lambda mid, label: label or mid)
        # Live RPM per output id (None = present-but-no-fan); absent id = unknown.
        self._rpm_by_id = {out["id"]: out.get("rpm") for out in available_outputs if "rpm" in out}

        layout = self.body_layout()

        # Instructions
        label = QLabel("Assign physical fan outputs to this control group.")
        label.setProperty("class", "PageSubtitle")
        layout.addWidget(label)

        # Two lists side by side
        lists = QHBoxLayout()

        # Left: available (header with a live "N found" count)
        left = QVBoxLayout()
        avail_header = QHBoxLayout()
        avail_header.addWidget(QLabel("Available Outputs"), 1)
        self._available_count = QLabel("")
        self._available_count.setProperty("class", "CardMeta")
        avail_header.addWidget(self._available_count)
        left.addLayout(avail_header)
        self._available_list = QListWidget()
        self._available_list.setObjectName("MemberEditor_List_available")
        self._available_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        left.addWidget(self._available_list)
        lists.addLayout(left)

        # Center: add/remove buttons (mockup arrows)
        center = QVBoxLayout()
        center.addStretch()
        self._add_btn = make_button("→", "secondary", object_name="MemberEditor_Btn_add")
        self._add_btn.setToolTip("Add selected outputs to this control")
        self._add_btn.setFixedWidth(40)
        self._add_btn.clicked.connect(self._on_add)
        center.addWidget(self._add_btn)
        self._remove_btn = make_button("←", "secondary", object_name="MemberEditor_Btn_remove")
        self._remove_btn.setToolTip("Remove selected outputs from this control")
        self._remove_btn.setFixedWidth(40)
        self._remove_btn.clicked.connect(self._on_remove)
        center.addWidget(self._remove_btn)
        center.addStretch()
        lists.addLayout(center)

        # Right: selected (header with a live "N assigned" count)
        right = QVBoxLayout()
        sel_header = QHBoxLayout()
        sel_header.addWidget(QLabel("Selected Members"), 1)
        self._selected_count = QLabel("")
        self._selected_count.setProperty("class", "CardMeta")
        sel_header.addWidget(self._selected_count)
        right.addLayout(sel_header)
        self._selected_list = QListWidget()
        self._selected_list.setObjectName("MemberEditor_List_selected")
        self._selected_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        right.addWidget(self._selected_list)
        lists.addLayout(right)

        layout.addLayout(lists)

        # Footer: multi-select hint (left) | Cancel | Apply Changes
        hint = QLabel("Multi-select with Shift/Ctrl")
        hint.setObjectName("MemberEditor_Label_hint")
        hint.setProperty("class", "CardMeta")
        self._footer_layout.insertWidget(0, hint)
        cancel_btn = self.add_footer_button("Cancel", "ghost")
        cancel_btn.clicked.connect(self.reject)
        apply_btn = self.add_footer_button(
            "Apply Changes", "primary", object_name="MemberEditor_Btn_apply"
        )
        apply_btn.clicked.connect(self.accept)

        # Populate
        current_ids = {m.member_id for m in current_members}
        self._all_outputs = available_outputs

        assigned = assigned_elsewhere or {}
        for out in available_outputs:
            if out["id"] not in current_ids:
                label_text = f"[{out['source']}] {out['label'] or out['id']}"
                label_text += self._rpm_suffix(out["id"])
                role_name_for = assigned.get(out["id"])
                if role_name_for:
                    label_text += f"  (Assigned to: {role_name_for})"
                item = QListWidgetItem(label_text)
                item.setData(Qt.ItemDataRole.UserRole, out)
                if role_name_for:
                    item.setFlags(
                        item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled
                    )
                    item.setToolTip(f"Already assigned to fan role: {role_name_for}")
                elif out.get("tooltip"):
                    item.setToolTip(out["tooltip"])
                self._available_list.addItem(item)

        for m in current_members:
            shown = self._display_name(m.member_id, m.member_label)
            text = f"[{m.source}] {shown}" + self._rpm_suffix(m.member_id)
            item = QListWidgetItem(text)
            item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "id": m.member_id,
                    "source": m.source,
                    "label": m.member_label,
                },
            )
            self._selected_list.addItem(item)

        self._update_counts()

    def _rpm_suffix(self, item_id: str) -> str:
        """Live-RPM suffix for a row, or empty when the caller gave no reading."""
        if item_id not in self._rpm_by_id:
            return ""
        rpm = self._rpm_by_id[item_id]
        return f" · {rpm} RPM" if rpm is not None else " · no fan"

    def _update_counts(self) -> None:
        self._available_count.setText(f"{self._available_list.count()} found")
        self._selected_count.setText(f"{self._selected_list.count()} assigned")

    def _on_add(self) -> None:
        for item in self._available_list.selectedItems():
            row = self._available_list.row(item)
            taken = self._available_list.takeItem(row)
            self._selected_list.addItem(taken)
        self._update_counts()

    def _on_remove(self) -> None:
        for item in self._selected_list.selectedItems():
            row = self._selected_list.row(item)
            taken = self._selected_list.takeItem(row)
            self._available_list.addItem(taken)
        self._update_counts()

    def get_members(self) -> list[ControlMember]:
        """Return the edited member list (call after accept)."""
        members = []
        for i in range(self._selected_list.count()):
            item = self._selected_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            members.append(
                ControlMember(
                    source=data["source"],
                    member_id=data["id"],
                    # DEC-228: persist the *undecorated* name. The picker's
                    # "label" carries display badges ("(read-only)",
                    # "(no fan detected)", "(AIO pump)") that describe hardware
                    # state, not the user's name for the fan — and member_label
                    # feeds infer_member_role, which sets the DEC-095/162
                    # CPU/pump PWM floor. A badge must never reach it.
                    member_label=data.get("clean_label") or data.get("label", ""),
                )
            )
        return members
