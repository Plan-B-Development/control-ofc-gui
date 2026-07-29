"""Direct unit tests for the Controls page's candidate-building logic.

These derivations used to live inline in ``ControlsPage._on_edit_members`` /
``_on_configure_aio``, reachable only by constructing a page and driving a modal
dialog. They are pure, and one of them decides ``ControlMember.member_label`` —
a **safety input**, since ``infer_member_role`` and the daemon's
``member_is_pump_or_cpu`` both match "cpu"/"pump"/"aio" against it to apply the
DEC-095/162 30% CPU/pump floor. Testing them headlessly is the point of the
extraction (audit 2026-07-29, rule-6 finding).
"""

from __future__ import annotations

from control_ofc.api.models import FanReading, HwmonHeader
from control_ofc.services.controls_view import (
    assigned_elsewhere_map,
    build_member_candidates,
    build_radiator_candidates,
    build_sensor_choices,
    role_preserving_label,
)


def _fan(fan_id: str, source: str = "hwmon", rpm: int | None = 900) -> FanReading:
    return FanReading(id=fan_id, source=source, rpm=rpm)


def _header(header_id: str, **kw) -> HwmonHeader:
    return HwmonHeader(id=header_id, **kw)


def _names(mapping: dict[str, str]):
    """An AppState-style resolver backed by a dict, defaulting to the id."""
    return lambda fan_id: mapping.get(fan_id, fan_id)


class TestBuildMemberCandidates:
    def test_read_only_hwmon_header_is_never_offered(self):
        """DEC-102: assigning a read-only header produced a 1 Hz EACCES storm."""
        fans = [_fan("h1"), _fan("h2")]
        headers = [
            _header("h1", is_writable=True),
            _header("h2", is_writable=False),
        ]
        rows = build_member_candidates(
            fans,
            headers,
            gpu_writable=True,
            display_name=_names({}),
            fallback_name=_names({}),
        )
        assert [r["id"] for r in rows] == ["h1"]

    def test_intel_and_nvidia_gpu_fans_are_never_offered(self):
        """DEC-121/DEC-204: no kernel write path exists, permanently."""
        fans = [_fan("i", source="intel_gpu"), _fan("n", source="nvidia_gpu")]
        rows = build_member_candidates(
            fans, [], gpu_writable=True, display_name=_names({}), fallback_name=_names({})
        )
        assert rows == []

    def test_read_only_amd_gpu_is_offered_but_flagged(self):
        """Unlike Intel/NVIDIA this is a fixable ppfeaturemask state, so it is
        surfaced rather than hidden."""
        rows = build_member_candidates(
            [_fan("gpu", source="amd_gpu")],
            [],
            gpu_writable=False,
            display_name=_names({"gpu": "9070XT Fan"}),
            fallback_name=_names({}),
        )
        assert len(rows) == 1
        assert "(read-only)" in rows[0]["label"]

    def test_writable_amd_gpu_is_offered_unflagged(self):
        """Twin of the read-only case — the suffix must be conditional, not
        unconditional."""
        rows = build_member_candidates(
            [_fan("gpu", source="amd_gpu")],
            [],
            gpu_writable=True,
            display_name=_names({"gpu": "9070XT Fan"}),
            fallback_name=_names({}),
        )
        assert len(rows) == 1
        assert "(read-only)" not in rows[0]["label"]
        assert rows[0]["clean_label"] == "9070XT Fan"

    def test_clean_label_keeps_the_role_a_user_alias_hides(self):
        """SAFETY (DEC-228): the persisted member_label sets the 30% CPU/pump
        floor on both sides. Renaming a CPU header must not silently drop the
        new control to the 20% chassis floor."""
        rows = build_member_candidates(
            [_fan("h1")],
            [_header("h1", is_writable=True)],
            gpu_writable=True,
            display_name=_names({"h1": "My Fan"}),  # alias hides the role
            fallback_name=_names({"h1": "CPU_OPT"}),  # hardware carries it
        )
        assert rows[0]["label"].startswith("My Fan"), "display must keep the alias"
        assert rows[0]["clean_label"] == "CPU_OPT", "persisted label must keep the role"

    def test_clean_label_keeps_an_alias_that_carries_the_role(self):
        """The converse: where the hardware label is role-less, the alias is the
        only thing that knows this is the pump."""
        rows = build_member_candidates(
            [_fan("h1")],
            [_header("h1", is_writable=True)],
            gpu_writable=True,
            display_name=_names({"h1": "Pump"}),
            fallback_name=_names({"h1": "SYS_FAN2"}),
        )
        assert rows[0]["clean_label"] == "Pump"

    def test_aio_tag_is_appended_to_the_persisted_label_too(self):
        """The AIO suffix is role-bearing ("pump"/"aio" both match the floor
        classifier), so it must reach clean_label, not just the display label."""
        rows = build_member_candidates(
            [_fan("h1")],
            [_header("h1", is_writable=True, is_aio=True)],
            gpu_writable=True,
            display_name=_names({"h1": "Pump"}),
            fallback_name=_names({"h1": "Pump"}),
        )
        assert "(AIO pump)" in rows[0]["label"]
        assert "(AIO pump)" in rows[0]["clean_label"]

    def test_non_hwmon_fan_gets_no_hardware_fallback(self):
        """An OpenFan channel has no header, so the fallback must stay empty —
        otherwise a raw daemon id could be mistaken for a role-bearing name."""
        rows = build_member_candidates(
            [_fan("openfan:ch00", source="openfan")],
            [],
            gpu_writable=True,
            display_name=_names({"openfan:ch00": "Front"}),
            fallback_name=_names({"openfan:ch00": "CPU_FAN"}),  # must be ignored
        )
        assert rows[0]["clean_label"] == "Front"

    def test_writable_header_without_a_fan_reading_is_added_once(self):
        headers = [_header("h1", is_writable=True), _header("h2", is_writable=True)]
        rows = build_member_candidates(
            [_fan("h1")],
            headers,
            gpu_writable=True,
            display_name=_names({}),
            fallback_name=_names({}),
        )
        assert sorted(r["id"] for r in rows) == ["h1", "h2"]
        assert sum(1 for r in rows if r["id"] == "h1") == 1, "no duplicate row"
        h2 = next(r for r in rows if r["id"] == "h2")
        assert h2["rpm"] is None, "a header with no reading must not invent an RPM"

    def test_rpm_is_passed_through_never_invented(self):
        rows = build_member_candidates(
            [_fan("h1", rpm=None)],
            [_header("h1", is_writable=True)],
            gpu_writable=True,
            display_name=_names({}),
            fallback_name=_names({}),
        )
        assert rows[0]["rpm"] is None


