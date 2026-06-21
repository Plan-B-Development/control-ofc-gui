"""Tests for the series selection model."""

from __future__ import annotations

from control_ofc.services.series_selection import ChartMode, SeriesGroup, SeriesSelectionModel


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


def test_toggle():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu"])
    model.toggle("sensor:cpu")
    assert not model.is_visible("sensor:cpu")
    model.toggle("sensor:cpu")
    assert model.is_visible("sensor:cpu")


def test_visible_keys():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu", "fan:openfan:ch00:rpm"])
    model.set_visible("sensor:gpu", False)
    assert model.visible_keys() == {"sensor:cpu", "fan:openfan:ch00:rpm"}


def test_group_toggle_temps():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu", "fan:openfan:ch00:rpm"])
    model.set_group_visible(SeriesGroup.TEMPS, False)
    assert not model.is_visible("sensor:cpu")
    assert not model.is_visible("sensor:gpu")
    assert model.is_visible("fan:openfan:ch00:rpm")


def test_group_toggle_mobo_fans():
    model = SeriesSelectionModel()
    model.update_known_keys(["fan:hwmon:nct:fan1:rpm", "fan:openfan:ch00:rpm"])
    model.set_group_visible(SeriesGroup.MOBO_FANS, False)
    assert not model.is_visible("fan:hwmon:nct:fan1:rpm")
    assert model.is_visible("fan:openfan:ch00:rpm")


def test_group_toggle_openfan_fans():
    model = SeriesSelectionModel()
    model.update_known_keys(["fan:openfan:ch00:rpm", "fan:openfan:ch01:rpm", "sensor:cpu"])
    model.set_group_visible(SeriesGroup.OPENFAN_FANS, False)
    assert not model.is_visible("fan:openfan:ch00:rpm")
    assert not model.is_visible("fan:openfan:ch01:rpm")
    assert model.is_visible("sensor:cpu")


def test_select_all():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu"])
    model.set_visible("sensor:cpu", False)
    model.select_all()
    assert model.is_visible("sensor:cpu")
    assert model.is_visible("sensor:gpu")


def test_select_none():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu"])
    model.select_none()
    assert not model.is_visible("sensor:cpu")
    assert not model.is_visible("sensor:gpu")


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
        model.toggle("sensor:cpu")


def test_is_group_fully_visible():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu"])
    assert model.is_group_fully_visible(SeriesGroup.TEMPS)
    model.set_visible("sensor:cpu", False)
    assert not model.is_group_fully_visible(SeriesGroup.TEMPS)


def test_is_group_partially_visible():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu"])
    model.set_visible("sensor:cpu", False)
    assert model.is_group_partially_visible(SeriesGroup.TEMPS)
    model.set_visible("sensor:gpu", False)
    assert not model.is_group_partially_visible(SeriesGroup.TEMPS)


def test_keys_for_group():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "fan:openfan:ch00:rpm"])
    assert model.keys_for_group(SeriesGroup.TEMPS) == {"sensor:cpu"}
    assert model.keys_for_group(SeriesGroup.OPENFAN_FANS) == {"fan:openfan:ch00:rpm"}


# ---------------------------------------------------------------------------
# set_group_visible: each member's hide/show must depend on BOTH `visible` AND
# the member's current hidden-state. A mutant relaxing `and`->`or` resurrects an
# already-hidden member when the group is hidden.
# ---------------------------------------------------------------------------


def test_set_group_visible_hide_with_one_already_hidden():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu"])
    model.set_visible("sensor:cpu", False)  # pre-hide one TEMPS member
    assert not model.is_visible("sensor:cpu")
    assert model.is_visible("sensor:gpu")
    # Hiding the whole group must hide BOTH and never resurrect the pre-hidden one.
    model.set_group_visible(SeriesGroup.TEMPS, False)
    assert not model.is_visible("sensor:cpu")  # the `and`->`or` mutant un-hides this
    assert not model.is_visible("sensor:gpu")


def test_set_group_visible_show_from_all_hidden():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu", "fan:openfan:ch00:rpm"])
    model.select_none()  # hide everything
    model.set_group_visible(SeriesGroup.TEMPS, True)  # show only the temps group
    assert model.is_visible("sensor:cpu")
    assert model.is_visible("sensor:gpu")
    assert not model.is_visible("fan:openfan:ch00:rpm")  # non-temps stays hidden


def test_set_group_visible_emits_once_on_change(qtbot):
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "sensor:gpu"])
    calls: list[int] = []
    model.selection_changed.connect(lambda: calls.append(1))
    model.set_group_visible(SeriesGroup.TEMPS, False)
    assert calls == [1]  # exactly one emit (kills the `changed=True`->None mutant)


def test_set_group_visible_no_emit_when_unchanged(qtbot):
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu"])
    model.set_group_visible(SeriesGroup.TEMPS, False)  # now hidden
    calls: list[int] = []
    model.selection_changed.connect(lambda: calls.append(1))
    model.set_group_visible(SeriesGroup.TEMPS, False)  # no-op
    assert calls == []  # no spurious emit (kills the `changed=False`->True mutant)


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
# classify: the synthetic aggregate-RPM key is its own group (checked first).
# ---------------------------------------------------------------------------


def test_classify_fan_aggregate():
    from control_ofc.constants import AGGREGATE_FAN_RPM_KEY

    assert SeriesSelectionModel.classify(AGGREGATE_FAN_RPM_KEY) == SeriesGroup.FAN_AGGREGATE


# ---------------------------------------------------------------------------
# apply_mode presets (unit-level; the dashboard-integration paths live in
# test_chart_modes.py). Pins each ChartMode's resolved visibility.
# ---------------------------------------------------------------------------


def test_apply_mode_thermals_shows_only_temps():
    model = SeriesSelectionModel()
    model.update_known_keys(["sensor:cpu", "fan:openfan:ch00:rpm", "fan:hwmon:nct:fan1:rpm"])
    model.apply_mode(ChartMode.THERMALS)
    assert model.active_mode == ChartMode.THERMALS
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
