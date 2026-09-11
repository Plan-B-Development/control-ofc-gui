"""Regression tests: the chart's series selection survives a restart (DEC-356).

Three defects, one cause. ``SeriesSelectionModel.update_known_keys`` pruned the
restored hidden set against the keys it had just been handed
(``_hidden_keys &= _known_keys``), and the page handed it a *partial* set: the
poll worker emits ``sensors_ready`` before ``fans_ready`` (``polling.py``), so the
first registration carried sensor keys only and the prune deleted every persisted
``fan:*`` entry. Silently — the prune emits nothing, so the loss surfaced only
when the user's next toggle rewrote the file without them.

- ``DASH-a`` every hidden *fan* series came back visible on each launch.
- ``DASH-b`` the same prune lost hide state for hardware that is merely absent
  (a stopped fan under ``hide_unused_fan_headers``, a DEC-193 quarantined
  sensor) — contradicting the rule ``services/orphan_prune.py`` already states
  and enforces: from one poll, "unplugged for good" and "asleep right now" are
  indistinguishable, so removal is a user action (DEC-246).
- ``DASH-c`` DEC-245's one-shot new-key disarm was spent on the sensors-only
  registration, so with a group mode restored the rule still fired on the same
  poll and re-hid a series the user had deliberately re-shown.

Every assertion here is a relationship against the *persisted* set rather than a
count or a literal, per ``CLAUDE.md § Hard-won lessons``: a test asserting "2
hidden keys" passes while holding the wrong two, and one restoring only a sensor
key cannot see the defect at all, because a sensor key is the one class the
partial registration never lost.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from control_ofc.api.models import ConnectionState, FanReading, SensorReading
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.series_selection import ChartMode, SeriesSelectionModel
from control_ofc.ui.pages.dashboard_page import DashboardPage


def _sensor(sid: str, kind: str = "cpu_temp") -> SensorReading:
    return SensorReading(id=sid, kind=kind, label=sid, value_c=45.0, source="hwmon", age_ms=100)


def _fan(fid: str, rpm: int | None = 900, pwm: int | None = 40) -> FanReading:
    return FanReading(id=fid, source="openfan", rpm=rpm, last_commanded_pwm=pwm, age_ms=100)


def _page(qtbot, app_state, selection, settings_service=None):
    """Build the page under test.

    **Keep the returned reference for the life of the test.** ``qtbot.addWidget``
    does not hold a strong Python reference, so dropping it lets the wrapper be
    collected and PySide6 quietly drops the bound-method connections made in
    ``DashboardPage.__init__`` — ``set_fans`` then reaches no slot and the page
    asserts nothing at all, with no error. Measured: the identical test passes
    with the reference bound and sees an empty ``known_keys()`` without it.
    """
    page = DashboardPage(
        state=app_state,
        history=HistoryStore(),
        selection=selection,
        settings_service=settings_service,
    )
    qtbot.addWidget(page)
    return page


def _poll(app_state, sensors, fans):
    """One poll cycle in the order the daemon client actually emits it.

    ``sensors_ready`` then ``fans_ready`` (``polling.py``), which is the whole
    mechanism behind DASH-a: replicating it is what regresses the bug. The demo
    tick uses the same order.
    """
    app_state.set_sensors(sensors)
    app_state.set_fans(fans)


class TestPersistedSelectionSurvivesTheFirstPoll:
    def test_a_mixed_hidden_set_is_restored_exactly(self, qtbot, app_state):
        """DASH-a. The fixture must hold BOTH classes: with sensors only this
        passes against the defect, because the sensors-only registration is
        precisely the one that keeps sensor keys."""
        persisted = {
            "sensor:cpu:tctl",
            "fan:openfan:ch00:rpm",
            "fan:hwmon:it87:it87.2624:pwm2:rpm",
        }
        selection = SeriesSelectionModel()
        selection.load_hidden(sorted(persisted))
        page = _page(qtbot, app_state, selection)
        app_state.set_connection(ConnectionState.CONNECTED)

        _poll(
            app_state,
            [_sensor("cpu:tctl"), _sensor("gpu:edge", "gpu_temp")],
            [
                _fan("openfan:ch00"),
                _fan("openfan:ch01"),
                _fan("hwmon:it87:it87.2624:pwm2"),
            ],
        )

        # Precondition: the page really saw the poll. Also what keeps `page` bound
        # for the life of the test — see `_page`. Do NOT take `ruff`'s F841 advice
        # to drop the binding: it un-wires the page and every assertion below
        # then passes or fails for the wrong reason.
        assert page._fans_polled is True
        # The whole persisted set, unchanged — not "3 keys", and not the sensor
        # half of it.
        assert set(selection.to_dict()["hidden_keys"]) == persisted
        # The opposite branch: a key that was never hidden must still be visible,
        # or a model that simply hid everything would pass the line above.
        assert selection.is_visible("sensor:gpu:edge")
        assert selection.is_visible("fan:openfan:ch01:rpm")

    def test_the_rail_renders_the_restored_fan_rows_unchecked(self, qtbot, app_state):
        """DASH-a at the surface the user actually sees. The model can hold the
        right set while the rail rebuilds its rows from a stale answer."""
        selection = SeriesSelectionModel()
        selection.load_hidden(["fan:openfan:ch00:rpm", "sensor:cpu:tctl"])
        page = _page(qtbot, app_state, selection)
        app_state.set_connection(ConnectionState.CONNECTED)

        _poll(app_state, [_sensor("cpu:tctl")], [_fan("openfan:ch00"), _fan("openfan:ch01")])

        rail = page._sensor_panel
        assert rail._fan_items["openfan:ch00"].checkState(0) == Qt.CheckState.Unchecked
        # The sibling the user did not hide — the opposite branch again.
        assert rail._fan_items["openfan:ch01"].checkState(0) == Qt.CheckState.Checked
        assert rail._sensor_items["cpu:tctl"].checkState(0) == Qt.CheckState.Unchecked

    def test_registration_waits_for_the_fan_half_of_the_poll(self, qtbot, app_state):
        """The mechanism, pinned directly: a sensors-only registration must not
        reach the model at all. Without this, a future refactor that re-registers
        eagerly reopens DASH-a and DASH-c together."""
        selection = SeriesSelectionModel()
        page = _page(qtbot, app_state, selection)
        app_state.set_connection(ConnectionState.CONNECTED)

        app_state.set_sensors([_sensor("cpu:tctl")])
        assert page._fans_polled is False
        assert selection.known_keys() == set()

        app_state.set_fans([_fan("openfan:ch00")])
        assert page._fans_polled is True
        assert selection.known_keys() == {"sensor:cpu:tctl", "fan:openfan:ch00:rpm"}

    def test_a_machine_with_no_fans_still_registers_its_sensors(self, qtbot, app_state):
        """The gate must open on an empty fan list too. On a machine with no fans
        that poll is the only one that will ever arrive, and gating on a truthy
        `fans` would leave the chart permanently empty."""
        selection = SeriesSelectionModel()
        page = _page(qtbot, app_state, selection)
        app_state.set_connection(ConnectionState.CONNECTED)

        _poll(app_state, [_sensor("cpu:tctl")], [])
        assert page._fans_polled is True
        assert selection.known_keys() == {"sensor:cpu:tctl"}


class TestHideSurvivesAbsentHardware:
    """DASH-b. ``orphan_prune.py``'s rule, now obeyed by the selection model:
    absent and asleep are indistinguishable from one poll, so only the user's
    Settings action removes a key."""

    def test_hiding_a_fan_that_then_stops_keeps_the_hide(self, qtbot, app_state, settings_service):
        # hide_unused_fan_headers defaults True, which is what drops a 0-RPM
        # non-GPU fan out of the displayable set and thus out of the known keys.
        assert settings_service.settings.hide_unused_fan_headers is True
        selection = SeriesSelectionModel()
        page = _page(qtbot, app_state, selection, settings_service=settings_service)
        app_state.set_connection(ConnectionState.CONNECTED)

        _poll(app_state, [_sensor("cpu:tctl")], [_fan("openfan:ch00"), _fan("openfan:ch01")])
        assert "openfan:ch00" in page._sensor_panel._fan_items
        selection.set_visible("fan:openfan:ch00:rpm", False)
        assert selection.is_hidden("fan:openfan:ch00:rpm")

        # The fan stops. `filter_displayable_fans` needs BOTH halves gone before
        # it drops one — a 0-RPM fan still commanded above 0 stays displayable as
        # evidence of a stall, so pwm must be 0 too for the fan to actually leave
        # the set. (Getting this wrong is how the test passes while exercising
        # nothing: the key never leaves, so nothing can prune it.)
        _poll(
            app_state,
            [_sensor("cpu:tctl")],
            [_fan("openfan:ch00", rpm=0, pwm=0), _fan("openfan:ch01")],
        )
        assert "fan:openfan:ch00:rpm" not in selection.known_keys()
        assert selection.is_hidden("fan:openfan:ch00:rpm")  # retained, not pruned

        # It spins up again — and comes back hidden, as the user left it.
        _poll(app_state, [_sensor("cpu:tctl")], [_fan("openfan:ch00"), _fan("openfan:ch01")])
        assert "fan:openfan:ch00:rpm" in selection.known_keys()
        assert not selection.is_visible("fan:openfan:ch00:rpm")

    def test_a_mode_application_does_not_drop_an_absent_key(self):
        """``set_only_visible`` recomputed the hidden set from the known set
        alone, so applying a mode re-opened the same hole the prune did. Pinned on
        the model, because the page reaches it via three different modes."""
        model = SeriesSelectionModel()
        model.load_hidden(["fan:openfan:ch00:rpm", "sensor:cpu"])
        model.update_known_keys(["sensor:cpu", "sensor:gpu"])  # the fan is away

        model.apply_mode(ChartMode.COMBINED, {"sensor:gpu"})

        assert model.is_hidden("fan:openfan:ch00:rpm")  # carried through
        assert model.is_hidden("sensor:cpu")  # out of the curated set
        assert model.is_visible("sensor:gpu")


class TestGroupModeDisarmCoversAWholeRegistration:
    """DASH-c. ``restore_mode`` disarms the new-key rule for one registration,
    because on the first poll every key looks new. That one registration has to
    be the complete one, or the rule fires on the fans and re-hides a series the
    user re-showed while in that mode."""

    def test_a_reshown_fan_survives_a_restored_thermals_mode(self, qtbot, app_state):
        selection = SeriesSelectionModel()
        # The persisted state of a Thermals user who re-showed one fan: Thermals
        # hid both, then they ticked ch00 back on.
        selection.load_hidden(["fan:openfan:ch01:rpm"])
        selection.restore_mode(ChartMode.THERMALS)
        page = _page(qtbot, app_state, selection)
        app_state.set_connection(ConnectionState.CONNECTED)

        _poll(app_state, [_sensor("cpu:tctl")], [_fan("openfan:ch00"), _fan("openfan:ch01")])

        # Precondition: the registration really happened, so the assertions below
        # are about the disarm and not about a page that never saw the poll.
        assert page._fans_polled is True
        assert "fan:openfan:ch00:rpm" in selection.known_keys()
        # Their deliberate re-show survived the launch.
        assert selection.is_visible("fan:openfan:ch00:rpm")
        # And the mode's own result is still intact — the disarm must not have
        # un-hidden anything either.
        assert selection.is_hidden("fan:openfan:ch01:rpm")

    def test_the_mode_rule_still_applies_to_genuinely_new_hardware(self, qtbot, app_state):
        """The re-arm, which is what stops the fix above becoming "the group mode
        never applies". A fan discovered on a *later* poll must follow Thermals."""
        selection = SeriesSelectionModel()
        selection.restore_mode(ChartMode.THERMALS)
        page = _page(qtbot, app_state, selection)
        app_state.set_connection(ConnectionState.CONNECTED)

        _poll(app_state, [_sensor("cpu:tctl")], [_fan("openfan:ch00")])
        assert page._fans_polled is True
        assert selection.is_visible("fan:openfan:ch00:rpm")  # was in the saved set

        # New hardware arrives.
        _poll(app_state, [_sensor("cpu:tctl")], [_fan("openfan:ch00"), _fan("openfan:ch09")])
        assert selection.is_hidden("fan:openfan:ch09:rpm")
