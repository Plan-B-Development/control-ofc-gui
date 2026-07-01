"""Tests for the central AppState."""

from __future__ import annotations

from control_ofc.api.models import (
    ConnectionState,
    HwmonHeader,
    OperationMode,
    SensorReading,
)
from control_ofc.services.app_state import AppState


def test_set_connection_emits_signal(qtbot):
    state = AppState()
    with qtbot.waitSignal(state.connection_changed, timeout=1000) as blocker:
        state.set_connection(ConnectionState.CONNECTED)
    assert blocker.args == [ConnectionState.CONNECTED]
    assert state.connection == ConnectionState.CONNECTED


def test_set_connection_no_duplicate_signal(qtbot):
    state = AppState()
    state.set_connection(ConnectionState.CONNECTED)
    # Second call with same value should not emit
    signals = []
    state.connection_changed.connect(lambda s: signals.append(s))
    state.set_connection(ConnectionState.CONNECTED)
    assert len(signals) == 0


def test_set_mode_emits_signal(qtbot):
    state = AppState()
    with qtbot.waitSignal(state.mode_changed, timeout=1000):
        state.set_mode(OperationMode.DEMO)
    assert state.mode == OperationMode.DEMO


def test_fan_display_name_alias():
    state = AppState()
    state.fan_aliases = {"openfan:ch00": "Front Intake 1"}
    assert state.fan_display_name("openfan:ch00") == "Front Intake 1"


def test_fan_display_name_hwmon_label():
    state = AppState()
    state.set_hwmon_headers([HwmonHeader(id="hwmon:test:pwm1", label="CPU Fan")])
    assert state.fan_display_name("hwmon:test:pwm1") == "CPU Fan"


def test_fan_display_name_fallback_to_id():
    state = AppState()
    assert state.fan_display_name("openfan:ch05") == "openfan:ch05"


def test_fan_display_name_uses_label_resolver_when_sysfs_label_empty():
    """A3: when the daemon's sysfs label is empty, fan_display_name
    consults the in-repo fallback table (and /etc/sensors.d, but tests
    inject an empty path list via the resolver's cache instead). On
    the X870E AORUS MASTER, IT8696E pwm1 should resolve to CPU_FAN."""
    from control_ofc.api.models import BoardInfo
    from control_ofc.ui.hwmon_label_resolver import (
        clear_libsensors_cache,
        load_libsensors_configs,
    )

    # Force a deterministic libsensors result — no system files read.
    clear_libsensors_cache()
    load_libsensors_configs(paths=[])  # cache is set only when paths is None
    try:
        state = AppState()
        state.board_info = BoardInfo(
            vendor="Gigabyte Technology Co., Ltd.",
            name="X870E AORUS MASTER",
        )
        state.set_hwmon_headers(
            [
                HwmonHeader(
                    id="hwmon:it8696:it87.2624:pwm1:pwm1",
                    label="",  # daemon couldn't read sysfs label
                    chip_name="it8696",
                    pwm_index=1,
                ),
            ]
        )
        assert state.fan_display_name("hwmon:it8696:it87.2624:pwm1:pwm1") == "CPU_FAN"
    finally:
        clear_libsensors_cache()


def test_fan_display_name_unverified_suffix_for_secondary_chip():
    """X870E AORUS MASTER IT87952E mappings are community-reported
    (frankcrawford/it87 issue #103, DEC-144) and must carry the
    (unverified) suffix until silkscreen tracing confirms."""
    from control_ofc.api.models import BoardInfo
    from control_ofc.ui.hwmon_label_resolver import clear_libsensors_cache

    clear_libsensors_cache()
    try:
        state = AppState()
        state.board_info = BoardInfo(
            vendor="Gigabyte Technology Co., Ltd.",
            name="X870E AORUS MASTER",
        )
        state.set_hwmon_headers(
            [
                HwmonHeader(
                    id="hwmon:it87952:it87.2656:pwm1:pwm1",
                    label="",
                    chip_name="it87952",
                    pwm_index=1,
                ),
            ]
        )
        label = state.fan_display_name("hwmon:it87952:it87.2656:pwm1:pwm1")
        # DEC-144: pwm1 → SYS_FAN5_PUMP per the issue #103 owner config
        # (previously a SYS_FAN4-first extrapolation).
        assert label.startswith("SYS_FAN5_PUMP")
        assert label.endswith("(unverified)")
    finally:
        clear_libsensors_cache()


def test_fan_display_name_alias_overrides_resolver():
    """User aliases take absolute precedence — they win even over a
    high-confidence resolver match."""
    from control_ofc.api.models import BoardInfo
    from control_ofc.ui.hwmon_label_resolver import clear_libsensors_cache

    clear_libsensors_cache()
    try:
        state = AppState()
        state.fan_aliases = {"hwmon:it8696:it87.2624:pwm1:pwm1": "My CPU Cooler"}
        state.board_info = BoardInfo(
            vendor="Gigabyte Technology Co., Ltd.",
            name="X870E AORUS MASTER",
        )
        state.set_hwmon_headers(
            [
                HwmonHeader(
                    id="hwmon:it8696:it87.2624:pwm1:pwm1",
                    label="",
                    chip_name="it8696",
                    pwm_index=1,
                ),
            ]
        )
        # Alias wins — CPU_FAN is what the resolver would return otherwise.
        assert state.fan_display_name("hwmon:it8696:it87.2624:pwm1:pwm1") == "My CPU Cooler"
    finally:
        clear_libsensors_cache()


