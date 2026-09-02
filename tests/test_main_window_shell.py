"""DEC-208: main-window shell integration.

The 8-entry sidebar routes each interim entry to the right (page, sub-tab), the
ribbon + footer are wired in, and the ribbon Alerts indicator opens Logs. The
preserved-contract checks live in test_ui_clicks (test_status_strip went with the
status strip itself in DEC-222; the footer's re-homed indicators are covered by
test_status_footer).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QPushButton

from control_ofc.api.models import DaemonStatus
from control_ofc.constants import (
    PAGE_HARDWARE,
    PAGE_LOGS,
    PAGE_OVERVIEW,
    PAGE_SYSTEM_STATE,
    PAGE_THEME,
)
from control_ofc.ui.main_window import MainWindow
from tests.layout_helpers import settle_at_minimum


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


def _click_nav(qtbot, window, object_name):
    btn = window.findChild(QPushButton, object_name)
    assert btn is not None, object_name
    qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)


def test_shell_has_ribbon_and_footer(window):
    assert window.status_ribbon.objectName() == "StatusRibbon_Root"
    assert window.footer.objectName() == "StatusFooter_Root"


def test_window_object_name_anchors_the_base_fill(window):
    """DEC-225: the ``#MainWindow`` QSS rule paints the app's base background now
    the blanket ``QWidget`` fill is gone. If this objectName is dropped the window
    renders unpainted while the stylesheet still parses cleanly, so the widget-side
    half of the fix needs its own guard."""
    assert window.objectName() == "MainWindow"


def test_overview_routes_to_overview_page(qtbot, window):
    # DEC-209: Overview now opens its own page, not the Diagnostics Overview tab.
    _click_nav(qtbot, window, "NavButton_Overview")
    assert window.page_stack.currentIndex() == PAGE_OVERVIEW
    assert window.page_stack.currentWidget() is window.overview_page


def test_system_state_routes_to_system_state_page(qtbot, window):
    # DEC-211: System State now opens its own page, not the Diagnostics tab.
    _click_nav(qtbot, window, "NavButton_SystemState")
    assert window.page_stack.currentIndex() == PAGE_SYSTEM_STATE
    assert window.page_stack.currentWidget() is window.system_state_page


def test_hardware_routes_to_hardware_page(qtbot, window):
    # DEC-212: Hardware now opens its own page, not the Diagnostics Readiness tab.
    _click_nav(qtbot, window, "NavButton_Hardware")
    assert window.page_stack.currentIndex() == PAGE_HARDWARE
    assert window.page_stack.currentWidget() is window.hardware_page


def test_theme_routes_to_theme_page(qtbot, window):
    """DEC-215: Theme is now its own page (PAGE_THEME), not a Settings sub-tab."""
    _click_nav(qtbot, window, "NavButton_Theme")
    assert window.page_stack.currentIndex() == PAGE_THEME
    assert window.page_stack.currentWidget() is window.theme_page


def test_logs_routes_to_logs_page(qtbot, window):
    # DEC-210: Logs now opens its own page, not the Diagnostics Event Log tab.
    _click_nav(qtbot, window, "NavButton_Logs")
    assert window.page_stack.currentIndex() == PAGE_LOGS
    assert window.page_stack.currentWidget() is window.logs_page


def test_ribbon_alerts_opens_logs(window):
    window.status_ribbon.alerts_clicked.emit()
    assert window.page_stack.currentIndex() == PAGE_LOGS
    assert window.page_stack.currentWidget() is window.logs_page


def test_sidebar_profile_combo_populated(window, profile_service):
    assert window.sidebar.profile_combo.count() == len(profile_service.profiles)


def test_startup_restores_divergent_page_and_highlights_nav(
    qtbot, app_state, profile_service, settings_service
):
    """DEC-216: a persisted ``last_page_index`` at a *divergent* NAV/PAGE entry
    (Logs: PAGE_LOGS=4 but NAV_LOGS=7) must, at construction, both show the right
    stack page AND highlight the right sidebar entry. ``_resolve_startup_page`` and
    ``select_page`` are each unit-tested; this pins their composition in the
    constructor for a secondary page (audit Rank 3)."""
    from control_ofc.constants import NAV_LOGS

    settings_service.settings.restore_last_page = True
    settings_service.settings.last_page_index = PAGE_LOGS

    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)

    assert win.page_stack.currentIndex() == PAGE_LOGS
    assert win.sidebar._group.checkedId() == NAV_LOGS


def test_warning_count_propagates_to_ribbon_and_footer(window, app_state):
    """DEC-208, as amended by DEC-282: both surfaces are still driven through the live
    MainWindow wiring, but by **different counts**, because they answer different
    questions.

    DEC-208 fed both from one number and acknowledgement zeroed it, so clicking a
    button made the footer announce "All systems nominal" over a condition that was
    still happening. The ribbon badge is an attention indicator and legitimately goes
    quiet when the user has looked; the footer is a health rollup and must not. That
    split is what changed here — the ribbon half of this test is unchanged.

    ``isHidden()`` is used (not ``isVisible()``) so the assertions don't depend on the
    never-shown window's ancestor visibility.
    """
    # No warnings → ribbon badge hidden, footer nominal.
    assert window.status_ribbon._alert_badge.isHidden() is True
    assert window.footer._health_label.text() == "All systems nominal"

    # One warning propagates to both surfaces.
    app_state.add_warning("warning", "test", "sensor stale", key="k1")
    assert window.status_ribbon._alert_badge.isHidden() is False
    assert window.footer._health_label.text() == "1 warning"

    # Acknowledging quietens the attention badge ONLY. The condition is untouched, so
    # the health rollup keeps reporting it — this is the DEC-282 change.
    app_state.acknowledge_all()
    assert window.status_ribbon._alert_badge.isHidden() is True
    assert window.footer._health_label.text() == "1 warning", (
        "acknowledgement is not resolution — the footer must not claim health here"
    )

    # Only the condition actually clearing reverts the footer.
    app_state.remove_warning("k1")
    assert window.footer._health_label.text() == "All systems nominal"
    assert window.status_ribbon._alert_badge.isHidden() is True, (
        "already acknowledged, so its recovery is not new attention"
    )


def test_thermal_state_propagates_to_ribbon(window, app_state):
    """Audit 2026-07-29 4.1: ``AppState.status_updated`` must drive the ribbon
    thermal pill through the live MainWindow wiring (``_on_status_for_ribbon``).
    The setter is unit-tested in isolation; this pins the cross-component chain
    end to end. ``isHidden()`` is used so the assertions don't depend on the
    never-shown window's ancestor visibility."""
    ribbon = window.status_ribbon

    app_state.set_status(DaemonStatus(thermal_state="emergency"))
    assert ribbon._thermal_pill.isHidden() is False
    assert ribbon._thermal_pill.text() == "THERMAL: EMERGENCY"  # pill upper-cases
    assert ribbon._thermal_pill.state() == "critical"

    # A benign state repaints the same pill (not just first-show).
    app_state.set_status(DaemonStatus(thermal_state="normal"))
    assert ribbon._thermal_pill.text() == "THERMAL OK"
    assert ribbon._thermal_pill.state() == "ok"


