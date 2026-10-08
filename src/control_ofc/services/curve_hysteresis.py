"""Per-curve falling-temperature deadband — the GUI-side rules (DEC-489).

Qt-free, so the embedded curve editor, the curve dialog, the curve card and the
Controls page all read one rule. The daemon is the only evaluator: it holds a
curve's output while the temperature falls less than ``hysteresis_c`` below
where the output last changed, lets go after a short steady hold (DEC-188), and
never holds below what the curve asks for now. The GUI only edits the width and
shows it; demo mode does not simulate it.

The bounds mirror the daemon's ``constants::HYSTERESIS_DEADBAND_C`` and
``HYSTERESIS_DEADBAND_MAX_C``. The daemon's ``validate()`` is what enforces the
range — a drifted copy here shows up as a refused save, never as a silent band.
"""

from __future__ import annotations

from control_ofc.services.profile_service import CurveConfig, CurveType, Profile

#: The daemon's band for a curve that sets none.
HYSTERESIS_DEFAULT_C = 2.0
#: The widest band the daemon accepts.
HYSTERESIS_MAX_C = 10.0
#: Editor step. The daemon accepts any value in range; this keeps the spin box
#: on values a user can reason about.
HYSTERESIS_STEP_C = 0.5

#: The curve types whose evaluation goes through the deadband. Trigger owns its
#: own idle..load band, Mix and Sync bypass the deadband, and a Flat curve's
#: output never changes, so there is nothing to hold.
HYSTERESIS_CURVE_TYPES: frozenset[CurveType] = frozenset(
    {CurveType.GRAPH, CurveType.STEPPED, CurveType.LINEAR}
)

_NOT_APPLICABLE_REASON: dict[CurveType, str] = {
    CurveType.FLAT: "A flat curve's speed never changes, so there is nothing to hold.",
    CurveType.TRIGGER: "A trigger curve has its own idle and load temperatures instead.",
    CurveType.MIX: "A mix curve combines other curves without a band of its own.",
    CurveType.SYNC: "A sync curve mirrors another fan without a band of its own.",
}


def curve_uses_hysteresis(curve_type: CurveType) -> bool:
    """Whether the daemon applies a curve of this type's band."""
    return curve_type in HYSTERESIS_CURVE_TYPES


def not_applicable_reason(curve_type: CurveType) -> str:
    """Why a curve of this type has no band; empty when it has one."""
    return _NOT_APPLICABLE_REASON.get(curve_type, "")


def clamp_hysteresis_c(value: float) -> float:
    """Into the range the daemon accepts — what its engine applies to a wider value."""
    return min(max(value, 0.0), HYSTERESIS_MAX_C)


def effective_hysteresis_c(curve: CurveConfig) -> float:
    """The band the daemon applies to *curve*: its own value, else the default."""
    if curve.hysteresis_c is None:
        return HYSTERESIS_DEFAULT_C
    return curve.hysteresis_c


def apply_hysteresis_to_all(profile: Profile, value_c: float) -> list[str]:
    """Set *value_c* on every curve in *profile* that has a band.

    Returns the ids of the curves whose value changed, so the caller can mark
    the profile unsaved and refresh exactly those cards. Curves without a band
    are left alone rather than given a value nothing reads.
    """
    changed: list[str] = []
    for curve in profile.curves:
        if not curve_uses_hysteresis(curve.type):
            continue
        if curve.hysteresis_c != value_c:
            curve.hysteresis_c = value_c
            changed.append(curve.id)
    return changed


def hysteresis_card_text(curve: CurveConfig, supported: bool) -> str:
    """The band as the curve card's meta line shows it; empty for the default.

    Only a band the user set is shown, so a profile nobody touched looks exactly
    as it did — and only when the daemon applies it (*supported*): an older
    daemon stores the field and runs its default, so showing it there would
    claim a band nothing applies.
    """
    if not supported or curve.hysteresis_c is None or not curve_uses_hysteresis(curve.type):
        return ""
    if curve.hysteresis_c == 0:
        return "no slow-down band"
    return f"{curve.hysteresis_c:g} °C band"


def curve_meta_text(curve: CurveConfig, supported: bool = False) -> str:
    """The curve card's type line: the type, then the band where one was set."""
    if curve.unknown_type is not None:
        return curve.unknown_type
    band = hysteresis_card_text(curve, supported)
    return f"{curve.type.value} · {band}" if band else curve.type.value
