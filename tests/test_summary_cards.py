"""Summary-card refinement tests (DEC-178; DEC-185 removed the Safety card).

Covers the trend glyph, the free-form detail line, the thermal-safety detail
(re-homed from the removed Safety card to the strip's thermal chip), the
Fans-card online/expected face, the disconnect "—" reset, and that a temp-card
click still opens the sensor binding picker (not a surprise chart-series toggle).
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from control_ofc.api.models import (
    ConnectionState,
    DaemonStatus,
    FanReading,
    OverrideStatusEntry,
    SensorReading,
)
from control_ofc.ui.main_window import MainWindow
from control_ofc.ui.pages import dashboard_page as dashboard_page_mod
from control_ofc.ui.pages.dashboard_page import _trend_from_rate
from control_ofc.ui.widgets.summary_card import SummaryCard


@pytest.fixture()
def window(qtbot, app_state, profile_service, settings_service):
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


# ─── SummaryCard widget ──────────────────────────────────────────────────


class TestSummaryCardTrend:
    def test_up_glyph_visible(self, qtbot):
        card = SummaryCard("CPU")
        qtbot.addWidget(card)
        card.set_trend("up")
        assert card._trend_label.text() == "↑"
        assert not card._trend_label.isHidden()

    def test_each_direction(self, qtbot):
        card = SummaryCard("CPU")
        qtbot.addWidget(card)
        for direction, glyph in (("down", "↓"), ("flat", "→")):
            card.set_trend(direction)
            assert card._trend_label.text() == glyph
            assert not card._trend_label.isHidden()

    def test_empty_hides_glyph(self, qtbot):
        card = SummaryCard("CPU")
        qtbot.addWidget(card)
        card.set_trend("up")
        card.set_trend("")
        assert card._trend_label.isHidden()
        assert card._trend_label.text() == ""

    def test_unknown_direction_hides(self, qtbot):
        card = SummaryCard("CPU")
        qtbot.addWidget(card)
        card.set_trend("sideways")
        assert card._trend_label.isHidden()


class TestSummaryCardDetail:
    def test_detail_text_shows_and_hides(self, qtbot):
        card = SummaryCard("Fans")
        qtbot.addWidget(card)
        card.set_detail_text("avg 45% PWM")
        assert card._range_label.text() == "avg 45% PWM"
        assert not card._range_label.isHidden()
        card.set_detail_text("")
        assert card._range_label.isHidden()

    def test_set_range_delegates_to_detail(self, qtbot):
        card = SummaryCard("CPU")
        qtbot.addWidget(card)
        card.set_range(30.0, 70.0)
        assert "30.0" in card._range_label.text()
        assert "70.0" in card._range_label.text()
        assert not card._range_label.isHidden()
        card.set_range(None, None)
        assert card._range_label.isHidden()


class TestSummaryCardCompaction:
    def test_card_width_is_capped(self, qtbot):
        """DEC-185: row-1 cards are width-capped so they read as an intentional
        row rather than stretching across the page (compaction)."""
        card = SummaryCard("CPU")
        qtbot.addWidget(card)
        assert card.maximumWidth() == 220


# ─── _trend_from_rate (pure) ─────────────────────────────────────────────


class TestTrendFromRate:
    @pytest.mark.parametrize(
        ("rate", "expected"),
        [
            (None, ""),  # no rate yet → no glyph
            (0.5, "up"),
            (-0.5, "down"),
            (0.0, "flat"),
            (0.04, "flat"),  # inside deadband
            (-0.04, "flat"),
            (0.06, "up"),  # just outside deadband
            (-0.06, "down"),
        ],
    )
    def test_rate_maps_to_direction(self, rate, expected):
        assert _trend_from_rate(rate) == expected


# ─── Thermal-safety detail (Safety card removed, DEC-185) ────────────────


class TestThermalSafetyDetail:
    """DEC-185: the Safety summary card was removed; thermal state shows on the
    strip's thermal chip and its detail re-homed to a click on that chip."""

    def test_no_safety_card_on_dashboard(self, qtbot, window):
        page = window.dashboard_page
        assert not hasattr(page, "_safety_card")
        assert page.findChild(SummaryCard, "Dashboard_Card_safety") is None

    def test_safety_detail_text_surfaces_state_and_hottest_cpu(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [SensorReading(id="cpu0", label="CPU", kind="CpuTemp", value_c=92.3, age_ms=100)]
        )
        app_state.set_status(DaemonStatus(thermal_state="emergency"))
        text = window.dashboard_page._safety_detail_text()
        assert "Emergency" in text
        assert "critical temperature" in text.lower()
        assert "92.3" in text  # current hottest CPU sensor surfaced

    def test_safety_detail_text_includes_override_count(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_status(
            DaemonStatus(
                thermal_state="normal",
                overrides=[OverrideStatusEntry(control_id="cpu", pwm_percent=80)],
            )
        )
        text = window.dashboard_page._safety_detail_text()
        assert "1 manual override" in text

    def test_thermal_chip_click_opens_safety_detail(self, qtbot, window, app_state):
        """Full chain: strip thermal_clicked → _open_safety_detail (exec is
        neutralised in tests, so the message box persists as a page child)."""
        from PySide6.QtWidgets import QMessageBox

        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_status(DaemonStatus(thermal_state="emergency"))
        page = window.dashboard_page
        page._status_strip.thermal_clicked.emit()
        assert page.findChild(QMessageBox, "Dashboard_Dialog_safetyDetail") is not None


# ─── Temp-card trend integration ─────────────────────────────────────────


class TestTempCardTrend:
    def test_rising_shows_up_arrow(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(
                    id="cpu0",
                    label="CPU",
                    kind="CpuTemp",
                    value_c=60.0,
                    age_ms=100,
                    rate_c_per_s=0.5,
                )
            ]
        )
        assert window.dashboard_page._cpu_card._trend_label.text() == "↑"

    def test_flat_within_deadband(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(
                    id="cpu0",
                    label="CPU",
                    kind="CpuTemp",
                    value_c=60.0,
                    age_ms=100,
                    rate_c_per_s=0.0,
                )
            ]
        )
        assert window.dashboard_page._cpu_card._trend_label.text() == "→"

    def test_stale_reading_hides_trend(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(
                    id="cpu0",
                    label="CPU",
                    kind="CpuTemp",
                    value_c=60.0,
                    age_ms=5000,
                    rate_c_per_s=0.5,
                )
            ]
        )
        # Stale rate is not trustworthy → no glyph.
        assert window.dashboard_page._cpu_card._trend_label.isHidden()


