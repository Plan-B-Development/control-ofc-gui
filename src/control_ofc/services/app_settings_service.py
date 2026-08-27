"""Application settings — GUI-owned preferences persisted as JSON."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from control_ofc.colors import is_valid_color
from control_ofc.paths import app_settings_path, atomic_write, load_json_capped

log = logging.getLogger(__name__)

# Settings keys excluded from the portable (shareable) export — machine/session
# state and hardware-id-keyed maps that should not travel between machines. The
# importer also strips these from incoming files, so a shared config can never
# move another user's window or wipe local data-dir overrides (DEC-140).
MACHINE_SPECIFIC_KEYS = frozenset(
    {
        "window_geometry",
        "last_page_index",
        "controls_card_sizes",
        "series_colors",
        "diagnostics_hidden_sensor_ids",
        "sensor_class_overrides",
        "acknowledged_kernel_warnings",
        "fan_aliases_seeded",
        "profiles_dir_override",
        "themes_dir_override",
        "export_default_dir",
        "daemon_import_prompted",
        # DEC-245: persisted view state. Pane sizes and log filters describe one
        # person's window on one screen; a shared export carrying them is the
        # DEC-140 problem (someone else's geometry moving your panes).
        "splitter_sizes",
        "logs_level_filters",
        "logs_search_text",
        "logs_source_filter",
    }
)

# DEC-245. Kept as literals rather than importing ChartMode, which would make the
# settings layer depend on a UI-facing service. `test_chart_modes_match_the_enum`
# pins the two together so the pair cannot drift.
_CHART_MODES = frozenset({"thermals", "fans", "combined", "diagnostics"})

# Bounds on the persisted layout map. The app has nine splitters; the headroom is
# for retired objectNames a future release leaves behind. Only reachable by hand
# editing (the key is machine-specific, so it cannot arrive by import), but every
# save deep-copies the whole dataclass and DEC-245 made saves far more frequent.
_MAX_SPLITTERS = 64
_MAX_PANES_PER_SPLITTER = 16

_LOG_LEVELS = frozenset({"info", "warn", "error"})
_LOG_LEVELS_ALL = ["info", "warn", "error"]

# Hardware-derived state a demo session must never write (DEC-244). Demo's
# synthetic ids collide *exactly* with real hardware (`openfan:ch00` …), so these
# are the keys where a demo write would land on the user's actual fans and
# sensors. Everything else in AppSettings is an ordinary preference and saves
# normally, even in demo — sealing the lot locked the user out of
# `demo_on_disconnect`, the one setting they need when a dead daemon has just
# dropped them into demo involuntarily.
#
# `chart_series_seeded` is not hardware-keyed but belongs here: it is a one-way
# "this install has been seeded" latch, so a demo session flipping it would
# permanently consume the real user's first-run chart seeding (DEC-181).
_DEMO_SEALED_KEYS = frozenset(
    {
        "fan_aliases",
        "fan_zones",
        "hidden_chart_series",
        "series_colors",
        "sensor_class_overrides",
        "controls_card_sizes",
        "chart_series_seeded",
    }
)

# How many `app_settings.json.corrupt[.N]` files to keep before refusing to
# quarantine further. Bounded so a repeatedly-corrupting file cannot fill the
# config directory, but >1 because the *newest* corrupt file is usually the most
# valuable one — see `_quarantine_unparseable`.
_MAX_QUARANTINE_SLOTS = 5

_CARD_SIZES = frozenset({"compact", "comfortable", "large"})

# Window-geometry sanity bound — rejects corruption and absurd off-screen values.
_GEOM_MAX = 32000


# --- Untrusted-input coercion helpers (DEC-137) -----------------------------
# Every helper takes an arbitrary JSON value plus the field default and returns
# a well-typed, in-range value. None of them raise: a bad value becomes the
# default (or, for collections, drops only the offending entries).


def _as_bool(value: object, default: bool) -> bool:
    """Accept only real booleans; reject 0/1 and everything else."""
    return value if isinstance(value, bool) else default


def _as_int(value: object, default: int, lo: int | None = None, hi: int | None = None) -> int:
    """Coerce to int (rejecting bool/float/str), then clamp to [lo, hi]."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _as_str(value: object, default: str, maxlen: int = 512) -> str:
    return value[:maxlen] if isinstance(value, str) else default


