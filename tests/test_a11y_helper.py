"""``ui/components/a11y.name_value_control`` — the shared naming rule (273-g).

Every assertion here reads the **announced** name through ``QAccessible``, never
``accessibleName()``. That distinction is the whole point of the helper: on a
non-editable ``QComboBox`` Qt's Unix ``QAccessibleComboBox::text(Name)`` falls
through to the current item and discards the property, so a property assertion
passes on a combo that announces nothing useful. It shipped as one once
(DEC-271), and the first version of this very helper had the same bug — a string
-named combo announced nothing until the buddy fallback was added.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QProgressBar, QSpinBox, QWidget

from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.toggle_switch import ToggleSwitch


def announced(widget) -> str:
    """What assistive tech would use to say what *widget* is.

    A Name distinct from the Value is a real name; otherwise fall back to the
    ``Label`` relation a ``setBuddy`` publishes, which is what AT-SPI exports as
    *labelled-by* and what Orca reads for a combo box.
    """
    iface = QAccessible.queryAccessibleInterface(widget)
    if iface is None:
        return ""
    name = iface.text(QAccessible.Text.Name)
    value = iface.text(QAccessible.Text.Value)
    if name and name != value:
        return name
    for target, flags in iface.relations():
        if flags & QAccessible.RelationFlag.Label:
            return target.text(QAccessible.Text.Name)
    return ""


@pytest.fixture
def host(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    return w


def test_a_visible_label_becomes_the_announced_name(host):
    combo = QComboBox(host)
    combo.addItem("Default Dark")
    label = QLabel("Theme:", host)

    name_value_control(combo, label)

    # The buddy path announces the LABEL's own visible text, colon and all —
    # rewriting a label the user can see would be a worse trade than a spoken
    # colon, so the helper's strip deliberately reaches only the property.
    assert announced(combo) == "Theme:"
    assert label.buddy() is combo, "the buddy is the half Orca actually reads"


def test_a_string_label_still_announces_without_a_visible_label(host):
    """The regression the app-wide sweep caught while 273-g was being written.

    ``setAccessibleName`` alone is silently discarded on a non-editable combo, so
    a control with no visible label to buddy announced nothing at all. A hidden
    proxy label parented to the control fixes it — measured to announce
    identically to a visible buddy.
    """
    combo = QComboBox(host)
    combo.addItem("Default Dark")
    combo.setObjectName("Test_Combo_theme")

    name_value_control(combo, "Theme")

    assert announced(combo) == "Theme", (
        "a combo named only by property announces its current item — the "
        "property is discarded by Qt on Unix"
    )


def test_the_property_alone_would_not_have_been_enough(host):
    """Pins the premise the helper exists for, so it cannot be quietly 'simplified'.

    If this ever passes, Qt's behaviour changed and the buddy half could in
    principle be dropped — but only then, and deliberately.
    """
    combo = QComboBox(host)
    combo.addItem("Default Dark")
    combo.setAccessibleName("Theme")

    assert announced(combo) == "", (
        "setAccessibleName alone now works on a combo — re-evaluate the two-call "
        "rule in components/a11y.py and docs/03 before relying on it"
    )


def test_a_trailing_colon_is_stripped(host):
    spin = QSpinBox(host)
    label = QLabel("Poll interval:", host)

    name_value_control(spin, label)

    assert announced(spin) == "Poll interval", '"Poll interval colon" is not an improvement'


def test_two_string_named_controls_do_not_collide(host):
    """The proxy label needs a unique objectName like every other widget.

    A fixed fallback string collided the moment a second control took this path,
    which is exactly what happened during the sweep.
    """
    first = QLineEdit(host)
    first.setObjectName("Test_Edit_first")
    second = QLineEdit(host)
    second.setObjectName("Test_Edit_second")

    name_value_control(first, "Search sensors")
    name_value_control(second, "Filter messages")

    proxies = {
        lbl.objectName()
        for lbl in host.findChildren(QLabel)
        if lbl.objectName().endswith("_A11yLabel")
    }
    assert len(proxies) == 2, f"proxy objectNames collided: {proxies}"
    assert announced(first) == "Search sensors"
    assert announced(second) == "Filter messages"


def test_a_control_named_before_its_objectname_still_gets_a_distinct_proxy(host):
    """Call order must not matter.

    The helper runs at construction and a caller may set the objectName on the
    next line. Deriving the proxy name from the label text when the control has
    none is what keeps that from producing a generic, collision-prone name — the
    bug this caught on the sensor-series search box.
    """
    edit = QLineEdit(host)  # deliberately no objectName yet

    name_value_control(edit, "Search sensors")
    edit.setObjectName("Test_Edit_late")

    proxy = next(
        lbl for lbl in host.findChildren(QLabel) if lbl.objectName().endswith("_A11yLabel")
    )
    assert proxy.objectName() == "Search_sensors_A11yLabel"
    assert announced(edit) == "Search sensors"


def test_a_toggle_switch_routes_to_its_own_label_setter(host):
    toggle = ToggleSwitch(host)
    label = QLabel("Start minimised:", host)

    name_value_control(toggle, label)

    assert "Start minimised" in announced(toggle)


def test_an_empty_label_is_a_no_op(host):
    combo = QComboBox(host)
    combo.addItem("Default Dark")

    name_value_control(combo, "   ")

    assert not combo.findChildren(QLabel), "no proxy should be created for an empty name"
    assert combo.accessibleName() == ""


def test_a_widget_that_is_not_a_value_control_is_left_alone(host):
    """A progress bar shows a value nobody sets, so naming it is not this
    helper's job — and giving it a name Qt would ignore is worse than none."""
    bar = QProgressBar(host)

    name_value_control(bar, "Progress")

    assert bar.accessibleName() == ""
    assert not bar.findChildren(QLabel)


def test_naming_the_same_control_twice_does_not_duplicate_the_proxy(host):
    """The docstring promises idempotence; the string path did not deliver it.

    A second call minted another QLabel with an IDENTICAL objectName, breaking
    the unique-objectName rule and any `findChild` on that name. No call site
    does this today — which is precisely why it needed a test rather than trust.
    """
    edit = QLineEdit(host)
    edit.setObjectName("Test_Edit_search")

    name_value_control(edit, "Search sensors")
    name_value_control(edit, "Search sensors")

    proxies = [lbl for lbl in edit.findChildren(QLabel) if lbl.objectName().endswith("_A11yLabel")]
    assert len(proxies) == 1, (
        f"a second call duplicated the proxy: {[p.objectName() for p in proxies]}"
    )
    assert announced(edit) == "Search sensors"


def test_renaming_a_control_updates_the_existing_proxy(host):
    """Reuse must re-label, not silently keep the first name."""
    combo = QComboBox(host)
    combo.addItem("Default Dark")
    combo.setObjectName("Test_Combo_x")

    name_value_control(combo, "Theme")
    name_value_control(combo, "Colour scheme")

    assert announced(combo) == "Colour scheme"
