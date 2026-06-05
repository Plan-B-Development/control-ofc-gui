"""Collapsible section widget — progressive-disclosure container.

A titled header that toggles the visibility of a content area, used to keep
dense diagnostic detail out of the way until the user asks for it. Unlike
``QToolBox`` (which shows exactly one page at a time), multiple
``CollapsibleSection`` instances can be expanded simultaneously — the right
behaviour when several detail groups may matter at once on a problem board.

Design notes:
- The header is a flat ``QPushButton``; the toggle indicator is a Unicode
  chevron rendered *in the header text* so it inherits the themed colour from
  the ``.CollapsibleSectionHeader`` stylesheet rule. A native ``QToolButton``
  arrow was avoided (it is painted from the widget palette, not the stylesheet
  ``color``, so it can desync from the active theme), and ``QToolButton`` also
  ignores stylesheet ``text-align`` — ``QPushButton`` honours it, so the header
  text left-aligns as a section header should.
- Expand/collapse is instant (no height animation) so widget tests stay
  deterministic — there is no timer to wait on.
- Content widgets remain children of this section and are reachable via
  ``findChild``/attribute access regardless of collapsed state, so callers can
  keep their existing widget references and tests keep working. Note that Qt's
  ``QWidget.isHidden()`` reflects a widget's *own* explicit show/hide flag, not
  whether an ancestor is collapsed, so a populated label inside a collapsed
  section still reports ``isHidden() is False``.
- An optional *persistent area* sits between the header and the collapsible
  content: widgets added via ``add_persistent_widget`` stay visible even when
  the section is collapsed, so a section can fold its detail away while keeping
  a one-line summary (and any critical alerts) on screen. (DEC-124 retired the
  readiness card's use of this — its verdict + alerts are now always-visible
  siblings — but the feature remains available to other sections.)
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QSizePolicy, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """A titled, collapsible container for progressive disclosure.

    Add collapsible content with :meth:`add_widget` / :meth:`add_layout`, and
    always-visible content (a summary or critical alerts that must stay on
    screen while folded) with :meth:`add_persistent_widget` /
    :meth:`add_persistent_layout`. Query or change the open state with
    :meth:`is_expanded` / :meth:`set_expanded`. The :attr:`toggled` signal
    emits ``True`` on expand and ``False`` on collapse.
    """

    toggled = Signal(bool)  # True when expanded, False when collapsed

    # Geometric-shapes glyphs (present in virtually every UI font): ▾ / ▸.
    _CHEVRON_EXPANDED = "▾"
    _CHEVRON_COLLAPSED = "▸"

    def __init__(
        self,
        title: str,
        object_name: str | None = None,
        *,
        expanded: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self._title = title
        self._expanded = expanded

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QPushButton()
        if object_name:
            self._header.setObjectName(f"{object_name}_Header")
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setFlat(True)
        self._header.setProperty("class", "CollapsibleSectionHeader")
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.toggled.connect(self._on_header_toggled)
        outer.addWidget(self._header)

        # Persistent area: widgets that stay visible regardless of collapsed
        # state, rendered directly under the header. Used when a section keeps
        # an at-a-glance summary (and any critical alerts) visible even while
        # collapsed. Hidden until the first persistent widget is added, so a
        # section with none renders byte-for-byte as before.
        self._persistent = QWidget()
        if object_name:
            self._persistent.setObjectName(f"{object_name}_Persistent")
        self._persistent_layout = QVBoxLayout(self._persistent)
        self._persistent_layout.setContentsMargins(10, 2, 0, 2)
        self._persistent_layout.setSpacing(8)
        self._persistent.setVisible(False)
        outer.addWidget(self._persistent)

        self._content = QWidget()
        if object_name:
            self._content.setObjectName(f"{object_name}_Content")
        self._content_layout = QVBoxLayout(self._content)
        # Indent content under the header so the grouping reads as hierarchy.
        self._content_layout.setContentsMargins(10, 2, 0, 6)
        self._content_layout.setSpacing(8)
        self._content.setVisible(expanded)
        outer.addWidget(self._content)

        self._render_header_text()

    # ── Public API ───────────────────────────────────────────────────

    def add_widget(self, widget: QWidget) -> None:
        """Append a widget to the section's content area."""
        self._content_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        """Append a nested layout to the section's content area."""
        self._content_layout.addLayout(layout)

    def add_persistent_widget(self, widget: QWidget) -> None:
        """Append a widget that stays visible regardless of collapsed state.

        Persistent widgets render between the header and the collapsible
        content; collapsing the section hides only the content, never these.
        Use for a summary line or critical alerts that must remain on screen
        when the section is folded.
        """
        self._persistent_layout.addWidget(widget)
        self._persistent.setVisible(True)

    def add_persistent_layout(self, layout) -> None:
        """Append a nested layout to the always-visible persistent area."""
        self._persistent_layout.addLayout(layout)
        self._persistent.setVisible(True)

    def is_expanded(self) -> bool:
        """Return whether the content area is currently shown."""
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """Expand or collapse the section.

        Idempotent — calling with the current state is a no-op. Used by the
        Troubleshooting tab to auto-expand a section when it holds a real problem
        the user must not miss.
        """
        if self._header.isChecked() != expanded:
            # setChecked emits toggled(), which runs _on_header_toggled.
            self._header.setChecked(expanded)
        elif self._expanded != expanded:
            # State desync guard (e.g. content visibility changed directly).
            self._apply_expanded(expanded)

    def set_title(self, title: str) -> None:
        """Replace the header title text (chevron is re-applied)."""
        self._title = title
        self._render_header_text()

    def header_button(self) -> QPushButton:
        """The clickable header button (exposed for theming/tests)."""
        return self._header

    def content_widget(self) -> QWidget:
        """The container holding the section's content widgets."""
        return self._content

    def persistent_widget(self) -> QWidget:
        """The container holding always-visible (non-collapsing) widgets."""
        return self._persistent

    # ── Internals ────────────────────────────────────────────────────

    def _on_header_toggled(self, checked: bool) -> None:
        self._apply_expanded(checked)
        self.toggled.emit(checked)

    def _apply_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._content.setVisible(expanded)
        self._render_header_text()

    def _render_header_text(self) -> None:
        chevron = self._CHEVRON_EXPANDED if self._expanded else self._CHEVRON_COLLAPSED
        # QPushButton/QAbstractButton treats a lone "&" as a mnemonic marker
        # (it would vanish from the label), so escape it to a literal "&&".
        title = self._title.replace("&", "&&")
        self._header.setText(f"{chevron}  {title}")
