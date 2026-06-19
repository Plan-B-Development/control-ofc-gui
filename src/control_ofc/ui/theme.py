"""Token-based theme system.

Every colour in the application is driven by named tokens. The default dark
theme provides the baseline. Users can customise, save, load, import, and
export themes via the Theme Editor in Settings.
"""

from __future__ import annotations

import hashlib
import json
import os
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
    # text_muted: bumped from #606878 (2.5:1 on surface_2) to #8a92a4 to hit
    # the WCAG AA 3:1 non-text target on cards (DEC-109).
    text_muted: str = "#8a92a4"
    # accent_primary: darkened from #4a90d9 (3.3:1 with white) to #2f73c4
    # so primary-button text reaches WCAG AA 4.5:1 (DEC-109).
    accent_primary: str = "#2f73c4"
    # accent_secondary: previously #7ec8e3, used as primary-button hover
    # (1.9:1 with white). Reused as a darker "pressed" tone (#1d5fa9, 5.7:1).
    accent_secondary: str = "#1d5fa9"

    # ─── Borders & separators ────────────────────────────────────────
    border_default: str = "#2a3a5c"
    border_focus: str = "#2f73c4"
    divider: str = "#2a3a5c"

    # ─── Interactive states ──────────────────────────────────────────
    hover_bg: str = "#253552"
    pressed_bg: str = "#1d5fa9"
    selected_bg: str = "#1a3a6a"
    focus_ring: str = "#2f73c4"
    disabled_bg: str = "#1e1e30"
    disabled_text: str = "#505868"

    # ─── Status colours ──────────────────────────────────────────────
    status_ok: str = "#4caf50"
    status_warn: str = "#ff9800"
    status_crit: str = "#ef5350"
    status_info: str = "#7ec8e3"
    # status_caution: amber/gold for the MEDIUM advisory tier (DEC-158). Placed
    # between status_warn (orange, HIGH) and status_info (blue, INFO) by hue so
    # the four advisory severities separate by colour as well as by icon + word
    # — INFO no longer shares an orange with the warning tiers. 8.6:1 on
    # surface_2, comfortably past the WCAG AA 4.5:1 text minimum.
    status_caution: str = "#f5c518"

    # ─── Charts / Graphs ─────────────────────────────────────────────
    chart_bg: str = "#16213e"
    chart_grid: str = "#2a3a5c"
    # chart_axis_text: bumped from #606878 (2.8:1) to #8a92a4 (4.5:1)
    # so chart axis labels meet WCAG AA non-text contrast (DEC-109).
    chart_axis_text: str = "#8a92a4"
    chart_line_primary: str = "#5fa4ec"
    chart_point: str = "#5fa4ec"
    chart_point_selected: str = "#ffffff"
    chart_point_hover: str = "#ffffff"
    chart_crosshair: str = "#8a92a4"
    chart_series: list[str] = field(
        default_factory=lambda: [
            "#5fa4ec",
            "#7ec8e3",
            "#e06c75",
            "#98c379",
            "#d19a66",
            "#c678dd",
            "#56b6c2",
            "#be5046",
        ]
    )
    # chart_tooltip_bg / _border drive the hover-readout plate painted over
    # the chart. Defaults match surface_2 / border_default so the chart
    # tooltip reads consistently with the app's QToolTip styling and inherits
    # the already-validated text_primary-on-surface_2 contrast (DEC-118).
    chart_tooltip_bg: str = "#1f2b47"
    chart_tooltip_border: str = "#2a3a5c"

    # ─── Sidebar / navigation ────────────────────────────────────────
    nav_bg: str = "#16213e"
    nav_text: str = "#a0a8c0"
    # nav_text_active: brightened to reach 3:1 against nav_item_active so the
    # selected sidebar item is legible (was 2.6:1 with #4a90d9) (DEC-109).
    nav_text_active: str = "#a4caf5"
    nav_item_hover: str = "#1f2b47"
    nav_item_active: str = "#1a3a6a"

    # ─── Inputs / controls ───────────────────────────────────────────
    input_bg: str = "#1f2b47"
    input_text: str = "#e0e0e8"
    # input_placeholder: bumped from #606878 (2.5:1 on input_bg) to match the
    # new text_muted at 3.3:1 — non-text contrast minimum (DEC-109).
    input_placeholder: str = "#8a92a4"
    input_border: str = "#2a3a5c"
    input_border_focus: str = "#2f73c4"

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

    # ─── Surfaces for inline code / commands ─────────────────────────
    # Used by widgets that need a "code block" tint over the app surface
    # (e.g. the systemctl enable hint on the dashboard). Token-driven so
    # light themes can swap to a lighter tint instead of pure black (DEC-109).
    code_block_bg: str = "#0d1224"

    # ─── Brand ───────────────────────────────────────────────────────
    brand_primary: str = "#2f73c4"
    brand_secondary: str = "#7ec8e3"
    brand_accent: str = "#1a3a6a"

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


