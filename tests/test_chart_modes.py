"""Phase 5 (DEC-181): chart readability.

Covers the curated first-run subset + ``chart_series_seeded`` flag, chart modes,
and poll-diff event annotations. All deterministic — injected state only, no real
timing/hardware. (The synthetic aggregate-RPM series and model-bound legend were
removed in v2.3.0 / DEC-186.)
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from control_ofc.api.models import (
    ConnectionState,
    DaemonStatus,
    FanReading,
    OverrideStatusEntry,
    SensorReading,
)
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import (
    ChartMode,
    SeriesSelectionModel,
)
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.widgets.timeline_chart import _MAX_ANNOTATIONS, TimelineChart


@pytest.fixture(autouse=True)
def _drain_qt_deferred_deletes():
    """Flush the Qt DeferredDelete queue after each test in this file.

    Distinct from DEC-180 (which fixed the real *teardown-safety* bug — severing
    outlive-the-widget signals + closing the scene synchronously): the source
    teardown is safe here (a 120-chart create/cleanup stress loop survives). What
    this guards is pure *accumulation*. These tests add many charts, each now
    carrying extra child widgets (mode combo, reset button) and annotation items;
    pytest-qt does not flush their deferred deletion between
    tests, so they pile up until a queued delete fires during a much later,
    unrelated test and segfaults under offscreen Qt/py3.14. Flushing here keeps
    this file's churn from tipping that heap-timing race. Autouse → set up before
    qtbot → finalised after it, so it runs once the test's widgets are closed."""
    yield
    app = QApplication.instance()
    if app is not None:
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def _sensor(
    sid: str, kind: str = "CpuTemp", value: float = 45.0, age_ms: int = 100
) -> SensorReading:
    return SensorReading(id=sid, kind=kind, label=sid, value_c=value, source="hwmon", age_ms=age_ms)


def _fan(fid: str, rpm: int | None = 900, stall: bool | None = None) -> FanReading:
    return FanReading(
        id=fid, source="openfan", rpm=rpm, last_commanded_pwm=40, age_ms=100, stall_detected=stall
    )


def _page(qtbot, app_state, settings_service, profile_service, selection=None):
    sel = selection or SeriesSelectionModel()
    page = DashboardPage(
        state=app_state,
        history=HistoryStore(),
        selection=sel,
        profile_service=profile_service,
        settings_service=settings_service,
    )
    qtbot.addWidget(page)
    return page, sel


# ── PWM keys are never chartable ─────────────────────────────────────


class TestPwmExclusion:
    def test_pwm_keys_excluded_from_known(self):
        m = SeriesSelectionModel()
        m.update_known_keys(["fan:x:rpm", "fan:x:pwm", "sensor:cpu"])
        assert "fan:x:rpm" in m.known_keys()
        assert "fan:x:pwm" not in m.known_keys()


# ── Modes on the pure model ──────────────────────────────────────────


class TestModes:
    def _model(self):
        m = SeriesSelectionModel()
        m.update_known_keys(
            [
                "sensor:cpu",
                "sensor:gpu",
                "sensor:disk",
                "fan:openfan:ch0:rpm",
            ]
        )
        return m

    def test_combined_curated_subset(self):
        m = self._model()
        m.apply_mode(ChartMode.COMBINED, {"sensor:cpu"})
        assert m.is_visible("sensor:cpu")
        assert not m.is_visible("sensor:gpu")
        assert not m.is_visible("fan:openfan:ch0:rpm")

    def test_combined_without_curated_keys_is_noop(self):
        m = self._model()
        m.apply_mode(ChartMode.COMBINED, None)
        assert all(m.is_visible(k) for k in m.known_keys())
        assert m.active_mode == ChartMode.COMBINED

    def test_thermals_only_temps(self):
        m = self._model()
        m.apply_mode(ChartMode.THERMALS)
        assert m.is_visible("sensor:cpu") and m.is_visible("sensor:disk")
        assert not m.is_visible("fan:openfan:ch0:rpm")

    def test_fans_only_fan_series(self):
        m = self._model()
        m.apply_mode(ChartMode.FANS)
        assert m.is_visible("fan:openfan:ch0:rpm")
        assert not m.is_visible("sensor:cpu")

    def test_diagnostics_shows_all(self):
        m = self._model()
        m.apply_mode(ChartMode.THERMALS)
        m.apply_mode(ChartMode.DIAGNOSTICS)
        assert all(m.is_visible(k) for k in m.known_keys())

    def test_set_only_visible_intersects_known(self):
        m = self._model()
        m.set_only_visible({"sensor:cpu", "sensor:nonexistent"})
        assert m.visible_keys() == {"sensor:cpu"}

    def test_new_fan_hidden_under_thermals(self):
        m = self._model()
        m.apply_mode(ChartMode.THERMALS)
        m.update_known_keys(
            [
                "sensor:cpu",
                "sensor:gpu",
                "sensor:disk",
                "fan:openfan:ch0:rpm",
                "fan:openfan:ch9:rpm",  # newly discovered fan
            ]
        )
        assert not m.is_visible("fan:openfan:ch9:rpm")

    def test_new_temp_visible_under_thermals(self):
        m = self._model()
        m.apply_mode(ChartMode.THERMALS)
        m.update_known_keys(
            [
                "sensor:cpu",
                "sensor:gpu",
                "sensor:disk",
                "sensor:vrm",
                "fan:openfan:ch0:rpm",
            ]
        )
        assert m.is_visible("sensor:vrm")  # a temp belongs to THERMALS → appears

    def test_new_key_visible_under_combined(self):
        # COMBINED is curated, not group-based, so the auto-appear contract holds.
        m = self._model()
        m.apply_mode(ChartMode.COMBINED, {"sensor:cpu"})
        m.update_known_keys(
            [
                "sensor:cpu",
                "sensor:gpu",
                "sensor:disk",
                "sensor:new",
                "fan:openfan:ch0:rpm",
            ]
        )
        assert m.is_visible("sensor:new")


