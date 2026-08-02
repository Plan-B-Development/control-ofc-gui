"""Token-based theme system.

Every colour in the application is driven by named tokens. The default dark
theme provides the baseline. Users can customise, save, load, import, and
export themes via the Theme Editor in Settings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # PySide6 is imported lazily so token-only use stays Qt-free
    from PySide6.QtGui import QPalette

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
    "chart_axis": "chart_axis_text",
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
    # Control-OFC green palette (DEC-208). The built-in theme keeps the name
    # "Default Dark" so existing users' saved theme_name adopts green with no
    # migration; the old blue palette ships as the "Classic Blue" preset.
    app_bg: str = "#0A0E08"
    surface_1: str = "#0D1610"
    surface_2: str = "#14241C"
    surface_3: str = "#1B3325"
    text_primary: str = "#C8D4C0"
    text_secondary: str = "#8AAA92"
    # text_muted: 3.38:1 on surface_2 — meets the WCAG AA 3:1 non-text target
    # (DEC-109/DEC-208).
    text_muted: str = "#5A7A62"
    # accent_primary: the brand teal. Primary-button text is dark
    # (primary_btn_text) so label-on-accent clears WCAG AA 4.5:1 (DEC-208).
    accent_primary: str = "#1FB88A"
    # accent_secondary: brighter primary-button hover tone (dark text 9.1:1).
    accent_secondary: str = "#33C89B"

    # ─── Borders & separators ────────────────────────────────────────
    border_default: str = "#1A2E20"

    # ─── Interactive states ──────────────────────────────────────────
    hover_bg: str = "#1B3325"
    pressed_bg: str = "#102A1E"
    selected_bg: str = "#123528"
    disabled_bg: str = "#0F1A14"
    disabled_text: str = "#3A5044"

    # ─── Status colours ──────────────────────────────────────────────
    # status_ok unifies with the brand accent in the green palette (DEC-208).
    status_ok: str = "#1FB88A"
    status_warn: str = "#FFAA00"
    status_crit: str = "#FF4D4D"
    status_info: str = "#00A8FF"
    # status_caution: amber/gold for the MEDIUM advisory tier (DEC-158). Placed
    # between status_warn (orange, HIGH) and status_info (blue, INFO) by hue so
    # the four advisory severities separate by colour as well as by icon + word
    # — INFO no longer shares an orange with the warning tiers. 8.6:1 on
    # surface_2, comfortably past the WCAG AA 4.5:1 text minimum.
    status_caution: str = "#f5c518"

    # ─── Charts / Graphs ─────────────────────────────────────────────
    chart_bg: str = "#0A0E08"
    chart_grid: str = "#1A2E20"
    # chart_axis_text: 4.07:1 on chart_bg — meets WCAG AA non-text contrast
    # (DEC-109/DEC-208).
    chart_axis_text: str = "#5A7A62"
    chart_point_selected: str = "#ffffff"
    chart_point_hover: str = "#ffffff"
    chart_crosshair: str = "#5A7A62"
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
    # the chart. Defaults match surface_1 / border_default so the chart
    # tooltip reads consistently with the app's QToolTip styling and inherits
    # the already-validated text_primary contrast (DEC-118).
    chart_tooltip_bg: str = "#0D1610"
    chart_tooltip_border: str = "#1A2E20"

    # ─── Sidebar / navigation ────────────────────────────────────────
    nav_bg: str = "#0D1610"
    nav_text: str = "#8AAA92"
    # nav_text_active: brand accent; 5.29:1 on nav_item_active (DEC-208).
    nav_text_active: str = "#1FB88A"
    nav_item_hover: str = "#14241C"
    nav_item_active: str = "#123528"

    # ─── Inputs / controls ───────────────────────────────────────────
    input_bg: str = "#14241C"
    input_text: str = "#C8D4C0"
    # input_placeholder: matches text_muted (3:1 non-text minimum, DEC-208).
    input_placeholder: str = "#5A7A62"
    input_border: str = "#1A2E20"
    input_border_focus: str = "#1FB88A"

    # ─── Modals / dialogs ────────────────────────────────────────────
    modal_bg: str = "#0D1610"
    modal_border: str = "#1A2E20"

    # ─── Tables ──────────────────────────────────────────────────────
    table_header_bg: str = "#0D1610"
    table_row_bg: str = "#0A0E08"
    table_row_alt_bg: str = "#14241C"
    table_row_hover_bg: str = "#1B3325"
    table_text: str = "#C8D4C0"

    # ─── Primary button text ─────────────────────────────────────────
    # Dark text on the teal accent — 7.67:1 (white would be 2.54:1, failing
    # WCAG AA). Classic Blue keeps white (DEC-208).
    primary_btn_text: str = "#0A0E08"

    # ─── Surfaces for inline code / commands ─────────────────────────
    # Used by widgets that need a "code block" tint over the app surface
    # (e.g. the systemctl enable hint on the dashboard). Token-driven so
    # light themes can swap to a lighter tint instead of pure black (DEC-109).
    code_block_bg: str = "#060A05"

    # ─── Typography ──────────────────────────────────────────────────
    # font_family drives the app-wide body QFont; font_family_heading is applied
    # via QSS to titles / section headers / numeric readouts (DEC-208). Both are
    # bundled OFL fonts registered at startup; empty = system default.
    font_family: str = "DM Sans"
    font_family_heading: str = "Space Grotesk"
    base_font_size_pt: int = 10  # user-adjustable, range 7-16


def font_sizes(base: int) -> dict[str, int]:
    """Compute role-based font sizes from the base size in points.

    Roles and multipliers:
    - title: page headings (1.6x)
    - section: section headers, group titles (1.3x)
    - body: default text, buttons (1.0x)
    - card_title: card name labels (1.1x)
    - small: card metadata, status chips (0.9x)
    - card_value: the RPM/SPEED/TEMP readings on a dashboard fan tile (1.5x)

    ``card_value`` was 2.2x — a hero-KPI size — until DEC-238. Three of them sit
    side by side in a ~235px tile, so at 2.2x the numbers outweighed the control
    name they belong to (10pt name vs 22pt value) and forced a 57px metrics row.
    1.5x keeps them the tile's dominant reading without dictating its height.
    """
    return {
        "title": round(base * 1.6),
        "section": round(base * 1.3),
        "body": base,
        "card_title": round(base * 1.1),
        "small": round(base * 0.9),
        "card_value": round(base * 1.5),
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


# Token -> QPalette role map, as (role_name, token_name) pairs. Kept as data so
# a test can assert every entry resolves and no role is left on Qt's default.
_PALETTE_ROLES: tuple[tuple[str, str], ...] = (
    # Base canvas + the text that sits on it.
    ("Window", "app_bg"),
    ("WindowText", "text_primary"),
    # Editable/scrollable content surfaces (QPlainTextEdit, unstyled item views).
    ("Base", "input_bg"),
    ("AlternateBase", "table_row_alt_bg"),
    ("Text", "input_text"),
    ("PlaceholderText", "input_placeholder"),
    # Native-drawn buttons; mirrors the QPushButton QSS rule.
    ("Button", "surface_2"),
    ("ButtonText", "text_primary"),
    ("BrightText", "status_crit"),
    # Mirrors the QToolTip QSS rule.
    ("ToolTipBase", "surface_2"),
    ("ToolTipText", "text_primary"),
    ("Highlight", "selected_bg"),
    ("HighlightedText", "text_primary"),
    ("Link", "accent_primary"),
    ("LinkVisited", "accent_secondary"),
    # 3D frame shading — keeps any style-drawn bevel in the theme's tones.
    ("Light", "surface_3"),
    ("Midlight", "surface_2"),
    ("Mid", "border_default"),
    ("Dark", "app_bg"),
    ("Shadow", "code_block_bg"),
    # Qt 6.6+. Fusion does not paint it today, but a style that does must not
    # reach for Qt's stock accent. Resolved defensively — see build_palette.
    ("Accent", "accent_primary"),
)

# Disabled-group overrides, same shape.
_PALETTE_DISABLED_ROLES: tuple[tuple[str, str], ...] = (
    ("WindowText", "disabled_text"),
    ("Text", "disabled_text"),
    ("ButtonText", "disabled_text"),
    ("Base", "disabled_bg"),
    ("Button", "disabled_bg"),
    ("Highlight", "disabled_bg"),
    ("HighlightedText", "disabled_text"),
)


def build_palette(tokens: ThemeTokens) -> QPalette:
    """Build a ``QPalette`` that agrees with :func:`build_stylesheet` (DEC-226).

    Qt's stylesheet engine re-resolves each styled widget's palette from the
    *application* palette rather than inheriting the parent's QSS-derived
    colours. So every surface Qt paints from the palette instead of from a QSS
    rule falls back to Qt's built-in light palette — a ``QScrollArea``'s content
    widget (``setWidget()`` force-enables ``autoFillBackground``), a
    ``QPlainTextEdit`` viewport, a tooltip, a native bevel.

    Until DEC-225 the blanket ``QWidget { background-color: app_bg }`` rule
    matched every widget and hid that; removing it exposed Qt's ``#efefef``
    Window on the scroll surfaces and ``#ffffff`` Base on the Logs snapshot
    panes. Painting the palette from the same tokens is the durable fix: QSS
    keeps owning the styled chrome, and the palette backs everything QSS does
    not paint.
    """
    from PySide6.QtGui import QColor, QPalette

    def colour(token_name: str) -> QColor:
        """The token's colour, falling back to the built-in default if Qt
        rejects it. ``is_valid_color`` accepts ``#RGBA``, which ``QColor`` does
        not — and an invalid QColor is stored as opaque *black*, so a single
        4-digit hex in a hand-edited theme would black out a whole surface.
        A dropped QSS declaration merely falls through; a palette role cannot."""
        parsed = QColor(getattr(tokens, token_name))
        if parsed.isValid():
            return parsed
        return QColor(getattr(ThemeTokens(), token_name))

    palette = QPalette()
    for role_name, token_name in _PALETTE_ROLES:
        # getattr with a default: ``Accent`` only exists on Qt >= 6.6, and a
        # role Qt does not know is skipped rather than crashing the theme.
        role = getattr(QPalette.ColorRole, role_name, None)
        if role is not None:
            palette.setColor(role, colour(token_name))
    disabled = QPalette.ColorGroup.Disabled
    for role_name, token_name in _PALETTE_DISABLED_ROLES:
        role = getattr(QPalette.ColorRole, role_name, None)
        if role is not None:
            palette.setColor(disabled, role, colour(token_name))
    return palette


def apply_theme_palette(tokens: ThemeTokens) -> None:
    """Apply the theme's palette to the application (DEC-226).

    Must run alongside ``setStyleSheet`` at every theme-application site, or
    unstyled surfaces revert to Qt's light default.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app:
        app.setPalette(build_palette(tokens))


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


