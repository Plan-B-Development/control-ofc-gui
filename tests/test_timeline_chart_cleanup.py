"""Regression tests for the timeline-chart teardown race (DEC-113).

The right-axis RPM ViewBox is X-linked to the main plot and synced on resize.
If those links survive into widget destruction, a final resize propagates
through the X-link to an already-freed ViewBox and shiboken raises
"Internal C++ object (ViewBox) already deleted". These tests lock the
invariants that prevent it:
  * ``cleanup()`` breaks the secondary-ViewBox links and is idempotent, and
  * ``DashboardPage.closeEvent`` runs cleanup, so the links are broken even
    when the page is closed (e.g. test teardown) rather than explicitly
    cleaned up.
"""

from __future__ import annotations

from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import SeriesSelectionModel
from control_ofc.ui.widgets.timeline_chart import TimelineChart


class TestTimelineChartCleanup:
    def test_cleanup_breaks_secondary_viewbox(self, qtbot):
        chart = TimelineChart(HistoryStore(), selection=SeriesSelectionModel())
        qtbot.addWidget(chart)
        assert chart._rpm_vb is not None
        chart.cleanup()
        assert chart._rpm_vb is None

    def test_cleanup_is_idempotent(self, qtbot):
        chart = TimelineChart(HistoryStore(), selection=SeriesSelectionModel())
        qtbot.addWidget(chart)
        chart.cleanup()
        # A second call must not raise (closeEvent + explicit shutdown can
        # both fire).
        chart.cleanup()
        assert chart._rpm_vb is None

    def test_resize_sync_is_noop_after_cleanup(self, qtbot):
        chart = TimelineChart(HistoryStore(), selection=SeriesSelectionModel())
        qtbot.addWidget(chart)
        chart.cleanup()
        # The slot must be a safe no-op once the ViewBox is gone — this is the
        # call path that raised the use-after-free during teardown.
        chart._sync_rpm_viewbox()  # must not raise


class TestDashboardCloseEventCleanup:
    def test_close_triggers_chart_cleanup(self, qtbot, app_state):
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(state=app_state)
        qtbot.addWidget(page)
        assert page._chart._rpm_vb is not None
        page.close()
        # closeEvent -> cleanup -> chart.cleanup must have broken the links.
        assert page._chart._rpm_vb is None
