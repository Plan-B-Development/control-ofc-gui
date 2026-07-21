"""Warnings surface — the status-strip warning chip + the dialog it opens
(DEC-184, was DEC-182's inspector Warnings tab).

The dialog hosts a :class:`WarningsView` over ``AppState.active_warnings`` (the
dedup-keyed set the chip counts), NOT the diagnostics event log. Each row carries
severity, summary, component, timestamp, a suggested next action, and an
expandable raw detail.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.ui.pages.logs_page import LogsPage
from control_ofc.ui.widgets.warnings_view import WarningsView, next_action_for_warning


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
        assert view._entry_count == 0
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
        assert view._entry_count == 1
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
        assert detail._expanded is False
        detail._header.setChecked(True)
        assert detail._expanded is True

    def test_refreshes_on_count_change(self, qtbot, app_state):
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        assert view._entry_count == 0
        app_state.add_warning(
            level="warning", source="api", message="version skew", key="api_version_skew"
        )
        assert view._entry_count == 1  # signal-driven refresh, no manual call

    def test_clear_all_empties(self, qtbot, app_state):
        app_state.add_warning(
            level="warning", source="sensor", message="stale", key="sensor_stale:x"
        )
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        assert view._entry_count == 1
        view.findChild(QPushButton, "WarningsView_Btn_clearAll").click()
        assert view._entry_count == 0
        assert app_state.warning_count == 0
        # Empty state re-renders through the warnings_cleared → refresh path.
        assert view.findChild(QLabel, "WarningsView_Label_empty") is not None
        assert view.findChild(QPushButton, "WarningsView_Btn_clearAll").isEnabled() is False

    def test_unknown_warning_renders_without_action_label(self, qtbot, app_state):
        """A warning whose type implies no next action shows no action line (the
        action label is omitted, not blank)."""
        app_state.add_warning(level="warning", source="mystery", message="odd", key="weird:1")
        view = WarningsView(app_state)
        qtbot.addWidget(view)
        assert view._entry_count == 1
        assert view.findChild(QLabel, "WarningsView_Entry_0_action") is None

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
        assert view._entry_count == 0


class TestWarningChipOpensWarningsDialog:
    def test_logs_page_hosts_the_warnings_view(self, qtbot, app_state):
        """DEC-222: Logs is the single warnings surface (was a Dashboard dialog
        opened from the status-strip chip, both since removed)."""
        page = LogsPage(diagnostics_service=DiagnosticsService(), state=app_state)
        qtbot.addWidget(page)
        view = page.findChild(WarningsView, "Logs_View_warnings")
        assert view is not None

    def test_logs_warnings_view_reflects_active_warnings(self, qtbot, app_state):
        """The hosted view renders AppState.active_warnings, not the event log."""
        page = LogsPage(diagnostics_service=DiagnosticsService(), state=app_state)
        qtbot.addWidget(page)
        assert page._warnings_view._entry_count == 0

        app_state.add_warning(level="error", source="fan", message="stall", key="fan_stall:f1")
        assert page._warnings_view._entry_count == 1

    def test_logs_warnings_view_is_live(self, qtbot, app_state):
        """A warning raised after construction must appear without a manual refresh
        — the view subscribes to AppState, and Logs is now the only place it shows."""
        app_state.add_warning(level="warning", source="sensor", message="stale", key="s:1")
        page = LogsPage(diagnostics_service=DiagnosticsService(), state=app_state)
        qtbot.addWidget(page)
        assert page._warnings_view._entry_count == 1

        app_state.add_warning(level="error", source="fan", message="stall", key="f:1")
        assert page._warnings_view._entry_count == 2

        app_state.clear_warnings()
        assert page._warnings_view._entry_count == 0
