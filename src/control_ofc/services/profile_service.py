"""Profile management — CRUD, persistence, and logical controls.

Profiles are GUI-owned. The daemon knows nothing about them.

Data model (v7):
- Profile contains LogicalControls (fan groups with mode) and a CurveConfig library.
- LogicalControl maps to physical outputs via ControlMember.
- CurveConfig supports Graph, Stepped, Linear, Flat, Trigger, Mix, and Sync types.
- v4 introduces role-aware ``minimum_pct`` defaults (20% chassis / 30% CPU+pump)
  enforced GUI-side, and the per-member ``fan_zero_rpm`` flag for GPU fans.
- v5 adds the Stepped (staircase) curve type (DEC-148).
- v6 adds the Trigger (two-state latch) curve type (DEC-149).
- v7 adds the composite Mix (combine other curves) and Sync (mirror a control's
  output) curve types, retiring the single-sensor rule DEC-014 (DEC-150/151/152).
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
from control_ofc.constants import DEFAULT_CURVE_POINTS
from control_ofc.knowledge.hwmon_label_resolver import is_placeholder_hwmon_label
from control_ofc.knowledge.sensor_knowledge import (
    classify_sensor_with_overrides,
    is_liquid_cooler_chip,
)
from control_ofc.paths import atomic_write, load_json_capped, profiles_dir

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.api.models import HwmonHeader

log = logging.getLogger(__name__)


# Upper bound on points in a single curve. Real curves have a handful of points;
# this guards against a crafted profile exhausting memory / per-tick CPU during
# validation and evaluation (audit P2-C). Generous — far above any real curve.
MAX_CURVE_POINTS = 256

# Upper bounds on a profile's collection sizes. Mirrors the daemon's
# ``MAX_PROFILE_CURVES`` / ``MAX_PROFILE_CONTROLS`` (profile.rs) so a profile one
# side accepts the other does too. These are recursion bounds, not taste limits:
# Mix/Sync dependency walks recurse once per link, so a deep — but perfectly
# ACYCLIC, hence cycle-check-passing — chain used to overflow the daemon's stack
# and abort it. The GUI's own walks are iterative (see ``_mix_reaches``), so this
# cap is about staying in lockstep with the daemon and refusing an absurd profile
# at the parse boundary rather than surfacing it in the editor.
MAX_PROFILE_CURVES = 256
MAX_PROFILE_CONTROLS = 256


def _is_finite(value: object) -> bool:
    """True only for a real, finite number (rejects NaN/inf, bool, non-numbers)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_profile_size(data: dict) -> None:
    """Reject a profile whose collections exceed the daemon-mirrored caps.

    Reads the *raw* payload so a non-list (or absent) entry is simply ignored
    here — shape errors belong to the per-item parsers.

    ``assignments`` is checked too, and that is not cosmetic: a v1 document
    carries neither ``curves`` nor ``controls``, so checking only those two made
    this a no-op for the v1 branch — and ``_migrate_v1_profile`` then builds one
    curve *and* one control per assignment directly, bypassing both these caps
    and ``MAX_CURVE_POINTS``. Bounded only by the 4 MB import cap, a crafted
    legacy bundle could inflate to millions of dataclasses and hang the GUI.
    """
    if isinstance(data.get("curves"), list) and len(data["curves"]) > MAX_PROFILE_CURVES:
        raise ValueError(
            f"profile has too many curves: {len(data['curves'])} > {MAX_PROFILE_CURVES}"
        )
    if isinstance(data.get("controls"), list) and len(data["controls"]) > MAX_PROFILE_CONTROLS:
        raise ValueError(
            f"profile has too many controls: {len(data['controls'])} > {MAX_PROFILE_CONTROLS}"
        )
    # One assignment migrates to one control AND one curve, so the tighter of the
    # two caps applies.
    max_assignments = min(MAX_PROFILE_CURVES, MAX_PROFILE_CONTROLS)
    if isinstance(data.get("assignments"), list) and len(data["assignments"]) > max_assignments:
        raise ValueError(
            f"profile has too many assignments: {len(data['assignments'])} > {max_assignments}"
        )


def _require_finite(value: object, name: str) -> float:
    """Return *value* if it is a real finite number; else raise ``ValueError``.

    Extends the curve-point ``_is_finite`` guard to the scalar profile fields so a
    crafted/corrupt import carrying ``NaN``/``Infinity``/``1e400`` (or a non-number)
    is rejected at load rather than poisoning evaluation/serialisation (P3 / DEC-172)."""
    if not _is_finite(value):
        raise ValueError(f"{name!r} is non-finite or non-numeric: {value!r}")
    return value


def _opt(data: dict, key: str, default: float) -> object:
    """Optional-field read that treats an explicit JSON ``null`` as absent.

    The daemon models the optional curve scalars as ``Option<f64>`` and accepts —
    then stores verbatim — a document carrying ``"start_temp_c": null`` (its
    guards are ``if let Some(v)``), so external tooling can legitimately hand the
    GUI one. ``null`` therefore means "use the default", not "fail the whole
    profile load". A present non-null value flows through unchanged, so the
    caller's ``_require_finite`` still rejects NaN/inf/non-numeric garbage."""
    value = data.get(key, default)
    return default if value is None else value


# ---------------------------------------------------------------------------
# Curve types
# ---------------------------------------------------------------------------


class CurveType(Enum):
    GRAPH = "graph"
    STEPPED = "stepped"
    LINEAR = "linear"
    FLAT = "flat"
    TRIGGER = "trigger"
    MIX = "mix"
    SYNC = "sync"


# Mix combine functions (FanControl parity). Ordered for the UI dropdown.
MIX_FUNCTIONS: tuple[str, ...] = ("max", "min", "average", "sum", "subtract")


@dataclass
class CurvePoint:
    temp_c: float
    output_pct: float


