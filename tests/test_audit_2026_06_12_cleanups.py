"""Regression + coverage tests for the 2026-06-12 audit P2/P3 cleanups (GUI side).

Covers: P2-A (settings update coercion), P2-E (lease-id renew race guard),
P2-G (path-override validation), the P3 double-cycle coalescing, P2-J (daemon
config 503 handling), and the QThread write-worker signal path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.errors import DaemonError
from control_ofc.paths import config_dir, profiles_dir, set_path_overrides, themes_dir
from control_ofc.services import control_loop as cl
from control_ofc.services.app_settings_service import AppSettings, AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.control_loop import ControlLoopService
from control_ofc.services.lease_service import LeaseService
from control_ofc.services.profile_service import ProfileService
from control_ofc.ui.pages.settings_page import SettingsPage

# ── P2-A: AppSettingsService.update routes through from_dict coercion ──────────


def test_update_coerces_wrong_typed_value(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    svc = AppSettingsService()
    svc.load()
    # A wrong-typed value must be coerced (P2-A), not stored raw then fail to load.
    svc.update(chart_default_range_index="not-an-int")
    assert isinstance(svc.settings.chart_default_range_index, int)
    # The persisted form reloads cleanly.
    reloaded = AppSettings.from_dict(svc.settings.to_dict())
    assert isinstance(reloaded.chart_default_range_index, int)


# ── P2-E: a renew completing for a stale lease_id must not clobber the new one ─


def test_stale_renew_completion_does_not_clobber_current_lease(qtbot):
    svc = LeaseService(MagicMock())
    svc._lease_id = "NEW"
    # A renew that completes for the previous lease (after re-acquire) is stale.
    svc._on_renew_completed(True, "OLD", 60, "")
    assert svc._lease_id == "NEW"


def test_renew_completion_for_current_lease_applies(qtbot):
    svc = LeaseService(MagicMock())
    svc._lease_id = "CUR"
    svc._renew_in_flight = True
    svc._on_renew_completed(True, "CUR", 60, "")
    assert svc._lease_id == "CUR"
    assert svc._renew_in_flight is False


def test_pause_for_thermal_override_clears_renew_in_flight(qtbot):
    svc = LeaseService(MagicMock())
    svc._lease_id = "X"
    svc._renew_in_flight = True
    svc.pause_for_thermal_override()
    assert svc._renew_in_flight is False
    assert svc._lease_id is None


# ── P2-G: set_path_overrides validates user-configured directories ────────────


def test_set_path_overrides_rejects_invalid_accepts_valid(tmp_path):
    set_path_overrides()  # clear any leaked global override state
    try:
        a_file = tmp_path / "a.json"
        a_file.write_text("{}")
        set_path_overrides(
            profiles_dir="relative/profiles",  # not absolute → rejected
            themes_dir=str(tmp_path / ".." / "themes"),  # contains '..' → rejected
            export_dir=str(a_file),  # existing file → rejected
        )
        assert profiles_dir() == config_dir() / "profiles"  # fell back to XDG default
        assert themes_dir() == config_dir() / "themes"

        valid = tmp_path / "good_profiles"
        valid.mkdir()
        not_yet = tmp_path / "not_yet_created"  # absolute, doesn't exist yet
        set_path_overrides(profiles_dir=str(valid), themes_dir=str(not_yet))
        assert profiles_dir() == valid
        assert themes_dir() == not_yet  # not-yet-existing absolute dir is allowed
    finally:
        set_path_overrides()  # cleanup


# ── P3: _tick coalesces the timer + sensor-update double-trigger ──────────────


def test_tick_coalesces_double_trigger(tmp_path, qtbot, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    profile_service = ProfileService()
    profile_service.load()
    loop = ControlLoopService(AppState(), profile_service)
    try:
        loop._running = True
        loop._manual_override = False
        calls: list[int] = []
        monkeypatch.setattr(loop, "_cycle", lambda: calls.append(1))
        loop._tick()
        loop._tick()  # within half the interval → suppressed
        assert calls == [1]
    finally:
        loop.shutdown()


# ── P2-J: daemon config 503 (persistence_failed) is handled, not crashed ──────


def test_save_app_settings_handles_daemon_persistence_error(tmp_path, qtbot, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    set_path_overrides()
    client = MagicMock()
    client.set_startup_delay.side_effect = DaemonError(
        code="persistence_failed", message="cannot persist runtime config"
    )
    svc = AppSettingsService()
    svc.load()
    page = SettingsPage(settings_service=svc, client=client)
    qtbot.addWidget(page)
    page._save_app_settings()  # must not raise
    assert "not synced" in page._status_label.text().lower()
    client.set_startup_delay.assert_called_once()
    set_path_overrides()


# ── P3 startup-wiring: the QThread write-worker emits the right outcome ────────


def test_write_worker_emits_ok_on_success(qtbot, monkeypatch):
    worker = cl._WriteWorker("/tmp/cofc-nonexistent.sock")
    worker._client = MagicMock()  # avoid constructing a real DaemonClient
    monkeypatch.setattr(cl, "_dispatch_write", lambda *a, **k: True)
    received: list[tuple[str, str]] = []
    worker.write_completed.connect(lambda tid, outcome: received.append((tid, outcome)))
    worker.do_write("openfan:ch00", 50, "lease-1")
    assert received == [("openfan:ch00", cl.OUTCOME_OK)]
    worker.shutdown()


def test_write_worker_emits_validation_when_dispatch_rejects(qtbot, monkeypatch):
    worker = cl._WriteWorker("/tmp/cofc-nonexistent.sock")
    worker._client = MagicMock()
    monkeypatch.setattr(cl, "_dispatch_write", lambda *a, **k: False)
    received: list[tuple[str, str]] = []
    worker.write_completed.connect(lambda tid, outcome: received.append((tid, outcome)))
    worker.do_write("bad:target", 50, "lease-1")
    assert received == [("bad:target", cl.OUTCOME_VALIDATION)]
    worker.shutdown()