def test_close_stops_the_poll_age_ticker(qtbot, settings_service):
    """DEC-222: the poll-age ticker writes into the footer every second. If it
    outlived closeEvent, a tick could land on an already-deleted widget during
    teardown — which is why every other timer here is stopped explicitly."""
    from control_ofc.ui.main_window import MainWindow

    # settings_service is explicit for a reason (DEC-244): omitting it makes
    # MainWindow default-construct one pointed at the real user config file, and
    # close() persists geometry — so this test used to overwrite it for real.
    window = MainWindow(settings_service=settings_service, demo_mode=True)
    qtbot.addWidget(window)
    assert window._poll_age_timer.isActive()

    window.close()
    assert not window._poll_age_timer.isActive()


def test_a_warning_raised_before_the_window_exists_still_reaches_the_badge(qtbot, app_state):
    """A signal carries changes, so a listener that connects late hears nothing.

    `main.py` raises profile-load failures before MainWindow is constructed, so those
    alerts were emitted to no one and the ribbon badge stayed hidden over a real
    warning until some unrelated change moved the count. Pre-existing before DEC-282
    and fixed there, because an alert nobody can see is the defect that work is about.
    """
    app_state.add_warning("warning", "profile", "Failed to load profile 'x'", key="pl:x")
    assert app_state.unacknowledged_count == 1

    win = MainWindow(state=app_state, demo_mode=False)
    qtbot.addWidget(win)

    assert win.status_ribbon._alert_badge.isHidden() is False
    assert win.footer._health_label.text() == "1 warning"


