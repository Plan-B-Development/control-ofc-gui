"""Tests for the series selection model."""

from __future__ import annotations

from control_ofc.services.series_selection import SeriesGroup, SeriesSelectionModel


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
