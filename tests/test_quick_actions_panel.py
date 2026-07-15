"""DEC-213: QuickActionsPanel — profile-shortcut buttons."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QSizePolicy

from control_ofc.ui.widgets.quick_actions_panel import QuickActionsPanel


def test_set_profiles_builds_one_button_each(qtbot):
    panel = QuickActionsPanel()
    qtbot.addWidget(panel)
    panel.set_profiles([("p1", "Silent"), ("p2", "Performance")])
    assert panel.findChild(QPushButton, "Dashboard_Btn_quickProfile_p1") is not None
    assert panel.findChild(QPushButton, "Dashboard_Btn_quickProfile_p2") is not None


def test_click_emits_activate_requested(qtbot):
    panel = QuickActionsPanel()
    qtbot.addWidget(panel)
    panel.set_profiles([("prof_bal_01", "Balanced")])
    btn = panel.findChild(QPushButton, "Dashboard_Btn_quickProfile_prof_bal_01")
    with qtbot.waitSignal(panel.activate_requested) as blocker:
        btn.click()
    assert blocker.args == ["prof_bal_01"]


def test_empty_profiles_shows_empty_state(qtbot):
    panel = QuickActionsPanel()
    qtbot.addWidget(panel)
    panel.set_profiles([])
    assert not panel._empty.isHidden()


def test_rebuild_replaces_old_buttons(qtbot):
    panel = QuickActionsPanel()
    qtbot.addWidget(panel)
    panel.set_profiles([("p1", "A")])
    panel.set_profiles([("p2", "B")])
    assert panel.findChild(QPushButton, "Dashboard_Btn_quickProfile_p1") is None
    assert panel.findChild(QPushButton, "Dashboard_Btn_quickProfile_p2") is not None


def test_panel_resists_vertical_compression(qtbot):
    """DEC-213: compact panel holds its natural height (Minimum vertical policy) so
    a short right rail squeezes the tall sensor list, not these buttons."""
    panel = QuickActionsPanel()
    qtbot.addWidget(panel)
    assert panel.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Minimum
