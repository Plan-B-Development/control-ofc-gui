"""Daemon-backed persistence for ProfileService (control-migration Phase 6b).

These exercise the ``client``-injected mode: load pulls from the daemon store
and mirrors to the local cache, save validates-then-uploads with an offline
local-draft fallback (no auto-sync), and delete propagates to the daemon
(refusing an in-use profile). Pure-local mode (``client=None``) stays
byte-for-byte the pre-migration behaviour and is covered by
``test_profile_service.py``; one test here pins that it makes no daemon calls.
"""

from __future__ import annotations

import json

import pytest

from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
from control_ofc.paths import profiles_dir
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    CurveConfig,
    CurvePoint,
    CurveType,
    LogicalControl,
    Profile,
    ProfileService,
    default_profiles,
)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Isolate the GUI config tree (and thus ``profiles_dir()``) per test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


class FakeDaemonClient:
    """In-memory stand-in for the profile-CRUD slice of ``DaemonClient``.

    Records every call and can be made to raise per-method (to simulate an
    offline daemon, a timeout, or a daemon-side rejection).
    """

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.calls: list[tuple[str, str | None]] = []
        self.raise_on: dict[str, Exception] = {}
        # Per-id fetch failures for get_profile (id -> exception). Lets a test
        # fail one profile's hydration while the rest still succeed (DEC-175).
        self.fail_get: dict[str, Exception] = {}

    def _maybe(self, name: str) -> None:
        exc = self.raise_on.get(name)
        if exc is not None:
            raise exc

    def list_profiles(self) -> list[dict]:
        self.calls.append(("list", None))
        self._maybe("list_profiles")
        # The real daemon returns lightweight summaries (DEC-160) — id/name/
        # description only, never the controls or curves. Mirroring that here
        # is what forces load() to hydrate per id (DEC-175); a fake that
        # returned full documents would hide the bug it now guards against.
        return [
            {"id": d["id"], "name": d.get("name", ""), "description": d.get("description", "")}
            for d in self.store.values()
        ]

    def get_profile(self, profile_id: str) -> dict:
        self.calls.append(("get", profile_id))
        self._maybe("get_profile")
        exc = self.fail_get.get(profile_id)
        if exc is not None:
            raise exc
        try:
            return dict(self.store[profile_id])
        except KeyError:
            raise DaemonError(
                code="validation_error",
                message=f"profile '{profile_id}' not found",
                status=404,
            ) from None

    def create_profile(self, document: dict) -> dict:
        self.calls.append(("create", document.get("id")))
        self._maybe("create_profile")
        pid = document["id"]
        if pid in self.store:
            raise DaemonError(code="already_exists", message="profile exists", status=409)
        self.store[pid] = dict(document)
        return {"created": pid}

    def update_profile(self, profile_id: str, document: dict) -> dict:
        self.calls.append(("update", profile_id))
        self._maybe("update_profile")
        self.store[profile_id] = dict(document)
        return {"updated": profile_id}

    def delete_profile(self, profile_id: str) -> dict:
        self.calls.append(("delete", profile_id))
        self._maybe("delete_profile")
        self.store.pop(profile_id, None)
        return {"deleted": profile_id}


def _seed(fake: FakeDaemonClient, *profiles: Profile) -> None:
    for p in profiles:
        fake.store[p.id] = p.to_dict()


def _names(fake: FakeDaemonClient) -> list[str]:
    return [c[0] for c in fake.calls]


def _ids(svc: ProfileService) -> set[str]:
    return {p.id for p in svc.profiles}


