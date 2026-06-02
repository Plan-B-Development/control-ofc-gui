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
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QSizePolicy, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """A titled, collapsible container for progressive disclosure.

    Add content with :meth:`add_widget` / :meth:`add_layout`. Query or change
    the open state with :meth:`is_expanded` / :meth:`set_expanded`. The
    :attr:`toggled` signal emits ``True`` on expand and ``False`` on collapse.
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

    def is_expanded(self) -> bool:
        """Return whether the content area is currently shown."""
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """Expand or collapse the section.

        Idempotent — calling with the current state is a no-op. Used by the
        Fans tab to auto-expand a section when it holds a real problem the
        user must not miss.
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
