"""DEC-213: RpmSparkline — static custom-paint sparkline (perf-safe)."""

from __future__ import annotations

from PySide6.QtCore import QTimer

from control_ofc.ui.widgets.rpm_sparkline import RpmSparkline


def test_paints_with_points(qtbot):
    spark = RpmSparkline(object_name="Test_Spark")
    qtbot.addWidget(spark)
    spark.resize(80, 32)
    spark.set_points([900, 950, 1000, 1050])
    assert spark.points() == (900.0, 950.0, 1000.0, 1050.0)
    assert not spark.grab().isNull()


def test_fewer_than_two_points_is_noop(qtbot):
    spark = RpmSparkline()
    qtbot.addWidget(spark)
    spark.resize(80, 32)
    spark.set_points([900])  # single point → paintEvent returns early
    assert not spark.grab().isNull()  # renders blank, never crashes


def test_redundant_set_points_skips_update(qtbot, monkeypatch):
    spark = RpmSparkline()
    qtbot.addWidget(spark)
    spark.set_points([900, 950])
    calls: list[int] = []
    monkeypatch.setattr(spark, "update", lambda: calls.append(1))
    spark.set_points([900, 950])  # identical → skip
    assert calls == []
    spark.set_points([900, 951])  # changed → repaint
    assert calls == [1]


def test_constant_size_hint(qtbot):
    spark = RpmSparkline()
    qtbot.addWidget(spark)
    baseline = spark.sizeHint()
    spark.set_points(list(range(1, 40)))
    spark.resize(400, 32)
    assert spark.sizeHint() == baseline  # never derived from data/size


def test_no_qtimer_child(qtbot):
    # Perf guard: the sparkline runs NO timer of its own — static paint only.
    spark = RpmSparkline()
    qtbot.addWidget(spark)
    assert spark.findChild(QTimer) is None


def test_warn_toggles_repaint(qtbot):
    spark = RpmSparkline()
    qtbot.addWidget(spark)
    spark.resize(80, 32)
    spark.set_points((1.0, 2.0, 3.0), warn=True)
    assert not spark.grab().isNull()
