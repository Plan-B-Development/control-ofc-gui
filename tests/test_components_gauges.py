"""DEC-211: RadialGauge — static custom-paint donut gauge."""

from __future__ import annotations

from control_ofc.ui.components.gauges import RadialGauge


def test_set_value_clamps_and_stores(qtbot):
    gauge = RadialGauge(object_name="Test_Gauge")
    qtbot.addWidget(gauge)
    gauge.set_value(0.5, center_text="996", caption="REVERTS", state="crit")
    assert gauge.fraction() == 0.5
    assert gauge.state() == "crit"
    gauge.set_value(5.0, center_text="x")  # over-range clamps to 1.0
    assert gauge.fraction() == 1.0
    gauge.set_value(-2.0, center_text="x")  # under-range clamps to 0.0
    assert gauge.fraction() == 0.0


def test_paints_without_error(qtbot):
    gauge = RadialGauge(object_name="Test_Gauge")
    qtbot.addWidget(gauge)
    gauge.resize(160, 160)
    gauge.set_value(0.75, center_text="996", caption="REVERTS", state="crit")
    pixmap = gauge.grab()
    assert not pixmap.isNull()


def test_zero_fraction_paints_track_only(qtbot):
    gauge = RadialGauge()
    qtbot.addWidget(gauge)
    gauge.resize(160, 160)
    gauge.set_value(0.0, center_text="0", caption="REVERTS", state="ok")
    assert not gauge.grab().isNull()  # exercises the no-progress-arc branch