@dataclass
class CurveConfig:
    """A named, typed curve in the profile's curve library."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    type: CurveType = CurveType.GRAPH
    sensor_id: str = ""

    # Graph type
    points: list[CurvePoint] = field(default_factory=list)

    # Linear type (2-point interpolation)
    start_temp_c: float = 30.0
    start_output_pct: float = 20.0
    end_temp_c: float = 80.0
    end_output_pct: float = 100.0

    # Flat type (constant output)
    flat_output_pct: float = 50.0

    # Trigger type (two-state latch with its own idle..load hysteresis band)
    trigger_idle_temp_c: float = 40.0
    trigger_load_temp_c: float = 60.0
    trigger_idle_pct: float = 30.0
    trigger_load_pct: float = 80.0

    # Mix type (combine other curves' outputs — DEC-150). Each child is
    # evaluated at its own sensor; the results are combined by ``mix_function``
    # and clamped 0-100. ``mix_curve_ids`` references CurveConfig.id values in
    # the same profile.
    mix_function: str = "max"  # one of MIX_FUNCTIONS
    mix_curve_ids: list[str] = field(default_factory=list)

    # Sync type (mirror another control's tuned output — DEC-151).
    # ``sync_control_id`` references LogicalControl.id in the same profile;
    # ``sync_offset_pct`` is added to that control's current-tick output.
    sync_control_id: str = ""
    sync_offset_pct: float = 0.0

    def interpolate(self, temp_c: float) -> float:
        """Return output percentage for the given temperature.

        Pure function of one temperature — serves graph/stepped/linear/flat and
        the Trigger cold-start value (the stateless ``curve_eval`` parity tier).
        Mix and Sync are NOT pure functions of a single temperature (they need a
        multi-curve / cross-control evaluation context, supplied by the control
        loop's resolver), so they fall through to the constant ``flat_output_pct``
        fallback here — never the path used to drive fans for those types."""
        if self.type == CurveType.GRAPH:
            return self._interpolate_graph(temp_c)
        elif self.type == CurveType.STEPPED:
            return self._interpolate_stepped(temp_c)
        elif self.type == CurveType.LINEAR:
            return self._interpolate_linear(temp_c)
        elif self.type == CurveType.TRIGGER:
            return self._interpolate_trigger(temp_c)
        return self.flat_output_pct

    def _interpolate_graph(self, temp_c: float) -> float:
        if not self.points:
            return 50.0
        if temp_c <= self.points[0].temp_c:
            return self.points[0].output_pct
        if temp_c >= self.points[-1].temp_c:
            return self.points[-1].output_pct
        for i in range(len(self.points) - 1):
            p0, p1 = self.points[i], self.points[i + 1]
            if p0.temp_c <= temp_c <= p1.temp_c:
                t = (temp_c - p0.temp_c) / (p1.temp_c - p0.temp_c) if p1.temp_c != p0.temp_c else 0
                return p0.output_pct + t * (p1.output_pct - p0.output_pct)
        return self.points[-1].output_pct

    def _interpolate_stepped(self, temp_c: float) -> float:
        """Staircase interpolation: hold each point's output until the next
        point's temperature is reached (lower-point-wins). Shares the Graph
        point model; only the fill rule differs. Must stay byte-for-byte
        identical to the daemon's ``evaluate_stepped`` (DEC-126 / DEC-148)."""
        if not self.points:
            return 50.0
        if temp_c <= self.points[0].temp_c:
            return self.points[0].output_pct
        if temp_c >= self.points[-1].temp_c:
            return self.points[-1].output_pct
        for i in range(len(self.points) - 1):
            if self.points[i].temp_c <= temp_c < self.points[i + 1].temp_c:
                return self.points[i].output_pct
        return self.points[-1].output_pct

    def _interpolate_linear(self, temp_c: float) -> float:
        if temp_c <= self.start_temp_c:
            return self.start_output_pct
        if temp_c >= self.end_temp_c:
            return self.end_output_pct
        span = self.end_temp_c - self.start_temp_c
        if span == 0:
            return self.start_output_pct
        t = (temp_c - self.start_temp_c) / span
        return self.start_output_pct + t * (self.end_output_pct - self.start_output_pct)

    def _interpolate_trigger(self, temp_c: float) -> float:
        """Stateless (cold-start) trigger output: the load speed at/above the
        load temperature, else the idle speed. The latching hysteresis — holding
        the load state down through the idle..load band — is applied per-control
        by the control loop (which owns cross-cycle state), NOT here, so
        ``interpolate`` stays a pure function for previews and the ``curve_eval``
        parity tier. Must match the daemon's ``evaluate_trigger_stateless``
        (DEC-126 / DEC-149)."""
        if temp_c >= self.trigger_load_temp_c:
            return self.trigger_load_pct
        return self.trigger_idle_pct

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "sensor_id": self.sensor_id,
        }
        if self.type in (CurveType.GRAPH, CurveType.STEPPED):
            d["points"] = [asdict(p) for p in self.points]
        elif self.type == CurveType.LINEAR:
            d["start_temp_c"] = self.start_temp_c
            d["start_output_pct"] = self.start_output_pct
            d["end_temp_c"] = self.end_temp_c
            d["end_output_pct"] = self.end_output_pct
        elif self.type == CurveType.FLAT:
            d["flat_output_pct"] = self.flat_output_pct
        elif self.type == CurveType.TRIGGER:
            d["trigger_idle_temp_c"] = self.trigger_idle_temp_c
            d["trigger_load_temp_c"] = self.trigger_load_temp_c
            d["trigger_idle_pct"] = self.trigger_idle_pct
            d["trigger_load_pct"] = self.trigger_load_pct
        elif self.type == CurveType.MIX:
            d["mix_function"] = self.mix_function
            d["mix_curve_ids"] = list(self.mix_curve_ids)
        elif self.type == CurveType.SYNC:
            d["sync_control_id"] = self.sync_control_id
            d["sync_offset_pct"] = self.sync_offset_pct
        return d

    @staticmethod
    def from_dict(data: dict) -> CurveConfig:
        type_str = data.get("type", "graph")
        try:
            curve_type = CurveType(type_str)
        except ValueError:
            log.warning("Unknown curve type '%s', falling back to flat", type_str)
            curve_type = CurveType.FLAT
        raw_points = data.get("points", [])
        if len(raw_points) > MAX_CURVE_POINTS:
            raise ValueError(f"curve has too many points: {len(raw_points)} > {MAX_CURVE_POINTS}")
        points = [CurvePoint(**p) for p in raw_points]
        for p in points:
            if not _is_finite(p.temp_c) or not _is_finite(p.output_pct):
                raise ValueError("curve point has non-finite or non-numeric values")
        return CurveConfig(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            type=curve_type,
            sensor_id=data.get("sensor_id", ""),
            points=points,
            # The ten optional scalars are daemon-side ``Option<f64>``: ``_opt``
            # maps an explicit JSON ``null`` to the default (matching the daemon,
            # which stores such a document verbatim) while ``_require_finite``
            # still rejects any present non-null garbage.
            start_temp_c=_require_finite(_opt(data, "start_temp_c", 30.0), "start_temp_c"),
            start_output_pct=_require_finite(
                _opt(data, "start_output_pct", 20.0), "start_output_pct"
            ),
            end_temp_c=_require_finite(_opt(data, "end_temp_c", 80.0), "end_temp_c"),
            end_output_pct=_require_finite(_opt(data, "end_output_pct", 100.0), "end_output_pct"),
            flat_output_pct=_require_finite(_opt(data, "flat_output_pct", 50.0), "flat_output_pct"),
            trigger_idle_temp_c=_require_finite(
                _opt(data, "trigger_idle_temp_c", 40.0), "trigger_idle_temp_c"
            ),
            trigger_load_temp_c=_require_finite(
                _opt(data, "trigger_load_temp_c", 60.0), "trigger_load_temp_c"
            ),
            trigger_idle_pct=_require_finite(
                _opt(data, "trigger_idle_pct", 30.0), "trigger_idle_pct"
            ),
            trigger_load_pct=_require_finite(
                _opt(data, "trigger_load_pct", 80.0), "trigger_load_pct"
            ),
            mix_function=data.get("mix_function", "max"),
            mix_curve_ids=list(data.get("mix_curve_ids", [])),
            sync_control_id=data.get("sync_control_id", ""),
            sync_offset_pct=_require_finite(_opt(data, "sync_offset_pct", 0.0), "sync_offset_pct"),
        )


# ---------------------------------------------------------------------------
# Logical controls
# ---------------------------------------------------------------------------


class ControlMode(Enum):
    CURVE = "curve"
    MANUAL = "manual"


@dataclass
class ControlMember:
    """A physical fan output assigned to a logical control."""

    source: str = ""  # "openfan" | "hwmon" | "amd_gpu" | "intel_gpu" | "nvidia_gpu"
    member_id: str = ""  # stable daemon ID (e.g. "openfan:ch00", "hwmon:nct6775:pwm1")
    member_label: str = ""  # cached display name
    # Per-GPU-member zero-RPM toggle (v4). When True, the daemon preserves the
    # PMFW ``fan_zero_rpm_enable`` setting when programming the curve, so GPU
    # fans stop below the firmware's idle threshold. False keeps the safe
    # default (zero-RPM disabled, fans always spin). Ignored for non-GPU
    # members. See DEC-095 in the GUI ``DECISIONS.md``.
    fan_zero_rpm: bool = False

    @property
    def target_id(self) -> str:
        """Return the daemon-addressable target ID."""
        return self.member_id

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> ControlMember:
        return ControlMember(
            source=data.get("source", ""),
            member_id=data.get("member_id", ""),
            member_label=data.get("member_label", ""),
            fan_zero_rpm=bool(data.get("fan_zero_rpm", False)),
        )


# ---------------------------------------------------------------------------
# Role inference and minimum-PWM policy (v4)
# ---------------------------------------------------------------------------
#
# Pumps and CPU headers stall below ~30% PWM; chassis/case fans are unsafe
# below ~20%. These per-role floors are GUI policy via
# ``LogicalControl.minimum_pct`` (see DEC-095); the daemon independently
# enforces the pump/CPU 30% floor as a hard backstop at both validate time and
# every eval tick (DEC-162), plus the 105°C thermal-emergency rule. The 20%
# chassis / 0% GPU floors stay GUI-only. Roles are inferred from member labels
# because the *resolved* display name is the best classifier we have — DEC-229
# retired the older claim that the daemon's header label was the authoritative
# one: on a chip that publishes no label file the daemon synthesises `pwmN`,
# which carries no role at all, and the GUI's own board table is what knows the
# header is CPU_FAN.

CONTROL_ROLE_GPU = "gpu"
CONTROL_ROLE_CPU_PUMP = "cpu_or_pump"
CONTROL_ROLE_CHASSIS = "chassis"

ROLE_MINIMUM_PCT: dict[str, float] = {
    CONTROL_ROLE_GPU: 0.0,  # GPU PMFW enforces its own OD_RANGE minimum
    CONTROL_ROLE_CPU_PUMP: 30.0,
    CONTROL_ROLE_CHASSIS: 20.0,
}

_CPU_PUMP_LABEL_HINTS = ("cpu", "pump", "aio")


def _label_indicates_cpu_or_pump(label: str) -> bool:
    """Return True when a hwmon header label looks like a CPU or pump header."""
    if not label:
        return False
    lower = label.lower()
    return any(hint in lower for hint in _CPU_PUMP_LABEL_HINTS)


def _member_is_aio_header(member: ControlMember) -> bool:
    """True when a hwmon member is a liquid-cooler header (NZXT Kraken /
    Aquacomputer), so a pump labelled only ``pwm1`` still gets the 30% pump
    floor (DEC-156).

    Derived from the chip-name segment of the stable id
    (``hwmon:<chip>:<device>:pwmN:<label>``) using the shared cooler set — the
    daemon's ``is_aio`` flag is not carried on a persisted member, so the chip
    embedded in the id is the schema-free signal that also works offline.
    """
    if member.source != "hwmon":
        return False
    parts = member.member_id.split(":")
    chip = parts[1] if len(parts) > 1 else ""
    return is_liquid_cooler_chip(chip)


