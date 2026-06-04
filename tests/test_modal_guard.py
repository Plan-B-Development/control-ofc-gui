"""Regression tests for the autouse modal guard in ``conftest.py``.

These protect the test infrastructure itself. The static ``QMessageBox`` /
``QFileDialog`` / ``QInputDialog`` helpers spin a nested event loop and block
forever in a headless run; the autouse ``_neutralize_modals`` fixture patches
them to safe non-blocking defaults. If that fixture is removed or broken, the
delete-profile tests would hang the whole suite again — these assertions turn
that silent landmine into an explicit failure.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox


def test_question_declines_by_default():
    # On a real modal this blocks until the user clicks; the guard returns No
    # (a *safe* default — destructive confirmations are declined, not accepted).
    assert QMessageBox.question(None, "t", "m") == QMessageBox.StandardButton.No


def test_message_helpers_are_non_blocking():
    assert QMessageBox.warning(None, "t", "m") == QMessageBox.StandardButton.Ok
    assert QMessageBox.information(None, "t", "m") == QMessageBox.StandardButton.Ok
    assert QMessageBox.critical(None, "t", "m") == QMessageBox.StandardButton.Ok


def test_file_pickers_return_cancelled():
    assert QFileDialog.getOpenFileName(None, "t") == ("", "")
    assert QFileDialog.getSaveFileName(None, "t") == ("", "")
    assert QFileDialog.getExistingDirectory(None, "t") == ""


def test_input_dialog_returns_cancelled():
    assert QInputDialog.getText(None, "t", "label") == ("", False)


def test_per_test_override_beats_the_guard(monkeypatch):
    # A per-test override is applied after the autouse fixture, so it wins —
    # this is the mechanism the delete-profile tests use to accept the confirm.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert QMessageBox.question(None, "t", "m") == QMessageBox.StandardButton.Yes