def apply_theme(tokens: ThemeTokens) -> None:
    """Apply *tokens* to the running application — the single entry point.

    Registers the theme as active, then pushes all three application-level
    channels: palette, stylesheet, font. A theme is only fully applied when all
    three land, and the regression this consolidates (DEC-226) was exactly a
    site that pushed two of three. Callers that need extra propagation (e.g.
    ``MainWindow`` refreshing per-page chart colours) call this first, then do
    their own work.
    """
    from PySide6.QtWidgets import QApplication

    set_active_theme(tokens)
    apply_theme_palette(tokens)
    app = QApplication.instance()
    if app:
        app.setStyleSheet(build_stylesheet(tokens))
    apply_theme_font(tokens)


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

    Bumps the schema version to v2 when an old file is loaded so the GUI
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
_NON_COLOR_STR_FIELDS = frozenset({"name", "font_family", "font_family_heading"})
_FONT_SIZE_MIN = 7
_FONT_SIZE_MAX = 16
_STR_FIELD_MAX_LEN = 256
# NAME_MAX is a per-component *byte* limit (255 on Linux ext4/btrfs/xfs). The
# load-boundary cap above counts characters, but a theme name becomes
# "{stem}.json" on disk, so theme_file_path re-checks the final filename's byte
# length — otherwise a long ASCII (>=251) or multi-byte (>=84 CJK) name reaches
# save_theme and raises an uncaught OSError/ENAMETOOLONG (DEC-217).
_NAME_MAX_BYTES = 255