def infer_member_role(member: ControlMember) -> str:
    """Classify a single member into one of the three role buckets."""
    # Intel (DEC-121) and NVIDIA (DEC-204) discrete GPU fans are read-only and
    # never offered as controllable members; the branch is defensive against a
    # hand-edited/legacy profile so such a member still classifies as GPU (0%
    # floor, harmless — the control loop no-ops the write).
    if member.source in ("amd_gpu", "intel_gpu", "nvidia_gpu"):
        return CONTROL_ROLE_GPU
    if member.source == "hwmon" and (
        _label_indicates_cpu_or_pump(member.member_label) or _member_is_aio_header(member)
    ):
        return CONTROL_ROLE_CPU_PUMP
    return CONTROL_ROLE_CHASSIS


def infer_control_role(members: list[ControlMember]) -> str:
    """Classify a control by its members.

    A control with any CPU/pump member gets the strictest floor; a control
    with only GPU members is GPU; otherwise chassis. Empty controls are
    treated as chassis (the safer default for a brand-new control with no
    members assigned yet).
    """
    if not members:
        return CONTROL_ROLE_CHASSIS
    if any(infer_member_role(m) == CONTROL_ROLE_CPU_PUMP for m in members):
        return CONTROL_ROLE_CPU_PUMP
    if all(infer_member_role(m) == CONTROL_ROLE_GPU for m in members):
        return CONTROL_ROLE_GPU
    return CONTROL_ROLE_CHASSIS


def role_minimum_pct(role: str) -> float:
    """Return the role's default ``minimum_pct``."""
    return ROLE_MINIMUM_PCT.get(role, 0.0)


def control_minimum_pct(members: list[ControlMember]) -> float:
    """Convenience: role-derived minimum_pct for a member list."""
    return role_minimum_pct(infer_control_role(members))


def member_minimum_pct(control: LogicalControl, member: ControlMember) -> float:
    """Effective minimum-PWM floor for a single member of ``control`` (DEC-119).

    GPU members are never floored above 0 by the GUI: the GPU's PMFW firmware
    enforces its own OD_RANGE minimum (~15% on RDNA3+), so a hard GUI floor is
    redundant and, in a *mixed* control (a GPU fan grouped with chassis/CPU
    fans), it would needlessly stop the GPU fan from idling. The user's intent
    — "GPU does not need any hard floor" — is honoured here regardless of how
    the control is composed.

    Non-GPU members honour the control-wide ``minimum_pct`` (already the
    strictest role floor across the control's members, set by
    :func:`apply_role_floor`). Because that value is always ``>=`` a non-GPU
    member's own role floor, this function only ever *lowers* the floor for
    GPU members and is byte-for-byte identical to the pre-DEC-119 control-wide
    behaviour for every non-GPU member and every homogeneous control.
    """
    role = infer_member_role(member)
    if role == CONTROL_ROLE_GPU:
        return role_minimum_pct(CONTROL_ROLE_GPU)  # 0.0 — no GUI floor for GPU
    return max(control.minimum_pct, role_minimum_pct(role))


@dataclass
class LogicalControl:
    """A user-defined control group with mode and member list."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    mode: ControlMode = ControlMode.CURVE
    curve_id: str = ""  # references CurveConfig.id in the same profile
    manual_output_pct: float = 50.0
    members: list[ControlMember] = field(default_factory=list)

    # Tuning parameters (applied post-evaluation in the control loop)
    step_up_pct: float = 100.0  # max increase per cycle (% per second)
    step_down_pct: float = 100.0  # max decrease per cycle
    start_pct: float = 0.0  # kickstart value when resuming from 0%
    stop_pct: float = 0.0  # below this, snap to 0%
    offset_pct: float = 0.0  # fixed offset added to curve output
    minimum_pct: float = 0.0  # hard floor

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode.value,
            "curve_id": self.curve_id,
            "manual_output_pct": self.manual_output_pct,
            "members": [m.to_dict() for m in self.members],
            "step_up_pct": self.step_up_pct,
            "step_down_pct": self.step_down_pct,
            "start_pct": self.start_pct,
            "stop_pct": self.stop_pct,
            "offset_pct": self.offset_pct,
            "minimum_pct": self.minimum_pct,
        }

    @staticmethod
    def from_dict(data: dict) -> LogicalControl:
        mode = ControlMode(data.get("mode", "curve"))
        members = [ControlMember.from_dict(m) for m in data.get("members", [])]
        return LogicalControl(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            mode=mode,
            curve_id=data.get("curve_id", ""),
            manual_output_pct=_require_finite(
                data.get("manual_output_pct", 50.0), "manual_output_pct"
            ),
            members=members,
            step_up_pct=_require_finite(data.get("step_up_pct", 100.0), "step_up_pct"),
            step_down_pct=_require_finite(data.get("step_down_pct", 100.0), "step_down_pct"),
            start_pct=_require_finite(data.get("start_pct", 0.0), "start_pct"),
            stop_pct=_require_finite(data.get("stop_pct", 0.0), "stop_pct"),
            offset_pct=_require_finite(data.get("offset_pct", 0.0), "offset_pct"),
            minimum_pct=_require_finite(data.get("minimum_pct", 0.0), "minimum_pct"),
        )


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


PROFILE_SCHEMA_VERSION = 7


@dataclass
class Profile:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    controls: list[LogicalControl] = field(default_factory=list)
    curves: list[CurveConfig] = field(default_factory=list)
    version: int = PROFILE_SCHEMA_VERSION

    def get_curve(self, curve_id: str) -> CurveConfig | None:
        for c in self.curves:
            if c.id == curve_id:
                return c
        return None

    def sanitize_hwmon_members(
        self,
        writable_header_ids: set[str],
        all_header_ids: set[str] | None = None,
    ) -> int:
        """Drop ``hwmon:`` members that no current header can satisfy (DEC-102).

        Args:
            writable_header_ids: Header ids the daemon reports as
                ``is_writable=True``. Members targeting these are kept.
            all_header_ids: Optional superset including read-only headers,
                used to distinguish "header is gone" from "header is
                read-only" in the log line. When None, every dropped
                member is logged simply as not-currently-writable.

        Returns:
            Number of members dropped across all controls. Callers
            should re-save affected profiles when this is non-zero so
            the cleanup persists across restarts.
        """
        dropped = 0
        for control in self.controls:
            kept: list[ControlMember] = []
            for m in control.members:
                if m.source != "hwmon":
                    kept.append(m)
                    continue
                if m.member_id in writable_header_ids:
                    kept.append(m)
                    continue
                # Member targets an hwmon header that is either missing
                # entirely or present-but-read-only. Both cases mean the
                # control loop will fail every cycle — drop the member.
                reason = (
                    "missing from current hwmon discovery"
                    if all_header_ids is not None and m.member_id not in all_header_ids
                    else "is not writable"
                )
                log.warning(
                    "DEC-102: removing member '%s' (label=%r) from control '%s' — header %s",
                    m.member_id,
                    m.member_label,
                    control.name or control.id,
                    reason,
                )
                dropped += 1
            control.members = kept
        return dropped

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "controls": [c.to_dict() for c in self.controls],
            "curves": [c.to_dict() for c in self.curves],
            "version": self.version,
        }

    @staticmethod
    def from_dict(data: dict) -> Profile:
        version = data.get("version", 1)

        # DEC-223: reject an unsafe id at the parse boundary, before either
        # construction path below — the id becomes an on-disk filename under
        # profiles_dir() (see profile_file_path). A missing id stays legal
        # (each path generates one). Callers already surface exceptions from
        # this method as per-profile load errors.
        raw_id = data.get("id")
        if raw_id is not None and (not isinstance(raw_id, str) or not _is_safe_profile_id(raw_id)):
            raise ValueError(f"unsafe profile id: {raw_id!r}")

        # Collection-size caps, in lockstep with the daemon (see
        # MAX_PROFILE_CURVES). Checked on the raw payload so it costs nothing to
        # reject before building objects, and so both construction paths below
        # are covered — including the v1 branch, which carries `assignments`
        # rather than curves/controls. Callers surface this as a load error.
        _check_profile_size(data)

        if version < 2 and "assignments" in data:
            profile = _migrate_v1_profile(data)
            # Apply the v4 floor pass to v1-migrated profiles too so the
            # one-shot upgrade path always lands at the current schema.
            profile.controls = [_migrate_control_to_v4(c) for c in profile.controls]
            # DEC-102 sanitization runs on every load, regardless of schema.
            _drop_dead_hwmon_members(profile.controls)
            return profile
        # v2→v3 was non-structural (new fields with defaults).
        # v3→v4 lifts ``minimum_pct`` to the role-derived floor where the
        # current value is lower, ensuring CPU/pump members never run below
        # 30% on legacy profiles authored before the safety policy existed.

        controls = [LogicalControl.from_dict(c) for c in data.get("controls", [])]
        curves = [CurveConfig.from_dict(c) for c in data.get("curves", [])]

        if version < 4:
            controls = [_migrate_control_to_v4(c) for c in controls]

        # DEC-102: strip members bound to the read-only RDNA3+ amdgpu hwmon
        # header (e.g. ``hwmon:amdgpu:0000:03:00.0:pwm1:pwm1``). The daemon
        # no longer advertises it (Option A), and writes against it returned
        # 503/EACCES every cycle — so any control that bound it produced a
        # 1 Hz error storm. The corresponding GPU fan is still controllable
        # via its ``amd_gpu:`` member; only the dead hwmon shadow is dropped.
        _drop_dead_hwmon_members(controls)

        # DEC-167: a pump must never be configured to stop. The GUI exposes no
        # stop_pct editor, but a hand-edited / imported / legacy profile (any
        # schema version) can carry a non-zero stop on a pump/CPU control — which
        # the engine would clamp and the daemon would reject (PUMP_STOP_FORBIDDEN).
        # Normalise to 0 on every load so the profile is consistent and accepted.
        for control in controls:
            sanitize_pump_stop(control)

        return Profile(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            description=data.get("description", ""),
            controls=controls,
            curves=curves,
            version=PROFILE_SCHEMA_VERSION,
        )


# ---------------------------------------------------------------------------
# Profile id / path safety (DEC-223)
# ---------------------------------------------------------------------------

_MAX_PROFILE_ID_BYTES = 128
"""Byte cap mirroring the daemon's ``MAX_PROFILE_ID_BYTES`` (profile.rs)."""


