"""DEC-102 regression tests — AMD GPU hwmon `pwm1` is excluded from controls.

The fix has three layers (Options A, B, and C). The daemon side is covered
by Rust unit and integration tests; this file covers the GUI side (Option C):

1. The Controls → Edit Members picker filters out hwmon headers whose
   ``is_writable=False`` flag indicates the daemon would reject the write.
2. The profile loader drops members bound to known-dead hwmon ids
   (canonical case: ``hwmon:amdgpu:...``) so existing user profiles repair
   themselves on first launch with the new code.
3. The runtime sanitizer drops members targeting unknown / read-only
   headers when the daemon's authoritative header set arrives.

The failure mode pre-DEC-102 is a 1 Hz 503/EACCES storm in the journal
when the user's ``balanced.json`` binds ``hwmon:amdgpu:0000:03:00.0:pwm1:pwm1``
to the CPU-Cooler control. These tests pin the bug fix.
"""

from __future__ import annotations

import json
import logging

from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    HwmonCapability,
    HwmonHeader,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    LogicalControl,
    Profile,
    ProfileService,
)


def _state_with_headers(headers: list[HwmonHeader]) -> AppState:
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    s.set_capabilities(
        Capabilities(
            hwmon=HwmonCapability(present=True, pwm_header_count=len(headers)),
            amd_gpu=AmdGpuCapability(),
        )
    )
    s.set_hwmon_headers(headers)
    return s


def _hdr(fan_id: str, *, is_writable: bool = True, label: str = "") -> HwmonHeader:
    return HwmonHeader(
        id=fan_id,
        label=label,
        chip_name="it8696" if "amdgpu" not in fan_id else "amdgpu",
        device_id="dev",
        pwm_index=1,
        supports_enable=True,
        rpm_available=True,
        is_writable=is_writable,
    )


# ─── Option C-1: member-picker filter ────────────────────────────────────


class TestEditMembersPickerFiltersUnwritableHeaders:
    """``ControlsPage._on_edit_members`` must hide read-only hwmon headers."""

    def _captured_picker(self, monkeypatch):
        """Monkeypatch ``MemberEditorDialog`` to capture its constructor args.

        Returns ``(captured, ControlsPageStub)`` — the ``captured`` dict
        gets ``available`` populated when the picker is opened, and
        ``ControlsPageStub`` is just an alias for the imported class.
        """
        from control_ofc.ui.pages import controls_page as cp_module

        captured: dict = {"available": None}

        class _StubDialog:
            def __init__(
                self,
                _current_members,
                available,
                _assigned_elsewhere=None,
                parent=None,
            ):
                captured["available"] = available
                # The dialog is constructed lazily inside _on_edit_members
                # via a local import; we patch the symbol on the
                # ``member_editor`` module the import target binds to.
                self.parent = parent

            def exec(self):
                return 0  # Cancel — _on_edit_members must not mutate state.

            def get_members(self):
                return []

        from control_ofc.ui.widgets import member_editor as me_module

        monkeypatch.setattr(me_module, "MemberEditorDialog", _StubDialog)
        return captured, cp_module.ControlsPage

    def test_read_only_hwmon_header_omitted_from_picker(self, qtbot, monkeypatch):
        """The canonical bug: ``hwmon:amdgpu:0000:03:00.0:pwm1:pwm1`` was
        offered with a "(read-only)" suffix. After DEC-102 it is dropped."""
        captured, ControlsPage = self._captured_picker(monkeypatch)

        readonly_id = "hwmon:amdgpu:0000:03:00.0:pwm1:pwm1"
        writable_id = "hwmon:it8696:it87.2624:pwm1:pwm1"
        state = _state_with_headers(
            [
                _hdr(readonly_id, is_writable=False, label="GPU PWM"),
                _hdr(writable_id, is_writable=True, label="CPU_FAN"),
            ]
        )

        # Build a profile with an empty control so _on_edit_members has a
        # target to populate.
        profile_service = ProfileService()
        profile = Profile(id="p1", name="Test")
        control = LogicalControl(id="c1", name="CPU-Cooler", mode=ControlMode.CURVE)
        profile.controls = [control]
        profile_service._profiles = {"p1": profile}
        profile_service._active_id = "p1"

        page = ControlsPage(state=state, profile_service=profile_service)
        qtbot.addWidget(page)

        page._on_edit_members("c1")

        ids = {entry["id"] for entry in (captured["available"] or [])}
        assert readonly_id not in ids, f"read-only header must not be offered to the picker: {ids}"
        assert writable_id in ids, f"writable header must remain in picker: {ids}"

    def test_picker_keeps_writable_hwmon_headers_with_present_label(self, qtbot, monkeypatch):
        """Writable headers still surface and never carry '(read-only)'."""
        captured, ControlsPage = self._captured_picker(monkeypatch)

        state = _state_with_headers(
            [
                _hdr("hwmon:it8696:it87.2624:pwm1:pwm1", is_writable=True, label="CPU_FAN"),
            ]
        )

        profile_service = ProfileService()
        profile = Profile(id="p1", name="Test")
        profile.controls = [LogicalControl(id="c1", name="CPU", mode=ControlMode.CURVE)]
        profile_service._profiles = {"p1": profile}
        profile_service._active_id = "p1"

        page = ControlsPage(state=state, profile_service=profile_service)
        qtbot.addWidget(page)

        page._on_edit_members("c1")

        available = captured["available"] or []
        assert len(available) == 1
        assert available[0]["id"] == "hwmon:it8696:it87.2624:pwm1:pwm1"
        assert "(read-only)" not in available[0]["label"]


