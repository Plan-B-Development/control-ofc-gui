"""Tests for MemberEditorDialog — exclusive membership enforcement."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from onlyfans.services.profile_service import ControlMember
from onlyfans.ui.widgets.member_editor import MemberEditorDialog


@pytest.fixture()
def available():
    return [
        {"id": "openfan:ch00", "source": "openfan", "label": "Front Intake 1"},
        {"id": "openfan:ch01", "source": "openfan", "label": "Front Intake 2"},
        {"id": "hwmon:cpu_fan", "source": "hwmon", "label": "CPU Fan"},
    ]


class TestExclusiveMembership:
    def test_assigned_fan_is_disabled(self, qtbot, available):
        """Fan assigned to another role is shown but disabled."""
        assigned_elsewhere = {"openfan:ch00": "Intake"}
        dlg = MemberEditorDialog([], available, assigned_elsewhere)
        qtbot.addWidget(dlg)

        # Find the item for ch00
        for i in range(dlg._available_list.count()):
            item = dlg._available_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data["id"] == "openfan:ch00":
                assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
                assert "Intake" in item.text()
                break
        else:
            pytest.fail("openfan:ch00 not found in available list")

    def test_unassigned_fan_is_enabled(self, qtbot, available):
        """Fan not assigned elsewhere remains selectable."""
        assigned_elsewhere = {"openfan:ch00": "Intake"}
        dlg = MemberEditorDialog([], available, assigned_elsewhere)
        qtbot.addWidget(dlg)

        for i in range(dlg._available_list.count()):
            item = dlg._available_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data["id"] == "openfan:ch01":
                assert item.flags() & Qt.ItemFlag.ItemIsEnabled
                break

    def test_no_assigned_all_enabled(self, qtbot, available):
        """Without assigned_elsewhere, all fans are enabled."""
        dlg = MemberEditorDialog([], available)
        qtbot.addWidget(dlg)

        for i in range(dlg._available_list.count()):
            item = dlg._available_list.item(i)
            assert item.flags() & Qt.ItemFlag.ItemIsEnabled


class TestMemberEditorBasics:
    def test_get_members_returns_selected(self, qtbot, available):
        """Selected list maps to ControlMember objects."""
        existing = [ControlMember(source="openfan", member_id="openfan:ch00", member_label="Fan 0")]
        dlg = MemberEditorDialog(existing, available)
        qtbot.addWidget(dlg)

        result = dlg.get_members()
        assert len(result) == 1
        assert result[0].member_id == "openfan:ch00"