def _is_safe_profile_id(value: str) -> bool:
    """Whether *value* is usable as a profile id (and thus an on-disk stem).

    Mirrors the daemon's ``is_safe_profile_id`` (``profile.rs``, the store's
    single id-safety rule): non-empty, ≤128 UTF-8 bytes, no ``/`` or ``\\``,
    no ``..``, no Unicode control character (C0/C1/DEL). A profile id names a
    file under :func:`~control_ofc.paths.profiles_dir` (see
    :func:`profile_file_path`), so a separator or dot-dot would address a file
    outside it — DEC-223, the profile-side twin of the DEC-217 theme-name rule.
    """
    if not value or len(value.encode("utf-8")) > _MAX_PROFILE_ID_BYTES:
        return False
    if "/" in value or "\\" in value or ".." in value:
        return False
    return not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in value)


def profile_file_path(profile_id: str) -> Path:
    """Derive the on-disk cache path for *profile_id*.

    The one sanctioned way to turn a profile id into a file path — call sites
    must not hand-compose ``profiles_dir() / f"{id}.json"``. Unsafe ids are
    already rejected at the parse boundary (:meth:`Profile.from_dict`); this
    is the second line of defence for an id that arrives another way, and the
    containment check on the *resolved* destination also catches a
    profiles-dir entry that symlinks outside (DEC-223; same posture as
    ``theme_file_path`` / DEC-217).

    Raises ``ValueError`` when *profile_id* has no safe representation.
    """
    if not _is_safe_profile_id(profile_id):
        raise ValueError(f"unsafe profile id: {profile_id!r}")
    base = profiles_dir()
    dest = base / f"{profile_id}.json"
    if not dest.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"profile id escapes {base}: {profile_id!r}")
    return dest


# ---------------------------------------------------------------------------
# Composite-curve cycle prevention (DEC-150 Mix / DEC-151 Sync)
# ---------------------------------------------------------------------------
#
# Mix curves depend on other *curves*; Sync curves depend on other *controls*.
# A dependency cycle is prohibited (DEC-152, retiring DEC-014). The evaluators
# guard cycles at eval time (visited-set / tick-output map → safe fallback);
# these pure helpers let the editor *prevent* a cycle being authored in the
# first place by offering only safe choices. Both are O(V+E) DFS reachability
# over the relevant dependency edges.


def _mix_reaches(profile: Profile, start_curve_id: str, target_id: str) -> bool:
    """True when the Mix curve ``start_curve_id`` transitively includes
    ``target_id`` through its ``mix_curve_ids`` children.

    Iterative by design: a long Mix chain is a legal acyclic graph, so the cycle
    check never fires on it, and the recursive form raised ``RecursionError``
    past ~1000 links — crashing the curve editor on open for a stored profile
    that the daemon would happily hold (audit P3, companion to the daemon's
    stack-overflow P1). An explicit stack has no depth limit.
    """
    seen: set[str] = set()
    stack: list[str] = [start_curve_id]
    while stack:
        curve_id = stack.pop()
        if curve_id in seen:
            continue
        seen.add(curve_id)
        curve = profile.get_curve(curve_id)
        if curve is None or curve.type != CurveType.MIX:
            continue
        for child_id in curve.mix_curve_ids:
            if child_id == target_id:
                return True
            stack.append(child_id)
    return False


def mix_candidate_curves(profile: Profile, mix_curve_id: str) -> list[tuple[str, str]]:
    """``(curve_id, name)`` pairs a Mix curve may include without forming a
    cycle. Excludes itself, Sync curves (Mix does not nest Sync), and any curve
    that transitively depends back on ``mix_curve_id``."""
    out: list[tuple[str, str]] = []
    for c in profile.curves:
        if c.id == mix_curve_id:
            continue
        if c.type == CurveType.SYNC:
            continue
        if _mix_reaches(profile, c.id, mix_curve_id):
            continue
        out.append((c.id, c.name))
    return out


def _control_sync_target(profile: Profile, control: LogicalControl) -> str | None:
    """The control id this control's Sync curve targets, or None when the
    control is not driven by a Sync curve."""
    if control.mode != ControlMode.CURVE:
        return None
    curve = profile.get_curve(control.curve_id)
    if curve is None or curve.type != CurveType.SYNC:
        return None
    return curve.sync_control_id or None


def _sync_reaches(profile: Profile, start_control_id: str, target_control_id: str) -> bool:
    """True when control ``start_control_id`` transitively mirrors
    ``target_control_id`` by following Sync control→control edges.

    Iterative for the same reason as :func:`_mix_reaches` — a Sync chain is
    linear, so depth grows with the number of chained controls.
    """
    by_id = {c.id: c for c in profile.controls}
    seen: set[str] = set()
    current = start_control_id
    while current not in seen:
        seen.add(current)
        control = by_id.get(current)
        if control is None:
            return False
        dep = _control_sync_target(profile, control)
        if dep is None:
            return False
        if dep == target_control_id:
            return True
        current = dep
    return False


def sync_candidate_controls(profile: Profile, sync_curve_id: str) -> list[tuple[str, str]]:
    """``(control_id, name)`` pairs a Sync curve may target without forming a
    cycle. Excludes any control already driven by this Sync curve (its
    *users*) and any control that transitively mirrors back to a user — both
    would close a loop through the new edge."""
    users = {c.id for c in profile.controls if c.curve_id == sync_curve_id}
    out: list[tuple[str, str]] = []
    for c in profile.controls:
        if c.id in users:
            continue
        if any(_sync_reaches(profile, c.id, u) for u in users):
            continue
        out.append((c.id, c.name))
    return out


# DEC-102: known-dead member-id patterns. These ids were advertised by
# pre-DEC-102 daemons that included AMD GPU `pwm1` in hwmon discovery.
# RDNA3+ exposes that file read-only without `pwm1_enable`, so any write
# returned EACCES → 503/retryable, producing a 1 Hz error storm in the
# control loop. Daemon discovery now drops `chip_name == "amdgpu"`, so the
# corresponding member id can never round-trip; sanitizing on load
# repairs profiles that were authored against an older daemon.
_DEAD_HWMON_MEMBER_PREFIXES: tuple[str, ...] = ("hwmon:amdgpu:",)


def _is_known_dead_hwmon_member(member: ControlMember) -> bool:
    """Return True for member ids that are known to be unwritable.

    The list is deliberately conservative — it only covers cases where the
    full prefix proves the id targets a read-only sysfs path. Broader
    sanitization (against the live header list) is handled separately at
    runtime by ``Profile.sanitize_hwmon_members``.
    """
    if member.source != "hwmon":
        return False
    return any(member.member_id.startswith(p) for p in _DEAD_HWMON_MEMBER_PREFIXES)


def _drop_dead_hwmon_members(controls: list[LogicalControl]) -> int:
    """Remove members whose ids match a known-dead hwmon pattern.

    Mutates each control's ``members`` list in place and returns the
    total number of members dropped. Logs a warning for every drop so a
    repaired profile leaves a forensic trail in the journal.
    """
    dropped = 0
    for control in controls:
        kept: list[ControlMember] = []
        for m in control.members:
            if _is_known_dead_hwmon_member(m):
                log.warning(
                    "DEC-102: dropping dead hwmon member '%s' (label=%r) from control '%s' — "
                    "this id refers to an AMD GPU pwm1 file that is read-only on RDNA3+ kernels; "
                    "GPU fan control should be bound via the 'amd_gpu:' member instead",
                    m.member_id,
                    m.member_label,
                    control.name or control.id,
                )
                dropped += 1
                continue
            kept.append(m)
        control.members = kept
    return dropped


