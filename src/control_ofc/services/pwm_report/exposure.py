"""How low the active profile can drive a header — the exposure check (DEC-404).

A test proves behaviour only over the duties it tested. If the profile can
command a duty below the lowest one any test verified with the fan turning, the
report says so, and names the untested band — rather than letting a clean sweep
from 20 % read as a clean bill of health for the 8 % the curve bottoms out at.

**DERIVED, and the rule is stated.** The lowest commandable duty is computed from
the stored profile document the daemon evaluates (its store of record, DEC-160)
in the daemon's own tuning order — ``daemon/src/profile_engine/tuning.rs``:
offset → floor → step-rate → stop-snap → start-kick → clamp. In steady state
step-rate only delays and start-kick only raises, so the floor is::

    L = max(curve_min + offset, max(minimum_pct, header floor))
    L = 0   if stop_pct > 0, L < stop_pct and the header is not hard-floored

A pump-protected header's floor is HARD (DEC-167): it is never stop-snapped.

**Where it cannot be bounded, it says so.** A Mix combines other curves and a
Sync mirrors another control's live output; neither has a minimum that can be
read off its own fields, so the answer is ``None`` with the reason — never a
guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: CurveConfig's own defaults (``services/profile_service.py``), which a stored
#: curve omits for the fields its type does not use.
_DEFAULTS = {
    "start_output_pct": 20.0,
    "end_output_pct": 100.0,
    "flat_output_pct": 50.0,
    "trigger_idle_pct": 30.0,
    "trigger_load_pct": 80.0,
    "manual_output_pct": 50.0,
}


@dataclass(frozen=True)
class Exposure:
    """The lowest duty one control can command on one member, or why not."""

    control_name: str
    lowest_pct: float | None
    #: How ``lowest_pct`` was obtained, or why it could not be.
    basis: str


def _num(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def curve_minimum(curve: Mapping) -> tuple[float | None, str]:
    """The lowest output a single curve can produce, or ``None`` with a reason."""
    kind = str(curve.get("type") or "graph")
    if kind in ("graph", "stepped"):
        outputs = [
            _num(p.get("output_pct"), float("nan"))
            for p in (curve.get("points") or [])
            if isinstance(p, Mapping)
        ]
        outputs = [o for o in outputs if o == o]  # drop NaN (malformed points)
        if not outputs:
            return None, "the curve has no points"
        return min(outputs), f"the lowest point of {kind} curve '{curve.get('name') or ''}'"
    if kind == "linear":
        low = min(
            _num(curve.get("start_output_pct"), _DEFAULTS["start_output_pct"]),
            _num(curve.get("end_output_pct"), _DEFAULTS["end_output_pct"]),
        )
        return low, f"the lower end of linear curve '{curve.get('name') or ''}'"
    if kind == "flat":
        return (
            _num(curve.get("flat_output_pct"), _DEFAULTS["flat_output_pct"]),
            f"flat curve '{curve.get('name') or ''}'",
        )
    if kind == "trigger":
        low = min(
            _num(curve.get("trigger_idle_pct"), _DEFAULTS["trigger_idle_pct"]),
            _num(curve.get("trigger_load_pct"), _DEFAULTS["trigger_load_pct"]),
        )
        return low, f"the idle level of trigger curve '{curve.get('name') or ''}'"
    if kind in ("mix", "sync"):
        return None, (
            f"a {kind.capitalize()} curve depends on other curves at run time, so its "
            "lowest output cannot be read from its own settings"
        )
    return None, f"curve type '{kind}' is not one this version understands"


def member_exposures(
    profile: object,
    member_id: str,
    *,
    header_floor_pct: float | None,
    hard_floor: bool,
) -> list[Exposure]:
    """One :class:`Exposure` per control in *profile* that drives *member_id*."""
    if not isinstance(profile, Mapping):
        return []
    curves = {str(c.get("id")): c for c in (profile.get("curves") or []) if isinstance(c, Mapping)}
    out: list[Exposure] = []
    for control in profile.get("controls") or []:
        if not isinstance(control, Mapping):
            continue
        members = [m for m in (control.get("members") or []) if isinstance(m, Mapping)]
        if not any(m.get("member_id") == member_id for m in members):
            continue
        name = str(control.get("name") or control.get("id") or "")
        if control.get("mode") == "manual":
            raw: float | None = _num(control.get("manual_output_pct"), 50.0)
            basis = "the control's manual output"
        else:
            curve = curves.get(str(control.get("curve_id") or ""))
            if curve is None:
                out.append(Exposure(name, None, "the control's curve is not in the profile"))
                continue
            raw, basis = curve_minimum(curve)
        if raw is None:
            out.append(Exposure(name, None, basis))
            continue
        lowest = raw + _num(control.get("offset_pct"), 0.0)
        floor = max(_num(control.get("minimum_pct"), 0.0), float(header_floor_pct or 0))
        lowest = max(lowest, floor)
        stop = _num(control.get("stop_pct"), 0.0)
        if not hard_floor and stop > 0 and lowest < stop:
            lowest = 0.0
            basis += f", snapped to 0 % below the control's stop threshold ({stop:g} %)"
        out.append(Exposure(name, max(0.0, min(100.0, lowest)), basis))
    return out
