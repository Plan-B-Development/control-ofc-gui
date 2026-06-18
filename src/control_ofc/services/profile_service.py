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

from control_ofc.api.errors import DaemonError, DaemonTimeout, DaemonUnavailable
from control_ofc.constants import DEFAULT_CURVE_POINTS
from control_ofc.paths import atomic_write, profiles_dir

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient

log = logging.getLogger(__name__)


# Upper bound on points in a single curve. Real curves have a handful of points;
# this guards against a crafted profile exhausting memory / per-tick CPU during
# validation and evaluation (audit P2-C). Generous — far above any real curve.
MAX_CURVE_POINTS = 256


def _is_finite(value: object) -> bool:
    """True only for a real, finite number (rejects NaN/inf, bool, non-numbers)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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
            start_temp_c=data.get("start_temp_c", 30.0),
            start_output_pct=data.get("start_output_pct", 20.0),
            end_temp_c=data.get("end_temp_c", 80.0),
            end_output_pct=data.get("end_output_pct", 100.0),
            flat_output_pct=data.get("flat_output_pct", 50.0),
            trigger_idle_temp_c=data.get("trigger_idle_temp_c", 40.0),
            trigger_load_temp_c=data.get("trigger_load_temp_c", 60.0),
            trigger_idle_pct=data.get("trigger_idle_pct", 30.0),
            trigger_load_pct=data.get("trigger_load_pct", 80.0),
            mix_function=data.get("mix_function", "max"),
            mix_curve_ids=list(data.get("mix_curve_ids", [])),
            sync_control_id=data.get("sync_control_id", ""),
            sync_offset_pct=data.get("sync_offset_pct", 0.0),
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

    source: str = ""  # "openfan" | "hwmon" | "amd_gpu" | "intel_gpu"
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
# below ~20%. The daemon enforces only the 105°C thermal-emergency rule —
# per-fan stall protection is GUI policy via ``LogicalControl.minimum_pct``
# (see DEC-095). Roles are inferred from member labels because the daemon's
# header label is the only authoritative classifier we have.

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
    # Local import mirrors app_state's hwmon_label_resolver usage — keeps the
    # cooler chip-name set in one place without a module-load import cycle.
    from control_ofc.ui.sensor_knowledge import is_liquid_cooler_chip

    parts = member.member_id.split(":")
    chip = parts[1] if len(parts) > 1 else ""
    return is_liquid_cooler_chip(chip)


def infer_member_role(member: ControlMember) -> str:
    """Classify a single member into one of the three role buckets."""
    # Intel discrete GPU fans are read-only and never offered as controllable
    # members (DEC-121); the branch is defensive against a hand-edited/legacy
    # profile so such a member still classifies as GPU (0% floor, harmless —
    # the control loop no-ops the write).
    if member.source in ("amd_gpu", "intel_gpu"):
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
            manual_output_pct=data.get("manual_output_pct", 50.0),
            members=members,
            step_up_pct=data.get("step_up_pct", 100.0),
            step_down_pct=data.get("step_down_pct", 100.0),
            start_pct=data.get("start_pct", 0.0),
            stop_pct=data.get("stop_pct", 0.0),
            offset_pct=data.get("offset_pct", 0.0),
            minimum_pct=data.get("minimum_pct", 0.0),
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

        return Profile(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            description=data.get("description", ""),
            controls=controls,
            curves=curves,
            version=PROFILE_SCHEMA_VERSION,
        )


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
    ``target_id`` through its ``mix_curve_ids`` children."""
    seen: set[str] = set()

    def visit(curve_id: str) -> bool:
        if curve_id in seen:
            return False
        seen.add(curve_id)
        curve = profile.get_curve(curve_id)
        if curve is None or curve.type != CurveType.MIX:
            return False
        return any(child_id == target_id or visit(child_id) for child_id in curve.mix_curve_ids)

    return visit(start_curve_id)


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
    ``target_control_id`` by following Sync control→control edges."""
    by_id = {c.id: c for c in profile.controls}
    seen: set[str] = set()

    def visit(control_id: str) -> bool:
        if control_id in seen:
            return False
        seen.add(control_id)
        control = by_id.get(control_id)
        if control is None:
            return False
        dep = _control_sync_target(profile, control)
        if dep is None:
            return False
        if dep == target_control_id:
            return True
        return visit(dep)

    return visit(start_control_id)


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


def apply_role_floor(control: LogicalControl) -> bool:
    """Raise ``control.minimum_pct`` to its role-derived floor when too low.

    Call this from the UI after the user edits a control's member list so the
    control's minimum tracks the new role. Never lowers an explicit value —
    user-set floors above the role default are preserved. Returns True when
    the value was changed.
    """
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


def detect_aio_setup(
    headers: list, sensors: list, sensor_overrides: dict | None = None
) -> AioDetection:
    """Pure detection for the Configure-AIO flow (DEC-157).

    ``headers`` are live ``HwmonHeader``s (with ``is_aio``/``is_writable``),
    ``sensors`` are live ``SensorReading``s, ``sensor_overrides`` is the user
    coolant-override map. The pump is the writable AIO header labelled "pump"
    (else the lowest pwm index); other writable AIO headers are radiator fans.
    """
    from control_ofc.ui.sensor_knowledge import classify_sensor_with_overrides

    overrides = sensor_overrides or {}
    aio_headers = [h for h in headers if getattr(h, "is_aio", False)]
    writable = [h for h in aio_headers if getattr(h, "is_writable", False)]

    pump_header = None
    if writable:
        pumps = [h for h in writable if "pump" in (h.label or "").lower()]
        pump_header = pumps[0] if pumps else min(writable, key=lambda h: h.pwm_index)

    pump_member = (
        ControlMember(
            source="hwmon", member_id=pump_header.id, member_label=pump_header.label or "Pump"
        )
        if pump_header is not None
        else None
    )
    radiator_members = [
        ControlMember(source="hwmon", member_id=h.id, member_label=h.label or "Radiator")
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
            data = json.loads(path.read_text())
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


class ProfileService:
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
    """

    def __init__(self, client: DaemonClient | None = None) -> None:
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
    def offline(self) -> bool:
        """True when the last daemon load/save fell back to the local cache."""
        return self._offline

    @property
    def daemon_backed(self) -> bool:
        """True when persistence is daemon-backed (a client was supplied), so the
        published/draft distinction is meaningful. False in pure-local mode,
        where every profile is just a local file."""
        return self._client is not None

    @property
    def unpublished_ids(self) -> set[str]:
        """Profile ids written locally but not yet published to the daemon."""
        return set(self._unpublished)

    def is_published(self, profile_id: str) -> bool:
        """True when ``profile_id`` is in the daemon store with no pending
        local-only edits. Always False in pure-local mode."""
        return profile_id in self._daemon_ids and profile_id not in self._unpublished

    def load(self) -> list[tuple[str, str]]:
        """Load profiles, preferring the daemon store when a client is set.

        Returns a list of ``(path_or_id, error_message)`` tuples for every
        profile that failed to parse, for the caller to surface via
        ``AppState.add_warning``. With a daemon client that is reachable,
        profiles come from ``GET /profiles`` and are mirrored into the local
        cache; if the daemon is unreachable the GUI falls back to the local
        cache so it still opens offline. With no client it reads the local
        store directly (pre-migration behaviour).
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

        Returns the per-profile error list on success (daemon reached, even if
        some stored documents failed to parse), or ``None`` when the daemon is
        unreachable so ``load()`` can fall back to the local cache.
        """
        assert self._client is not None
        try:
            documents = self._client.list_profiles()
        except (DaemonUnavailable, DaemonTimeout):
            return None
        except DaemonError as e:
            # Reachable but errored (unexpected for a GET) — surface it as a
            # daemon-side failure rather than masking it as an offline fallback.
            log.warning("Daemon profile listing failed (%s): %s", e.code, e.message)
            return None

        errors: list[tuple[str, str]] = []
        for doc in documents:
            ident = doc.get("id", "<unknown>") if isinstance(doc, dict) else "<unknown>"
            try:
                profile = Profile.from_dict(doc)
            except Exception as e:  # malformed stored document
                log.warning("Failed to parse daemon profile %s: %s", ident, e)
                errors.append((str(ident), str(e)))
                continue
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
                self.save_profile(p)

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
                data = json.loads(path.read_text())
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
                self._write_local(p)

        if not self._active_id and self._profiles:
            self._active_id = next(iter(self._profiles))

        return errors

    def _write_local(self, profile: Profile) -> None:
        """Write a profile to the local cache dir (atomic; 0600 via paths)."""
        path = profiles_dir() / f"{profile.id}.json"
        atomic_write(path, json.dumps(profile.to_dict(), indent=2) + "\n")

    def save_profile(self, profile: Profile) -> None:
        """Persist a profile: always to the local cache, then to the daemon.

        With a daemon client, the local write is the offline mirror/draft and
        the profile is uploaded (replace if it already exists in the store,
        else create). If the daemon is unreachable the edit is kept as a local
        draft (tracked in :attr:`unpublished_ids`) and re-published only when
        the user saves again — there is no background auto-sync (migration
        Decision 3). With no client this is a pure local write.
        """
        self._write_local(profile)
        if self._client is None:
            return
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
            # Keep the local draft so the edit is never lost; the Controls page
            # validates before save (Phase 6c) and surfaces field_violations.
            self._unpublished.add(profile.id)
            log.warning(
                "Profile %s rejected by the daemon (%s): %s — kept as a local draft",
                profile.id,
                e.code,
                e.message,
            )

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
            self._active_id = profile_id
            return True
        return False

    def create_profile(self, name: str) -> Profile:
        p = Profile(name=name)
        self._profiles[p.id] = p
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
        path = profiles_dir() / f"{profile.id}.json"
        if path.exists():
            path.unlink()
        self._daemon_ids.discard(profile_id)
        self._unpublished.discard(profile_id)
        if self._active_id == profile_id:
            self._active_id = next(iter(self._profiles), "")
        return True

    def get_profile(self, profile_id: str) -> Profile | None:
        return self._profiles.get(profile_id)

    def profile_path(self, profile_id: str) -> Path:
        """Return the filesystem path for a profile's JSON file."""
        return profiles_dir() / f"{profile_id}.json"
