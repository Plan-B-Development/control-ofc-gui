"""DEC-238: the shared ElidedLabel primitive.

The point of the widget is that it degrades *visually* without degrading its
data — a card can render a long name inside a fixed-width tile while every
consumer that reads ``text()`` (including the untrusted-text guard) still sees
the string it was handed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from control_ofc.ui.components.labels import ElidedLabel

LONG = "Front Radiator Intake Trio (push)"


def test_text_survives_elision(qtbot):
    """The stored string is never rewritten — only the painted one is."""
    label = ElidedLabel(LONG)
    qtbot.addWidget(label)
    label.setFixedWidth(60)
    label.show()
    assert label.text() == LONG
    assert label.elided_text() != LONG
    assert label.elided_text().endswith("…")


def test_short_text_is_not_elided(qtbot):
    label = ElidedLabel("CPU")
    qtbot.addWidget(label)
    label.setFixedWidth(200)
    label.show()
    assert label.elided_text() == "CPU"


def test_minimum_size_hint_does_not_hold_the_layout_open(qtbot):
    """The whole reason the widget exists: a plain QLabel refuses to shrink below
    its full text, which is what widens one tile in a grid of uniform ones."""
    elided = ElidedLabel(LONG)
    qtbot.addWidget(elided)
    assert elided.minimumSizeHint().width() < elided.sizeHint().width()
    # Small enough to be a non-constraint, not merely "a bit smaller".
    assert elided.minimumSizeHint().width() < 20


def test_renders_as_plain_text(qtbot):
    """Names are untrusted; markup must never be reinterpreted as formatting."""
    label = ElidedLabel("<b>PWNED</b>")
    qtbot.addWidget(label)
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.text() == "<b>PWNED</b>"


def test_object_name_is_settable(qtbot):
    """Shared components take a caller-supplied objectName so two instances in
    one page do not collide in findChild/click tests."""
    a = ElidedLabel("x", object_name="Test_Label_a")
    b = ElidedLabel("x", object_name="Test_Label_b")
    qtbot.addWidget(a)
    qtbot.addWidget(b)
    assert a.objectName() == "Test_Label_a"
    assert a.objectName() != b.objectName()


def test_elide_mode_is_honoured(qtbot):
    label = ElidedLabel(LONG, mode=Qt.TextElideMode.ElideMiddle)
    qtbot.addWidget(label)
    label.setFixedWidth(60)
    label.show()
    assert "…" in label.elided_text()
    assert not label.elided_text().endswith("…")


def test_paints_without_error(qtbot):
    """paintEvent is overridden; exercise it rather than trusting it compiles."""
    label = ElidedLabel(LONG)
    qtbot.addWidget(label)
    label.setFixedWidth(60)
    label.show()
    label.grab()  # forces a real paint through the override


def test_stylesheet_box_survives_the_custom_paint(qtbot):
    """Overriding paintEvent must not cost the widget its QSS box. Qt paints the
    box outside the paint event, so this holds without an explicit drawPrimitive
    — pinned because that is load-bearing for a component meant for reuse."""
    label = ElidedLabel("x")
    qtbot.addWidget(label)
    label.setStyleSheet("border: 3px solid rgb(0, 255, 0);")
    label.resize(30, 30)
    label.show()
    image = label.grab().toImage()
    assert image.pixelColor(1, 15).green() > 200, "stylesheet border was not painted"
