"""Tests for colour-string validation (is_valid_color, DEC-137)."""

from __future__ import annotations

import pytest

from control_ofc.colors import is_valid_color


@pytest.mark.parametrize(
    "value",
    ["#abc", "#abcd", "#aabbcc", "#aabbccdd", "#000000aa", "#FFFFFF", "#FfF"],
)
def test_accepts_valid_hex(value):
    assert is_valid_color(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "red",
        "#xyz",
        "#12",
        "#12345",
        "#1234567",
        "aabbcc",  # missing leading '#'
        "rgb(1,2,3)",
        "rgba(0,0,0,0.5)",
        "} * { color: red }",
        "#aabbcc; color: red",
        "",
        "   ",
        123,
        None,
        ["#fff"],
        {"a": "#fff"},
    ],
)
def test_rejects_invalid(value):
    assert is_valid_color(value) is False
