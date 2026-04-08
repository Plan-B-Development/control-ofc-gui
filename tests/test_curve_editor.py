"""Tests for the interactive curve editor widget."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from onlyfans.services.profile_service import CurveConfig, CurvePoint
from onlyfans.ui.widgets.curve_editor import PRESETS, CurveEditor


@pytest.fixture()
def editor(qtbot):
    w = CurveEditor()
    qtbot.addWidget(w)
    return w


@pytest.fixture()
def curve_5pt():
    """A 5-point curve for testing."""
    return CurveConfig(
        sensor_id="test_sensor",
        points=[
            CurvePoint(30.0, 25.0),
            CurvePoint(40.0, 35.0),
            CurvePoint(50.0, 50.0),
            CurvePoint(60.0, 70.0),
            CurvePoint(80.0, 100.0),
        ],
    )


class TestCurveEditorBasics:
    def test_set_curve_populates_table(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        assert editor._table.rowCount() == 5

    def test_get_curve_returns_data(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        result = editor.get_curve()
        assert result is not None
        assert len(result.points) == 5

    def test_set_curve_clears_undo(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        assert len(editor._undo_stack) == 0
        assert len(editor._redo_stack) == 0


class TestTableEditing:
    def test_table_edit_updates_curve(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        # Edit the output of the first point
        editor._table.setItem(
            0,
            1,
            __import__("PySide6.QtWidgets", fromlist=["QTableWidgetItem"]).QTableWidgetItem("30.0"),
        )
        assert curve_5pt.points[0].output_pct == 30.0

    def test_table_edit_clamps_output(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        from PySide6.QtWidgets import QTableWidgetItem

        editor._table.setItem(0, 1, QTableWidgetItem("150.0"))
        # Should be clamped to 100
        assert curve_5pt.points[0].output_pct == 100.0

    def test_table_edit_clamps_temp(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        from PySide6.QtWidgets import QTableWidgetItem

        editor._table.setItem(0, 0, QTableWidgetItem("-10.0"))
        # Should be clamped to 0
        # After sort, this point is still first
        assert editor._curve.points[0].temp_c == 0.0

    def test_table_edit_maintains_x_order(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        from PySide6.QtWidgets import QTableWidgetItem

        # Set first point temp to 55 — should sort after 50
        editor._table.setItem(0, 0, QTableWidgetItem("55.0"))
        temps = [p.temp_c for p in curve_5pt.points]
        assert temps == sorted(temps)

    def test_table_edit_invalid_value_reverts(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        from PySide6.QtWidgets import QTableWidgetItem

        original = curve_5pt.points[0].temp_c
        editor._table.setItem(0, 0, QTableWidgetItem("abc"))
        # Should revert — table refresh restores original value
        assert curve_5pt.points[0].temp_c == original

    def test_table_edit_pushes_undo(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        from PySide6.QtWidgets import QTableWidgetItem

        editor._table.setItem(0, 1, QTableWidgetItem("42.0"))
        assert len(editor._undo_stack) == 1


class TestAddRemovePoints:
    def test_add_point_increases_count(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        initial = len(curve_5pt.points)
        editor._on_add_point()
        assert len(curve_5pt.points) == initial + 1

    def test_add_point_maintains_sort(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._on_add_point()
        temps = [p.temp_c for p in curve_5pt.points]
        assert temps == sorted(temps)

    def test_add_point_at_specific_temp(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._add_point_at(45.0)
        temps = [p.temp_c for p in curve_5pt.points]
        assert 45.0 in temps
        assert temps == sorted(temps)

    def test_add_point_too_close_rejected(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        initial = len(curve_5pt.points)
        # Try to add at exactly an existing point
        editor._add_point_at(30.0)
        assert len(curve_5pt.points) == initial  # no change

    def test_remove_point_decreases_count(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._selected_idx = 2
        initial = len(curve_5pt.points)
        editor._on_remove_point()
        assert len(curve_5pt.points) == initial - 1

    def test_remove_enforces_minimum(self, editor):
        curve = CurveConfig(points=[CurvePoint(30.0, 25.0), CurvePoint(80.0, 100.0)])
        editor.set_curve(curve)
        editor._selected_idx = 0
        editor._on_remove_point()
        # Should not remove — minimum 2 points
        assert len(curve.points) == 2

    def test_remove_no_selection_is_noop(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._selected_idx = None
        initial = len(curve_5pt.points)
        editor._on_remove_point()
        assert len(curve_5pt.points) == initial

    def test_add_pushes_undo(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._on_add_point()
        assert len(editor._undo_stack) == 1

    def test_remove_pushes_undo(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._selected_idx = 2
        editor._on_remove_point()
        assert len(editor._undo_stack) == 1


class TestUndoRedo:
    def test_undo_restores_previous(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        original_output = curve_5pt.points[0].output_pct
        # Make a change
        editor._push_undo()
        curve_5pt.points[0].output_pct = 99.0
        # Undo
        editor.undo()
        assert curve_5pt.points[0].output_pct == original_output

    def test_redo_after_undo(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._push_undo()
        curve_5pt.points[0].output_pct = 99.0
        editor.undo()
        # Now redo
        editor.redo()
        assert curve_5pt.points[0].output_pct == 99.0

    def test_undo_empty_stack_is_noop(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        original = curve_5pt.points[0].output_pct
        editor.undo()  # nothing to undo
        assert curve_5pt.points[0].output_pct == original

    def test_redo_empty_stack_is_noop(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        original = curve_5pt.points[0].output_pct
        editor.redo()  # nothing to redo
        assert curve_5pt.points[0].output_pct == original

    def test_new_change_clears_redo(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._push_undo()
        curve_5pt.points[0].output_pct = 99.0
        editor.undo()
        assert len(editor._redo_stack) == 1
        # New change clears redo
        editor._push_undo()
        assert len(editor._redo_stack) == 0


class TestPresets:
    def test_preset_replaces_curve(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        # Select "Linear" preset (index 1, since 0 is placeholder)
        editor._preset_combo.setCurrentIndex(1)
        assert len(curve_5pt.points) == len(PRESETS["Linear"])
        assert curve_5pt.points[0].temp_c == PRESETS["Linear"][0].temp_c

    def test_preset_pushes_undo(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._preset_combo.setCurrentIndex(2)  # Quiet
        assert len(editor._undo_stack) == 1

    def test_preset_combo_resets_to_placeholder(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._preset_combo.setCurrentIndex(1)
        assert editor._preset_combo.currentIndex() == 0


class TestKeyboardNudge:
    def test_nudge_up_increases_output(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._selected_idx = 2
        original = curve_5pt.points[2].output_pct
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(event)
        assert curve_5pt.points[2].output_pct == original + 1

    def test_nudge_down_decreases_output(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._selected_idx = 2
        original = curve_5pt.points[2].output_pct
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(event)
        assert curve_5pt.points[2].output_pct == original - 1

    def test_nudge_right_increases_temp(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._selected_idx = 2
        original = curve_5pt.points[2].temp_c
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(event)
        assert curve_5pt.points[2].temp_c == original + 1

    def test_nudge_left_decreases_temp(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._selected_idx = 2
        original = curve_5pt.points[2].temp_c
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(event)
        assert curve_5pt.points[2].temp_c == original - 1

    def test_nudge_respects_neighbour_constraint(self, editor):
        """Can't nudge right past the next point."""
        curve = CurveConfig(
            points=[CurvePoint(30.0, 25.0), CurvePoint(30.5, 50.0), CurvePoint(80.0, 100.0)]
        )
        editor.set_curve(curve)
        editor._selected_idx = 0
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        # Try nudge right — should clamp at 30.0 (next is 30.5, spacing 0.5)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(event)
        assert curve.points[0].temp_c <= curve.points[1].temp_c - 0.5

    def test_nudge_pushes_undo(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor._selected_idx = 2
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(event)
        assert len(editor._undo_stack) == 1


class TestSensorMarker:
    def test_set_sensor_value_creates_marker(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor.set_current_sensor_value(45.0)
        assert editor._sensor_vline is not None
        assert editor._sensor_marker is not None

    def test_clear_sensor_value_removes_marker(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        editor.set_current_sensor_value(45.0)
        editor.set_current_sensor_value(None)
        assert editor._sensor_vline is None
        assert editor._sensor_marker is None


class TestMonotonicConstraint:
    def test_points_stay_sorted_after_table_edit(self, editor, curve_5pt):
        editor.set_curve(curve_5pt)
        from PySide6.QtWidgets import QTableWidgetItem

        # Set first point temp higher than second
        editor._table.setItem(0, 0, QTableWidgetItem("45.0"))
        temps = [p.temp_c for p in curve_5pt.points]
        assert temps == sorted(temps)


class TestSensorSelection:
    """CTRL-001: Sensor selection must persist and not silently revert."""

    def test_sensor_selection_persists_to_model(self, editor, curve_5pt):
        """Selecting a sensor immediately updates the curve's sensor_id."""
        editor.set_curve(curve_5pt)
        editor.set_available_sensors(
            [
                ("sensor_a", "CPU Temp"),
                ("sensor_b", "GPU Temp"),
            ]
        )
        # Select second sensor
        editor._sensor_combo.setCurrentIndex(1)
        assert curve_5pt.sensor_id == "sensor_b"

    def test_sensor_selection_survives_refresh(self, editor, curve_5pt):
        """Repopulating the sensor list does not clobber user selection."""
        curve_5pt.sensor_id = "sensor_b"
        editor.set_curve(curve_5pt)
        sensors = [("sensor_a", "CPU Temp"), ("sensor_b", "GPU Temp")]
        editor.set_available_sensors(sensors)
        assert editor._sensor_combo.currentData() == "sensor_b"

        # Simulate a second refresh with same sensors
        editor.set_available_sensors(sensors)
        assert editor._sensor_combo.currentData() == "sensor_b"

    def test_sensor_list_change_preserves_selection(self, editor, curve_5pt):
        """When sensor list changes (new sensor added), selection is preserved."""
        curve_5pt.sensor_id = "sensor_a"
        editor.set_curve(curve_5pt)
        editor.set_available_sensors([("sensor_a", "CPU"), ("sensor_b", "GPU")])
        assert editor._sensor_combo.currentData() == "sensor_a"

        # New sensor added to list
        editor._last_sensor_ids = []  # force repopulation
        editor.set_available_sensors(
            [
                ("sensor_a", "CPU"),
                ("sensor_b", "GPU"),
                ("sensor_c", "NVMe"),
            ]
        )
        assert editor._sensor_combo.currentData() == "sensor_a"

    def test_sensor_removed_falls_to_first(self, editor, curve_5pt):
        """If selected sensor disappears, combo falls to first available."""
        curve_5pt.sensor_id = "sensor_gone"
        editor.set_curve(curve_5pt)
        editor.set_available_sensors([("sensor_a", "CPU"), ("sensor_b", "GPU")])
        # sensor_gone not in list, combo defaults to index 0
        assert editor._sensor_combo.currentIndex() == 0


class TestMouseDrag:
    """P1: Simulate actual point dragging via viewport event filter."""

    def test_drag_moves_point(self, editor, curve_5pt, qtbot):
        """Press on a point, move mouse, release — point should move."""
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent

        editor.set_curve(curve_5pt)
        editor.show()
        qtbot.waitExposed(editor)

        # Get the viewport
        viewport = editor._plot_widget.viewport()
        plot = editor._plot_widget.getPlotItem()
        if plot is None:
            pytest.skip("No plot item")
        vb = plot.vb

        # Map point 2 (50°C, 50%) to scene then to viewport coords
        scene_pos = vb.mapViewToScene(QPointF(50.0, 50.0))
        widget_pos = editor._plot_widget.mapFromScene(scene_pos)

        original_output = curve_5pt.points[2].output_pct

        # Simulate press (use globalPos variant to avoid deprecation)
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(widget_pos),
            QPointF(widget_pos),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        editor.eventFilter(viewport, press)
        assert editor._drag_active

        # Simulate move upward (increase output)
        moved_scene = vb.mapViewToScene(QPointF(50.0, 60.0))
        moved_widget = editor._plot_widget.mapFromScene(moved_scene)
        # sigMouseMoved uses scene coordinates
        editor._on_mouse_moved(moved_scene)

        # Simulate release
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(moved_widget),
            QPointF(moved_widget),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        editor.eventFilter(viewport, release)
        assert not editor._drag_active

        # Point should have moved (output changed)
        assert curve_5pt.points[2].output_pct != original_output

    def test_drag_pushes_undo_at_start(self, editor, curve_5pt, qtbot):
        """Drag start should push undo stack."""
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent

        editor.set_curve(curve_5pt)
        editor.show()
        qtbot.waitExposed(editor)

        viewport = editor._plot_widget.viewport()
        plot = editor._plot_widget.getPlotItem()
        vb = plot.vb
        scene_pos = vb.mapViewToScene(QPointF(50.0, 50.0))
        widget_pos = editor._plot_widget.mapFromScene(scene_pos)

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(widget_pos),
            QPointF(widget_pos),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        editor.eventFilter(viewport, press)
        assert len(editor._undo_stack) == 1
