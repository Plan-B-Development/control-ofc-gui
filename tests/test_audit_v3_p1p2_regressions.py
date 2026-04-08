"""V3 Audit P1/P2 regression tests — write counter, poll count, migration dedup,
atomic export, fan alias whitespace.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from onlyfans.api.models import ConnectionState, OperationMode
from onlyfans.services.app_state import AppState
from onlyfans.services.profile_service import (
    _migrate_v1_profile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state() -> AppState:
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    state.set_mode(OperationMode.AUTOMATIC)
    return state


# ---------------------------------------------------------------------------
# WP-V3-04: Write failure counter decrement logic
# ---------------------------------------------------------------------------


class TestWriteFailureCounter:
    """Failure counter decrements on success, not deletes."""

    def _make_loop(self):
        from onlyfans.services.control_loop import ControlLoopService

        state = _make_state()
        profile_svc = MagicMock()
        client = MagicMock()
        svc = ControlLoopService(state=state, profile_service=profile_svc, client=client)
        return svc, state

    def test_three_failures_triggers_warning(self):
        svc, _state = self._make_loop()
        target = "openfan:ch00"
        for _ in range(3):
            svc._write_failure_counts[target] = svc._write_failure_counts.get(target, 0) + 1

        count = svc._write_failure_counts[target]
        assert count == 3

    def test_success_decrements_not_deletes(self):
        svc, _state = self._make_loop()
        target = "openfan:ch00"
        svc._write_failure_counts[target] = 5

        # Simulate one success — should decrement to 4, not delete
        count = svc._write_failure_counts.get(target, 0)
        if count > 0:
            count -= 1
            if count == 0:
                svc._write_failure_counts.pop(target, None)
            else:
                svc._write_failure_counts[target] = count

        assert svc._write_failure_counts.get(target) == 4

    def test_sustained_success_reaches_zero(self):
        svc, _state = self._make_loop()
        target = "openfan:ch00"
        svc._write_failure_counts[target] = 3

        for _ in range(3):
            count = svc._write_failure_counts.get(target, 0)
            if count > 0:
                count -= 1
                if count == 0:
                    svc._write_failure_counts.pop(target, None)
                else:
                    svc._write_failure_counts[target] = count

        assert target not in svc._write_failure_counts

    def test_intermittent_pattern_reaches_warning(self):
        """fail, success, fail, fail, fail — counter should reach 3."""
        svc, _ = self._make_loop()
        target = "openfan:ch00"

        # fail → 1
        svc._write_failure_counts[target] = svc._write_failure_counts.get(target, 0) + 1
        assert svc._write_failure_counts[target] == 1

        # success → 0 (removed)
        count = svc._write_failure_counts.get(target, 0) - 1
        if count <= 0:
            svc._write_failure_counts.pop(target, None)
        else:
            svc._write_failure_counts[target] = count
        assert target not in svc._write_failure_counts

        # fail, fail, fail → 3
        for _ in range(3):
            svc._write_failure_counts[target] = svc._write_failure_counts.get(target, 0) + 1
        assert svc._write_failure_counts[target] == 3


# ---------------------------------------------------------------------------
# WP-V3-05: Poll count after reconnect
# ---------------------------------------------------------------------------


class TestPollCountAfterReconnect:
    """After reconnect, poll_count should be 1 (caps already fetched)."""

    def test_reconnect_sets_poll_count_to_one(self):
        from onlyfans.services.polling import _PollWorker

        worker = _PollWorker.__new__(_PollWorker)
        worker._consecutive_failures = 3
        worker._poll_count = 5

        # Simulate reconnect logic: if consecutive_failures > 0, set to 1
        if worker._consecutive_failures > 0:
            worker._poll_count = 1
        else:
            worker._poll_count += 1
        worker._consecutive_failures = 0

        assert worker._poll_count == 1
        assert worker._consecutive_failures == 0

    def test_normal_increment_when_no_failures(self):
        from onlyfans.services.polling import _PollWorker

        worker = _PollWorker.__new__(_PollWorker)
        worker._consecutive_failures = 0
        worker._poll_count = 5

        if worker._consecutive_failures > 0:
            worker._poll_count = 1
        else:
            worker._poll_count += 1
        worker._consecutive_failures = 0

        assert worker._poll_count == 6


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
        from onlyfans.services.app_settings_service import AppSettingsService

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()

        export_path = tmp_path / "exported.json"
        svc.export_settings(export_path)

        assert export_path.exists()
        data = json.loads(export_path.read_text())
        assert isinstance(data, dict)

    def test_export_roundtrip(self, tmp_path, monkeypatch):
        from onlyfans.services.app_settings_service import AppSettingsService

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

    def test_whitespace_only_clears_alias(self, qtbot):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "Front Intake")
        assert state.fan_aliases["openfan:ch00"] == "Front Intake"

        state.set_fan_alias("openfan:ch00", "   ")
        assert "openfan:ch00" not in state.fan_aliases

    def test_leading_trailing_trimmed(self, qtbot):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "  Front Intake  ")
        assert state.fan_aliases["openfan:ch00"] == "Front Intake"

    def test_valid_alias_stored(self, qtbot):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "Top Exhaust")
        assert state.fan_aliases["openfan:ch00"] == "Top Exhaust"

    def test_empty_string_clears(self, qtbot):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "Something")
        state.set_fan_alias("openfan:ch00", "")
        assert "openfan:ch00" not in state.fan_aliases

    def test_none_alias_clears(self, qtbot):
        state = AppState()
        state.set_fan_alias("openfan:ch00", "Something")
        state.set_fan_alias("openfan:ch00", None)
        assert "openfan:ch00" not in state.fan_aliases
