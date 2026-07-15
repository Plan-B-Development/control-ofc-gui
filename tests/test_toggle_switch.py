"""DEC-215: ToggleSwitch — an iOS-style QCheckBox subclass."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QCheckBox, QWidget

from control_ofc.ui.components.toggle_switch import ToggleSwitch


def test_is_a_checkbox(qtbot):
    t = ToggleSwitch()
    qtbot.addWidget(t)
    assert isinstance(t, QCheckBox)


def test_default_unchecked_and_toggle(qtbot):
    t = ToggleSwitch()
    qtbot.addWidget(t)
    assert t.isChecked() is False
    t.setChecked(True)
    assert t.isChecked() is True


def test_toggled_signal_on_click(qtbot):
    t = ToggleSwitch()
    qtbot.addWidget(t)
    with qtbot.waitSignal(t.toggled) as blocker:
        t.click()
    assert blocker.args == [True]


def test_constant_size_hint(qtbot):
    t = ToggleSwitch()
    qtbot.addWidget(t)
    assert t.sizeHint() == QSize(36, 20)


def test_findchild_by_checkbox_and_objectname(qtbot):
    """A settings row still resolves it via ``findChild(QCheckBox, name)``."""
    holder = QWidget()
    qtbot.addWidget(holder)
    t = ToggleSwitch(holder)
    t.setObjectName("Settings_Check_example")
    assert holder.findChild(QCheckBox, "Settings_Check_example") is t


def test_paints_in_all_states(qtbot):
    """paintEvent must not raise for checked / unchecked / disabled."""
    t = ToggleSwitch()
    qtbot.addWidget(t)
    t.resize(36, 20)
    for checked in (False, True):
        for enabled in (True, False):
            t.setChecked(checked)
            t.setEnabled(enabled)
            t.grab()  # forces a paintEvent