class TestAssignedElsewhereMap:
    def test_maps_members_of_other_controls_only(self):
        class _M:
            def __init__(self, member_id):
                self.member_id = member_id

        class _C:
            def __init__(self, cid, name, members):
                self.id, self.name, self.members = cid, name, members

        controls = [
            _C("a", "CPU", [_M("h1"), _M("h2")]),
            _C("b", "Chassis", [_M("h3")]),
        ]
        assert assigned_elsewhere_map(controls, "a") == {"h3": "Chassis"}
        assert assigned_elsewhere_map(controls, "b") == {"h1": "CPU", "h2": "CPU"}


class TestBuildRadiatorCandidates:
    def test_pump_and_gpus_are_excluded(self):
        fans = [
            _fan("pump"),
            _fan("rad"),
            _fan("gpu", source="amd_gpu"),
            _fan("nv", source="nvidia_gpu"),
        ]
        headers = [_header("pump", is_writable=True), _header("rad", is_writable=True)]
        rows = build_radiator_candidates(
            fans,
            headers,
            pump_id="pump",
            preselect_ids=set(),
            display_name=_names({}),
        )
        assert [r["id"] for r in rows] == ["rad"]

    def test_preselect_from_detector_header_flag_or_name(self):
        headers = [
            _header("a", is_writable=True),
            _header("b", is_writable=True, is_aio=True),
            _header("c", is_writable=True),
        ]
        rows = build_radiator_candidates(
            [],
            headers,
            pump_id=None,
            preselect_ids={"a"},
            display_name=_names({"c": "Top Radiator"}),
        )
        by_id = {r["id"]: r for r in rows}
        assert by_id["a"]["preselect"] is True, "detector match"
        assert by_id["b"]["preselect"] is True, "header reports liquid-cooled"
        assert by_id["c"]["preselect"] is True, "name says radiator"

    def test_read_only_header_excluded(self):
        rows = build_radiator_candidates(
            [],
            [_header("ro", is_writable=False)],
            pump_id=None,
            preselect_ids=set(),
            display_name=_names({}),
        )
        assert rows == []


class TestBuildSensorChoices:
    def test_cpu_sensor_is_preferred(self):
        class _S:
            def __init__(self, sid, label, kind, chip="chip"):
                self.id, self.label, self.kind, self.chip_name = sid, label, kind, chip

        rows = build_sensor_choices(
            [_S("s1", "Tctl", "cpu_temp"), _S("s2", "Ambient", "other")],
            overrides={},
        )
        by_id = {r["id"]: r for r in rows}
        assert by_id["s1"]["preferred"] is True
        assert by_id["s2"]["preferred"] is False

    def test_coolant_sensor_is_preferred_via_class_override(self):
        """The second, independent route to `preferred` — classification rather
        than `kind`. It is the one that matters for AIO radiator curves."""

        class _S:
            def __init__(self, sid, label, kind, chip="chip"):
                self.id, self.label, self.kind, self.chip_name = sid, label, kind, chip

        rows = build_sensor_choices(
            [_S("s1", "Coolant", "other")],
            overrides={"s1": "coolant"},
        )
        assert rows[0]["preferred"] is True


class TestRolePreservingLabelAtItsNewHome:
    """The helper moved into the headless layer; the page re-exports it under
    its historical private name for the existing DEC-228 regression tests."""

    def test_non_hwmon_source_keeps_the_display_name(self):
        assert role_preserving_label("Front", "CPU_FAN", "openfan") == "Front"

    def test_empty_fallback_keeps_the_display_name(self):
        assert role_preserving_label("My Fan", "", "hwmon") == "My Fan"

    def test_page_reexport_is_the_same_function(self):
        from control_ofc.ui.pages.controls_page import _role_preserving_label

        assert _role_preserving_label is role_preserving_label
