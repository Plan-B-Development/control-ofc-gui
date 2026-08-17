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
    accessible_name: str | None = None,
    parent: QWidget | None = None,
) -> QPushButton:
    """Create a QPushButton with a redesign variant + optional unique objectName.

    ``accessible_name`` is **required in practice for a glyph-only button**
    (DEC-268) — ``+``, ``⋮``, ``→`` and friends announce as an anonymous button
    to a screen reader, and a tooltip does not fix that: Qt does not expose a
    tooltip as an accessible name, and a keyboard-only user never triggers one.
    Enforced by ``TestAccessibleNames::test_every_glyph_only_button_is_named``,
    which fails on a short button label with no name rather than trusting review
    to notice.

    A parameter here rather than a ``setAccessibleName`` call at each site,
    because the ad-hoc form is what let five of these ship unnamed: the standard
    is invisible at the call site unless the factory asks for it.
    """
    button = QPushButton(text, parent)
    if variant in _VARIANTS:
        button.setProperty("variant", variant)
    if object_name:
        button.setObjectName(object_name)
    if accessible_name:
        button.setAccessibleName(accessible_name)
    return button
