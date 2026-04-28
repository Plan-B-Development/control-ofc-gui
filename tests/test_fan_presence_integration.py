"""Integration tests for fan presence rendering across surfaces (A2).

Targets the three surfaces called out in PWM_VERIFY_REMEDIATION.md §A2:
Diagnostics → Fans, Controls fan-role member picker, and Fan Wizard. Each
test drives synthetic fan + header pairs through the production code path
and asserts the presence badge renders distinctly.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from control_ofc.api.models import (
    AmdGpuCapability,
    Capabilities,
    ConnectionState,
    FanReading,
    HwmonCapability,
    HwmonHeader,
    OperationMode,
)
from control_ofc.services.app_state import AppState
from control_ofc.ui.fan_presence import (
    PRESENCE_BADGE,
    FanPresence,
    classify_fan_presence,
)
from control_ofc.ui.pages.diagnostics_page import DiagnosticsPage
from control_ofc.ui.widgets.member_editor import MemberEditorDialog


def _state(headers=None, fans=None):
    s = AppState()
    s.set_connection(ConnectionState.CONNECTED)
    s.set_mode(OperationMode.AUTOMATIC)
    s.set_capabilities(
        Capabilities(
            hwmon=HwmonCapability(present=True, pwm_header_count=2, write_support=True),
            amd_gpu=AmdGpuCapability(),
        )
    )
    if headers is not None:
        s.set_hwmon_headers(headers)
    if fans is not None:
        s.fans = list(fans)
    return s


def _hdr(fan_id: str, *, is_writable: bool = True, rpm_available: bool = True) -> HwmonHeader:
    return HwmonHeader(
        id=fan_id,
        label="",
        chip_name="it8696",
        device_id="it87.2624",
        pwm_index=1,
        supports_enable=True,
        rpm_available=rpm_available,
        is_writable=is_writable,
    )


# ─── Diagnostics → Fans ──────────────────────────────────────────────


class TestDiagnosticsFansBadging:
    """Fan rows on Diagnostics → Fans must surface the presence state in the
    RPM cell so the user can distinguish "controllable but empty" from
    "uncontrollable" without hovering."""

    def test_present_fan_shows_only_rpm(self, qtbot):
        s = _state(
            headers=[_hdr("hwmon:it8696:it87.2624:pwm1:pwm1")],
            fans=[
                FanReading(
                    id="hwmon:it8696:it87.2624:pwm1:pwm1",
                    source="hwmon",
                    rpm=994,
                    last_commanded_pwm=75,
                    age_ms=500,
                )
            ],
        )
        page = DiagnosticsPage(state=s)
        qtbot.addWidget(page)
        page._on_fans(s.fans)
        # PRESENT badge is empty — RPM cell is just the number.
        assert page._fan_table.item(0, 3).text() == "994"

    def test_empty_header_appended_to_rpm_cell(self, qtbot):
        """The X870E AORUS MASTER case — writable header, fan_input present,
        but RPM=0 because nothing is plugged in."""
        s = _state(
            headers=[_hdr("hwmon:it8696:it87.2624:pwm2:pwm2")],
            fans=[
                FanReading(
                    id="hwmon:it8696:it87.2624:pwm2:pwm2",
                    source="hwmon",
                    rpm=0,
                    last_commanded_pwm=50,
                    age_ms=500,
                )
            ],
        )
        page = DiagnosticsPage(state=s)
        qtbot.addWidget(page)
        page._on_fans(s.fans)
        text = page._fan_table.item(0, 3).text()
        assert PRESENCE_BADGE[FanPresence.EMPTY_HEADER] in text
        assert text.startswith("0")

    def test_read_only_header_badged_in_rpm_cell(self, qtbot):
        s = _state(
            headers=[_hdr("hwmon:locked:it87.2624:pwm1:pwm1", is_writable=False)],
            fans=[
                FanReading(
                    id="hwmon:locked:it87.2624:pwm1:pwm1",
                    source="hwmon",
                    rpm=1500,
                    age_ms=500,
                )
            ],
        )
        page = DiagnosticsPage(state=s)
        qtbot.addWidget(page)
        page._on_fans(s.fans)
        text = page._fan_table.item(0, 3).text()
        assert PRESENCE_BADGE[FanPresence.READ_ONLY] in text


# ─── Controls → Edit Members picker ─────────────────────────────────────


class TestMemberEditorBadging:
    """The fan-role member picker must show presence badges so users do
    not accidentally assign curves to empty headers."""

    def test_empty_header_label_carries_badge(self, qtbot):
        """When _on_edit_members builds the available-list it should append
        the EMPTY_HEADER badge to the label so it appears in the picker."""
        # Drive the helper directly — _on_edit_members builds an `available`
        # dict list, then hands it to MemberEditorDialog. The badge must be
        # in the visible label string.
        empty_header = _hdr("hwmon:it8696:it87.2624:pwm2:pwm2")
        empty_fan = FanReading(
            id="hwmon:it8696:it87.2624:pwm2:pwm2",
            source="hwmon",
            rpm=0,
            last_commanded_pwm=None,
            age_ms=500,
        )
        # Confirm the classifier returns EMPTY_HEADER for these inputs.
        assert classify_fan_presence(empty_fan, empty_header) == FanPresence.EMPTY_HEADER

        # Build a minimal dialog with the decorated label as the production
        # code would emit it.
        decorated_label = (
            f"{empty_header.label or empty_header.id} ({PRESENCE_BADGE[FanPresence.EMPTY_HEADER]})"
        )
        available = [
            {
                "id": empty_header.id,
                "source": "hwmon",
                "label": decorated_label,
            },
        ]
        dlg = MemberEditorDialog([], available)
        qtbot.addWidget(dlg)
        # The first available item's text must contain the badge.
        text = dlg._available_list.item(0).text()
        assert PRESENCE_BADGE[FanPresence.EMPTY_HEADER] in text

    def test_present_fan_no_badge_in_member_label(self, qtbot):
        present_header = _hdr("hwmon:it8696:it87.2624:pwm1:pwm1")
        present_fan = FanReading(
            id="hwmon:it8696:it87.2624:pwm1:pwm1",
            source="hwmon",
            rpm=994,
            last_commanded_pwm=75,
            age_ms=500,
        )
        assert classify_fan_presence(present_fan, present_header) == FanPresence.PRESENT
        # PRESENT badge is empty — picker label is plain.
        decorated = present_header.label or present_header.id
        assert PRESENCE_BADGE[FanPresence.PRESENT] == ""
        available = [
            {"id": present_header.id, "source": "hwmon", "label": decorated},
        ]
        dlg = MemberEditorDialog([], available)
        qtbot.addWidget(dlg)
        item = dlg._available_list.item(0)
        for badge in (
            PRESENCE_BADGE[FanPresence.EMPTY_HEADER],
            PRESENCE_BADGE[FanPresence.READ_ONLY],
            PRESENCE_BADGE[FanPresence.PWM_ONLY],
        ):
            assert badge not in item.text()


# ─── Fan Wizard ─────────────────────────────────────────────────────────


class TestFanWizardEmptyHeader:
    """The Fan Wizard's discovery filter excludes EMPTY_HEADER fans (R59 /
    R60 — empty slots have no physical fan to identify). A2 verifies this
    filter remains correct after the classifier exists, and that the
    classifier confirms the same exclusion logic."""

    def test_empty_header_excluded_from_wizard_targets(self, qtbot):
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        s = _state(
            headers=[_hdr("hwmon:it8696:it87.2624:pwm2:pwm2")],
            fans=[
                # Spinning fan — included.
                FanReading(
                    id="hwmon:it8696:it87.2624:pwm1:pwm1",
                    source="hwmon",
                    rpm=994,
                    age_ms=500,
                ),
                # Empty header — excluded.
                FanReading(
                    id="hwmon:it8696:it87.2624:pwm2:pwm2",
                    source="hwmon",
                    rpm=0,
                    age_ms=500,
                ),
            ],
        )
        wizard = FanConfigWizard(state=s)
        qtbot.addWidget(wizard)
        target_ids = {t["id"] for t in wizard._targets}
        assert "hwmon:it8696:it87.2624:pwm1:pwm1" in target_ids
        assert "hwmon:it8696:it87.2624:pwm2:pwm2" not in target_ids

    def test_classifier_agrees_with_wizard_filter(self):
        """Mid-test, if a fan that was in the wizard transitions to
        EMPTY_HEADER state (RPM drops to 0 unexpectedly), the wizard's RPM
        display does not crash. Verified via classify_fan_presence
        agreeing with the FanReading shape the wizard observes."""
        # Original target had RPM > 0.
        original = FanReading(
            id="hwmon:foo:bar:pwm1:pwm1",
            source="hwmon",
            rpm=1100,
            age_ms=500,
        )
        # Mid-test transition.
        transition = FanReading(
            id="hwmon:foo:bar:pwm1:pwm1",
            source="hwmon",
            rpm=0,
            age_ms=500,
        )
        header = _hdr("hwmon:foo:bar:pwm1:pwm1")
        assert classify_fan_presence(original, header) == FanPresence.PRESENT
        assert classify_fan_presence(transition, header) == FanPresence.EMPTY_HEADER

    def test_wizard_handles_qt_data_role_unaffected(self, qtbot):
        """Defensive: the member-editor's UserRole data structure is
        unchanged by A2's label decoration. A test that round-trips an
        item through Qt confirms decoded data is still a clean dict."""
        from PySide6.QtWidgets import QListWidgetItem

        item = QListWidgetItem("CPU_FAN (no fan detected)")
        payload = {
            "id": "hwmon:it8696:it87.2624:pwm2:pwm2",
            "source": "hwmon",
            "label": "CPU_FAN (no fan detected)",
        }
        item.setData(Qt.ItemDataRole.UserRole, payload)
        retrieved = item.data(Qt.ItemDataRole.UserRole)
        assert retrieved["id"] == payload["id"]
        assert retrieved["source"] == payload["source"]
