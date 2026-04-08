"""Tests for S1 — import validation of profiles and themes at import time."""

from __future__ import annotations

import pytest

from onlyfans.services.profile_service import Profile
from onlyfans.ui.theme import ThemeTokens, _migrate_tokens

# ---------------------------------------------------------------------------
# Profile validation (Profile.from_dict)
# ---------------------------------------------------------------------------


class TestProfileImportValidation:
    """Profile.from_dict() must reject structurally invalid data."""

    def test_valid_profile_roundtrips(self):
        data = {
            "id": "abc123",
            "name": "Test Profile",
            "version": 3,
            "controls": [],
            "curves": [
                {
                    "id": "c1",
                    "name": "CPU Curve",
                    "type": "graph",
                    "sensor_id": "cpu0",
                    "points": [{"temp_c": 30, "output_pct": 20}],
                }
            ],
        }
        profile = Profile.from_dict(data)
        assert profile.name == "Test Profile"
        assert len(profile.curves) == 1

    def test_empty_dict_produces_default_profile(self):
        profile = Profile.from_dict({})
        assert profile.name == ""
        assert profile.version == 3
        assert profile.controls == []
        assert profile.curves == []

    def test_invalid_control_mode_raises(self):
        data = {
            "controls": [{"mode": "not_a_real_mode"}],
            "curves": [],
        }
        with pytest.raises(ValueError, match="not_a_real_mode"):
            Profile.from_dict(data)

    def test_non_dict_curve_points_raises(self):
        data = {
            "curves": [{"points": ["not", "dicts"]}],
            "controls": [],
        }
        with pytest.raises(TypeError):
            Profile.from_dict(data)

    def test_non_dict_control_member_raises(self):
        data = {
            "controls": [{"members": [42]}],
            "curves": [],
        }
        with pytest.raises((TypeError, AttributeError)):
            Profile.from_dict(data)


# ---------------------------------------------------------------------------
# Theme validation (_migrate_tokens + ThemeTokens construction)
# ---------------------------------------------------------------------------


class TestThemeImportValidation:
    """Theme import validation must catch non-dict data."""

    def test_valid_theme_dict_accepted(self):
        data = {"name": "My Theme", "app_bg": "#112233", "version": 2}
        migrated = _migrate_tokens(data)
        tokens = ThemeTokens()
        for k, v in migrated.items():
            if hasattr(tokens, k):
                setattr(tokens, k, v)
        assert tokens.name == "My Theme"
        assert tokens.app_bg == "#112233"

    def test_empty_dict_produces_defaults(self):
        migrated = _migrate_tokens({})
        tokens = ThemeTokens()
        for k, v in migrated.items():
            if hasattr(tokens, k):
                setattr(tokens, k, v)
        assert tokens.name == "Default Dark"
        assert tokens.version == 2

    def test_unknown_keys_ignored(self):
        data = {"name": "Custom", "totally_fake_key": "whatever", "version": 2}
        migrated = _migrate_tokens(data)
        tokens = ThemeTokens()
        for k, v in migrated.items():
            if hasattr(tokens, k):
                setattr(tokens, k, v)
        assert tokens.name == "Custom"
        assert not hasattr(tokens, "totally_fake_key")


# ---------------------------------------------------------------------------
# Integration: _import_profiles / _import_themes on SettingsPage
# ---------------------------------------------------------------------------


class TestSettingsPageImportValidation:
    """SettingsPage import methods must skip invalid entries."""

    def _make_page(self, tmp_path, qtbot, monkeypatch):
        """Create a minimal SettingsPage wired to tmp_path directories."""
        from onlyfans.services.app_settings_service import AppSettingsService
        from onlyfans.ui.pages.settings_page import SettingsPage

        # Override paths so imports write to tmp_path
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        themes = tmp_path / "themes"
        themes.mkdir()
        monkeypatch.setattr("onlyfans.paths.profiles_dir", lambda: profiles)
        monkeypatch.setattr("onlyfans.ui.pages.settings_page.themes_dir", lambda: themes)

        svc = AppSettingsService()
        page = SettingsPage(settings_service=svc)
        qtbot.addWidget(page)
        return page, profiles, themes

    def test_valid_profile_imported(self, tmp_path, qtbot, monkeypatch):
        page, profiles, _themes = self._make_page(tmp_path, qtbot, monkeypatch)
        data = {
            "good": {
                "id": "g1",
                "name": "Good",
                "version": 3,
                "controls": [],
                "curves": [],
            }
        }
        skipped = page._import_profiles(data)
        assert skipped == 0
        assert (profiles / "good.json").exists()

    def test_invalid_profile_skipped(self, tmp_path, qtbot, monkeypatch):
        page, profiles, _themes = self._make_page(tmp_path, qtbot, monkeypatch)
        data = {
            "good": {
                "id": "g1",
                "name": "Good",
                "version": 3,
                "controls": [],
                "curves": [],
            },
            "bad": {
                "controls": [{"mode": "invalid_mode_value"}],
                "curves": [],
            },
            "also_bad": "not a dict",
        }
        skipped = page._import_profiles(data)
        assert skipped == 2
        assert (profiles / "good.json").exists()
        assert not (profiles / "bad.json").exists()
        assert not (profiles / "also_bad.json").exists()

    def test_valid_theme_imported(self, tmp_path, qtbot, monkeypatch):
        page, _profiles, themes = self._make_page(tmp_path, qtbot, monkeypatch)
        data = {"mytheme": {"name": "My Theme", "app_bg": "#001122", "version": 2}}
        skipped = page._import_themes(data)
        assert skipped == 0
        assert (themes / "mytheme.json").exists()

    def test_invalid_theme_skipped(self, tmp_path, qtbot, monkeypatch):
        page, _profiles, themes = self._make_page(tmp_path, qtbot, monkeypatch)
        data = {
            "good": {"name": "Good", "version": 2},
            "bad": "not a dict",
        }
        skipped = page._import_themes(data)
        assert skipped == 1
        assert (themes / "good.json").exists()
        assert not (themes / "bad.json").exists()