def test_fan_display_name_sysfs_label_wins_over_resolver():
    """Daemon-supplied sysfs label has priority over the resolver — if
    the kernel driver exposes a fanN_label, the resolver does not run."""
    from control_ofc.api.models import BoardInfo
    from control_ofc.ui.hwmon_label_resolver import clear_libsensors_cache

    clear_libsensors_cache()
    try:
        state = AppState()
        state.board_info = BoardInfo(
            vendor="Gigabyte Technology Co., Ltd.",
            name="X870E AORUS MASTER",
        )
        state.set_hwmon_headers(
            [
                HwmonHeader(
                    id="hwmon:it8696:it87.2624:pwm1:pwm1",
                    label="DAEMON_LABEL",
                    chip_name="it8696",
                    pwm_index=1,
                ),
            ]
        )
        assert state.fan_display_name("hwmon:it8696:it87.2624:pwm1:pwm1") == "DAEMON_LABEL"
    finally:
        clear_libsensors_cache()


def test_warning_count_updates(qtbot):
    state = AppState()
    signals = []
    state.warning_count_changed.connect(lambda c: signals.append(c))
    state.set_sensors(
        [
            SensorReading(id="fresh", age_ms=500),
            SensorReading(id="stale", age_ms=5000),
        ]
    )
    assert state.warning_count == 1
    assert signals == [1]


def test_add_warning_emits_signal_immediately(qtbot):
    """Audit P2.4 regression: ad-hoc warnings must update the count and emit
    ``warning_count_changed`` synchronously. Previously, ``add_warning`` only
    mutated ``_external_warnings`` and the UI had to wait up to 1 s for the
    next ``set_sensors``/``set_fans`` cycle to call ``_update_warnings`` and
    emit the signal — control-loop failures appeared "delayed".
    """
    state = AppState()
    signals: list[int] = []
    state.warning_count_changed.connect(lambda c: signals.append(c))

    state.add_warning("error", "control_loop", "fan write failed")

    # Active list must already contain the warning, count must be 1.
    assert state.warning_count == 1
    assert len(state.active_warnings) == 1
    assert state.active_warnings[0]["source"] == "control_loop"
    # And the signal must already have fired — no waiting for the next tick.
    assert signals == [1]


def test_remove_warning_emits_signal_immediately(qtbot):
    """Audit P2.4 regression: ``remove_warning`` must also recompute the
    count synchronously so the UI badge clears without a polling-tick delay.
    """
    state = AppState()
    state.add_warning("error", "control_loop", "fan write failed", key="fan_write")
    assert state.warning_count == 1

    signals: list[int] = []
    state.warning_count_changed.connect(lambda c: signals.append(c))

    state.remove_warning("fan_write")

    assert state.warning_count == 0
    assert state.active_warnings == []
    assert signals == [0]


def test_add_warning_no_signal_when_acknowledged(qtbot):
    """An acknowledged warning must not re-emit when added again — the
    immediate-emit fix must not regress this idempotency contract.
    """
    state = AppState()
    state.add_warning("error", "control_loop", "fan write failed", key="fan_write")
    state.clear_warnings()
    assert state.warning_count == 0

    signals: list[int] = []
    state.warning_count_changed.connect(lambda c: signals.append(c))

    # Re-adding the now-acknowledged key must be a silent no-op.
    state.add_warning("error", "control_loop", "fan write failed", key="fan_write")

    assert state.warning_count == 0
    assert signals == []


def test_set_active_profile(qtbot):
    state = AppState()
    with qtbot.waitSignal(state.active_profile_changed, timeout=1000):
        state.set_active_profile("Balanced")
    assert state.active_profile_name == "Balanced"


def test_set_status_reflects_active_profile_fast_path(qtbot):
    """DEC-194: a poll status carrying the active profile updates it that cycle
    (edge-triggered), so an external activation shows within ~1 s instead of the
    slow /profile/active refresh."""
    from control_ofc.api.models import DaemonStatus

    state = AppState()
    status = DaemonStatus(active_profile_id="silent", active_profile_name="Silent")
    with qtbot.waitSignal(state.active_profile_changed, timeout=1000) as blocker:
        state.set_status(status)
    assert blocker.args == ["Silent"]
    assert state.active_profile_name == "Silent"


def test_set_status_absent_active_profile_does_not_clobber(qtbot):
    """DEC-194: a status WITHOUT the active-profile field (older daemon, or no
    active profile) must not overwrite a name already set via the /profile/active
    fallback — active_profile_name defaults to None, so the fast-path is skipped."""
    from control_ofc.api.models import DaemonStatus

    state = AppState()
    state.set_active_profile("Balanced")  # e.g. established by the fallback fetch
    state.set_status(DaemonStatus())  # poll status omits the field → default None
    assert state.active_profile_name == "Balanced"
