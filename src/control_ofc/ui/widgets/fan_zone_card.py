"""Dashboard fan zone cards (DEC-179, Phase 4).

Thin Qt renderers over the pure Phase-1 fan-grouping view-model
(:mod:`control_ofc.services.fan_grouping`). :class:`FanZoneGrid` lays out one
:class:`FanGroupCard` per ``FanGroupVM`` in a wrapping flow; each card renders
its ``FanTileVM`` tiles. The cards make fan state understandable *without*
reading the raw table (refinement §10) while keeping every datum truthful:

- state chips are **text + glyph**, never colour-only (WCAG 1.4.1);
- tile detail is reachable by **click or keyboard**, never hover-only
  (WCAG 1.4.13);
- an OFFLINE tile shows ``—`` for rpm/pwm and the expected-RPM range is always
  ``—`` (the daemon does not report it — we do not invent it).

No ``FanReading`` access here: data arrives only as view-model objects. The one
piece of GUI state these touch is :class:`AppState`, for the rename
(``set_fan_alias``) and zone-assign (``set_fan_zone``) actions — mirroring the
existing alias flow rather than re-implementing persistence.

Updates are reconciled **in place** (per-card and per-tile, keyed by id) so the
1 Hz poll never tears down and rebuilds the widget tree — that would flicker,
drop focus, and close an open detail dialog.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.app_state import AppState
from control_ofc.services.fan_grouping import FanGroupVM, FanState, FanTileVM
from control_ofc.services.profile_service import (
    CONTROL_ROLE_CHASSIS,
    CONTROL_ROLE_CPU_PUMP,
    CONTROL_ROLE_GPU,
)
from control_ofc.ui.widgets.flow_layout import FlowLayout

# Per-state presentation: (glyph, qss status class). Glyph + the state's text
# label means the chip is never distinguished by colour alone (WCAG 1.4.1). The
# classes (SuccessChip / WarningChip / CriticalChip) are the same theme chips the
# summary cards use. OVERRIDE is informational, not a fault, but earns a gentle
# WarningChip because the fan is no longer following its curve.
_STATE_PRESENTATION: dict[FanState, tuple[str, str]] = {
    FanState.NORMAL: ("✓", "SuccessChip"),  # ✓
    FanState.OVERRIDE: ("✋", "WarningChip"),  # ✋ manual hold
    FanState.LOW_RPM: ("⚠", "WarningChip"),  # ⚠
    FanState.STALE: ("⏱", "WarningChip"),  # ⏱
    FanState.STALL: ("⚠", "CriticalChip"),  # ⚠
    FanState.OFFLINE: ("⊘", "CriticalChip"),  # ⊘
}

# Friendly source / role labels for tile faces and detail. Kept local (small) so
# the widget does not reach into the view-model's private maps.
_SOURCE_LABELS: dict[str, str] = {
    "openfan": "OpenFan",
    "hwmon": "Motherboard",
    "amd_gpu": "AMD GPU",
    "intel_gpu": "Intel GPU",
}
_ROLE_LABELS: dict[str, str] = {
    CONTROL_ROLE_CPU_PUMP: "CPU / Pump",
    CONTROL_ROLE_GPU: "GPU",
    CONTROL_ROLE_CHASSIS: "Chassis",
}

_EM_DASH = "—"
_UNASSIGNED_LABEL = "(Unassigned)"
_MANY_TILES = 8  # group tile count above which the tiles start collapsed


def _sanitize(token: str) -> str:
    """objectName-safe slug of a fan id (``openfan:ch00`` -> ``openfan_ch00``)."""
    body = "".join(c if c.isalnum() else "_" for c in token)
    while "__" in body:
        body = body.replace("__", "_")
    return body.strip("_") or "x"


def _restyle(widget: QWidget) -> None:
    """Re-apply QSS after a dynamic ``class`` property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class FanTile(QFrame):
    """One fan rendered as a compact tile over a :class:`FanTileVM`.

    Clickable and keyboard-activatable (Enter/Space/click); activation opens a
    read-only detail dialog carrying a rename and an assign-to-zone action.
    """

    activated = Signal(str)  # fan_id — emitted on click / keyboard activation

    def __init__(
        self,
        vm: FanTileVM,
        *,
        state: AppState | None = None,
        zone_provider: Callable[[], list[str]] | None = None,
        current_zone: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._vm = vm
        self._state = state
        self._zone_provider = zone_provider or (lambda: [])
        self._current_zone = current_zone
        self.setObjectName(f"FanZone_Tile_{_sanitize(vm.fan_id)}")
        self.setProperty("class", "FanTile")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(150)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)

        self._name_label = QLabel()
        self._name_label.setObjectName(f"FanZone_Tile_name_{_sanitize(vm.fan_id)}")
        self._name_label.setProperty("class", "FanTileName")
        self._metrics_label = QLabel()
        self._metrics_label.setObjectName(f"FanZone_Tile_metrics_{_sanitize(vm.fan_id)}")
        self._status_label = QLabel()
        self._status_label.setObjectName(f"FanZone_Tile_status_{_sanitize(vm.fan_id)}")
        for w in (self._name_label, self._metrics_label, self._status_label):
            lay.addWidget(w)

        self._render()

    # ── rendering ────────────────────────────────────────────────────
    def update_vm(self, vm: FanTileVM, *, current_zone: str = "") -> None:
        """Update in place from a fresh VM (no widget teardown)."""
        self._vm = vm
        self._current_zone = current_zone
        self._render()

    def _render(self) -> None:
        vm = self._vm
        self._name_label.setText(vm.display_name)
        self._name_label.setToolTip(vm.fan_id)
        rpm = f"{vm.rpm} rpm" if vm.rpm is not None else _EM_DASH
        pwm = f"{vm.pwm_pct}%" if vm.pwm_pct is not None else _EM_DASH
        src = _SOURCE_LABELS.get(vm.source, vm.source)
        self._metrics_label.setText(f"{rpm} · {pwm} · {src}")
        glyph, css = _STATE_PRESENTATION.get(vm.state, ("", ""))
        self._status_label.setText(f"{glyph} {vm.state.value}".strip())
        self._status_label.setProperty("class", f"FanTileStatus {css}".strip())
        # Accessible name carries the state in text so it is not colour-only.
        self.setAccessibleName(f"{vm.display_name}: {vm.state.value}")
        _restyle(self._status_label)

    # ── detail text (pure, unit-testable) ────────────────────────────
    def detail_text(self) -> str:
        """Read-only per-fan detail. Honest about what is and isn't known."""
        vm = self._vm
        src = _SOURCE_LABELS.get(vm.source, vm.source)
        role = _ROLE_LABELS.get(vm.role, vm.role) if vm.role else _EM_DASH
        rpm = f"{vm.rpm} rpm" if vm.rpm is not None else _EM_DASH
        pwm = f"{vm.pwm_pct}%" if vm.pwm_pct is not None else _EM_DASH
        lines = [
            f"Fan: {vm.display_name}",
            f"ID: {vm.fan_id}",
            f"Source: {src}",
            f"State: {vm.state.value}",
            "",
            f"RPM (measured): {rpm}",
            f"PWM (commanded): {pwm}",  # target ≈ actual: only last_commanded exists
            f"Expected RPM range: {_EM_DASH}",  # not daemon-provided — never invented
            "",
            f"Zone: {self._current_zone or _UNASSIGNED_LABEL}",
            f"Role: {role}",
            f"Controlled by daemon: {'yes' if vm.controlled_by_daemon else 'no'} (approx)",
        ]
        if vm.curve_source:
            lines.append(f"Curve sensor: {vm.curve_source}")
        if vm.age_ms is not None:
            lines.append(f"Last update: {vm.age_ms / 1000:.1f}s ago")
        return "\n".join(lines)

    # ── actions (seams for tests + dialogs) ──────────────────────────
    def apply_rename(self, name: str) -> None:
        """Apply a rename via the canonical alias flow (empty = no-op)."""
        cleaned = name.strip()
        if cleaned and self._state is not None:
            self._state.set_fan_alias(self._vm.fan_id, cleaned)

    def apply_assign(self, zone: str) -> None:
        """Assign (or, with ``""``, unassign) this fan's zone."""
        if self._state is not None:
            self._state.set_fan_zone(self._vm.fan_id, zone.strip())

    # ── interaction ──────────────────────────────────────────────────
    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self._activate()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._activate()
            event.accept()
            return
        super().keyPressEvent(event)

    def _activate(self) -> None:
        self.activated.emit(self._vm.fan_id)
        self._open_detail()

    def _open_detail(self) -> None:
        dlg = QDialog(self)
        dlg.setObjectName(f"FanZone_TileDetail_{_sanitize(self._vm.fan_id)}")
        dlg.setWindowTitle(self._vm.display_name)
        v = QVBoxLayout(dlg)
        text = QLabel(self.detail_text(), dlg)
        text.setObjectName("FanZone_TileDetail_text")
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(text)

        actions = QHBoxLayout()
        rename_btn = QPushButton("Rename…", dlg)
        rename_btn.setObjectName("FanZone_TileDetail_rename")
        rename_btn.clicked.connect(self._prompt_rename)
        assign_btn = QPushButton("Assign to zone…", dlg)
        assign_btn.setObjectName("FanZone_TileDetail_assign")
        assign_btn.clicked.connect(self._prompt_assign)
        actions.addWidget(rename_btn)
        actions.addWidget(assign_btn)
        actions.addStretch()
        v.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dlg)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        v.addWidget(buttons)
        dlg.exec()

    def _prompt_rename(self) -> None:
        new, ok = QInputDialog.getText(self, "Rename fan", "Label:", text=self._vm.display_name)
        if ok:
            self.apply_rename(new)

    def _prompt_assign(self) -> None:
        existing = sorted(self._zone_provider())
        items = [_UNASSIGNED_LABEL, *existing]
        current_idx = items.index(self._current_zone) if self._current_zone in items else 0
        choice, ok = QInputDialog.getItem(
            self,
            "Assign to zone",
            "Zone (type a new name to create one):",
            items,
            current_idx,
            True,
        )
        if ok:
            self.apply_assign("" if choice == _UNASSIGNED_LABEL else choice)


class FanGroupCard(QFrame):
    """One zone or role/source bucket: a header (label, user-zone marker, state
    chip, online/expected, average rpm/pwm) over a wrapping flow of
    :class:`FanTile` s. Tiles collapse behind a toggle when the group has many
    fans (cards stay calm)."""

    tile_activated = Signal(str)  # re-emits a child tile's fan_id

    def __init__(
        self,
        vm: FanGroupVM,
        *,
        state: AppState | None = None,
        zone_provider: Callable[[], list[str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._zone_provider = zone_provider or (lambda: [])
        self._tiles: dict[str, FanTile] = {}
        self._tiles_explicit = False  # True once the user toggles the tiles area
        self._key = vm.key
        self.setObjectName(f"FanZone_Card_{vm.key}")
        self.setProperty("class", "FanGroupCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setMinimumWidth(240)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(4)

        # Header row 1: marker + label + state chip
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self._marker = QLabel()
        self._marker.setObjectName(f"FanZone_Card_marker_{vm.key}")
        self._label = QLabel()
        self._label.setObjectName(f"FanZone_Card_label_{vm.key}")
        self._label.setProperty("class", "FanGroupTitle")
        self._chip = QLabel()
        self._chip.setObjectName(f"FanZone_Chip_state_{vm.key}")
        row1.addWidget(self._marker)
        row1.addWidget(self._label)
        row1.addStretch()
        row1.addWidget(self._chip)
        outer.addLayout(row1)

        # Header row 2: online/expected + averages + (optional) collapse toggle
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self._counts = QLabel()
        self._counts.setObjectName(f"FanZone_Card_counts_{vm.key}")
        self._avgs = QLabel()
        self._avgs.setObjectName(f"FanZone_Card_avgs_{vm.key}")
        self._toggle = QToolButton()
        self._toggle.setObjectName(f"FanZone_Card_toggle_{vm.key}")
        self._toggle.setCheckable(True)
        self._toggle.setAutoRaise(True)
        self._toggle.clicked.connect(self._on_toggle)
        row2.addWidget(self._counts)
        row2.addWidget(self._avgs)
        row2.addStretch()
        row2.addWidget(self._toggle)
        outer.addLayout(row2)

        self._tiles_container = QWidget()
        self._tiles_container.setObjectName(f"FanZone_Card_tiles_{vm.key}")
        self._tiles_flow = FlowLayout(self._tiles_container, margin=0)
        outer.addWidget(self._tiles_container)

        self.update_vm(vm)

    def update_vm(self, vm: FanGroupVM) -> None:
        """Reconcile header + tiles in place from a fresh group VM."""
        self._label.setText(vm.label)
        if vm.is_user_zone:
            self._marker.setText("★")  # ★
            self._marker.setToolTip("User-assigned zone")
            self._label.setToolTip("User-assigned zone")
        else:
            self._marker.setText("○")  # ○
            self._marker.setToolTip("Auto-grouped — assign fans to a zone to customise")
            self._label.setToolTip("Automatically grouped by role/source")

        glyph, css = _STATE_PRESENTATION.get(vm.state, ("", ""))
        self._chip.setText(f"{glyph} {vm.state.value}".strip())
        self._chip.setProperty("class", f"FanGroupChip {css}".strip())
        _restyle(self._chip)

        self._counts.setText(f"{vm.fans_online}/{vm.fans_expected} online")
        avg_parts = []
        if vm.avg_pwm_pct is not None:
            avg_parts.append(f"avg {vm.avg_pwm_pct}% PWM")
        if vm.avg_rpm is not None:
            avg_parts.append(f"{vm.avg_rpm} rpm")
        self._avgs.setText(" · ".join(avg_parts))
        self.setAccessibleName(
            f"{vm.label}: {vm.state.value}, {vm.fans_online} of {vm.fans_expected} online"
        )

        self._reconcile_tiles(vm)
        self._apply_toggle(len(vm.tiles))

    def _reconcile_tiles(self, vm: FanGroupVM) -> None:
        zone_name = vm.label if vm.is_user_zone else ""
        new_ids = [t.fan_id for t in vm.tiles]
        # Drop tiles no longer in the group.
        for fan_id in list(self._tiles):
            if fan_id not in new_ids:
                tile = self._tiles.pop(fan_id)
                tile.setParent(None)
                tile.deleteLater()
        # Empty the flow without destroying surviving tiles, then re-add in order.
        while self._tiles_flow.count():
            self._tiles_flow.takeAt(0)
        for tile_vm in vm.tiles:
            tile = self._tiles.get(tile_vm.fan_id)
            if tile is None:
                tile = FanTile(
                    tile_vm,
                    state=self._state,
                    zone_provider=self._zone_provider,
                    current_zone=zone_name,
                    parent=self._tiles_container,
                )
                tile.activated.connect(self.tile_activated)
                self._tiles[tile_vm.fan_id] = tile
            else:
                tile.update_vm(tile_vm, current_zone=zone_name)
            self._tiles_flow.addWidget(tile)
            tile.show()

    def _apply_toggle(self, tile_count: int) -> None:
        """Show/collapse the tiles area; auto-collapse big groups until the user
        decides otherwise."""
        many = tile_count > _MANY_TILES
        self._toggle.setVisible(many)
        if not many:
            self._tiles_container.setVisible(True)
            return
        if not self._tiles_explicit:
            self._toggle.setChecked(False)  # default collapsed when many
        self._sync_toggle()

    def _on_toggle(self) -> None:
        self._tiles_explicit = True
        self._sync_toggle()

    def _sync_toggle(self) -> None:
        expanded = self._toggle.isChecked()
        self._tiles_container.setVisible(expanded)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        n = len(self._tiles)
        self._toggle.setText(f" Hide {n} fans" if expanded else f" Show {n} fans")


class FanZoneGrid(QWidget):
    """Wrapping flow of :class:`FanGroupCard` s, reconciled in place each poll.

    Bind it to the dashboard with :meth:`set_groups`, passing the ordered list
    that :func:`control_ofc.services.fan_grouping.build_fan_groups` returns.
    """

    tile_activated = Signal(str)

    def __init__(
        self,
        *,
        state: AppState | None = None,
        zone_provider: Callable[[], list[str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FanZone_Grid")
        self._state = state
        self._zone_provider = zone_provider or (lambda: [])
        self._cards: dict[str, FanGroupCard] = {}
        self._flow = FlowLayout(self, margin=0, h_spacing=12, v_spacing=12)
        self._empty = QLabel("No fans are being reported yet.")
        self._empty.setObjectName("FanZone_Grid_empty")
        self._flow.addWidget(self._empty)

    def set_groups(self, groups: list[FanGroupVM]) -> None:
        new_keys = [g.key for g in groups]
        for key in list(self._cards):
            if key not in new_keys:
                card = self._cards.pop(key)
                card.setParent(None)
                card.deleteLater()
        while self._flow.count():
            self._flow.takeAt(0)
        if not groups:
            self._flow.addWidget(self._empty)
            self._empty.show()
            return
        self._empty.hide()
        for g in groups:
            card = self._cards.get(g.key)
            if card is None:
                card = FanGroupCard(
                    g, state=self._state, zone_provider=self._zone_provider, parent=self
                )
                card.tile_activated.connect(self.tile_activated)
                self._cards[g.key] = card
            else:
                card.update_vm(g)
            self._flow.addWidget(card)
            card.show()