def _as_str_dict(value: object, default: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, dict):
        return dict(default)
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


# AIO Phase 1 (DEC-156): user sensor-classification overrides. Only "coolant"
# is offered today; the whitelist stops an untrusted settings/import file from
# injecting an arbitrary source_class string into the display layer.
_SENSOR_OVERRIDE_VALUES = frozenset({"coolant"})


def _as_sensor_overrides(value: object, default: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, dict):
        return dict(default)
    return {k: v for k, v in value.items() if isinstance(k, str) and v in _SENSOR_OVERRIDE_VALUES}


def _as_str_list(value: object, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    return [v for v in value if isinstance(v, str)]


def _as_color_dict(value: object, default: dict[str, str]) -> dict[str, str]:
    """Keep only str -> valid-hex-colour entries (drops injection/garbage)."""
    if not isinstance(value, dict):
        return dict(default)
    return {k: v for k, v in value.items() if isinstance(k, str) and is_valid_color(v)}


def _as_enum(value: object, allowed: frozenset[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _as_geometry(value: object, default: list[int]) -> list[int]:
    """Require exactly 4 sane ints [x, y, w, h]; else the default."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return list(default)
    if any(isinstance(n, bool) or not isinstance(n, int) for n in value):
        return list(default)
    x, y, w, h = value
    if not (-_GEOM_MAX <= x <= _GEOM_MAX and -_GEOM_MAX <= y <= _GEOM_MAX):
        return list(default)
    if not (1 <= w <= _GEOM_MAX and 1 <= h <= _GEOM_MAX):
        return list(default)
    return [x, y, w, h]


def _as_splitter_sizes(value: object, default: dict[str, list[int]]) -> dict[str, list[int]]:
    """Keep only objectName -> list-of-positive-ints entries (DEC-245).

    Length is not checked here — a splitter's pane count can legitimately change
    between releases, and the restore side drops a stale entry whose length no
    longer matches rather than forcing a wrong layout.
    """
    if not isinstance(value, dict):
        return dict(default)
    result: dict[str, list[int]] = {}
    for key, sizes in value.items():
        if len(result) >= _MAX_SPLITTERS:
            break  # a hand-edited file cannot make every save deep-copy a huge dict
        if not isinstance(key, str) or not isinstance(sizes, (list, tuple)) or not sizes:
            continue
        if len(sizes) > _MAX_PANES_PER_SPLITTER:
            continue
        if any(
            isinstance(n, bool) or not isinstance(n, int) or not (0 <= n <= _GEOM_MAX)
            for n in sizes
        ):
            continue
        result[key] = list(sizes)
    return result


def _as_card_sizes(value: object, default: dict[str, list[int]]) -> dict[str, list[int]]:
    """Keep only str -> [width, height] entries of two positive ints."""
    if not isinstance(value, dict):
        return dict(default)
    result: dict[str, list[int]] = {}
    for key, dims in value.items():
        if not isinstance(key, str):
            continue
        if not isinstance(dims, (list, tuple)) or len(dims) != 2:
            continue
        if any(
            isinstance(n, bool) or not isinstance(n, int) or not (0 < n <= _GEOM_MAX) for n in dims
        ):
            continue
        result[key] = [dims[0], dims[1]]
    return result


@dataclass
class AppSettings:
    """GUI preferences. Persisted at ~/.config/control-ofc/app_settings.json."""

    # v2 = DEC-216 page-index renumber; v3 = DEC-224 vestige-key drop;
    # v4 = daemon_startup_delay_secs dropped (the daemon owns it, DEC-285)
    version: int = 4
    default_startup_page: int = 0  # PAGE_DASHBOARD
    restore_last_page: bool = True
    demo_on_disconnect: bool = False
    chart_default_range_index: int = 4  # 15m in TimelineChart
    theme_name: str = "Default Dark"
    fan_aliases: dict[str, str] = field(default_factory=dict)
    # DEC-176: GUI-owned named physical zones for fans (fan_id -> zone name),
    # e.g. "Front Intake" / "Exhaust". Mirrors fan_aliases exactly — both are
    # fan_id-keyed display labels — so it is portable (NOT in MACHINE_SPECIFIC_KEYS)
    # and travels with export. Unassigned fans fall back to role/source grouping in
    # the dashboard's retired fan-zone grid. Dormant since DEC-222 — retained so
    # no settings-schema migration is needed and saved zones are not lost.
    fan_zones: dict[str, str] = field(default_factory=dict)
    # DEC-228: set once the GUI has adopted profile `member_label`s as fan
    # aliases. Needed for the same reason as `chart_series_seeded` (DEC-181):
    # without it, an alias the user deliberately *clears* would be re-seeded from
    # the profile on the next launch and could never be removed. Machine-specific
    # (it describes what this install has already done), so it is excluded from
    # portable export via MACHINE_SPECIFIC_KEYS.
    fan_aliases_seeded: bool = False
    hidden_chart_series: list[str] = field(default_factory=list)
    # DEC-181: True once the dashboard has seeded the curated first-run chart
    # subset. Needed because hidden_chart_series == [] is indistinguishable
    # between a fresh user (seed the curated default) and a returning user who
    # chose "show all" (must NOT be re-decluttered). Without this flag the
    # dashboard cannot tell them apart — do not seed first-run defaults without it.
    chart_series_seeded: bool = False
    show_gpu_zero_rpm_warning: bool = True
    # DEC-157: one-time popup explaining the AIO pump floor ("don't run the
    # pump too low"), shown when an AIO pump is first added to a control. Mirrors
    # show_gpu_zero_rpm_warning — a behaviour preference that travels with export.
    show_aio_pump_info: bool = True
    # DEC-161: set once the GUI has offered the one-time "import my profiles
    # into the daemon" migration, so the startup prompt does not nag on every
    # launch. Machine-specific (each install imports its own local profiles), so
    # excluded from portable export via MACHINE_SPECIFIC_KEYS.
    daemon_import_prompted: bool = False
    series_colors: dict[str, str] = field(default_factory=dict)
    last_page_index: int = 0
    window_geometry: list[int] = field(default_factory=lambda: [100, 100, 1200, 800])

    # Configurable data directories (empty = use XDG default)
    profiles_dir_override: str = ""
    themes_dir_override: str = ""
    export_default_dir: str = ""

    # Behaviour settings
    wizard_spindown_seconds: int = 8  # Fan Wizard spin-down timer (5-12s)
    # NOTE: there is deliberately no `daemon_startup_delay_secs` here (DEC-285).
    # It was the last daemon-owned key the GUI mirrored locally, and the mirror
    # was the bug: it was seeded from this file at construction, so a failed or
    # absent `GET /config` left the spinner showing a local guess, and pressing
    # Save POSTed it unconditionally — writing `runtime.toml` and permanently
    # shadowing the operator's `daemon.toml` with a value nobody chose. Do not
    # reintroduce a field for a setting the daemon is the source of truth for.
    hide_igpu_sensors: bool = True  # Auto-hide iGPU sensors when dGPU present
    hide_unused_fan_headers: bool = True  # Auto-hide fan headers with 0 RPM

    # DEC-128: Controls-page card density tier — "compact" | "comfortable" |
    # "large". Cards auto-scale with the theme font size; this tier multiplies
    # that size so the user can trade density for readability. Unknown values
    # fall back to "comfortable" at render time (card_metrics.card_dimensions).
    card_size: str = "comfortable"

    # DEC-129: per-card user size overrides on the Controls page, keyed by
    # control/curve id → [width, height] (snapped to the shared lattice).
    # Absent key = theme-derived sizing (DEC-128). Pruned of ids that no
    # longer exist in any known profile whenever a size is saved.
    controls_card_sizes: dict[str, list[int]] = field(default_factory=dict)

    # DEC-098: kernel-warning IDs the user has already dismissed for this
    # GPU. Keyed by warning.id (not session-scoped). Persisting prevents the
    # popup from re-firing on every restart for a known-bad-kernel that the
    # user has acknowledged but cannot or will not change.
    acknowledged_kernel_warnings: list[str] = field(default_factory=list)

    # DEC-117: sensor IDs the user has hidden in the Diagnostics > Sensors
    # table. Local to that page only (the dashboard chart uses its own
    # SeriesSelectionModel-backed list). Hidden sensors collapse into a
    # group row at the bottom of the table, not silently removed.
    diagnostics_hidden_sensor_ids: list[str] = field(default_factory=list)

    # DEC-245: view state that used to reset on every launch.
    #
    # `chart_mode` closes a live inconsistency, not just a lost preference: the
    # selector reset to Combined on restart while `hidden_chart_series` still held
    # the *previous* mode's result, so the label and the chart disagreed.
    chart_mode: str = "combined"
    # Per-splitter pane sizes keyed by objectName (every QSplitter in the app has a
    # unique one). One dict rather than nine fields, mirroring how DEC-234 put the
    # handle *styling* in one shared helper. Restored sizes are clamped to a
    # per-pane minimum — DEC-222 removed the sensors rail's show/hide toggle in
    # favour of the splitter, so a persisted fully-collapsed pane would be an
    # unrecoverable soft-lock.
    splitter_sizes: dict[str, list[int]] = field(default_factory=dict)
    # Enabled Logs level toggles. Absent means "all on"; an explicitly empty list
    # is a real state (the user unticked everything) and is preserved.
    logs_level_filters: list[str] = field(default_factory=lambda: list(_LOG_LEVELS_ALL))
    logs_search_text: str = ""
    # DEC-282: the Logs source dropdown. Machine-specific and a per-user view
    # filter, like logs_search_text — excluded from exports and support bundles.
    logs_source_filter: str = ""

    # DEC-156: user overrides forcing a sensor's classification, keyed by stable
    # sensor id -> source_class (only "coolant" today). GUI-owned policy — the
    # daemon stays hardware-truthful; this lets the user mark a coolant sensor
    # the conservative auto-classifier missed. Machine-specific (sensor ids are
    # local), so excluded from portable export.
    sensor_class_overrides: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> AppSettings:
        """Build settings from an untrusted dict (on-disk file or import).

        Never raises: every field is type-checked and coerced, out-of-range
        values are clamped, and malformed entries fall back to the field
        default. This is the trust boundary for both the persisted settings
        file and user-supplied import files (DEC-137).
        """
        if not isinstance(data, dict):
            return AppSettings()
        # DEC-216 migration: the legacy Diagnostics page (stack index 3) was
        # removed and the pages after it renumbered down one, so decrement any
        # persisted page index at or past the first shifted page — restore then
        # lands on the same page. Version-gated + idempotent (reads the original
        # on-disk value; a versionless legacy file is treated as v1 and migrates).
        raw_version = _as_int(data.get("version"), 1, lo=1)
        startup_page = _as_int(data.get("default_startup_page"), 0, lo=0, hi=99)
        last_page = _as_int(data.get("last_page_index"), 0, lo=0, hi=99)
        if raw_version < 2:
            if startup_page >= 4:
                startup_page -= 1
            if last_page >= 4:
                last_page -= 1
            raw_version = 2
        # DEC-224 (v3): four written-never-read keys dropped (fan_zone_order,
        # fan_zones_collapsed, card_sensor_bindings, show_hardware_guidance).
        # No data transform — from_dict simply no longer reads them, so the
        # next save persists a clean v3 file. Idempotent.
        if raw_version < 3:
            raw_version = 3
        # DEC-285 (v4): `daemon_startup_delay_secs` dropped — the daemon owns
        # that setting and `GET /config` reports it. Same shape as the v3
        # migration: no data transform, `from_dict` simply no longer reads the
        # key, so the next save writes a clean v4 file. Idempotent.
        if raw_version < 4:
            raw_version = 4
        return AppSettings(
            version=raw_version,
            default_startup_page=startup_page,
            restore_last_page=_as_bool(data.get("restore_last_page"), True),
            demo_on_disconnect=_as_bool(data.get("demo_on_disconnect"), False),
            chart_default_range_index=_as_int(
                data.get("chart_default_range_index"), 4, lo=0, hi=99
            ),
            theme_name=_as_str(data.get("theme_name"), "Default Dark"),
            fan_aliases=_as_str_dict(data.get("fan_aliases"), {}),
            fan_zones=_as_str_dict(data.get("fan_zones"), {}),
            fan_aliases_seeded=_as_bool(data.get("fan_aliases_seeded"), False),
            hidden_chart_series=_as_str_list(data.get("hidden_chart_series"), []),
            chart_series_seeded=_as_bool(data.get("chart_series_seeded"), False),
            show_gpu_zero_rpm_warning=_as_bool(data.get("show_gpu_zero_rpm_warning"), True),
            show_aio_pump_info=_as_bool(data.get("show_aio_pump_info"), True),
            daemon_import_prompted=_as_bool(data.get("daemon_import_prompted"), False),
            series_colors=_as_color_dict(data.get("series_colors"), {}),
            last_page_index=last_page,
            window_geometry=_as_geometry(data.get("window_geometry"), [100, 100, 1200, 800]),
            profiles_dir_override=_as_str(data.get("profiles_dir_override"), ""),
            themes_dir_override=_as_str(data.get("themes_dir_override"), ""),
            export_default_dir=_as_str(data.get("export_default_dir"), ""),
            wizard_spindown_seconds=_as_int(data.get("wizard_spindown_seconds"), 8, lo=5, hi=12),
            hide_igpu_sensors=_as_bool(data.get("hide_igpu_sensors"), True),
            hide_unused_fan_headers=_as_bool(data.get("hide_unused_fan_headers"), True),
            card_size=_as_enum(data.get("card_size"), _CARD_SIZES, "comfortable"),
            controls_card_sizes=_as_card_sizes(data.get("controls_card_sizes"), {}),
            acknowledged_kernel_warnings=_as_str_list(data.get("acknowledged_kernel_warnings"), []),
            diagnostics_hidden_sensor_ids=_as_str_list(
                data.get("diagnostics_hidden_sensor_ids"), []
            ),
            sensor_class_overrides=_as_sensor_overrides(data.get("sensor_class_overrides"), {}),
            chart_mode=_as_enum(data.get("chart_mode"), _CHART_MODES, "combined"),
            splitter_sizes=_as_splitter_sizes(data.get("splitter_sizes"), {}),
            logs_level_filters=[
                lv
                for lv in _as_str_list(data.get("logs_level_filters"), _LOG_LEVELS_ALL)
                if lv in _LOG_LEVELS
            ],
            logs_search_text=_as_str(data.get("logs_search_text"), "", maxlen=200),
            logs_source_filter=_as_str(data.get("logs_source_filter"), "", maxlen=64),
        )

    def portable_dict(self) -> dict:
        """Return ``to_dict()`` minus machine-specific keys (shareable export)."""
        return {k: v for k, v in self.to_dict().items() if k not in MACHINE_SPECIFIC_KEYS}


class AppSettingsService:
    """Load, save, and manage application settings.

    Two guards stand between this class and the user's file (DEC-244), because
    ``save()`` writes a **whole-object snapshot** — there is no read-modify-write,
    so a single ``update()`` from a service holding the wrong state rewrites
    every key at once.

    * **An unloaded service never writes.** ``__init__`` seeds a defaults
      object and only ``main.py`` calls ``load()``, so a default-constructed
      service holds *placeholder* values while still pointing ``save()`` at the
      real file. One ``update()`` would then replace the user's entire config
      with defaults. Not theoretical: three tests reached this path and
      reproducibly wiped the developer's chart colours and series selection —
      on every quality-gate run, which is why the loss looked like it came from
      a daemon update.
    * **An ephemeral service never writes hardware-derived state.** Demo mode
      swaps the hardware-keyed maps for synthetic fixtures whose ids collide
      exactly with real hardware (``openfan:ch00`` …). ``make_ephemeral`` seals
      ``_DEMO_SEALED_KEYS`` for the whole session, in memory as well as on disk,
      so all ~29 persist call sites are safe by construction. Guarding them one
      at a time is precisely what failed before: DEC-227 guarded two and the
      rest silently diverged.

      The seal is scoped to those keys rather than to every write. Sealing
      everything meant a user dropped into demo by ``demo_on_disconnect`` (a
      dead daemon) could not turn ``demo_on_disconnect`` back off — the Settings
      page reported success and the write was dropped, so the next launch was
      demo again, with no in-app escape.
    * **A file we could not read is never overwritten.** ``load()`` separates
      "unparseable" from "unreadable". A proven-bad file is moved to a numbered
      ``.corrupt`` slot and saving resumes; an ``OSError`` — including one raised
      by ``open()`` on a symlink whose target is missing, which a ``path.exists()``
      pre-check would have hidden — leaves the service unloaded. If the file
      cannot be moved aside at all, the service stays unloaded too: better to
      save nothing than to destroy the only copy. A failed *re*-load disarms an
      already-armed service for the same reason.

    The unloaded guard refuses the *write* only — in-memory state still updates,
    so the session behaves normally on screen and leaves nothing on disk. The
    demo seal instead drops sealed keys from ``update()`` outright, memory
    included: that is what stops a Settings *export* taken mid-demo from
    carrying synthetic ids, since ``portable_dict()`` reads the in-memory
    object. This class is the **only** place the demo rule lives — the per-site
    guard MainWindow used to carry was retired with it, because a second partial
    copy of the rule is exactly what let DEC-227's gap go unnoticed.
    """

    def __init__(self) -> None:
        self._settings = AppSettings()
        self._loaded = False
        self._ephemeral_reason = ""

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def is_ephemeral(self) -> bool:
        """Whether this service has been latched off disk for the session."""
        return bool(self._ephemeral_reason)

    def make_ephemeral(self, reason: str) -> None:
        """Seal hardware-derived state for the rest of the process.

        One-way by design: there is no demo -> live transition, and a service
        that could be re-armed would put the clobber back within reach of some
        future caller.

        Ordinary preferences keep saving — see ``_DEMO_SEALED_KEYS`` for why the
        seal is scoped rather than total.
        """
        if self._ephemeral_reason:
            return
        self._ephemeral_reason = reason
        log.info("App settings: hardware-derived keys are session-only (%s)", reason)

    def _quarantine_unparseable(self, path: Path, error: Exception) -> bool:
        """Move a proven-unparseable settings file aside. True if saving may resume.

        Without this, a file that fails to parse loads as defaults and the very
        next ``update()`` replaces it — the user's whole config gone behind a
        single log line. Renaming keeps it recoverable by hand while letting the
        app carry on with a fresh file.

        Quarantines are **numbered, not single-slot**. The first draft kept only
        the earliest ``.corrupt`` on the theory that a later one would be the
        app's own generated file — which is wrong once any time has passed: a
        second corruption years in holds a fully rebuilt configuration, while the
        preserved copy is the near-empty day-one file. Whichever file is more
        valuable is not knowable here, so none is discarded.

        Returning ``False`` (rename failed, or every slot taken) means the bad
        file is still in place, so the caller must **not** arm the service —
        writing would destroy the only copy.
        """
        for n in range(_MAX_QUARANTINE_SLOTS):
            suffix = ".corrupt" if n == 0 else f".corrupt.{n}"
            target = path.with_suffix(path.suffix + suffix)
            if target.exists():
                continue
            try:
                os.replace(path, target)
            except OSError as e:
                log.warning(
                    "Could not quarantine unparseable %s: %s — refusing to write over it", path, e
                )
                return False
            log.warning("Moved unparseable app settings to %s (%s)", target, error)
            return True
        log.warning(
            "All %d quarantine slots beside %s are occupied — refusing to overwrite the "
            "unreadable file. Settings will not be saved this session; remove the .corrupt "
            "files to re-enable saving.",
            _MAX_QUARANTINE_SLOTS,
            path,
        )
        return False

    def load(self) -> None:
        path = app_settings_path()
        # Reset both: a *re*-load that fails must disarm, or the service is left
        # armed while holding defaults — precisely the clobber this class exists
        # to prevent, just reached from the second call instead of the first.
        self._settings = AppSettings()
        self._loaded = False

        try:
            data = load_json_capped(path)
        except FileNotFoundError:
            if path.is_symlink():
                # The link exists, its target does not — a dotfiles repo that is
                # not checked out, or a drive not mounted at login. `lstat` is
                # what separates this from a genuinely absent file; `exists()`
                # and `open()` both collapse the two. Arming here would let the
                # first save replace the *symlink* with a regular file and
                # silently detach the user's managed config from its repo.
                log.warning(
                    "App settings symlink %s has no target — this session will not persist", path
                )
                return
            # Genuinely absent: a legitimate authoritative state (fresh install).
            # Defaults *are* the truth, and the first save creates the file.
            self._loaded = True
            return
        except OSError as e:
            # Could not read it — the config may be perfectly intact, so do *not*
            # authorise writing over it.
            log.warning(
                "Could not read app settings from %s: %s — this session will not persist", path, e
            )
            return
        except ValueError as e:
            # Unparseable JSON, or past the size cap. Either way the file cannot
            # be used, and `load_json_capped` raises this before we ever see a
            # dict. (`json.JSONDecodeError` is a `ValueError`.)
            log.warning("Unusable app settings at %s: %s — using defaults", path, e)
            if not self._quarantine_unparseable(path, e):
                return  # bad file still in place — saving would destroy it
            self._loaded = True
            return

        try:
            if not isinstance(data, dict):
                # Valid JSON, wrong shape — a themes/profiles export or a sync
                # conflict artefact copied over the file. `from_dict` never
                # raises and would coerce this to defaults, arming the service so
                # the next save destroys it: the exact loss DEC-244 exists to
                # stop, arriving through the one door still open.
                raise ValueError(f"top-level JSON is {type(data).__name__}, expected an object")
            self._settings = AppSettings.from_dict(data)
        except (KeyError, TypeError, ValueError) as e:
            log.warning("Wrong-shaped app settings at %s: %s — using defaults", path, e)
            if not self._quarantine_unparseable(path, e):
                return  # bad file still in place — saving would destroy it
        else:
            log.info("Loaded app settings from %s", path)
        self._loaded = True

    def save(self) -> None:
        # No ephemeral check here on purpose: the demo seal is enforced in
        # update(), which never lets a sealed key reach self._settings. Refusing
        # the whole write instead would also block ordinary preferences and trap
        # the user in demo_on_disconnect.
        if not self._loaded:
            # A programming error, not a user condition — something built a
            # service and wrote through it without load(). Loud on purpose: a
            # silently dropped save is how the next settings bug would hide.
            log.warning(
                "Refusing to write app settings — this service never loaded %s, so saving "
                "would overwrite it with defaults",
                app_settings_path(),
            )
            return
        atomic_write(app_settings_path(), json.dumps(self._settings.to_dict(), indent=2) + "\n")

    def update(self, **kwargs: object) -> None:
        """Update specific settings and save.

        Routes through ``AppSettings.from_dict`` so every value is coerced and
        range-checked exactly like a fresh load (P2-A) — a wrong-typed value can
        no longer persist in memory and then fail to reload next launch.

        In a sealed (demo) session, ``_DEMO_SEALED_KEYS`` are dropped here rather
        than at ``save()``. Dropping them from *memory* is the point: a Settings
        export reads ``portable_dict()`` off the in-memory object, so a key that
        reached memory could still travel even though it never hit disk.
        """
        if self._ephemeral_reason:
            sealed = _DEMO_SEALED_KEYS.intersection(kwargs)
            if sealed:
                log.debug(
                    "Session-only (%s) — dropping hardware-derived %s",
                    self._ephemeral_reason,
                    ", ".join(sorted(sealed)),
                )
                kwargs = {k: v for k, v in kwargs.items() if k not in _DEMO_SEALED_KEYS}
                if not kwargs:
                    return
        merged = self._settings.to_dict()
        merged.update({k: v for k, v in kwargs.items() if hasattr(self._settings, k)})
        self._settings = AppSettings.from_dict(merged)
        self.save()

    def import_settings_from_dict(self, data: dict) -> AppSettings:
        """Import settings from a dict (e.g., from a comprehensive export file)."""
        return AppSettings.from_dict(data)

    def apply_imported(self, settings: AppSettings) -> None:
        """Apply imported settings and save."""
        self._settings = settings
        self.save()
