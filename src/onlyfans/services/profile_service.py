"""Profile management — CRUD, persistence, and logical controls.

Profiles are GUI-owned. The daemon knows nothing about them.

Data model (v2):
- Profile contains LogicalControls (fan groups with mode) and a CurveConfig library.
- LogicalControl maps to physical outputs via ControlMember.
- CurveConfig supports Graph, Linear, and Flat types.
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
        if self.type == CurveType.GRAPH:
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

    source: str = ""  # "openfan" | "hwmon"
    member_id: str = ""  # stable daemon ID (e.g. "openfan:ch00", "hwmon:nct6775:pwm1")
    member_label: str = ""  # cached display name

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
        )


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


@dataclass
class Profile:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    controls: list[LogicalControl] = field(default_factory=list)
    curves: list[CurveConfig] = field(default_factory=list)
    version: int = 3

    def get_curve(self, curve_id: str) -> CurveConfig | None:
        for c in self.curves:
            if c.id == curve_id:
                return c
        return None

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
            return _migrate_v1_profile(data)
        # v2→v3: new fields have defaults, no structural migration needed

        controls = [LogicalControl.from_dict(c) for c in data.get("controls", [])]
        curves = [CurveConfig.from_dict(c) for c in data.get("curves", [])]
        return Profile(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            description=data.get("description", ""),
            controls=controls,
            curves=curves,
            version=3,
        )


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
                log.warning("V1 migration: skipping duplicate fan %s", target_id)
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
        version=3,
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

    def load(self) -> None:
        """Load profiles from disk. Create defaults if none exist."""
        d = profiles_dir()
        d.mkdir(parents=True, exist_ok=True)
        loaded = False

        for path in sorted(d.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                profile = Profile.from_dict(data)
                self._profiles[profile.id] = profile
                # Re-save if migrated from v1
                if data.get("version", 1) < 2:
                    self.save_profile(profile)
                    log.info("Migrated profile %s to v2", profile.name)
                loaded = True
            except Exception as e:
                log.warning("Failed to load profile %s: %s", path, e)

        if not loaded:
            for p in default_profiles():
                self._profiles[p.id] = p
                self.save_profile(p)

        if not self._active_id and self._profiles:
            self._active_id = next(iter(self._profiles))

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
