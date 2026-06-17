"""Tests for the one-time local→daemon profile import (DEC-161, migration P2).

Covers the four Qt-free layers (capabilities parse, local collector, upload
orchestration, offer gate) plus the thin UI wiring (main-window auto-offer gate
and the Settings-page manual import), asserting outcomes — uploads dispatched,
report grouping, and persisted-flag mutation — not just that buttons exist.
"""

from __future__ import annotations

import json

import pytest

from control_ofc.api.errors import DaemonError, DaemonUnavailable
from control_ofc.api.models import Capabilities, ControlCapability, parse_capabilities
from control_ofc.services.app_settings_service import MACHINE_SPECIFIC_KEYS, AppSettings
from control_ofc.services.profile_import_service import import_profiles, should_offer_import
from control_ofc.services.profile_service import (
    ImportCandidate,
    ImportCollection,
    Profile,
    collect_local_profiles_for_import,
)
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.pages.settings_page import SettingsPage

# --- helpers ---------------------------------------------------------------


class _FakeClient:
    """Minimal stand-in for DaemonClient exposing only ``create_profile``.

    ``responder(document)`` returns the success body or raises a DaemonError to
    drive the various import outcomes.
    """

    def __init__(self, responder):
        self.calls: list[dict] = []
        self._responder = responder

    def create_profile(self, document: dict) -> dict:
        self.calls.append(dict(document))
        return self._responder(document)


def _candidate(pid: str = "p1", name: str = "P1") -> ImportCandidate:
    doc = {"id": pid, "name": name, "version": 7, "controls": [], "curves": []}
    return ImportCandidate(source_path=f"/tmp/{pid}.json", profile_id=pid, name=name, document=doc)


def _caps(storage: bool) -> Capabilities:
    return Capabilities(control=ControlCapability(profile_storage=storage))


def _write_json(directory, name: str, obj) -> object:
    path = directory / name
    path.write_text(json.dumps(obj) + "\n")
    return path


# --- 1. capabilities parse (control block) ---------------------------------


def test_parse_capabilities_control_block():
    caps = parse_capabilities(
        {"control": {"profile_storage": True, "curve_evaluation": True, "manual_override": False}}
    )
    assert caps.control.profile_storage is True
    assert caps.control.curve_evaluation is True
    assert caps.control.manual_override is False


def test_parse_capabilities_control_absent_defaults_safe():
    caps = parse_capabilities({"daemon_version": "1.18.0"})
    assert caps.control.profile_storage is False


def test_parse_capabilities_control_forward_compatible():
    # An unknown future field in the control block must not break parsing.
    caps = parse_capabilities({"control": {"profile_storage": True, "future_flag": "x"}})
    assert caps.control.profile_storage is True


# --- 2. local collector ----------------------------------------------------


def test_collect_migrates_to_current_schema(tmp_path):
    d = tmp_path / "profiles"
    d.mkdir()
    prof = Profile(id="ab12cd34", name="Quiet").to_dict()
    prof["version"] = 4  # downgraded on disk
    _write_json(d, "ab12cd34.json", prof)

    coll = collect_local_profiles_for_import(directory=d)

    assert not coll.failed
    assert len(coll.ready) == 1
    cand = coll.ready[0]
    assert cand.profile_id == "ab12cd34"
    assert cand.name == "Quiet"
    assert cand.document["version"] == 7  # re-migrated to current schema


def test_collect_migrates_v1_profile(tmp_path):
    d = tmp_path / "profiles"
    d.mkdir()
    v1 = {
        "id": "old",
        "name": "Old Profile",
        "version": 1,
        "assignments": [
            {
                "target_id": "openfan:ch00",
                "target_type": "fan",
                "sensor_id": "cpu_temp",
                "curve": {
                    "sensor_id": "cpu_temp",
                    "points": [
                        {"temp_c": 30.0, "output_pct": 20.0},
                        {"temp_c": 70.0, "output_pct": 80.0},
                    ],
                },
                "enabled": True,
            }
        ],
    }
    _write_json(d, "old.json", v1)

    coll = collect_local_profiles_for_import(directory=d)

    assert len(coll.ready) == 1
    assert coll.ready[0].document["version"] == 7
    assert coll.ready[0].document["controls"]  # migration produced a control