# ─── Option C-2a: load-time syntactic sanitizer ──────────────────────────


class TestProfileLoadDropsKnownDeadHwmonMembers:
    """Profile.from_dict must strip ``hwmon:amdgpu:`` members on every load."""

    def test_amdgpu_pwm1_member_dropped_from_loaded_profile(self):
        raw = {
            "id": "balanced",
            "name": "Balanced",
            "version": 4,
            "controls": [
                {
                    "id": "c1",
                    "name": "CPU-Cooler",
                    "mode": "curve",
                    "curve_id": "balanced_curve",
                    "members": [
                        {
                            "source": "hwmon",
                            "member_id": "hwmon:it8696:it87.2624:pwm1:pwm1",
                            "member_label": "CPU_FAN",
                        },
                        {
                            "source": "hwmon",
                            "member_id": "hwmon:amdgpu:0000:03:00.0:pwm1:pwm1",
                            "member_label": "amdgpu pwm1",
                        },
                    ],
                },
                {
                    "id": "c2",
                    "name": "GPU-Fans",
                    "mode": "curve",
                    "curve_id": "balanced_curve",
                    "members": [
                        {
                            "source": "amd_gpu",
                            "member_id": "amd_gpu:0000:03:00.0",
                            "member_label": "9070XT Fan",
                        }
                    ],
                },
            ],
            "curves": [],
        }

        profile = Profile.from_dict(raw)

        cpu = profile.controls[0]
        gpu = profile.controls[1]
        cpu_member_ids = [m.member_id for m in cpu.members]
        assert "hwmon:it8696:it87.2624:pwm1:pwm1" in cpu_member_ids
        assert "hwmon:amdgpu:0000:03:00.0:pwm1:pwm1" not in cpu_member_ids
        # GPU control's amd_gpu binding is preserved — only the dead hwmon
        # shadow gets dropped.
        assert [m.member_id for m in gpu.members] == ["amd_gpu:0000:03:00.0"]

    def test_load_resaves_when_dead_member_dropped(self, tmp_path, monkeypatch, caplog):
        """``ProfileService.load`` must rewrite the file so the cleanup
        persists across restarts. Without this, every launch would log the
        same warning forever."""
        # Redirect profiles_dir() to a clean tmp path. ``profiles_dir`` reads
        # ``XDG_CONFIG_HOME`` afresh every call, so setting the env var is
        # enough — no cache to clear.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from control_ofc.paths import profiles_dir

        d = profiles_dir()
        d.mkdir(parents=True, exist_ok=True)

        # Write a profile with the dead member
        raw = {
            "id": "balanced",
            "name": "Balanced",
            "version": 4,
            "controls": [
                {
                    "id": "c1",
                    "name": "CPU-Cooler",
                    "mode": "curve",
                    "curve_id": "x",
                    "members": [
                        {
                            "source": "hwmon",
                            "member_id": "hwmon:amdgpu:0000:03:00.0:pwm1:pwm1",
                            "member_label": "GPU pwm1",
                        }
                    ],
                }
            ],
            "curves": [],
        }
        fp = d / "balanced.json"
        fp.write_text(json.dumps(raw))

        svc = ProfileService()
        with caplog.at_level(logging.WARNING):
            errors = svc.load()
        assert errors == []

        on_disk = json.loads(fp.read_text())
        members = on_disk["controls"][0]["members"]
        assert members == [], f"dead member must be persisted out of the profile JSON: {members}"

        # And a structured warning records the sanitization for posterity.
        warning_messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("hwmon:amdgpu:0000:03:00.0:pwm1:pwm1" in m for m in warning_messages), (
            f"expected DEC-102 warning, got: {warning_messages}"
        )


