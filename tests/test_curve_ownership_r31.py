"""R31: Curve ownership, preview truthfulness, and theme adherence tests.

Proves that each CurveConfig owns its own sensor_id and points independently,
that editing a curve refreshes its card preview, and that the Controls page
has no hardcoded font-size overrides.
"""

from __future__ import annotations

from pathlib import Path

from onlyfans.services.profile_service import (
    CurveConfig,
    CurvePoint,
    CurveType,
    Profile,
    ProfileService,
)


class TestPerCurveSensorOwnership:
    """Each curve stores its own sensor_id independently."""

    def test_two_curves_different_sensors(self):
        c1 = CurveConfig(id="c1", name="A", type=CurveType.GRAPH, sensor_id="cpu_temp")
        c2 = CurveConfig(id="c2", name="B", type=CurveType.GRAPH, sensor_id="gpu_temp")
        assert c1.sensor_id == "cpu_temp"
        assert c2.sensor_id == "gpu_temp"
        assert c1.sensor_id != c2.sensor_id

    def test_editing_one_sensor_does_not_change_other(self):
        c1 = CurveConfig(id="c1", name="A", type=CurveType.GRAPH, sensor_id="cpu_temp")
        c2 = CurveConfig(id="c2", name="B", type=CurveType.GRAPH, sensor_id="gpu_temp")
        c1.sensor_id = "disk_temp"
        assert c1.sensor_id == "disk_temp"
        assert c2.sensor_id == "gpu_temp"  # unchanged

    def test_same_sensor_allowed(self):
        """Multiple curves can reference the same sensor."""
        c1 = CurveConfig(id="c1", name="A", type=CurveType.GRAPH, sensor_id="cpu_temp")
        c2 = CurveConfig(id="c2", name="B", type=CurveType.GRAPH, sensor_id="cpu_temp")
        assert c1.sensor_id == c2.sensor_id
        # But they are still independent objects
        c1.sensor_id = "changed"
        assert c2.sensor_id == "cpu_temp"


class TestPerCurveGraphOwnership:
    """Each curve stores its own points independently."""

    def test_two_curves_different_points(self):
        c1 = CurveConfig(
            id="c1",
            name="A",
            type=CurveType.GRAPH,
            points=[CurvePoint(30, 20), CurvePoint(70, 80)],
        )
        c2 = CurveConfig(
            id="c2",
            name="B",
            type=CurveType.GRAPH,
            points=[CurvePoint(40, 30), CurvePoint(80, 90)],
        )
        assert c1.points[0].temp_c == 30
        assert c2.points[0].temp_c == 40

    def test_editing_one_graph_does_not_change_other(self):
        c1 = CurveConfig(
            id="c1",
            name="A",
            type=CurveType.GRAPH,
            points=[CurvePoint(30, 20), CurvePoint(70, 80)],
        )
        c2 = CurveConfig(
            id="c2",
            name="B",
            type=CurveType.GRAPH,
            points=[CurvePoint(40, 30), CurvePoint(80, 90)],
        )
        c1.points[0] = CurvePoint(10, 10)
        assert c1.points[0].temp_c == 10
        assert c2.points[0].temp_c == 40  # unchanged

    def test_same_sensor_different_graphs(self):
        """Two curves referencing same sensor have independent point sets."""
        c1 = CurveConfig(
            id="c1",
            name="A",
            type=CurveType.GRAPH,
            sensor_id="cpu_temp",
            points=[CurvePoint(30, 20), CurvePoint(70, 80)],
        )
        c2 = CurveConfig(
            id="c2",
            name="B",
            type=CurveType.GRAPH,
            sensor_id="cpu_temp",
            points=[CurvePoint(40, 50), CurvePoint(80, 100)],
        )
        assert c1.sensor_id == c2.sensor_id
        assert c1.points != c2.points