# Characters that would close the quoted QSS declaration that build_stylesheet
# wraps font_family_heading in, letting an imported theme append arbitrary style
# rules. Backslash is included because CSS treats it as an escape, so a trailing
# one would swallow the closing quote (DEC-217). Colour tokens are already immune
# via is_valid_color's strict hex match.
_QSS_UNSAFE_CHARS = frozenset("'\";{}\\\n\r\x00")


def _coerce_base_font_size(value: object, default: int) -> int:
    """Clamp a base font size into the supported range; non-ints fall back."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(_FONT_SIZE_MIN, min(_FONT_SIZE_MAX, value))


def _is_safe_theme_name(value: str) -> bool:
    """Whether *value* is usable as a theme's on-disk file name.

    ``ThemeTokens.name`` is not merely a label: it becomes the file stem under
    ``themes_dir()`` (see :func:`theme_file_path`) and the identity key that
    ``main.py`` matches the persisted ``theme_name`` against. A separator would
    place the file outside the themes directory, and an all-dots name is a path
    component rather than a name (DEC-217).
    """
    if not value.strip():
        return False
    if any(c in value for c in ("/", "\\", "\x00")):
        return False
    return value.strip(".").strip() != ""


def _is_safe_font_family(value: str) -> bool:
    """Whether *value* is safe to interpolate into the generated stylesheet.

    ``font_family_heading`` lands inside a quoted QSS declaration in
    :func:`build_stylesheet`; an unescaped quote or brace would close it early
    and inject arbitrary rules (DEC-217). Empty is valid — it means "inherit the
    app body font" (DEC-208).
    """
    return not any(c in value for c in _QSS_UNSAFE_CHARS)


def _apply_token_dict(tokens: ThemeTokens, data: dict, *, strict: bool) -> None:
    """Apply a (migrated) token dict onto *tokens*, validating every value.

    Colour tokens — and each ``chart_series`` entry — must pass
    :func:`control_ofc.colors.is_valid_color`; ``base_font_size_pt`` is clamped
    to 7-16; ``name``/``font_family``/``font_family_heading`` must be strings
    and are length-capped, then content-validated against the sink each one
    reaches — the filesystem for ``name`` and the generated stylesheet for the
    font families (DEC-217). With ``strict=True`` (theme *import*) the first
    invalid value raises ``ValueError`` so the caller skips the whole theme;
    with ``strict=False`` (loading an on-disk theme) the offending token is
    dropped and the dataclass default kept, so a hand-edited or corrupt file
    can never break the stylesheet (DEC-142).
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
                candidate = value[:_STR_FIELD_MAX_LEN]
                safe = (
                    _is_safe_theme_name(candidate)
                    if key == "name"
                    else _is_safe_font_family(candidate)
                )
                if safe:
                    setattr(tokens, key, candidate)
                elif strict:
                    raise ValueError(f"unsafe value for token {key!r}: {value!r}")
                # non-strict: drop the unsafe value, keep the dataclass default
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