# ─── Option C-2b: runtime sanitizer against live header set ──────────────


class TestProfileSanitizeAgainstHeaders:
    """``Profile.sanitize_hwmon_members`` drops members that no current
    writable header can satisfy. Catches non-canonical cases the syntactic
    drop misses (e.g. a future BIOS-locked motherboard chip)."""

    def test_drops_member_targeting_unwritable_header(self):
        profile = Profile(
            id="p1",
            name="Test",
            controls=[
                LogicalControl(
                    id="c1",
                    name="Group",
                    members=[
                        ControlMember(
                            source="hwmon",
                            member_id="hwmon:locked:dev:pwm1:pwm1",
                            member_label="locked",
                        ),
                        ControlMember(
                            source="hwmon",
                            member_id="hwmon:it8696:dev:pwm1:CPU_FAN",
                            member_label="CPU_FAN",
                        ),
                    ],
                )
            ],
        )
        writable = {"hwmon:it8696:dev:pwm1:CPU_FAN"}
        all_ids = {"hwmon:it8696:dev:pwm1:CPU_FAN", "hwmon:locked:dev:pwm1:pwm1"}

        dropped = profile.sanitize_hwmon_members(writable, all_ids)

        assert dropped == 1
        remaining = [m.member_id for m in profile.controls[0].members]
        assert remaining == ["hwmon:it8696:dev:pwm1:CPU_FAN"]

    def test_drops_member_targeting_missing_header(self):
        profile = Profile(
            id="p1",
            name="Test",
            controls=[
                LogicalControl(
                    id="c1",
                    name="Group",
                    members=[
                        ControlMember(
                            source="hwmon",
                            member_id="hwmon:gone:dev:pwm1:pwm1",
                            member_label="gone",
                        )
                    ],
                )
            ],
        )

        dropped = profile.sanitize_hwmon_members(writable_header_ids=set(), all_header_ids=set())

        assert dropped == 1
        assert profile.controls[0].members == []

    def test_keeps_non_hwmon_members_unconditionally(self):
        """OpenFan and amd_gpu members are never the hwmon-discovery
        problem — sanitization must leave them alone even when no hwmon
        headers are present at all."""
        profile = Profile(
            id="p1",
            name="Test",
            controls=[
                LogicalControl(
                    id="c1",
                    name="Group",
                    members=[
                        ControlMember(source="openfan", member_id="openfan:ch00"),
                        ControlMember(source="amd_gpu", member_id="amd_gpu:0000:03:00.0"),
                    ],
                )
            ],
        )

        dropped = profile.sanitize_hwmon_members(set(), set())
        assert dropped == 0
        assert len(profile.controls[0].members) == 2

    def test_no_drops_when_every_member_is_writable(self):
        profile = Profile(
            id="p1",
            name="Test",
            controls=[
                LogicalControl(
                    id="c1",
                    name="Group",
                    members=[
                        ControlMember(
                            source="hwmon",
                            member_id="hwmon:it8696:dev:pwm1:CPU_FAN",
                        )
                    ],
                )
            ],
        )
        writable = {"hwmon:it8696:dev:pwm1:CPU_FAN"}

        dropped = profile.sanitize_hwmon_members(writable, writable)

        assert dropped == 0
        assert profile.controls[0].members[0].member_id == "hwmon:it8696:dev:pwm1:CPU_FAN"
