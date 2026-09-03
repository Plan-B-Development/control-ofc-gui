"""A card inspecting one PWM header (AIO-MB Phase 6 §3-§6, DEC-318).

A **thin renderer** over ``services/header_inspector_view.HeaderInspectorView``.
Everything true or worded is decided in that Qt-free view-model; this file
decides only structure. Nothing here computes a floor, a role or a status.

Two structural choices worth stating, because both are Qt traps this project has
paid for:

* The details block is a ``CollapsibleSection`` from ``ui/widgets/`` (NOT
  ``ui/components/`` — it lives with the widgets), so the card is compact by
  default and the engineering detail is one click away, per §4.
* Every interactive control carries an explicit ``accessible_name`` and inherits
  the theme's focus ring (DEC-251). Nothing sets a font size or a colour here —
  those are the theme's (``CLAUDE.md § GUI component standard``).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.header_inspector_view import HeaderInspectorView, InfoRow
from control_ofc.ui.components.badges import StatusPill
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import ContentSizedCard
from control_ofc.ui.components.labels import ElidedLabel
from control_ofc.ui.widgets.collapsible_section import CollapsibleSection


def _slug(header_id: str) -> str:
    """A stable objectName fragment for one header.

    Header ids carry colons and dots (`hwmon:it8696:isa-0a40:pwm5:PUMP`), which
    make brittle objectNames and awkward selectors. Every shared component in
    this project takes a settable objectName precisely so reuse does not collide
    (`CLAUDE.md § GUI component standard`), so the card derives a unique one.
    """
    return "".join(c if c.isalnum() else "_" for c in header_id)


class PwmHeaderCard(ContentSizedCard):
    """One header, its live values, its capabilities and its diagnostics."""

    test_requested = Signal(str)  # header_id
    characterize_requested = Signal(str)  # header_id

    def __init__(self, view: HeaderInspectorView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._header_id = view.header_id
        slug = _slug(view.header_id)
        self.setObjectName(f"HeaderCard_{slug}")
        # Render caches: the label sequence each grid was built for, and the
        # value widgets to update in place while it is unchanged.
        self._live_state: _GridState | None = None
        self._detail_key: tuple | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ── Title row: name + role pill ──────────────────────────────────────
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._title = ElidedLabel(view.title, self)
        self._title.setObjectName(f"HeaderCard_Title_{slug}")
        self._title.setProperty("class", "CardTitle")
        title_row.addWidget(self._title, 1)
        self._role_pill = StatusPill(
            view.role_label,
            "warn" if view.pump_protected else "neutral",
            object_name=f"HeaderCard_Pill_role_{slug}",
        )
        self._role_pill.setAccessibleName(f"Role: {view.role_label}")
        title_row.addWidget(self._role_pill)
        root.addLayout(title_row)

        self._subtitle = QLabel(view.subtitle, self)
        self._subtitle.setObjectName(f"HeaderCard_Subtitle_{slug}")
        self._subtitle.setProperty("class", "CardMeta")
        root.addWidget(self._subtitle)

        # ── Live values ──────────────────────────────────────────────────────
        self._live_grid = QGridLayout()
        self._live_grid.setContentsMargins(0, 4, 0, 4)
        self._live_grid.setHorizontalSpacing(12)
        self._live_grid.setVerticalSpacing(3)
        self._live_grid.setColumnStretch(1, 1)
        root.addLayout(self._live_grid)

        # ── Details disclosure ───────────────────────────────────────────────
        self._details = CollapsibleSection(
            "Details", f"HeaderCard_Details_{slug}", expanded=False, parent=self
        )
        self._detail_host = QWidget(self._details)
        self._detail_layout = QVBoxLayout(self._detail_host)
        self._detail_layout.setContentsMargins(0, 0, 0, 0)
        self._detail_layout.setSpacing(8)
        self._details.add_widget(self._detail_host)
        root.addWidget(self._details)

        # ── Actions ──────────────────────────────────────────────────────────
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._test_btn = make_button(
            "Test Control",
            "secondary",
            object_name=f"HeaderCard_Btn_test_{slug}",
            accessible_name=f"Test PWM control on {view.title}",
            parent=self,
        )
        self._test_btn.clicked.connect(lambda: self.test_requested.emit(self._header_id))
        actions.addWidget(self._test_btn)

        self._char_btn = make_button(
            "Characterise",
            "secondary",
            object_name=f"HeaderCard_Btn_characterize_{slug}",
            accessible_name=f"Characterise PWM response of {view.title}",
            parent=self,
        )
        self._char_btn.clicked.connect(lambda: self.characterize_requested.emit(self._header_id))
        actions.addWidget(self._char_btn)
        actions.addStretch(1)
        root.addLayout(actions)

        self.set_view(view)

    # ── rendering ────────────────────────────────────────────────────────────

    def header_id(self) -> str:
        return self._header_id

    def set_view(self, view: HeaderInspectorView) -> None:
        """Re-render from a fresh view-model. **Called on every 1 Hz poll tick.**

        So it updates text in place and rebuilds a grid only when its *structure*
        changes — the row labels, not their values. Rebuilding unconditionally
        measured 9.3 ms per tick for eight cards and pushed ~14,600 transient
        widgets through the deferred-delete queue every 30 s, to redraw text that
        had not changed. Nothing leaked (verified over 1000 rebuilds: layouts,
        QObjects and widgets all flat), but `apply_theme` re-polishes every live
        widget, so transient population is a cost this project has already paid
        for twice (DEC-286/287).

        The details block changes only when a capability, role or reclaim count
        does, which is close to never, so it is skipped outright on most ticks.
        """
        self._header_id = view.header_id
        self._title.setText(view.title)
        self._subtitle.setText(view.subtitle)
        self._role_pill.set_text(view.role_label)
        self._role_pill.set_state("warn" if view.pump_protected else "neutral")
        self._role_pill.setAccessibleName(f"Role: {view.role_label}")

        self._live_state = _fill_grid(
            self._live_grid,
            view.live_rows,
            self,
            f"{_slug(view.header_id)}_live",
            self._live_state,
        )

        # Disabled actions explain themselves (§11) — a greyed control with no
        # reason is indistinguishable from a broken one. Set BEFORE the
        # details-unchanged early return below, because enablement tracks live
        # capability while the details block does not.
        self._test_btn.setEnabled(view.can_test)
        self._test_btn.setToolTip(view.test_disabled_reason)
        self._char_btn.setEnabled(view.can_characterize)
        self._char_btn.setToolTip(view.characterize_disabled_reason)

        # The details block is rebuilt wholesale rather than diffed — but only
        # when it actually differs. A wholesale rebuild cannot leave a stale row
        # behind the way a partial update can, and skipping it when nothing
        # changed costs one tuple comparison.
        detail_key = (view.identity_rows, view.capability_rows, view.safety_rows)
        if detail_key == self._detail_key:
            return
        self._detail_key = detail_key
        _clear_layout(self._detail_layout)
        for title, rows in (
            ("Identity", view.identity_rows),
            ("Capabilities", view.capability_rows),
            ("Classification and safety", view.safety_rows),
        ):
            if not rows:
                continue
            heading = QLabel(title, self._detail_host)
            heading.setProperty("class", "CardMeta")
            self._detail_layout.addWidget(heading)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 6)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(3)
            grid.setColumnStretch(1, 1)
            _fill_grid(
                grid, rows, self._detail_host, f"{_slug(view.header_id)}_{_slug(title)}", None
            )
            self._detail_layout.addLayout(grid)


def _clear_layout(layout: QVBoxLayout | QGridLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear_layout(child)


@dataclass
class _GridState:
    """What a rendered grid was built for, so the next tick can update in place.

    ``key`` is the row *labels* — the structure. When it matches, only the values
    changed and the existing widgets are reused; when it differs, the grid is
    rebuilt. Values are deliberately NOT part of the key: they change on almost
    every tick, and reacting to that is the whole point.
    """

    key: tuple[str, ...]
    values: list[QWidget]


def _fill_grid(
    grid: QGridLayout,
    rows: list[InfoRow],
    parent: QWidget,
    slug: str,
    state: _GridState | None,
) -> _GridState:
    """Render label/value rows, reusing the existing widgets where possible."""
    key = tuple(row.label for row in rows)

    if state is not None and state.key == key and len(state.values) == len(rows):
        for widget, row in zip(state.values, rows, strict=True):
            if isinstance(widget, StatusPill):
                widget.set_text(row.value)
                widget.set_state(row.state)
            else:
                widget.setText(row.value)
            widget.setAccessibleName(f"{row.label}: {row.value}")
            widget.setToolTip(row.note)
        return state

    _clear_layout(grid)
    values: list[QWidget] = []
    for idx, row in enumerate(rows):
        label = QLabel(row.label, parent)
        label.setObjectName(f"Row_Label_{slug}_{idx}")
        label.setProperty("class", "CardMeta")
        grid.addWidget(label, idx, 0)

        # The status row is ALWAYS a pill, even when neutral. A widget whose
        # type depends on its value could not be updated in place — the reuse
        # path above would have to swap the widget, which is the rebuild it
        # exists to avoid.
        value: QWidget
        if row.label == "Status":
            value = StatusPill(row.value, row.state, object_name=f"Row_Pill_{slug}_{idx}")
        else:
            value = QLabel(row.value, parent)
            value.setObjectName(f"Row_Value_{slug}_{idx}")
        value.setAccessibleName(f"{row.label}: {row.value}")
        value.setToolTip(row.note)
        grid.addWidget(value, idx, 1)
        values.append(value)
    return _GridState(key=key, values=values)
