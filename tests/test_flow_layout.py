"""FlowLayout.insertWidget must behave like QBoxLayout::insertWidget."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QFrame, QWidget

from control_ofc.ui.widgets.flow_layout import FlowLayout


def _shown_host(qtbot) -> tuple[QWidget, FlowLayout]:
    host = QWidget()
    qtbot.addWidget(host)
    layout = FlowLayout(host)
    host.resize(400, 300)
    host.show()
    qtbot.waitExposed(host)
    return host, layout


def test_insert_into_visible_host_shows_the_widget(qtbot):
    host, layout = _shown_host(qtbot)
    child = QFrame(host)
    assert child.isHidden()  # Qt never auto-shows a child of a visible parent
    layout.insertWidget(0, child)
    QApplication.processEvents()
    assert layout.indexOf(child) == 0
    assert child.isVisibleTo(host)


def test_insert_reparents_an_unparented_widget(qtbot):
    host, layout = _shown_host(qtbot)
    child = QFrame()
    layout.insertWidget(0, child)
    QApplication.processEvents()
    assert child.parentWidget() is host
    assert child.isVisibleTo(host)


def test_explicitly_hidden_widget_stays_hidden(qtbot):
    host, layout = _shown_host(qtbot)
    child = QFrame(host)
    child.hide()
    layout.insertWidget(0, child)
    QApplication.processEvents()
    assert layout.indexOf(child) == 0
    assert not child.isVisibleTo(host)


def test_reinsert_keeps_order_and_visibility(qtbot):
    host, layout = _shown_host(qtbot)
    a, b = QFrame(host), QFrame(host)
    layout.addWidget(a)
    layout.addWidget(b)
    QApplication.processEvents()
    layout.removeWidget(b)
    layout.insertWidget(0, b)
    QApplication.processEvents()
    assert [layout.itemAt(i).widget() for i in range(layout.count())] == [b, a]
    assert a.isVisibleTo(host) and b.isVisibleTo(host)
