"""Theme editor widget — colour token editing with live preview and contrast warnings."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from onlyfans.ui.theme import ThemeTokens, check_contrast_warnings, default_dark_theme

# Token display groups and their human-readable descriptions
_TOKEN_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Core",
        [
            ("app_bg", "Application background"),
            ("surface_1", "Primary panels / sidebar"),
            ("surface_2", "Cards / elevated surfaces"),
            ("surface_3", "Hover / raised surfaces"),
            ("text_primary", "Main text"),
            ("text_secondary", "Secondary text"),
            ("text_muted", "Muted / placeholder text"),
            ("accent_primary", "Primary accent colour"),
            ("accent_secondary", "Secondary accent colour"),
        ],
    ),
    (
        "Borders & Separators",
        [
            ("border_default", "Default border"),
            ("border_focus", "Focus border"),
            ("divider", "Divider lines"),
        ],
    ),
    (
        "Interactive States",
        [
            ("hover_bg", "Hover background"),
            ("pressed_bg", "Pressed / active background"),
            ("selected_bg", "Selected item background"),
            ("focus_ring", "Focus ring colour"),
            ("disabled_bg", "Disabled background"),
            ("disabled_text", "Disabled text"),
        ],
    ),
    (
        "Status",
        [
            ("status_ok", "Success / OK"),
            ("status_warn", "Warning"),
            ("status_crit", "Critical / Error"),
            ("status_info", "Info / Demo"),
        ],
    ),
    (
        "Charts",
        [
            ("chart_bg", "Chart background"),
            ("chart_grid", "Chart gridlines"),
            ("chart_axis_text", "Chart axis labels"),
            ("chart_line_primary", "Primary chart line"),
            ("chart_point", "Chart point"),
            ("chart_point_selected", "Selected point ring"),
            ("chart_point_hover", "Hover point ring"),
        ],
    ),
    (
        "Navigation",
        [
            ("nav_bg", "Sidebar background"),
            ("nav_text", "Sidebar text"),
            ("nav_text_active", "Active nav item text"),
            ("nav_item_hover", "Nav item hover"),
            ("nav_item_active", "Nav item active"),
        ],
    ),
    (
        "Inputs",
        [
            ("input_bg", "Input background"),
            ("input_text", "Input text"),
            ("input_placeholder", "Input placeholder"),
            ("input_border", "Input border"),
            ("input_border_focus", "Input focus border"),
        ],
    ),
    (
        "Tables",
        [
            ("table_header_bg", "Table header background"),
            ("table_row_bg", "Table row background"),
            ("table_row_alt_bg", "Alternate row background"),
            ("table_row_hover_bg", "Row hover background"),
            ("table_text", "Table text"),
        ],
    ),
    (
        "Dialogs",
        [
            ("modal_bg", "Dialog background"),
            ("modal_border", "Dialog border"),
            ("primary_btn_text", "Primary button text"),
        ],
    ),
]


class ColorSwatch(QPushButton):
    """A clickable colour swatch that opens a QColorDialog."""

    color_changed = Signal(str, str)  # token_name, hex_color

    def __init__(self, token_name: str, hex_color: str, parent=None) -> None:
        super().__init__(parent)
        self._token_name = token_name
        self._color = hex_color
        self.setFixedSize(32, 24)
        self.setToolTip(f"Click to change {token_name}")
        self._update_style()
        self.clicked.connect(self._pick_color)

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color
        self._update_style()

    def _update_style(self) -> None:
        self.setStyleSheet(
            f"background-color: {self._color}; "
            f"border: 1px solid #666; border-radius: 3px; "
            f"min-width: 30px; max-width: 30px;"
        )

    def _pick_color(self) -> None:
        initial = QColor(self._color)
        # Temporarily clear the app stylesheet to prevent the global
        # QWidget {} rule from corrupting QColorDialog's internal
        # custom-painted widgets (spectrum, hue strip, preview).
        # The app-level rule cascades into all child widgets and cannot
        # be overridden by dialog-level setStyleSheet().
        app = QApplication.instance()
        saved_stylesheet = app.styleSheet() if app else ""
        if app:
            app.setStyleSheet("")

        dlg = QColorDialog(initial, self.window())
        dlg.setWindowTitle(f"Choose {self._token_name}")
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
        result = dlg.exec()

        if app:
            app.setStyleSheet(saved_stylesheet)

        if result:
            color = dlg.currentColor()
            self._color = color.name()
            self._update_style()
            self.color_changed.emit(self._token_name, self._color)


class ThemePreview(QFrame):
    """Live preview panel showing sample UI elements with current theme."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("class", "Card")
        self.setMinimumHeight(200)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("Preview")
        title.setProperty("class", "PageSubtitle")
        layout.addWidget(title)

        # Sample buttons
        btn_row = QHBoxLayout()
        self._normal_btn = QPushButton("Normal")
        self._normal_btn.setObjectName("ThemePreview_Btn_normal")
        btn_row.addWidget(self._normal_btn)
        self._primary_btn = QPushButton("Primary")
        self._primary_btn.setObjectName("PrimaryButton")
        btn_row.addWidget(self._primary_btn)
        self._disabled_btn = QPushButton("Disabled")
        self._disabled_btn.setEnabled(False)
        btn_row.addWidget(self._disabled_btn)
        layout.addLayout(btn_row)

        # Sample card
        self._sample_card = QFrame()
        self._sample_card.setProperty("class", "Card")
        card_layout = QVBoxLayout(self._sample_card)
        self._card_title = QLabel("Sample Card")
        self._card_title.setStyleSheet("font-weight: bold;")
        card_layout.addWidget(self._card_title)
        self._card_body = QLabel("Body text on a card surface")
        self._card_body.setProperty("class", "PageSubtitle")
        card_layout.addWidget(self._card_body)
        layout.addWidget(self._sample_card)

        # Status chips
        chip_row = QHBoxLayout()
        self._ok_chip = QLabel("OK")
        self._ok_chip.setProperty("class", "SuccessChip")
        chip_row.addWidget(self._ok_chip)
        self._warn_chip = QLabel("Warning")
        self._warn_chip.setProperty("class", "WarningChip")
        chip_row.addWidget(self._warn_chip)
        self._crit_chip = QLabel("Critical")
        self._crit_chip.setProperty("class", "CriticalChip")
        chip_row.addWidget(self._crit_chip)
        self._info_chip = QLabel("Info")
        self._info_chip.setProperty("class", "DemoBadge")
        chip_row.addWidget(self._info_chip)
        chip_row.addStretch()
        layout.addLayout(chip_row)

        # Sample table row
        self._sample_table = QTableWidget(2, 3)
        self._sample_table.setHorizontalHeaderLabels(["Sensor", "Value", "Status"])
        self._sample_table.setMaximumHeight(80)
        from PySide6.QtWidgets import QTableWidgetItem

        self._sample_table.setItem(0, 0, QTableWidgetItem("CPU Temp"))
        self._sample_table.setItem(0, 1, QTableWidgetItem("42.0 C"))
        self._sample_table.setItem(0, 2, QTableWidgetItem("Fresh"))
        self._sample_table.setItem(1, 0, QTableWidgetItem("GPU Temp"))
        self._sample_table.setItem(1, 1, QTableWidgetItem("38.5 C"))
        self._sample_table.setItem(1, 2, QTableWidgetItem("Fresh"))
        layout.addWidget(self._sample_table)

    def apply_theme_stylesheet(self, stylesheet: str) -> None:
        """Apply stylesheet to this preview widget and its children."""
        self.setStyleSheet(stylesheet)


