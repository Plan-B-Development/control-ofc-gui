"""V3 Audit P1/P2 regression tests — write counter, poll count, migration dedup,
atomic export, fan alias whitespace.
"""

from __future__ import annotations

import json

from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import (
    _migrate_v1_profile,
)

# ---------------------------------------------------------------------------
# WP-V3-05: Poll count after reconnect
# ---------------------------------------------------------------------------


class TestPollCountAfterReconnect:
    """After reconnect, poll() resets _poll_count to 0 so capabilities are re-fetched."""

    def test_reconnect_resets_poll_count_for_capabilities_refetch(self):
        from control_ofc.services.polling import _PollWorker
        from tests.conftest import FakeDaemonClient

        worker = _PollWorker("/tmp/nonexistent.sock")
        worker._client = FakeDaemonClient()
        worker._consecutive_failures = 1
        worker._poll_count = 2  # 2 % min(8, 2**1) == 0, passes backoff

        worker.poll()

        assert worker._poll_count == 0
        assert worker._consecutive_failures == 0

    def test_normal_poll_increments_count(self):
        from control_ofc.services.polling import _PollWorker
        from tests.conftest import FakeDaemonClient

        worker = _PollWorker("/tmp/nonexistent.sock")
        worker._client = FakeDaemonClient()
        worker._consecutive_failures = 0
        worker._poll_count = 5

        worker.poll()

        assert worker._poll_count == 6
        assert worker._consecutive_failures == 0


# ---------------------------------------------------------------------------
# WP-V3-08: V1 migration deduplicates fan members
# ---------------------------------------------------------------------------


class TestV1MigrationDedup:
    """V1 migration skips duplicate fan assignments."""

    def test_duplicate_fan_skipped(self):
        data = {
            "id": "test",
            "name": "Test",
            "assignments": [
                {
                    "target_id": "openfan:ch00",
                    "target_type": "fan",
                    "curve": {"points": [{"temp_c": 30, "output_pct": 20}]},
                },
                {
                    "target_id": "openfan:ch00",
                    "target_type": "fan",
                    "curve": {"points": [{"temp_c": 50, "output_pct": 60}]},
                },
            ],
        }
        profile = _migrate_v1_profile(data)

        # Both controls exist (one curve each), but only the first has the member
        assert len(profile.controls) == 2
        members_with_fan = [c for c in profile.controls if c.members]
        assert len(members_with_fan) == 1
        assert members_with_fan[0].members[0].member_id == "openfan:ch00"

    def test_unique_fans_preserved(self):
        data = {
            "id": "test",
            "name": "Test",
            "assignments": [
                {
                    "target_id": "openfan:ch00",
                    "target_type": "fan",
                    "curve": {"points": [{"temp_c": 30, "output_pct": 20}]},
                },
                {
                    "target_id": "openfan:ch01",
                    "target_type": "fan",
                    "curve": {"points": [{"temp_c": 50, "output_pct": 60}]},
                },
            ],
        }
        profile = _migrate_v1_profile(data)
        members_with_fan = [c for c in profile.controls if c.members]
        assert len(members_with_fan) == 2

    def test_group_targets_unaffected(self):
        data = {
            "id": "test",
            "name": "Test",
            "assignments": [
                {
                    "target_id": "all",
                    "target_type": "group",
                    "curve": {"points": [{"temp_c": 30, "output_pct": 20}]},
                },
            ],
        }
        profile = _migrate_v1_profile(data)
        assert len(profile.controls) == 1
        assert len(profile.controls[0].members) == 0


# ---------------------------------------------------------------------------
# WP-V3-09: Atomic write for export_settings
# ---------------------------------------------------------------------------


class TestAtomicExportSettings:
    """export_settings produces a valid JSON file."""

    def test_export_creates_valid_json(self, tmp_path, monkeypatch):
        from control_ofc.services.app_settings_service import AppSettingsService

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()

        export_path = tmp_path / "exported.json"
        svc.export_settings(export_path)

        assert export_path.exists()
        data = json.loads(export_path.read_text())
        assert isinstance(data, dict)

    def test_export_roundtrip(self, tmp_path, monkeypatch):
        from control_ofc.services.app_settings_service import AppSettingsService

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()
        svc.update(theme_name="custom_dark")

        export_path = tmp_path / "exported.json"
        svc.export_settings(export_path)

        imported = svc.import_settings(export_path)
        assert imported.theme_name == "custom_dark"


# ---------------------------------------------------------------------------
# WP-V3-10: Fan alias whitespace validation
# ---------------------------------------------------------------------------


class TestFanAliasWhitespace:
    """Fan alias rejects whitespace-only strings and trims leading/trailing."""

    def test_whitespace_only_clears_alias(self):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "Front Intake")
        assert state.fan_aliases["openfan:ch00"] == "Front Intake"

        state.set_fan_alias("openfan:ch00", "   ")
        assert "openfan:ch00" not in state.fan_aliases

    def test_leading_trailing_trimmed(self):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "  Front Intake  ")
        assert state.fan_aliases["openfan:ch00"] == "Front Intake"

    def test_valid_alias_stored(self):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "Top Exhaust")
        assert state.fan_aliases["openfan:ch00"] == "Top Exhaust"

    def test_empty_string_clears(self):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "Something")
        state.set_fan_alias("openfan:ch00", "")
        assert "openfan:ch00" not in state.fan_aliases

    def test_none_alias_clears(self):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "Something")
        state.set_fan_alias("openfan:ch00", None)
        assert "openfan:ch00" not in state.fan_aliases
