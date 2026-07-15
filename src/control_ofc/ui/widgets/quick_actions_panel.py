"""Quick Actions panel — one-click activation of saved profiles (DEC-213).

The mockup's Silent Mode / Max Perf / Auto Curve / Emergency map to nothing real,
so this honest replacement surfaces the profiles that actually exist: one button
per saved profile that emits ``activate_requested(profile_id)`` (the page routes it
to ``ProfileService.activate``). No timers, no writes here.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_") or "profile"


class QuickActionsPanel(Card):
    """A grid of profile-shortcut buttons driven by the real saved profiles."""

    activate_requested = Signal(str)  # profile id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Dashboard_Panel_quickActions")
        # Compact panel: hold its natural height so the tall sensor list absorbs a
        # short-rail squeeze rather than compressing these buttons (DEC-213).
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.addWidget(
            SectionHeader("Quick Actions", object_name="Dashboard_SectionHeader_quickActions")
        )
        self._grid_holder = QWidget()
        self._grid = QGridLayout(self._grid_holder)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        layout.addWidget(self._grid_holder)
        self._empty = QLabel("No saved profiles.")
        self._empty.setObjectName("Dashboard_Label_quickActionsEmpty")
        self._empty.setProperty("class", "CardMeta")
        self._empty.setVisible(False)
        layout.addWidget(self._empty)

    def set_profiles(self, profiles: list[tuple[str, str]]) -> None:
        """(Re)build one activate-button per ``(profile_id, name)``."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)  # detach now so findChild can't see a stale button
                widget.deleteLater()
        if not profiles:
            self._empty.setVisible(True)
            self._grid_holder.setVisible(False)
            return
        self._empty.setVisible(False)
        self._grid_holder.setVisible(True)
        for i, (profile_id, name) in enumerate(profiles):
            btn = make_button(
                name, "secondary", object_name=f"Dashboard_Btn_quickProfile_{_slug(profile_id)}"
            )
            btn.setToolTip(f"Activate the '{name}' profile")
            btn.clicked.connect(lambda _=False, pid=profile_id: self.activate_requested.emit(pid))
            self._grid.addWidget(btn, i // 2, i % 2)