class TestMoboCard:
    """The motherboard temp card is driven by the same _update_card path as the
    CPU/GPU cards (kind 'MbTemp'/'mb_temp') but had no test exercising it."""

    def test_value_and_rising_trend(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(
                    id="mb0",
                    label="Motherboard",
                    kind="MbTemp",
                    value_c=45.5,
                    age_ms=100,
                    rate_c_per_s=0.5,
                )
            ]
        )
        card = window.dashboard_page._mb_card
        assert "45.5" in card._value_label.text()
        assert card._trend_label.text() == "↑"

    def test_stale_reading_hides_trend(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [
                SensorReading(
                    id="mb0",
                    label="Motherboard",
                    kind="MbTemp",
                    value_c=50.0,
                    age_ms=5000,
                    rate_c_per_s=0.5,
                )
            ]
        )
        assert window.dashboard_page._mb_card._trend_label.isHidden()

    def test_disconnect_blanks_mobo_card(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [SensorReading(id="mb0", label="Motherboard", kind="MbTemp", value_c=50.0, age_ms=100)]
        )
        assert "50.0" in window.dashboard_page._mb_card._value_label.text()
        app_state.set_connection(ConnectionState.DISCONNECTED)
        assert window.dashboard_page._mb_card._value_label.text() == "—"
        assert window.dashboard_page._mb_card._trend_label.isHidden()


# ─── Disconnect resets cards ─────────────────────────────────────────────


class TestDisconnectReset:
    def test_disconnect_blanks_cards(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [SensorReading(id="cpu0", label="CPU", kind="CpuTemp", value_c=55.0, age_ms=100)]
        )
        assert "55.0" in window.dashboard_page._cpu_card._value_label.text()

        app_state.set_connection(ConnectionState.DISCONNECTED)
        assert window.dashboard_page._cpu_card._value_label.text() == "—"
        assert window.dashboard_page._cpu_card._trend_label.isHidden()


# ─── Fans card ───────────────────────────────────────────────────────────


class TestFansCard:
    def test_online_expected_face(self, qtbot, window, app_state):
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_fans(
            [
                FanReading(id="f1", source="openfan", rpm=1200, last_commanded_pwm=50, age_ms=100),
                FanReading(id="f2", source="openfan", rpm=1000, last_commanded_pwm=40, age_ms=100),
            ]
        )
        card = window.dashboard_page._fans_card
        assert card._value_label.text() == "2/2"
        assert "PWM" in card._range_label.text()
        assert "rpm" in card._range_label.text()

    def test_update_fans_card_shortfall_warns(self, qtbot, window):
        # Direct unit test of the helper — deterministic, bypasses display filter.
        fans = [
            FanReading(id="f1", source="openfan", rpm=1200, last_commanded_pwm=60, age_ms=100),
            FanReading(id="f2", source="openfan", rpm=0, last_commanded_pwm=40, age_ms=5000),
        ]
        card = window.dashboard_page._fans_card
        window.dashboard_page._update_fans_card(fans)
        assert card._value_label.text() == "1/2"  # one fresh, one stale
        assert card._value_label.property("class") == "WarningChip"
        # avg PWM = round((60+40)/2) = 50
        assert "avg 50% PWM" in card._range_label.text()

    def test_update_fans_card_empty(self, qtbot, window):
        card = window.dashboard_page._fans_card
        window.dashboard_page._update_fans_card([])
        assert card._value_label.text() == "0/0"
        assert card._range_label.isHidden()


# ─── Card click keeps the binding picker (Q2) ────────────────────────────


class _FakePicker:
    """Spy standing in for SensorPickerDialog: records construction, never opens."""

    instances: ClassVar[list[dict]] = []

    class DialogCode:
        Accepted = 1
        Rejected = 0

    def __init__(self, **kwargs):
        _FakePicker.instances.append(kwargs)
        self.selected_sensor_id = None

    def update_values(self, *args):
        pass

    def exec(self):
        return _FakePicker.DialogCode.Rejected


class TestCardClickKeepsBindingPicker:
    def test_temp_card_click_opens_picker_no_series_toggle(
        self, qtbot, window, app_state, monkeypatch
    ):
        _FakePicker.instances = []
        monkeypatch.setattr(dashboard_page_mod, "SensorPickerDialog", _FakePicker)
        app_state.set_connection(ConnectionState.CONNECTED)
        app_state.set_sensors(
            [SensorReading(id="cpu0", label="CPU", kind="CpuTemp", value_c=50.0, age_ms=100)]
        )
        bindings_before = dict(window.dashboard_page._card_bindings)

        window.dashboard_page._on_card_clicked("cpu_temp")

        # The binding picker was opened for this card …
        assert len(_FakePicker.instances) == 1
        assert _FakePicker.instances[0]["category"] == "cpu_temp"
        # … and a rejected picker neither rebinds nor toggles chart series.
        assert window.dashboard_page._card_bindings == bindings_before
