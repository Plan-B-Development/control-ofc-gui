"""R47: Persistence hardening — bundle completeness, geometry, settings roundtrip."""

from __future__ import annotations

import json

from control_ofc.api.models import ConnectionState, OperationMode
from control_ofc.services.app_settings_service import AppSettings, AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.profile_service import ProfileService


class TestSupportBundleCompleteness:
    """Support bundle includes app settings, profiles, themes."""

    def test_bundle_includes_app_settings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_mode(OperationMode.AUTOMATIC)

        svc = AppSettingsService()
        svc.update(theme_name="Custom Red")

        diag = DiagnosticsService(state, settings_service=svc)
        bundle_path = tmp_path / "bundle.json"
        diag.export_support_bundle(bundle_path)

        data = json.loads(bundle_path.read_text())
        assert "app_settings" in data
        assert data["app_settings"]["theme_name"] == "Custom Red"

    def test_bundle_includes_profile_inventory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        state = AppState()
        psvc = ProfileService()
        psvc.load()

        diag = DiagnosticsService(state, profile_service=psvc)
        bundle_path = tmp_path / "bundle.json"
        diag.export_support_bundle(bundle_path)

        data = json.loads(bundle_path.read_text())
        assert "profiles" in data
        assert isinstance(data["profiles"], list)

    def test_bundle_includes_theme_info(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        state = AppState()
        svc = AppSettingsService()

        diag = DiagnosticsService(state, settings_service=svc)
        bundle_path = tmp_path / "bundle.json"
        diag.export_support_bundle(bundle_path)

        data = json.loads(bundle_path.read_text())
        assert "themes" in data
        assert "active_theme" in data["themes"]
        assert "series_color_count" in data["themes"]

    def test_bundle_without_services_still_works(self, tmp_path):
        state = AppState()
        diag = DiagnosticsService(state)
        bundle_path = tmp_path / "bundle.json"
        diag.export_support_bundle(bundle_path)

        data = json.loads(bundle_path.read_text())
        assert "timestamp" in data
        assert "app_settings" not in data  # No service → no settings


class TestWindowGeometryPersistence:
    """Window geometry and last page persist across settings roundtrip."""

    def test_geometry_fields_exist(self):
        settings = AppSettings()
        assert settings.last_page_index == 0
        assert settings.window_geometry == [100, 100, 1200, 800]

    def test_geometry_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()
        svc.update(last_page_index=2, window_geometry=[50, 50, 1400, 900])

        svc2 = AppSettingsService()
        svc2.load()
        assert svc2.settings.last_page_index == 2
        assert svc2.settings.window_geometry == [50, 50, 1400, 900]

    def test_geometry_in_export(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()
        svc.update(last_page_index=3, window_geometry=[200, 200, 800, 600])

        export_path = tmp_path / "exported.json"
        svc.export_settings(export_path)

        data = json.loads(export_path.read_text())
        assert data["last_page_index"] == 3
        assert data["window_geometry"] == [200, 200, 800, 600]


class TestSettingsRoundtripCompleteness:
    """All settings fields survive save/load/export/import roundtrips."""

    def test_all_fields_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()
        svc.update(
            theme_name="Neon",
            fun_mode=False,
            show_splash=False,
            show_gpu_zero_rpm_warning=False,
            series_colors={"sensor:cpu": "#ff0000"},
            fan_aliases={"openfan:ch00": "Intake"},
            last_page_index=2,
            window_geometry=[10, 20, 1000, 700],
        )

        svc2 = AppSettingsService()
        svc2.load()
        s = svc2.settings
        assert s.theme_name == "Neon"
        assert s.fun_mode is False
        assert s.show_splash is False
        assert s.show_gpu_zero_rpm_warning is False
        assert s.series_colors == {"sensor:cpu": "#ff0000"}
        assert s.fan_aliases == {"openfan:ch00": "Intake"}
        assert s.last_page_index == 2
        assert s.window_geometry == [10, 20, 1000, 700]


class TestImportExportR51:
    """R51 import/export improvements: version check, all themes exported."""

    def test_export_includes_export_version(self, tmp_path, monkeypatch):
        """_build_full_export produces export_version field."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from control_ofc.ui.pages.settings_page import SettingsPage

        svc = AppSettingsService()
        svc.load()
        page = SettingsPage.__new__(SettingsPage)
        page._settings_svc = svc
        page._profile_service = None
        export = page._build_full_export()
        assert export["export_version"] == 1
        assert "settings" in export

    def test_export_includes_all_custom_themes(self, tmp_path, monkeypatch):
        """All custom themes exported, not just the active one."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from control_ofc.paths import themes_dir

        td = themes_dir()
        td.mkdir(parents=True, exist_ok=True)
        import json

        (td / "neon.json").write_text(json.dumps({"bg": "#111"}))
        (td / "ocean.json").write_text(json.dumps({"bg": "#222"}))

        from control_ofc.ui.pages.settings_page import SettingsPage

        svc = AppSettingsService()
        svc.load()
        page = SettingsPage.__new__(SettingsPage)
        page._settings_svc = svc
        page._profile_service = None
        export = page._build_full_export()
        assert "themes" in export
        assert "neon" in export["themes"]
        assert "ocean" in export["themes"]