class ThemeEditorWidget(QWidget):
    """Full theme editor with grouped token editing, preview, and contrast warnings."""

    theme_modified = Signal(ThemeTokens)

    def __init__(self, tokens: ThemeTokens | None = None, parent=None) -> None:
        super().__init__(parent)
        self._tokens = tokens or default_dark_theme()
        self._swatches: dict[str, ColorSwatch] = {}
        self._hex_labels: dict[str, QLabel] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Left: token editor (scrollable) — minimum width prevents the
        # colour swatches from being squeezed too thin
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setMinimumWidth(360)
        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.setSpacing(12)

        for group_name, token_list in _TOKEN_GROUPS:
            group_frame = QFrame()
            group_frame.setProperty("class", "Card")
            group_layout = QVBoxLayout(group_frame)

            group_title = QLabel(group_name)
            group_title.setProperty("class", "PageSubtitle")
            group_layout.addWidget(group_title)

            grid = QGridLayout()
            grid.setSpacing(4)
            for row_idx, (token_name, description) in enumerate(token_list):
                value = getattr(self._tokens, token_name, "#000000")

                swatch = ColorSwatch(token_name, value)
                swatch.color_changed.connect(self._on_color_changed)
                self._swatches[token_name] = swatch
                grid.addWidget(swatch, row_idx, 0)

                hex_label = QLabel(value)
                hex_label.setStyleSheet("font-family: monospace; font-size: 12px;")
                hex_label.setMinimumWidth(70)
                self._hex_labels[token_name] = hex_label
                grid.addWidget(hex_label, row_idx, 1)

                desc_label = QLabel(description)
                desc_label.setProperty("class", "PageSubtitle")
                grid.addWidget(desc_label, row_idx, 2)

                reset_btn = QPushButton("R")
                reset_btn.setToolTip(f"Reset {token_name} to default")
                reset_btn.setFixedSize(24, 24)
                reset_btn.clicked.connect(lambda checked, tn=token_name: self._reset_token(tn))
                grid.addWidget(reset_btn, row_idx, 3)

            group_layout.addLayout(grid)
            editor_layout.addWidget(group_frame)

        editor_layout.addStretch()
        editor_scroll.setWidget(editor_widget)
        layout.addWidget(editor_scroll, 2)

        # Right: preview + warnings
        right = QVBoxLayout()
        right.setSpacing(12)

        self._preview = ThemePreview()
        right.addWidget(self._preview, 1)

        # Contrast warnings
        warn_frame = QFrame()
        warn_frame.setProperty("class", "Card")
        warn_layout = QVBoxLayout(warn_frame)
        warn_title = QLabel("Contrast Warnings")
        warn_title.setProperty("class", "PageSubtitle")
        warn_layout.addWidget(warn_title)
        self._warnings_label = QLabel("No warnings")
        self._warnings_label.setProperty("class", "PageSubtitle")
        self._warnings_label.setWordWrap(True)
        warn_layout.addWidget(self._warnings_label)
        right.addWidget(warn_frame)

        layout.addLayout(right, 1)

        # Initial state
        self._update_warnings()

    @property
    def tokens(self) -> ThemeTokens:
        return self._tokens

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Load a new set of tokens into the editor."""
        self._tokens = tokens
        for token_name, swatch in self._swatches.items():
            value = getattr(self._tokens, token_name, "#000000")
            swatch.set_color(value)
            self._hex_labels[token_name].setText(value)
        self._update_warnings()
        self._update_preview()

    def _on_color_changed(self, token_name: str, hex_color: str) -> None:
        setattr(self._tokens, token_name, hex_color)
        self._hex_labels[token_name].setText(hex_color)
        self._update_warnings()
        self._update_preview()
        self.theme_modified.emit(self._tokens)

    def _reset_token(self, token_name: str) -> None:
        default = default_dark_theme()
        default_value = getattr(default, token_name, "#000000")
        setattr(self._tokens, token_name, default_value)
        self._swatches[token_name].set_color(default_value)
        self._hex_labels[token_name].setText(default_value)
        self._update_warnings()
        self._update_preview()
        self.theme_modified.emit(self._tokens)

    def _update_warnings(self) -> None:
        warnings = check_contrast_warnings(self._tokens)
        if warnings:
            self._warnings_label.setText("\n".join(warnings))
            self._warnings_label.setProperty("class", "WarningChip")
        else:
            self._warnings_label.setText("No contrast issues detected")
            self._warnings_label.setProperty("class", "SuccessChip")
        self._warnings_label.style().unpolish(self._warnings_label)
        self._warnings_label.style().polish(self._warnings_label)

    def _update_preview(self) -> None:
        from onlyfans.ui.theme import build_stylesheet

        stylesheet = build_stylesheet(self._tokens)
        self._preview.apply_theme_stylesheet(stylesheet)