def _migrate_control_to_v4(control: LogicalControl) -> LogicalControl:
    """Apply the role-aware ``minimum_pct`` floor to a v3-or-older control.

    Never lowers an explicit user-set value — only raises ``minimum_pct`` to
    meet the role policy. Controls with no members get no change; the floor
    is reapplied automatically when members are added through the UI.
    """
    role_floor = control_minimum_pct(control.members)
    if role_floor > control.minimum_pct:
        log.info(
            "Profile migration: control '%s' minimum_pct %.0f → %.0f (%s policy)",
            control.name or control.id,
            control.minimum_pct,
            role_floor,
            infer_control_role(control.members),
        )
        control.minimum_pct = role_floor
    return control


def sanitize_pump_stop(control: LogicalControl) -> bool:
    """Force ``stop_pct`` to 0 on a control with any pump/CPU member (DEC-167).

    A pump must never be configured to stop — coolant-flow loss leads to rapid
    thermal runaway. The GUI exposes no ``stop_pct`` editor, so a non-zero value
    only arrives via a hand-edited / imported / legacy profile. Mirrors the
    daemon's ``PUMP_STOP_FORBIDDEN`` validate rule and its eval-time stop-snap
    exemption, so a loaded profile is never sent to the daemon only to be
    rejected, nor silently carried with a dangerous field. Returns True when the
    value was changed.
    """
    if control.stop_pct != 0.0 and any(
        infer_member_role(m) == CONTROL_ROLE_CPU_PUMP for m in control.members
    ):
        control.stop_pct = 0.0
        return True
    return False


def apply_role_floor(control: LogicalControl) -> bool:
    """Raise ``control.minimum_pct`` to its role-derived floor when too low.

    Call this from the UI after the user edits a control's member list so the
    control's minimum tracks the new role. Never lowers an explicit value —
    user-set floors above the role default are preserved. Also sanitises a
    pump/CPU control's ``stop_pct`` to 0 (DEC-167) so adding a pump member to a
    control that carried a stop never leaves a stop configured. Returns True
    when ``minimum_pct`` was raised.
    """
    sanitize_pump_stop(control)
    role_floor = control_minimum_pct(control.members)
    if role_floor > control.minimum_pct:
        control.minimum_pct = role_floor
        return True
    return False


# ---------------------------------------------------------------------------
# AIO guided setup (DEC-157) — pure detection + control/curve creation. Kept
# free of Qt so it is unit-testable; the dialog is a thin UI over this.
# ---------------------------------------------------------------------------

# Radiator-fan default curve (coolant-temperature range). Research: coolant
# idles ~30-34 C and loads ~50-60 C; the chassis 20% floor still applies. The
# curve editor auto-scales its axis to these points — no per-sensor axis code.
_AIO_RADIATOR_CURVE_POINTS: tuple[tuple[float, float], ...] = (
    (30.0, 20.0),
    (40.0, 40.0),
    (50.0, 75.0),
    (55.0, 100.0),
)

# Pump constant-speed presets (DEC-157). A pump runs best at a CONSTANT speed —
# never a temperature curve — so "Configure AIO" offers these flat levels.
AIO_PUMP_PRESETS: tuple[tuple[str, int], ...] = (
    ("Low", 30),
    ("Mid", 60),
    ("High", 80),
    ("Max", 100),
)
AIO_PUMP_DEFAULT_PCT = 80


@dataclass
class AioDetection:
    """What a one-click AIO setup found on this machine (DEC-157)."""

    pump_member: ControlMember | None  # writable AIO pump header, else None
    radiator_members: list[ControlMember]  # other writable AIO fan headers
    coolant_sensor_id: str | None  # best coolant sensor for the radiator curve
    monitor_only: bool  # an AIO is present but no writable pump exists


def _real_header_label(header: HwmonHeader) -> str:
    """A header's label, or ``""`` when the daemon only synthesised one.

    DEC-229: ``HwmonHeader.label`` is never empty — a chip with no
    ``pwmN_label``/``fanN_label`` file gets ``"pwmN"`` invented for it — so the
    ``h.label or <default>`` idiom silently stops producing its default. Here
    that meant an AIO pump on such a board was named ``"pwm1"`` instead of
    ``"Pump"``, and ``member_label`` is the DEC-095/162 floor input, so the
    pump lost its 30% floor and got the 20% chassis one.

    This is deliberately the raw-label check only: ``detect_aio_setup`` is a
    pure function with no board identity to consult, so it cannot reach the
    ``/etc/sensors.d`` or board-table tiers.

    Reads the fields directly rather than via ``getattr`` defaults: a duck-typed
    stand-in that lacks ``pwm_index`` should fail loudly in a test, not silently
    compare against ``"pwm-1"`` and never match.
    """
    label = header.label or ""
    if is_placeholder_hwmon_label(label, header.pwm_index):
        return ""
    return label


def detect_aio_setup(
    headers: list, sensors: list, sensor_overrides: dict | None = None
) -> AioDetection:
    """Pure detection for the Configure-AIO flow (DEC-157).

    ``headers`` are live ``HwmonHeader``s (with ``is_aio``/``is_writable``),
    ``sensors`` are live ``SensorReading``s, ``sensor_overrides`` is the user
    coolant-override map. The pump is the writable AIO header labelled "pump"
    (else the lowest pwm index); other writable AIO headers are radiator fans.
    """
    overrides = sensor_overrides or {}
    aio_headers = [h for h in headers if h.is_aio]
    writable = [h for h in aio_headers if h.is_writable]

    pump_header = None
    if writable:
        pumps = [h for h in writable if "pump" in _real_header_label(h).lower()]
        pump_header = pumps[0] if pumps else min(writable, key=lambda h: h.pwm_index)

    pump_member = (
        ControlMember(
            source="hwmon",
            member_id=pump_header.id,
            member_label=_real_header_label(pump_header) or "Pump",
        )
        if pump_header is not None
        else None
    )
    radiator_members = [
        ControlMember(
            source="hwmon", member_id=h.id, member_label=_real_header_label(h) or "Radiator"
        )
        for h in writable
        if pump_header is None or h.id != pump_header.id
    ]

    coolant_sensor_id = None
    for s in sensors:
        cls = classify_sensor_with_overrides(
            s.id,
            chip_name=getattr(s, "chip_name", ""),
            label=getattr(s, "label", ""),
            overrides=overrides,
        )
        if cls.source_class in ("coolant", "coolant_in", "coolant_out"):
            coolant_sensor_id = s.id
            break

    aio_present = bool(aio_headers) or coolant_sensor_id is not None
    monitor_only = aio_present and pump_member is None
    return AioDetection(pump_member, radiator_members, coolant_sensor_id, monitor_only)


def build_aio_controls(
    profile: Profile,
    *,
    pump_member: ControlMember | None,
    pump_pct: int,
    radiator_members: list[ControlMember],
    radiator_sensor_id: str,
) -> list[LogicalControl]:
    """Create the pump + radiator controls (and their curves) for a one-click
    AIO setup, append them to ``profile``, and return the created controls
    (DEC-157).

    The pump runs at a CONSTANT speed (a Flat curve), never a temperature curve,
    floored at 30% by role policy. The radiator fans get a coolant-range graph
    curve bound to ``radiator_sensor_id``.
    """
    created: list[LogicalControl] = []

    if pump_member is not None:
        pump_curve = CurveConfig(
            name="AIO Pump", type=CurveType.FLAT, flat_output_pct=float(pump_pct)
        )
        profile.curves.append(pump_curve)
        pump_control = LogicalControl(
            name="AIO Pump",
            mode=ControlMode.CURVE,
            curve_id=pump_curve.id,
            members=[pump_member],
        )
        apply_role_floor(pump_control)  # 30% pump floor (DEC-095)
        profile.controls.append(pump_control)
        created.append(pump_control)

    if radiator_members:
        rad_curve = CurveConfig(
            name="AIO Radiator",
            type=CurveType.GRAPH,
            sensor_id=radiator_sensor_id,
            points=[CurvePoint(t, o) for t, o in _AIO_RADIATOR_CURVE_POINTS],
        )
        profile.curves.append(rad_curve)
        rad_control = LogicalControl(
            name="AIO Radiator",
            mode=ControlMode.CURVE,
            curve_id=rad_curve.id,
            members=list(radiator_members),
        )
        apply_role_floor(rad_control)  # 20% chassis floor
        profile.controls.append(rad_control)
        created.append(rad_control)

    return created


# ---------------------------------------------------------------------------
# GPU dedicate guided setup (DEC-221) — pure control/curve creation. Kept free
# of Qt so it is unit-testable; the dialog is a thin UI over this.
# ---------------------------------------------------------------------------

# Default curve for a dedicated GPU fan. The first point sits at 0% up to 45 C
# (graph interpolation clamps to the first point below its temperature), so the
# fan *target* is 0% at idle; the PMFW firmware raises a bare 0% target to its
# OD_RANGE minimum (~15%), so true 0 RPM comes from the member ``fan_zero_rpm``
# stop feature, not the 0% point alone. The ramp then climbs to full by 95 C.
# See DEC-221 (shape chosen by the user).
_GPU_DEDICATE_CURVE_POINTS: tuple[tuple[float, float], ...] = (
    (45.0, 0.0),
    (47.0, 20.0),
    (58.0, 40.0),
    (75.0, 60.0),
    (95.0, 100.0),
)