def _rich_profile(name: str = "Rich") -> Profile:
    """A profile carrying a real control + curve.

    The whole point of DEC-175 is that controls and curves survive a daemon
    round-trip; a bare ``Profile(name=...)`` has neither, so it can't prove
    hydration is lossless. This one does.
    """
    p = Profile(name=name)
    p.curves.append(
        CurveConfig(
            id="c1",
            name="CPU Fan",
            type=CurveType.GRAPH,
            sensor_id="cpu_temp",
            points=[CurvePoint(30, 20), CurvePoint(55, 60), CurvePoint(80, 100)],
        )
    )
    p.controls.append(
        LogicalControl(
            id="ctrl1",
            name="Chassis",
            mode=ControlMode.CURVE,
            curve_id="c1",
            members=[
                ControlMember(source="openfan", member_id="openfan:ch00", member_label="Fan 1")
            ],
        )
    )
    return p


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


def test_load_pulls_from_daemon_and_mirrors(cfg):
    fake = FakeDaemonClient()
    p = Profile(name="Daemon One")
    _seed(fake, p)
    svc = ProfileService(client=fake)

    errors = svc.load()

    assert errors == []
    assert "list" in _names(fake)
    assert p.id in _ids(svc)
    assert svc.offline is False
    assert svc.is_published(p.id)
    # Mirrored into the local cache so it is editable offline.
    assert (profiles_dir() / f"{p.id}.json").exists()


def test_load_falls_back_to_local_when_daemon_unreachable(cfg):
    # A previously-mirrored profile already sits in the local cache.
    cached = Profile(name="Cached")
    profiles_dir().mkdir(parents=True, exist_ok=True)
    (profiles_dir() / f"{cached.id}.json").write_text(json.dumps(cached.to_dict()))

    fake = FakeDaemonClient()
    fake.raise_on["list_profiles"] = DaemonUnavailable()
    svc = ProfileService(client=fake)

    errors = svc.load()

    assert errors == []
    assert svc.offline is True
    assert cached.id in _ids(svc)


def test_load_empty_daemon_seeds_and_publishes_defaults(cfg):
    fake = FakeDaemonClient()  # daemon store starts empty (fresh install)
    svc = ProfileService(client=fake)

    errors = svc.load()

    assert errors == []
    assert len(svc.profiles) == len(default_profiles())
    # Seeded starters are published to the daemon, not left local-only.
    assert len(fake.store) == len(default_profiles())
    assert _names(fake).count("create") == len(default_profiles())
    assert svc.unpublished_ids == set()


def test_load_reports_malformed_daemon_document(cfg):
    fake = FakeDaemonClient()
    good = Profile(name="Good")
    _seed(fake, good)
    fake.store["broken"] = {"id": "broken", "curves": [{"points": "not-a-list"}]}
    svc = ProfileService(client=fake)

    errors = svc.load()

    assert any(ident == "broken" for ident, _ in errors)
    assert good.id in _ids(svc)  # the good one still loads
    assert svc.offline is False


def test_load_hydrates_full_profile_documents(cfg):
    # Regression for DEC-175: GET /profiles returns id/name/description summaries
    # with no controls or curves; load() must hydrate each via GET /profiles/{id}.
    # Before the fix the summary was parsed directly, so every fan role (control)
    # and curve was silently dropped on restart.
    fake = FakeDaemonClient()
    rich = _rich_profile()
    _seed(fake, rich)
    svc = ProfileService(client=fake)

    errors = svc.load()

    assert errors == []
    # The listing was hydrated per id, not parsed in place.
    assert ("get", rich.id) in fake.calls
    loaded = svc.get_profile(rich.id)
    assert loaded is not None
    assert len(loaded.controls) == 1  # would be 0 with the summary-only bug
    assert len(loaded.curves) == 1
    assert loaded.controls[0].curve_id == "c1"
    assert loaded.controls[0].members[0].member_id == "openfan:ch00"
    assert len(loaded.curves[0].points) == 3
    # The lossless document — not the stripped summary — is mirrored locally.
    mirror = json.loads((profiles_dir() / f"{rich.id}.json").read_text())
    assert len(mirror["controls"]) == 1
    assert len(mirror["curves"]) == 1


