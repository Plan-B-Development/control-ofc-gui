"""Tests for the series selection model."""

from __future__ import annotations

from control_ofc.api.models import SensorReading
from control_ofc.services.series_selection import (
    ChartMode,
    SeriesGroup,
    SeriesSelectionModel,
    default_series_keys,
)


def _sensor(id: str, kind: str) -> SensorReading:
    return SensorReading(id=id, kind=kind, value_c=50.0, age_ms=100)


def test_new_keys_default_visible():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "fan:openfan:ch00:rpm"])
    assert model.is_visible("sensor:cpu")
    assert model.is_visible("fan:openfan:ch00:rpm")


def test_hide_key():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu"])
    model.set_visible("sensor:cpu", False)
    assert not model.is_visible("sensor:cpu")


def test_is_hidden_unknown_key_is_not_hidden():
    """Unknown keys default to visible — is_hidden must not treat them as hidden."""
    model = SeriesSelectionModel()
    assert not model.is_hidden("sensor:never-seen")


def test_is_hidden_tracks_explicit_hides_and_persistence():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu"])
    model.set_visible("sensor:cpu", False)
    assert model.is_hidden("sensor:cpu")

    restored = SeriesSelectionModel()
    restored.load_hidden(model.to_dict()["hidden_keys"])
    assert restored.is_hidden("sensor:cpu")
    assert not restored.is_hidden("sensor:gpu")


def test_visible_keys():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu", "fan:openfan:ch00:rpm"])
    model.set_visible("sensor:gpu", False)
    assert model.visible_keys() == {"sensor:cpu", "fan:openfan:ch00:rpm"}


def test_select_all():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu"])
    model.set_visible("sensor:cpu", False)
    model.select_all()
    assert model.is_visible("sensor:cpu")
    assert model.is_visible("sensor:gpu")


def test_classify_sensor():
    assert SeriesSelectionModel.classify("sensor:hwmon:k10temp:Tctl") == SeriesGroup.TEMPS


def test_classify_openfan():
    assert SeriesSelectionModel.classify("fan:openfan:ch00:rpm") == SeriesGroup.OPENFAN_FANS


def test_classify_hwmon():
    assert SeriesSelectionModel.classify("fan:hwmon:nct6775:fan1:rpm") == SeriesGroup.MOBO_FANS


def test_pwm_keys_excluded():
    model = SeriesSelectionModel()
    model.update_known_keys(["fan:openfan:ch00:rpm", "fan:openfan:ch00:pwm"])
    assert "fan:openfan:ch00:rpm" in model.visible_keys()
    assert "fan:openfan:ch00:pwm" not in model.visible_keys()


def test_to_dict_and_load_roundtrip():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu", "fan:openfan:ch00:rpm"])
    model.set_visible("sensor:gpu", False)

    data = model.to_dict()
    assert "sensor:gpu" in data["hidden_keys"]

    model2 = SeriesSelectionModel()
    model2.update_known_keys(["sensor:cpu", "sensor:gpu", "fan:openfan:ch00:rpm"])
    model2.load_hidden(data["hidden_keys"])
    assert not model2.is_visible("sensor:gpu")
    assert model2.is_visible("sensor:cpu")


def test_selection_changed_signal(qtbot):
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu"])
    with qtbot.waitSignal(model.selection_changed, timeout=500):
        model.set_visible("sensor:cpu", False)


def test_keys_for_group():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "fan:openfan:ch00:rpm"])
    assert model.keys_for_group(SeriesGroup.TEMPS) == {"sensor:cpu"}
    assert model.keys_for_group(SeriesGroup.OPENFAN_FANS) == {"fan:openfan:ch00:rpm"}


# ---------------------------------------------------------------------------
# update_known_keys: prune dropped hidden keys + emit when a group mode hides a
# freshly-seen key.
# ---------------------------------------------------------------------------


def test_update_known_keys_prunes_dropped_hidden_key():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu"])
    model.set_visible("sensor:gpu", False)
    assert model.is_hidden("sensor:gpu")
    model.update_known_keys(["sensor:cpu"])  # gpu disappears from the known set
    # Re-discovering gpu must show it visible — i.e. it was pruned from hidden,
    # not silently retained across the drop.
    model.update_known_keys(["sensor:cpu", "sensor:gpu"])
    assert model.is_visible("sensor:gpu")


def test_update_known_keys_emits_when_mode_hides_new_key(qtbot):
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu"])
    model.apply_mode(ChartMode.THERMALS)  # group-based mode active
    calls: list[int] = []
    model.selection_changed.connect(lambda: calls.append(1))
    model.update_known_keys(["sensor:cpu", "fan:openfan:ch00:rpm"])  # new non-temps key
    assert calls == [1]  # the mode hid the fresh fan -> one emit
    assert not model.is_visible("fan:openfan:ch00:rpm")


# ---------------------------------------------------------------------------
# apply_mode presets (unit-level; the dashboard-integration paths live in
# test_chart_modes.py). Pins each ChartMode's resolved visibility.
# ---------------------------------------------------------------------------


