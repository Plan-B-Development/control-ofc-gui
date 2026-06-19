"""XDG-compliant path helpers for config, state, and cache.

Includes ``atomic_write`` for crash-safe persistence (temp file + fsync +
os.replace). See Dan Luu "Files are hard" and the POSIX rename(2) guarantee.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

_APP = "control-ofc"

# Hard cap on a single JSON config/import file (profiles, settings, themes).
# Real files are a few KB; this only stops a crafted oversized file from being
# read wholesale into memory. 4 MiB is ~1000x any real profile (audit 2026-06-19
# ph7 / DEC-172). Per-file — bundles are read one file at a time.
MAX_IMPORT_BYTES = 4 * 1024 * 1024

log = logging.getLogger(__name__)

# Path overrides set by the user in Settings → Application.
# Empty string = use XDG default.
_overrides: dict[str, Path] = {}


def _validated_override(name: str, value: str) -> Path | None:
    """Validate a user-configured directory override (P2-G).

    Accept only an absolute path with no ``..`` components that is not an
    existing non-directory; reject (log + ignore) anything else so a
    hand-edited ``app_settings.json`` cannot point the app at a traversing or
    bogus location. A not-yet-existing absolute dir is allowed — callers create
    it lazily.
    """
    p = Path(value)
    if not p.is_absolute() or ".." in p.parts:
        log.warning(
            "Ignoring %s dir override (not an absolute, non-traversing path): %s", name, value
        )
        return None
    try:
        if p.exists() and not p.is_dir():
            log.warning("Ignoring %s dir override (exists but is not a directory): %s", name, value)
            return None
    except OSError as e:
        log.warning("Ignoring %s dir override (%s): %s", name, e, value)
        return None
    return p


def set_path_overrides(
    profiles_dir: str = "",
    themes_dir: str = "",
    export_dir: str = "",
) -> None:
    """Apply user-configured directory overrides. Empty string clears the override.

    Each non-empty override is validated (absolute, non-traversing, not an
    existing file); invalid values are logged and ignored (P2-G).
    """
    for key in ("profiles", "themes", "export"):
        _overrides.pop(key, None)
    for key, value in (
        ("profiles", profiles_dir),
        ("themes", themes_dir),
        ("export", export_dir),
    ):
        if not value:
            continue
        validated = _validated_override(key, value)
        if validated is not None:
            _overrides[key] = validated


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


def _reject_nonfinite(token: str) -> float:
    """``parse_constant`` hook: reject the non-standard JSON ``NaN`` /
    ``Infinity`` / ``-Infinity`` literals that the stdlib ``json`` accepts by
    default. (A finite literal that overflows to ``inf``, e.g. ``1e400``, slips
    past this — the per-field ``_is_finite`` guards catch that case.)"""
    raise ValueError(f"non-finite JSON constant in import: {token!r}")


def load_json_capped(path: Path, *, max_bytes: int = MAX_IMPORT_BYTES) -> object:
    """Read and JSON-parse *path* with a hard size cap (P3 import hardening).

    Bounded read — never pulls more than ``max_bytes + 1`` into memory — so a
    crafted oversized file cannot exhaust RAM, plus ``parse_constant`` rejection
    of the non-standard ``NaN``/``Infinity`` literals. Raises ``ValueError`` when
    the file exceeds the cap or carries a non-finite constant (``json.JSONDecodeError``
    is itself a ``ValueError``), and ``OSError`` for read failures — callers
    handle both. For external/import paths only; internal trusted reads (e.g.
    ``/proc/cmdline``) need no cap.
    """
    with path.open("rb") as f:
        raw = f.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"import file exceeds {max_bytes} bytes: {path}")
    return json.loads(raw, parse_constant=_reject_nonfinite)