def test_load_skips_unfetchable_profile_and_preserves_its_mirror(cfg):
    # DEC-175: if one profile fails to hydrate (e.g. deleted between the listing
    # and the fetch → 404), the rest still load, the failure is reported, and
    # that profile's existing local mirror is left intact (not clobbered by the
    # failed fetch).
    good = _rich_profile("Good")
    stale = _rich_profile("Stale")
    fake = FakeDaemonClient()
    _seed(fake, good, stale)

    # A previously-good local mirror of `stale` already sits on disk.
    profiles_dir().mkdir(parents=True, exist_ok=True)
    mirror_path = profiles_dir() / f"{stale.id}.json"
    mirror_path.write_text(json.dumps(stale.to_dict(), indent=2))

    # The daemon still lists `stale` but now 404s its fetch (removed under us).
    fake.fail_get[stale.id] = DaemonError(
        code="validation_error", message=f"profile '{stale.id}' not found", status=404
    )
    svc = ProfileService(client=fake)

    errors = svc.load()

    assert svc.offline is False  # daemon was reachable — this is not an outage
    assert good.id in _ids(svc)  # the healthy profile still loaded
    assert stale.id not in _ids(svc)  # the 404'd one was skipped
    assert any(ident == stale.id for ident, _ in errors)
    # Its existing mirror was not overwritten by the failed hydration.
    assert mirror_path.exists()
    preserved = json.loads(mirror_path.read_text())
    assert len(preserved["controls"]) == 1


def test_load_disconnect_mid_hydration_falls_back_to_local(cfg):
    # DEC-175: a daemon that drops *after* listing but *during* hydration must
    # abandon the partial set and fall back to the local cache (offline), rather
    # than committing an arbitrary subset of profiles.
    cached = _rich_profile("Cached")
    profiles_dir().mkdir(parents=True, exist_ok=True)
    (profiles_dir() / f"{cached.id}.json").write_text(json.dumps(cached.to_dict()))

    fake = FakeDaemonClient()
    _seed(fake, cached)  # the daemon lists it...
    fake.raise_on["get_profile"] = DaemonUnavailable()  # ...but dies on the fetch
    svc = ProfileService(client=fake)

    errors = svc.load()

    assert errors == []
    assert svc.offline is True
    # Present from the local cache, controls intact.
    loaded = svc.get_profile(cached.id)
    assert loaded is not None
    assert len(loaded.controls) == 1


# ---------------------------------------------------------------------------
# save_profile()
# ---------------------------------------------------------------------------


def test_save_new_profile_creates_on_daemon(cfg):
    fake = FakeDaemonClient()
    svc = ProfileService(client=fake)
    p = Profile(name="New")

    svc.save_profile(p)

    assert ("create", p.id) in fake.calls
    assert p.id in fake.store
    assert svc.is_published(p.id)
    assert p.id not in svc.unpublished_ids
    assert (profiles_dir() / f"{p.id}.json").exists()


def test_save_known_daemon_id_updates(cfg):
    fake = FakeDaemonClient()
    p = Profile(name="Existing")
    _seed(fake, p)
    svc = ProfileService(client=fake)
    svc.load()  # learns p.id is a daemon id
    fake.calls.clear()

    p.name = "Edited"
    svc.save_profile(p)

    assert ("update", p.id) in fake.calls
    assert ("create", p.id) not in fake.calls
    assert fake.store[p.id]["name"] == "Edited"


def test_save_create_conflict_falls_back_to_update(cfg):
    # Daemon already has the id (e.g. imported via DEC-161) but this session
    # never loaded it, so it is not yet a known daemon id.
    fake = FakeDaemonClient()
    p = Profile(name="Imported")
    _seed(fake, p)
    svc = ProfileService(client=fake)
    svc._profiles[p.id] = p  # present in memory, absent from _daemon_ids

    p.name = "Edited Offline Copy"
    svc.save_profile(p)

    names = _names(fake)
    assert "create" in names and "update" in names  # tried create, then updated
    assert svc.is_published(p.id)
    assert fake.store[p.id]["name"] == "Edited Offline Copy"


