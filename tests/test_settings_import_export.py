"""Page-level import/export: portable export, merge, robustness, chart range.

Covers F6 (chart-range combo), F8 (round-trip/robustness coverage), F10
(malformed-input containment), F11 (import applies live side effects), and
F12 (portable export + machine-state-preserving merge).
"""

from __future__ import annotations

import json
from pathlib import Path

from control_ofc.paths import config_dir, set_path_overrides, themes_dir
from control_ofc.services.app_settings_service import MACHINE_SPECIFIC_KEYS, AppSettingsService


def _make_page(tmp_path, qtbot, monkeypatch, client=None):
    from control_ofc.ui.pages.settings_page import SettingsPage

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    set_path_overrides()  # clear any leaked global directory overrides
    svc = AppSettingsService()
    svc.load()
    page = SettingsPage(settings_service=svc, client=client)
    qtbot.addWidget(page)
    return page, svc


def _drive_import(page, monkeypatch, path: Path):
    """Run _import_settings against *path* by stubbing the open dialog."""
    monkeypatch.setattr(
        "control_ofc.ui.pages.settings_page.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(path), ""),
    )
    page._import_settings()


class TestPortableExport:
    def test_export_excludes_machine_keys(self, tmp_path, qtbot, monkeypatch):
        page, svc = _make_page(tmp_path, qtbot, monkeypatch)
        svc.update(window_geometry=[1, 2, 3, 4], fan_aliases={"f": "n"}, theme_name="X")
        block = page._build_full_export()["settings"]
        for key in MACHINE_SPECIFIC_KEYS:
            assert key not in block
        assert block["theme_name"] == "X"
        assert block["fan_aliases"] == {"f": "n"}  # kept portable (user choice)
        assert block["hidden_chart_series"] == []


class TestImportMerge:
    def test_import_preserves_local_machine_state(self, tmp_path, qtbot, monkeypatch):
        page, svc = _make_page(tmp_path, qtbot, monkeypatch)
        svc.update(window_geometry=[11, 22, 333, 444], profiles_dir_override="/local/x")
        # Even a legacy FULL export carrying machine keys must not clobber them.
        imp = tmp_path / "imp.json"
        imp.write_text(
            json.dumps(
                {
                    "export_version": 1,
                    "settings": {
                        "theme_name": "Imported",
                        "window_geometry": [9, 9, 9, 9],
                        "profiles_dir_override": "/their/path",
                    },
                }
            )
        )
        _drive_import(page, monkeypatch, imp)
        assert svc.settings.theme_name == "Imported"  # portable applied
        assert svc.settings.window_geometry == [11, 22, 333, 444]  # local preserved
        assert svc.settings.profiles_dir_override == "/local/x"  # local preserved

    def test_import_pushes_startup_delay_with_client(self, tmp_path, qtbot, monkeypatch):
        class _RecordingClient:
            def __init__(self):
                self.calls = []

            def set_startup_delay(self, delay_secs):
                self.calls.append(delay_secs)

        client = _RecordingClient()
        page, _svc = _make_page(tmp_path, qtbot, monkeypatch, client=client)
        imp = tmp_path / "imp.json"
        imp.write_text(
            json.dumps({"export_version": 1, "settings": {"daemon_startup_delay_secs": 7}})
        )
        _drive_import(page, monkeypatch, imp)
        assert client.calls == [7]


class TestImportRobustness:
    def test_malformed_root_rejected(self, tmp_path, qtbot, monkeypatch):
        page, svc = _make_page(tmp_path, qtbot, monkeypatch)
        before = svc.settings.theme_name
        for content in ["[]", "null", '"x"', "not json at all"]:
            imp = tmp_path / "bad.json"
            imp.write_text(content)
            _drive_import(page, monkeypatch, imp)  # must not raise
        assert svc.settings.theme_name == before  # unchanged

    def test_unsupported_version_rejected(self, tmp_path, qtbot, monkeypatch):
        page, svc = _make_page(tmp_path, qtbot, monkeypatch)
        imp = tmp_path / "v2.json"
        imp.write_text(json.dumps({"export_version": 2, "settings": {"theme_name": "Nope"}}))
        _drive_import(page, monkeypatch, imp)
        assert svc.settings.theme_name != "Nope"

    def test_non_numeric_version_rejected(self, tmp_path, qtbot, monkeypatch):
        page, svc = _make_page(tmp_path, qtbot, monkeypatch)
        imp = tmp_path / "vx.json"
        imp.write_text(json.dumps({"export_version": "x", "settings": {"theme_name": "Nope"}}))
        _drive_import(page, monkeypatch, imp)  # must not leak TypeError
        assert svc.settings.theme_name != "Nope"

    def test_profiles_as_list_does_not_crash(self, tmp_path, qtbot, monkeypatch):
        page, _svc = _make_page(tmp_path, qtbot, monkeypatch)
        imp = tmp_path / "pl.json"
        imp.write_text(json.dumps({"export_version": 1, "profiles": ["not", "a", "dict"]}))
        _drive_import(page, monkeypatch, imp)  # must not let AttributeError escape

    def test_backup_created_before_import(self, tmp_path, qtbot, monkeypatch):
        page, svc = _make_page(tmp_path, qtbot, monkeypatch)
        svc.save()  # ensure an app_settings.json exists to back up
        imp = tmp_path / "imp.json"
        imp.write_text(json.dumps({"export_version": 1, "settings": {"theme_name": "New"}}))
        _drive_import(page, monkeypatch, imp)
        backups = list((config_dir() / "backups").glob("settings_backup_*.json"))
        assert backups


class TestChartRangeCombo:
    def test_combo_matches_time_ranges(self, tmp_path, qtbot, monkeypatch):
        from control_ofc.ui.widgets.timeline_chart import TIME_RANGES

        page, _svc = _make_page(tmp_path, qtbot, monkeypatch)
        combo = page._chart_range_combo
        labels = [combo.itemText(i) for i in range(combo.count())]
        assert labels == [label for label, _ in TIME_RANGES]
        assert labels[4] == "15m"  # default index 4 → 15m


class TestThemeColorImport:
    def test_import_themes_skips_bad_color(self, tmp_path, qtbot, monkeypatch):
        page, _svc = _make_page(tmp_path, qtbot, monkeypatch)
        data = {
            "good": {"name": "Good", "app_bg": "#112233", "version": 2},
            "bad": {"name": "Bad", "app_bg": "}; color:red", "version": 2},
        }
        skipped = page._import_themes(data)
        assert skipped == 1
        assert (themes_dir() / "good.json").exists()
        assert not (themes_dir() / "bad.json").exists()