# ── The app's minimum window size must be one every page can survive ───────
#
# `AUD2-b`: `setMinimumSize(1200, 750)` did not raise a floor under the layout's
# own minimum, it CAPPED it (the DEC-281 family, one axis over). Three pages
# needed more than the 1010px a 1200px window leaves after the sidebar, so the
# literal licensed each of them to be squeezed below its content — measured, the
# Logs toolbar rendered its search box as "Sea…" at the app's own declared
# minimum. Both tests below read REALISED geometry from a shown window and
# assert relationships, never pixel counts (CLAUDE.md § Hard-won lessons).


def test_the_apps_minimum_window_is_one_every_page_actually_fits_in(qtbot, window):
    """The invariant the literal was violating, stated per page.

    `QStackedLayout` lays out ONLY the current page, so each page is navigated to
    before it is measured — a width read from a page the test never showed is a
    phantom (CLAUDE.md § Hard-won lessons).
    """
    window.show()
    qtbot.waitExposed(window)
    settle_at_minimum(window)

    too_narrow = []
    for idx in range(window.page_stack.count()):
        window.page_stack.setCurrentIndex(idx)
        settle_at_minimum(window)
        page = window.page_stack.widget(idx)
        needs = page.minimumSizeHint().width()
        if page.width() < needs:
            too_narrow.append(f"{type(page).__name__}: {page.width()}px < {needs}px needed")

    assert not too_narrow, (
        "at the app's own minimum window width these pages are squeezed below "
        "what their content asks for: " + "; ".join(too_narrow)
    )


def test_the_logs_search_box_shows_its_placeholder_at_the_apps_minimum(qtbot, window):
    """`AUD2-b` in the terms the user saw it: the search box rendered as "Sea…".

    The end-to-end statement of the invariant above — a real window at its own
    minimum, the real Logs page, and the placeholder measured in the live font
    rather than assumed (CLAUDE.md § Hard-won lessons: a font metric is not a
    portable constant).
    """
    window.show()
    qtbot.waitExposed(window)
    window.page_stack.setCurrentIndex(PAGE_LOGS)
    settle_at_minimum(window)

    edit = window.logs_page._search_edit
    needed = QFontMetrics(edit.font()).horizontalAdvance(edit.placeholderText())

    assert edit.width() > needed, (
        f"at the app's minimum window width the search field is {edit.width()}px "
        f"and its placeholder needs {needed}px — it renders elided"
    )


def test_the_window_minimum_is_not_capped_below_its_own_layout(qtbot, window):
    """The mechanism, so a future literal cannot quietly reintroduce the cap.

    Asserted as a relationship against the layout's own computed minimum rather
    than against a number — the widest page is a font metric, so the figure
    differs on every font stack (DEC-303).
    """
    window.show()
    qtbot.waitExposed(window)
    settle_at_minimum(window)

    assert window.minimumWidth() >= window.layout().minimumSize().width(), (
        "the window's minimum width is capped below what its own layout needs — "
        "a literal `setMinimumSize` width overrides `minimumSizeHint` rather "
        "than raising a floor under it"
    )
