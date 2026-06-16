"""Application settings — GUI-owned preferences persisted as JSON."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from control_ofc.colors import is_valid_color
from control_ofc.paths import app_settings_path, atomic_write

log = logging.getLogger(__name__)

# Settings keys excluded from the portable (shareable) export — machine/session
# state and hardware-id-keyed maps that should not travel between machines. The
# importer also strips these from incoming files, so a shared config can never
# move another user's window or wipe local data-dir overrides (DEC-140).
MACHINE_SPECIFIC_KEYS = frozenset(
    {
        "window_geometry",
        "last_page_index",
        "controls_card_sizes",
        "card_sensor_bindings",
        "series_colors",
        "diagnostics_hidden_sensor_ids",
        "sensor_class_overrides",
        "acknowledged_kernel_warnings",
        "profiles_dir_override",
        "themes_dir_override",
        "export_default_dir",
    }
)

_CARD_SIZES = frozenset({"compact", "comfortable", "large"})

# Window-geometry sanity bound — rejects corruption and absurd off-screen values.
_GEOM_MAX = 32000


# --- Untrusted-input coercion helpers (DEC-137) -----------------------------
# Every helper takes an arbitrary JSON value plus the field default and returns
# a well-typed, in-range value. None of them raise: a bad value becomes the
# default (or, for collections, drops only the offending entries).


def _as_bool(value: object, default: bool) -> bool:
    """Accept only real booleans; reject 0/1 and everything else."""
    return value if isinstance(value, bool) else default


def _as_int(value: object, default: int, lo: int | None = None, hi: int | None = None) -> int:
    """Coerce to int (rejecting bool/float/str), then clamp to [lo, hi]."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _as_str(value: object, default: str, maxlen: int = 512) -> str:
    return value[:maxlen] if isinstance(value, str) else default


def _as_str_dict(value: object, default: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, dict):
        return dict(default)
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


# AIO Phase 1 (DEC-156): user sensor-classification overrides. Only "coolant"
# is offered today; the whitelist stops an untrusted settings/import file from
# injecting an arbitrary source_class string into the display layer.
_SENSOR_OVERRIDE_VALUES = frozenset({"coolant"})


def _as_sensor_overrides(value: object, default: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, dict):
        return dict(default)
    return {k: v for k, v in value.items() if isinstance(k, str) and v in _SENSOR_OVERRIDE_VALUES}


def _as_str_list(value: object, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    return [v for v in value if isinstance(v, str)]


def _as_color_dict(value: object, default: dict[str, str]) -> dict[str, str]:
    """Keep only str -> valid-hex-colour entries (drops injection/garbage)."""
    if not isinstance(value, dict):
        return dict(default)
    return {k: v for k, v in value.items() if isinstance(k, str) and is_valid_color(v)}


def _as_enum(value: object, allowed: frozenset[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _as_geometry(value: object, default: list[int]) -> list[int]:
    """Require exactly 4 sane ints [x, y, w, h]; else the default."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return list(default)
    if any(isinstance(n, bool) or not isinstance(n, int) for n in value):
        return list(default)
    x, y, w, h = value
    if not (-_GEOM_MAX <= x <= _GEOM_MAX and -_GEOM_MAX <= y <= _GEOM_MAX):
        return list(default)
    if not (1 <= w <= _GEOM_MAX and 1 <= h <= _GEOM_MAX):
        return list(default)
    return [x, y, w, h]


def _as_card_sizes(value: object, default: dict[str, list[int]]) -> dict[str, list[int]]:
    """Keep only str -> [width, height] entries of two positive ints."""
    if not isinstance(value, dict):
        return dict(default)
    result: dict[str, list[int]] = {}
    for key, dims in value.items():
        if not isinstance(key, str):
            continue
        if not isinstance(dims, (list, tuple)) or len(dims) != 2:
            continue
        if any(
            isinstance(n, bool) or not isinstance(n, int) or not (0 < n <= _GEOM_MAX) for n in dims
        ):
            continue
        result[key] = [dims[0], dims[1]]
    return result


@dataclass
class AppSettings:
    """GUI preferences. Persisted at ~/.config/control-ofc/app_settings.json."""

    version: int = 1
    default_startup_page: int = 0  # PAGE_DASHBOARD
    restore_last_page: bool = True
    demo_on_disconnect: bool = False
    chart_default_range_index: int = 4  # 15m in TimelineChart
    theme_name: str = "Default Dark"
    fan_aliases: dict[str, str] = field(default_factory=dict)
    hidden_chart_series: list[str] = field(default_factory=list)
    card_sensor_bindings: dict[str, str] = field(default_factory=dict)
    show_gpu_zero_rpm_warning: bool = True
    # DEC-157: one-time popup explaining the AIO pump floor ("don't run the
    # pump too low"), shown when an AIO pump is first added to a control. Mirrors
    # show_gpu_zero_rpm_warning — a behaviour preference that travels with export.
    show_aio_pump_info: bool = True
    series_colors: dict[str, str] = field(default_factory=dict)
    last_page_index: int = 0
    window_geometry: list[int] = field(default_factory=lambda: [100, 100, 1200, 800])

    # Configurable data directories (empty = use XDG default)
    profiles_dir_override: str = ""
    themes_dir_override: str = ""
    export_default_dir: str = ""

    # Behaviour settings
    wizard_spindown_seconds: int = 8  # Fan Wizard spin-down timer (5-12s)
    daemon_startup_delay_secs: int = 0  # Daemon startup delay (0-30s, daemon-side)
    hide_igpu_sensors: bool = True  # Auto-hide iGPU sensors when dGPU present
    hide_unused_fan_headers: bool = True  # Auto-hide fan headers with 0 RPM
    show_hardware_guidance: bool = True  # Show hardware readiness guidance in diagnostics

    # DEC-128: Controls-page card density tier — "compact" | "comfortable" |
    # "large". Cards auto-scale with the theme font size; this tier multiplies
    # that size so the user can trade density for readability. Unknown values
    # fall back to "comfortable" at render time (card_metrics.card_dimensions).
    card_size: str = "comfortable"

    # DEC-129: per-card user size overrides on the Controls page, keyed by
    # control/curve id → [width, height] (snapped to the shared lattice).
    # Absent key = theme-derived sizing (DEC-128). Pruned of ids that no
    # longer exist in any known profile whenever a size is saved.
    controls_card_sizes: dict[str, list[int]] = field(default_factory=dict)

    # DEC-098: kernel-warning IDs the user has already dismissed for this
    # GPU. Keyed by warning.id (not session-scoped). Persisting prevents the
    # popup from re-firing on every restart for a known-bad-kernel that the
    # user has acknowledged but cannot or will not change.
    acknowledged_kernel_warnings: list[str] = field(default_factory=list)

    # DEC-117: sensor IDs the user has hidden in the Diagnostics > Sensors
    # table. Local to that page only (the dashboard chart uses its own
    # SeriesSelectionModel-backed list). Hidden sensors collapse into a
    # group row at the bottom of the table, not silently removed.
    diagnostics_hidden_sensor_ids: list[str] = field(default_factory=list)

    # DEC-156: user overrides forcing a sensor's classification, keyed by stable
    # sensor id -> source_class (only "coolant" today). GUI-owned policy — the
    # daemon stays hardware-truthful; this lets the user mark a coolant sensor
    # the conservative auto-classifier missed. Machine-specific (sensor ids are
    # local), so excluded from portable export.
    sensor_class_overrides: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> AppSettings:
        """Build settings from an untrusted dict (on-disk file or import).

        Never raises: every field is type-checked and coerced, out-of-range
        values are clamped, and malformed entries fall back to the field
        default. This is the trust boundary for both the persisted settings
        file and user-supplied import files (DEC-137).
        """
        if not isinstance(data, dict):
            return AppSettings()
        return AppSettings(
            version=_as_int(data.get("version"), 1, lo=1),
            default_startup_page=_as_int(data.get("default_startup_page"), 0, lo=0, hi=99),
            restore_last_page=_as_bool(data.get("restore_last_page"), True),
            demo_on_disconnect=_as_bool(data.get("demo_on_disconnect"), False),
            chart_default_range_index=_as_int(
                data.get("chart_default_range_index"), 4, lo=0, hi=99
            ),
            theme_name=_as_str(data.get("theme_name"), "Default Dark"),
            fan_aliases=_as_str_dict(data.get("fan_aliases"), {}),
            hidden_chart_series=_as_str_list(data.get("hidden_chart_series"), []),
            card_sensor_bindings=_as_str_dict(data.get("card_sensor_bindings"), {}),
            show_gpu_zero_rpm_warning=_as_bool(data.get("show_gpu_zero_rpm_warning"), True),
            show_aio_pump_info=_as_bool(data.get("show_aio_pump_info"), True),
            series_colors=_as_color_dict(data.get("series_colors"), {}),
            last_page_index=_as_int(data.get("last_page_index"), 0, lo=0, hi=99),
            window_geometry=_as_geometry(data.get("window_geometry"), [100, 100, 1200, 800]),
            profiles_dir_override=_as_str(data.get("profiles_dir_override"), ""),
            themes_dir_override=_as_str(data.get("themes_dir_override"), ""),
            export_default_dir=_as_str(data.get("export_default_dir"), ""),
            wizard_spindown_seconds=_as_int(data.get("wizard_spindown_seconds"), 8, lo=5, hi=12),
            daemon_startup_delay_secs=_as_int(
                data.get("daemon_startup_delay_secs"), 0, lo=0, hi=30
            ),
            hide_igpu_sensors=_as_bool(data.get("hide_igpu_sensors"), True),
            hide_unused_fan_headers=_as_bool(data.get("hide_unused_fan_headers"), True),
            show_hardware_guidance=_as_bool(data.get("show_hardware_guidance"), True),
            card_size=_as_enum(data.get("card_size"), _CARD_SIZES, "comfortable"),
            controls_card_sizes=_as_card_sizes(data.get("controls_card_sizes"), {}),
            acknowledged_kernel_warnings=_as_str_list(data.get("acknowledged_kernel_warnings"), []),
            diagnostics_hidden_sensor_ids=_as_str_list(
                data.get("diagnostics_hidden_sensor_ids"), []
            ),
            sensor_class_overrides=_as_sensor_overrides(data.get("sensor_class_overrides"), {}),
        )

    def portable_dict(self) -> dict:
        """Return ``to_dict()`` minus machine-specific keys (shareable export)."""
        return {k: v for k, v in self.to_dict().items() if k not in MACHINE_SPECIFIC_KEYS}


class AppSettingsService:
    """Load, save, and manage application settings."""

    def __init__(self) -> None:
        self._settings = AppSettings()

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def load(self) -> None:
        path = app_settings_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self._settings = AppSettings.from_dict(data)
                log.info("Loaded app settings from %s", path)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as e:
                log.warning("Failed to load app settings from %s: %s — using defaults", path, e)
                self._settings = AppSettings()
        else:
            self._settings = AppSettings()

    def save(self) -> None:
        atomic_write(app_settings_path(), json.dumps(self._settings.to_dict(), indent=2) + "\n")

    def update(self, **kwargs: object) -> None:
        """Update specific settings and save.

        Routes through ``AppSettings.from_dict`` so every value is coerced and
        range-checked exactly like a fresh load (P2-A) — a wrong-typed value can
        no longer persist in memory and then fail to reload next launch.
        """
        merged = self._settings.to_dict()
        merged.update({k: v for k, v in kwargs.items() if hasattr(self._settings, k)})
        self._settings = AppSettings.from_dict(merged)
        self.save()

    def export_settings(self, path: Path) -> None:
        """Export settings to a user-chosen file (atomic write for crash safety)."""
        from control_ofc.paths import atomic_write

        atomic_write(path, json.dumps(self._settings.to_dict(), indent=2) + "\n")

    def import_settings(self, path: Path) -> AppSettings:
        """Import settings from file. Returns the loaded settings (caller decides to apply)."""
        data = json.loads(path.read_text())
        return AppSettings.from_dict(data)

    def import_settings_from_dict(self, data: dict) -> AppSettings:
        """Import settings from a dict (e.g., from a comprehensive export file)."""
        return AppSettings.from_dict(data)

    def apply_imported(self, settings: AppSettings) -> None:
        """Apply imported settings and save."""
        self._settings = settings
        self.save()