# ── Chart widget: modes + annotations ────────────────────────────────


class TestChartWidget:
    @pytest.fixture()
    def chart_sel(self, qtbot):
        """A chart with a little history, torn down deterministically.

        Bare-chart tests call ``cleanup()`` on teardown (the
        ``test_timeline_chart_cleanup`` convention) so the pyqtgraph scene is freed
        synchronously. Leaving scene items to qtbot/GC deferred deletion lets them
        accumulate across the suite and tips the DEC-180 teardown race."""
        h = HistoryStore()
        sel = SeriesSelectionModel()
        for _ in range(3):
            h.record_sensors([_sensor("cpu")])
            h.record_fans([_fan("openfan:ch0")])
        sel.update_known_keys(h.series_keys())
        chart = TimelineChart(h, selection=sel)
        qtbot.addWidget(chart)
        yield chart, sel
        chart.cleanup()

    def test_mode_combo_emits(self, chart_sel):
        chart, _ = chart_sel
        got = []
        chart.mode_selected.connect(got.append)
        chart._mode_combo.setCurrentIndex(1)  # Thermals
        assert got == [ChartMode.THERMALS]

    def test_reset_button_emits(self, chart_sel):
        chart, _ = chart_sel
        fired = []
        chart.reset_requested.connect(lambda: fired.append(1))
        chart._reset_btn.click()
        assert fired == [1]

    def test_set_mode_does_not_emit(self, chart_sel):
        chart, _ = chart_sel
        got = []
        chart.mode_selected.connect(got.append)
        chart.set_mode(ChartMode.DIAGNOSTICS)
        assert got == []
        assert chart._mode_combo.currentData() == ChartMode.DIAGNOSTICS

    def test_humanize_key(self, chart_sel):
        chart, _ = chart_sel
        assert chart._humanize_key("sensor:cpu:tctl") == "tctl"
        assert chart._humanize_key("fan:openfan:ch0:rpm") == "ch0"

    def test_annotation_render_prune_and_cap(self, chart_sel):
        chart, _ = chart_sel
        chart.add_annotation(time.monotonic(), "Profile: quiet")
        chart.update_chart()
        assert len(chart._annotation_items) == 1
        # An annotation older than the window is pruned on the next render.
        chart.add_annotation(time.monotonic() - 10_000, "ancient")
        chart.update_chart()
        assert all("ancient" not in lbl for _, _, lbl in chart._annotations)
        # Hard cap on retained annotations.
        for i in range(_MAX_ANNOTATIONS + 20):
            chart.add_annotation(time.monotonic(), f"e{i}")
        assert len(chart._annotations) <= _MAX_ANNOTATIONS

    def test_cleanup_clears_annotations_idempotent(self, chart_sel):
        chart, _ = chart_sel
        chart.add_annotation(time.monotonic(), "x")
        chart.update_chart()
        chart.cleanup()
        chart.cleanup()  # idempotent
        assert chart._annotation_items == {}
        assert chart._annotations == []


# ── First-run seeding (A-fork) ───────────────────────────────────────


class TestFirstRunSeeding:
    def test_fresh_config_seeds_curated_subset(
        self, qtbot, app_state, settings_service, profile_service
    ):
        assert settings_service.settings.chart_series_seeded is False
        _, sel = _page(qtbot, app_state, settings_service, profile_service)
        app_state.set_sensors(
            [_sensor("cpu"), _sensor("gpu", "GpuTemp"), _sensor("disk", "DiskTemp")]
        )
        app_state.set_fans([_fan("f1"), _fan("f2")])

        # Curated: CPU + GPU temps visible; everything else hidden.
        assert sel.is_visible("sensor:cpu")
        assert sel.is_visible("sensor:gpu")
        assert not sel.is_visible("sensor:disk")
        assert not sel.is_visible("fan:f1:rpm")
        assert not sel.is_visible("fan:f2:rpm")
        # Flag latched + persisted.
        assert settings_service.settings.chart_series_seeded is True

    def test_no_seed_until_both_sensors_and_fans(
        self, qtbot, app_state, settings_service, profile_service
    ):
        _, sel = _page(qtbot, app_state, settings_service, profile_service)
        app_state.set_sensors([_sensor("cpu"), _sensor("disk", "DiskTemp")])
        # Only sensors so far — must NOT seed (disk still visible, flag unset).
        assert settings_service.settings.chart_series_seeded is False
        assert sel.is_visible("sensor:disk")
        app_state.set_fans([_fan("f1")])
        assert settings_service.settings.chart_series_seeded is True
        assert not sel.is_visible("sensor:disk")

    def test_returning_user_not_reseeded(self, qtbot, app_state, settings_service, profile_service):
        # A user who already seeded and chose "show all" (empty hidden set) must
        # not be re-decluttered.
        settings_service.settings.chart_series_seeded = True
        _, sel = _page(qtbot, app_state, settings_service, profile_service)
        app_state.set_sensors([_sensor("cpu"), _sensor("disk", "DiskTemp")])
        app_state.set_fans([_fan("f1")])
        assert sel.is_visible("sensor:disk")
        assert sel.is_visible("fan:f1:rpm")


