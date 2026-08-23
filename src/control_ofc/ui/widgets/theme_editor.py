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
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from control_ofc.colors import is_valid_color
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.cards import SectionHeader
from control_ofc.ui.qt_util import repolish
from control_ofc.ui.theme import (
    ThemeTokens,
    active_theme,
    check_contrast_warnings,
    contrast_ratio,
    default_dark_theme,
)

# Token display groups and their human-readable descriptions
_TOKEN_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Core UI",
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
        ],
    ),
    (
        "Interactive States",
        [
            ("hover_bg", "Hover background"),
            ("pressed_bg", "Pressed / active background"),
            ("selected_bg", "Selected item background"),
            ("disabled_bg", "Disabled background"),
            ("disabled_text", "Disabled text"),
        ],
    ),
    (
        "Status Indicators",
        [
            ("status_ok", "Success / OK"),
            ("status_warn", "Warning"),
            ("status_crit", "Critical / Error"),
            ("status_info", "Info / Demo"),
        ],
    ),
    (
        "Chart Elements",
        [
            ("chart_bg", "Chart background"),
            ("chart_grid", "Chart gridlines"),
            ("chart_axis_text", "Chart axis labels"),
            ("chart_point_selected", "Selected point ring"),
            ("chart_point_hover", "Hover point ring"),
            ("chart_crosshair", "Hover crosshair"),
            ("chart_tooltip_bg", "Hover tooltip background"),
            ("chart_tooltip_border", "Hover tooltip border"),
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
            ("code_block_bg", "Inline command / code tint"),
        ],
    ),
]

# Number of chart series swatches exposed in the editor. The default theme
# ships an 8-colour palette — exposing all of them lets the user repaint the
# whole timeline graph from one place.
_CHART_SERIES_SLOTS = 8


