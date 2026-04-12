"""Token-based theme system.

Every colour in the application is driven by named tokens. The default dark
theme provides the baseline. Users can customise, save, load, import, and
export themes via the Theme Editor in Settings.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Migration map: old token name -> new token name
# ---------------------------------------------------------------------------
_TOKEN_MIGRATION: dict[str, str] = {
    "window_bg": "app_bg",
    "panel_bg": "surface_1",
    "raised_surface": "surface_2",
    "border": "border_default",
    "success": "status_ok",
    "warning": "status_warn",
    "critical": "status_crit",
    "selection": "selected_bg",
    "disabled_fg": "disabled_text",
    "disabled_surface": "disabled_bg",
    "chart_grid": "chart_grid",
    "chart_axis": "chart_axis_text",
    "manual_override_highlight": "status_warn",
    "demo_mode_highlight": "status_info",
    "card_bg": "surface_2",
    "card_border": "border_default",
    "card_hover": "surface_3",
}


@dataclass
class ThemeTokens:
    """Complete set of colour tokens that drive the application stylesheet."""

    name: str = "Default Dark"
    version: int = 2

    # ─── Core ────────────────────────────────────────────────────────
    app_bg: str = "#1a1a2e"
    surface_1: str = "#16213e"
    surface_2: str = "#1f2b47"
    surface_3: str = "#253552"
    text_primary: str = "#e0e0e8"
    text_secondary: str = "#a0a8c0"
    text_muted: str = "#606878"
    accent_primary: str = "#4a90d9"
    accent_secondary: str = "#7ec8e3"

    # ─── Borders & separators ────────────────────────────────────────
    border_default: str = "#2a3a5c"
    border_focus: str = "#4a90d9"
    divider: str = "#2a3a5c"

    # ─── Interactive states ──────────────────────────────────────────
    hover_bg: str = "#253552"
    pressed_bg: str = "#4a90d9"
    selected_bg: str = "#2a4a7f"
    focus_ring: str = "#4a90d9"
    disabled_bg: str = "#1e1e30"
    disabled_text: str = "#505868"

    # ─── Status colours ──────────────────────────────────────────────
    status_ok: str = "#4caf50"
    status_warn: str = "#ff9800"
    status_crit: str = "#ef5350"
    status_info: str = "#7ec8e3"

    # ─── Charts / Graphs ─────────────────────────────────────────────
    chart_bg: str = "#16213e"
    chart_grid: str = "#2a3a5c"
    chart_axis_text: str = "#606878"
    chart_line_primary: str = "#4a90d9"
    chart_point: str = "#4a90d9"
    chart_point_selected: str = "#ffffff"
    chart_point_hover: str = "#ffffff"
    chart_crosshair: str = "#606878"
    chart_series: list[str] = field(
        default_factory=lambda: [
            "#4a90d9",
            "#7ec8e3",
            "#e06c75",
            "#98c379",
            "#d19a66",
            "#c678dd",
            "#56b6c2",
            "#be5046",
        ]
    )

    # ─── Sidebar / navigation ────────────────────────────────────────
    nav_bg: str = "#16213e"
    nav_text: str = "#a0a8c0"
    nav_text_active: str = "#4a90d9"
    nav_item_hover: str = "#1f2b47"
    nav_item_active: str = "#2a4a7f"

    # ─── Inputs / controls ───────────────────────────────────────────
    input_bg: str = "#1f2b47"
    input_text: str = "#e0e0e8"
    input_placeholder: str = "#606878"
    input_border: str = "#2a3a5c"
    input_border_focus: str = "#4a90d9"

    # ─── Modals / dialogs ────────────────────────────────────────────
    modal_overlay: str = "#000000aa"
    modal_bg: str = "#1f2b47"
    modal_border: str = "#2a3a5c"

    # ─── Tables ──────────────────────────────────────────────────────
    table_header_bg: str = "#16213e"
    table_row_bg: str = "#1a1a2e"
    table_row_alt_bg: str = "#1f2b47"
    table_row_hover_bg: str = "#253552"
    table_text: str = "#e0e0e8"

    # ─── Primary button text ─────────────────────────────────────────
    primary_btn_text: str = "#ffffff"

    # ─── Brand ───────────────────────────────────────────────────────
    brand_primary: str = "#4a90d9"
    brand_secondary: str = "#7ec8e3"
    brand_accent: str = "#2a4a7f"

    # ─── Typography ──────────────────────────────────────────────────
    font_family: str = ""  # empty = system default
    base_font_size_pt: int = 10  # user-adjustable, range 7-16


def font_sizes(base: int) -> dict[str, int]:
    """Compute role-based font sizes from the base size in points.

    Roles and multipliers:
    - title: page headings (1.6x)
    - section: section headers, group titles (1.3x)
    - body: default text, buttons (1.0x)
    - card_title: card name labels (1.1x)
    - small: card metadata, status chips (0.9x)
    - card_value: dashboard summary card reading (2.2x)
    - brand: sidebar brand text (1.4x)
    """
    return {
        "title": round(base * 1.6),
        "section": round(base * 1.3),
        "body": base,
        "card_title": round(base * 1.1),
        "small": round(base * 0.9),
        "card_value": round(base * 2.2),
        "brand": round(base * 1.4),
    }


_active_base_size: int = 10


def set_active_base_size(size: int) -> None:
    """Update the module-level base font size used by current_font_sizes()."""
    global _active_base_size
    _active_base_size = size


def apply_theme_font(tokens: ThemeTokens) -> None:
    """Apply the theme's font family and base size to the application."""
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    set_active_base_size(tokens.base_font_size_pt)

    family = tokens.font_family
    if not family:
        sys_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        family = sys_font.family()

    font = QFont(family, tokens.base_font_size_pt)
    app = QApplication.instance()
    if app:
        app.setFont(font)