def test_apply_mode_thermals_shows_only_temps():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "fan:openfan:ch00:rpm", "fan:hwmon:nct:fan1:rpm"])
    model.apply_mode(ChartMode.THERMALS)
    assert model._active_mode == ChartMode.THERMALS
    assert model.is_visible("sensor:cpu")
    assert not model.is_visible("fan:openfan:ch00:rpm")
    assert not model.is_visible("fan:hwmon:nct:fan1:rpm")


def test_apply_mode_fans_shows_only_fans():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "fan:openfan:ch00:rpm", "fan:hwmon:nct:fan1:rpm"])
    model.apply_mode(ChartMode.FANS)
    assert not model.is_visible("sensor:cpu")
    assert model.is_visible("fan:openfan:ch00:rpm")
    assert model.is_visible("fan:hwmon:nct:fan1:rpm")


def test_apply_mode_diagnostics_shows_all():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "fan:openfan:ch00:rpm"])
    model.set_visible("sensor:cpu", False)
    model.apply_mode(ChartMode.DIAGNOSTICS)  # everything visible (select_all)
    assert model.is_visible("sensor:cpu")
    assert model.is_visible("fan:openfan:ch00:rpm")


def test_apply_mode_combined_uses_curated_keys():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu", "fan:openfan:ch00:rpm"])
    model.apply_mode(ChartMode.COMBINED, curated_keys={"sensor:cpu"})
    assert model.is_visible("sensor:cpu")
    assert not model.is_visible("sensor:gpu")
    assert not model.is_visible("fan:openfan:ch00:rpm")


# ─── default_series_keys (relocated from dashboard_view, DEC-222) ─────────


def test_default_series_picks_one_per_category():
    """CPU, GPU and one mobo temp — the curated Combined subset."""
    sensors = [
        _sensor("c0", "cpu_temp"),
        _sensor("g0", "gpu_temp"),
        _sensor("m0", "mb_temp"),
    ]
    assert default_series_keys(sensors) == {"sensor:c0", "sensor:g0", "sensor:m0"}


def test_default_series_uses_the_wire_kind_tokens_only():
    """One spelling, and it is the daemon's (`WIRE-c`).

    This test used to assert that BOTH casings resolved, because `DemoService`
    emitted PascalCase kinds the daemon never sends. That was fixed at the
    source, so a PascalCase kind now resolves to nothing — which is correct: it
    is not a value any producer emits, and accepting it would keep the
    compensation alive in the one place still testing it.
    """
    wire = [_sensor("c0", "cpu_temp"), _sensor("g0", "gpu_temp"), _sensor("m0", "mb_temp")]
    assert default_series_keys(wire) == {"sensor:c0", "sensor:g0", "sensor:m0"}
    # Deliberately PascalCase: this asserts the compensation is GONE, so it
    # must survive any future normalisation sweep over this file.
    assert default_series_keys([_sensor("c0", "Cpu" + "Temp")]) == set()


def test_default_series_seeds_coolant_on_a_liquid_cooled_machine():
    """`WIRE-ai`: on an AIO machine the coolant temperature is arguably the
    headline number, and it was absent from the default chart."""
    sensors = [
        _sensor("c0", "cpu_temp"),
        _sensor("g0", "gpu_temp"),
        _sensor("m0", "mb_temp"),
        _sensor("l0", "coolant_temp"),
    ]
    assert default_series_keys(sensors) == {
        "sensor:c0",
        "sensor:g0",
        "sensor:m0",
        "sensor:l0",
    }


def test_an_air_cooled_machine_gets_exactly_what_it_got_before():
    """The opposite branch: the coolant slot must not add an empty series, or a
    machine with no liquid cooler pays for a feature it cannot use."""
    sensors = [_sensor("c0", "cpu_temp"), _sensor("g0", "gpu_temp"), _sensor("m0", "mb_temp")]
    assert default_series_keys(sensors) == {"sensor:c0", "sensor:g0", "sensor:m0"}


def test_default_series_takes_the_first_match_per_category():
    """Several CPU sensors yield exactly one key — the subset stays curated."""
    sensors = [_sensor("c0", "cpu_temp"), _sensor("c1", "cpu_temp")]
    assert default_series_keys(sensors) == {"sensor:c0"}


def test_default_series_drops_absent_categories():
    """A machine with no discrete GPU simply gets no GPU slot, not a bad key."""
    assert default_series_keys([_sensor("c0", "cpu_temp")]) == {"sensor:c0"}


def test_default_series_ignores_unrelated_kinds():
    assert default_series_keys([_sensor("x0", "water_temp")]) == set()


def test_default_series_empty_input():
    assert default_series_keys([]) == set()


def test_default_series_feeds_combined_mode_end_to_end():
    """The relocation's real contract: default_series_keys → apply_mode(COMBINED)
    leaves exactly the curated sensors visible. Before DEC-222 this pairing lived
    across two modules; a None curated set makes apply_mode a silent no-op, so
    this pins that the wiring actually selects something."""
    sensors = [_sensor("c0", "cpu_temp"), _sensor("g0", "gpu_temp")]
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:c0", "sensor:g0", "sensor:other", "fan:openfan:ch00:rpm"])
    model.apply_mode(ChartMode.COMBINED, default_series_keys(sensors))
    assert model.is_visible("sensor:c0")
    assert model.is_visible("sensor:g0")
    assert not model.is_visible("sensor:other")
    assert not model.is_visible("fan:openfan:ch00:rpm")
