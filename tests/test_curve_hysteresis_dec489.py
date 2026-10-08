"""DEC-489: a curve's own slow-down band (falling-temperature deadband width).

The daemon evaluates the band; the GUI edits and shows it. These tests pin the
GUI's half: the field round-trips without inventing a value, the editors are
gated on ``control.curve_hysteresis``, only the curve types with a band take
one, and "Apply to all curves" reaches exactly those curves. The daemon-side
behaviour is pinned by its own tests and the shared ``parity_vectors.json``.
"""

from __future__ import annotations

import math

import pytest

from control_ofc.api.models import (
    Capabilities,
    ControlCapability,
    OperationMode,
    parse_capabilities,
)
from control_ofc.services.curve_hysteresis import (
    HYSTERESIS_CURVE_TYPES,
    HYSTERESIS_DEFAULT_C,
    HYSTERESIS_MAX_C,
    apply_hysteresis_to_all,
    curve_meta_text,
    curve_uses_hysteresis,
    effective_hysteresis_c,
    not_applicable_reason,
)
from control_ofc.services.daemon_features import daemon_supports
from control_ofc.services.profile_service import CurveConfig, CurveType, Profile
from control_ofc.ui.components.hysteresis_row import DEMO_NOTE
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.widgets.curve_edit_dialog import CurveEditDialog
from control_ofc.ui.widgets.curve_editor import CurveEditor

SUPPORTED = Capabilities(control=ControlCapability(autonomous_control=True, curve_hysteresis=True))


def _curve(cid: str, ctype: CurveType, band: float | None = None) -> CurveConfig:
    return CurveConfig(id=cid, name=cid, type=ctype, hysteresis_c=band)


# ─── Model ────────────────────────────────────────────────────────────


class TestModelRoundTrip:
    @pytest.mark.parametrize("ctype", sorted(HYSTERESIS_CURVE_TYPES, key=lambda t: t.value))
    def test_a_set_band_round_trips_on_every_type_that_has_one(self, ctype):
        curve = CurveConfig.from_dict(
            {"id": "c", "name": "C", "type": ctype.value, "hysteresis_c": 4.5}
        )
        assert curve.hysteresis_c == 4.5
        assert curve.to_dict()["hysteresis_c"] == 4.5

    def test_absent_stays_absent_through_a_save(self):
        """Defaulting the field to a number would write it into every curve on the
        next save, turning the daemon's default into a pinned value."""
        curve = CurveConfig.from_dict({"id": "c", "name": "C", "type": "graph"})
        assert curve.hysteresis_c is None
        assert "hysteresis_c" not in curve.to_dict()
        # null is "absent", as for the other optional curve scalars.
        nulled = CurveConfig.from_dict(
            {"id": "c", "name": "C", "type": "graph", "hysteresis_c": None}
        )
        assert nulled.hysteresis_c is None

    @pytest.mark.parametrize("ctype", [CurveType.FLAT, CurveType.TRIGGER, CurveType.MIX])
    def test_types_without_a_band_do_not_write_one(self, ctype):
        curve = _curve("c", ctype, band=5.0)
        assert "hysteresis_c" not in curve.to_dict()

    @pytest.mark.parametrize(("raw", "loaded"), [(12.0, HYSTERESIS_MAX_C), (-1.0, 0.0), (4.0, 4.0)])
    def test_a_band_outside_the_daemons_range_is_clamped_at_load(self, raw, loaded):
        """The spin box can only show 0..max; the model, the card and the next save
        must agree with it rather than carry a value the daemon refuses."""
        curve = CurveConfig.from_dict(
            {"id": "c", "name": "C", "type": "graph", "hysteresis_c": raw}
        )
        assert curve.hysteresis_c == loaded
        assert curve.to_dict()["hysteresis_c"] == loaded

    @pytest.mark.parametrize("bad", [math.nan, math.inf, "wide", True])
    def test_a_non_finite_band_is_rejected_at_load(self, bad):
        with pytest.raises(ValueError, match="hysteresis_c"):
            CurveConfig.from_dict({"id": "c", "name": "C", "type": "graph", "hysteresis_c": bad})


