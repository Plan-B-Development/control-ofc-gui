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
    """The Settings-page export produces a valid JSON file via atomic_write.

    Re-vehicled in the 2026-07-21 sweep: the original vehicle was the dead
    ``AppSettingsService.export_settings`` (never called by the UI); the
    atomicity + valid-JSON properties now pin the LIVE page export path.
    """

    def _export_via_page(self, tmp_path, qtbot, monkeypatch):
        from control_ofc.services.app_settings_service import AppSettingsService
        from control_ofc.ui.pages.settings_page import SettingsPage

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()
        svc.load()
        svc.update(theme_name="Exported Theme")
        page = SettingsPage(settings_service=svc)
        qtbot.addWidget(page)
        export_path = tmp_path / "exported.json"
        monkeypatch.setattr(
            "control_ofc.ui.pages.settings_page.QFileDialog.getSaveFileName",
            staticmethod(lambda *a, **k: (str(export_path), "")),
        )
        page._export_settings()
        return export_path

    def test_export_creates_valid_json(self, tmp_path, qtbot, monkeypatch):
        export_path = self._export_via_page(tmp_path, qtbot, monkeypatch)
        assert export_path.exists()
        data = json.loads(export_path.read_text())
        assert isinstance(data, dict)

    def test_export_roundtrip(self, tmp_path, qtbot, monkeypatch):
        from control_ofc.services.app_settings_service import AppSettings

        export_path = self._export_via_page(tmp_path, qtbot, monkeypatch)
        data = json.loads(export_path.read_text())
        restored = AppSettings.from_dict(data.get("settings", data))
        assert restored.theme_name == "Exported Theme"


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


class TestPollWorkerShutdownLatch:
    """DEC-256: a worker must stop accepting work once shutdown is called.

    ``shutdown()`` closed the client, but the 1 Hz timer→``poll()`` connection is
    a QUEUED connection — an invocation already sitting in the worker thread's
    event queue still ran afterwards, and ``_ensure_client()`` rebuilt the very
    client that had just been closed, opening a fresh socket and blocking on it.
    That kept the thread busy past ``wait(2000)`` and forced
    ``QThread::terminate()``, orphaning a half-written request.
    """

    def test_queued_poll_after_shutdown_does_no_work(self, monkeypatch):
        from control_ofc.services.polling import _PollWorker

        worker = _PollWorker(socket_path="/tmp/nonexistent-shutdown.sock")

        built = []
        monkeypatch.setattr(
            "control_ofc.services.polling.DaemonClient",
            lambda **kw: built.append(kw) or object(),
        )

        worker.shutdown()
        worker.poll()  # the queued invocation that used to slip through

        assert not built, (
            "a poll queued before shutdown must not open a new client afterwards — "
            "this is what kept the thread busy until terminate()"
        )

    def test_the_client_is_never_resurrected_after_shutdown(self, monkeypatch):
        """The half that made the latch necessary: closing is not enough if the
        next call simply builds another one."""
        import pytest

        from control_ofc.services.polling import _PollWorker

        worker = _PollWorker(socket_path="/tmp/nonexistent-shutdown.sock")
        monkeypatch.setattr("control_ofc.services.polling.DaemonClient", lambda **kw: object())

        # Before shutdown the client is built on demand, as normal.
        assert worker._ensure_client() is not None

        worker.shutdown()
        with pytest.raises(RuntimeError):
            worker._ensure_client()

    def test_shutdown_latches_before_closing(self):
        """Order matters: a poll slipping in between must find the latch set,
        not an open client it can keep using."""
        from control_ofc.services.polling import _PollWorker

        worker = _PollWorker(socket_path="/tmp/nonexistent-shutdown.sock")
        seen: list[bool] = []

        def _record_close() -> None:
            seen.append(worker._shutting_down)

        worker._close_client = _record_close  # type: ignore[method-assign]
        worker.shutdown()

        assert seen == [True], "the latch must be set before the client is closed"
