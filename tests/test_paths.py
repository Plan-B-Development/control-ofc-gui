"""Tests for XDG path helpers."""

from __future__ import annotations

from control_ofc.paths import config_dir, ensure_dirs, profiles_dir, state_dir, themes_dir


def test_config_dir_ends_with_control_ofc():
    assert config_dir().name == "control-ofc"


def test_profiles_dir_inside_config():
    assert profiles_dir().parent == config_dir()


def test_themes_dir_inside_config():
    assert themes_dir().parent == config_dir()


def test_state_dir_ends_with_control_ofc():
    assert state_dir().name == "control-ofc"


def test_ensure_dirs_creates_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    ensure_dirs()
    assert (tmp_path / "config" / "control-ofc" / "profiles").is_dir()
    assert (tmp_path / "config" / "control-ofc" / "themes").is_dir()
    assert (tmp_path / "state" / "control-ofc").is_dir()
    assert (tmp_path / "cache" / "control-ofc").is_dir()