def test_collect_quarantines_corrupt_json(tmp_path):
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "bad.json").write_text("{not valid json")

    coll = collect_local_profiles_for_import(directory=d)

    assert not coll.ready
    assert len(coll.failed) == 1
    assert coll.failed[0][0].endswith("bad.json")


def test_collect_quarantines_non_dict_json(tmp_path):
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "list.json").write_text("[1, 2, 3]")

    coll = collect_local_profiles_for_import(directory=d)

    assert not coll.ready
    assert len(coll.failed) == 1


def test_collect_leaves_originals_untouched(tmp_path):
    d = tmp_path / "profiles"
    d.mkdir()
    prof = Profile(id="keepme12", name="Keep").to_dict()
    prof["version"] = 5  # would be migrated on a ProfileService.load(), but...
    path = _write_json(d, "keepme12.json", prof)
    before = path.read_bytes()

    collect_local_profiles_for_import(directory=d)

    assert path.read_bytes() == before  # collector only reads — never re-saves


def test_collect_missing_dir_is_empty(tmp_path):
    coll = collect_local_profiles_for_import(directory=tmp_path / "nope")
    assert coll.is_empty


# --- 3. upload orchestration -----------------------------------------------


def test_import_all_succeed():
    coll = ImportCollection(ready=[_candidate("a"), _candidate("b")])
    client = _FakeClient(lambda doc: {"profile_id": doc["id"], "created": True, "warnings": []})

    report = import_profiles(client, coll)

    assert len(report.imported) == 2
    assert not report.skipped and not report.quarantined
    assert len(client.calls) == 2


def test_import_conflict_skips_by_default():
    coll = ImportCollection(ready=[_candidate("dup")])

    def responder(doc):
        raise DaemonError(code="already_exists", message="exists", status=409)

    report = import_profiles(_FakeClient(responder), coll, on_conflict="skip")

    assert not report.imported
    assert len(report.skipped) == 1
    assert report.skipped[0].profile_id == "dup"


def test_import_conflict_rename_imports_copy():
    coll = ImportCollection(ready=[_candidate("dup", name="Quiet")])

    def responder(doc):
        if doc["id"] == "dup":
            raise DaemonError(code="already_exists", message="exists", status=409)
        return {"profile_id": doc["id"], "created": True}

    client = _FakeClient(responder)
    report = import_profiles(client, coll, on_conflict="rename")

    assert len(report.imported) == 1
    assert not report.skipped
    out = report.imported[0]
    assert out.profile_id != "dup"  # minted a fresh id
    assert out.name == "Quiet (imported)"
    # The retried upload carried the renamed id + name.
    assert client.calls[1]["id"] != "dup"
    assert client.calls[1]["name"] == "Quiet (imported)"


def test_import_validation_error_quarantines_with_summary():
    coll = ImportCollection(ready=[_candidate("bad")])
    details = {
        "field_violations": [
            {
                "field": "curves[0].points[0].output_pct",
                "reason": "OUT_OF_RANGE",
                "description": "pwm out of range",
            }
        ]
    }

    def responder(doc):
        raise DaemonError(code="validation_error", message="invalid", status=400, details=details)

    report = import_profiles(_FakeClient(responder), coll)

    assert not report.imported
    assert len(report.quarantined) == 1
    reason = report.quarantined[0].reason
    assert "output_pct" in reason
    assert "pwm out of range" in reason


def test_import_other_daemon_error_quarantines():
    coll = ImportCollection(ready=[_candidate("x")])

    def responder(doc):
        raise DaemonError(code="internal_error", message="boom", status=500)

    report = import_profiles(_FakeClient(responder), coll)

    assert len(report.quarantined) == 1
    assert "boom" in report.quarantined[0].reason


def test_import_transport_error_aborts_batch():
    coll = ImportCollection(ready=[_candidate("x"), _candidate("y")])

    def responder(doc):
        raise DaemonUnavailable()

    with pytest.raises(DaemonUnavailable):
        import_profiles(_FakeClient(responder), coll)


def test_import_preupload_failures_quarantined():
    coll = ImportCollection(ready=[_candidate("ok")], failed=[("/tmp/bad.json", "not json")])
    client = _FakeClient(lambda doc: {"profile_id": doc["id"], "created": True})

    report = import_profiles(client, coll)

    assert len(report.imported) == 1
    assert len(report.quarantined) == 1
    assert report.quarantined[0].source_path == "/tmp/bad.json"
    assert report.quarantined[0].reason == "not json"