def theme_file_path(name: str, directory: Path | None = None) -> Path:
    """Derive the on-disk path for a theme called *name*.

    The one sanctioned way to turn a theme name into a file path. Call sites
    must not hand-compose ``themes_dir() / f"{name}.json"``: *name* originates
    in untrusted theme JSON, so a separator in it would place the file outside
    the themes directory. :func:`_apply_token_dict` already rejects unsafe names
    at the load boundary — this is the second line of defence for a name that
    arrives another way, and it also catches a themes-dir entry that symlinks
    outside, since containment is checked on the *resolved* destination
    (DEC-217).

    Raises ``ValueError`` when *name* has no safe representation.
    """
    from control_ofc.paths import themes_dir

    target_dir = themes_dir() if directory is None else directory
    stem = (name or "Custom").strip().lower().replace(" ", "_")
    if not _is_safe_theme_name(stem):
        raise ValueError(f"unsafe theme name: {name!r}")
    filename = f"{stem}.json"
    if len(filename.encode("utf-8")) > _NAME_MAX_BYTES:
        raise ValueError(f"theme name too long for the filesystem: {name!r}")
    dest = target_dir / filename
    if not dest.resolve().is_relative_to(target_dir.resolve()):
        raise ValueError(f"theme name escapes {target_dir}: {name!r}")
    return dest


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
    supply one. The app bundles no image assets and supports arbitrary custom
    theme colours, so a static asset cannot follow the theme — instead we
    generate a tiny chevron SVG in the requested colour and reference it from
    the stylesheet (DEC-113). The theme palette (DEC-226) carries colours, not
    glyphs, so it cannot supply the arrow either.

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
        # Use the canonical XDG cache dir (~/.cache/control-ofc/) from the paths
        # module rather than a hardcoded "-gui" variant, so every cache consumer
        # agrees on one location (DEC-113).
        from control_ofc.paths import cache_dir

        arrow_dir = cache_dir()
        arrow_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(color.encode("utf-8")).hexdigest()[:12]
        path = arrow_dir / f"combo-arrow-{digest}.svg"
        if not path.exists() or path.read_text(encoding="utf-8") != svg:
            path.write_text(svg, encoding="utf-8")
        # Forward slashes only — Qt stylesheet url() wants them on every OS.
        return path.as_posix()
    except OSError:
        return None


