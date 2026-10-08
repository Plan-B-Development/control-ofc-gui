"""Freshness follows the daemon's cadence and the connection; restarts are noticed.

The 2026-10-08 interoperability audit, batch 5:

- The GUI judged every reading against a fixed 2 s, while the daemon judges its
  own against twice its poll interval (admin-configurable up to 6 s). At a slow
  cadence healthy readings showed stale for most of each cycle, and at 2 s a
  "stale" alert was raised and cleared every few seconds.
- After the daemon stopped, the Overview fan table kept "fresh" pills and the
  Controls cards kept "Now: N%" — figures nothing was updating.
- A daemon restart that failed no poll went unnoticed: capabilities, headers and
  the active profile stayed as the old daemon described them for up to 300 s.
- `/diagnostics/hardware` (trip point, coolant limit) was read once per process.
- A malformed list element in `/poll` or `/hwmon/headers` faked a sensor or
  stopped every later update.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import (
    DEFAULT_DAEMON_POLL_INTERVAL_MS,
    ActiveProfileInfo,
    BoardInfo,
    Capabilities,
    ConnectionState,
    ControlCapability,
    ControlOutput,
    DaemonConfig,
    DaemonConfigKey,
    DaemonStatus,
    FanReading,
    FeatureFlags,
    Freshness,
    HardwareDiagnosticsResult,
    SensorReading,
    freshness_for_age,
    parse_hwmon_headers,
    parse_sensors,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.polling import PollingService, _PollWorker

# ── The rule ────────────────────────────────────────────────────────────────


class TestFreshnessForAge:
    def test_the_fresh_limit_is_twice_the_daemons_interval(self):
        # The daemon's own band (`health/staleness.rs`): fresh within 2 intervals.
        for interval in (1000, 2000, 3000, 6000):
            limit = max(2000, 2 * interval)
            assert freshness_for_age(limit - 1, interval) is Freshness.FRESH
            assert freshness_for_age(limit, interval) is Freshness.STALE

    def test_a_reading_a_full_slow_interval_old_is_fresh(self):
        # At a 6 s cadence a healthy reading is up to 6 s old. The fixed 2 s
        # rule called it stale for two thirds of every cycle.
        assert freshness_for_age(5900, 6000) is Freshness.FRESH
        assert freshness_for_age(5900, DEFAULT_DAEMON_POLL_INTERVAL_MS) is Freshness.STALE

    def test_the_limits_never_drop_below_the_old_fixed_ones(self):
        assert freshness_for_age(1999, 250) is Freshness.FRESH
        assert freshness_for_age(9999, 250) is Freshness.STALE
        assert freshness_for_age(10000, 250) is Freshness.INVALID
        assert freshness_for_age(29999, 6000) is Freshness.STALE
        assert freshness_for_age(30000, 6000) is Freshness.INVALID


# ── AppState's two accessors ────────────────────────────────────────────────


def _sensor(age_ms: int, sid: str = "hwmon:k10temp:Tctl") -> SensorReading:
    return SensorReading(id=sid, kind="cpu_temp", label="Tctl", value_c=45.0, age_ms=age_ms)


def _stale_alerts(state: AppState) -> list[str]:
    return [c.key for c in state._current_conditions() if c.key.startswith("sensor_stale:")]


class TestAppStateFreshness:
    def test_the_learned_cadence_decides(self, qapp):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        reading = _sensor(age_ms=3000)
        assert state.reading_freshness(reading) is Freshness.STALE  # default 1 s cadence
        state.set_daemon_poll_interval(2000)
        assert state.reading_freshness(reading) is Freshness.FRESH
        assert state.display_freshness(reading) is Freshness.FRESH

    def test_a_healthy_slow_daemon_raises_no_stale_alert(self, qapp):
        # The flicker: at a 2 s cadence the reading's age crosses 2 s every cycle.
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        state.set_sensors([_sensor(age_ms=2600)])
        assert _stale_alerts(state)  # presence at the default cadence first
        state.set_daemon_poll_interval(2000)
        assert _stale_alerts(state) == []

    def test_nothing_is_shown_fresh_while_disconnected(self, qapp):
        state = AppState()
        state.set_connection(ConnectionState.CONNECTED)
        reading = _sensor(age_ms=100)
        assert state.display_freshness(reading) is Freshness.FRESH  # precondition
        state.set_connection(ConnectionState.DISCONNECTED)
        assert state.display_freshness(reading) is Freshness.STALE
        # The reading's own age is unchanged, so no per-sensor alert floods in on
        # a disconnect — the connection has its own.
        assert state.reading_freshness(reading) is Freshness.FRESH
        state.set_sensors([reading])
        assert _stale_alerts(state) == []
        # An already-invalid reading stays invalid.
        assert state.display_freshness(_sensor(age_ms=60_000)) is Freshness.INVALID

    def test_a_malformed_interval_is_ignored(self, qapp):
        state = AppState()
        for bad in (0, -5, True, "2000", None, 2.5):
            state.set_daemon_poll_interval(bad)  # type: ignore[arg-type]
        assert state.daemon_poll_interval_ms == DEFAULT_DAEMON_POLL_INTERVAL_MS


# ── Pages repaint what they claim on a disconnect ───────────────────────────


def test_overview_fan_freshness_turns_stale_on_disconnect(qtbot):
    from control_ofc.services.diagnostics_service import DiagnosticsService
    from control_ofc.ui.pages.overview_page import _FAN_FRESH_COL, OverviewPage

    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    page = OverviewPage(state=state, diagnostics_service=DiagnosticsService(state))
    qtbot.addWidget(page)
    state.set_fans([FanReading(id="openfan:ch00", source="openfan", rpm=900, age_ms=100)])

    pill = page._fan_table.cellWidget(0, _FAN_FRESH_COL)
    assert pill is not None
    assert "fresh" in _pill_text(pill).lower()  # precondition

    state.set_connection(ConnectionState.DISCONNECTED)
    pill = page._fan_table.cellWidget(0, _FAN_FRESH_COL)
    assert "stale" in _pill_text(pill).lower()


def _pill_text(holder) -> str:
    from control_ofc.ui.components.badges import StatusPill

    pill = holder if isinstance(holder, StatusPill) else holder.findChild(StatusPill)
    assert pill is not None
    return pill.text()


def test_controls_cards_drop_their_output_on_disconnect(qtbot, app_state, profile_service):
    from control_ofc.services.profile_service import (
        ControlMember,
        ControlMode,
        CurveConfig,
        CurveType,
        LogicalControl,
        Profile,
    )
    from control_ofc.ui.pages.controls_page import ControlsPage

    page = ControlsPage(state=app_state, profile_service=profile_service, client=MagicMock())
    qtbot.addWidget(page)
    curve = CurveConfig(id="c1", name="C", type=CurveType.FLAT, flat_output_pct=40.0)
    ctrl = LogicalControl(
        id="lc1",
        name="LC",
        mode=ControlMode.CURVE,
        curve_id="c1",
        members=[ControlMember(source="openfan", member_id="openfan:ch00")],
    )
    page._refresh_controls_grid(Profile(id="p", name="P", controls=[ctrl], curves=[curve]))
    app_state.set_connection(ConnectionState.CONNECTED)
    page._apply_live_outputs(
        DaemonStatus(control_outputs=[ControlOutput(control_id="lc1", output_pct=42.0)])
    )
    card = page._control_cards["lc1"]
    assert "42%" in card._output_label.text()  # precondition

    app_state.set_connection(ConnectionState.DISCONNECTED)
    assert card._output_label.text() == "—"


# ── The poll worker ─────────────────────────────────────────────────────────


def _client(status: DaemonStatus | None = None) -> MagicMock:
    client = MagicMock()
    client.capabilities.return_value = Capabilities(
        daemon_version="4.1.1",
        features=FeatureFlags(),
        control=ControlCapability(autonomous_control=True),
    )
    client.hwmon_headers.return_value = []
    client.active_profile.return_value = ActiveProfileInfo(active=False)
    client.hardware_diagnostics.return_value = HardwareDiagnosticsResult(board=BoardInfo())
    client.get_daemon_config.return_value = DaemonConfig(
        keys=[DaemonConfigKey(key="polling.poll_interval_ms", value=3000, running_value=3000)]
    )
    client.poll.return_value = (status or DaemonStatus(overall_status="ok"), [], [])
    return client


def _worker(client: MagicMock) -> _PollWorker:
    worker = _PollWorker(socket_path="/tmp/fake.sock")
    worker._ensure_client = MagicMock(return_value=client)
    return worker


def _spy(signal) -> list:
    seen: list = []
    signal.connect(lambda *a: seen.append(a))
    return seen


class TestPollWorker:
    def test_the_running_poll_interval_is_learned(self, qapp):
        client = _client()
        worker = _worker(client)
        seen = _spy(worker.poll_interval_ready)
        worker.poll()
        assert seen == [(3000,)]

    def test_an_unreported_interval_emits_nothing(self, qapp):
        client = _client()
        client.get_daemon_config.side_effect = RuntimeError("boom")
        worker = _worker(client)
        seen = _spy(worker.poll_interval_ready)
        connected = _spy(worker.connected)
        worker.poll()
        assert seen == []
        assert len(connected) == 1, "a failed config read must not fail the poll"

    def _restart_case(self, first: DaemonStatus, second: DaemonStatus):
        client = _client(first)
        worker = _worker(client)
        restarted = _spy(worker.daemon_restarted)
        worker.poll()  # the capabilities cycle
        worker.poll()  # an ordinary one
        client.capabilities.reset_mock()
        client.poll.return_value = (second, [], [])
        worker.poll()  # sees the restart
        worker.poll()  # re-reads what a reconnect would
        return restarted, client

    def test_uptime_going_back_is_a_restart_and_re_reads_capabilities(self, qapp):
        restarted, client = self._restart_case(
            DaemonStatus(uptime_seconds=3600, daemon_version="4.1.1"),
            DaemonStatus(uptime_seconds=2, daemon_version="4.1.1"),
        )
        assert len(restarted) == 1
        client.capabilities.assert_called_once()

    def test_a_version_change_is_a_restart(self, qapp):
        restarted, client = self._restart_case(
            DaemonStatus(uptime_seconds=10, daemon_version="4.1.1"),
            DaemonStatus(uptime_seconds=60, daemon_version="4.0.0"),
        )
        assert len(restarted) == 1
        client.capabilities.assert_called_once()

    def test_a_running_daemon_is_not_a_restart(self, qapp):
        restarted, client = self._restart_case(
            DaemonStatus(uptime_seconds=10, daemon_version="4.1.1"),
            DaemonStatus(uptime_seconds=13, daemon_version="4.1.1"),
        )
        assert restarted == []
        client.capabilities.assert_not_called()

    def test_a_header_list_element_of_the_wrong_shape_does_not_stop_polling(self, qapp):
        # Worst case past the parser guard: whatever raises inside the cycle is
        # a failed cycle (disconnected + backoff), never an escape that leaves
        # the GUI showing "connected" with nothing updating.
        client = _client()
        client.hwmon_headers.side_effect = AttributeError("'str' object has no attribute 'get'")
        worker = _worker(client)
        disconnected = _spy(worker.disconnected)
        worker.poll()
        assert len(disconnected) == 1


class TestPollingServiceWiring:
    def test_the_interval_reaches_app_state(self, qtbot, tmp_path):
        state = AppState()
        svc = PollingService(state, str(tmp_path / "nonexistent.sock"))
        try:
            svc._worker.poll_interval_ready.emit(4000)
            qtbot.waitUntil(lambda: state.daemon_poll_interval_ms == 4000, timeout=2000)
        finally:
            svc.shutdown()

    def test_a_diagnostics_refresh_request_reaches_the_worker(self, qtbot, tmp_path):
        state = AppState()
        svc = PollingService(state, str(tmp_path / "nonexistent.sock"))
        try:
            assert svc._worker._hw_diag_refresh_pending is False  # precondition
            state.request_hw_diagnostics_refresh()
            qtbot.waitUntil(lambda: svc._worker._hw_diag_refresh_pending, timeout=2000)
        finally:
            svc.shutdown()

    def test_a_restart_resets_the_session_statistics(self, qtbot, tmp_path):
        state = AppState()
        svc = PollingService(state, str(tmp_path / "nonexistent.sock"))
        try:
            state.set_sensors([_sensor(age_ms=100)])
            assert state.session_stats.get("hwmon:k10temp:Tctl") is not None  # precondition
            svc._worker.daemon_restarted.emit()
            qtbot.waitUntil(
                lambda: state.session_stats.get("hwmon:k10temp:Tctl") is None, timeout=2000
            )
        finally:
            svc.shutdown()


def test_a_coolant_limit_write_asks_for_a_diagnostics_re_read(qtbot, app_state, settings_service):
    from control_ofc.api.models import ConfigWriteResult
    from control_ofc.ui.pages.settings_page import SettingsPage

    client = MagicMock()
    client.get_daemon_config.return_value = DaemonConfig(keys=[])
    client.set_coolant_limit.return_value = ConfigWriteResult(
        updated=True, key="safety.coolant_limit_c", value=65
    )
    page = SettingsPage(state=app_state, settings_service=settings_service, client=client)
    qtbot.addWidget(page)
    with qtbot.waitSignal(app_state.hw_diagnostics_refresh_requested, timeout=1000):
        page._write_daemon_key("safety.coolant_limit_c", 65, lambda c: c.set_coolant_limit(65))


# ── Parser guards ───────────────────────────────────────────────────────────


def test_a_non_object_sensor_is_skipped_not_faked():
    sensors = parse_sensors({"sensors": ["junk", {"id": "a", "value_c": 40.0, "age_ms": 100}]})
    assert [s.id for s in sensors] == ["a"]


def test_a_non_object_header_is_skipped():
    headers = parse_hwmon_headers({"headers": ["junk", {"id": "hwmon:x:pwm1"}]})
    assert [h.id for h in headers] == ["hwmon:x:pwm1"]
