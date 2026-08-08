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

    def test_reset_clears_saved_layout_and_evens_the_panes(self, qtbot, settings_service):
        """The other half of the bargain: the clamp stops a pane returning
        unusable, this is the way out of a layout the user dislikes."""
        from control_ofc.ui.splitter_persistence import SplitterPersistence

        settings_service.update(splitter_sizes={"Test_Splitter": [100, 500]})
        host, sp = self._splitter(qtbot)
        p = SplitterPersistence(settings_service)
        p.adopt(host)
        p.reset()

        assert settings_service.settings.splitter_sizes == {}
        assert sp.sizes()[0] == sp.sizes()[1]

    def test_main_window_adopts_every_named_splitter(self, qtbot, settings_service):
        """findChildren rather than nine registrations — a splitter added later is
        covered without anyone remembering (the DEC-244 lesson)."""
        from PySide6.QtWidgets import QSplitter as QS

        from control_ofc.ui.main_window import MainWindow

        w = MainWindow(settings_service=settings_service, demo_mode=True)
        qtbot.addWidget(w)
        named = {s.objectName() for s in w.findChildren(QS) if s.objectName()}
        assert len(named) >= 9, f"expected all nine page splitters, got {sorted(named)}"
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

    def test_theme_combo_reflects_the_saved_theme(self, qtbot, settings_service):
        """The page claimed 'Default Dark' on every launch regardless of
        theme_name, because rebuilding the list left index 0 selected."""
        from control_ofc.ui.pages.theme_page import ThemePage

        page = ThemePage(settings_service=settings_service)
        qtbot.addWidget(page)
        assert page._theme_combo.currentText() == "Default Dark"

        settings_service.update(theme_name="Nonexistent Theme")
        page._refresh_theme_list()
        # An unknown name leaves the selection alone rather than blanking it.
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
