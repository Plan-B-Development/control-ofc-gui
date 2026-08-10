"""Tests for CurveCard widget — preview rendering, sensor display, status."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QPushButton

from control_ofc.services.profile_service import CurveConfig, CurvePoint, CurveType
from control_ofc.ui.widgets.curve_card import CurveCard


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


@pytest.fixture()
def stepped_curve():
    return CurveConfig(
        id="s1",
        name="Test Stepped",
        type=CurveType.STEPPED,
        sensor_id="cpu_temp",
        points=[
            CurvePoint(30.0, 20.0),
            CurvePoint(50.0, 50.0),
            CurvePoint(80.0, 100.0),
        ],
    )


class TestPreview:
    """The preview is owner-drawn (DEC-129): it paints the curve in
    paintEvent and exposes the painted text via summary_text()."""

    def test_graph_preview_renders(self, qtbot, graph_curve):
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        assert card._preview.curve is graph_curve
        assert card._preview.summary_text() == ""  # graphs paint a polyline
        assert not card._preview.grab().isNull()  # paint pass succeeds

    def test_flat_shows_text_summary(self, qtbot, flat_curve):
        card = CurveCard(flat_curve)
        qtbot.addWidget(card)
        assert "Flat: 50%" in card._preview.summary_text()

    def test_long_summary_elides_rather_than_clipping(self, qtbot, flat_curve):
        """DEC-238: ``drawText`` cuts mid-glyph when the summary outruns the
        width — and half a digit reads as a real value. The Trigger form is the
        long one, and the dashboard tile renders summaries in a narrow band."""
        curve = CurveConfig(
            id="t1",
            name="Trig",
            type=CurveType.TRIGGER,
            trigger_idle_pct=30.0,
            trigger_idle_temp_c=40.0,
            trigger_load_pct=100.0,
            trigger_load_temp_c=70.0,
        )
        card = CurveCard(curve)
        qtbot.addWidget(card)
        card._preview.setFixedWidth(50)
        card.show()
        painted = card._preview.painted_summary_text()
        assert painted.endswith("…")
        assert painted != card._preview.summary_text()
        # The full text is still what the widget knows — only the paint is short.
        assert card._preview.summary_text().startswith("Idle 30%")

    def test_summary_that_fits_is_painted_whole(self, qtbot, flat_curve):
        card = CurveCard(flat_curve)
        qtbot.addWidget(card)
        card._preview.setFixedWidth(400)
        card.show()
        assert card._preview.painted_summary_text() == card._preview.summary_text()

    def test_graph_curve_paints_no_summary_text(self, qtbot, graph_curve):
        """Elision must not invent text for the curves that paint a polyline."""
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        assert card._preview.painted_summary_text() == ""

    def test_stepped_preview_renders_staircase(self, qtbot, stepped_curve):
        # Stepped paints a staircase polyline like a graph (no text summary).
        card = CurveCard(stepped_curve)
        qtbot.addWidget(card)
        assert card._preview.curve is stepped_curve
        assert card._preview.summary_text() == ""
        assert not card._preview.grab().isNull()

    def test_trigger_shows_text_summary(self, qtbot):
        curve = CurveConfig(
            type=CurveType.TRIGGER,
            trigger_idle_temp_c=40,
            trigger_load_temp_c=60,
            trigger_idle_pct=30,
            trigger_load_pct=80,
        )
        card = CurveCard(curve)
        qtbot.addWidget(card)
        text = card._preview.summary_text()
        assert "Idle 30%" in text
        assert "Load 80%" in text

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
        text = card._preview.summary_text()
        assert "30" in text
        assert "80" in text

    def test_mix_shows_function_and_count(self, qtbot):
        # DEC-150: the Mix summary is self-contained (function + input count),
        # never resolving other curves' names — the R31 ownership rule.
        curve = CurveConfig(type=CurveType.MIX, mix_function="max", mix_curve_ids=["a", "b", "c"])
        card = CurveCard(curve)
        qtbot.addWidget(card)
        assert card._preview.summary_text() == "Max of 3 curves"

    def test_mix_singular_count(self, qtbot):
        curve = CurveConfig(type=CurveType.MIX, mix_function="average", mix_curve_ids=["a"])
        card = CurveCard(curve)
        qtbot.addWidget(card)
        assert card._preview.summary_text() == "Average of 1 curve"

    def test_sync_shows_signed_offset(self, qtbot):
        # DEC-151: Sync summary shows a signed offset, no target name resolution.
        card_pos = CurveCard(CurveConfig(type=CurveType.SYNC, sync_offset_pct=10.0))
        qtbot.addWidget(card_pos)
        assert card_pos._preview.summary_text() == "Mirror control +10%"
        card_neg = CurveCard(CurveConfig(type=CurveType.SYNC, sync_offset_pct=-5.0))
        qtbot.addWidget(card_neg)
        assert card_neg._preview.summary_text() == "Mirror control -5%"

    def test_single_point_no_crash(self, qtbot):
        curve = CurveConfig(type=CurveType.GRAPH, points=[CurvePoint(50.0, 50.0)])
        card = CurveCard(curve)
        qtbot.addWidget(card)
        assert not card._preview.grab().isNull()

    def test_empty_points_no_crash(self, qtbot):
        curve = CurveConfig(type=CurveType.GRAPH, points=[])
        card = CurveCard(curve)
        qtbot.addWidget(card)
        assert not card._preview.grab().isNull()

    def test_update_redraws(self, qtbot, graph_curve):
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        graph_curve.points.append(CurvePoint(90.0, 100.0))
        card.update_curve(graph_curve)
        assert card._preview.curve is graph_curve
        assert len(card._preview.curve.points) == 4


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

    def test_used_by_sets_active_property(self, qtbot, graph_curve):
        """DEC-214: an assigned curve is the ACTIVE card (accent border via the
        ``active`` QSS property); an unassigned one is not."""
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        card.set_used_by(["Intake"])
        assert card.property("active") is True
        card.set_used_by([])
        assert card.property("active") is False

    def test_set_editing_toggles_property_and_glow(self, qtbot, graph_curve):
        """DEC-233: editing a curve lights its card — an ``editing`` QSS property
        (2px border + tint) plus an accent drop-shadow glow effect."""
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        assert card.is_editing is False
        assert card.graphicsEffect() is None
        card.set_editing(True)
        assert card.is_editing is True
        assert card.property("editing") is True
        assert card.graphicsEffect() is not None
        card.set_editing(False)
        assert card.is_editing is False
        assert card.property("editing") is False
        assert card.graphicsEffect() is None

    def test_editing_is_independent_of_active(self, qtbot, graph_curve):
        """DEC-233: a curve can be assigned (active) AND being edited at once — the
        two highlight states are separate properties."""
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        card.set_used_by(["Intake"])
        card.set_editing(True)
        assert card.property("active") is True
        assert card.property("editing") is True

    def test_unlink_action_emits_unlink_requested(self, qtbot, graph_curve):
        """DEC-214: the Actions ▸ Unlink entry emits ``unlink_requested(curve_id)``."""
        card = CurveCard(graph_curve)
        qtbot.addWidget(card)
        btn = card.findChild(QPushButton, f"CurveCard_Btn_actions_{graph_curve.id}")
        assert btn is not None
        unlink = next(a for a in btn.menu().actions() if a.text() == "Unlink")
        with qtbot.waitSignal(card.unlink_requested) as blocker:
            unlink.trigger()
        assert blocker.args == [graph_curve.id]


def test_curve_card_grip_resize_carries_curve_id(qtbot, graph_curve):
    """DEC-235: the extracted ResizableGridCard base stores ``curve.id`` as its
    ``_item_id``, so the grip's resize signal must carry ``curve.id`` — a wrong id
    in ``_init_grid_card`` would silently break the Controls page's id-keyed size
    persistence, and no ControlCard-only test would catch it."""
    from dataclasses import replace

    card = CurveCard(graph_curve)
    qtbot.addWidget(card)
    seen: list[str] = []
    card.resized.connect(lambda cid, w, h: seen.append(cid))
    card._grip.resize_finished.emit(360, 320)
    assert seen == [graph_curve.id]
    # update_curve keeps the item id live (parity: the pre-refactor code read
    # self._curve.id at emit time, so a same-id update must not change the id).
    card.update_curve(replace(graph_curve, name="Renamed"))
    seen.clear()
    card._grip.resize_finished.emit(360, 320)
    assert seen == [graph_curve.id]


class TestUsedByElision:
    """DEC-258, extended. Caught by looking at the release screenshot.

    The card is a fixed width, so a plain `QLabel` holding two or three role
    names clips mid-word with no ellipsis — it shipped as "Used by: Case Intake,
    Case Exha". Same defect and same fix as the control card's curve line; the
    sibling widget was fixed and this one was not.
    """

    def test_the_used_by_label_can_shrink(self, qtbot):
        from control_ofc.services.profile_service import CurveConfig, CurveType
        from control_ofc.ui.components.labels import ElidedLabel
        from control_ofc.ui.widgets.curve_card import CurveCard

        card = CurveCard(CurveConfig(id="c1", name="Case Fan Curve", type=CurveType.GRAPH))
        qtbot.addWidget(card)
        card.set_used_by(["Case Intake", "Case Exhaust", "Front Radiator"])

        label = card._used_by_label
        assert isinstance(label, ElidedLabel), (
            "a plain QLabel refuses to shrink below its full text, so it clips "
            "mid-word instead of eliding"
        )
        assert label.minimumSizeHint().width() < label.sizeHint().width()

    def test_the_full_role_list_survives_as_text_and_tooltip(self, qtbot):
        """Elision is paint-time (DEC-231): the stored string stays verbatim, and
        the tooltip still carries every name, not just the first three."""
        from control_ofc.services.profile_service import CurveConfig, CurveType
        from control_ofc.ui.widgets.curve_card import CurveCard

        card = CurveCard(CurveConfig(id="c1", name="Case Fan Curve", type=CurveType.GRAPH))
        qtbot.addWidget(card)
        roles = ["Case Intake", "Case Exhaust", "Front Radiator", "Pump / AIO"]
        card.set_used_by(roles)

        assert "Case Exhaust" in card._used_by_label.text()
        assert "…" not in card._used_by_label.text()
        for r in roles:
            assert r in card._used_by_label.toolTip()
