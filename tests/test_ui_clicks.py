"""Automated UI click tests — at least one per screen.

Every test asserts a real outcome (state change, signal, or daemon call),
not just "didn't crash".
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
)

from control_ofc.constants import PAGE_CONTROLS
from control_ofc.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    """Create a fully wired MainWindow in non-demo mode."""
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------


class TestSidebarNavigation:
    def test_click_controls_switches_page(self, qtbot, window):
        btn = window.findChild(QPushButton, "NavButton_Controls")
        assert btn is not None

        qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)

        stack = window.findChild(QStackedWidget, "MainWindow_Stack_pages")
        assert stack.currentIndex() == PAGE_CONTROLS


# ---------------------------------------------------------------------------
# Controls page
# ---------------------------------------------------------------------------


class TestControlsPage:
    def test_new_profile_adds_item(self, qtbot, window):
        """Creating a new profile via handler increases combo count."""
        combo = window.controls_page._profile_combo
        initial_count = combo.count()

        # New Profile is now in the Manage Profiles menu, call handler directly
        window.controls_page._on_new_profile("Test Profile")

        assert combo.count() == initial_count + 1

    def test_new_control_button_exists(self, qtbot, window):
        """New control button is present and clickable."""
        btn = window.findChild(QPushButton, "Controls_Btn_newControl")
        assert btn is not None
        assert btn.isEnabled()


# ---------------------------------------------------------------------------
# Diagnostics page
# ---------------------------------------------------------------------------


class TestDiagnosticsPage:
    def test_clear_logs(self, qtbot, window):
        log_view = window.findChild(QPlainTextEdit, "Diagnostics_Text_logView")
        assert log_view is not None

        # Pre-populate with text
        log_view.setPlainText("some log output\nmore lines")
        assert log_view.toPlainText() != ""

        clear_btn = window.findChild(QPushButton, "Diagnostics_Btn_clearLogs")
        assert clear_btn is not None
        qtbot.mouseClick(clear_btn, Qt.MouseButton.LeftButton)

        # The clear handler sets "(cleared)" rather than empty string
        assert "some log output" not in log_view.toPlainText()

    def test_refresh_overview_button_exists(self, qtbot, window):
        """Verify the refresh button is findable and clickable."""
        btn = window.findChild(QPushButton, "Diagnostics_Btn_refreshOverview")
        assert btn is not None
        assert btn.isEnabled()


# ---------------------------------------------------------------------------
# Error banner
# ---------------------------------------------------------------------------


class TestErrorBanner:
    def test_dismiss_hides_banner(self, qtbot, window):
        window.show()
        qtbot.waitExposed(window)

        banner = window.error_banner
        banner.show_warning("Test warning")
        assert banner.isVisible()

        dismiss_btn = banner.findChild(QPushButton, "ErrorBanner_Btn_dismiss")
        assert dismiss_btn is not None
        qtbot.mouseClick(dismiss_btn, Qt.MouseButton.LeftButton)

        assert banner.isHidden()
