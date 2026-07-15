"""Button-variant factory (DEC-208).

Sets a dynamic ``variant`` property (primary / secondary / ghost / danger)
styled by QSS, leaving the objectName free for a unique test hook.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QWidget

_VARIANTS = frozenset({"primary", "secondary", "ghost", "danger"})


def make_button(
    text: str,
    variant: str = "secondary",
    *,
    object_name: str | None = None,
    parent: QWidget | None = None,
) -> QPushButton:
    """Create a QPushButton with a redesign variant + optional unique objectName."""
    button = QPushButton(text, parent)
    if variant in _VARIANTS:
        button.setProperty("variant", variant)
    if object_name:
        button.setObjectName(object_name)
    return button
