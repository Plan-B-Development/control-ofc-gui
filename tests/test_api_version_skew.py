"""Tests for the API-version-skew guard (banner + log + diagnostics).

The GUI and daemon are independently packaged (AUR), so a user can upgrade one
without the other. The GUI now compares the daemon's reported ``api_version``
against ``EXPECTED_API_VERSION`` on every capabilities update and surfaces a
non-fatal warning (dashboard banner + keyed state warning + diagnostics flag)
on a mismatch, clearing it when the versions match again.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QWidget

from control_ofc.api.models import Capabilities
from control_ofc.constants import EXPECTED_API_VERSION
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.pages.dashboard_page import DashboardPage

SKEWED = EXPECTED_API_VERSION + 1


def test_expected_api_version_matches_daemon_contract():
    # Documents the cross-repo coupling: must equal the daemon's
    # responses.rs::API_VERSION (currently 1). Bump in lockstep.
    assert EXPECTED_API_VERSION == 1


def test_dashboard_banner_shows_and_clears_on_skew(qtbot, app_state):
    page = DashboardPage(state=app_state)
    qtbot.addWidget(page)
    banner = page.findChild(QWidget, "Dashboard_Banner_api_version")
    assert banner is not None

    # Mismatch → banner visible + a keyed "api" warning recorded.
    app_state.set_capabilities(Capabilities(daemon_version="9.9.9", api_version=SKEWED))
    assert not banner.isHidden()
    assert any(w["source"] == "api" for w in app_state.active_warnings)

    # Matching version → banner hidden + warning removed.
    app_state.set_capabilities(
        Capabilities(daemon_version="9.9.9", api_version=EXPECTED_API_VERSION)
    )
    assert banner.isHidden()
    assert not any(w["source"] == "api" for w in app_state.active_warnings)


def test_diagnostics_text_bundle_flags_skew():
    state = AppState()
    state.set_capabilities(Capabilities(daemon_version="1.0.0", api_version=SKEWED))
    text = DiagnosticsService(state=state).format_daemon_status()
    assert "MISMATCH" in text
    assert f"v{EXPECTED_API_VERSION}" in text


def test_diagnostics_text_bundle_clean_when_matched():
    state = AppState()
    state.set_capabilities(Capabilities(daemon_version="1.0.0", api_version=EXPECTED_API_VERSION))
    text = DiagnosticsService(state=state).format_daemon_status()
    assert "MISMATCH" not in text
    assert f"API version: {EXPECTED_API_VERSION}" in text


def test_support_bundle_records_skew_flag(tmp_path: Path):
    state = AppState()
    state.set_capabilities(Capabilities(daemon_version="1.0.0", api_version=SKEWED))
    dest = tmp_path / "bundle.json"
    DiagnosticsService(state=state).export_support_bundle(dest)
    caps = json.loads(dest.read_text())["capabilities"]
    assert caps["api_version"] == SKEWED
    assert caps["expected_api_version"] == EXPECTED_API_VERSION
    assert caps["api_version_skew"] is True


def test_support_bundle_no_skew_when_matched(tmp_path: Path):
    state = AppState()
    state.set_capabilities(Capabilities(daemon_version="1.0.0", api_version=EXPECTED_API_VERSION))
    dest = tmp_path / "bundle.json"
    DiagnosticsService(state=state).export_support_bundle(dest)
    caps = json.loads(dest.read_text())["capabilities"]
    assert caps["api_version_skew"] is False
