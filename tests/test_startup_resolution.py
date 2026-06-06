"""Tests for startup-page (F3) and demo-mode/probe (F4, DEC-139) resolution."""

from __future__ import annotations

import pytest

from control_ofc.api.errors import DaemonTimeout, DaemonUnavailable
from control_ofc.main import _probe_daemon, _resolve_demo_mode
from control_ofc.services.app_settings_service import AppSettings
from control_ofc.ui.main_window import _resolve_startup_page

# --- F4: demo-mode decision (pure) ------------------------------------------


@pytest.mark.parametrize(
    "cli_demo,on_disconnect,reachable,expected",
    [
        (True, False, True, True),  # --demo always wins
        (True, True, True, True),
        (False, True, False, True),  # opted in + unreachable → demo
        (False, True, True, False),  # reachable → live
        (False, False, False, False),  # not opted in → stay live even if down
        (False, False, True, False),
    ],
)
def test_resolve_demo_mode(cli_demo, on_disconnect, reachable, expected):
    assert _resolve_demo_mode(cli_demo, on_disconnect, reachable) is expected


# --- F3: startup-page resolution (pure) -------------------------------------


def test_startup_page_restore_last():
    s = AppSettings(restore_last_page=True, last_page_index=2, default_startup_page=0)
    assert _resolve_startup_page(s, 4) == 2


def test_startup_page_uses_default_when_not_restoring():
    s = AppSettings(restore_last_page=False, default_startup_page=3, last_page_index=1)
    assert _resolve_startup_page(s, 4) == 3


def test_startup_page_clamps_out_of_range():
    assert (
        _resolve_startup_page(AppSettings(restore_last_page=False, default_startup_page=99), 4) == 3
    )
    assert _resolve_startup_page(AppSettings(restore_last_page=True, last_page_index=99), 4) == 3


# --- F4: daemon probe -------------------------------------------------------


class _FakeClient:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.closed = False

    def status(self):
        if self._behaviour == "unavailable":
            raise DaemonUnavailable()
        if self._behaviour == "timeout":
            raise DaemonTimeout()
        return object()

    def close(self):
        self.closed = True


def _patch_client(monkeypatch, behaviour):
    created = {}

    def factory(socket_path, timeout):
        created["client"] = _FakeClient(behaviour)
        return created["client"]

    monkeypatch.setattr("control_ofc.main.DaemonClient", factory)
    return created


def test_probe_unavailable_returns_false(monkeypatch):
    created = _patch_client(monkeypatch, "unavailable")
    assert _probe_daemon("/x/y.sock") is False
    assert created["client"].closed is True  # client always closed


def test_probe_reachable_returns_true(monkeypatch):
    _patch_client(monkeypatch, "ok")
    assert _probe_daemon("/x/y.sock") is True


def test_probe_timeout_stays_live(monkeypatch):
    # A slow/hung daemon is present — do NOT silently drop the user into demo.
    _patch_client(monkeypatch, "timeout")
    assert _probe_daemon("/x/y.sock") is True
