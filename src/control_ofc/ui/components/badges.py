"""Filled status pill / badge (DEC-208).

A small pill with a tinted background + border + uppercase label, styled by the
``.Pill_*`` QSS classes. Replaces the previous colour-only text chips for the
redesign (the old ``.*Chip`` classes remain for existing callers).
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from control_ofc.ui.qt_util import set_chip_class

_PILL_CLASS: dict[str, str] = {
    "ok": "Pill_success",
    "success": "Pill_success",
    "warn": "Pill_warning",
    "warning": "Pill_warning",
    "crit": "Pill_critical",
    "critical": "Pill_critical",
    "info": "Pill_info",
    "neutral": "Pill_neutral",
    "muted": "Pill_neutral",
}


def pill_class_for(state: str) -> str:
    """Map a semantic state to its ``.Pill_*`` QSS class (defaults to neutral)."""
    return _PILL_CLASS.get(state, "Pill_neutral")


class StatusPill(QLabel):
    """An uppercase filled status badge whose colour follows *state*."""

    def __init__(
        self,
        text: str = "",
        state: str = "neutral",
        parent: QWidget | None = None,
        *,
        object_name: str | None = None,
    ) -> None:
        super().__init__(text.upper(), parent)
        # A settable objectName (matches SectionHeader/RadialGauge/make_button):
        # a fixed one collides wherever the pill is reused and breaks findChild
        # tests. Callers with a meaningful, single instance should pass one.
        self.setObjectName(object_name or "StatusPill")
        self._state = state
        set_chip_class(self, pill_class_for(state))

    def set_state(self, state: str) -> None:
        self._state = state
        set_chip_class(self, pill_class_for(state), skip_if_unchanged=True)

    def state(self) -> str:
        return self._state

    def set_text(self, text: str) -> None:
        self.setText(text.upper())