class ColorSwatch(QPushButton):
    """A clickable colour swatch that opens a QColorDialog."""

    color_changed = Signal(str, str)  # token_name, hex_color

    def __init__(self, token_name: str, hex_color: str, parent=None) -> None:
        super().__init__(parent)
        self._token_name = token_name
        self._color = hex_color
        self.setFixedSize(32, 24)
        self.setToolTip(f"Click to change {token_name}")
        # The swatch carries no text — its whole content is the colour it shows —
        # so without a name assistive tech announces an anonymous button, once per
        # token (DEC-251). A tooltip is not an accessible name: it is not exposed
        # as one and never reaches a keyboard-only screen-reader user.
        self.setAccessibleName(f"{token_name} colour")
        self._update_style()
        self.clicked.connect(self._pick_color)

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color
        self._update_style()

    def _update_style(self) -> None:
        # Swatch outline follows the active theme's border token rather than a
        # hardcoded grey, so it stays legible in every theme.
        # DEC-255: a widget's OWN stylesheet outranks the application stylesheet
        # by origin, not specificity — so `QPushButton:focus` from the theme can
        # never reach this swatch, and it was the one control invisible to a
        # keyboard user among ~40 identical siblings. The focus ring has to live
        # in the same string. (The dialog-level note below is the same rule seen
        # from the other side.)
        t = active_theme()
        # DEC-264: the ring is drawn ON the swatch, whose colour is whatever the
        # token happens to be — so no single token can contrast with it. A fixed
        # `text_primary` ring scored 1.00:1 on the `text_primary` swatch itself
        # (ring and fill are literally the same colour) and under 3:1 on 13-20 of
        # the ~40 swatches per theme.
        #
        # DEC-266: derive the ring from the swatch's own luminance rather than
        # from theme tokens. The previous fix picked the better of `text_primary`
        # / `primary_btn_text`, which clears 3:1 only if those two are a genuine
        # light/dark pair. In Classic Blue they are not — `#e0e0e8` and `#ffffff`,
        # 1.31:1 apart — so `max()` had no dark option and 18 of 54 swatches were
        # left under threshold, including the `text_primary` swatch the original
        # fix was written for. Tokens are user-editable, so no pairing of them can
        # be guaranteed; black-vs-white can. Its worst case is the crossover grey
        # at 4.607:1, which is where the "~4.6:1" figure came from all along.
        ring = max(("#000000", "#ffffff"), key=lambda c: contrast_ratio(c, self._color))
        self.setStyleSheet(
            f"ColorSwatch {{ background-color: {self._color}; "
            f"border: 1px solid {t.border_default}; border-radius: 3px; "
            f"min-width: 30px; max-width: 30px; }}"
            f"ColorSwatch:focus {{ border: 2px solid {ring}; }}"
        )

    def _pick_color(self) -> None:
        initial = QColor(self._color)
        # Temporarily clear the app stylesheet so its rules cannot reach
        # QColorDialog's internal custom-painted widgets (spectrum, hue strip,
        # preview) — an app-level rule cascades into every child and a
        # dialog-level setStyleSheet() cannot override it. The theme palette
        # stays applied, so the dialog still follows the active theme (DEC-226).
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

        title = QLabel("UI Blueprint Preview")
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
    """Full theme editor with grouped token editing, preview, and contrast warnings.

    DEC-215 restyle: mockup card/row style + an editable hex ``QLineEdit`` beside
    each swatch (typing a valid hex applies it, like the swatch picker). Every
    ``_TOKEN_GROUPS`` group + all chart-series slots stay editable (the mockup's
    Core UI / Status Indicators / Chart Elements are the prominent cards; the rest
    follow so no token loses its editor). The ``ColorSwatch`` picker + the public
    API (``tokens`` / ``set_tokens`` / ``theme_modified``) are unchanged.
    """

    theme_modified = Signal(ThemeTokens)

    def __init__(self, tokens: ThemeTokens | None = None, parent=None) -> None:
        super().__init__(parent)
        self._tokens = tokens or default_dark_theme()
        self._swatches: dict[str, ColorSwatch] = {}
        self._hex_inputs: dict[str, QLineEdit] = {}
        # Per-series chart-palette swatches, keyed by slot index.
        self._series_swatches: dict[int, ColorSwatch] = {}
        self._series_hex_inputs: dict[int, QLineEdit] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Left: token editor (scrollable) — one card per token group.
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setMinimumWidth(360)
        editor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(12)

        for group_name, token_list in _TOKEN_GROUPS:
            card = QFrame()
            card.setProperty("class", "Card")
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(SectionHeader(group_name))
            grid = QGridLayout()
            grid.setSpacing(6)
            for row_idx, (token_name, description) in enumerate(token_list):
                value = getattr(self._tokens, token_name, "#000000")
                self._add_color_row(grid, row_idx, token_name, description, value)
            card_layout.addLayout(grid)
            editor_layout.addWidget(card)

        # Chart series palette (a list-of-hex, so it's built by slot index).
        series_card = QFrame()
        series_card.setProperty("class", "Card")
        series_layout = QVBoxLayout(series_card)
        series_layout.addWidget(SectionHeader("Chart Series"))
        series_grid = QGridLayout()
        series_grid.setSpacing(6)
        for slot in range(_CHART_SERIES_SLOTS):
            value = (
                self._tokens.chart_series[slot]
                if slot < len(self._tokens.chart_series)
                else "#888888"
            )
            self._add_series_row(series_grid, slot, value)
        series_layout.addLayout(series_grid)
        editor_layout.addWidget(series_card)

        editor_layout.addStretch()
        editor_scroll.setWidget(editor_widget)
        layout.addWidget(editor_scroll, 2)

        # Right: UI blueprint preview + contrast diagnostics.
        right = QVBoxLayout()
        right.setSpacing(12)
        self._preview = ThemePreview()
        right.addWidget(self._preview, 1)

        warn_frame = QFrame()
        warn_frame.setProperty("class", "Card")
        warn_layout = QVBoxLayout(warn_frame)
        warn_layout.addWidget(SectionHeader("Contrast Diagnostics"))
        self._warnings_label = QLabel("No contrast issues detected (WCAG AA pass).")
        self._warnings_label.setProperty("class", "PageSubtitle")
        self._warnings_label.setWordWrap(True)
        warn_layout.addWidget(self._warnings_label)
        right.addWidget(warn_frame)

        layout.addLayout(right, 1)
        self._update_warnings()

    # ─── Row builders ────────────────────────────────────────────────

    def _hex_input(self, value: str, accessible_name: str) -> QLineEdit:
        """One hex field, named for the specific thing it sets (273-g phase 2).

        `accessible_name` is a parameter rather than derived here because the two
        call sites key on different things — a theme token and a chart-series
        slot — and a single shared name would collide across the ~54 fields this
        builds. Naming inside the helper (rather than at each call site) is what
        lets one edit cover both, and it is what the AST lint keys on: its
        allowlist entry names the `edit` variable in *this* function.
        """
        edit = QLineEdit(value)
        edit.setStyleSheet("font-family: monospace;")
        edit.setMaxLength(9)  # #RRGGBBAA
        edit.setMinimumWidth(84)
        edit.setMaximumWidth(100)
        name_value_control(edit, accessible_name)
        return edit

    def _add_color_row(self, grid, row_idx, token_name, description, value) -> None:
        swatch = ColorSwatch(token_name, value)
        swatch.color_changed.connect(self._on_color_changed)
        self._swatches[token_name] = swatch
        grid.addWidget(swatch, row_idx, 0)

        hex_input = self._hex_input(value, f"{token_name} hex value")
        hex_input.editingFinished.connect(lambda tn=token_name: self._on_hex_edited(tn))
        self._hex_inputs[token_name] = hex_input
        grid.addWidget(hex_input, row_idx, 1)

        desc = QLabel(description)
        desc.setProperty("class", "CardMeta")
        grid.addWidget(desc, row_idx, 2)
        grid.setColumnStretch(2, 1)

        reset = QPushButton("↺")  # ↺
        reset.setToolTip(f"Reset {token_name} to default")
        # "↺" announces as an anonymous symbol, and there is one of these per
        # token — a screen-reader user would hear the same meaningless glyph
        # dozens of times down the page (DEC-251).
        reset.setAccessibleName(f"Reset {token_name} to default")
        # DEC-258: was a hard 24x24 at every theme font size, so the glyph
        # clipped at the SHIPPED DEFAULT and worse as text scaled — once per
        # token, roughly fifty times down the page. Sized from the font instead.
        _size = max(24, round(self.fontMetrics().height() * 1.6))
        reset.setFixedSize(_size, _size)
        reset.clicked.connect(lambda _checked, tn=token_name: self._reset_token(tn))
        grid.addWidget(reset, row_idx, 3)

    def _add_series_row(self, grid, slot, value) -> None:
        swatch = ColorSwatch(f"chart_series[{slot}]", value)
        swatch.color_changed.connect(lambda _n, hx, s=slot: self._on_series_color_changed(s, hx))
        self._series_swatches[slot] = swatch
        grid.addWidget(swatch, slot, 0)

        hex_input = self._hex_input(value, f"Series {slot + 1} hex value")
        hex_input.editingFinished.connect(lambda s=slot: self._on_series_hex_edited(s))
        self._series_hex_inputs[slot] = hex_input
        grid.addWidget(hex_input, slot, 1)

        desc = QLabel(f"Series #{slot + 1}")
        desc.setProperty("class", "CardMeta")
        grid.addWidget(desc, slot, 2)
        grid.setColumnStretch(2, 1)

    # ─── API ─────────────────────────────────────────────────────────

    @property
    def tokens(self) -> ThemeTokens:
        return self._tokens

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Load a new set of tokens into the editor (silent — never re-emits)."""
        self._tokens = tokens
        for token_name, swatch in self._swatches.items():
            value = getattr(self._tokens, token_name, "#000000")
            swatch.set_color(value)
            self._hex_inputs[token_name].setText(value)
        for slot, swatch in self._series_swatches.items():
            value = (
                self._tokens.chart_series[slot]
                if slot < len(self._tokens.chart_series)
                else "#888888"
            )
            swatch.set_color(value)
            self._series_hex_inputs[slot].setText(value)
        self._update_warnings()
        self._update_preview()

    # ─── Mutation ────────────────────────────────────────────────────

    def _apply_hex(self, token_name: str, hex_color: str) -> None:
        setattr(self._tokens, token_name, hex_color)
        self._swatches[token_name].set_color(hex_color)
        if self._hex_inputs[token_name].text() != hex_color:
            self._hex_inputs[token_name].setText(hex_color)
        self._update_warnings()
        self._update_preview()
        self.theme_modified.emit(self._tokens)

    def _on_color_changed(self, token_name: str, hex_color: str) -> None:
        self._apply_hex(token_name, hex_color)

    def _on_hex_edited(self, token_name: str) -> None:
        text = self._hex_inputs[token_name].text().strip().upper()
        if is_valid_color(text):
            self._apply_hex(token_name, text)
        else:
            # Reject an invalid hex — revert the field to the live token value.
            self._hex_inputs[token_name].setText(getattr(self._tokens, token_name, "#000000"))

    def _apply_series_hex(self, slot: int, hex_color: str) -> None:
        # Pad the list if the loaded theme had fewer entries than slots.
        while len(self._tokens.chart_series) <= slot:
            self._tokens.chart_series.append("#888888")
        self._tokens.chart_series[slot] = hex_color
        self._series_swatches[slot].set_color(hex_color)
        if self._series_hex_inputs[slot].text() != hex_color:
            self._series_hex_inputs[slot].setText(hex_color)
        self._update_preview()
        self.theme_modified.emit(self._tokens)

    def _on_series_color_changed(self, slot: int, hex_color: str) -> None:
        self._apply_series_hex(slot, hex_color)

    def _on_series_hex_edited(self, slot: int) -> None:
        text = self._series_hex_inputs[slot].text().strip().upper()
        if is_valid_color(text):
            self._apply_series_hex(slot, text)
        else:
            cur = (
                self._tokens.chart_series[slot]
                if slot < len(self._tokens.chart_series)
                else "#888888"
            )
            self._series_hex_inputs[slot].setText(cur)

    def _reset_token(self, token_name: str) -> None:
        default_value = getattr(default_dark_theme(), token_name, "#000000")
        self._apply_hex(token_name, default_value)

    def _update_warnings(self) -> None:
        warnings = check_contrast_warnings(self._tokens)
        if warnings:
            self._warnings_label.setText("\n".join(warnings))
            self._warnings_label.setProperty("class", "WarningChip")
        else:
            self._warnings_label.setText("No contrast issues detected (WCAG AA pass).")
            self._warnings_label.setProperty("class", "SuccessChip")
        repolish(self._warnings_label)

    def _update_preview(self) -> None:
        from control_ofc.ui.theme import build_stylesheet

        stylesheet = build_stylesheet(self._tokens)
        self._preview.apply_theme_stylesheet(stylesheet)
