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
aliases a user has effectively already authored. It is run once (gated by
``AppSettings.fan_aliases_seeded``) so that an alias the user later *clears* is
not resurrected on the next launch.

It deliberately does **not** touch ``member_label``. That field feeds
``infer_member_role``, which sets the DEC-095/162 CPU/pump minimum-PWM floor —
leaving it byte-identical keeps the safety path out of this change entirely.
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


def strip_member_label_decorations(label: str) -> str:
    """Remove any trailing picker badges from *label*.

    Applied repeatedly, because the picker can stack two (e.g. a read-only AIO
    header renders as ``"Pump (read-only) (AIO pump)"``).
    """
    cleaned = (label or "").strip()
    while True:
        stripped = _TRAILING_BADGE_RE.sub("", cleaned)
        if stripped == cleaned:
            return cleaned.strip()
        cleaned = stripped.strip()


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
        seeded[member_id] = capped
    return seeded
