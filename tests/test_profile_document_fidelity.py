"""A profile document survives the GUI unchanged where the GUI did not change it.

Batch 6 of the 2026-10-08 GUI↔daemon audit. Before it, a load-and-save — and so
every activation, which saves first — sent the daemon a different document from
the one it held:

* a curve of a type this build does not know was saved as a Flat 50 %;
* keys this build does not model were dropped at every level;
* an unknown control ``mode``, an explicit ``null`` ``mix_function`` or an extra
  key on a curve point failed or crashed instead of loading;
* a versionless document was read as v1 and had the chassis 20 % default baked
  into it, where the daemon reads it as the current schema;
* a repeated curve id resolved to the first curve in the GUI and the last in the
  daemon's engine.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from control_ofc.services.profile_service import (
    PROFILE_SCHEMA_VERSION,
    UNKNOWN_CURVE_TYPE_OUTPUT_PCT,
    ControlMember,
    ControlMode,
    CurveConfig,
    CurveType,
    Profile,
    ProfileService,
    control_minimum_pct,
    profile_schema_version,
    unlink_curve,
)

_FUTURE_CURVE = {
    "id": "c-future",
    "name": "Future",
    "type": "spline_v9",
    "sensor_id": "hwmon:k10temp:Tctl",
    "flat_output_pct": 12.0,
    "knots": [[30, 20], [70, 90]],
}


def _doc(**overrides) -> dict:
    doc = {
        "id": "p1",
        "name": "P1",
        "description": "",
        "version": PROFILE_SCHEMA_VERSION,
        "controls": [
            {
                "id": "ctl1",
                "name": "Case",
                "mode": "curve",
                "curve_id": "c-future",
                "members": [
                    {"source": "openfan", "member_id": "openfan:ch00", "member_label": "Front"}
                ],
                "minimum_pct": 20.0,
            }
        ],
        "curves": [copy.deepcopy(_FUTURE_CURVE)],
    }
    doc.update(overrides)
    return doc


class TestUnknownCurveType:
    def test_it_saves_as_the_document_it_came_from(self):
        saved = Profile.from_dict(_doc()).to_dict()["curves"][0]
        assert saved == _FUTURE_CURVE
        assert saved["type"] != CurveType.FLAT.value

    def test_it_shows_at_the_daemons_fallback_not_its_own_flat_figure(self):
        curve = Profile.from_dict(_doc()).curves[0]
        assert curve.is_unsupported and curve.unknown_type == "spline_v9"
        assert curve.type == CurveType.FLAT
        assert _FUTURE_CURVE["flat_output_pct"] != UNKNOWN_CURVE_TYPE_OUTPUT_PCT
        assert curve.interpolate(55.0) == UNKNOWN_CURVE_TYPE_OUTPUT_PCT

    def test_a_rename_or_new_id_is_kept_and_nothing_else_moves(self):
        curve = Profile.from_dict(_doc()).curves[0]
        curve.name = "Renamed"
        curve.id = "c-copy"
        assert curve.to_dict() == {**_FUTURE_CURVE, "name": "Renamed", "id": "c-copy"}

    def test_a_non_string_type_is_still_refused(self):
        with pytest.raises(ValueError, match="curve type"):
            CurveConfig.from_dict({**_FUTURE_CURVE, "type": 3})

    def test_activation_hands_the_daemon_the_unknown_curve_unchanged(self, tmp_path):
        """The wire end: activate() saves first, and the saved document is
        what the daemon receives."""
        client = MagicMock()
        client.create_profile.return_value = {"created": "p1"}
        client.activate_profile.return_value = MagicMock(activated=True)
        ps = ProfileService(client=client)
        profile = Profile.from_dict(_doc())
        ps._profiles[profile.id] = profile

        outcome = ps.activate(profile.id, client=client)

        assert outcome.activated
        (document,), _ = client.create_profile.call_args
        assert document["curves"][0] == _FUTURE_CURVE


class TestUnknownKeysSurvive:
    def test_every_level_keeps_what_it_does_not_model(self):
        doc = _doc(future_profile_key={"a": 1})
        doc["controls"][0]["future_control_key"] = True
        doc["controls"][0]["members"][0]["future_member_key"] = "m"
        doc["curves"].append(
            {
                "id": "c-graph",
                "name": "G",
                "type": "graph",
                "sensor_id": "s",
                "points": [{"temp_c": 30, "output_pct": 20, "weight": 0.5}],
                "future_curve_key": [1, 2],
            }
        )

        saved = Profile.from_dict(doc).to_dict()

        assert saved["future_profile_key"] == {"a": 1}
        assert saved["controls"][0]["future_control_key"] is True
        assert saved["controls"][0]["members"][0]["future_member_key"] == "m"
        graph = saved["curves"][1]
        assert graph["future_curve_key"] == [1, 2]
        assert graph["points"] == [{"temp_c": 30, "output_pct": 20, "weight": 0.5}]

    def test_a_modelled_key_wins_over_a_stale_extra(self):
        member = ControlMember.from_dict({"source": "openfan", "member_id": "openfan:ch01"})
        member.extra_fields["member_id"] = "stale"  # cannot arise from from_dict
        assert member.to_dict()["member_id"] == "openfan:ch01"


class TestTolerantFields:
    def test_an_unknown_mode_loads_as_curve_and_is_written_back(self):
        doc = _doc()
        doc["controls"][0]["mode"] = "adaptive"
        profile = Profile.from_dict(doc)
        control = profile.controls[0]
        assert control.mode == ControlMode.CURVE
        assert profile.to_dict()["controls"][0]["mode"] == "adaptive"

    def test_a_mode_the_user_chooses_replaces_the_unknown_one(self):
        doc = _doc()
        doc["controls"][0]["mode"] = "adaptive"
        profile = Profile.from_dict(doc)
        assert unlink_curve(profile, "c-future")
        control = profile.controls[0]
        control.mode = ControlMode.CURVE  # and back again
        assert control.to_dict()["mode"] == ControlMode.CURVE.value

    def test_a_null_mix_function_is_the_daemons_default(self):
        curve = CurveConfig.from_dict(
            {
                "id": "m",
                "name": "M",
                "type": "mix",
                "mix_function": None,
                "mix_curve_ids": None,
                "sync_control_id": None,
            }
        )
        assert curve.mix_function == "max"
        assert curve.mix_curve_ids == []
        assert curve.sync_control_id == ""

    def test_a_null_mix_function_renders_on_the_curve_card(self, qtbot):
        from control_ofc.ui.widgets.curve_card import CurveCard

        curve = CurveConfig.from_dict({"id": "m", "name": "M", "type": "mix", "mix_function": None})
        card = CurveCard(curve)
        qtbot.addWidget(card)
        assert card._preview.summary_text() == "Max of 0 curves"


class TestSchemaVersion:
    def _chassis_at_zero(self, **overrides) -> dict:
        doc = _doc(**overrides)
        doc["controls"][0]["minimum_pct"] = 0.0
        return doc

    def test_a_versionless_document_is_the_current_schema_as_the_daemon_reads_it(self):
        doc = self._chassis_at_zero()
        del doc["version"]
        control = Profile.from_dict(doc).controls[0]
        assert control_minimum_pct(control.members) > 0  # the v4 pass would raise it
        assert control.minimum_pct == 0.0

    def test_an_older_numbered_document_still_gets_the_v4_floor(self):
        control = Profile.from_dict(self._chassis_at_zero(version=3)).controls[0]
        assert control.minimum_pct == control_minimum_pct(control.members)

    def test_a_versionless_pump_still_gets_the_enforced_floor(self):
        doc = _doc()
        del doc["version"]
        doc["controls"][0]["members"] = [
            {"source": "hwmon", "member_id": "hwmon:nct6798:pwm2", "member_label": "AIO Pump"}
        ]
        doc["controls"][0]["minimum_pct"] = 0.0
        control = Profile.from_dict(doc).controls[0]
        floor = control_minimum_pct(control.members)
        assert floor > 0
        assert control.minimum_pct == floor

    def test_the_v1_shape_is_still_recognised_without_a_number(self):
        assert profile_schema_version({"assignments": []}) == 1

    def test_a_newer_document_keeps_its_number(self):
        assert Profile.from_dict(_doc(version=PROFILE_SCHEMA_VERSION + 2)).to_dict()["version"] == (
            PROFILE_SCHEMA_VERSION + 2
        )

    @pytest.mark.parametrize("bad", ["7", 7.0, True])
    def test_a_non_integer_version_is_refused(self, bad):
        with pytest.raises(ValueError, match="version"):
            Profile.from_dict(_doc(version=bad))


class TestDuplicateCurveIds:
    def test_a_repeat_is_re_identified_and_references_stay_on_the_first(self):
        first = {"id": "dup", "name": "First", "type": "flat", "flat_output_pct": 30.0}
        second = {"id": "dup", "name": "Second", "type": "flat", "flat_output_pct": 90.0}
        profile = Profile.from_dict(_doc(curves=[first, second]))

        ids = [c.id for c in profile.curves]
        assert len(set(ids)) == 2
        assert profile.get_curve("dup").name == "First"
        saved = profile.to_dict()
        assert [c["id"] for c in saved["curves"]].count("dup") == 1
        assert saved["controls"][0]["curve_id"] == "c-future"  # untouched reference


class TestUnsupportedCurveInTheUi:
    def test_edit_explains_and_opens_no_editor(self, qtbot, app_state, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        from control_ofc.ui.pages import controls_page as cp_mod

        ps = ProfileService(client=None)
        profile = Profile.from_dict(_doc())
        ps._profiles[profile.id] = profile
        ps.set_active(profile.id)
        page = cp_mod.ControlsPage(state=app_state, profile_service=ps, client=None)
        qtbot.addWidget(page)
        told: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "information", lambda _p, _t, text, *a, **k: told.append(text)
        )
        opened: list[object] = []
        monkeypatch.setattr(page._curve_editor, "set_curve", lambda c: opened.append(c))

        page._on_edit_curve("c-future")

        assert len(told) == 1 and "spline_v9" in told[0]
        assert opened == []
        assert page._get_current_profile().to_dict()["curves"][0] == _FUTURE_CURVE

    def test_the_card_names_the_type_instead_of_a_flat_figure(self, qtbot):
        from control_ofc.ui.widgets.curve_card import CurveCard

        card = CurveCard(Profile.from_dict(_doc()).curves[0])
        qtbot.addWidget(card)
        assert "spline_v9" in card._type_label.text()
        assert "spline_v9" in card._preview.summary_text()
        assert "Flat" not in card._preview.summary_text()


class TestLoadPersistsTheNewId:
    def test_the_repeat_keeps_one_new_id_across_loads(self):
        import json

        from control_ofc.paths import profiles_dir

        first = {"id": "dup", "name": "First", "type": "flat", "flat_output_pct": 30.0}
        second = {"id": "dup", "name": "Second", "type": "flat", "flat_output_pct": 90.0}
        profiles_dir().mkdir(parents=True, exist_ok=True)
        path = profiles_dir() / "p1.json"
        path.write_text(json.dumps(_doc(curves=[first, second])))

        ProfileService(client=None).load()
        on_disk = [c["id"] for c in json.loads(path.read_text())["curves"]]
        assert len(set(on_disk)) == 2

        again = ProfileService(client=None)
        again.load()
        assert [c.id for c in again.get_profile("p1").curves] == on_disk


# ---------------------------------------------------------------------------
# Contract review remediation (batch 6)
# ---------------------------------------------------------------------------


class TestReviewRemediation:
    def test_the_new_id_is_derived_not_random(self):
        first = {"id": "dup", "name": "A", "type": "flat"}
        second = {"id": "dup", "name": "B", "type": "flat"}
        taken = {"id": "dup-2", "name": "C", "type": "flat"}
        ids = [
            [c.id for c in Profile.from_dict(_doc(curves=[first, second, taken])).curves]
            for _ in range(2)
        ]
        assert ids[0] == ids[1] == ["dup", "dup-3", "dup-2"]

    def test_a_daemon_profile_that_repeats_a_curve_id_is_not_published(self):
        first = {"id": "dup", "name": "A", "type": "flat"}
        second = {"id": "dup", "name": "B", "type": "flat"}
        clean = _doc(id="p2", curves=[copy.deepcopy(_FUTURE_CURVE)])
        stored = {"p1": _doc(curves=[first, second]), "p2": clean}
        client = MagicMock()
        client.list_profiles.return_value = [{"id": k} for k in stored]
        client.get_profile.side_effect = lambda ident: copy.deepcopy(stored[ident])
        ps = ProfileService(client=client)

        ps.load()

        assert ps.is_published("p2")  # the opposite branch
        assert not ps.is_published("p1")

    @pytest.mark.parametrize("bad", [None, 0, False, [], {}])
    def test_a_falsy_non_string_mode_is_refused(self, bad):
        doc = _doc()
        doc["controls"][0]["mode"] = bad
        with pytest.raises(ValueError, match="mode"):
            Profile.from_dict(doc)

    def test_an_empty_mode_is_written_back_as_it_came(self):
        doc = _doc()
        doc["controls"][0]["mode"] = ""
        profile = Profile.from_dict(doc)
        assert profile.controls[0].mode == ControlMode.CURVE
        assert profile.to_dict()["controls"][0]["mode"] == ""

    def test_an_unknown_type_reads_its_sensor(self):
        curve = Profile.from_dict(_doc()).curves[0]
        assert curve.type == CurveType.FLAT  # shown as flat …
        assert curve.reads_sensor  # … but the daemon reads its sensor
        assert not CurveConfig.from_dict({"id": "f", "name": "F", "type": "flat"}).reads_sensor

    @pytest.mark.parametrize(
        ("change", "kept"),
        [({}, "adaptive"), ({"curve_id": "other"}, ControlMode.CURVE.value)],
        ids=["name-only", "new-curve"],
    )
    def test_the_role_dialog_drops_the_unknown_mode_only_on_a_new_choice(
        self, qtbot, app_state, monkeypatch, change, kept
    ):
        from control_ofc.ui.pages import controls_page as cp_mod
        from control_ofc.ui.widgets import fan_role_dialog

        doc = _doc()
        doc["controls"][0]["mode"] = "adaptive"
        ps = ProfileService(client=None)
        profile = Profile.from_dict(doc)
        ps._profiles[profile.id] = profile
        ps.set_active(profile.id)
        page = cp_mod.ControlsPage(state=app_state, profile_service=ps, client=None)
        qtbot.addWidget(page)
        control = profile.controls[0]
        result = {
            "name": "Renamed",
            "mode": ControlMode.CURVE,
            "curve_id": control.curve_id,
            "manual_output_pct": control.manual_output_pct,
            "gpu_fan_zero_rpm": {},
            "delete": False,
            **change,
        }

        class _FakeRoleDialog:
            def __init__(self, *args, **kwargs):
                pass

            def set_edit_members_callback(self, _cb):
                pass

            def exec(self):
                return 1

            def get_result(self):
                return result

        monkeypatch.setattr(fan_role_dialog, "FanRoleDialog", _FakeRoleDialog)

        page._on_edit_role(control.id)

        assert control.name == "Renamed"
        assert control.to_dict()["mode"] == kept
