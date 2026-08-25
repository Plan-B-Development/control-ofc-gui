"""Session view state survives a restart (DEC-245).

DEC-234 decision 4 chose "session defaults only" for splitters; this reverses it,
so the clamp and the reset action are conditions of the reversal rather than
polish. The soft-lock case below is the one that justifies the whole clamp.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from control_ofc.services.app_settings_service import (
    _CHART_MODES,
    MACHINE_SPECIFIC_KEYS,
    AppSettings,
)
from control_ofc.services.layout_state import MIN_PANE_PX, clamp_restored_sizes
from control_ofc.services.series_selection import ChartMode


class TestClampRestoredSizes:
    def test_a_collapsed_pane_never_comes_back_collapsed(self):
        """The reason the clamp exists. DEC-222 removed the sensors rail's
        show/hide toggle in favour of the splitter, so restoring a fully collapsed
        pane would put back the hidden state that ADR designed out — with no
        affordance left to undo it."""
        restored = clamp_restored_sizes([0, 1000], [500, 500])
        assert restored is not None
        assert min(restored) >= MIN_PANE_PX
        assert sum(restored) == 1000

    def test_rescales_to_the_current_total(self):
        """A layout saved in a small window must still apply in a large one."""
        restored = clamp_restored_sizes([100, 300], [500, 500])
        assert sum(restored) == 1000
        assert restored[1] > restored[0]  # proportion preserved

    @pytest.mark.parametrize(
        ("saved", "current", "why"),
        [
            (None, [500, 500], "nothing saved"),
            ([], [500, 500], "empty"),
            ([100, 100, 100], [500, 500], "pane count changed between releases"),
            ([100, 300], [0, 0], "not laid out yet — no total to distribute"),
        ],
    )
    def test_refuses_rather_than_guessing(self, saved, current, why):
        assert clamp_restored_sizes(saved, current) is None, why

    def test_refuses_when_the_window_is_too_small_for_the_floor(self):
        """Honouring the layout would silently violate the minimum, so Qt gets to
        distribute instead."""
        assert clamp_restored_sizes([10, 10], [20, 20], min_px=48) is None


class TestSplitterPersistence:
    def _splitter(self, qtbot, name="Test_Splitter"):
        """A splitter inside a host widget.

        The host matters: ``adopt`` uses ``findChildren``, which returns a
        widget's *descendants* and never the widget itself — so adopting a bare
        splitter would silently register nothing. MainWindow always passes the
        window, so the real caller is unaffected.
        """
        host = QWidget()
        layout = QVBoxLayout(host)
        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.setObjectName(name)
        sp.addWidget(QLabel("a"))
        sp.addWidget(QLabel("b"))
        layout.addWidget(sp)
        host.resize(600, 100)
        sp.setSizes([300, 300])
        qtbot.addWidget(host)
        return host, sp

    def test_adopt_skips_unnamed_splitters(self, qtbot, settings_service):
        """An unnamed splitter has no stable key, so its sizes would collide with
        the next unnamed one — persisting nonsense is worse than not persisting."""
        from control_ofc.ui.splitter_persistence import SplitterPersistence

        host, sp = self._splitter(qtbot, name="")
        assert sp.objectName() == ""
        assert SplitterPersistence(settings_service).adopt(host) == 0

    def test_drag_is_written_back_debounced(self, qtbot, settings_service):
        from control_ofc.ui.splitter_persistence import SplitterPersistence

        host, sp = self._splitter(qtbot)
        p = SplitterPersistence(settings_service)
        p.adopt(host)
        sp.setSizes([200, 400])
        sp.splitterMoved.emit(200, 1)

        # Still inside the debounce window: nothing written yet. That is the
        # point of the timer — a drag emits splitterMoved per pixel.
        assert "Test_Splitter" not in settings_service.settings.splitter_sizes

        p.stop()  # flush, as closeEvent does
        # Compare against what Qt actually laid out, not the requested numbers:
        # an unshown splitter under the offscreen platform recomputes its own.
        assert settings_service.settings.splitter_sizes["Test_Splitter"] == list(sp.sizes())

    def test_restore_applies_saved_sizes(self, qtbot, settings_service):
        from control_ofc.ui.splitter_persistence import SplitterPersistence

        settings_service.update(splitter_sizes={"Test_Splitter": [150, 450]})
        host, sp = self._splitter(qtbot)
        applied: list[list[int]] = []
        sp.setSizes = applied.append  # type: ignore[method-assign]

        p = SplitterPersistence(settings_service)
        p.adopt(host)
        p.restore_all()

        # Recorded rather than read back: an unshown splitter discards setSizes,
        # so reading sizes() would test the offscreen platform, not the restore.
        assert applied, "restore_all did not apply the saved layout"
        restored = applied[-1]
        assert len(restored) == 2
        assert min(restored) >= MIN_PANE_PX
        assert restored[1] > restored[0]  # the saved 150:450 proportion survives

    def test_reset_restores_the_page_default_not_an_even_split(self, qtbot, settings_service):
        """The other half of the bargain: the clamp stops a pane returning
        unusable, this is the way out of a layout the user dislikes.

        It must restore what the page's constructor asked for. An even split is
        an arrangement the app never ships — Logs is 820:320, Controls 300:900 —
        so a control labelled "Reset" producing 50/50 would be showing something
        the user has never seen.
        """
        from control_ofc.ui.splitter_persistence import SplitterPersistence

        host, sp = self._splitter(qtbot)
        sp.setSizes([180, 420])  # the page's designed proportion
        designed = list(sp.sizes())

        p = SplitterPersistence(settings_service)
        p.adopt(host)
        p.restore_all()  # captures `designed` as the baseline
        sp.setSizes([500, 100])  # user drags it somewhere they later regret
        sp.splitterMoved.emit(500, 1)
        p.stop()
        assert settings_service.settings.splitter_sizes  # the drag was saved

        p.reset()

        assert settings_service.settings.splitter_sizes == {}
        assert sp.sizes() == designed
        assert sp.sizes()[0] != sp.sizes()[1], "an even split is not the page default"

    def test_a_page_never_opened_cannot_overwrite_its_saved_layout(self, qtbot, settings_service):
        """The DEC-245 remediation, and the nastiest bug in it.

        QStackedLayout lays out only the *current* page, so a splitter on a page
        the user never opened reports sizes derived from Qt's 640x480 default.
        The first cut flushed every adopted splitter on close, writing that
        phantom over a layout the user had genuinely dragged — losing their Logs
        arrangement without them ever opening Logs. Measured before the fix:
        Logs_Splitter [738, 440] -> [332, 300].
        """
        from control_ofc.constants import PAGE_DASHBOARD
        from control_ofc.ui.main_window import MainWindow

        seeded = {"Logs_Splitter": [738, 440], "Controls_Splitter_sections": [300, 870]}
        settings_service.update(
            splitter_sizes=dict(seeded),
            restore_last_page=False,
            default_startup_page=PAGE_DASHBOARD,
        )

        w = MainWindow(settings_service=settings_service, demo_mode=True)
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)
        w.close()  # never made Logs or Controls current

        for name, sizes in seeded.items():
            assert settings_service.settings.splitter_sizes[name] == sizes, (
                f"{name} was overwritten by a page the user never opened"
            )

    def test_a_splitter_is_only_persisted_once_it_has_real_geometry(self, qtbot, settings_service):
        """The mechanism behind the test above, in isolation."""
        from control_ofc.ui.splitter_persistence import SplitterPersistence

        host, _sp = self._splitter(qtbot)
        p = SplitterPersistence(settings_service)
        p.adopt(host)
        p.stop()  # nothing laid out or dragged yet

        assert settings_service.settings.splitter_sizes == {}

    def test_main_window_adopts_every_named_splitter(self, qtbot, settings_service):
        """findChildren rather than nine registrations — a splitter added later is
        covered without anyone remembering (the DEC-244 lesson)."""
        from PySide6.QtWidgets import QSplitter as QS

        from control_ofc.ui.main_window import MainWindow

        w = MainWindow(settings_service=settings_service, demo_mode=True)
        qtbot.addWidget(w)
        named = {s.objectName() for s in w.findChildren(QS) if s.objectName()}
        # Eight since DEC-282 retired the Logs left/right column handles along with
        # the permanent snapshot pane and warnings panel they divided.
        assert len(named) >= 8, f"expected every page splitter, got {sorted(named)}"
        assert named <= set(w._splitter_persistence._splitters)


class TestChartViewState:
    def test_range_change_is_written_back(self, qtbot, app_state, settings_service):
        """Before DEC-245 the persisted value was applied at startup and every
        later change discarded — the combo was session-local."""
        from control_ofc.services.history_store import HistoryStore
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(
            state=app_state, history=HistoryStore(), settings_service=settings_service
        )
        qtbot.addWidget(page)
        page._chart._range_combo.setCurrentIndex(1)
        page._range_write_timer.timeout.emit()  # fire the debounce

        assert settings_service.settings.chart_default_range_index == 1

    def test_mode_is_persisted_and_restored_as_a_label_only(
        self, qtbot, app_state, settings_service
    ):
        """The divergence fix. The saved hidden set is already this mode's result
        plus later tweaks, so restoring must not re-apply the preset."""
        from control_ofc.services.history_store import HistoryStore
        from control_ofc.services.series_selection import SeriesSelectionModel
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        settings_service.update(chart_mode="fans", hidden_chart_series=["sensor:tweaked"])
        model = SeriesSelectionModel()
        model.load_hidden(["sensor:tweaked"])
        page = DashboardPage(
            state=app_state,
            history=HistoryStore(),
            selection=model,
            settings_service=settings_service,
        )
        qtbot.addWidget(page)

        assert model._active_mode == ChartMode.FANS
        assert model.to_dict()["hidden_keys"] == ["sensor:tweaked"]  # untouched

    def test_restored_group_mode_does_not_re_hide_the_users_own_choices(self):
        """Found by three independent reviewers; the shipped test missed it
        because it never called update_known_keys.

        On the first poll `_known_keys` is empty, so *every* key counts as newly
        added. With a group mode restored, the new-key rule would re-hide every
        out-of-group series — including the pump trace the user deliberately
        re-showed while in Thermals — and the resulting selection_changed writes
        the loss to disk. Their tweak would die on every launch.
        """
        from control_ofc.services.series_selection import SeriesSelectionModel

        pump = "fan:openfan:ch00:rpm"
        keys = ["sensor:cpu0", "sensor:gpu0", pump, "fan:hwmon:h1:rpm"]

        model = SeriesSelectionModel()
        model.restore_mode(ChartMode.THERMALS)
        model.load_hidden(["fan:hwmon:h1:rpm"])  # pump absent => user re-showed it
        emitted: list[int] = []
        model.selection_changed.connect(lambda: emitted.append(1))

        model.update_known_keys(keys)

        assert model.is_visible(pump), "the user's re-shown series was re-hidden"
        assert not emitted, "a spurious emit would persist the loss"

    def test_the_mode_rule_still_applies_to_hardware_that_arrives_later(self):
        """The other side of it: disarming is for one registration only, so a fan
        genuinely appearing later under Thermals is still auto-hidden."""
        from control_ofc.services.series_selection import SeriesSelectionModel

        model = SeriesSelectionModel()
        model.restore_mode(ChartMode.THERMALS)
        model.update_known_keys(["sensor:cpu0"])  # first poll, rule sits out

        model.update_known_keys(["sensor:cpu0", "fan:openfan:ch09:rpm"])

        assert not model.is_visible("fan:openfan:ch09:rpm")

    def test_selecting_a_mode_saves_it(self, qtbot, app_state, settings_service):
        from control_ofc.services.history_store import HistoryStore
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(
            state=app_state, history=HistoryStore(), settings_service=settings_service
        )
        qtbot.addWidget(page)
        page._on_chart_mode_selected(ChartMode.THERMALS)

        assert settings_service.settings.chart_mode == "thermals"

    def test_chart_modes_match_the_enum(self):
        """The settings layer keeps the mode values as literals so it need not
        import a UI-facing service; this stops the pair drifting."""
        assert {m.value for m in ChartMode} == _CHART_MODES


class TestLogsFilterState:
    def _page(self, qtbot, settings_service):
        from control_ofc.services.diagnostics_service import DiagnosticsService
        from control_ofc.ui.pages.logs_page import LogsPage

        p = LogsPage(diagnostics_service=DiagnosticsService(), settings_service=settings_service)
        qtbot.addWidget(p)
        return p

    def test_level_toggle_round_trips(self, qtbot, settings_service):
        """An ERR-only filter silently reverted to all-levels on every launch."""
        page = self._page(qtbot, settings_service)
        page._toggle_info.setChecked(False)
        page._toggle_warn.setChecked(False)
        assert settings_service.settings.logs_level_filters == ["error"]

        restored = self._page(qtbot, settings_service)
        assert not restored._toggle_info.isChecked()
        assert restored._toggle_error.isChecked()

    def test_all_levels_off_is_a_real_state_not_a_missing_one(self, qtbot, settings_service):
        page = self._page(qtbot, settings_service)
        for btn in page._level_toggles().values():
            btn.setChecked(False)
        assert settings_service.settings.logs_level_filters == []

        restored = self._page(qtbot, settings_service)
        assert not any(b.isChecked() for b in restored._level_toggles().values())

    def test_search_text_round_trips(self, qtbot, settings_service):
        page = self._page(qtbot, settings_service)
        page._search_edit.setText("thermal")
        page._persist_log_filters()  # the timer's payload, fired directly

        assert self._page(qtbot, settings_service)._search_edit.text() == "thermal"

    def test_restoring_does_not_write_itself_back(self, qtbot, settings_service):
        """Restore blocks signals; without that, opening the page would re-save
        the filter and defeat any later external change."""
        settings_service.update(logs_level_filters=["error"], logs_search_text="x")
        before = settings_service.settings.logs_level_filters
        self._page(qtbot, settings_service)
        assert settings_service.settings.logs_level_filters == before


class TestWindowState:
    def test_geometry_is_written_without_waiting_for_close(self, qtbot, settings_service):
        """It used to be written only in closeEvent, so a crash, SIGKILL or logout
        lost it."""
        from control_ofc.ui.main_window import MainWindow

        w = MainWindow(settings_service=settings_service, demo_mode=True)
        qtbot.addWidget(w)
        w.page_stack.setCurrentIndex(2)
        w._geometry_timer.timeout.emit()  # fire the debounce without waiting

        assert settings_service.settings.last_page_index == 2

    def test_theme_combo_label_and_editor_all_agree(self, qtbot, settings_service):
        """The page claimed 'Default Dark' on every launch regardless of
        theme_name, because rebuilding the list left index 0 selected.

        Selecting the index *alone* was worse than the bug: the combo is not
        connected to currentIndexChanged and the editor initialises from
        default_dark_theme(), so the picker would read one theme while the label
        beneath it and the colour grid read another — and Apply Theme Globally
        would apply what the label said, not what the picker showed. All three
        must agree.
        """
        from control_ofc.ui.pages.theme_page import ThemePage

        page = ThemePage(settings_service=settings_service)
        qtbot.addWidget(page)

        shown = page._theme_combo.currentText()
        assert shown in page._theme_name_label.text(), (
            f"picker says {shown!r}, label says {page._theme_name_label.text()!r}"
        )
        assert page._theme_editor.tokens.name == shown

    def test_unknown_saved_theme_leaves_the_selection_alone(self, qtbot, settings_service):
        from control_ofc.ui.pages.theme_page import ThemePage

        settings_service.update(theme_name="Nonexistent Theme")
        page = ThemePage(settings_service=settings_service)
        qtbot.addWidget(page)
        assert page._theme_combo.currentText() == "Default Dark"


def test_view_state_keys_are_machine_specific():
    """Pane sizes and log filters describe one person's window on one screen; a
    shared export carrying them is the DEC-140 problem."""
    for key in ("splitter_sizes", "logs_level_filters", "logs_search_text"):
        assert key in MACHINE_SPECIFIC_KEYS
    assert not (set(AppSettings().portable_dict()) & {"splitter_sizes", "logs_search_text"})


def test_malformed_splitter_sizes_fall_back_to_defaults():
    """The settings file is a trust boundary (DEC-137)."""
    s = AppSettings.from_dict(
        {
            "splitter_sizes": {
                "ok": [100, 200],
                "bad_type": "nope",
                "negative": [-5, 10],
                "empty": [],
                123: [1, 2],
            }
        }
    )
    assert s.splitter_sizes == {"ok": [100, 200]}


class TestTriggerWiring:
    """The trigger end, not the payload end (pre-release review, DEC-245).

    Every debounced write in this release was originally asserted only by firing
    the timer by hand or calling the persist method directly — which proves the
    *save* works and says nothing about whether the thing that is supposed to
    start it actually does. Deleting each connection left all 3196 tests green.
    These drive the real user-facing trigger and assert the timer armed.
    """

    def _window(self, qtbot, settings_service):
        from control_ofc.ui.main_window import MainWindow

        w = MainWindow(settings_service=settings_service, demo_mode=True)
        qtbot.addWidget(w)
        return w

    def test_changing_page_arms_the_geometry_timer(self, qtbot, settings_service):
        w = self._window(qtbot, settings_service)
        w._geometry_timer.stop()
        w.page_stack.setCurrentIndex(2)
        assert w._geometry_timer.isActive()

    def test_resizing_arms_the_geometry_timer(self, qtbot, settings_service):
        """Dispatches a real QResizeEvent rather than calling .resize(): Qt posts
        rather than sends these to an unshown widget, and Qt's own delivery is not
        what can regress here — the override being deleted is."""
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QResizeEvent
        from PySide6.QtWidgets import QApplication

        w = self._window(qtbot, settings_service)
        w._geometry_timer.stop()
        QApplication.sendEvent(w, QResizeEvent(QSize(1301, 851), QSize(1400, 850)))
        assert w._geometry_timer.isActive()

    def test_moving_arms_the_geometry_timer(self, qtbot, settings_service):
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QMoveEvent
        from PySide6.QtWidgets import QApplication

        w = self._window(qtbot, settings_service)
        w._geometry_timer.stop()
        QApplication.sendEvent(w, QMoveEvent(QPoint(37, 41), QPoint(0, 0)))
        assert w._geometry_timer.isActive()

    def test_reset_layout_button_reaches_the_splitter_persistence(self, qtbot, settings_service):
        """The escape hatch that made persisting splitters acceptable at all, and
        it had zero coverage at any layer — no click test, no signal test, and no
        test that MainWindow even connects it."""
        settings_service.update(splitter_sizes={"Logs_Splitter": [700, 400]})
        w = self._window(qtbot, settings_service)
        w.settings_page._reset_layout_btn.click()
        assert settings_service.settings.splitter_sizes == {}

    def test_typing_a_search_phrase_arms_the_logs_filter_timer(self, qtbot, settings_service):
        from control_ofc.services.diagnostics_service import DiagnosticsService
        from control_ofc.ui.pages.logs_page import LogsPage

        page = LogsPage(diagnostics_service=DiagnosticsService(), settings_service=settings_service)
        qtbot.addWidget(page)
        page._filter_write_timer.stop()
        page._search_edit.setText("thermal")
        assert page._filter_write_timer.isActive()

    def test_closing_the_logs_page_flushes_a_pending_search(self, qtbot, settings_service):
        """Unlike the splitter timer there was no close-time safety net, so a
        phrase typed and closed within 500 ms was lost outright."""
        from control_ofc.services.diagnostics_service import DiagnosticsService
        from control_ofc.ui.pages.logs_page import LogsPage

        page = LogsPage(diagnostics_service=DiagnosticsService(), settings_service=settings_service)
        qtbot.addWidget(page)
        page._search_edit.setText("thermal")
        page.cleanup()
        assert settings_service.settings.logs_search_text == "thermal"

    def test_a_wheel_scroll_over_the_range_combo_writes_once(
        self, qtbot, app_state, settings_service
    ):
        """A QComboBox changes index on every wheel notch, so an undebounced write
        turned one scroll gesture into five to ten whole-file fsync'd writes on
        the GUI thread."""
        from unittest.mock import patch

        from control_ofc.services.history_store import HistoryStore
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(
            state=app_state, history=HistoryStore(), settings_service=settings_service
        )
        qtbot.addWidget(page)

        with patch.object(type(settings_service), "update", autospec=True) as spy:
            for i in range(1, 6):
                page._chart._range_combo.setCurrentIndex(i)
            assert spy.call_count == 0, "writes should be deferred to the debounce"
            page._range_write_timer.timeout.emit()
            assert spy.call_count == 1

    def test_closing_the_dashboard_flushes_a_pending_chart_range(
        self, qtbot, app_state, settings_service
    ):
        """The fifth instance of this class, and it shipped without a test.

        `cleanup()` used to merely *stop* the 400 ms range debounce, so changing
        the chart range and closing the window inside that window discarded the
        change — the same shape as the logs-page search above. The fix flushes
        instead of stopping; this pins it, because deleting the flush leaves the
        stop behind and every other dashboard test still passes.
        """
        from control_ofc.services.history_store import HistoryStore
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(
            state=app_state, history=HistoryStore(), settings_service=settings_service
        )
        qtbot.addWidget(page)

        combo = page._chart._range_combo
        target = 1 if combo.currentIndex() != 1 else 2
        combo.setCurrentIndex(target)
        assert page._range_write_timer.isActive(), "the write must be debounced, not immediate"

        page.cleanup()

        assert settings_service.settings.chart_default_range_index == target, (
            "closing within the debounce window dropped the chart range the user "
            "picked — stopping a timer whose payload has not run is data loss"
        )

    def test_reset_layout_cancels_a_pending_drag_write(self, qtbot, settings_service):
        """Reset raced its own debounce: a drag in the last 400 ms left a pending
        flush that fired straight after and wrote all nine entries back, so
        "forget every saved layout" ended with a full map."""
        from control_ofc.ui.splitter_persistence import SplitterPersistence

        host, sp = TestSplitterPersistence()._splitter(qtbot)
        p = SplitterPersistence(settings_service)
        p.adopt(host)
        p.restore_all()
        sp.setSizes([420, 180])
        sp.splitterMoved.emit(420, 1)  # arms the 400 ms debounce

        assert p._timer.isActive()
        p.reset()

        # Assert the timer is *disarmed*, not that a hand-emitted timeout is
        # harmless — emitting the signal directly would bypass stop() and test
        # nothing. Disarming is the fix.
        assert not p._timer.isActive(), "a pending flush would rewrite the map"
        assert settings_service.settings.splitter_sizes == {}

    def test_chart_reset_persists_the_mode(self, qtbot, app_state, settings_service):
        """Reset is a second way to change the mode. Omitting the write here
        reintroduced the divergence DEC-245 exists to close: reset while in Fans
        and the next launch showed 'Fans' over Combined data."""
        from control_ofc.services.history_store import HistoryStore
        from control_ofc.ui.pages.dashboard_page import DashboardPage

        page = DashboardPage(
            state=app_state, history=HistoryStore(), settings_service=settings_service
        )
        qtbot.addWidget(page)
        page._on_chart_mode_selected(ChartMode.FANS)
        assert settings_service.settings.chart_mode == "fans"

        page._chart._reset_btn.click()
        assert settings_service.settings.chart_mode == "combined"


def test_every_named_splitter_is_pinned_by_name(qtbot, settings_service):
    """`>= 9` caught a splitter losing its objectName but not a rename, and four
    of the nine (Logs x3, Overview) have no rename protection anywhere else."""
    from PySide6.QtWidgets import QSplitter as QS

    from control_ofc.ui.main_window import MainWindow

    expected = {
        "Controls_Splitter_curvesEditor",
        "Controls_Splitter_sections",
        "Dashboard_Splitter_horizontal",
        "Dashboard_Splitter_vertical",
        "Logs_Splitter",
        "Overview_Splitter_sections",
        "SystemState_Splitter_row2",
        "SystemState_Splitter_sections",
    }
    w = MainWindow(settings_service=settings_service, demo_mode=True)
    qtbot.addWidget(w)
    assert {s.objectName() for s in w.findChildren(QS) if s.objectName()} == expected


class TestNewFieldCoercion:
    """The settings file is a trust boundary (DEC-137); the four new fields had no
    entry in the from_dict fuzzing every other field gets."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("not_a_real_mode", "combined"), (123, "combined"), ("fans", "fans")],
    )
    def test_chart_mode_is_enum_gated(self, value, expected):
        assert AppSettings.from_dict({"chart_mode": value}).chart_mode == expected

    def test_log_levels_are_whitelisted(self):
        s = AppSettings.from_dict({"logs_level_filters": ["info", "bogus", 123, "warn"]})
        assert s.logs_level_filters == ["info", "warn"]

    def test_a_non_list_log_filter_falls_back_to_all_levels(self):
        assert AppSettings.from_dict({"logs_level_filters": "info"}).logs_level_filters == [
            "info",
            "warn",
            "error",
        ]

    def test_search_text_is_length_capped(self):
        assert len(AppSettings.from_dict({"logs_search_text": "x" * 5000}).logs_search_text) == 200

    def test_splitter_sizes_accepts_zero_unlike_card_sizes(self):
        """Load-bearing one-character asymmetry with the adjacent `_as_card_sizes`,
        which rejects 0. A splitter genuinely can be saved fully collapsed; it is
        the runtime clamp, not the coercer, that promotes it back to MIN_PANE_PX.
        A copy-paste 'fix' aligning the two would silently break the restore."""
        assert AppSettings.from_dict({"splitter_sizes": {"x": [0, 200]}}).splitter_sizes == {
            "x": [0, 200]
        }

    @pytest.mark.parametrize(
        "value",
        [
            {"x": [True, 200]},  # bool is not an int here
            {"x": [100, 99999]},  # over _GEOM_MAX
            "garbage",  # not a dict at all
            {"x": []},  # empty pane list
        ],
    )
    def test_splitter_sizes_rejects_malformed(self, value):
        assert AppSettings.from_dict({"splitter_sizes": value}).splitter_sizes == {}

    def test_splitter_sizes_entry_count_is_capped(self):
        from control_ofc.services.app_settings_service import _MAX_SPLITTERS

        crafted = {f"s{i}": [10, 10] for i in range(_MAX_SPLITTERS + 50)}
        got = AppSettings.from_dict({"splitter_sizes": crafted}).splitter_sizes
        assert len(got) == _MAX_SPLITTERS

    def test_all_three_view_state_keys_are_stripped_from_export(self):
        s = AppSettings.from_dict(
            {"splitter_sizes": {"x": [1, 2]}, "logs_search_text": "q", "logs_level_filters": []}
        )
        portable = s.portable_dict()
        for key in ("splitter_sizes", "logs_search_text", "logs_level_filters"):
            assert key not in portable
