"""DEC-208: shared component library — badges, buttons, cards, section header,
dense table, segmented control, and the modal-dialog base.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QTableWidget, QWidget

from control_ofc.ui.components.badges import StatusPill, pill_class_for
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader
from control_ofc.ui.components.dialog import ModalDialog
from control_ofc.ui.components.tables import apply_dense_table

# ── badges ──


def test_pill_class_map():
    assert pill_class_for("ok") == "Pill_success"
    assert pill_class_for("warning") == "Pill_warning"
    assert pill_class_for("critical") == "Pill_critical"
    assert pill_class_for("info") == "Pill_info"
    assert pill_class_for("anything-else") == "Pill_neutral"


def test_status_pill_is_uppercase_and_tracks_state(qtbot):
    pill = StatusPill("fresh", "ok")
    qtbot.addWidget(pill)
    assert pill.text() == "FRESH"
    assert pill.property("class") == "Pill_success"
    pill.set_state("critical")
    assert pill.state() == "critical"
    assert pill.property("class") == "Pill_critical"


def test_status_pill_object_name_param(qtbot):
    # Matches the make_button/RadialGauge/SectionHeader convention: default keeps
    # the shared name; a caller with a meaningful single instance sets a unique
    # one so reused pills don't collide on a hardcoded objectName.
    default = StatusPill("x", "ok")
    qtbot.addWidget(default)
    assert default.objectName() == "StatusPill"
    named = StatusPill("x", "ok", object_name="Foo_Pill_bar")
    qtbot.addWidget(named)
    assert named.objectName() == "Foo_Pill_bar"


# ── buttons ──


def test_make_button_sets_variant_and_objectname(qtbot):
    btn = make_button("Save", "primary", object_name="MyBtn")
    qtbot.addWidget(btn)
    assert isinstance(btn, QPushButton)
    assert btn.property("variant") == "primary"
    assert btn.objectName() == "MyBtn"


def test_make_button_unknown_variant_is_ignored(qtbot):
    btn = make_button("X", "bogus")
    qtbot.addWidget(btn)
    assert btn.property("variant") is None


# ── cards + section header ──


def test_card_class(qtbot):
    card = Card()
    qtbot.addWidget(card)
    assert card.property("class") == "Card"


def test_section_header(qtbot):
    header = SectionHeader("Fan Array")
    qtbot.addWidget(header)
    assert header.title() == "FAN ARRAY"
    assert header.objectName() == "SectionHeader_FanArray"


def test_section_header_default_lead_is_bar(qtbot):
    from PySide6.QtWidgets import QFrame

    header = SectionHeader("Fan Array")
    qtbot.addWidget(header)
    assert isinstance(header._bar, QFrame)
    assert header._bar.property("class") == "SectionBar"


def test_section_header_step_badge(qtbot):
    """DEC-233: a numbered step replaces the plain bar with a filled badge."""
    from PySide6.QtWidgets import QLabel

    header = SectionHeader("Assign Roles", step=1)
    qtbot.addWidget(header)
    assert isinstance(header._bar, QLabel)
    assert header._bar.text() == "1"
    assert header._bar.property("class") == "StepBadge"


# ── dense table ──


def test_apply_dense_table(qtbot):
    table = QTableWidget(2, 3)
    qtbot.addWidget(table)
    apply_dense_table(table)
    assert table.property("class") == "DenseTable"
    assert not table.showGrid()
    assert table.verticalHeader().isHidden()


# ── modal dialog ──


def test_modal_dialog_structure(qtbot):
    dlg = ModalDialog("Edit Role")
    qtbot.addWidget(dlg)
    assert dlg.objectName() == "ModalDialog"
    save = dlg.add_footer_button("Save", "primary", object_name="Dlg_Save")
    assert save.property("variant") == "primary"
    assert save.objectName() == "Dlg_Save"
    body = QWidget()
    dlg.body_layout().addWidget(body)
    assert body.parent() is dlg._body


def test_modal_dialog_scrim_lifecycle(qtbot):
    # Exercise scrim create/destroy directly — showing a modal QDialog under the
    # offscreen platform is exactly what the autouse modal guard exists to avoid.
    host = QWidget()
    host.resize(400, 300)
    qtbot.addWidget(host)
    dlg = ModalDialog("Edit", parent=host)
    qtbot.addWidget(dlg)
    assert dlg._scrim is None
    dlg._ensure_scrim()
    assert dlg._scrim is not None  # veil created over the parent window
    assert dlg._scrim.objectName() == "ModalScrim"
    dlg._remove_scrim()
    assert dlg._scrim is None