# ── Mode/reset wiring through the page ───────────────────────────────


class TestModeWiring:
    def test_chart_mode_signal_applies_to_model(
        self, qtbot, app_state, settings_service, profile_service
    ):
        page, sel = _page(qtbot, app_state, settings_service, profile_service)
        app_state.set_sensors([_sensor("cpu"), _sensor("gpu", "GpuTemp")])
        app_state.set_fans([_fan("f1")])
        page._chart.mode_selected.emit(ChartMode.THERMALS)
        assert sel.is_visible("sensor:cpu")
        assert not sel.is_visible("fan:f1:rpm")

    def test_reset_restores_combined(self, qtbot, app_state, settings_service, profile_service):
        page, sel = _page(qtbot, app_state, settings_service, profile_service)
        app_state.set_sensors([_sensor("cpu"), _sensor("disk", "DiskTemp")])
        app_state.set_fans([_fan("f1")])
        sel.apply_mode(ChartMode.DIAGNOSTICS)  # show everything
        assert sel.is_visible("sensor:disk")
        page._chart.reset_requested.emit()
        assert not sel.is_visible("sensor:disk")  # back to curated
        assert sel.is_visible("sensor:cpu")
        assert page._chart._mode_combo.currentData() == ChartMode.COMBINED


# ── Poll-diff annotation feed ────────────────────────────────────────


class TestAnnotationFeed:
    def _labels(self, page):
        return [lbl for _, _, lbl in page._chart._annotations]

    def test_profile_change_annotates_when_wired(
        self, qtbot, app_state, settings_service, profile_service
    ):
        page, _ = _page(qtbot, app_state, settings_service, profile_service)
        app_state.set_active_profile("quiet")  # real signal path
        assert "Profile: quiet" in self._labels(page)

    def test_thermal_transition_annotates(
        self, qtbot, app_state, settings_service, profile_service
    ):
        page, _ = _page(qtbot, app_state, settings_service, profile_service)
        page._on_status_updated(DaemonStatus(thermal_state="emergency"))
        assert "Thermal: emergency" in self._labels(page)

    def test_reconnect_annotates(self, qtbot, app_state, settings_service, profile_service):
        page, _ = _page(qtbot, app_state, settings_service, profile_service)
        page._prev_connection = ConnectionState.DISCONNECTED
        page._on_connection_changed(ConnectionState.CONNECTED)
        # _labels is a list, so this is exact membership (not a substring match):
        # exactly one "Connected" annotation for the disconnected→connected edge.
        assert self._labels(page).count("Connected") == 1
        # A redundant CONNECTED→CONNECTED is not a transition and must NOT add a
        # second annotation (guards the `_prev_connection != CONNECTED` check).
        page._on_connection_changed(ConnectionState.CONNECTED)
        assert self._labels(page).count("Connected") == 1

    def test_override_appear_and_end_annotate(
        self, qtbot, app_state, settings_service, profile_service
    ):
        page, _ = _page(qtbot, app_state, settings_service, profile_service)
        page._on_status_updated(DaemonStatus(overrides=[OverrideStatusEntry(control_id="cpu_fan")]))
        assert "Override: cpu_fan" in self._labels(page)
        page._on_status_updated(DaemonStatus())  # override cleared
        assert "Override end: cpu_fan" in self._labels(page)

    def test_stale_sensor_annotates_onset_once(
        self, qtbot, app_state, settings_service, profile_service
    ):
        page, _ = _page(qtbot, app_state, settings_service, profile_service)
        app_state.set_sensors([_sensor("cpu", age_ms=100)])  # fresh — no annotation
        app_state.set_sensors([_sensor("cpu", age_ms=99_999)])  # stale onset
        labels = self._labels(page)
        assert labels.count("Stale: cpu") == 1
        app_state.set_sensors([_sensor("cpu", age_ms=99_999)])  # still stale — no repeat
        assert self._labels(page).count("Stale: cpu") == 1

    def test_fan_stall_annotates(self, qtbot, app_state, settings_service, profile_service):
        page, _ = _page(qtbot, app_state, settings_service, profile_service)
        app_state.set_fans([_fan("f1", rpm=0, stall=True)])
        assert any(lbl.startswith("Stall:") for lbl in self._labels(page))
