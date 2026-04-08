"""Tests for CurveCard widget — preview rendering, sensor display, status."""

from __future__ import annotations

import pytest

from onlyfans.services.profile_service import CurveConfig, CurvePoint, CurveType
from onlyfans.ui.widgets.curve_card import CurveCard


@pytest.fixture()
def graph_curve():
    return CurveConfig(
        id="g1",
        name="Test Graph",
        type=CurveType.GRAPH,
        sensor_id="cpu_temp",
        points=[
            CurvePoint(30.0, 20.0),
            CurvePoint(50.0, 50.0),
            CurvePoint(80.0, 100.0),
        ],
    )


@pytest.fixture()
def flat_curve():
    return CurveConfig(id="f1", name="Test Flat", type=CurveType.FLAT, flat_output_pct=50.0)


class TestPreview:
    def test_graph_preview_renders(self, qtbot, graph_curve):
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        pixmap = card._preview.pixmap()
        assert pixmap is not None
        assert not pixmap.isNull()

    def test_flat_shows_text_summary(self, qtbot, flat_curve):
        card = CurveCard(flat_curve)
        qtbot.addWidget(card)
        assert "Flat: 50%" in card._preview.text()

    def test_linear_shows_text_summary(self, qtbot):
        curve = CurveConfig(
            type=CurveType.LINEAR,
            start_temp_c=30,
            start_output_pct=20,
            end_temp_c=80,
            end_output_pct=100,
        )
        card = CurveCard(curve)
        qtbot.addWidget(card)
        text = card._preview.text()
        assert "30" in text
        assert "80" in text

    def test_single_point_no_crash(self, qtbot):
        curve = CurveConfig(type=CurveType.GRAPH, points=[CurvePoint(50.0, 50.0)])
        card = CurveCard(curve)
        qtbot.addWidget(card)
        assert card._preview is not None

    def test_empty_points_no_crash(self, qtbot):
        curve = CurveConfig(type=CurveType.GRAPH, points=[])
        card = CurveCard(curve)
        qtbot.addWidget(card)
        assert card._preview is not None

    def test_update_redraws(self, qtbot, graph_curve):
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        graph_curve.points.append(CurvePoint(90.0, 100.0))
        card.update_curve(graph_curve)
        pixmap = card._preview.pixmap()
        assert pixmap is not None


class TestCurveCardContent:
    def test_shows_name(self, qtbot, graph_curve):
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        assert card._name_label.text() == "Test Graph"

    def test_sensor_display(self, qtbot, graph_curve):
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        card.update_sensor_display("CPU Package", 42.5)
        assert "CPU Package" in card._sensor_label.text()
        assert "42.5" in card._sensor_label.text()

    def test_used_by_assigned(self, qtbot, graph_curve):
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        card.set_used_by(["Intake", "Exhaust"])
        assert "Intake" in card._used_by_label.text()
        assert "Assigned" in card._status_label.text()

    def test_used_by_empty_shows_unassigned(self, qtbot, graph_curve):
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        card.set_used_by([])
        assert "Not assigned" in card._used_by_label.text()
        assert "Unassigned" in card._status_label.text()

    def test_used_by_truncates_many(self, qtbot, graph_curve):
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        card.set_used_by(["A", "B", "C", "D", "E"])
        assert "+2" in card._used_by_label.text()
