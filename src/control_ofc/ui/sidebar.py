"""Left navigation sidebar — 8-entry grouped nav + active-profile selector (DEC-208).

Eight entries in three groups mirror the redesign mockup. Only four
``QStackedWidget`` pages exist during the staged migration, so a :class:`NavItem`
carries both a ``page_id`` and an optional ``sub_tab`` index; the sidebar emits
``nav_activated(page_id, sub_tab)`` and ``main_window`` routes the sub-tab via a
page shim. The four "primary" entries keep ``nav_id == page_id`` so ``select_page``
and ``QButtonGroup.checkedId`` still track the page index. The brand wordmark now
lives in the top ``StatusRibbon``, so the sidebar no longer renders one.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from control_ofc.constants import (
    NAV_CONTROLS,
    NAV_DASHBOARD,
    NAV_HARDWARE,
    NAV_LOGS,
    NAV_OVERVIEW,
    NAV_SETTINGS,
    NAV_SYSTEM_STATE,
    NAV_THEME,
    PAGE_CONTROLS,
    PAGE_DASHBOARD,
    PAGE_HARDWARE,
    PAGE_LOGS,
    PAGE_OVERVIEW,
    PAGE_SETTINGS,
    PAGE_SYSTEM_STATE,
    PAGE_THEME,
)
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.buttons import make_button


@dataclass(frozen=True)
class NavItem:
    """One sidebar entry: which page (+ optional sub-tab) it opens."""

    nav_id: int
    label: str
    page_id: int
    sub_tab: int  # -1 = the page itself (no sub-tab)
    group: str  # "A" | "config" | "C"


_NAV_ITEMS: list[NavItem] = [
    NavItem(NAV_DASHBOARD, "Dashboard", PAGE_DASHBOARD, -1, "A"),
    NavItem(NAV_OVERVIEW, "Overview", PAGE_OVERVIEW, -1, "A"),
    NavItem(NAV_CONTROLS, "Controls", PAGE_CONTROLS, -1, "A"),
    NavItem(NAV_SYSTEM_STATE, "System State", PAGE_SYSTEM_STATE, -1, "config"),
    NavItem(NAV_HARDWARE, "Hardware", PAGE_HARDWARE, -1, "config"),
    NavItem(NAV_SETTINGS, "Settings", PAGE_SETTINGS, -1, "C"),
    NavItem(NAV_THEME, "Theme", PAGE_THEME, -1, "C"),
    NavItem(NAV_LOGS, "Logs", PAGE_LOGS, -1, "C"),
]


def _nav_object_name(label: str) -> str:
    return "NavButton_" + "".join(c for c in label.title() if c.isalnum())


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("Sidebar_Separator")
    line.setFrameShape(QFrame.Shape.HLine)
    return line


class Sidebar(QWidget):
    """Grouped 8-entry navigation + a bottom active-profile selector."""

    nav_activated = Signal(int, int)  # (page_id, sub_tab)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(190)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 12)
        layout.setSpacing(3)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[int, QPushButton] = {}
        self._items: dict[int, NavItem] = {item.nav_id: item for item in _NAV_ITEMS}

        prev_group: str | None = None
        for item in _NAV_ITEMS:
            if prev_group is not None and item.group != prev_group:
                layout.addWidget(_separator())
                if item.group == "config":
                    header = QLabel("CONFIG")
                    header.setObjectName("Sidebar_Group_config")
                    header.setProperty("class", "SectionHeader")
                    layout.addWidget(header)
            btn = QPushButton(item.label)
            btn.setCheckable(True)
            btn.setObjectName(_nav_object_name(item.label))
            self._group.addButton(btn, item.nav_id)
            self._buttons[item.nav_id] = btn
            layout.addWidget(btn)
            prev_group = item.group

        layout.addStretch(1)

        # Profile browser (bottom-left) — populated + wired by main_window.
        #
        # `CTRL-c`: the combo BROWSES, it does not activate. Selecting an entry
        # moves what the Controls page edits; **Apply** is what tells the daemon
        # to run it. The header therefore reads "PROFILE" rather than "ACTIVE
        # PROFILE" — the selection is whatever the user is looking at, and the
        # active one is marked by a "(active)" suffix on its own entry instead
        # (main_window._populate_sidebar_profiles). A header naming the selection
        # "active" was true only until the user touched it.
        layout.addWidget(_separator())
        profile_title = QLabel("PROFILE")
        profile_title.setObjectName("Sidebar_Label_profileTitle")
        profile_title.setProperty("class", "SectionHeader")
        layout.addWidget(profile_title)
        self.profile_combo = QComboBox()
        self.profile_combo.setObjectName("Sidebar_Combo_profile")
        name_value_control(self.profile_combo, profile_title)
        layout.addWidget(self.profile_combo)
        self.apply_profile_btn = make_button(
            "Apply", "secondary", object_name="Sidebar_Btn_applyProfile"
        )
        self.apply_profile_btn.setToolTip("Run the selected profile on the daemon")
        layout.addWidget(self.apply_profile_btn)

        # `CTRL-c`: add/remove live beside the selector. Both delegate to the
        # Controls page's existing handlers — the capability was never missing,
        # only unreachable from the one place profiles are chosen (it sat behind
        # a 32px "⋮" on a page you had to navigate away from the selector to
        # reach). Rename/Duplicate stay on that menu; these two are the pair the
        # switching workflow needs.
        profile_actions = QHBoxLayout()
        profile_actions.setContentsMargins(0, 0, 0, 0)
        profile_actions.setSpacing(4)
        self.new_profile_btn = make_button("New", "secondary", object_name="Sidebar_Btn_newProfile")
        self.new_profile_btn.setToolTip("Create a new profile")
        self.delete_profile_btn = make_button(
            "Delete", "secondary", object_name="Sidebar_Btn_deleteProfile"
        )
        self.delete_profile_btn.setToolTip("Delete the selected profile")
        profile_actions.addWidget(self.new_profile_btn)
        profile_actions.addWidget(self.delete_profile_btn)
        layout.addLayout(profile_actions)

        # About — pinned at the very bottom.
        self._about_btn = QPushButton("About")
        self._about_btn.setObjectName("NavButton_About")
        self._about_btn.setToolTip("About Control-OFC")
        self._about_btn.clicked.connect(self._show_about)
        layout.addWidget(self._about_btn)

        self._group.idToggled.connect(self._on_toggled)
        self._buttons[NAV_DASHBOARD].setChecked(True)

    def _on_toggled(self, nav_id: int, checked: bool) -> None:
        if not checked:
            return
        item = self._items.get(nav_id)
        if item is not None:
            self.nav_activated.emit(item.page_id, item.sub_tab)

    def _show_about(self) -> None:
        from control_ofc.ui.about_dialog import AboutDialog

        AboutDialog(self).exec()

    def activate_nav(self, nav_id: int) -> None:
        """Select a nav entry AND guarantee ``nav_activated`` fires, even if the
        entry was already checked — used by deep-links so the target page + sub-tab
        is always (re)applied."""
        btn = self._buttons.get(nav_id)
        if btn is None:
            return
        if btn.isChecked():
            self._on_toggled(nav_id, True)
        else:
            btn.setChecked(True)

    def select_page(self, page_id: int) -> None:
        """Check the FIRST nav entry that routes to *page_id* (DEC-209).

        As pages split out of the Diagnostics god-page the ``nav_id == page_id``
        coincidence no longer holds for every entry, so match by ``page_id``
        rather than keying ``_buttons`` by it. For the four whose nav_id still
        equals their page_id this preserves ``checkedId() == page_id``.
        """
        for item in _NAV_ITEMS:
            if item.page_id == page_id:
                btn = self._buttons.get(item.nav_id)
                if btn is not None:
                    btn.setChecked(True)
                return