class TestRules:
    def test_exactly_graph_stepped_and_linear_have_a_band(self):
        for ctype in CurveType:
            has = curve_uses_hysteresis(ctype)
            assert has == (ctype in {CurveType.GRAPH, CurveType.STEPPED, CurveType.LINEAR})
            # Every type without a band says why; every type with one says nothing.
            assert bool(not_applicable_reason(ctype)) is (not has), ctype

    def test_effective_band_is_the_default_when_absent(self):
        assert effective_hysteresis_c(_curve("c", CurveType.GRAPH)) == HYSTERESIS_DEFAULT_C
        assert effective_hysteresis_c(_curve("c", CurveType.GRAPH, 0.0)) == 0.0

    def test_apply_to_all_reaches_only_curves_with_a_band(self):
        profile = Profile(name="P")
        profile.curves = [
            _curve("g", CurveType.GRAPH),
            _curve("s", CurveType.STEPPED, 3.0),
            _curve("l", CurveType.LINEAR, 6.0),
            _curve("f", CurveType.FLAT),
            _curve("t", CurveType.TRIGGER),
            _curve("m", CurveType.MIX),
            _curve("y", CurveType.SYNC),
        ]
        assert apply_hysteresis_to_all(profile, 6.0) == ["g", "s"]
        bands = {c.id: c.hysteresis_c for c in profile.curves}
        assert bands == {"g": 6.0, "s": 6.0, "l": 6.0, "f": None, "t": None, "m": None, "y": None}
        assert apply_hysteresis_to_all(profile, 6.0) == [], "a repeat changes nothing"

    def test_card_meta_shows_only_a_band_the_user_set(self):
        assert curve_meta_text(_curve("c", CurveType.GRAPH), True) == "graph"
        assert curve_meta_text(_curve("c", CurveType.GRAPH, 5.0), True) == "graph · 5 °C band"
        assert (
            curve_meta_text(_curve("c", CurveType.LINEAR, 0.0), True)
            == "linear · no slow-down band"
        )
        assert curve_meta_text(_curve("c", CurveType.TRIGGER, 5.0), True) == "trigger"

    def test_card_meta_hides_a_band_the_daemon_does_not_apply(self):
        """An older daemon stores the field and runs its default, so the card must
        not claim the band there."""
        assert curve_meta_text(_curve("c", CurveType.GRAPH, 5.0), False) == "graph"


class TestCapability:
    def test_the_flag_parses_and_gates_the_feature(self):
        on = parse_capabilities({"control": {"curve_hysteresis": True}})
        assert daemon_supports("curve_hysteresis", on) is True
        # An older daemon omits the flag: it must not read as supported.
        off = parse_capabilities({"control": {}})
        assert daemon_supports("curve_hysteresis", off) is not True


# ─── Embedded point editor ────────────────────────────────────────────


class TestCurveEditor:
    def _editor(self, qtbot) -> CurveEditor:
        editor = CurveEditor()
        qtbot.addWidget(editor)
        return editor

    def test_row_hidden_until_the_daemon_supports_it(self, qtbot):
        editor = self._editor(qtbot)
        editor.set_curve(_curve("g", CurveType.GRAPH))
        assert not editor._hysteresis_row.isVisibleTo(editor)
        editor.set_hysteresis_supported(True)
        assert editor._hysteresis_row.isVisibleTo(editor)
        editor.set_hysteresis_supported(False)
        assert not editor._hysteresis_row.isVisibleTo(editor)

    def test_loading_a_curve_does_not_write_a_band(self, qtbot):
        editor = self._editor(qtbot)
        editor.set_hysteresis_supported(True)
        curve = _curve("g", CurveType.GRAPH)
        changes: list[None] = []
        editor.curve_changed.connect(lambda: changes.append(None))
        editor.set_curve(curve)
        assert editor._hysteresis_row.value() == HYSTERESIS_DEFAULT_C
        assert curve.hysteresis_c is None
        assert changes == []

    def test_a_user_edit_writes_the_band_and_signals(self, qtbot):
        editor = self._editor(qtbot)
        editor.set_hysteresis_supported(True)
        curve = _curve("g", CurveType.GRAPH)
        editor.set_curve(curve)
        with qtbot.waitSignal(editor.curve_changed, timeout=1000):
            editor._hysteresis_row._spin.setValue(4.5)
        assert curve.hysteresis_c == 4.5

    def test_zero_reads_as_off_and_the_maximum_is_the_daemons(self, qtbot):
        editor = self._editor(qtbot)
        spin = editor._hysteresis_row._spin
        assert spin.maximum() == HYSTERESIS_MAX_C
        spin.setValue(0.0)
        assert spin.text() == "Off"

    def test_apply_all_emits_the_current_band(self, qtbot):
        editor = self._editor(qtbot)
        editor.set_hysteresis_supported(True)
        editor.set_curve(_curve("g", CurveType.GRAPH, 7.0))
        with qtbot.waitSignal(editor.hysteresis_apply_all, timeout=1000) as blocker:
            editor._hysteresis_row._apply_btn.click()
        assert blocker.args == [7.0]

    def test_demo_mode_says_the_band_is_not_simulated(self, qtbot):
        editor = self._editor(qtbot)
        editor.set_curve(_curve("g", CurveType.GRAPH))
        editor.set_hysteresis_supported(True, demo=False)
        caption = editor._hysteresis_row._caption
        assert not caption.isVisibleTo(editor)
        editor.set_hysteresis_supported(True, demo=True)
        assert caption.isVisibleTo(editor)
        assert caption.text() == DEMO_NOTE


# ─── Curve dialog ─────────────────────────────────────────────────────


class TestCurveEditDialog:
    def _dialog(self, qtbot, curve, **kw) -> CurveEditDialog:
        dlg = CurveEditDialog(curve, [("cpu", "CPU")], **kw)
        qtbot.addWidget(dlg)
        return dlg

    def test_no_row_against_a_daemon_without_the_flag(self, qtbot):
        dlg = self._dialog(qtbot, _curve("l", CurveType.LINEAR))
        assert dlg._hysteresis_row is None
        assert dlg.hysteresis_apply_all() is None

    def test_untouched_linear_curve_keeps_its_absent_band(self, qtbot):
        curve = _curve("l", CurveType.LINEAR)
        dlg = self._dialog(qtbot, curve, hysteresis_supported=True)
        assert dlg._hysteresis_row is not None
        dlg.apply_to_curve()
        assert curve.hysteresis_c is None

    def test_an_edited_band_lands_on_save(self, qtbot):
        curve = _curve("l", CurveType.LINEAR)
        dlg = self._dialog(qtbot, curve, hysteresis_supported=True)
        dlg._hysteresis_row._spin.setValue(3.5)
        assert curve.hysteresis_c is None, "nothing lands before Save"
        dlg.apply_to_curve()
        assert curve.hysteresis_c == 3.5

    @pytest.mark.parametrize("ctype", [CurveType.FLAT, CurveType.TRIGGER, CurveType.SYNC])
    def test_types_without_a_band_show_it_disabled_with_the_reason(self, qtbot, ctype):
        curve = _curve("x", ctype)
        dlg = self._dialog(qtbot, curve, hysteresis_supported=True)
        row = dlg._hysteresis_row
        assert row is not None
        assert not row._spin.isEnabled()
        assert not row._apply_btn.isEnabled()
        assert row._caption.isVisibleTo(dlg)
        assert row._caption.text() == not_applicable_reason(ctype)
        dlg.apply_to_curve()
        assert curve.hysteresis_c is None
        assert dlg.hysteresis_apply_all() is None

    def test_apply_all_is_a_toggle_read_on_save(self, qtbot):
        dlg = self._dialog(qtbot, _curve("l", CurveType.LINEAR), hysteresis_supported=True)
        assert dlg.hysteresis_apply_all() is None
        dlg._hysteresis_row._spin.setValue(6.0)
        dlg._hysteresis_row._apply_btn.click()
        assert dlg.hysteresis_apply_all() == 6.0
        dlg._hysteresis_row._apply_btn.click()
        assert dlg.hysteresis_apply_all() is None


# ─── Controls page wiring ─────────────────────────────────────────────


def _page(qtbot, app_state, profile_service) -> ControlsPage:
    page = ControlsPage(state=app_state, profile_service=profile_service)
    qtbot.addWidget(page)
    return page


def _seed(page) -> Profile:
    profile = page._get_current_profile()
    profile.curves.extend(
        [
            _curve("g1", CurveType.GRAPH),
            _curve("g2", CurveType.STEPPED),
            _curve("l1", CurveType.LINEAR),
            _curve("t1", CurveType.TRIGGER),
            _curve("f1", CurveType.FLAT),
        ]
    )
    page._refresh_all()
    return profile


class TestControlsPage:
    def test_the_editor_row_follows_the_capability(self, qtbot, app_state, profile_service):
        page = _page(qtbot, app_state, profile_service)
        _seed(page)
        page._on_edit_curve("g1")
        row = page._curve_editor._hysteresis_row
        assert not row.isVisibleTo(page._curve_editor)
        # Capabilities can land after the editor opened.
        app_state.set_capabilities(SUPPORTED)
        assert row.isVisibleTo(page._curve_editor)
        app_state.set_capabilities(Capabilities(control=ControlCapability(autonomous_control=True)))
        assert not row.isVisibleTo(page._curve_editor)

    def test_the_card_shows_the_band_only_with_the_capability(
        self, qtbot, app_state, profile_service
    ):
        page = _page(qtbot, app_state, profile_service)
        profile = _seed(page)
        profile.get_curve("l1").hysteresis_c = 6.0
        page._refresh_all()
        label = page._curve_cards["l1"]._type_label
        assert label.text() == "linear"
        app_state.set_capabilities(SUPPORTED)
        assert label.text() == "linear · 6 °C band"
        app_state.set_capabilities(Capabilities(control=ControlCapability(autonomous_control=True)))
        assert label.text() == "linear"

    def test_editing_the_band_marks_the_profile_unsaved(self, qtbot, app_state, profile_service):
        app_state.set_capabilities(SUPPORTED)
        page = _page(qtbot, app_state, profile_service)
        profile = _seed(page)
        page._on_edit_curve("g1")
        page._set_unsaved(False)
        page._curve_editor._hysteresis_row._spin.setValue(5.0)
        assert profile.get_curve("g1").hysteresis_c == 5.0
        assert page._has_unsaved is True
        assert page._curve_cards["g1"]._type_label.text() == "graph · 5 °C band"

    def test_apply_all_from_the_editor(self, qtbot, app_state, profile_service):
        app_state.set_capabilities(SUPPORTED)
        page = _page(qtbot, app_state, profile_service)
        profile = _seed(page)
        page._on_edit_curve("g1")
        page._set_unsaved(False)
        page._curve_editor._hysteresis_row._spin.setValue(4.0)
        page._set_unsaved(False)
        page._curve_editor._hysteresis_row._apply_btn.click()
        bands = {
            c.id: c.hysteresis_c for c in profile.curves if c.id in {"g1", "g2", "l1", "t1", "f1"}
        }
        assert bands == {"g1": 4.0, "g2": 4.0, "l1": 4.0, "t1": None, "f1": None}
        assert page._has_unsaved is True
        # Only the changed cards repaint their meta line, and they do repaint.
        assert page._curve_cards["l1"]._type_label.text() == "linear · 4 °C band"
        assert page._curve_cards["t1"]._type_label.text() == "trigger"

    def test_apply_all_from_the_dialog(self, qtbot, app_state, profile_service, monkeypatch):
        app_state.set_capabilities(SUPPORTED)
        page = _page(qtbot, app_state, profile_service)
        profile = _seed(page)
        page._on_edit_curve("g1")  # the point editor is open on another curve

        def fake_exec(dialog):
            dialog._hysteresis_row._spin.setValue(8.0)
            dialog._hysteresis_row._apply_btn.click()
            return 1

        monkeypatch.setattr(CurveEditDialog, "exec", fake_exec, raising=True)
        page._on_edit_curve("l1")

        bands = {c.id: c.hysteresis_c for c in profile.curves if c.id in {"g1", "g2", "l1", "t1"}}
        assert bands == {"g1": 8.0, "g2": 8.0, "l1": 8.0, "t1": None}
        # The open point editor shows the band the dialog applied, not a stale one.
        assert page._curve_editor._hysteresis_row.value() == 8.0

    def test_the_dialog_is_ungated_without_the_flag(
        self, qtbot, app_state, profile_service, monkeypatch
    ):
        page = _page(qtbot, app_state, profile_service)
        _seed(page)
        built: list[CurveEditDialog] = []

        def fake_exec(dialog):
            built.append(dialog)
            return 0

        monkeypatch.setattr(CurveEditDialog, "exec", fake_exec, raising=True)
        page._on_edit_curve("l1")
        assert len(built) == 1
        assert built[0]._hysteresis_row is None

    def test_demo_mode_carries_the_note(self, qtbot, app_state, profile_service):
        app_state.set_mode(OperationMode.DEMO)
        app_state.set_capabilities(SUPPORTED)
        page = _page(qtbot, app_state, profile_service)
        _seed(page)
        page._on_edit_curve("g1")
        caption = page._curve_editor._hysteresis_row._caption
        assert caption.isVisibleTo(page._curve_editor)
        assert caption.text() == DEMO_NOTE