def build_gpu_control(
    profile: Profile,
    *,
    gpu_member: ControlMember,
    sensor_id: str,
    zero_rpm: bool = True,
) -> LogicalControl | None:
    """Create a dedicated GPU-only control + curve for one writable GPU fan,
    append them to ``profile``, and return the created control (DEC-221).

    "Dedicating" a GPU fan gives it its own GPU-only control (role floor 0%, so
    the curve is authorable all the way down to 0% — no chassis/CPU minimum is
    ever applied) bound to a GPU temperature sensor, with the firmware zero-RPM
    idle-stop enabled (``fan_zero_rpm=zero_rpm``). Enabling zero-RPM is the real
    lever for true 0 RPM at idle: without it the daemon disables the firmware
    stop on every write and the fan always spins.

    The member is first removed from every other control so the fan is never
    driven by two controls at once (a fan must have exactly one writer). A
    control that this removal *empties* — e.g. a previous "Dedicate GPU Fan"
    control that held only this GPU — is dropped, so a repeat dedicate cannot
    accumulate dead empty controls; controls that still hold other members are
    kept, and pre-existing empty controls (that never held this GPU) are left
    untouched. A dropped control's curve stays in the library, unassigned, so a
    hand-tuned curve is never silently lost.

    Returns ``None`` (a no-op) if ``gpu_member`` carries no id — defensive; the
    caller only offers this for a present, writable GPU fan.
    """
    if gpu_member is None or not gpu_member.member_id:
        return None

    kept_controls: list[LogicalControl] = []
    for control in profile.controls:
        had_member = any(m.member_id == gpu_member.member_id for m in control.members)
        control.members = [m for m in control.members if m.member_id != gpu_member.member_id]
        if had_member and not control.members:
            continue  # drop a control this call just vacated (repeat-dedicate)
        kept_controls.append(control)
    profile.controls = kept_controls

    member = ControlMember(
        source=gpu_member.source,
        member_id=gpu_member.member_id,
        member_label=gpu_member.member_label,
        fan_zero_rpm=bool(zero_rpm),
    )
    curve = CurveConfig(
        name="GPU Fan",
        type=CurveType.GRAPH,
        sensor_id=sensor_id,
        points=[CurvePoint(t, o) for t, o in _GPU_DEDICATE_CURVE_POINTS],
    )
    profile.curves.append(curve)
    control = LogicalControl(
        name="GPU Fan",
        mode=ControlMode.CURVE,
        curve_id=curve.id,
        members=[member],
    )
    apply_role_floor(control)  # GPU-only role → minimum_pct stays 0 (DEC-095/DEC-119)
    profile.controls.append(control)
    return control