def _rgba(hex_color: str, alpha: float) -> str:
    """Return a Qt-stylesheet ``rgba(r, g, b, a)`` string from a hex token.

    Qt style sheets do not reliably parse 8-digit ``#RRGGBBAA`` hex, so
    translucent tints are emitted as ``rgba()`` derived from a solid token
    (DEC-208). Non-hex input degrades to fully-transparent black.
    """
    h = hex_color.lstrip("#")
    if len(h) >= 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    else:
        r = g = b = 0
    return f"rgba({r}, {g}, {b}, {alpha})"


def build_stylesheet(t: ThemeTokens) -> str:
    """Generate a Qt stylesheet from theme tokens."""
    fs = font_sizes(t.base_font_size_pt)
    # Heading font applied via QSS to titles / section headers / numeric
    # readouts; an empty token inherits the app body font (DEC-208).
    hf = f"font-family: '{t.font_family_heading}';" if t.font_family_heading else ""
    _arrow_path = combo_arrow_svg_path(t.text_secondary)
    combo_down_arrow = (
        f"QComboBox::down-arrow {{ image: url({_arrow_path}); width: 12px; height: 12px; }}"
        if _arrow_path
        else ""
    )
    return f"""
    /* Global — base text + font ONLY. A blanket ``background-color`` here would
       paint every bare container (fan-card metric columns, control-card sub-rows,
       dialog bodies) in app_bg, which then leaks through the transparent labels
       sitting on top of a lighter .Card surface. Anchoring the window fill on the
       top-level #MainWindow instead lets bare containers stay transparent and
       reveal whatever surface they actually sit on (DEC-225). */
    QWidget {{
        color: {t.text_primary};
        font-size: {fs["body"]}pt;
    }}

    /* Window base fill. The app's top-level is a QWidget (MainWindow), not a
       QMainWindow, so the base app background is anchored on its objectName. */
    #MainWindow {{
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

    /* Dashboard fan tile (DEC-238). A small status tile, not a page-level card:
       it drops .Card's 12px padding to zero and owns its inset in the layout
       instead, so the curve band can run full-bleed to the inner border while
       the text rows stay inset. The radius steps 8->6 to stay optically crisp
       at tile scale.

       An *attribute* selector, not a second class name: a bare tile class would
       tie with .Card on specificity and the winner would depend on rule order,
       whereas class+attribute beats plain .Card outright. Only FanControlCard
       sets density="tile", so every other card on every page is untouched. */
    .Card[density="tile"] {{
        padding: 0px;
        border-radius: 6px;
    }}

    /* The tile's title-row Edit button. Overrides the roomier `.Card QPushButton`
       inset (class+attribute+type > class+type) so the button fits the title row
       instead of setting its height. It also takes the primary text colour: as a
       ghost it is borderless, and at the secondary tone it was the same colour
       and weight as the .CardMeta fan count one row below, so the tile's only
       action did not read as a control at rest. */
    .Card[density="tile"] QPushButton {{
        padding: 1px 6px;
    }}

    .Card[density="tile"] QPushButton[variant="ghost"] {{
        color: {t.text_primary};
    }}

    /* The tile's state chip shares a row with the .CardMeta fan count (DEC-238),
       and chips are otherwise body-sized — which left one row carrying two type
       sizes at opposite ends. Scoped to the tile so chips everywhere else keep
       the size they were designed at; the size still comes from the theme's
       `small` role, never a literal. */
    .Card[density="tile"] .SuccessChip,
    .Card[density="tile"] .WarningChip,
    .Card[density="tile"] .CriticalChip,
    .Card[density="tile"] .CautionChip,
    .Card[density="tile"] .InfoChip {{
        font-size: {fs["small"]}pt;
    }}

    /* The current/highlighted card (DEC-214): the selected Fan-Role card and the
       ACTIVE/assigned curve card take a solid accent border (mockup treatment). */
    .Card[active="true"], .Card[selected="true"] {{
        border-color: {t.accent_primary};
    }}

    /* The curve card open in the editor pane (DEC-233): a bolder 2px accent
       border + a faint accent-tinted fill, on top of the accent glow effect the
       card applies in code. Placed after the active/selected rule so an assigned
       curve that is also being edited reads unambiguously as "on the workbench".
       The :hover keeps the tint so the fill does not flicker on mouse-over. */
    .Card[editing="true"], .Card[editing="true"]:hover {{
        border: 2px solid {t.accent_secondary};
        background-color: {t.selected_bg};
    }}

    /* Page titles */
    .PageTitle {{
        color: {t.text_primary};
        {hf}
        font-size: {fs["title"]}pt;
        font-weight: 600;
    }}

    .PageSubtitle {{
        color: {t.text_secondary};
        font-size: {fs["section"]}pt;
    }}

    .SectionTitle {{
        {hf}
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
        {hf}
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

    /* De-emphasised inline text (settings dir-picker paths). A scoped class —
       not an inline token f-string — so the tint re-resolves from the freshly
       generated stylesheet on every live theme change. */
    .MutedLabel {{
        color: {t.text_muted};
    }}

    /* Hairline rule between the dashboard fan-card metric columns (DEC-225): a
       1px vertical line in the card's own border tone, so the RPM/SPEED/TEMP
       trio stays scannable without an inset panel of a different colour. */
    .CardDivider {{
        background-color: {t.border_default};
        border: none;
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

    /* Splitters — one shared resize handle on every page (DEC-234). A quiet
       hairline at rest (the same divider language as .CardDivider / gridlines),
       brightening to the brand accent on hover so the drag affordance is
       discoverable. The grab zone is wider than the painted line via
       setHandleWidth() (ui.qt_util.style_splitter); the margins here thin the
       paint to a centred ~2px hairline. */
    QSplitter::handle {{
        background-color: {t.border_default};
    }}
    QSplitter::handle:horizontal {{
        margin: 2px 3px;
    }}
    QSplitter::handle:vertical {{
        margin: 3px 2px;
    }}
    QSplitter::handle:horizontal:hover,
    QSplitter::handle:vertical:hover {{
        background-color: {t.accent_primary};
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

    /* Read-only output panes — the Logs snapshot previews and the inspector's
       raw message. Both are monospace command/log output, which is what
       code_block_bg exists for; sitting them on the card surface with no rule
       left the frame to the native sunken bevel (DEC-226). */
    QPlainTextEdit {{
        background-color: {t.code_block_bg};
        color: {t.text_primary};
        border: 1px solid {t.border_default};
        border-radius: 4px;
        padding: 4px;
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

    /* Popup menus (e.g. the curve-card Actions dropdown). Needs an explicit fill
       now the blanket QWidget background is gone (DEC-225); uses the same raised
       surface as the QComboBox popup for consistency. */
    QMenu {{
        background-color: {t.surface_1};
        color: {t.text_primary};
        border: 1px solid {t.border_default};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 4px 24px 4px 12px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {t.selected_bg};
    }}
    QMenu::separator {{
        height: 1px;
        background-color: {t.border_default};
        margin: 4px 6px;
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

    /* ─── Redesign shared components (DEC-208) ───────────────────────── */

    /* Bracket card: a calm surface with a left accent bar that intensifies on
       hover; the one accent per card is its state. */
    .BracketCard {{
        background-color: {t.surface_2};
        border: 1px solid {t.border_default};
        border-left: 2px solid {t.border_default};
        border-radius: 6px;
    }}
    .BracketCard:hover {{
        border-left-color: {t.accent_primary};
    }}
    .BracketCard[warning="true"] {{
        border-left-color: {t.status_warn};
    }}

    /* Section header: accent bar + uppercase heading-font label */
    .SectionBar {{
        background-color: {t.accent_primary};
        border-radius: 1px;
    }}
    /* Numbered step badge (DEC-233): a filled accent disc carrying the step
       number, replacing the plain bar on the Controls 1→2→3 workflow headers.
       Dark-on-accent text (primary_btn_text) clears WCAG AA like the primary
       button. */
    .StepBadge {{
        background-color: {t.accent_primary};
        color: {t.primary_btn_text};
        border-radius: 9px;
        {hf}
        font-size: {fs["small"]}pt;
        font-weight: 700;
    }}
    .SectionHeader {{
        color: {t.text_secondary};
        {hf}
        font-size: {fs["small"]}pt;
        font-weight: 600;
    }}

    /* Filled status pills (bg tint + border + uppercase heading font). Net-new;
       the colour-only .*Chip classes above are kept for back-compat. */
    .Pill_success, .Pill_warning, .Pill_critical, .Pill_info, .Pill_neutral {{
        {hf}
        font-size: {fs["small"]}pt;
        font-weight: 600;
        border-radius: 4px;
        padding: 1px 6px;
    }}
    .Pill_success {{
        color: {t.status_ok};
        background-color: {_rgba(t.status_ok, 0.12)};
        border: 1px solid {_rgba(t.status_ok, 0.30)};
    }}
    .Pill_warning {{
        color: {t.status_warn};
        background-color: {_rgba(t.status_warn, 0.12)};
        border: 1px solid {_rgba(t.status_warn, 0.30)};
    }}
    .Pill_critical {{
        color: {t.status_crit};
        background-color: {_rgba(t.status_crit, 0.12)};
        border: 1px solid {_rgba(t.status_crit, 0.30)};
    }}
    .Pill_info {{
        color: {t.status_info};
        background-color: {_rgba(t.status_info, 0.12)};
        border: 1px solid {_rgba(t.status_info, 0.30)};
    }}
    .Pill_neutral {{
        color: {t.text_muted};
        background-color: {_rgba(t.text_muted, 0.12)};
        border: 1px solid {_rgba(t.text_muted, 0.30)};
    }}

    /* Button variants — property-driven so a widget's objectName stays free for
       tests; #PrimaryButton (objectName) above stays for existing callers. */
    QPushButton[variant="primary"] {{
        background-color: {t.accent_primary};
        color: {t.primary_btn_text};
        border: none;
    }}
    QPushButton[variant="primary"]:hover {{
        background-color: {t.accent_secondary};
    }}
    QPushButton[variant="secondary"] {{
        background-color: {t.surface_1};
        color: {t.text_primary};
        border: 1px solid {t.border_default};
    }}
    QPushButton[variant="secondary"]:hover {{
        border-color: {t.accent_primary};
        color: {t.accent_primary};
    }}
    QPushButton[variant="ghost"] {{
        background-color: transparent;
        color: {t.text_secondary};
        border: 1px solid transparent;
    }}
    QPushButton[variant="ghost"]:hover {{
        color: {t.text_primary};
        background-color: {t.hover_bg};
    }}

    /* Keyboard focus for the ghost variant (DEC-238). Ghost is transparent fill
       *and* transparent border, and a QSS-styled button loses Qt's native focus
       rect — so without this a tab-focused ghost button is invisible (WCAG
       2.4.7). Reuses the button's own transparent border, no layout shift.

       Deliberately *not* accent_primary: accent is this app's primary-action
       language (`[variant="primary"]` fills with it), and ghost is what the
       modal footers use for Cancel/Discard. Focus landing on a dialog's Cancel
       would have outlined it in accent right beside a filled-accent Save — two
       primary-looking buttons, one of them the dismiss. */
    QPushButton[variant="ghost"]:focus {{
        border: 1px solid {t.text_primary};
        color: {t.text_primary};
    }}
    QPushButton[variant="danger"] {{
        background-color: transparent;
        color: {t.status_crit};
        border: 1px solid {_rgba(t.status_crit, 0.40)};
    }}
    QPushButton[variant="danger"]:hover {{
        background-color: {_rgba(t.status_crit, 0.14)};
        border-color: {t.status_crit};
    }}

    /* Dense data table (Overview / Logs consume this later) */
    .DenseTable {{
        background-color: {t.surface_2};
        gridline-color: transparent;
    }}
    .DenseTable QHeaderView::section {{
        background-color: {t.surface_1};
        color: {t.text_muted};
        {hf}
        font-size: {fs["small"]}pt;
        font-weight: 600;
        border: none;
        border-bottom: 1px solid {t.border_default};
        padding: 6px 10px;
    }}
    .DenseTable::item {{
        padding: 5px 10px;
    }}

    /* Global shell chrome: top ribbon + bottom footer */
    #StatusRibbon_Root {{
        background-color: {t.surface_1};
        border-bottom: 1px solid {t.border_default};
    }}
    .RibbonBrand {{
        color: {t.text_primary};
        {hf}
        font-size: {fs["card_title"]}pt;
        font-weight: 700;
    }}
    #StatusFooter_Root {{
        background-color: {t.surface_1};
        border-top: 1px solid {t.border_default};
    }}

    /* Modal scrim: a translucent dark veil behind a ModalDialog (no live blur —
       Qt cannot do backdrop-filter efficiently). */
    #ModalScrim {{
        background-color: {_rgba(t.app_bg, 0.66)};
    }}
    """