def test_save_offline_keeps_local_draft(cfg):
    fake = FakeDaemonClient()
    fake.raise_on["create_profile"] = DaemonTimeout()
    svc = ProfileService(client=fake)
    p = Profile(name="Draft")

    svc.save_profile(p)

    assert p.id in svc.unpublished_ids
    assert svc.is_published(p.id) is False
    assert svc.offline is True
    # The edit is never lost — the local draft is written regardless.
    assert (profiles_dir() / f"{p.id}.json").exists()


def test_save_rejected_by_daemon_keeps_draft_not_offline(cfg):
    fake = FakeDaemonClient()
    fake.raise_on["create_profile"] = DaemonError(
        code="validation_error", message="bad floor", status=400
    )
    svc = ProfileService(client=fake)
    p = Profile(name="Bad")

    svc.save_profile(p)

    assert p.id in svc.unpublished_ids
    assert svc.is_published(p.id) is False
    assert svc.offline is False  # daemon was reachable, it just rejected the doc
    assert (profiles_dir() / f"{p.id}.json").exists()


def test_create_profile_method_uploads(cfg):
    fake = FakeDaemonClient()
    svc = ProfileService(client=fake)

    p = svc.create_profile("Made")

    assert p.id in fake.store
    assert svc.is_published(p.id)


def test_duplicate_profile_uploads(cfg):
    fake = FakeDaemonClient()
    src = Profile(name="Source")
    _seed(fake, src)
    svc = ProfileService(client=fake)
    svc.load()
    fake.calls.clear()

    dup = svc.duplicate_profile(src.id, "Copy")

    assert dup is not None
    assert dup.id in fake.store
    assert svc.is_published(dup.id)


# ---------------------------------------------------------------------------
# delete_profile()
# ---------------------------------------------------------------------------


def test_delete_propagates_to_daemon(cfg):
    fake = FakeDaemonClient()
    p = Profile(name="Doomed")
    _seed(fake, p)
    svc = ProfileService(client=fake)
    svc.load()

    assert svc.delete_profile(p.id) is True
    assert ("delete", p.id) in fake.calls
    assert p.id not in fake.store
    assert p.id not in _ids(svc)
    assert not (profiles_dir() / f"{p.id}.json").exists()


def test_delete_refused_when_profile_in_use(cfg):
    fake = FakeDaemonClient()
    p = Profile(name="Active")
    _seed(fake, p)
    svc = ProfileService(client=fake)
    svc.load()
    fake.raise_on["delete_profile"] = DaemonError(
        code="profile_in_use", message="active", status=409
    )

    assert svc.delete_profile(p.id) is False
    # Still present both locally and on the daemon — no desync.
    assert p.id in _ids(svc)
    assert p.id in fake.store
    assert (profiles_dir() / f"{p.id}.json").exists()


def test_delete_offline_removes_locally(cfg):
    fake = FakeDaemonClient()
    p = Profile(name="Gone")
    _seed(fake, p)
    svc = ProfileService(client=fake)
    svc.load()
    fake.raise_on["delete_profile"] = DaemonUnavailable()

    assert svc.delete_profile(p.id) is True
    assert p.id not in _ids(svc)
    assert svc.offline is True


# ---------------------------------------------------------------------------
# pure-local mode (client=None) makes no daemon calls
# ---------------------------------------------------------------------------


def test_no_client_is_pure_local(cfg):
    svc = ProfileService()  # no client

    errors = svc.load()
    assert errors == []
    assert len(svc.profiles) == len(default_profiles())  # seeded locally

    p = Profile(name="LocalOnly")
    svc.save_profile(p)

    # No daemon concept: nothing is "published", nothing is a pending draft.
    assert svc.unpublished_ids == set()
    assert svc.is_published(p.id) is False
    assert svc.offline is False
    assert (profiles_dir() / f"{p.id}.json").exists()
