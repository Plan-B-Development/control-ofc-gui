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


def log_path() -> Path:
    return state_dir() / "gui.log"


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

    Uses the standard temp-file + fsync + os.replace pattern:
    1. Write to a temp file in the same directory (same filesystem required)
    2. fsync the file to flush data to disk
    3. os.replace atomically swaps old -> new (POSIX rename guarantee)
    4. Cleanup temp file on any error

    This ensures readers always see either the complete old file or the
    complete new file — never a partial write. A mid-write crash leaves
    the original file intact.
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
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise
