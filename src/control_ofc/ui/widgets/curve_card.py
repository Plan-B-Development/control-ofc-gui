"""Curve card — compact, information-dense card for the curve library grid.

Shows: name, type badge, sensor+value, used-by, status, mini preview.
Actions via setMenu dropdown. The preview is owner-drawn (paintEvent) with a
constant font-derived size hint, so it can never inflate the card (DEC-129).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from control_ofc.services.profile_service import CurveConfig, CurveType
from control_ofc.ui.theme import active_theme, default_dark_theme
from control_ofc.ui.widgets.card_metrics import (
    DEFAULT_CARD_SIZE,
    MIN_USER_CARD_WIDTH_PX,
    card_dimensions,
)
from control_ofc.ui.widgets.card_resize import CardResizeGrip, snap_size

# QWIDGETSIZE_MAX — not exported by this PySide6 build; used to undo a fixed
# height when a user size override is cleared (DEC-129).
_QWIDGETSIZE_MAX = 16777215


class CurvePreview(QWidget):
    """Owner-drawn curve preview: graph sparkline or text summary.

    Replaces the old QLabel+QPixmap preview whose ``sizeHint`` was its pixmap:
    rendering at the current size grew the hint, the flow layout granted the
    bigger hint, and the card ratcheted taller on every pass. Painting in
    ``paintEvent`` with a constant, font-derived hint makes that loop
    structurally impossible — the default card shows a modest sparkline a few
    text lines tall, and the Expanding policy lets the preview fill whatever
    extra space a DEC-129 user resize grants.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._curve: CurveConfig | None = None
        self._theme = default_dark_theme()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(24)

    @property
    def curve(self) -> CurveConfig | None:
        return self._curve

    def set_curve(self, curve: CurveConfig) -> None:
        self._curve = curve
        self.update()

    def set_theme(self, tokens) -> None:
        self._theme = tokens
        self.update()

    def summary_text(self) -> str:
        """The text painted for linear/flat curves (empty for graph curves)."""
        curve = self._curve
        if curve is None:
            return ""
        if curve.type == CurveType.LINEAR:
            return (
                f"{curve.start_temp_c:.0f}°C→{curve.end_temp_c:.0f}°C: "
                f"{curve.start_output_pct:.0f}%→{curve.end_output_pct:.0f}%"
            )
        if curve.type == CurveType.FLAT:
            return f"Flat: {curve.flat_output_pct:.0f}%"
        return ""

    def sizeHint(self) -> QSize:
        # Constant per font — never derived from what was last painted, so
        # the old render→hint→grant→render ratchet cannot recur.
        return QSize(120, self.fontMetrics().height() * 3)

    def paintEvent(self, event) -> None:
        if self._curve is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        text = self.summary_text()
        if text:
            # Linear/flat: centered one-line summary in the muted card tone.
            painter.setPen(QPen(QColor(self._theme.text_secondary)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
            painter.end()
            return

        points = self._curve.points
        if len(points) < 2:
            painter.end()
            return

        w = self.width()
        h = self.height()
        x_min = points[0].temp_c
        x_max = points[-1].temp_c
        x_range = max(x_max - x_min, 1.0)
        pad = 3

        path = QPainterPath()
        for i, p in enumerate(points):
            px = ((p.temp_c - x_min) / x_range) * (w - 2 * pad) + pad
            py = h - pad - (p.output_pct / 100.0) * (h - 2 * pad)
            if i == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)

        painter.setPen(QPen(QColor(self._theme.accent_primary), 2))
        painter.drawPath(path)
        painter.end()


class CurveCard(QFrame):
    """Compact curve card with preview, metadata, and actions."""

    edit_requested = Signal(str)
    delete_requested = Signal(str)
    rename_requested = Signal(str)
    duplicate_requested = Signal(str)
    # DEC-129 per-card user resize: resized fires on grip release with the
    # snapped size actually applied; size_reset fires on grip double-click
    # after the card has restored its theme-derived default.
    resized = Signal(str, int, int)  # curve_id, width, height
    size_reset = Signal(str)  # curve_id

    def __init__(
        self,
        curve: CurveConfig,
        card_size: str = DEFAULT_CARD_SIZE,
        user_size: tuple[int, int] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "Card")
        self._curve = curve
        self._theme = default_dark_theme()
        # Fixed width keeps the grid columns aligned; height is a floor so the
        # card grows to fit scaled text rather than clipping rows (DEC-128).
        self._card_size_tier = card_size
        # DEC-129: persisted per-card override; None = theme-derived sizing.
        self._user_size: tuple[int, int] | None = None
        # Grip exists before the first resizeEvent (any setFixedWidth below
        # triggers one) so resizeEvent can always reposition it.
        self._grip = CardResizeGrip(self)
        self._grip.setObjectName(f"CurveCard_Grip_{curve.id}")
        self._grip.resize_finished.connect(self._on_grip_resized)
        self._grip.reset_requested.connect(self._on_grip_reset)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        # Row 1: Name + type badge + actions
        header = QHBoxLayout()
        header.setSpacing(4)
        self._name_label = QLabel(curve.name or "Unnamed")
        self._name_label.setStyleSheet("font-weight: bold; background: transparent;")
        self._name_label.setObjectName(f"CurveCard_Label_{curve.id}")
        self._name_label.setToolTip(curve.name or "Unnamed")
        header.addWidget(self._name_label, 1)

        type_label = QLabel(curve.type.value)
        type_label.setProperty("class", "CardMeta")
        type_label.setStyleSheet("background: transparent;")
        header.addWidget(type_label)

        actions_btn = QPushButton("Actions")
        actions_btn.setObjectName(f"CurveCard_Btn_actions_{curve.id}")
        actions_btn.setStyleSheet("padding: 2px 6px; background: transparent;")
        actions_menu = QMenu(actions_btn)
        actions_menu.addAction("Edit", lambda: self.edit_requested.emit(self._curve.id))
        actions_menu.addAction("Rename", lambda: self.rename_requested.emit(self._curve.id))
        actions_menu.addAction("Duplicate", lambda: self.duplicate_requested.emit(self._curve.id))
        actions_menu.addSeparator()
        actions_menu.addAction("Delete", lambda: self.delete_requested.emit(self._curve.id))
        actions_btn.setMenu(actions_menu)
        header.addWidget(actions_btn)
        layout.addLayout(header)

        # Row 2: Sensor + live value
        self._sensor_label = QLabel("No sensor")
        self._sensor_label.setProperty("class", "CardMeta")
        self._sensor_label.setStyleSheet("background: transparent;")
        self._sensor_label.setObjectName(f"CurveCard_Label_sensor_{curve.id}")
        if curve.sensor_id:
            self._sensor_label.setToolTip(f"Raw ID: {curve.sensor_id}")
        layout.addWidget(self._sensor_label)

        # Row 3: Preview — Expanding, so surplus card height (DEC-128 floor
        # or a DEC-129 user resize) grows the sparkline instead of padding
        # out the text rows.
        self._preview = CurvePreview()
        self._preview.setObjectName(f"CurveCard_Preview_{curve.id}")
        self._preview.set_theme(self._theme)
        self._preview.set_curve(curve)
        layout.addWidget(self._preview, 1)

        # Row 4: Used by + status
        footer = QHBoxLayout()
        footer.setSpacing(4)
        self._used_by_label = QLabel("Not assigned")
        self._used_by_label.setProperty("class", "CardMeta")
        self._used_by_label.setStyleSheet("background: transparent;")
        self._used_by_label.setObjectName(f"CurveCard_Label_usedBy_{curve.id}")
        footer.addWidget(self._used_by_label, 1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("background: transparent;")
        self._status_label.setObjectName(f"CurveCard_Label_status_{curve.id}")
        footer.addWidget(self._status_label)
        layout.addLayout(footer)

        self.apply_card_size(active_theme().base_font_size_pt, card_size, user_size)

    @property
    def curve(self) -> CurveConfig:
        return self._curve

    @property
    def user_size(self) -> tuple[int, int] | None:
        """The persisted per-card size override, or None for theme sizing."""
        return self._user_size

    def apply_card_size(
        self,
        base_pt: int,
        tier: str = DEFAULT_CARD_SIZE,
        user_size: tuple[int, int] | None = None,
    ) -> None:
        """Size the card from the theme base font size and a density tier.

        Without a user override: width is fixed so the flow grid stays
        column-aligned; height is a minimum floor (no maximum), so scaled-up
        text grows the card instead of clipping the preview/footer (DEC-128).

        With a user override (DEC-129): both dimensions are fixed to the
        snapped override, re-clamped to the current content minimum at every
        re-apply — so a theme/tier change clamps the override but never
        clears it. Passing ``user_size=None`` keeps any existing override;
        clearing is explicit via :meth:`clear_user_size`.
        """
        self._card_size_tier = tier
        if user_size is not None:
            self._user_size = self._snap_to_content(*user_size)
        if self._user_size is not None:
            width, height = self._snap_to_content(*self._user_size)
            self.setFixedWidth(width)
            self.setFixedHeight(height)
        else:
            width, height = card_dimensions(base_pt, tier)
            self.setFixedWidth(width)
            # Undo a previous override's fixed height before re-flooring.
            self.setMaximumHeight(_QWIDGETSIZE_MAX)
            self.setMinimumHeight(height)
        self.updateGeometry()

    def set_user_size(self, width: int, height: int) -> tuple[int, int]:
        """Apply a live user resize (grip drag), snapped and clamped.

        Returns the size actually applied so the grip can report it on
        release.
        """
        applied = self._snap_to_content(width, height)
        self._user_size = applied
        self.setFixedWidth(applied[0])
        self.setFixedHeight(applied[1])
        self.updateGeometry()
        return applied

    def clear_user_size(self) -> None:
        """Drop the per-card override and restore theme-derived sizing."""
        self._user_size = None
        self.apply_card_size(active_theme().base_font_size_pt, self._card_size_tier)

    def _snap_to_content(self, width: int, height: int) -> tuple[int, int]:
        """Snap to the shared lattice, clamped so rows can never clip."""
        return snap_size(
            width,
            height,
            MIN_USER_CARD_WIDTH_PX,
            self.layout().minimumSize().height(),
        )

    def _on_grip_resized(self, width: int, height: int) -> None:
        self.resized.emit(self._curve.id, width, height)

    def _on_grip_reset(self) -> None:
        self.clear_user_size()
        self.size_reset.emit(self._curve.id)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep the resize grip pinned to the bottom-right corner, above the
        # card content (it floats outside the layout). The preview repaints
        # itself at the new size — no re-render hook needed here (DEC-129).
        self._grip.move(self.width() - self._grip.width(), self.height() - self._grip.height())
        self._grip.raise_()

    def set_theme(self, tokens) -> None:
        """Update theme and repaint the curve preview."""
        self._theme = tokens
        self._preview.set_theme(tokens)

    def update_sensor_display(self, label: str, value_c: float | None = None) -> None:
        if value_c is not None:
            self._sensor_label.setText(f"{label} — {value_c:.1f}°C")
        else:
            self._sensor_label.setText(label if label else "No sensor")

    def set_used_by(self, role_names: list[str]) -> None:
        if role_names:
            text = ", ".join(role_names[:3])
            if len(role_names) > 3:
                text += f" +{len(role_names) - 3}"
            self._used_by_label.setText(f"Used by: {text}")
            self._used_by_label.setToolTip(", ".join(role_names))
            self._status_label.setText("Assigned")
            self._status_label.setProperty("class", "SuccessChip")
        else:
            self._used_by_label.setText("Not assigned")
            self._status_label.setText("Unassigned")
            self._status_label.setProperty("class", "PageSubtitle")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)

    def update_curve(self, curve: CurveConfig) -> None:
        self._curve = curve
        self._name_label.setText(curve.name or "Unnamed")
        self._name_label.setToolTip(curve.name or "Unnamed")
        if curve.sensor_id:
            self._sensor_label.setToolTip(f"Raw ID: {curve.sensor_id}")
        else:
            self._sensor_label.setText("No sensor")
        self._preview.set_curve(curve)
        if self._user_size is not None:
            # Content may have changed: re-clamp the user override so a
            # previously-valid size can't start clipping rows.
            self.apply_card_size(active_theme().base_font_size_pt, self._card_size_tier)
