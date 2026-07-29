"""DEC-219 Phase 7 safety net — a golden-master of each god-class page's static
objectName tree (fresh construction, Qt-internal ``qt_*`` names excluded).

Every widget-decomposition / VM-extraction split of these three pages MUST leave
this byte-identical: a changed set means the refactor renamed, dropped, or added
a widget. This is a Feathers characterization test — it captures what the tree
IS today so the "zero behaviour change" split can be verified mechanically.

Regenerate a frozenset ONLY on a deliberate objectName change, and call that out
in the commit message.
"""

from __future__ import annotations

from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import DiagnosticsService
from control_ofc.services.profile_service import ProfileService
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.pages.system_state_page import SystemStatePage


def _object_names(widget) -> set[str]:
    """Every non-empty, non-Qt-internal objectName in the widget subtree."""
    names: set[str] = set()
    if widget.objectName():
        names.add(widget.objectName())
    for child in widget.findChildren(object):
        try:
            name = child.objectName()
        except Exception:
            continue
        if name and not name.startswith("qt_"):
            names.add(name)
    return names


SYSTEM_STATE_OBJECTNAMES = frozenset(
    {
        "SystemState_Bar_speedRange",
        "SystemState_Btn_openReport",
        "SystemState_Btn_restoreGpu",
        "SystemState_Btn_verifyAll",
        "SystemState_Btn_verifyGpu",
        "SystemState_Btn_verifyPwm",
        "SystemState_Card_health",
        "SystemState_Card_interference",
        "SystemState_Card_registry",
        "SystemState_Card_safety",
        "SystemState_Combo_verifyHeader",
        "SystemState_Gauge_reverts",
        "SystemState_Label_contentionTitle",
        "SystemState_Label_gpuModel",
        "SystemState_Label_headerId",
        "SystemState_Label_interferenceExplain",
        "SystemState_Label_registrySummary",
        "SystemState_Label_rescanResult",
        "SystemState_Label_restoreGpuResult",
        "SystemState_Label_subtitle",
        "SystemState_Label_summary",
        "SystemState_Label_thermal",
        "SystemState_Label_title",
        "SystemState_Label_verifyAllProgress",
        "SystemState_Label_verifyGpuResult",
        "SystemState_Label_verifyResult",
        # DEC-234: health overview ↕ registry gained a resize handle; the health
        # row is wrapped in a named pane so it can be a splitter child.
        "SystemState_Pane_healthOverview",
        "SystemState_Pill_issueCount",
        "SystemState_Pill_thermal",
        "SystemState_Root",
        "SystemState_SectionHeader_health",
        "SystemState_SectionHeader_interference",
        "SystemState_SectionHeader_registry",
        "SystemState_SectionHeader_safety",
        "SystemState_Section_advanced",
        "SystemState_Section_advanced_Content",
        "SystemState_Section_advanced_Header",
        "SystemState_Section_advanced_Persistent",
        "SystemState_Splitter_sections",  # DEC-234
        "SystemState_Table_registry",
    }
)

DASHBOARD_OBJECTNAMES = frozenset(
    {
        "Chart_Btn_resetSeries",
        "Chart_Mode_selector",
        "Dashboard_Banner_api_version",
        "Dashboard_Banner_hwmon",
        "Dashboard_Banner_thermal",
        "Dashboard_Btn_apply",
        "Dashboard_Btn_copyEnableCommand",
        "Dashboard_Btn_openReadiness",
        "Dashboard_Combo_profile",
        "Dashboard_Frame_serviceHint",
        "Dashboard_Host_fanCards",
        "Dashboard_Label_enableCommand",
        "Dashboard_Label_fanCardsEmpty",
        "Dashboard_Label_fanCount",
        "Dashboard_Label_subHwmon",
        "Dashboard_Label_subOpenfan",
        "Dashboard_Pane_fanCards",
        "Dashboard_ScrollArea_fanCards",
        "Dashboard_SectionHeader_fans",
        "Dashboard_SectionHeader_telemetry",
        "Dashboard_Section_telemetry",
        "Dashboard_Splitter_horizontal",
        "Dashboard_Splitter_vertical",
        "ErrorBanner_Btn_dismiss",
        "Inspector_Heading",
        "Inspector_Panel_sensors",
        "Inspector_Root",
        "SensorSeriesPanel_Edit_search",
        "TimelineChart_Combo_range",
        "TimelineChart_Label_mode",
        "TimelineChart_Label_range",
    }
)

CONTROLS_OBJECTNAMES = frozenset(
    {
        # DEC-233: the contextual hardware-setup buttons became "Set up ▾" menu
        # actions (Controls_Act_*); the header gained Revert + a Close-editor
        # button + the edited-profile label; the Unassigned label became a button.
        "Controls_Act_configureAio",
        "Controls_Act_dedicateGpu",
        "Controls_Act_fanWizard",
        "Controls_Btn_addCurve",
        "Controls_Btn_closeEditor",
        "Controls_Btn_manageProfiles",
        "Controls_Btn_newControl",
        "Controls_Btn_revert",
        "Controls_Btn_save",
        "Controls_Btn_setup",
        "Controls_Btn_testCurve",
        "Controls_Btn_unassigned",
        "Controls_CurveEditor_main",
        "Controls_Divider_header",
        "Controls_Label_editedProfile",
        "Controls_Label_editorPlaceholder",
        "Controls_Label_editorTitle",
        "Controls_Root",
        "Controls_Section_assignRoles",
        "Controls_Section_curveEditor",
        "Controls_Section_linkLogic",
        "Controls_Splitter_curvesEditor",
        "Controls_Splitter_sections",
        "Controls_Timer_overrideRenew",
        "Controls_Timer_overrideValue",
        "CurveEditor_Btn_addPoint",
        "CurveEditor_Btn_removePoint",
        "CurveEditor_Combo_preset",
        "CurveEditor_Combo_sensor",
        "CurveEditor_Label_sensorValue",
        "CurveEditor_Spin_end_output",
        "CurveEditor_Spin_end_temp",
        "CurveEditor_Spin_flatOutput",
        "CurveEditor_Spin_start_output",
        "CurveEditor_Spin_start_temp",
        "CurveEditor_Spin_trigger_idle_output",
        "CurveEditor_Spin_trigger_idle_temp",
        "CurveEditor_Spin_trigger_load_output",
        "CurveEditor_Spin_trigger_load_temp",
        "CurveEditor_Table_points",
    }
)


def test_system_state_objectname_tree_unchanged(qtbot):
    s = AppState()
    page = SystemStatePage(state=s, diagnostics_service=DiagnosticsService(s))
    qtbot.addWidget(page)
    assert _object_names(page) == SYSTEM_STATE_OBJECTNAMES


def test_dashboard_objectname_tree_unchanged(qtbot):
    page = DashboardPage(state=AppState())
    qtbot.addWidget(page)
    assert _object_names(page) == DASHBOARD_OBJECTNAMES


def test_controls_objectname_tree_unchanged(qtbot):
    page = ControlsPage(state=AppState(), profile_service=ProfileService())
    qtbot.addWidget(page)
    assert _object_names(page) == CONTROLS_OBJECTNAMES
