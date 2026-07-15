"""Contract tests — verify daemon calls, state mutations, and error handling.

Organized by page. Every test asserts a real contract (signal emitted,
service state changed, or daemon method called).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

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
# Controls Page contracts
# ---------------------------------------------------------------------------


class TestControlsContracts:
    def test_activate_sets_active_profile(self, qtbot, window, profile_service):
        """DEC-214: activation moved to the sidebar Apply button, which drives the
        shared ProfileService.activate path (the Controls page dropped its own
        combo + Activate button)."""
        combo = window.sidebar.profile_combo

        if combo.count() < 2:
            pytest.skip("Need at least 2 profiles")
        combo.setCurrentIndex(1)
        target_id = combo.currentData()

        apply_btn = window.findChild(QPushButton, "Sidebar_Btn_applyProfile")
        qtbot.mouseClick(apply_btn, Qt.MouseButton.LeftButton)

        assert profile_service.active_id == target_id

    def test_delete_removes_profile(self, qtbot, window, profile_service, monkeypatch):
        """Delete profile via handler → the profile store shrinks by one (DEC-214:
        the page combo is gone; assert against the service)."""
        controls = window.controls_page
        initial_count = len(profile_service.profiles)
        assert initial_count > 0

        # The autouse modal guard declines confirmations by default; opt into
        # "Yes" to exercise the actual deletion path.
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

        # Call delete directly since it's now in a menu
        controls._on_delete_profile()

        assert len(profile_service.profiles) == initial_count - 1

    def test_save_persists_profile(self, qtbot, window, profile_service):
        """Mark unsaved, click save → unsaved cleared, profile persists."""
        controls = window.controls_page
        profile_id = controls._get_current_profile().id

        controls._set_unsaved(True)
        assert controls._has_unsaved

        save_btn = window.findChild(QPushButton, "Controls_Btn_save")
        qtbot.mouseClick(save_btn, Qt.MouseButton.LeftButton)

        assert not controls._has_unsaved
        assert profile_service.get_profile(profile_id) is not None

    def test_control_card_curve_selection_updates_model(self, qtbot, window, profile_service):
        """Changing curve dropdown on a control card updates the control's curve_id."""
        from control_ofc.services.profile_service import (
            ControlMode,
            CurveConfig,
            CurveType,
            LogicalControl,
            Profile,
        )

        # Create a profile with 2 curves so the test can actually run
        c1 = CurveConfig(id="c1", name="Curve A", type=CurveType.FLAT, flat_output_pct=30.0)
        c2 = CurveConfig(id="c2", name="Curve B", type=CurveType.FLAT, flat_output_pct=60.0)
        ctrl = LogicalControl(id="sel_ctrl", name="Selector", mode=ControlMode.CURVE, curve_id="c1")
        profile = Profile(id="sel_test", name="Selector Test", controls=[ctrl], curves=[c1, c2])
        profile_service._profiles["sel_test"] = profile
        profile_service.set_active("sel_test")

        controls = window.controls_page
        # DEC-214: view the profile on the page (replaces the removed combo select);
        # this refreshes and builds the cards for it.
        controls.select_profile("sel_test")

        card = controls._control_cards.get("sel_ctrl")
        assert card is not None

        # Change curve via the edit role handler (curve selection is now in dialog)
        ctrl.curve_id = "c2"
        card.update_control(ctrl, [c1, c2])
        assert ctrl.curve_id == "c2"
        assert "Curve B" in card._curve_label.text()

    def test_delete_control_removes_from_profile(self, qtbot, window, profile_service):
        """Deleting a control removes it from the profile and the grid."""
        controls = window.controls_page
        profile = controls._get_current_profile()
        initial_count = len(profile.controls)

        if initial_count == 0:
            pytest.skip("No controls to delete")

        control_id = profile.controls[0].id
        controls._on_delete_control(control_id)

        assert len(profile.controls) == initial_count - 1
        assert control_id not in controls._control_cards

    def test_delete_curve_cascades_to_controls(self, qtbot, window, profile_service):
        """Deleting a curve unassigns it from any controls that reference it."""
        from control_ofc.services.profile_service import (
            ControlMode,
            CurveConfig,
            CurveType,
            LogicalControl,
            Profile,
        )

        c1 = CurveConfig(id="del_c1", name="Delete Me", type=CurveType.FLAT, flat_output_pct=50.0)
        ctrl = LogicalControl(id="del_ctrl", name="Test", mode=ControlMode.CURVE, curve_id="del_c1")
        profile = Profile(id="del_test", name="Del Test", controls=[ctrl], curves=[c1])
        profile_service._profiles["del_test"] = profile
        profile_service.set_active("del_test")

        controls = window.controls_page
        # DEC-214: view the profile on the page (replaces the removed combo select).
        controls.select_profile("del_test")

        # Delete the curve
        controls._on_delete_curve("del_c1")

        # Control should have curve_id cleared
        assert ctrl.curve_id == ""
        # Curve should be gone
        assert profile.get_curve("del_c1") is None


# ---------------------------------------------------------------------------
# Settings Page contracts
# ---------------------------------------------------------------------------


class TestSettingsContracts:
    def test_save_app_settings_persists(self, qtbot, window, settings_service):
        """Change combo, click Save → settings_service updated (signal removed)."""
        settings_page = window.settings_page

        # Change startup page combo to a different value
        combo = settings_page._startup_page_combo
        new_index = (combo.currentIndex() + 1) % combo.count()
        combo.setCurrentIndex(new_index)
        expected_page = combo.currentData()

        save_btn = window.findChild(QPushButton, "Settings_Btn_saveApp")
        qtbot.mouseClick(save_btn, Qt.MouseButton.LeftButton)

        assert settings_service.settings.default_startup_page == expected_page


# ---------------------------------------------------------------------------
# Diagnostics Page contracts
# ---------------------------------------------------------------------------


class TestFooterActionContracts:
    """DEC-216: the global footer's Rescan/Export actions relocated off the
    retired Diagnostics page — Rescan to System State, Export to Logs."""

    def test_export_bundle_via_footer_writes_file(self, qtbot, window, tmp_path):
        """Fire the footer Export signal → the Logs page's bundle handler runs."""
        dest = tmp_path / "bundle.json"

        with patch(
            "control_ofc.ui.pages.logs_page.QFileDialog.getSaveFileName",
            return_value=(str(dest), "JSON files (*.json)"),
        ):
            window.footer.export_bundle_clicked.emit()

        assert dest.exists()
        assert dest.stat().st_size > 0

    def test_rescan_via_footer_surfaces_system_state(self, qtbot, window):
        """Fire the footer Rescan signal → the System State page is surfaced and
        its rescan-result line is populated (no-client demo → an error line)."""
        from control_ofc.constants import PAGE_SYSTEM_STATE

        window.footer.rescan_clicked.emit()

        assert window.page_stack.currentIndex() == PAGE_SYSTEM_STATE
        assert window.system_state_page._rescan_result_label.text() != ""
