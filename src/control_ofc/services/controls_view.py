"""Qt-free view helpers for the Controls page (DEC-214; DEC-219 Phase 7.3).

Pure derivations kept out of the Qt page so they stay headless-testable — no
widget construction, no ``QApplication``. Some pull in ``profile_service`` role
helpers, which transitively loads ``PySide6.QtCore`` (ProfileService is a
QObject), exactly like :mod:`control_ofc.services.fan_cards_view`; the module
still builds no widgets and every function is unit-testable without a display.
No value is fabricated.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from control_ofc.knowledge.sensor_knowledge import classify_sensor_with_overrides
from control_ofc.services.profile_service import (
    CONTROL_ROLE_GPU,
    control_minimum_pct,
    infer_member_role,
)

# The renew timer never fires slower than this floor, so the daemon deadman is
# honoured even for a grant advising a sub-second cadence.
_RENEW_FLOOR_MS = 1000


def unassigned_fan_ids(fans: Iterable, controls: Sequence) -> list[str]:
    """Fan ids that are not a member of any logical control.

    Feeds the "Unassigned Fans (N)" dropzone in the Assign-Roles pane.
    """
    assigned = {m.member_id for c in controls for m in c.members}
    return [f.id for f in fans if f.id not in assigned]


def member_rpm_map(control, fan_readings: dict) -> dict[str, int | None]:
    """Map each of a control's member ids to its live RPM (``None`` when unknown).

    ``fan_readings`` is an id → reading mapping. A member with no reading (or a
    reading without an RPM) maps to ``None`` so the card leaves that RPM column
    blank rather than inventing a value.
    """
    result: dict[str, int | None] = {}
    for member in control.members:
        reading = fan_readings.get(member.member_id)
        result[member.member_id] = getattr(reading, "rpm", None) if reading is not None else None
    return result


def curve_min_output_floor(profile, curve_id: str) -> float:
    """Highest ``minimum_pct`` of any control referencing this curve (0 when none).

    The editor clamps curve points to this so a shared curve can't be authored
    below the strictest role's safe minimum. A freshly-created control still
    contributes its role-aware floor (derived from members) before migration.
    """
    floor = 0.0
    for ctrl in profile.controls:
        if ctrl.curve_id == curve_id:
            effective = max(ctrl.minimum_pct, control_minimum_pct(ctrl.members))
            floor = max(floor, effective)
    return floor


def divergent_gpu_output(control, control_output: float, members: dict) -> float | None:
    """A GPU member's applied output when it diverges from the control-wide value
    (DEC-119), else ``None``.

    Only mixed controls (a GPU grouped with chassis/CPU fans) can diverge: the
    GPU member is re-tuned with a 0% floor and may idle below the control-wide
    floor. A GPU-only control's headline already *is* the GPU value, so it is
    never annotated.
    """
    if not members:
        return None
    if not any(infer_member_role(m) != CONTROL_ROLE_GPU for m in control.members):
        return None
    for m in control.members:
        if infer_member_role(m) == CONTROL_ROLE_GPU:
            gpu_out = members.get(m.target_id)
            if gpu_out is not None and abs(gpu_out - control_output) > 1.0:
                return gpu_out
    return None


def renew_interval_ms(
    renew_secs: dict, fallback_ms: int, floor_ms: int = _RENEW_FLOOR_MS
) -> int | None:
    """The shared renew-timer interval: the MIN cadence across held grants, floored.

    ``None`` when nothing is held (leave the running timer as-is). One timer
    serves every override and ``setInterval`` resets the countdown, so it must
    fire fast enough for the shortest-TTL grant — last-writer-wins on a larger
    ``renew_secs`` could otherwise stretch a renew past an earlier override's
    shorter TTL. Grants missing ``renew_secs`` fall back to ``fallback_ms``.
    """
    if not renew_secs:
        return None
    interval_ms = min((secs * 1000) if secs else fallback_ms for secs in renew_secs.values())
    return max(floor_ms, interval_ms)


def override_rejection_feedback(code: str) -> tuple[str, str] | None:
    """``(message, css_class)`` for a *user-actionable* override rejection, else
    ``None`` (benign races stay a quiet card revert).

    Only two codes tell the user something the card flipping back to auto cannot:
    ``thermal_abort`` (safety is holding the fans) and ``stale_fencing_token``
    (another client superseded this override). Keeping every other code silent is
    exactly what makes a superseded override distinct from a lapsed one (DEC-163).
    """
    if code == "thermal_abort":
        return ("Override blocked — thermal emergency (fans held by safety)", "CriticalChip")
    if code == "stale_fencing_token":
        return ("Override superseded by another client", "WarningChip")
    return None


def sensor_combo_label(s, overrides: dict) -> str:
    """Curve-editor sensor-combo label, starring coolant + CPU sensors (★) — the
    recommended bindings for AIO/radiator curves (DEC-157). Selection stays free;
    this only highlights."""
    val_text = f" — {s.value_c:.1f}°C" if s.value_c is not None else ""
    cls = classify_sensor_with_overrides(
        s.id, chip_name=s.chip_name, label=s.label, overrides=overrides
    )
    preferred = cls.source_class in ("coolant", "coolant_in", "coolant_out") or s.kind in (
        "cpu_temp",
        "CpuTemp",
    )
    star = "★ " if preferred else ""
    return f"{star}{s.label} ({s.kind}){val_text}"


def parse_stored_card_size(raw) -> tuple[int, int] | None:
    """Validate a persisted ``[w, h]`` card-size override; ``None`` if malformed."""
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            return (int(raw[0]), int(raw[1]))
        except (TypeError, ValueError):
            return None
    return None


def prune_card_sizes(sizes: dict, known_ids: set) -> None:
    """Drop card-size overrides for ids absent from ``known_ids`` (mutates in place).

    ``known_ids`` is keyed across *all* profiles (not just the active one) so
    switching profiles never sheds an inactive profile's card sizes.
    """
    for stale in [card_id for card_id in sizes if card_id not in known_ids]:
        del sizes[stale]