def current_font_sizes() -> dict[str, int]:
    """Return font sizes for the currently active base size.

    Cards call this at construction time so their inline styles use the
    current base font size, which is updated by apply_theme_font().
    """
    return font_sizes(_active_base_size)


def default_dark_theme() -> ThemeTokens:
    return ThemeTokens()


def load_theme(path: Path) -> ThemeTokens:
    """Load a theme from JSON, migrating old token names if present."""
    data = json.loads(path.read_text())
    migrated = _migrate_tokens(data)
    tokens = ThemeTokens()
    for k, v in migrated.items():
        if hasattr(tokens, k):
            setattr(tokens, k, v)
    return tokens


def _migrate_tokens(data: dict) -> dict:
    """Migrate old token names to new spec names."""
    result = dict(data)
    for old_name, new_name in _TOKEN_MIGRATION.items():
        if old_name in result and new_name not in result:
            result[new_name] = result.pop(old_name)
        elif old_name in result:
            del result[old_name]
    if result.get("version", 1) < 2:
        result["version"] = 2
    return result


def save_theme(tokens: ThemeTokens, path: Path) -> None:
    from control_ofc.paths import atomic_write

    atomic_write(path, json.dumps(asdict(tokens), indent=2) + "\n")


# ---------------------------------------------------------------------------
# Contrast checking
# ---------------------------------------------------------------------------


