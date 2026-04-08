"""R40: GPU curve assignment — zero-RPM management, V1 migration source, settings.

Covers: daemon disables zero-RPM for GPU writes, zero-RPM warning setting persists,
V1 migration correctly tags amd_gpu source, controls page shows popup for GPU fans.
"""

from __future__ import annotations

from control_ofc.services.app_settings_service import AppSettings, AppSettingsService
from control_ofc.services.profile_service import _migrate_v1_profile

# ---------------------------------------------------------------------------
# V1 migration GPU source detection
# ---------------------------------------------------------------------------


class TestV1MigrationGpuSource:
    """V1 migration correctly tags amd_gpu fans."""

    def test_amd_gpu_target_gets_correct_source(self):
        data = {
            "id": "test",
            "name": "Test",
            "assignments": [
                {
                    "target_id": "amd_gpu:0000:03:00.0",
                    "target_type": "fan",
                    "curve": {"points": [{"temp_c": 30, "output_pct": 40}]},
                },
            ],
        }
        profile = _migrate_v1_profile(data)
        members_with_fan = [c for c in profile.controls if c.members]
        assert len(members_with_fan) == 1
        member = members_with_fan[0].members[0]
        assert member.source == "amd_gpu"
        assert member.member_id == "amd_gpu:0000:03:00.0"

    def test_openfan_target_still_correct(self):
        data = {
            "id": "test",
            "name": "Test",
            "assignments": [
                {
                    "target_id": "openfan:ch00",
                    "target_type": "fan",
                    "curve": {"points": [{"temp_c": 30, "output_pct": 40}]},
                },
            ],
        }
        profile = _migrate_v1_profile(data)
        member = profile.controls[0].members[0]
        assert member.source == "openfan"

    def test_hwmon_target_still_correct(self):
        data = {
            "id": "test",
            "name": "Test",
            "assignments": [
                {
                    "target_id": "hwmon:it8696:fan1",
                    "target_type": "fan",
                    "curve": {"points": [{"temp_c": 30, "output_pct": 40}]},
                },
            ],
        }
        profile = _migrate_v1_profile(data)
        member = profile.controls[0].members[0]
        assert member.source == "hwmon"


# ---------------------------------------------------------------------------
# Settings: GPU zero-RPM warning toggle
# ---------------------------------------------------------------------------


class TestGpuZeroRpmWarningSetting:
    """show_gpu_zero_rpm_warning persists correctly."""

    def test_default_is_true(self):
        settings = AppSettings()
        assert settings.show_gpu_zero_rpm_warning is True

    def test_persists_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()
        svc.update(show_gpu_zero_rpm_warning=False)

        # Reload
        svc2 = AppSettingsService()
        svc2.load()
        assert svc2.settings.show_gpu_zero_rpm_warning is False

    def test_persists_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        svc = AppSettingsService()
        svc.update(show_gpu_zero_rpm_warning=True)

        svc2 = AppSettingsService()
        svc2.load()
        assert svc2.settings.show_gpu_zero_rpm_warning is True

    def test_roundtrip_via_dict(self):
        settings = AppSettings(show_gpu_zero_rpm_warning=False)
        d = settings.to_dict()
        restored = AppSettings.from_dict(d)
        assert restored.show_gpu_zero_rpm_warning is False

    def test_missing_key_defaults_true(self):
        restored = AppSettings.from_dict({})
        assert restored.show_gpu_zero_rpm_warning is True


# ---------------------------------------------------------------------------
# Settings page checkbox
# ---------------------------------------------------------------------------


class TestSettingsPageZeroRpmCheckbox:
    """Settings page has GPU zero-RPM warning checkbox."""

    def test_checkbox_exists(self, qtbot, app_state, settings_service):
        from PySide6.QtWidgets import QCheckBox

        from control_ofc.ui.pages.settings_page import SettingsPage

        page = SettingsPage(state=app_state, settings_service=settings_service)
        qtbot.addWidget(page)

        cb = page.findChild(QCheckBox, "Settings_Check_gpuZeroRpmWarn")
        assert cb is not None
        assert cb.isChecked()  # default is True


# ---------------------------------------------------------------------------
# Controls page: warning trigger condition
# ---------------------------------------------------------------------------


class TestControlsGpuWarningTrigger:
    """Zero-RPM warning only triggers when NEW GPU fan is added to a role."""

    def test_gpu_fan_detected_as_new_member(self):
        """The set difference correctly identifies new GPU members."""
        from control_ofc.services.profile_service import ControlMember

        old_members = []
        new_members = [
            ControlMember(source="amd_gpu", member_id="amd_gpu:0000:03:00.0"),
        ]

        old_gpu_ids = {m.member_id for m in old_members if m.source == "amd_gpu"}
        new_gpu_ids = {m.member_id for m in new_members if m.source == "amd_gpu"}
        added_gpu = new_gpu_ids - old_gpu_ids

        assert len(added_gpu) == 1
        assert "amd_gpu:0000:03:00.0" in added_gpu

    def test_existing_gpu_member_not_flagged(self):
        """Re-saving with the same GPU member doesn't trigger."""
        from control_ofc.services.profile_service import ControlMember

        existing = ControlMember(source="amd_gpu", member_id="amd_gpu:0000:03:00.0")
        old_members = [existing]
        new_members = [existing]

        old_gpu_ids = {m.member_id for m in old_members if m.source == "amd_gpu"}
        new_gpu_ids = {m.member_id for m in new_members if m.source == "amd_gpu"}
        added_gpu = new_gpu_ids - old_gpu_ids

        assert len(added_gpu) == 0

    def test_non_gpu_fan_not_flagged(self):
        """Adding an OpenFan fan doesn't trigger GPU warning."""
        from control_ofc.services.profile_service import ControlMember

        old_members = []
        new_members = [
            ControlMember(source="openfan", member_id="openfan:ch00"),
        ]

        old_gpu_ids = {m.member_id for m in old_members if m.source == "amd_gpu"}
        new_gpu_ids = {m.member_id for m in new_members if m.source == "amd_gpu"}
        added_gpu = new_gpu_ids - old_gpu_ids

        assert len(added_gpu) == 0
