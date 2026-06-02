"""Tests for the CollapsibleSection progressive-disclosure widget (DEC-112)."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from control_ofc.ui.widgets.collapsible_section import CollapsibleSection


def _make(qtbot, **kwargs) -> CollapsibleSection:
    section = CollapsibleSection(kwargs.pop("title", "Section"), **kwargs)
    qtbot.addWidget(section)
    return section


class TestInitialState:
    def test_default_is_collapsed(self, qtbot):
        section = _make(qtbot, object_name="Sec_a")
        assert section.is_expanded() is False
        assert section.content_widget().isVisibleTo(section) is False

    def test_expanded_when_requested(self, qtbot):
        section = _make(qtbot, object_name="Sec_b", expanded=True)
        assert section.is_expanded() is True
        # Content is shown when the section starts expanded (own visibility
        # flag is clear — actual on-screen visibility depends on the parent).
        assert section.content_widget().isHidden() is False

    def test_object_names_derived(self, qtbot):
        section = _make(qtbot, object_name="Sec_c")
        assert section.objectName() == "Sec_c"
        assert section.header_button().objectName() == "Sec_c_Header"
        assert section.content_widget().objectName() == "Sec_c_Content"

    def test_no_object_name_is_allowed(self, qtbot):
        # Should not raise and should not stamp empty derived names.
        section = _make(qtbot, object_name=None)
        assert section.header_button().objectName() == ""

    def test_header_text_contains_title_and_chevron(self, qtbot):
        section = _make(qtbot, title="Detected hardware", object_name="Sec_d")
        text = section.header_button().text()
        assert "Detected hardware" in text
        assert CollapsibleSection._CHEVRON_COLLAPSED in text
        assert CollapsibleSection._CHEVRON_EXPANDED not in text

    def test_ampersand_in_title_is_escaped(self, qtbot):
        # A lone "&" is a QPushButton mnemonic marker and would vanish from
        # the label (e.g. "Thermal safety & GPU"); the widget must escape it.
        section = _make(qtbot, title="Thermal safety & GPU", object_name="Sec_amp")
        assert "&&" in section.header_button().text()


class TestToggle:
    def test_click_expands_and_collapses(self, qtbot):
        section = _make(qtbot, object_name="Sec_e")
        emitted: list[bool] = []
        section.toggled.connect(emitted.append)

        section.header_button().click()
        assert section.is_expanded() is True
        assert section.content_widget().isHidden() is False
        assert emitted == [True]

        section.header_button().click()
        assert section.is_expanded() is False
        assert section.content_widget().isVisibleTo(section) is False
        assert emitted == [True, False]

    def test_chevron_flips_on_toggle(self, qtbot):
        section = _make(qtbot, title="Thermal", object_name="Sec_f")
        section.set_expanded(True)
        assert CollapsibleSection._CHEVRON_EXPANDED in section.header_button().text()
        section.set_expanded(False)
        assert CollapsibleSection._CHEVRON_COLLAPSED in section.header_button().text()

    def test_set_expanded_emits_only_on_change(self, qtbot):
        section = _make(qtbot, object_name="Sec_g")
        emitted: list[bool] = []
        section.toggled.connect(emitted.append)

        # Already collapsed → no emission.
        section.set_expanded(False)
        assert emitted == []

        section.set_expanded(True)
        assert emitted == [True]

        # Idempotent re-expand → no extra emission.
        section.set_expanded(True)
        assert emitted == [True]

    def test_set_title_preserves_chevron(self, qtbot):
        section = _make(qtbot, title="Old", object_name="Sec_h", expanded=True)
        section.set_title("New title")
        text = section.header_button().text()
        assert "New title" in text
        assert "Old" not in text
        assert CollapsibleSection._CHEVRON_EXPANDED in text


class TestContent:
    def test_added_widget_is_reachable_via_findchild(self, qtbot):
        section = _make(qtbot, object_name="Sec_i")
        label = QLabel("payload")
        label.setObjectName("Payload_label")
        section.add_widget(label)
        # Recursive findChild reaches it regardless of collapsed state.
        assert section.findChild(QLabel, "Payload_label") is label

    def test_child_own_hidden_flag_is_independent_of_collapse(self, qtbot):
        # This is the load-bearing invariant the Fans-tab refactor relies on:
        # a child's isHidden() reflects ITS OWN explicit show/hide flag, not
        # whether an ancestor section is collapsed. The diagnostics tests
        # assert isHidden() on labels that now live inside collapsed sections.
        section = _make(qtbot, object_name="Sec_j")  # collapsed
        label = QLabel("gated")
        section.add_widget(label)

        label.setVisible(True)
        assert label.isHidden() is False
        label.setVisible(False)
        assert label.isHidden() is True