def _migrate_v1_profile(data: dict) -> Profile:
    """Migrate a v1 profile (TargetAssignment + CurveDefinition) to v2."""
    curves: list[CurveConfig] = []
    controls: list[LogicalControl] = []
    seen_members: set[str] = set()

    for i, a in enumerate(data.get("assignments", [])):
        curve_data = a.get("curve", {})
        points = [CurvePoint(**p) for p in curve_data.get("points", [])]
        curve_id = f"migrated_{i}"
        curve = CurveConfig(
            id=curve_id,
            name=f"Curve {i + 1}",
            type=CurveType.GRAPH,
            sensor_id=curve_data.get("sensor_id", a.get("sensor_id", "")),
            points=points,
        )
        curves.append(curve)

        target_id = a.get("target_id", "")
        target_type = a.get("target_type", "fan")
        name = target_id if target_type == "fan" else f"Group: {target_id}"

        # For specific fan targets, create a member — skip duplicates to
        # prevent conflicting PWM writes from multiple controls.
        members: list[ControlMember] = []
        if target_type == "fan" and target_id:
            if target_id in seen_members:
                log.info("V1 migration: skipping duplicate fan %s", target_id)
            else:
                seen_members.add(target_id)
                if target_id.startswith("openfan"):
                    source = "openfan"
                elif target_id.startswith("amd_gpu:"):
                    source = "amd_gpu"
                else:
                    source = "hwmon"
                members.append(ControlMember(source=source, member_id=target_id))

        control = LogicalControl(
            id=str(uuid.uuid4())[:8],
            name=name,
            mode=ControlMode.CURVE if a.get("enabled", True) else ControlMode.MANUAL,
            curve_id=curve_id,
            members=members,
        )
        controls.append(control)

    return Profile(
        id=data.get("id", str(uuid.uuid4())[:8]),
        name=data.get("name", ""),
        description=data.get("description", ""),
        controls=controls,
        curves=curves,
        version=PROFILE_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Default profiles
# ---------------------------------------------------------------------------


def _default_graph_points(
    low: float, high: float, low_pct: float, high_pct: float
) -> list[CurvePoint]:
    n = DEFAULT_CURVE_POINTS
    points = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0
        temp = low + t * (high - low)
        pct = low_pct + t * (high_pct - low_pct)
        points.append(CurvePoint(temp_c=round(temp, 1), output_pct=round(pct, 1)))
    return points


def default_profiles() -> list[Profile]:
    """Create the three built-in starter profiles."""
    quiet_curve = CurveConfig(
        id="quiet_curve",
        name="Quiet Ramp",
        type=CurveType.GRAPH,
        points=_default_graph_points(30, 80, 25, 60),
    )
    balanced_curve = CurveConfig(
        id="balanced_curve",
        name="Balanced Ramp",
        type=CurveType.GRAPH,
        points=_default_graph_points(30, 80, 30, 80),
    )
    performance_curve = CurveConfig(
        id="perf_curve",
        name="Performance Ramp",
        type=CurveType.GRAPH,
        points=_default_graph_points(30, 75, 50, 100),
    )

    return [
        Profile(
            id="quiet",
            name="Quiet",
            description="Low noise, gentle ramp",
            curves=[quiet_curve],
            controls=[
                LogicalControl(
                    id="quiet_all",
                    name="All Fans",
                    mode=ControlMode.CURVE,
                    curve_id="quiet_curve",
                ),
            ],
        ),
        Profile(
            id="balanced",
            name="Balanced",
            description="Moderate noise and cooling",
            curves=[balanced_curve],
            controls=[
                LogicalControl(
                    id="balanced_all",
                    name="All Fans",
                    mode=ControlMode.CURVE,
                    curve_id="balanced_curve",
                ),
            ],
        ),
        Profile(
            id="performance",
            name="Performance",
            description="Maximum cooling, higher noise",
            curves=[performance_curve],
            controls=[
                LogicalControl(
                    id="perf_all",
                    name="All Fans",
                    mode=ControlMode.CURVE,
                    curve_id="perf_curve",
                ),
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# Profile service
# ---------------------------------------------------------------------------


@dataclass
class ImportCandidate:
    """A local profile migrated to the current schema, ready to upload to the
    daemon's profile store (DEC-161)."""

    source_path: str
    profile_id: str
    name: str
    document: dict


@dataclass
class ImportCollection:
    """Result of scanning the local profiles dir for daemon import.

    ``ready`` are migrated v7 documents to upload; ``failed`` are
    ``(source_path, reason)`` pairs that could not be parsed/migrated —
    quarantined before any upload, never silently dropped.
    """

    ready: list[ImportCandidate] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.ready and not self.failed


def collect_local_profiles_for_import(directory: Path | None = None) -> ImportCollection:
    """Scan the local profiles dir and migrate each file to the current schema.

    Reads ``~/.config/control-ofc/profiles/*.json`` (the GUI's own store), runs
    each file through the existing migration ladder (``Profile.from_dict`` →
    v7) and re-serialises with ``to_dict``. Files that fail to parse/migrate go
    to ``failed`` (pre-upload quarantine) rather than aborting the scan.
    **Originals are only read — never modified or deleted** (rollback path;
    DEC-161). Qt-free so the import flow stays unit-testable.
    """
    coll = ImportCollection()
    d = directory or profiles_dir()
    if not d.exists():
        return coll
    for path in sorted(d.glob("*.json")):
        try:
            data = load_json_capped(path)
            profile = Profile.from_dict(data)
            coll.ready.append(
                ImportCandidate(
                    source_path=str(path),
                    profile_id=profile.id,
                    name=profile.name,
                    document=profile.to_dict(),
                )
            )
        except Exception as e:
            log.warning("Profile %s could not be prepared for import: %s", path, e)
            coll.failed.append((str(path), str(e)))
    return coll


@dataclass
class ProfileActivateOutcome:
    """Result of :meth:`ProfileService.activate` — a small, UI-agnostic verdict
    the pages branch on.

    ``activated`` is the only success signal; ``error`` is a human-readable
    reason on failure (``None`` on success); ``local_only`` marks the no-client
    (demo) path where activation was applied without a daemon confirmation.
    """

    activated: bool
    error: str | None = None
    local_only: bool = False


class ProfileService(QObject):
    """Manages profile loading, saving, and selection.

    Persistence is daemon-backed when a :class:`DaemonClient` is supplied (the
    control-migration model: the daemon owns the authoritative profile store).
    ``load()`` then pulls from ``GET /profiles`` and mirrors each profile into
    the local cache dir so they stay viewable/editable offline; ``save_profile``
    validates-then-uploads and, when the daemon is unreachable, keeps the edit
    as a local draft (no background auto-sync — the user re-saves explicitly;
    migration Decision 3). With ``client=None`` the service is purely local —
    byte-for-byte the pre-migration behaviour — which keeps demo mode and the
    existing unit tests unchanged.

    It is a :class:`QObject` (mirroring :class:`AppState`) so it can emit signals
    when its data changes, letting every page observe profile CRUD / activation
    from one place instead of reaching into each other.
    """

    # active_changed fires when the active profile id changes; profiles_changed
    # fires when the profile *list* changes (create / duplicate / rename / save /
    # delete). Both keep cross-page views (e.g. the dashboard combo) in sync.
    active_changed = Signal(str)  # active profile id
    profiles_changed = Signal()  # the profile list changed (CRUD)

    def __init__(self, client: DaemonClient | None = None) -> None:
        super().__init__()
        self._profiles: dict[str, Profile] = {}
        self._active_id: str = ""
        self._client = client
        # Profile ids known to exist in the daemon store (from the last
        # successful daemon load or upload). Selects create (POST) vs replace
        # (PUT) on save without a probe round-trip.
        self._daemon_ids: set[str] = set()
        # Profile ids written to the local cache but NOT published to the
        # daemon — saved while offline, or rejected on upload. The Controls
        # page badges these as unpublished drafts (Phase 6c). Always empty in
        # pure-local mode (there is no daemon to publish to).
        self._unpublished: set[str] = set()
        # True once a load()/save fell back to the local cache because the
        # daemon was unreachable — the GUI is working against the offline
        # mirror. Cleared on the next successful daemon load.
        self._offline: bool = False

    @property
    def profiles(self) -> list[Profile]:
        return list(self._profiles.values())

    @property
    def active_profile(self) -> Profile | None:
        return self._profiles.get(self._active_id)

    @property
    def active_id(self) -> str:
        return self._active_id

    @property
    def daemon_backed(self) -> bool:
        """True when persistence is daemon-backed (a client was supplied), so the
        published/draft distinction is meaningful. False in pure-local mode,
        where every profile is just a local file."""
        return self._client is not None

    def is_published(self, profile_id: str) -> bool:
        """True when ``profile_id`` is in the daemon store with no pending
        local-only edits. Always False in pure-local mode."""
        return profile_id in self._daemon_ids and profile_id not in self._unpublished

    def load(self) -> list[tuple[str, str]]:
        """Load profiles, preferring the daemon store when a client is set.

        Returns a list of ``(path_or_id, error_message)`` tuples for every
        profile that failed to load, for the caller to surface via
        ``AppState.add_warning``. With a daemon client that is reachable,
        profiles are listed via ``GET /profiles`` then hydrated to their full
        documents via ``GET /profiles/{id}`` (DEC-175) and mirrored into the
        local cache; if the daemon is unreachable the GUI falls back to the
        local cache so it still opens offline. With no client it reads the
        local store directly (pre-migration behaviour).
        """
        if self._client is not None:
            errors = self._load_from_daemon()
            if errors is not None:
                self._offline = False
                return errors
            # Daemon unreachable — fall back to the local mirror so the GUI
            # still opens; edits become drafts until the daemon returns.
            log.info("Daemon unreachable at load — using the local profile cache (offline)")
            self._offline = True
        return self._load_from_local()

    def _load_from_daemon(self) -> list[tuple[str, str]] | None:
        """Pull profiles from the daemon store and mirror them locally.

        ``GET /profiles`` returns lightweight summaries (``id``/``name``/
        ``description`` only — DEC-160), which carry no controls or curves, so
        each listed profile is *hydrated* to its full document via
        ``GET /profiles/{id}`` before parsing (DEC-175). Returns the per-profile
        error list on success (daemon reached, even if some stored documents
        failed to fetch or parse), or ``None`` when the daemon is unreachable —
        at the listing *or* mid-hydration — so ``load()`` can fall back to the
        local cache.
        """
        assert self._client is not None
        try:
            summaries = self._client.list_profiles()
        except (DaemonUnavailable, DaemonTimeout):
            return None
        except DaemonError as e:
            # Reachable but errored (unexpected for a GET) — surface it as a
            # daemon-side failure rather than masking it as an offline fallback.
            log.warning("Daemon profile listing failed (%s): %s", e.code, e.message)
            return None

        errors: list[tuple[str, str]] = []
        hydrated: list[Profile] = []
        for summary in summaries:
            ident = summary.get("id") if isinstance(summary, dict) else None
            if not ident:
                log.warning("Daemon profile summary without an id: %r", summary)
                errors.append(("<unknown>", "profile summary missing 'id'"))
                continue
            try:
                document = self._client.get_profile(ident)
            except (DaemonUnavailable, DaemonTimeout):
                # The daemon went away mid-hydration. Abandon the partial set
                # and fall back to the local cache so the GUI opens with a
                # consistent view rather than an arbitrary subset of profiles.
                log.info(
                    "Daemon became unreachable while hydrating profile %s — "
                    "using the local profile cache (offline)",
                    ident,
                )
                return None
            except DaemonError as e:
                # One profile could not be fetched (e.g. removed between the
                # listing and this fetch → 404). Skip and report it, but keep
                # loading the rest — and never touch its local mirror.
                log.warning("Failed to fetch daemon profile %s (%s): %s", ident, e.code, e.message)
                errors.append((str(ident), e.message))
                continue
            try:
                profile = Profile.from_dict(document)
            except Exception as e:  # malformed stored document
                log.warning("Failed to parse daemon profile %s: %s", ident, e)
                errors.append((str(ident), str(e)))
                continue
            hydrated.append(profile)

        # Commit only fully-hydrated profiles. A failed fetch/parse leaves the
        # existing local mirror untouched, so a transient per-profile error
        # can't clobber a previously-good cached copy (DEC-175).
        for profile in hydrated:
            self._profiles[profile.id] = profile
            self._daemon_ids.add(profile.id)
            self._unpublished.discard(profile.id)
            # Mirror to the local cache (write only — never re-upload) so the
            # profile stays editable while offline.
            self._write_local(profile)

        # A daemon with no stored profiles (fresh install) — seed the built-in
        # starters and publish them, matching local-mode default seeding.
        if not self._profiles:
            for p in default_profiles():
                self._profiles[p.id] = p
                try:
                    self.save_profile(p)
                except Exception as e:
                    # Phase-4 follow-up: a read-only config dir must degrade to
                    # in-memory defaults + a surfaced error, never crash load().
                    log.warning("Failed to persist default profile %s: %s", p.id, e)
                    errors.append((p.id, str(e)))

        if not self._active_id and self._profiles:
            self._active_id = next(iter(self._profiles))
        return errors

    def _load_from_local(self) -> list[tuple[str, str]]:
        """Load profiles from the local cache dir (offline / no-client path).

        The pre-migration loader: migrate each file to the current schema,
        persist migrations/sanitisations to the cache, seed defaults when empty.
        """
        d = profiles_dir()
        d.mkdir(parents=True, exist_ok=True)
        loaded = False
        errors: list[tuple[str, str]] = []

        for path in sorted(d.glob("*.json")):
            try:
                data = load_json_capped(path)
                # Snapshot the on-disk member ids before sanitization so we
                # can detect DEC-102 drops without re-running the pattern
                # match here. Compares against the post-load profile state.
                pre_sanitize_member_ids = {
                    m.get("member_id", "")
                    for c in data.get("controls", [])
                    for m in c.get("members", [])
                }
                profile = Profile.from_dict(data)
                self._profiles[profile.id] = profile
                # Re-save if migrated from any earlier schema version. The
                # v4 migration may also raise ``minimum_pct`` on disk so the
                # change persists across restarts.
                schema_migrated = data.get("version", 1) < PROFILE_SCHEMA_VERSION
                # DEC-102: also re-save when load-time sanitization dropped
                # any members, so the cleanup persists. Without this,
                # every restart would re-detect and re-warn forever.
                post_sanitize_member_ids = {
                    m.member_id for c in profile.controls for m in c.members
                }
                members_sanitized = pre_sanitize_member_ids != post_sanitize_member_ids
                if schema_migrated or members_sanitized:
                    # Local-only write: load() never re-uploads (no auto-sync).
                    self._write_local(profile)
                    if schema_migrated:
                        log.info("Migrated profile %s to v%d", profile.name, PROFILE_SCHEMA_VERSION)
                    if members_sanitized:
                        log.info(
                            "Profile %s persisted after DEC-102 member sanitization",
                            profile.name,
                        )
                loaded = True
            except Exception as e:
                log.warning("Failed to load profile %s: %s", path, e)
                errors.append((str(path), str(e)))

        if not loaded:
            for p in default_profiles():
                self._profiles[p.id] = p
                try:
                    self._write_local(p)
                except Exception as e:
                    # Phase-4 follow-up: a read-only config dir must degrade to
                    # in-memory defaults + a surfaced error, never crash load().
                    log.warning("Failed to write default profile %s: %s", p.id, e)
                    errors.append((p.id, str(e)))

        if not self._active_id and self._profiles:
            self._active_id = next(iter(self._profiles))

        return errors

    def _write_local(self, profile: Profile) -> None:
        """Write a profile to the local cache dir (atomic; 0600 via paths)."""
        path = profile_file_path(profile.id)
        atomic_write(path, json.dumps(profile.to_dict(), indent=2) + "\n")

    def save_profile(self, profile: Profile) -> None:
        """Persist a profile: always to the local cache, then to the daemon.

        With a daemon client, the local write is the offline mirror/draft and
        the profile is uploaded (replace if it already exists in the store,
        else create). If the daemon is unreachable the edit is kept as a local
        draft (tracked internally; see :meth:`is_published`) and re-published
        only when the user saves again — there is no background auto-sync
        (migration Decision 3). With no client this is a pure local write.
        """
        self._write_local(profile)
        if self._client is not None:
            try:
                self._publish(profile)
            except (DaemonUnavailable, DaemonTimeout):
                self._offline = True
                self._unpublished.add(profile.id)
                log.info(
                    "Profile %s saved as a local draft — daemon offline, not published",
                    profile.id,
                )
            except DaemonError as e:
                # Daemon reached but rejected the document (validation / conflict).
                # Keep the local draft so the edit is never lost. Validation is
                # server-side on publish (POST/PUT) — there is no pre-save gate, so
                # the reject reason is not surfaced here; the profile simply stays
                # an unpublished draft until a later save succeeds.
                self._unpublished.add(profile.id)
                log.warning(
                    "Profile %s rejected by the daemon (%s): %s — kept as a local draft",
                    profile.id,
                    e.code,
                    e.message,
                )
        # A save can create, rename, or otherwise mutate the profile list —
        # notify observers once, after the write. create/duplicate reach this via
        # their save_profile call, so they need no separate emit (non-recursive).
        self.profiles_changed.emit()

    def _publish(self, profile: Profile) -> None:
        """Upload a profile to the daemon store (replace existing, else create)."""
        assert self._client is not None
        document = profile.to_dict()
        if profile.id in self._daemon_ids:
            self._client.update_profile(profile.id, document)
        else:
            try:
                self._client.create_profile(document)
            except DaemonError as e:
                if e.code == "already_exists":
                    # The store already has this id (e.g. imported via DEC-161
                    # before this session knew about it) — replace it instead.
                    self._client.update_profile(profile.id, document)
                else:
                    raise
        self._daemon_ids.add(profile.id)
        self._unpublished.discard(profile.id)

    def set_active(self, profile_id: str) -> bool:
        if profile_id in self._profiles:
            if profile_id != self._active_id:
                self._active_id = profile_id
                self.active_changed.emit(profile_id)
            return True
        return False

    def activate(self, profile_id: str, *, client: DaemonClient | None) -> ProfileActivateOutcome:
        """Activate a profile end-to-end: persist it, confirm with the daemon,
        then set it active *locally* — in that order.

        This is the single activation path shared by the Controls and Dashboard
        pages (they own only their own visual feedback). The ordering is
        load-bearing and must not change:

        1. save first, so the daemon reads the latest edited version;
        2. with no client (demo / local mode) set active immediately and report
           ``local_only`` — there is no daemon to confirm with;
        3. otherwise ask the daemon and set active *only after* it confirms, so a
           rejected or failed activation never desyncs local state from the
           daemon (daemon-confirm-before-local).

        ``client`` is passed in rather than read from ``self._client`` because the
        pages hold the live daemon client, whereas the service's own client
        governs profile persistence. Never raises for a daemon error — the reason
        is captured into :attr:`ProfileActivateOutcome.error` for the caller to
        surface. ``set_active`` (fired here) still emits ``active_changed``.
        """
        profile = self.get_profile(profile_id)
        if profile is None:
            return ProfileActivateOutcome(activated=False, error="Profile not found")

        # Save first so the daemon reads the latest version.
        self.save_profile(profile)

        if client is None:
            # Local-only branch (demo mode): no daemon to confirm with.
            self.set_active(profile_id)
            log.debug("No daemon client — profile %s activated locally only", profile_id)
            return ProfileActivateOutcome(activated=True, local_only=True)

        profile_path = str(self.profile_path(profile_id))
        try:
            result = client.activate_profile(profile_path)
        except DaemonError as exc:
            return ProfileActivateOutcome(activated=False, error=exc.message or "unknown error")
        if not result.activated:
            return ProfileActivateOutcome(activated=False, error="Activation rejected by daemon")

        # Local state is updated only after the daemon confirms.
        self.set_active(profile_id)
        return ProfileActivateOutcome(activated=True)

    def create_profile(self, name: str) -> Profile:
        p = Profile(name=name)
        self._profiles[p.id] = p
        # save_profile emits profiles_changed, so create is observed downstream
        # without a second emit here (keeps emissions minimal / non-recursive).
        self.save_profile(p)
        return p

    def duplicate_profile(self, source_id: str, new_name: str) -> Profile | None:
        source = self._profiles.get(source_id)
        if not source:
            return None
        data = source.to_dict()
        data["id"] = str(uuid.uuid4())[:8]
        data["name"] = new_name
        new_profile = Profile.from_dict(data)
        self._profiles[new_profile.id] = new_profile
        self.save_profile(new_profile)
        return new_profile

    def delete_profile(self, profile_id: str) -> bool:
        if profile_id not in self._profiles:
            return False
        if self._client is not None:
            try:
                self._client.delete_profile(profile_id)
            except (DaemonUnavailable, DaemonTimeout):
                # Offline: drop it locally; the daemon copy reconciles on the
                # next online load (activation needs the daemon anyway).
                self._offline = True
            except DaemonError as e:
                if e.code == "profile_in_use":
                    # The daemon is actively running this profile — refuse the
                    # delete rather than desync the GUI from a live profile.
                    log.warning("Cannot delete profile %s — it is active on the daemon", profile_id)
                    return False
                log.warning(
                    "Daemon delete of profile %s failed (%s): %s",
                    profile_id,
                    e.code,
                    e.message,
                )
        profile = self._profiles.pop(profile_id)
        path = profile_file_path(profile.id)
        if path.exists():
            path.unlink()
        self._daemon_ids.discard(profile_id)
        self._unpublished.discard(profile_id)
        if self._active_id == profile_id:
            self._active_id = next(iter(self._profiles), "")
        # delete does not go through save_profile, so emit here for observers.
        self.profiles_changed.emit()
        return True

    def get_profile(self, profile_id: str) -> Profile | None:
        return self._profiles.get(profile_id)

    def reload_profile(self, profile_id: str) -> Profile | None:
        """Re-read one profile from its store, discarding in-memory edits (DEC-233).

        Backs the Controls page's "Revert" action. Prefers the daemon document
        (the store of record — DEC-160) when the profile is published and a
        client is set, falling back to the local cache on any daemon read
        failure; with no client it reads the local cache directly. Replaces the
        in-memory copy and returns the reloaded profile, or ``None`` (leaving the
        in-memory copy untouched) when the profile has never been persisted or
        every read fails — so an unsaved brand-new draft is never silently
        dropped. Does not emit ``profiles_changed``; the caller refreshes its own
        view.
        """
        # Daemon store of record, when this profile is published.
        if self._client is not None and profile_id in self._daemon_ids:
            try:
                document = self._client.get_profile(profile_id)
                profile = Profile.from_dict(document)
            except (DaemonUnavailable, DaemonTimeout):
                profile = None  # offline — fall back to the local mirror
            except Exception as e:
                # Daemon reached but the document errored / parsed badly — a 404
                # after a server-side delete, or a non-dict body (`from_dict`
                # raises AttributeError). Fall back to the local mirror rather than
                # escaping. Broad, matching the sibling hydration parse above.
                log.warning("Reload of profile %s from daemon failed: %s", profile_id, e)
                profile = None
            if profile is not None:
                self._profiles[profile.id] = profile
                try:
                    self._write_local(profile)  # best-effort mirror; never abort the revert
                except OSError as e:
                    log.warning("Mirror write for reloaded profile %s failed: %s", profile_id, e)
                return profile

        # Local cache fallback (offline / no client / unpublished draft). The path
        # build (containment check → ValueError for an unsafe id) and the read are
        # both inside the guard so a bad id / malformed / unreadable file returns
        # None cleanly rather than escaping.
        try:
            path = profile_file_path(profile_id)
            if not path.exists():
                return None
            data = load_json_capped(path)
            profile = Profile.from_dict(data)
        except Exception as e:
            log.warning("Reload of profile %s from local cache failed: %s", profile_id, e)
            return None
        self._profiles[profile.id] = profile
        return profile

    def profile_path(self, profile_id: str) -> Path:
        """Return the filesystem path for a profile's JSON file.

        Raises ``ValueError`` for an id that cannot safely name a file under
        the profiles dir (DEC-223) — ids from this service's own registry
        always can.
        """
        return profile_file_path(profile_id)