_active_theme: ThemeTokens | None = None


def set_active_theme(tokens: ThemeTokens) -> None:
    """Register the currently-applied theme so widgets without a parent
    reference can look up the live tokens via :func:`active_theme`.

    Called by ``main.py`` at startup and by ``MainWindow._on_theme_changed``
    whenever the user applies a new theme. Lets widgets like the
    diagnostics page or the timeline chart read the current colour set on
    every render instead of capturing a stale snapshot at import time
    (DEC-109).
    """
    global _active_theme
    _active_theme = tokens


def active_theme() -> ThemeTokens:
    """Return the currently-applied theme, or the default dark theme if
    no theme has been registered yet (e.g. during pure unit tests that do
    not boot the QApplication)."""
    return _active_theme if _active_theme is not None else default_dark_theme()


def apply_theme_font(tokens: ThemeTokens) -> None:
    """Apply the theme's font family and base size to the application."""
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    family = tokens.font_family
    if not family:
        sys_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        family = sys_font.family()

    font = QFont(family, tokens.base_font_size_pt)
    app = QApplication.instance()
    if app:
        app.setFont(font)


def default_dark_theme() -> ThemeTokens:
    return ThemeTokens()


def load_theme(path: Path) -> ThemeTokens:
    """Load a theme from JSON, migrating old token names and validating values.

    Invalid colour tokens, out-of-range font sizes, and bad ``font_family``
    values are dropped/clamped to the dataclass default so a hand-edited or
    corrupt on-disk theme can never break the stylesheet (DEC-142).
    """
    from control_ofc.paths import load_json_capped

    data = load_json_capped(path)
    migrated = _migrate_tokens(data)
    tokens = ThemeTokens()
    _apply_token_dict(tokens, migrated, strict=False)
    return tokens


def _migrate_tokens(data: dict) -> dict:
    """Migrate old token names to new spec names.

    Bumps the schema version to v3 when an old file is loaded so the GUI
    can later detect themes that predate DEC-109's WCAG-AA pass.
    """
    result = dict(data)
    for old_name, new_name in _TOKEN_MIGRATION.items():
        if old_name in result and new_name not in result:
            result[new_name] = result.pop(old_name)
        elif old_name in result:
            del result[old_name]
    if result.get("version", 1) < 2:
        result["version"] = 2
    return result


# Theme string fields that are NOT colour tokens (skip hex validation).
_NON_COLOR_STR_FIELDS = frozenset({"name", "font_family"})
_FONT_SIZE_MIN = 7
_FONT_SIZE_MAX = 16


def _coerce_base_font_size(value: object, default: int) -> int:
    """Clamp a base font size into the supported range; non-ints fall back."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(_FONT_SIZE_MIN, min(_FONT_SIZE_MAX, value))


def _apply_token_dict(tokens: ThemeTokens, data: dict, *, strict: bool) -> None:
    """Apply a (migrated) token dict onto *tokens*, validating every value.

    Colour tokens — and each ``chart_series`` entry — must pass
    :func:`control_ofc.colors.is_valid_color`; ``base_font_size_pt`` is clamped
    to 7-16 and ``name``/``font_family`` coerced to strings. With
    ``strict=True`` (theme *import*) the first invalid colour raises
    ``ValueError`` so the caller skips the whole theme; with ``strict=False``
    (loading an on-disk theme) the offending token is dropped and the dataclass
    default kept, so a hand-edited or corrupt file can never break the
    stylesheet (DEC-142).
    """
    from control_ofc.colors import is_valid_color

    for key, value in data.items():
        if not hasattr(tokens, key):
            continue
        if key == "version":
            if isinstance(value, int) and not isinstance(value, bool):
                tokens.version = value
            continue
        if key == "base_font_size_pt":
            tokens.base_font_size_pt = _coerce_base_font_size(value, tokens.base_font_size_pt)
            continue
        if key in _NON_COLOR_STR_FIELDS:
            if isinstance(value, str):
                setattr(tokens, key, value[:256])
            continue
        if key == "chart_series":
            if not isinstance(value, list):
                if strict:
                    raise ValueError("chart_series must be a list of colours")
                continue
            cleaned = [c for c in value if is_valid_color(c)]
            if strict and len(cleaned) != len(value):
                raise ValueError("chart_series contains an invalid colour")
            if cleaned:
                tokens.chart_series = cleaned
            continue
        # Everything else is a colour token.
        if is_valid_color(value):
            setattr(tokens, key, value)
        elif strict:
            raise ValueError(f"invalid colour for token {key!r}: {value!r}")
        # non-strict: drop the invalid colour, keep the default


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
    """Return a list of WCAG 2.1 contrast warnings for critical token pairs.

    Thresholds:
      - 4.5:1 — body text (WCAG AA, < 18pt regular / < 14pt bold)
      - 3.0:1 — large text, non-text UI components, icons, focus indicators

    Coverage was expanded in DEC-109 to catch the failures the previous
    set silently allowed (primary-button-on-accent, hover state,
    active-nav-on-its-fill, chart axis text, placeholder text, muted text
    on cards). WCAG explicitly exempts disabled controls from contrast
    requirements, so ``disabled_text``/``disabled_bg`` is intentionally
    excluded here.
    """
    warnings: list[str] = []
    pairs = [
        # ─── Body text (AA: 4.5:1) ────────────────────────────────
        ("text_primary", "app_bg", 4.5),
        ("text_primary", "surface_1", 4.5),
        ("text_primary", "surface_2", 4.5),
        ("input_text", "input_bg", 4.5),
        ("table_text", "table_row_bg", 4.5),
        ("table_text", "table_row_alt_bg", 4.5),
        # ─── Primary-button label on its fill (AA: 4.5:1) ─────────
        # Covers both the resting state and the hover state so the editor
        # can't certify a theme whose button hover becomes unreadable.
        # ``pressed_bg`` is intentionally excluded — that token drives the
        # *normal* QPushButton:pressed state (under text_primary), not the
        # primary button.
        ("primary_btn_text", "accent_primary", 4.5),
        ("primary_btn_text", "accent_secondary", 4.5),
        # ─── Normal-button pressed state (text_primary on pressed_bg) ───
        ("text_primary", "pressed_bg", 4.5),
        # ─── Secondary / muted text on its surface (AA-large: 3:1) ───
        ("text_secondary", "surface_2", 3.0),
        ("text_muted", "surface_2", 3.0),
        ("text_muted", "surface_1", 3.0),
        ("input_placeholder", "input_bg", 3.0),
        # ─── Navigation (AA-large: 3:1) ───────────────────────────
        ("nav_text", "nav_bg", 3.0),
        ("nav_text_active", "nav_item_active", 3.0),
        # ─── Chart axis text on chart bg (AA-large: 3:1) ──────────
        ("chart_axis_text", "chart_bg", 3.0),
        # ─── Chart hover-tooltip text on its plate (AA: 4.5:1) ────
        ("text_primary", "chart_tooltip_bg", 4.5),
        # ─── Focus indicator (AA non-text: 3:1) ───────────────────
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
# Bundled presets
# ---------------------------------------------------------------------------


def bundled_themes_dir() -> Path:
    """Return the directory containing in-tree JSON theme presets.

    Presets ship with the package and are copied into ``themes_dir()`` on
    first run by ``ensure_bundled_themes_installed`` so they appear in the
    selector without requiring the user to download them. The function
    locates the directory relative to this module so it works both in the
    dev tree and in the installed package layout.
    """
    return Path(__file__).resolve().parent / "presets"


def list_bundled_themes() -> list[Path]:
    """Return all *.json preset files shipped in :func:`bundled_themes_dir`."""
    d = bundled_themes_dir()
    if not d.exists():
        return []
    return sorted(d.glob("*.json"))


def ensure_bundled_themes_installed(target_dir: Path) -> list[Path]:
    """Copy any bundled presets into *target_dir* that aren't there already.

    Returns the list of files written. Existing files are left alone so a
    user who has edited a preset doesn't lose their changes on the next
    launch. The copy uses :func:`atomic_write` so a crash mid-copy can't
    leave a half-written theme file behind.
    """
    from control_ofc.paths import atomic_write

    written: list[Path] = []
    if not target_dir.exists():
        return written
    for src in list_bundled_themes():
        dest = target_dir / src.name
        if dest.exists():
            continue
        try:
            atomic_write(dest, src.read_text())
            written.append(dest)
        except OSError:
            # Best-effort; missing presets only mean the user has to import
            # them manually. Surfacing this would require a UI plumbing that
            # isn't worth the noise for an optional convenience.
            continue
    return written


# ---------------------------------------------------------------------------
# Stylesheet generation
# ---------------------------------------------------------------------------


def combo_arrow_svg_path(color: str) -> str | None:
    """Write a themed combo-box down-arrow SVG to the cache dir; return its path.

    Styling ``QComboBox::drop-down`` (as this theme does, to drop the native
    separator) makes Qt discard the native down-arrow entirely, so we must
    supply one. The app is stylesheet-only with no bundled image assets and
    supports arbitrary custom theme colours, so a static asset cannot follow
    the theme — instead we generate a tiny chevron SVG in the requested colour
    and reference it from the stylesheet (DEC-113).

    The file is keyed by colour so repeated calls for the same theme reuse it.
    Returns ``None`` (and the caller omits the rule) if the cache is not
    writable — the combo still works, it just falls back to no custom arrow.
    """
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" '
        'viewBox="0 0 12 12">'
        f'<path d="M2.5 4.75 L6 8.25 L9.5 4.75" fill="none" stroke="{color}" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    try:
        cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache"
        )
        cache_dir = Path(cache_root) / "control-ofc-gui"
        cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(color.encode("utf-8")).hexdigest()[:12]
        path = cache_dir / f"combo-arrow-{digest}.svg"
        if not path.exists() or path.read_text(encoding="utf-8") != svg:
            path.write_text(svg, encoding="utf-8")
        # Forward slashes only — Qt stylesheet url() wants them on every OS.
        return path.as_posix()
    except OSError:
        return None


def build_stylesheet(t: ThemeTokens) -> str:
    """Generate a Qt stylesheet from theme tokens."""
    fs = font_sizes(t.base_font_size_pt)
    _arrow_path = combo_arrow_svg_path(t.text_secondary)
    combo_down_arrow = (
        f"QComboBox::down-arrow {{ image: url({_arrow_path}); width: 12px; height: 12px; }}"
        if _arrow_path
        else ""
    )
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

    .SectionTitle {{
        font-size: {fs["section"]}pt;
        font-weight: bold;
    }}

    /* Collapsible section headers (progressive disclosure).
       Subordinate to .PageSubtitle card titles — body-sized + semibold so a
       card can hold several without competing with its own title. The chevron
       is part of the button text, so it inherits this colour. */
    .CollapsibleSectionHeader {{
        background-color: transparent;
        color: {t.text_primary};
        border: none;
        border-radius: 4px;
        padding: 6px 4px;
        font-size: {fs["body"]}pt;
        font-weight: 600;
        text-align: left;
    }}

    .CollapsibleSectionHeader:hover {{
        background-color: {t.hover_bg};
    }}

    .CollapsibleSectionHeader:pressed {{
        background-color: {t.pressed_bg};
    }}

    .SmallLabel {{
        font-size: {fs["small"]}pt;
    }}

    .CardValue {{
        font-size: {fs["card_value"]}pt;
        font-weight: bold;
    }}

    .CardRange {{
        color: {t.text_secondary};
        font-size: {fs["small"]}pt;
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
        width: 22px;
        subcontrol-origin: padding;
        subcontrol-position: center right;
    }}

    {combo_down_arrow}

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

    /* Warning/status chips. The four advisory-severity tiers (DEC-158) map
       CRITICAL→CriticalChip, HIGH→WarningChip, MEDIUM→CautionChip,
       INFO→InfoChip; each badge also carries an icon + word so colour is never
       the only severity cue (WCAG 1.4.1). */
    .WarningChip {{
        color: {t.status_warn};
    }}

    .CriticalChip {{
        color: {t.status_crit};
    }}

    .CautionChip {{
        color: {t.status_caution};
    }}

    .InfoChip {{
        color: {t.status_info};
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
