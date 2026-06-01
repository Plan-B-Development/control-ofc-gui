"""XDG-compliant path helpers for config, state, and cache.

Includes ``atomic_write`` for crash-safe persistence (temp file + fsync +
os.replace). See Dan Luu "Files are hard" and the POSIX rename(2) guarantee.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

_APP = "control-ofc"

# Path overrides set by the user in Settings → Application.
# Empty string = use XDG default.
_overrides: dict[str, Path] = {}


def set_path_overrides(
    profiles_dir: str = "",
    themes_dir: str = "",
    export_dir: str = "",
) -> None:
    """Apply user-configured directory overrides. Empty string clears the override."""
    _overrides.pop("profiles", None)
    _overrides.pop("themes", None)
    _overrides.pop("export", None)
    if profiles_dir:
        _overrides["profiles"] = Path(profiles_dir)
    if themes_dir:
        _overrides["themes"] = Path(themes_dir)
    if export_dir:
        _overrides["export"] = Path(export_dir)


def _xdg(env_var: str, fallback: str) -> Path:
    return Path(os.environ.get(env_var, fallback))


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", str(Path.home() / ".config")) / _APP


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", str(Path.home() / ".local" / "state")) / _APP


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", str(Path.home() / ".cache")) / _APP


def profiles_dir() -> Path:
    return _overrides.get("profiles", config_dir() / "profiles")


def themes_dir() -> Path:
    return _overrides.get("themes", config_dir() / "themes")


def export_default_dir() -> Path:
    return _overrides.get("export", Path.home())


def app_settings_path() -> Path:
    return config_dir() / "app_settings.json"


def assets_dir() -> Path:
    """Return the branding assets directory.

    Checks: dev layout (relative to package), installed layout (/opt/control-ofc),
    and CWD fallback.
    """
    candidates = [
        Path(__file__).parent.parent.parent / "assets" / "branding",
        Path("/usr/share/control-ofc-gui/assets/branding"),
        Path("assets/branding"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]  # return dev path even if missing


def ensure_dirs() -> None:
    """Create all required directories if they don't exist."""
    for d in [config_dir(), profiles_dir(), themes_dir(), state_dir(), cache_dir()]:
        d.mkdir(parents=True, exist_ok=True)


def atomic_write(filepath: Path, content: str) -> None:
    """Write *content* to *filepath* atomically.

    Uses the standard temp-file + fsync + os.replace + dir-fsync pattern:
    1. Write to a temp file in the same directory (same filesystem required)
    2. fsync the file to flush data to disk
    3. os.replace atomically swaps old -> new (POSIX rename guarantee)
    4. fsync the parent directory so the rename itself is durable across
       power loss (matches daemon/src/atomic_io.rs DEC-108)
    5. Cleanup temp file on any error

    This ensures readers always see either the complete old file or the
    complete new file — never a partial write. A mid-write crash leaves
    the original file intact. Without step 4, ext4/btrfs can land the
    rename in the journal while the dirent change is still in the page
    cache, so power loss between rename and journal flush can resurrect
    the old name on next mount even though the new file's data is durable.
    """
    dirpath = str(filepath.parent)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", prefix=".", dir=dirpath)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(filepath))
        os.chmod(str(filepath), 0o600)
        # Parent-dir fsync — see step 4 in the docstring. Failure here is
        # not fatal: the file content is already on disk under the new
        # name; only the rename's durability across power loss is lost.
        # Some filesystems (tmpfs) do not honour dir fsync; suppress
        # OSError to stay portable, matching the daemon's "log+continue"
        # posture in atomic_io.rs.
        with contextlib.suppress(OSError):
            dir_fd = os.open(dirpath, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise
