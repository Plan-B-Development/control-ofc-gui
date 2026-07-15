"""DEC-215: ThemeEditorWidget — editable hex inputs + full token coverage."""

from __future__ import annotations

import pytest

from control_ofc.ui.theme import default_dark_theme
from control_ofc.ui.widgets.theme_editor import (
    _CHART_SERIES_SLOTS,
    _TOKEN_GROUPS,
    ThemeEditorWidget,
)


@pytest.fixture()
def editor(qtbot):
    w = ThemeEditorWidget()
    qtbot.addWidget(w)
    return w


def test_every_token_group_has_a_swatch_and_hex_input(editor):
    """DEC-215 CRITICAL: the restyle must not drop any editable token."""
    for _group, token_list in _TOKEN_GROUPS:
        for token_name, _desc in token_list:
            assert token_name in editor._swatches, f"missing swatch for {token_name}"
            assert token_name in editor._hex_inputs, f"missing hex input for {token_name}"


def test_all_chart_series_slots_editable(editor):
    for slot in range(_CHART_SERIES_SLOTS):
        assert slot in editor._series_swatches
        assert slot in editor._series_hex_inputs


def test_valid_hex_edit_applies_and_emits(editor, qtbot):
    field = editor._hex_inputs["accent_primary"]
    field.setText("#123456")
    with qtbot.waitSignal(editor.theme_modified):
        field.editingFinished.emit()
    assert editor.tokens.accent_primary == "#123456"


def test_invalid_hex_edit_reverts(editor):
    original = editor.tokens.app_bg
    field = editor._hex_inputs["app_bg"]
    field.setText("not-a-colour")
    field.editingFinished.emit()
    assert editor.tokens.app_bg == original  # unchanged
    assert field.text() == original  # field reverted


def test_series_hex_edit_applies(editor):
    field = editor._series_hex_inputs[0]
    field.setText("#ABCDEF")
    field.editingFinished.emit()
    assert editor.tokens.chart_series[0] == "#ABCDEF"


def test_set_tokens_updates_inputs(editor):
    tokens = default_dark_theme()
    tokens.accent_primary = "#0F0F0F"
    editor.set_tokens(tokens)
    assert editor._hex_inputs["accent_primary"].text() == "#0F0F0F"


def test_reset_token_restores_default(editor):
    editor._hex_inputs["accent_primary"].setText("#010203")
    editor._hex_inputs["accent_primary"].editingFinished.emit()
    editor._reset_token("accent_primary")
    assert editor.tokens.accent_primary == default_dark_theme().accent_primary
