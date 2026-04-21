"""Member editor dialog — assign physical fan outputs to a logical control."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from control_ofc.services.profile_service import ControlMember


class MemberEditorDialog(QDialog):
    """Dialog for editing which physical outputs belong to a logical control."""

    def __init__(
        self,
        current_members: list[ControlMember],
        available_outputs: list[dict],  # [{id, source, label}, ...]
        assigned_elsewhere: dict[str, str] | None = None,  # fan_id -> role_name
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Members")
        self.setMinimumSize(500, 350)

        self._result_members: list[ControlMember] = []

        layout = QVBoxLayout(self)

        # Instructions
        label = QLabel("Assign physical fan outputs to this control group.")
        label.setProperty("class", "PageSubtitle")
        layout.addWidget(label)

        # Two lists side by side
        lists = QHBoxLayout()

        # Left: available
        left = QVBoxLayout()
        left.addWidget(QLabel("Available Outputs"))
        self._available_list = QListWidget()
        self._available_list.setObjectName("MemberEditor_List_available")
        self._available_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        left.addWidget(self._available_list)
        lists.addLayout(left)

        # Center: add/remove buttons
        center = QVBoxLayout()
        center.addStretch()
        self._add_btn = QPushButton(">")
        self._add_btn.setObjectName("MemberEditor_Btn_add")
        self._add_btn.setToolTip("Add selected outputs to this control")
        self._add_btn.setFixedWidth(40)
        self._add_btn.clicked.connect(self._on_add)
        center.addWidget(self._add_btn)
        self._remove_btn = QPushButton("<")
        self._remove_btn.setObjectName("MemberEditor_Btn_remove")
        self._remove_btn.setToolTip("Remove selected outputs from this control")
        self._remove_btn.setFixedWidth(40)
        self._remove_btn.clicked.connect(self._on_remove)
        center.addWidget(self._remove_btn)
        center.addStretch()
        lists.addLayout(center)

        # Right: selected
        right = QVBoxLayout()
        right.addWidget(QLabel("Selected Members"))
        self._selected_list = QListWidget()
        self._selected_list.setObjectName("MemberEditor_List_selected")
        self._selected_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        right.addWidget(self._selected_list)
        lists.addLayout(right)

        layout.addLayout(lists)

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Apply")
        ok_btn.setObjectName("MemberEditor_Btn_apply")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        # Populate
        current_ids = {m.member_id for m in current_members}
        self._all_outputs = available_outputs

        assigned = assigned_elsewhere or {}
        for out in available_outputs:
            if out["id"] not in current_ids:
                label_text = f"[{out['source']}] {out['label'] or out['id']}"
                role_name = assigned.get(out["id"])
                if role_name:
                    label_text += f"  (Assigned to: {role_name})"
                item = QListWidgetItem(label_text)
                item.setData(Qt.ItemDataRole.UserRole, out)
                if role_name:
                    item.setFlags(
                        item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled
                    )
                    item.setToolTip(f"Already assigned to fan role: {role_name}")
                elif out.get("tooltip"):
                    item.setToolTip(out["tooltip"])
                self._available_list.addItem(item)

        for m in current_members:
            item = QListWidgetItem(f"[{m.source}] {m.member_label or m.member_id}")
            item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "id": m.member_id,
                    "source": m.source,
                    "label": m.member_label,
                },
            )
            self._selected_list.addItem(item)

    def _on_add(self) -> None:
        for item in self._available_list.selectedItems():
            row = self._available_list.row(item)
            taken = self._available_list.takeItem(row)
            self._selected_list.addItem(taken)

    def _on_remove(self) -> None:
        for item in self._selected_list.selectedItems():
            row = self._selected_list.row(item)
            taken = self._selected_list.takeItem(row)
            self._available_list.addItem(taken)

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
                    member_label=data.get("label", ""),
                )
            )
        return members