def _relative_luminance(hex_color: str) -> float:
    """Compute relative luminance per WCAG 2.1."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) < 6:
        return 0.0
    r, g, b = (int(hex_color[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

    def linearize(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def contrast_ratio(color_a: str, color_b: str) -> float:
    """Compute WCAG contrast ratio between two hex colours."""
    la = _relative_luminance(color_a)
    lb = _relative_luminance(color_b)
    lighter = max(la, lb)
    darker = min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def check_contrast_warnings(tokens: ThemeTokens) -> list[str]:
    """Return a list of contrast warnings for critical token pairs."""
    warnings: list[str] = []
    pairs = [
        ("text_primary", "surface_1", 4.5),
        ("text_secondary", "surface_2", 3.0),
        ("nav_text", "nav_bg", 3.0),
        ("input_text", "input_bg", 4.5),
        ("table_text", "table_row_bg", 4.5),
        ("focus_ring", "app_bg", 3.0),
    ]
    for fg_name, bg_name, min_ratio in pairs:
        fg = getattr(tokens, fg_name, "")
        bg = getattr(tokens, bg_name, "")
        if not fg or not bg:
            continue
        ratio = contrast_ratio(fg, bg)
        if ratio < min_ratio:
            warnings.append(
                f"{fg_name} vs {bg_name}: contrast {ratio:.1f}:1 "
                f"(minimum {min_ratio}:1 recommended)"
            )
    return warnings


# ---------------------------------------------------------------------------
# Stylesheet generation
# ---------------------------------------------------------------------------


def build_stylesheet(t: ThemeTokens) -> str:
    """Generate a Qt stylesheet from theme tokens."""
    fs = font_sizes(t.base_font_size_pt)
    return f"""
    /* Global */
    QWidget {{
        background-color: {t.app_bg};
        color: {t.text_primary};
        font-size: {fs["body"]}pt;
    }}

    QMainWindow {{
        background-color: {t.app_bg};
    }}

    /* Sidebar */
    #Sidebar {{
        background-color: {t.nav_bg};
        border-right: 1px solid {t.border_default};
    }}

    #Sidebar QPushButton {{
        background-color: transparent;
        color: {t.nav_text};
        border: none;
        border-radius: 6px;
        padding: 10px 16px;
        text-align: left;
        font-size: {fs["body"]}pt;
        font-weight: 500;
    }}

    #Sidebar QPushButton:hover {{
        background-color: {t.nav_item_hover};
        color: {t.text_primary};
    }}

    #Sidebar QPushButton:checked {{
        background-color: {t.nav_item_active};
        color: {t.nav_text_active};
    }}

    /* Status banner */
    #StatusBanner {{
        background-color: {t.surface_1};
        border-bottom: 1px solid {t.border_default};
        padding: 4px 12px;
    }}

    #StatusBanner QLabel {{
        color: {t.text_secondary};
        font-size: {fs["card_title"]}pt;
    }}

    /* Cards */
    .Card {{
        background-color: {t.surface_2};
        border: 1px solid {t.border_default};
        border-radius: 8px;
        padding: 12px;
    }}

    .Card:hover {{
        background-color: {t.surface_3};
    }}

    /* Page titles */
    .PageTitle {{
        color: {t.text_primary};
        font-size: {fs["title"]}pt;
        font-weight: 600;
    }}

    .PageSubtitle {{
        color: {t.text_secondary};
        font-size: {fs["section"]}pt;
    }}

    .CardValue {{
        font-size: {fs["card_value"]}pt;
        font-weight: bold;
    }}

    .CardMeta {{
        color: {t.text_secondary};
        font-size: {fs["small"]}pt;
    }}

    .ValueLabel {{
        font-weight: bold;
        font-size: {fs["body"]}pt;
    }}

    .Card QPushButton {{
        padding: 4px 8px;
    }}

    /* Buttons */
    QPushButton {{
        background-color: {t.surface_2};
        color: {t.text_primary};
        border: 1px solid {t.border_default};
        border-radius: 6px;
        padding: 6px 16px;
        font-size: {fs["body"]}pt;
    }}

    QPushButton:hover {{
        background-color: {t.hover_bg};
    }}

    QPushButton:pressed {{
        background-color: {t.pressed_bg};
    }}

    QPushButton:disabled {{
        background-color: {t.disabled_bg};
        color: {t.disabled_text};
    }}

    QPushButton#PrimaryButton {{
        background-color: {t.accent_primary};
        color: {t.primary_btn_text};
        border: none;
    }}

    QPushButton#PrimaryButton:hover {{
        background-color: {t.accent_secondary};
    }}

    /* Scroll areas */
    QScrollArea {{
        border: none;
    }}

    QScrollBar:vertical {{
        background-color: {t.app_bg};
        width: 8px;
    }}

    QScrollBar::handle:vertical {{
        background-color: {t.border_default};
        border-radius: 4px;
        min-height: 24px;
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    /* Labels */
    QLabel {{
        color: {t.text_primary};
    }}

    /* Combo boxes */
    QComboBox {{
        background-color: {t.input_bg};
        color: {t.input_text};
        border: 1px solid {t.input_border};
        border-radius: 4px;
        padding: 4px 8px;
    }}

    QComboBox::drop-down {{
        border: none;
    }}

    QComboBox QAbstractItemView {{
        background-color: {t.surface_1};
        color: {t.text_primary};
        selection-background-color: {t.selected_bg};
    }}

    /* Sliders */
    QSlider::groove:horizontal {{
        background: {t.border_default};
        height: 4px;
        border-radius: 2px;
    }}

    QSlider::handle:horizontal {{
        background: {t.accent_primary};
        width: 16px;
        height: 16px;
        margin: -6px 0;
        border-radius: 8px;
    }}

    /* Line edits and spin boxes */
    QLineEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {t.input_bg};
        color: {t.input_text};
        border: 1px solid {t.input_border};
        border-radius: 4px;
        padding: 4px 8px;
    }}

    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {t.input_border_focus};
    }}

    /* Tab widgets */
    QTabWidget::pane {{
        border: 1px solid {t.border_default};
        border-radius: 4px;
    }}

    QTabBar::tab {{
        background-color: {t.surface_1};
        color: {t.text_secondary};
        padding: 8px 16px;
        border-bottom: 2px solid transparent;
    }}

    QTabBar::tab:selected {{
        color: {t.accent_primary};
        border-bottom-color: {t.accent_primary};
    }}

    /* Tables */
    QTableWidget {{
        background-color: {t.table_row_bg};
        color: {t.table_text};
        gridline-color: {t.border_default};
    }}

    QTableWidget::item {{
        padding: 4px;
    }}

    QTableWidget::item:alternate {{
        background-color: {t.table_row_alt_bg};
    }}

    QTableWidget::item:hover {{
        background-color: {t.table_row_hover_bg};
    }}

    QTableWidget::item:selected {{
        background-color: {t.selected_bg};
    }}

    QHeaderView::section {{
        background-color: {t.table_header_bg};
        color: {t.text_secondary};
        border: 1px solid {t.border_default};
        padding: 4px;
    }}

    /* Tree widgets */
    QTreeWidget {{
        background-color: {t.table_row_bg};
        color: {t.table_text};
        border: 1px solid {t.border_default};
        alternate-background-color: {t.table_row_alt_bg};
    }}

    QTreeWidget::item {{
        padding: 2px;
    }}

    QTreeWidget::item:hover {{
        background-color: {t.table_row_hover_bg};
    }}

    QTreeWidget::item:selected {{
        background-color: {t.selected_bg};
    }}

    QTreeWidget::branch {{
        background-color: {t.table_row_bg};
    }}

    QTreeWidget::branch:has-children:closed {{
        border-image: none;
        image: none;
        border-left: 6px solid transparent;
        border-top: 4px solid transparent;
        border-bottom: 4px solid transparent;
        border-right: none;
        border-left-color: {t.text_secondary};
        width: 0px;
        height: 0px;
        margin-left: 4px;
    }}

    QTreeWidget::branch:has-children:open {{
        border-image: none;
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 6px solid {t.text_secondary};
        border-bottom: none;
        width: 0px;
        height: 0px;
        margin-left: 2px;
    }}

    /* Drop indicator for drag-reorder */
    .DropIndicator {{
        background-color: {t.accent_primary};
        border-radius: 1px;
    }}

    /* Tooltips */
    QToolTip {{
        background-color: {t.surface_2};
        color: {t.text_primary};
        border: 1px solid {t.border_default};
        padding: 4px;
    }}

    /* Dialogs */
    QDialog {{
        background-color: {t.modal_bg};
        border: 1px solid {t.modal_border};
    }}

    /* Warning/status chips */
    .WarningChip {{
        color: {t.status_warn};
    }}

    .CriticalChip {{
        color: {t.status_crit};
    }}

    .SuccessChip {{
        color: {t.status_ok};
    }}

    .DemoBadge {{
        color: {t.status_info};
        font-weight: bold;
    }}

    .ManualBadge {{
        color: {t.status_warn};
        font-weight: bold;
    }}

    /* List widgets */
    QListWidget {{
        background-color: {t.surface_1};
        color: {t.text_primary};
        border: 1px solid {t.border_default};
        border-radius: 4px;
    }}

    QListWidget::item:selected {{
        background-color: {t.selected_bg};
    }}

    QListWidget::item:hover {{
        background-color: {t.hover_bg};
    }}
    """
