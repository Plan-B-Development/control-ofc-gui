"""One-time adoption of profile member labels as fan aliases (DEC-228).

The GUI grew **two** independent per-fan name stores that never reconciled:

* ``AppSettings.fan_aliases`` — keyed by fan id, written by the Fan Wizard and
  the DEC-227 rename surfaces, and read by ``AppState.fan_display_name``, which
  is what every *display* surface uses (Sensors rail, fan cards, Overview table,
  member picker).
* ``ControlMember.member_label`` — keyed by the same id string, written when a
  member is added to a control, and read by the *control* surfaces (control-card
  member rows, fan-role chips, the member editor).

Users who named their fans while building a profile therefore filled the second
store, and every display surface — which only ever consults the first — showed a
fallback instead. DEC-227 made that starker rather than causing it: it replaced
the raw-id fallback with a plausible synthetic name (``OpenFan CH0``), so a
missing label began to read as the app deliberately overriding the user.

This module is the reconciliation: a **pure**, Qt-free function that derives the
aliases a user has effectively already authored. It runs once (gated by
``AppSettings.fan_aliases_seeded``) so that an alias the user clears *after* the
seed is not resurrected on the next launch. An alias
cleared on an older version is indistinguishable from one never set, so the seed
does adopt the profile label for it — once.

The seed deliberately does **not** write ``member_label``. That field feeds
``infer_member_role`` (and its daemon mirror ``member_is_pump_or_cpu``), which
set the DEC-095/162 CPU/pump minimum-PWM floor — leaving it byte-identical keeps
the safety path out of this migration entirely.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from control_ofc.services.app_state import MAX_FAN_ALIAS_LEN
from control_ofc.services.profile_service import Profile

# Decorations the member picker appends for display and that
# ``MemberEditorDialog.get_members()`` has been persisting into ``member_label``
# verbatim. They describe hardware *state*, not the user's name for the fan, so
# they are stripped before a label is adopted as an alias.
#
# Kept as literals rather than imported from ``ui.fan_presence``: this module is
# a data-repair path for labels already on disk, so it must keep recognising a
# badge even if the live UI later reworks its wording. New badges must be added
# here as well as there.
_BADGE_SUFFIXES = (
    "read-only",
    "no fan detected",
    "PWM only — no RPM",
    "PWM only - no RPM",
    "AIO pump",
    "AIO radiator",
)

_TRAILING_BADGE_RE = re.compile(
    r"\s*\((?:" + "|".join(re.escape(b) for b in _BADGE_SUFFIXES) + r")\)\s*$",
    re.IGNORECASE,
)


# Longest label the stripper will consider. The badge regex is an alternation over
# a repeatedly-applied sub, which is superlinear in the input: a crafted 176 kB
# label measured ~58 s, and profiles are untrusted input (a file import, or the
# 0666 daemon store any local user can POST to). The seed runs on the Qt main
# thread during the first poll, so an unclamped label would freeze the GUI on
# every launch — the one-shot flag is never written, so it would never recover.
# Well above MAX_FAN_ALIAS_LEN, so it cannot truncate a legitimate name.
_MAX_STRIP_INPUT = 512

# Belt-and-braces bound on the strip loop. Each firing `sub` removes at least ten
# characters so it already terminates; this caps the work regardless.
_MAX_STRIP_PASSES = 8

# Prefixes of daemon-minted fan ids. A label matching one is an id, not a name —
# see `_is_raw_id`.
_FAN_ID_PREFIXES = ("openfan:", "hwmon:", "amd_gpu:", "intel_gpu:", "nvidia_gpu:")


def _is_raw_id(label: str, member_id: str) -> bool:
    """True when *label* is a daemon fan id rather than a name a user chose.

    Before DEC-227 gave OpenFan channels a readable fallback, ``fan_display_name``
    returned the raw id — and the member picker cached whatever it displayed into
    ``member_label``. So profiles authored on <= v2.27.1 are full of labels like
    ``"openfan:ch00"``. Adopting those would invert this release's whole purpose:
    the rail would show the id it is supposed to replace, and because an alias
    means "keep this fan visible" to ``filter_displayable_fans``, every idle
    channel would also be pinned on screen — permanently, since the seed is
    one-shot.

    The equality check catches the exact case; the prefix check also catches a
    label carrying some *other* fan's id.
    """
    return label == member_id or label.startswith(_FAN_ID_PREFIXES)


def strip_member_label_decorations(label: str) -> str:
    """Remove any trailing picker badges from *label*.

    Applied repeatedly, because the picker can stack two (e.g. a read-only AIO
    header renders as ``"Pump (read-only) (AIO pump)"``).

    **Only safe for deriving a display alias.** This strips the ``(AIO …)`` tag
    too, and that tag is role-bearing where it is *stored*: both
    ``infer_member_role`` and the daemon's ``member_is_pump_or_cpu`` match
    "cpu"/"pump"/"aio" against ``member_label`` to decide the DEC-095/162 30%
    pump floor. Aliases feed only ``fan_display_name``, so full stripping is
    correct here — but never route ``member_label`` through this function.
    The picker keeps the AIO tag in its ``clean_label`` for exactly that reason.
    """
    if not isinstance(label, str):
        return ""  # a hand-edited/hostile profile can carry any JSON type here
    cleaned = label[:_MAX_STRIP_INPUT].strip()
    for _ in range(_MAX_STRIP_PASSES):
        stripped = _TRAILING_BADGE_RE.sub("", cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped.strip()
    return cleaned.strip()


def collect_member_labels(
    profiles: Iterable[Profile],
    active_profile_id: str = "",
) -> dict[str, str]:
    """Map member id -> the best user-authored label across *profiles*.

    The active profile is consulted first because it is the one the user is
    actually looking at; the rest follow in their existing order, which is
    stable across launches. First non-empty label wins, so an older profile can
    supply a name for a fan the active profile never names, but can never
    override one it does.
    """
    ordered = list(profiles)
    if active_profile_id:
        ordered.sort(key=lambda p: p.id != active_profile_id)

    labels: dict[str, str] = {}
    for profile in ordered:
        for control in profile.controls:
            for member in control.members:
                member_id = (member.member_id or "").strip()
                if not member_id or member_id in labels:
                    continue
                cleaned = strip_member_label_decorations(member.member_label)
                if cleaned:
                    labels[member_id] = cleaned
    return labels


def seed_fan_aliases_from_profiles(
    profiles: Iterable[Profile],
    existing_aliases: dict[str, str],
    known_fan_ids: set[str],
    fallback_name: Callable[[str], str],
    active_profile_id: str = "",
) -> dict[str, str]:
    """Return the aliases to *add* — never mutates its arguments.

    An entry is adopted only when all of the following hold:

    * the fan has no alias already (a real alias, including one the user set to
      something different, always wins over a cached profile label);
    * the id names a fan the daemon is currently reporting — this drops members
      left behind by retired id schemes rather than reviving dead names;
    * the label survives decoration-stripping and length-capping non-empty;
    * the label differs from what the fan would display anyway. Adopting a label
      equal to the fallback would be a no-op visually but *not* behaviourally:
      ``fan_display.filter_displayable_fans`` reads "has an alias" as "the user
      wants this fan visible", so it would silently pin an idle header on screen.
      This mirrors the rule ``AppState.apply_fan_rename`` already applies.
    """
    candidates = collect_member_labels(profiles, active_profile_id)
    seeded: dict[str, str] = {}
    for member_id, label in candidates.items():
        if member_id in existing_aliases:
            continue
        if member_id not in known_fan_ids:
            continue
        capped = label[:MAX_FAN_ALIAS_LEN].strip()
        if not capped or capped == fallback_name(member_id):
            continue
        if _is_raw_id(capped, member_id):
            continue
        seeded[member_id] = capped
    return seeded
