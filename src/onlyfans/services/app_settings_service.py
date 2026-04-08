"""Application settings — GUI-owned preferences persisted as JSON."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from onlyfans.paths import app_settings_path, atomic_write

log = logging.getLogger(__name__)


@dataclass
class AppSettings:
    """GUI preferences. Persisted at ~/.config/onlyfans/app_settings.json."""

    version: int = 1
    default_startup_page: int = 0  # PAGE_DASHBOARD
    restore_last_page: bool = True
    demo_on_disconnect: bool = False
    remember_last_profile: bool = True
    chart_default_range_index: int = 4  # 30m in TimelineChart
    theme_name: str = "Default Dark"
    fun_mode: bool = True
    show_splash: bool = True
    fan_aliases: dict[str, str] = field(default_factory=dict)
    hidden_chart_series: list[str] = field(default_factory=list)
    card_sensor_bindings: dict[str, str] = field(default_factory=dict)
    show_gpu_zero_rpm_warning: bool = True
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

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> AppSettings:
        return AppSettings(
            version=data.get("version", 1),
            default_startup_page=data.get("default_startup_page", 0),
            restore_last_page=data.get("restore_last_page", True),
            demo_on_disconnect=data.get("demo_on_disconnect", False),
            remember_last_profile=data.get("remember_last_profile", True),
            chart_default_range_index=data.get("chart_default_range_index", 4),
            theme_name=data.get("theme_name", "Default Dark"),
            fun_mode=data.get("fun_mode", True),
            show_splash=data.get("show_splash", True),
            fan_aliases=data.get("fan_aliases", {}),
            hidden_chart_series=data.get("hidden_chart_series", []),
            card_sensor_bindings=data.get("card_sensor_bindings", {}),
            show_gpu_zero_rpm_warning=data.get("show_gpu_zero_rpm_warning", True),
            series_colors=data.get("series_colors", {}),
            last_page_index=data.get("last_page_index", 0),
            window_geometry=data.get("window_geometry", [100, 100, 1200, 800]),
            profiles_dir_override=data.get("profiles_dir_override", ""),
            themes_dir_override=data.get("themes_dir_override", ""),
            export_default_dir=data.get("export_default_dir", ""),
            wizard_spindown_seconds=data.get("wizard_spindown_seconds", 8),
            daemon_startup_delay_secs=data.get("daemon_startup_delay_secs", 0),
            hide_igpu_sensors=data.get("hide_igpu_sensors", True),
            hide_unused_fan_headers=data.get("hide_unused_fan_headers", True),
        )


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
            except Exception as e:
                log.warning("Failed to load app settings from %s: %s — using defaults", path, e)
                self._settings = AppSettings()
        else:
            self._settings = AppSettings()

    def save(self) -> None:
        atomic_write(app_settings_path(), json.dumps(self._settings.to_dict(), indent=2) + "\n")

    def update(self, **kwargs: object) -> None:
        """Update specific settings and save."""
        for key, value in kwargs.items():
            if hasattr(self._settings, key):
                setattr(self._settings, key, value)
        self.save()

    def export_settings(self, path: Path) -> None:
        """Export settings to a user-chosen file (atomic write for crash safety)."""
        from onlyfans.paths import atomic_write

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
