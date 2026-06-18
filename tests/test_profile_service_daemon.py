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
from control_ofc.services.profile_service import Profile, ProfileService, default_profiles


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

    def _maybe(self, name: str) -> None:
        exc = self.raise_on.get(name)
        if exc is not None:
            raise exc

    def list_profiles(self) -> list[dict]:
        self.calls.append(("list", None))
        self._maybe("list_profiles")
        return [dict(d) for d in self.store.values()]

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
