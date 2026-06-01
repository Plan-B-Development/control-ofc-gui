"""Tests for XDG path helpers."""

from __future__ import annotations

import os
import stat

import pytest

from control_ofc import paths
from control_ofc.paths import (
    atomic_write,
    config_dir,
    ensure_dirs,
    profiles_dir,
    state_dir,
    themes_dir,
)


def test_config_dir_ends_with_control_ofc():
    assert config_dir().name == "control-ofc"


def test_profiles_dir_inside_config():
    assert profiles_dir().parent == config_dir()


def test_themes_dir_inside_config():
    assert themes_dir().parent == config_dir()


def test_state_dir_ends_with_control_ofc():
    assert state_dir().name == "control-ofc"


def test_ensure_dirs_creates_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    ensure_dirs()
    assert (tmp_path / "config" / "control-ofc" / "profiles").is_dir()
    assert (tmp_path / "config" / "control-ofc" / "themes").is_dir()
    assert (tmp_path / "state" / "control-ofc").is_dir()
    assert (tmp_path / "cache" / "control-ofc").is_dir()


# ---------------------------------------------------------------------------
# atomic_write
# ---------------------------------------------------------------------------


def test_atomic_write_writes_content(tmp_path):
    target = tmp_path / "file.json"
    atomic_write(target, '{"k":"v"}\n')
    assert target.read_text() == '{"k":"v"}\n'


def test_atomic_write_sets_owner_only_permissions(tmp_path):
    target = tmp_path / "file.json"
    atomic_write(target, "x")
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600


def test_atomic_write_leaves_no_tmp_sibling_on_success(tmp_path):
    target = tmp_path / "file.json"
    atomic_write(target, "ok")
    siblings = list(tmp_path.iterdir())
    assert siblings == [target], f"unexpected leftover files: {siblings}"


def test_atomic_write_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "deep" / "file.json"
    atomic_write(target, "hi")
    assert target.read_text() == "hi"


def test_atomic_write_replaces_existing_file_atomically(tmp_path):
    target = tmp_path / "file.json"
    target.write_text("old")
    atomic_write(target, "new")
    assert target.read_text() == "new"


def test_atomic_write_cleans_up_tmp_on_error(tmp_path, monkeypatch):
    """When os.replace raises, the temp file must be unlinked so a later
    save isn't blocked by stale `.tmp` siblings."""

    def boom(_src, _dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", boom)
    target = tmp_path / "file.json"
    with pytest.raises(OSError):
        atomic_write(target, "data")
    leftover = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftover == [], f"tmp file not cleaned: {leftover}"


def test_atomic_write_fsyncs_parent_directory(tmp_path, monkeypatch):
    """Regression test for the DEC-108 GUI/daemon parity backport.

    Without the parent-dir fsync, ext4/btrfs can land the rename in the
    journal while the dirent change is still in page cache. Power loss
    between rename and journal flush can then resurrect the old name on
    remount. We assert the helper *attempts* the dir fsync by recording
    every fd that os.fsync is called on and confirming at least one is the
    parent directory's file descriptor.
    """
    fsynced_paths: list[str] = []
    real_fsync = os.fsync
    real_open = os.open

    def tracking_fsync(fd: int) -> None:
        # /proc/self/fd/<fd> resolves to the underlying path so we can
        # tell file fsyncs from directory fsyncs.
        try:
            link = os.readlink(f"/proc/self/fd/{fd}")
            fsynced_paths.append(link)
        except OSError:
            pass
        real_fsync(fd)

    monkeypatch.setattr(paths.os, "fsync", tracking_fsync)
    # os.open is patched only to make sure the test still uses the real
    # open; this guards against accidental shadowing.
    monkeypatch.setattr(paths.os, "open", real_open)

    target = tmp_path / "file.json"
    atomic_write(target, "x")
    assert any(p == str(tmp_path) for p in fsynced_paths), (
        f"parent dir {tmp_path} was never fsynced; only saw {fsynced_paths}"
    )


def test_atomic_write_tolerates_parent_dir_fsync_failure(tmp_path, monkeypatch):
    """The dir fsync is best-effort: OSError must not propagate, since the
    file content is already durable under the new name."""
    real_fsync = os.fsync
    file_path = tmp_path / "file.json"

    def selective_fsync(fd: int) -> None:
        # Fail the dir fsync (fd refers to the directory) but allow the
        # data fsync through so the test still exercises the durability
        # path for the content itself.
        try:
            link = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            real_fsync(fd)
            return
        if link == str(tmp_path):
            raise OSError("simulated dir fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(paths.os, "fsync", selective_fsync)

    # Must not raise even though the dir fsync failed.
    atomic_write(file_path, "still-saved")
    assert file_path.read_text() == "still-saved"
