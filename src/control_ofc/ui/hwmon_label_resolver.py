"""Hwmon header label resolver (A3).

Three-tier resolution for the human-readable label of a motherboard PWM
header. Used when neither the user alias nor the daemon-supplied sysfs
label produces a useful name — the it87 driver, in particular, does not
expose ``pwmN_label`` / ``fanN_label`` files for most Gigabyte boards,
so without this resolver the user sees ``pwm1`` etc. on the X870E AORUS
MASTER instead of CPU_FAN / SYS_FAN1 / SYS_FAN2 …

Priority (callers should check 1 first):

    1. ``app_settings.fan_aliases[fan_id]``           ← caller's job
    2. ``HwmonHeader.label`` (daemon-supplied sysfs)  ← caller's job
    3. ``/etc/sensors.d/*.conf`` and friends          ← this module
    4. In-repo ``HWMON_LABEL_FALLBACK`` table          ← this module
    5. raw sensor name (``pwmN``)                      ← this module

Reading ``/etc/sensors.d/*.conf`` is plain user-space file I/O, not
hardware access — it does not violate the GUI ↔ daemon boundary.

The libsensors parser is intentionally minimal: it recognises only the
``chip "<glob>"`` block header, ``label <feature> "<text>"`` lines, and
``ignore <feature>`` directives. Other libsensors features (``compute``,
``set``, ``bus``) are ignored. This is enough to resolve the chip
labels every Gigabyte / ASUS / MSI fan-header config in the wild
actually carries, without us having to track the full libsensors
grammar across releases.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from glob import glob as _glob
from pathlib import Path

log = logging.getLogger(__name__)


# ─── Fallback table ────────────────────────────────────────────────────


@dataclass(frozen=True)
class BoardKey:
    """Identifies a ``(vendor, board, chip)`` triple for fallback lookup.

    Attributes:
        vendor: DMI ``board_vendor`` exact match (empty = wildcard).
        board_glob: ``fnmatch`` pattern against DMI ``board_name``
            (empty = wildcard).
        chip: hwmon chip name exact match (e.g. ``it8696``,
            ``it87952``).
    """

    vendor: str
    board_glob: str
    chip: str


@dataclass(frozen=True)
class FallbackLabel:
    """A board-specific label entry. ``verified=False`` entries display
    with an ``(unverified)`` suffix per Decision 4-A — we are the first
    Linux source of truth for this mapping and want to make that obvious
    until users confirm by physically tracing fan cables."""

    text: str
    verified: bool

    def display(self) -> str:
        return self.text if self.verified else f"{self.text} (unverified)"


HWMON_LABEL_FALLBACK: dict[BoardKey, dict[str, FallbackLabel]] = {
    # ── Gigabyte X870E AORUS MASTER ────────────────────────────────
    # IT8696E primary chip. Mapping verified against the vendor user
    # manual and the bakman2 / nathan818fr community references.
    BoardKey(
        vendor="Gigabyte Technology Co., Ltd.",
        board_glob="X870E AORUS MASTER",
        chip="it8696",
    ): {
        "pwm1": FallbackLabel("CPU_FAN", verified=True),
        "pwm2": FallbackLabel("SYS_FAN1", verified=True),
        "pwm3": FallbackLabel("SYS_FAN2", verified=True),
        "pwm4": FallbackLabel("SYS_FAN3", verified=True),
        "pwm5": FallbackLabel("CPU_OPT", verified=True),
    },
    # IT87952E secondary chip. Mapping is a best-guess extrapolation
    # from the X570 AORUS MASTER's IT8792E pattern (SYS_FAN5_PUMP /
    # SYS_FAN6_PUMP / SYS_FAN4) and from the X870E AORUS MASTER user
    # manual's fan-header layout. Marked unverified — silkscreen
    # tracing required to confirm pwmN→header.
    BoardKey(
        vendor="Gigabyte Technology Co., Ltd.",
        board_glob="X870E AORUS MASTER",
        chip="it87952",
    ): {
        "pwm1": FallbackLabel("SYS_FAN4", verified=False),
        "pwm2": FallbackLabel("SYS_FAN5_PUMP", verified=False),
        "pwm3": FallbackLabel("SYS_FAN6_PUMP", verified=False),
    },
    # ── DEC-105: AM4 400-series boards with upstream lm-sensors configs ─
    # Each entry below is taken VERBATIM from a config file in
    # https://github.com/lm-sensors/lm-sensors/tree/master/configs — we
    # only ship the fallback for boards where there is an upstream config
    # to defend the mapping. Other AM4 400-series boards rely on the
    # libsensors parser to pick up the user's own /etc/sensors.d/ file or
    # default to the raw pwmN identifier.
    # ── Gigabyte X470 AORUS ULTRA GAMING ────────────────────────────
    # Source: configs/Gigabyte/X470-AORUS-ULTRA-GAMING.conf
    # Primary IT8686E at 0x0a40 (CPU_FAN, SYS_FAN1..3, CPU_OPT) and
    # secondary IT8792E at 0x0a60 (SYS_FAN5_PUMP, SYS_FAN6_PUMP, SYS_FAN4).
    BoardKey(
        vendor="Gigabyte Technology Co., Ltd.",
        board_glob="X470 AORUS ULTRA GAMING",
        chip="it8686",
    ): {
        "pwm1": FallbackLabel("CPU_FAN", verified=True),
        "pwm2": FallbackLabel("SYS_FAN1", verified=True),
        "pwm3": FallbackLabel("SYS_FAN2", verified=True),
        "pwm4": FallbackLabel("SYS_FAN3", verified=True),
        "pwm5": FallbackLabel("CPU_OPT", verified=True),
    },
    BoardKey(
        vendor="Gigabyte Technology Co., Ltd.",
        board_glob="X470 AORUS ULTRA GAMING",
        chip="it8792",
    ): {
        "pwm1": FallbackLabel("SYS_FAN5_PUMP", verified=True),
        "pwm2": FallbackLabel("SYS_FAN6_PUMP", verified=True),
        "pwm3": FallbackLabel("SYS_FAN4", verified=True),
    },
    # ── MSI X470 GAMING PRO (MS-7B79) ───────────────────────────────
    # Source: configs/MSI/MS_7B79_X470_GAMINGPRO.conf
    # Chip: NCT6795D. Note: fan1 = PUMP_FAN1, fan2 = CPU_FAN1.
    BoardKey(
        vendor="Micro-Star International Co., Ltd.",
        board_glob="X470 GAMING PRO",
        chip="nct6795",
    ): {
        "pwm1": FallbackLabel("PUMP_FAN1", verified=True),
        "pwm2": FallbackLabel("CPU_FAN1", verified=True),
        "pwm3": FallbackLabel("SYS_FAN1", verified=True),
        "pwm4": FallbackLabel("SYS_FAN2", verified=True),
        "pwm5": FallbackLabel("SYS_FAN3", verified=True),
        "pwm6": FallbackLabel("SYS_FAN4", verified=True),
    },
    # ── MSI B450M MORTAR (MS-7B89) ──────────────────────────────────
    # Source: configs/MSI/MS-7B89-B450M-MORTAR.conf
    # Chip: NCT6797D. fan1 is ignored upstream; fan2-fan5 mapped.
    # NCT6797D is the chip that the out-of-tree nct6687 driver
    # mis-claims via ID 0xd450 — see DEC-105 collision warning.
    BoardKey(
        vendor="Micro-Star International Co., Ltd.",
        board_glob="B450M MORTAR*",
        chip="nct6797",
    ): {
        "pwm2": FallbackLabel("CPU 1", verified=True),
        "pwm3": FallbackLabel("SYSTEM 1", verified=True),
        "pwm4": FallbackLabel("SYSTEM 2", verified=True),
        "pwm5": FallbackLabel("SYSTEM 3", verified=True),
    },
    # ── ASRock B450 Gaming ITX/AC ────────────────────────────────────
    # Source: configs/ASRock/B450-Gaming-ITX-ac.conf
    # Chip: NCT6792D (mainline kernel coverage).
    BoardKey(
        vendor="ASRock",
        board_glob="B450 Gaming ITX/ac",
        chip="nct6792",
    ): {
        "pwm1": FallbackLabel("CHA_FAN1", verified=True),
        "pwm2": FallbackLabel("CPU_FAN1", verified=True),
        "pwm3": FallbackLabel("CHA_FAN2", verified=True),
    },
    # ── DEC-106: AM4 500-series boards with upstream lm-sensors configs ─
    # Same D-B.B1 discipline as DEC-105: adopt only where upstream gives a
    # citable mapping. The libsensors convention is that `fanN_label` and
    # `pwmN` address the same physical fan, so we translate `fanN` labels
    # to `pwmN` keys at `verified=True` exactly as DEC-105 did.
    # ── Gigabyte B550 VISION D (GA-B550-VISION-D) ──────────────────
    # Source: configs/Gigabyte/GA-B550-VISION-D.conf
    # Primary IT8688E at 0x0a40 + secondary IT8792E at 0x0a60.
    BoardKey(
        vendor="Gigabyte Technology Co., Ltd.",
        board_glob="B550 VISION D",
        chip="it8688",
    ): {
        "pwm1": FallbackLabel("CPU_FAN", verified=True),
        "pwm2": FallbackLabel("SYS_FAN1", verified=True),
        "pwm3": FallbackLabel("SYS_FAN2", verified=True),
        "pwm4": FallbackLabel("SYS_FAN3", verified=True),
        "pwm5": FallbackLabel("CPU_OPT", verified=True),
    },
    BoardKey(
        vendor="Gigabyte Technology Co., Ltd.",
        board_glob="B550 VISION D",
        chip="it8792",
    ): {
        "pwm1": FallbackLabel("SYS_FAN5_PUMP", verified=True),
        "pwm2": FallbackLabel("SYS_FAN6_PUMP", verified=True),
        "pwm3": FallbackLabel("SYS_FAN4", verified=True),
    },
    # ── Gigabyte B550M AORUS PRO (GA-B550M-AORUS-PRO) ───────────────
    # Source: configs/Gigabyte/GA-B550M-AORUS-PRO.conf
    # Single-chip variant — IT8688E only.
    BoardKey(
        vendor="Gigabyte Technology Co., Ltd.",
        board_glob="B550M AORUS PRO*",
        chip="it8688",
    ): {
        "pwm1": FallbackLabel("CPU_FAN", verified=True),
        "pwm2": FallbackLabel("SYS_FAN1", verified=True),
        "pwm3": FallbackLabel("SYS_FAN2", verified=True),
        "pwm4": FallbackLabel("SYS_FAN3", verified=True),
        "pwm5": FallbackLabel("CPU_OPT", verified=True),
    },
    # ── MSI X570-A PRO (MS-7C37) ────────────────────────────────────
    # Source: configs/MSI/X570-A-Pro.conf
    # Chip: NCT6797D — same chip family as the DEC-105 B450M MORTAR
    # entry; the DEC-105 collision warning applies if `nct6687d` is
    # also loaded on this board.
    BoardKey(
        vendor="Micro-Star International Co., Ltd.",
        board_glob="X570-A PRO*",
        chip="nct6797",
    ): {
        "pwm1": FallbackLabel("Pump", verified=True),
        "pwm2": FallbackLabel("CPU Fan", verified=True),
        "pwm3": FallbackLabel("System Fan 1", verified=True),
        "pwm4": FallbackLabel("System Fan 2", verified=True),
        "pwm5": FallbackLabel("System Fan 3", verified=True),
        "pwm6": FallbackLabel("System Fan 4", verified=True),
        "pwm7": FallbackLabel("PCH Fan", verified=True),
    },
    # ── DEC-110: Intel LGA1700 — ASRock Z690 Extreme ───────────────
    # Source: configs/ASRock/Z690_Extreme.conf (verified upstream
    # lm-sensors config — chip reported as nct6798-isa-02a0 even though
    # the physical part is NCT6796D-E).
    # Only Intel-era board with an upstream lm-sensors config covering
    # the LGA1700+ generation as of 2026-Q2; other Intel Z690/Z790/Z890
    # boards rely on the libsensors parser to pick up the user's own
    # /etc/sensors.d/ file or default to the raw pwmN identifier.
    BoardKey(
        vendor="ASRock",
        board_glob="Z690 Extreme",
        chip="nct6798",
    ): {
        "pwm1": FallbackLabel("Chassis fan3", verified=True),
        "pwm2": FallbackLabel("CPU fan1", verified=True),
        "pwm3": FallbackLabel("CPU fan2", verified=True),
        "pwm4": FallbackLabel("Chassis fan1", verified=True),
        "pwm5": FallbackLabel("Chassis fan2", verified=True),
        "pwm6": FallbackLabel("Chassis fan4", verified=True),
        "pwm7": FallbackLabel("Chassis fan5", verified=True),
    },
}


# ─── Libsensors config parser ──────────────────────────────────────────


# Standard libsensors search locations (libsensors 3.x). 3.5/3.6 are the
# canonical subdirs on most distros.
LIBSENSORS_CONFIG_PATHS: list[str] = [
    "/etc/sensors.conf",
    "/etc/sensors3.conf",
    "/etc/sensors.d/*.conf",
    "/usr/share/sensors/*.conf",
    "/usr/share/sensors/3.5/*.conf",
    "/usr/share/sensors/3.6/*.conf",
]


@dataclass
class LibsensorsChipLabels:
    """Parsed labels from a single libsensors ``chip "<glob>" { … }`` block."""

    chip_glob: str
    labels: dict[str, str]
    ignored: set[str]


# Top-level chip line. We keep the rest of the line for multi-glob splitting.
_CHIP_RE = re.compile(r"^\s*chip\s+(.+?)\s*$")
# Label line: label <feature> "<text>"
_LABEL_RE = re.compile(r'^\s*label\s+(\S+)\s+"(.+)"\s*$')
# Ignore line: ignore <feature>
_IGNORE_RE = re.compile(r"^\s*ignore\s+(\S+)\s*$")


def parse_libsensors_config(text: str) -> list[LibsensorsChipLabels]:
    """Parse the chip-block label entries out of a libsensors config.

    Returns one ``LibsensorsChipLabels`` per chip glob declared (a single
    config line may declare multiple chips that share the rest of the
    block's label/ignore directives).
    """
    chips: list[LibsensorsChipLabels] = []
    current_globs: list[str] = []
    current_labels: dict[str, str] = {}
    current_ignored: set[str] = set()

    def flush() -> None:
        nonlocal current_labels, current_ignored
        for glob in current_globs:
            chips.append(
                LibsensorsChipLabels(
                    chip_glob=glob,
                    labels=dict(current_labels),
                    ignored=set(current_ignored),
                )
            )
        current_labels = {}
        current_ignored = set()

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        m = _CHIP_RE.match(line)
        if m:
            flush()
            current_globs = _split_chip_globs(m.group(1))
            continue
        m = _LABEL_RE.match(line)
        if m and current_globs:
            current_labels[m.group(1)] = _unescape(m.group(2))
            continue
        m = _IGNORE_RE.match(line)
        if m and current_globs:
            current_ignored.add(m.group(1))
            continue
        # Anything else (compute, set, bus, brackets) — out of scope.

    flush()
    return chips


def _split_chip_globs(text: str) -> list[str]:
    """Pull every quoted glob out of a chip line. Multiple globs may
    appear on one line, each in its own pair of quotes."""
    out: list[str] = []
    in_quote = False
    buf: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            if in_quote:
                out.append("".join(buf))
                buf = []
                in_quote = False
            else:
                in_quote = True
            i += 1
            continue
        if in_quote:
            if c == "\\" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 2
                continue
            buf.append(c)
        i += 1
    return out


def _unescape(text: str) -> str:
    """Drop ``\\<x>`` escapes, keeping ``<x>``. Covers ``\\"`` and ``\\\\``,
    which is everything libsensors actually emits."""
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


# ─── Cache ─────────────────────────────────────────────────────────────


# Module-level cache so the parse runs once per session. Tests and the
# eventual Diagnostics rescan path call ``clear_libsensors_cache()`` to
# force a re-read.
_libsensors_cache: list[LibsensorsChipLabels] | None = None


def load_libsensors_configs(
    paths: list[str] | None = None, *, force: bool = False
) -> list[LibsensorsChipLabels]:
    """Load and parse all installed libsensors configs.

    Args:
        paths: Override the default search list (used by tests).
        force: Re-read even if cached.

    Returns:
        Concatenated parsed chip blocks across all readable files.
        I/O and parse errors log a warning and skip the offending
        file — never fatal.
    """
    global _libsensors_cache
    if paths is None and _libsensors_cache is not None and not force:
        return _libsensors_cache
    chips: list[LibsensorsChipLabels] = []
    search_paths = paths or LIBSENSORS_CONFIG_PATHS
    for pattern in search_paths:
        for path in _expand_glob(pattern):
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError as e:
                log.warning("Could not read sensors config %s: %s", path, e)
                continue
            try:
                chips.extend(parse_libsensors_config(text))
            except Exception as e:  # pragma: no cover — defensive
                log.warning("Failed to parse %s: %s", path, e)
                continue
    if paths is None:
        _libsensors_cache = chips
    return chips


def clear_libsensors_cache() -> None:
    global _libsensors_cache
    _libsensors_cache = None


def _expand_glob(pattern: str) -> list[str]:
    if "*" in pattern or "?" in pattern:
        return sorted(_glob(pattern))
    return [pattern] if Path(pattern).is_file() else []


# ─── Lookup helpers ────────────────────────────────────────────────────


def _match_chip_glob(chip_glob: str, chip_name: str) -> bool:
    """Match a libsensors chip identifier against our hwmon chip name.

    libsensors chip identifiers are formatted ``<name>-<bus>-<addr>``
    (e.g. ``it8688-isa-0a40``, ``nct6798-isa-0290``). For label
    resolution we only care about the name component — matching on
    bus + addr would force every user to author a per-machine config
    and adds nothing for a same-chip-different-address case.
    """
    name_part = chip_glob.split("-", 1)[0]
    return fnmatch.fnmatchcase(chip_name, name_part)


def resolve_label_from_libsensors(
    chip_name: str,
    sensor_name: str,
    *,
    paths: list[str] | None = None,
) -> str | None:
    """Search installed libsensors configs for ``label <sensor> "<text>"``.

    Returns ``None`` if no chip block matches, or if a matching block
    explicitly ignored ``sensor_name``.
    """
    for chip in load_libsensors_configs(paths=paths):
        if not _match_chip_glob(chip.chip_glob, chip_name):
            continue
        if sensor_name in chip.ignored:
            return None
        label = chip.labels.get(sensor_name)
        if label:
            return label
    return None


def resolve_label_from_fallback(
    *,
    vendor: str,
    board_name: str,
    chip_name: str,
    sensor_name: str,
) -> str | None:
    """Look up a label in :data:`HWMON_LABEL_FALLBACK`.

    Returns the display-formatted text (``(unverified)`` suffix
    appended for unverified entries), or ``None`` if no entry matches.
    """
    for key, mapping in HWMON_LABEL_FALLBACK.items():
        if key.vendor and vendor != key.vendor:
            continue
        if key.board_glob and not fnmatch.fnmatchcase(board_name, key.board_glob):
            continue
        if chip_name != key.chip:
            continue
        entry = mapping.get(sensor_name)
        if entry is not None:
            return entry.display()
    return None


def resolve_hwmon_header_label(
    *,
    sysfs_label: str,
    chip_name: str,
    pwm_index: int,
    board_vendor: str = "",
    board_name: str = "",
    sensors_paths: list[str] | None = None,
) -> str:
    """Resolve the best display label for a hwmon PWM header.

    The caller is responsible for the user-alias check (priority 1).
    This function covers priorities 2-5:

        2. daemon-supplied ``sysfs_label`` (may be empty)
        3. libsensors config lookup, ``pwmN`` and ``fanN`` keys
        4. in-repo fallback table
        5. raw ``pwmN`` as the last resort

    Args:
        sysfs_label: ``HwmonHeader.label`` from the daemon (passes
            through if non-empty).
        chip_name: ``HwmonHeader.chip_name`` (e.g. ``it8696``).
        pwm_index: ``HwmonHeader.pwm_index`` (the N in ``pwmN``).
        board_vendor: DMI ``board_vendor`` from
            ``/diagnostics/hardware``. Empty ⇒ skip vendor match.
        board_name: DMI ``board_name``. Empty ⇒ skip name match.
        sensors_paths: Override libsensors search paths (tests only).
    """
    if sysfs_label:
        return sysfs_label
    pwm_key = f"pwm{pwm_index}"
    fan_key = f"fan{pwm_index}"
    # Tier 3 — libsensors. Communities almost always write fan-N labels
    # rather than pwm-N, so check fan first.
    label = resolve_label_from_libsensors(
        chip_name, fan_key, paths=sensors_paths
    ) or resolve_label_from_libsensors(chip_name, pwm_key, paths=sensors_paths)
    if label:
        return label
    # Tier 4 — fallback table. Same fan/pwm preference for consistency.
    label = resolve_label_from_fallback(
        vendor=board_vendor,
        board_name=board_name,
        chip_name=chip_name,
        sensor_name=fan_key,
    ) or resolve_label_from_fallback(
        vendor=board_vendor,
        board_name=board_name,
        chip_name=chip_name,
        sensor_name=pwm_key,
    )
    if label:
        return label
    return pwm_key