def test_import_success_warnings_noted():
    coll = ImportCollection(ready=[_candidate("w")])
    client = _FakeClient(
        lambda doc: {"profile_id": doc["id"], "created": True, "warnings": [{"x": 1}]}
    )

    report = import_profiles(client, coll)

    assert "1 warning" in report.imported[0].reason


# --- 4. offer gate ---------------------------------------------------------


def test_should_offer_when_all_conditions_met():
    assert should_offer_import(_caps(True), AppSettings(), has_local_profiles=True, demo=False)


def test_should_not_offer_in_demo():
    assert not should_offer_import(_caps(True), AppSettings(), has_local_profiles=True, demo=True)


def test_should_not_offer_when_already_prompted():
    s = AppSettings(daemon_import_prompted=True)
    assert not should_offer_import(_caps(True), s, has_local_profiles=True, demo=False)


def test_should_not_offer_without_storage_capability():
    assert not should_offer_import(_caps(False), AppSettings(), has_local_profiles=True, demo=False)


def test_should_not_offer_without_local_profiles():
    assert not should_offer_import(_caps(True), AppSettings(), has_local_profiles=False, demo=False)


def test_should_not_offer_when_caps_none():
    assert not should_offer_import(None, AppSettings(), has_local_profiles=True, demo=False)


# --- 5. settings flag ------------------------------------------------------


def test_daemon_import_prompted_roundtrip():
    s = AppSettings(daemon_import_prompted=True)
    assert AppSettings.from_dict(s.to_dict()).daemon_import_prompted is True


def test_daemon_import_prompted_default_false():
    assert AppSettings().daemon_import_prompted is False
    assert AppSettings.from_dict({}).daemon_import_prompted is False


def test_daemon_import_prompted_excluded_from_portable_export():
    s = AppSettings(daemon_import_prompted=True)
    assert "daemon_import_prompted" in MACHINE_SPECIFIC_KEYS
    assert "daemon_import_prompted" not in s.portable_dict()


def test_daemon_import_prompted_coerces_bad_value():
    # Untrusted non-bool falls back to the default (DEC-137 trust boundary).
    assert AppSettings.from_dict({"daemon_import_prompted": "yes"}).daemon_import_prompted is False


# --- 6. main-window auto-offer gate ----------------------------------------


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


def test_main_window_offers_import_once_when_capable(window, profile_service, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(
        window.settings_page, "run_profile_import", lambda *, auto: calls.append(auto)
    )
    assert profile_service.profiles  # fixture loaded defaults → local profiles exist

    window._state.set_capabilities(_caps(True))
    assert calls == [True]

    # A second capabilities emission must not re-open the offer (session guard).
    window._state.set_capabilities(_caps(True))
    assert calls == [True]


def test_main_window_no_offer_without_capability(window, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(
        window.settings_page, "run_profile_import", lambda *, auto: calls.append(auto)
    )
    window._state.set_capabilities(_caps(False))
    assert calls == []


# --- 7. settings-page manual import ----------------------------------------


def test_settings_page_manual_import_uploads_and_marks_prompted(
    qtbot, app_state, settings_service, monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from control_ofc.paths import profiles_dir

    d = profiles_dir()
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d, "seed1234.json", Profile(id="seed1234", name="Seed").to_dict())

    app_state.set_capabilities(_caps(True))
    client = _FakeClient(lambda doc: {"profile_id": doc["id"], "created": True})
    page = SettingsPage(state=app_state, settings_service=settings_service, client=client)
    qtbot.addWidget(page)

    page.run_profile_import(auto=False)

    assert any(c["id"] == "seed1234" for c in client.calls)
    assert settings_service.settings.daemon_import_prompted is True


def test_settings_page_manual_import_noop_without_storage_capability(
    qtbot, app_state, settings_service
):
    app_state.set_capabilities(_caps(False))
    client = _FakeClient(lambda doc: {"profile_id": doc["id"], "created": True})
    page = SettingsPage(state=app_state, settings_service=settings_service, client=client)
    qtbot.addWidget(page)

    page.run_profile_import(auto=False)

    assert client.calls == []
    # Declined-by-capability must not consume the one-time auto-offer.
    assert settings_service.settings.daemon_import_prompted is False