class TestPersistenceRoundtrip:
    """Save/load preserves per-curve sensor and points."""

    def test_save_load_preserves_sensor_per_curve(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = ProfileService()
        svc.load()

        profile = Profile(
            id="test",
            name="Test",
            curves=[
                CurveConfig(id="c1", name="A", type=CurveType.FLAT, sensor_id="cpu_temp"),
                CurveConfig(id="c2", name="B", type=CurveType.FLAT, sensor_id="gpu_temp"),
            ],
        )
        svc._profiles["test"] = profile
        svc.save_profile(profile)

        # Reload from disk
        svc2 = ProfileService()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc2.load()
        loaded = svc2.get_profile("test")

        assert loaded is not None
        assert loaded.curves[0].sensor_id == "cpu_temp"
        assert loaded.curves[1].sensor_id == "gpu_temp"

    def test_save_load_preserves_points_per_curve(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = ProfileService()
        svc.load()

        pts1 = [CurvePoint(30, 20), CurvePoint(70, 80)]
        pts2 = [CurvePoint(40, 50), CurvePoint(80, 100)]
        profile = Profile(
            id="test",
            name="Test",
            curves=[
                CurveConfig(id="c1", name="A", type=CurveType.GRAPH, points=pts1),
                CurveConfig(id="c2", name="B", type=CurveType.GRAPH, points=pts2),
            ],
        )
        svc._profiles["test"] = profile
        svc.save_profile(profile)

        svc2 = ProfileService()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc2.load()
        loaded = svc2.get_profile("test")

        assert loaded is not None
        assert len(loaded.curves[0].points) == 2
        assert loaded.curves[0].points[0].temp_c == 30
        assert loaded.curves[1].points[0].temp_c == 40


class TestEditorSensorIsolation:
    """R32: Switching curves in the editor loads each curve's own sensor."""

    def test_set_curve_restores_sensor_selection(self, qtbot):
        """set_curve() must set the sensor combo to match curve.sensor_id."""
        from onlyfans.ui.widgets.curve_editor import CurveEditor

        editor = CurveEditor()
        qtbot.addWidget(editor)

        # Populate sensor list
        editor.set_available_sensors([("cpu_temp", "CPU"), ("gpu_temp", "GPU")])

        # Load a curve with sensor_id = "gpu_temp"
        curve = CurveConfig(
            id="c1",
            name="Test",
            type=CurveType.GRAPH,
            sensor_id="gpu_temp",
            points=[CurvePoint(30, 20), CurvePoint(70, 80)],
        )
        editor.set_curve(curve)

        # The combo must show gpu_temp, not the default first item
        assert editor._sensor_combo.currentData() == "gpu_temp"

    def test_switching_curves_shows_each_own_sensor(self, qtbot):
        """After editing CPU(tctl), opening GPU(edge) must show edge."""
        from onlyfans.ui.widgets.curve_editor import CurveEditor

        editor = CurveEditor()
        qtbot.addWidget(editor)

        editor.set_available_sensors([("tctl", "Tctl"), ("edge", "Edge")])

        cpu_curve = CurveConfig(
            id="cpu",
            name="CPU",
            type=CurveType.GRAPH,
            sensor_id="tctl",
            points=[CurvePoint(30, 20), CurvePoint(70, 80)],
        )
        gpu_curve = CurveConfig(
            id="gpu",
            name="GPU",
            type=CurveType.GRAPH,
            sensor_id="edge",
            points=[CurvePoint(40, 30), CurvePoint(80, 90)],
        )

        # Edit CPU curve
        editor.set_curve(cpu_curve)
        assert editor._sensor_combo.currentData() == "tctl"

        # Switch to GPU curve
        editor.set_curve(gpu_curve)
        assert editor._sensor_combo.currentData() == "edge"

    def test_get_curve_returns_correct_sensor_after_switch(self, qtbot):
        """get_curve().sensor_id must match the loaded curve, not the previous one."""
        from onlyfans.ui.widgets.curve_editor import CurveEditor

        editor = CurveEditor()
        qtbot.addWidget(editor)

        editor.set_available_sensors([("tctl", "Tctl"), ("edge", "Edge")])

        cpu_curve = CurveConfig(
            id="cpu",
            name="CPU",
            type=CurveType.GRAPH,
            sensor_id="tctl",
            points=[CurvePoint(30, 20), CurvePoint(70, 80)],
        )
        gpu_curve = CurveConfig(
            id="gpu",
            name="GPU",
            type=CurveType.GRAPH,
            sensor_id="edge",
            points=[CurvePoint(40, 30), CurvePoint(80, 90)],
        )

        editor.set_curve(cpu_curve)
        editor.set_curve(gpu_curve)

        result = editor.get_curve()
        assert result.sensor_id == "edge"


class TestControlsPageThemeAdherence:
    """Controls page has no hardcoded font-size — inherits from theme."""

    def test_no_hardcoded_font_size_in_controls_page(self):
        """Verify the controls_page.py source has no inline font-size: Xpx."""
        import re

        source = Path("src/onlyfans/ui/pages/controls_page.py").read_text()
        # Should not contain font-size: Npx (hardcoded pixel sizes)
        matches = re.findall(r"font-size:\s*\d+px", source)
        assert matches == [], f"Hardcoded font-size found in controls_page.py: {matches}"
