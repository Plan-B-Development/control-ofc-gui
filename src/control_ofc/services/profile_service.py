"""Profile management — CRUD, persistence, and logical controls.

Profiles are GUI-owned. The daemon knows nothing about them.

Data model (v5):
- Profile contains LogicalControls (fan groups with mode) and a CurveConfig library.
- LogicalControl maps to physical outputs via ControlMember.
- CurveConfig supports Graph, Stepped, Linear, and Flat types.
- v4 introduces role-aware ``minimum_pct`` defaults (20% chassis / 30% CPU+pump)
  enforced GUI-side, and the per-member ``fan_zero_rpm`` flag for GPU fans.
- v5 adds the Stepped (staircase) curve type (DEC-148).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from control_ofc.constants import DEFAULT_CURVE_POINTS
from control_ofc.paths import atomic_write, profiles_dir

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Curve types
# ---------------------------------------------------------------------------


class CurveType(Enum):
    GRAPH = "graph"
    STEPPED = "stepped"
    LINEAR = "linear"
    FLAT = "flat"


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

    def interpolate(self, temp_c: float) -> float:
        """Return output percentage for the given temperature."""
        if self.type == CurveType.GRAPH:
            return self._interpolate_graph(temp_c)
        elif self.type == CurveType.STEPPED:
            return self._interpolate_stepped(temp_c)
        elif self.type == CurveType.LINEAR:
            return self._interpolate_linear(temp_c)
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
        return d

    @staticmethod
    def from_dict(data: dict) -> CurveConfig:
        type_str = data.get("type", "graph")
        try:
            curve_type = CurveType(type_str)
        except ValueError:
            log.warning("Unknown curve type '%s', falling back to flat", type_str)
            curve_type = CurveType.FLAT
        points = [CurvePoint(**p) for p in data.get("points", [])]
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


def infer_member_role(member: ControlMember) -> str:
    """Classify a single member into one of the three role buckets."""
    # Intel discrete GPU fans are read-only and never offered as controllable
    # members (DEC-121); the branch is defensive against a hand-edited/legacy
    # profile so such a member still classifies as GPU (0% floor, harmless —
    # the control loop no-ops the write).
    if member.source in ("amd_gpu", "intel_gpu"):
        return CONTROL_ROLE_GPU
    if member.source == "hwmon" and _label_indicates_cpu_or_pump(member.member_label):
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


PROFILE_SCHEMA_VERSION = 5


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


class ProfileService:
    """Manages profile loading, saving, and selection."""

    def __init__(self) -> None:
        self._profiles: dict[str, Profile] = {}
        self._active_id: str = ""

    @property
    def profiles(self) -> list[Profile]:
        return list(self._profiles.values())

    @property
    def active_profile(self) -> Profile | None:
        return self._profiles.get(self._active_id)

    @property
    def active_id(self) -> str:
        return self._active_id

    def load(self) -> list[tuple[str, str]]:
        """Load profiles from disk. Create defaults if none exist.

        Returns a list of ``(path, error_message)`` tuples for every profile
        file that failed to parse. The caller is expected to surface these
        via ``AppState.add_warning`` so Diagnostics shows the failure — prior
        to this, corrupted profiles were silently dropped from the UI.
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
                    self.save_profile(profile)
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
                self.save_profile(p)

        if not self._active_id and self._profiles:
            self._active_id = next(iter(self._profiles))

        return errors

    def save_profile(self, profile: Profile) -> None:
        path = profiles_dir() / f"{profile.id}.json"
        atomic_write(path, json.dumps(profile.to_dict(), indent=2) + "\n")

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
        profile = self._profiles.pop(profile_id)
        path = profiles_dir() / f"{profile.id}.json"
        if path.exists():
            path.unlink()
        if self._active_id == profile_id:
            self._active_id = next(iter(self._profiles), "")
        return True

    def get_profile(self, profile_id: str) -> Profile | None:
        return self._profiles.get(profile_id)

    def profile_path(self, profile_id: str) -> Path:
        """Return the filesystem path for a profile's JSON file."""
        return profiles_dir() / f"{profile_id}.json"
