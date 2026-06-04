"""Tests for the last-resort uncaught-exception hook in ``main.py``.

This is the defense-in-depth net (the primary daemon-disconnect handling is in
the API client + workers). It must: log the traceback at CRITICAL, drop a
breadcrumb into diagnostics when wired, never raise even if diagnostics fails,
and delegate KeyboardInterrupt/SystemExit to the default hook.
"""

from __future__ import annotations

import logging
import sys

from control_ofc import main as main_mod


def _exc_info(exc: BaseException):
    try:
        raise exc
    except BaseException:
        return sys.exc_info()


def test_handle_uncaught_logs_critical_with_traceback(caplog):
    caplog.set_level(logging.DEBUG)
    main_mod._handle_uncaught(*_exc_info(ValueError("boom")))
    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert crit, "expected a CRITICAL log record"
    assert crit[0].exc_info is not None
    assert crit[0].exc_info[1].args[0] == "boom"


def test_handle_uncaught_records_diagnostics(monkeypatch):
    recorded = []

    class FakeDiag:
        def log_event(self, level, source, message):
            recorded.append((level, source, message))

    monkeypatch.setattr(main_mod, "_diagnostics", FakeDiag())
    main_mod._handle_uncaught(*_exc_info(RuntimeError("kaboom")))
    assert recorded, "expected a diagnostics breadcrumb"
    level, source, message = recorded[0]
    assert level == "error"
    assert source == "gui"
    assert "RuntimeError" in message
    assert "kaboom" in message


def test_handle_uncaught_survives_diagnostics_failure(monkeypatch, caplog):
    class BoomDiag:
        def log_event(self, *a, **k):
            raise RuntimeError("diagnostics is down")

    monkeypatch.setattr(main_mod, "_diagnostics", BoomDiag())
    caplog.set_level(logging.DEBUG)
    # Must not raise even though diagnostics.log_event blows up.
    main_mod._handle_uncaught(*_exc_info(ValueError("boom")))
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_handle_uncaught_delegates_keyboardinterrupt(monkeypatch):
    seen = {}
    monkeypatch.setattr(sys, "__excepthook__", lambda et, ev, tb: seen.update(type=et))
    main_mod._handle_uncaught(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert seen.get("type") is KeyboardInterrupt


def test_set_uncaught_diagnostics(monkeypatch):
    monkeypatch.setattr(main_mod, "_diagnostics", None)
    sentinel = object()
    main_mod._set_uncaught_diagnostics(sentinel)
    assert main_mod._diagnostics is sentinel
