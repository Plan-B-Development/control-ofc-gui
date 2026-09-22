"""The "Your setup" facts: what the user can tell us that software cannot see
(DEC-404 decision 7, S4-6).

Physical wiring — a splitter behind a header, a pump behind a CPU_FAN header,
the BIOS header mode, the position of a pump's own mode switch — is invisible
from sysfs, and every one of them changes what a measurement means. A tach on a
three-fan splitter reports ONE fan; a report that did not know that would call
the other two "verified".

These facts are **USER_METADATA** and are never promoted to an observation.
They are remembered per stable header id in GUI settings, as machine-specific
keys (they describe one machine's wiring and must never travel in a settings
export). Blank means *not supplied*, and the report says so rather than
substituting a default (the brief: "do not hide missing evidence by
substituting defaults").

Qt-free and import-free on purpose: ``app_settings_service`` calls the coercers
below, and the settings layer must not depend on anything heavier.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

#: What is connected to a header. ``""`` is the default and reads "Not sure" in
#: the form and *not supplied* in the report — "not sure" carries no
#: information, so it is recorded as the absence it is.
CONNECTED_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "Not sure"),
    ("nothing", "Nothing"),
    ("fan", "Fan"),
    ("pump", "Pump"),
    ("pump_and_fans", "Pump + fans"),
    ("hub", "Fan hub or controller"),
    ("other", "Other"),
)

#: The BIOS/UEFI mode set for the header. ``""`` reads "Not sure".
BIOS_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "Not sure"),
    ("pwm", "PWM"),
    ("dc", "DC"),
    ("auto", "Auto"),
)

#: Fans behind one header: blank, or 1-8. More than one means a splitter or a
#: hub, which scopes every RPM claim on that header to the one fan whose tach
#: is wired through.
FANS_BEHIND_MAX = 8

#: Length caps. A settings file is untrusted input (DEC-137): an unbounded
#: string would be copied into every report and every export.
NOTES_MAX = 500
MODEL_MAX = 120
SWITCH_MAX = 120
#: Headers remembered. A real board has well under twenty; the cap only stops a
#: hand-edited file making every settings save deep-copy something huge.
MAX_HEADERS = 64

CONNECTED_LABELS: MappingProxyType[str, str] = MappingProxyType(dict(CONNECTED_CHOICES))
BIOS_MODE_LABELS: MappingProxyType[str, str] = MappingProxyType(dict(BIOS_MODE_CHOICES))

#: What the report prints for a blank fact.
NOT_SUPPLIED = "not supplied"


@dataclass(frozen=True)
class HeaderFacts:
    """What the user said about one header. Every field blank = nothing said."""

    connected: str = ""
    fans_behind: int | None = None
    bios_mode: str = ""
    notes: str = ""

    def is_blank(self) -> bool:
        return not (self.connected or self.fans_behind or self.bios_mode or self.notes)

    @property
    def behind_splitter(self) -> bool:
        """More than one fan declared behind the header (a splitter or a hub)."""
        return self.fans_behind is not None and self.fans_behind > 1

    def to_dict(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "fans_behind": self.fans_behind,
            "bios_mode": self.bios_mode,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class CoolerFacts:
    """What the user said about the cooler as a whole (once, not per header)."""

    model: str = ""
    #: A pump's own mode switch, in the manufacturer's words ("manual",
    #: "balanced"…). Free text because every vendor names the positions
    #: differently, and a guessed vocabulary would be wrong for most of them.
    pump_switch: str = ""

    def is_blank(self) -> bool:
        return not (self.model or self.pump_switch)

    def to_dict(self) -> dict[str, str]:
        return {"model": self.model, "pump_switch": self.pump_switch}


def _clean_str(value: object, maxlen: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:maxlen]


def _clean_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= FANS_BEHIND_MAX else None


def header_facts_from(raw: object) -> HeaderFacts:
    """One header's facts from an untrusted mapping. Never raises."""
    if not isinstance(raw, dict):
        return HeaderFacts()
    connected = raw.get("connected")
    bios = raw.get("bios_mode")
    return HeaderFacts(
        connected=connected if connected in CONNECTED_LABELS else "",
        fans_behind=_clean_count(raw.get("fans_behind")),
        bios_mode=bios if bios in BIOS_MODE_LABELS else "",
        notes=_clean_str(raw.get("notes"), NOTES_MAX),
    )


def cooler_facts_from(raw: object) -> CoolerFacts:
    """The cooler facts from an untrusted mapping. Never raises."""
    if not isinstance(raw, dict):
        return CoolerFacts()
    return CoolerFacts(
        model=_clean_str(raw.get("model"), MODEL_MAX),
        pump_switch=_clean_str(raw.get("pump_switch"), SWITCH_MAX),
    )


def coerce_hardware_notes(value: object) -> dict[str, dict[str, object]]:
    """The ``hardware_notes`` settings key, coerced (DEC-137 trust boundary).

    Keyed by the daemon's stable header id. A blank entry is dropped rather than
    stored, so the map only ever holds something the user actually said.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for key, raw in value.items():
        if len(out) >= MAX_HEADERS:
            break
        if not isinstance(key, str) or not key or len(key) > 512:
            continue
        facts = header_facts_from(raw)
        if not facts.is_blank():
            out[key] = facts.to_dict()
    return out


def coerce_cooler_notes(value: object) -> dict[str, str]:
    """The ``cooler_notes`` settings key, coerced. ``{}`` when nothing was said."""
    facts = cooler_facts_from(value)
    return {} if facts.is_blank() else facts.to_dict()


def connected_label(token: str) -> str:
    """Report wording for a ``connected`` token; blank is *not supplied*."""
    return CONNECTED_LABELS.get(token, token) if token else NOT_SUPPLIED


def bios_mode_label(token: str) -> str:
    """Report wording for a ``bios_mode`` token; blank is *not supplied*."""
    return BIOS_MODE_LABELS.get(token, token) if token else NOT_SUPPLIED
