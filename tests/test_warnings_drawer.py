"""DEC-182: warnings surface — the inspector's Warnings tab + the status-strip
warning chip that opens it.

The Warnings tab renders ``AppState.active_warnings`` (the dedup-keyed set the
chip counts), NOT the diagnostics event log. Each row carries severity, summary,
component, timestamp, a suggested next action, and an expandable raw detail.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.dashboard_inspector import WarningsView, next_action_for_warning


class TestNextActionForWarning:
    @pytest.mark.parametrize(
        "warning, has_action",
        [
            ({"_key": "sensor_stale:s1", "source": "sensor"}, True),
            ({"_key": "fan_stale:f1", "source": "fan"}, True),
            ({"_key": "fan_stall:f1", "source": "fan"}, True),
            ({"_key": "api_version_skew", "source": "api"}, True),
            # source-only fallback (key prefix unrecognised) still yields an action,
            # exercising the `or source == "fan"/"sensor"` branches in isolation:
            ({"_key": "weird:1", "source": "fan"}, True),
            ({"_key": "weird:2", "source": "sensor"}, True),
            ({"_key": "weird:thing", "source": "mystery"}, False),
            ({}, False),
        ],
    )
    def test_action_presence(self, warning, has_action):
        assert (next_action_for_warning(warning) is not None) is has_action

    def test_stall_action_is_fan_specific_not_generic_stale(self):
        stall = next_action_for_warning({"_key": "fan_stall:f1", "source": "fan"})
        stale = next_action_for_warning({"_key": "fan_stale:f1", "source": "fan"})
        assert stall != stale
        assert "spinning" in stall.lower()


class TestWarningsView:
    def test_empty_state(self, qtbot, app_state):
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        assert view.entry_count() == 0
        assert view.findChild(QLabel, "WarningsView_Label_empty") is not None
        assert view.findChild(QPushButton, "WarningsView_Btn_clearAll").isEnabled() is False

    def test_renders_all_row_fields(self, qtbot, app_state):
        app_state.add_warning(
            level="warning",
            source="sensor",
            message="Sensor 'CPU' is stale (age 5000ms)",
            key="sensor_stale:cpu",
        )
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        assert view.entry_count() == 1
        sev = view.findChild(QLabel, "WarningsView_Entry_0_severity")
        summ = view.findChild(QLabel, "WarningsView_Entry_0_summary")
        comp = view.findChild(QLabel, "WarningsView_Entry_0_component")
        assert "WARNING" in sev.text()
        assert "stale" in summ.text().lower()
        assert "sensor" in comp.text().lower()
        assert view.findChild(QLabel, "WarningsView_Entry_0_time") is not None

    def test_next_action_shown_for_known_type(self, qtbot, app_state):
        app_state.add_warning(
            level="error", source="fan", message="Fan 'f1' stall detected", key="fan_stall:f1"
        )
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        act = view.findChild(QLabel, "WarningsView_Entry_0_action")
        assert act is not None
        assert act.text().startswith("→")

    def test_error_level_uses_critical_chip(self, qtbot, app_state):
        app_state.add_warning(level="error", source="fan", message="stall", key="fan_stall:f1")
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        sev = view.findChild(QLabel, "WarningsView_Entry_0_severity")
        assert sev.property("class") == "CriticalChip"
        assert "ERROR" in sev.text()

    def test_raw_detail_is_expandable_not_hover_only(self, qtbot, app_state):
        """The raw detail lives behind a focusable CollapsibleSection (click/
        keyboard), so it is reachable without hover (WCAG 1.4.13)."""
        from control_ofc.ui.widgets.collapsible_section import CollapsibleSection

        app_state.add_warning(
            level="warning", source="sensor", message="stale", key="sensor_stale:x"
        )
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        detail = view.findChild(CollapsibleSection, "WarningsView_Entry_0_detail")
        assert detail is not None
        assert detail.is_expanded() is False
        detail.set_expanded(True)
        assert detail.is_expanded() is True

    def test_refreshes_on_count_change(self, qtbot, app_state):
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        assert view.entry_count() == 0
        app_state.add_warning(
            level="warning", source="api", message="version skew", key="api_version_skew"
        )
        assert view.entry_count() == 1  # signal-driven refresh, no manual call

    def test_clear_all_empties(self, qtbot, app_state):
        app_state.add_warning(
            level="warning", source="sensor", message="stale", key="sensor_stale:x"
        )
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        assert view.entry_count() == 1
        view.findChild(QPushButton, "WarningsView_Btn_clearAll").click()
        assert view.entry_count() == 0
        assert app_state.warning_count == 0
        # Empty state re-renders through the warnings_cleared → refresh path.
        assert view.findChild(QLabel, "WarningsView_Label_empty") is not None
        assert view.findChild(QPushButton, "WarningsView_Btn_clearAll").isEnabled() is False

    def test_daemon_strings_render_as_plain_text(self, qtbot, app_state):
        """Sensor-label markup must not be reinterpreted as rich text (truthful UI)."""
        from PySide6.QtCore import Qt

        app_state.add_warning(
            level="warning",
            source="sensor",
            message="Sensor '<b>CPU</b>' is stale",
            key="sensor_stale:cpu",
        )
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        summ = view.findChild(QLabel, "WarningsView_Entry_0_summary")
        assert summ.textFormat() == Qt.TextFormat.PlainText
        assert "<b>" in summ.text()  # shown literally, not rendered

    def test_none_state_is_safe(self, qtbot):
        view = WarningsView(None)
        qtbot.addWidget(view)
        assert view.entry_count() == 0


class TestWarningChipOpensWarningsTab:
    def test_chip_reopens_inspector_and_selects_warnings(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page._set_inspector_shown(False)  # start collapsed to prove the chip re-opens
        app_state.add_warning(
            level="warning", source="sensor", message="stale", key="sensor_stale:x"
        )
        page._status_strip.warning_clicked.emit()
        assert page._inspector_shown is True
        assert page._inspector.tabs().currentWidget().objectName() == "Inspector_Tab_warnings"

    def test_warning_chip_button_click_opens_warnings(self, qtbot, app_state):
        """End-to-end via the real chip button: _warning.clicked → warning_clicked
        → _open_warnings. A severed connection fails here, unlike the .emit() test."""
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        page._set_inspector_shown(False)
        app_state.add_warning(
            level="warning", source="sensor", message="stale", key="sensor_stale:x"
        )
        chip = page._status_strip.findChild(QPushButton, "StatusStrip_Chip_warnings")
        assert chip is not None
        chip.click()
        assert page._inspector_shown is True
        assert page._inspector.tabs().currentWidget().objectName() == "Inspector_Tab_warnings"

    def test_warnings_tab_reflects_active_warnings(self, qtbot, app_state):
        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        app_state.add_warning(level="error", source="fan", message="stall", key="fan_stall:f1")
        assert page._warnings_view.entry_count() == 1
