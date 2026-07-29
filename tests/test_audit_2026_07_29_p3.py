"""Regression tests for the 2026-07-29 GUI code-audit P3 remediation.

Grouped by the audit cluster. Every test asserts an outcome (escaped output,
mutual exclusion, timer state, objectName presence, wiring effect) rather than a
click, per the repo testing policy.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time

import pytest
from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import QLabel, QWidget

from control_ofc.api.errors import DaemonUnavailable
from control_ofc.api.models import (
    DaemonStatus,
    FanReading,
    HistoryPoint,
    HwmonHeader,
    SensorReading,
    SubsystemStatus,
)
from control_ofc.knowledge.sensor_knowledge import classify_sensor, format_sensor_tooltip
from control_ofc.services.app_state import AppState
from control_ofc.services.diagnostics_service import JOURNAL_TIMEOUT_S, DiagnosticsService
from control_ofc.services.history_store import HistoryStore
from control_ofc.services.overview_view import build_sensor_rows, fan_row_tooltip
from control_ofc.services.profile_service import (
    ControlMember,
    ControlMode,
    LogicalControl,
    ProfileService,
)
from control_ofc.ui.hwmon_guidance import dual_chip_warning_html
from control_ofc.ui.pages.controls_page import ControlsPage
from control_ofc.ui.pages.dashboard_page import DashboardPage
from control_ofc.ui.pages.logs_page import LogsPage
from control_ofc.ui.theme import active_theme, default_dark_theme
from control_ofc.ui.widgets.control_card import ControlCard
from control_ofc.ui.widgets.readiness_report import _link
from control_ofc.ui.widgets.theme_editor import ColorSwatch

MARKUP = "<script>alert(1)</script>"


# ── Cluster 1 — security escape discipline ────────────────────────────────


class TestSecurityEscape:
    def test_dual_chip_warning_escapes_board_name(self):
        """Finding 1.1: the DMI board name lands in a RichText QLabel with
        external links; a stray tag must be neutralised, not rendered."""
        html = dual_chip_warning_html(MARKUP, ["it8688", "it8792"], ["it8688"])
        assert html is not None
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_dual_chip_warning_escapes_unknown_chip_names(self):
        """Finding 1.1: `_pretty_chip` echoes daemon chip names it does not know."""
        html = dual_chip_warning_html("Board", ["it8688", "evil<b>chip"], ["it8688"])
        assert html is not None
        assert "<b>chip" not in html
        # _pretty_chip upper-cases the raw name, then it is escaped: EVIL<B>CHIP
        # -> EVIL&lt;B&gt;CHIP (only the '<'/'>' become entities).
        assert "EVIL&lt;B&gt;CHIP" in html

    def test_fan_row_tooltip_escapes_daemon_strings(self):
        """Finding 1.2: tooltips auto-detect rich text; daemon id/source/chip must
        be escaped so a tag cannot become markup."""
        fan = FanReading(id="<b>1", source="hwmon", rpm=900, age_ms=10)
        header = HwmonHeader(id="<b>1", chip_name=MARKUP)
        tip = fan_row_tooltip(fan, [header], None)
        assert "<b>1" not in tip
        assert "<script>" not in tip
        assert "&lt;b&gt;1" in tip
        assert "&lt;script&gt;" in tip

    def test_fan_row_tooltip_keeps_normal_strings_verbatim(self):
        """Escaping is a no-op for real daemon strings (no cosmetic regression)."""
        fan = FanReading(id="openfan:ch00", source="openfan", rpm=1200, age_ms=10)
        tip = fan_row_tooltip(fan, [], None)
        assert "ID: openfan:ch00" in tip
        assert "Source: openfan" in tip

    def test_format_sensor_tooltip_escapes_label_via_description(self):
        """Finding 1.2: display_description embeds the raw daemon label; the whole
        path must be neutralised."""
        classification = classify_sensor(chip_name="nct6798", label=MARKUP, temp_type=None)
        tip = format_sensor_tooltip(classification, sensor_id="<i>id", chip_name="nct<b>")
        assert "<script>" not in tip
        assert "<i>id" not in tip
        assert "nct<b>" not in tip
        assert "&lt;script&gt;" in tip

    def test_build_sensor_rows_escapes_source(self):
        """Finding 1.2: the appended `Source:` line carries the daemon source."""
        rows = build_sensor_rows(
            [SensorReading(id="s1", kind="CpuTemp", label="Tctl", value_c=40.0, source="<b>src")],
            overrides={},
            board_vendor="ASUS",
        )
        assert "<b>src" not in rows[0].tooltip
        assert "&lt;b&gt;src" in rows[0].tooltip

    def test_dashboard_subsystem_labels_are_plaintext(self, qtbot):
        """Finding 1.3: subsystem status/reason are daemon strings in an AutoText
        label — force PlainText so a reason tag can't be reinterpreted."""
        page = DashboardPage(state=AppState())
        qtbot.addWidget(page)
        assert page._sub_openfan_label.textFormat() == Qt.TextFormat.PlainText
        assert page._sub_hwmon_label.textFormat() == Qt.TextFormat.PlainText
        # And a markup reason is stored verbatim (PlainText renders it literally).
        page._on_status_updated(
            DaemonStatus(
                subsystems=[SubsystemStatus(name="openfan", status="degraded", reason="<b>x</b>")]
            )
        )
        assert "<b>x</b>" in page._sub_openfan_label.text()

    def test_readiness_link_escapes_url_and_title(self, qtbot):
        """Finding 1.4: defence-in-depth for the (currently GUI-authored) link
        builder — an ampersand/tag must not break the href or inject markup."""
        html = _link("https://x/y?a=1&b=2", "A & B <x>")
        assert "&amp;b=2" in html  # href ampersand escaped
        assert "A &amp; B &lt;x&gt;" in html  # title escaped
        assert "<x>" not in html


# ── Cluster 2 — concurrency ────────────────────────────────────────────────


class TestHistoryStoreLock:
    def test_record_sensors_acquires_the_lock(self):
        """Finding 2.1: the mutation must serialise against a concurrent prefill.
        Holding the lock in the test thread must block record_sensors until it is
        released (proving the mutation is guarded)."""
        store = HistoryStore()
        done = threading.Event()

        def worker():
            store.record_sensors(
                [SensorReading(id="s1", kind="CpuTemp", label="x", value_c=1.0, source="hwmon")]
            )
            done.set()

        with store._lock:
            t = threading.Thread(target=worker)
            t.start()
            # Blocked on the held lock — must NOT complete while we hold it.
            assert not done.wait(0.3)
        t.join(2.0)
        assert done.is_set()
        assert store.get_series("sensor:s1")  # the point landed once unblocked

    def test_prefill_and_record_concurrently_do_not_corrupt(self):
        """Finding 2.1: hammering prefill (worker thread) against record (main
        thread) on the same key must not raise (e.g. deque-mutated-during-
        iteration) and must keep the series timestamp-sorted."""
        store = HistoryStore()
        errors: list[Exception] = []
        base = int(time.time() * 1000)

        def recorder():
            try:
                for _ in range(400):
                    store.record_sensors(
                        [
                            SensorReading(
                                id="s1", kind="CpuTemp", label="x", value_c=1.0, source="hwmon"
                            )
                        ]
                    )
                    store.get_series("sensor:s1")
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        def prefiller():
            try:
                for i in range(400):
                    store.prefill_sensor("s1", [HistoryPoint(ts=base - i * 10, v=float(i))])
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        threads = [threading.Thread(target=recorder), threading.Thread(target=prefiller)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5.0)
        assert not errors, errors
        series = store.get_series("sensor:s1")
        assert series == sorted(series, key=lambda r: r.timestamp)


class TestLogsJournalTeardown:
    def _blocking_diag(self, monkeypatch, started, release):
        state = AppState()
        diag = DiagnosticsService(state)

        def slow_fetch():
            started.set()
            release.wait(6.0)  # bounded so a failed test can never hang forever
            return "journal text"

        monkeypatch.setattr(diag, "fetch_journal_entries", slow_fetch)
        return state, diag

    def test_cleanup_joins_a_midflight_fetch_without_terminate(self, qtbot, monkeypatch, caplog):
        """Finding 2.2: a fetch in flight must let the worker join on cleanup
        rather than being terminate()-d (which orphans the journalctl child)."""
        started = threading.Event()
        release = threading.Event()
        state, diag = self._blocking_diag(monkeypatch, started, release)
        page = LogsPage(diag, state=state)
        qtbot.addWidget(page)

        page._ensure_journal_worker()
        page._journal_request.emit()
        assert started.wait(3.0)  # the worker is now inside the blocking fetch

        # Release shortly; cleanup's extended wait must join cleanly.
        threading.Timer(0.2, release.set).start()
        with caplog.at_level(logging.WARNING):
            page.cleanup()

        assert not any("terminating" in r.getMessage() for r in caplog.records)
        assert page._journal_thread is None
        assert page._journal_worker is None

    def test_cleanup_wait_budget_covers_the_subprocess_timeout(self, qtbot, monkeypatch):
        """Finding 2.2: the join budget must be at least the subprocess timeout —
        the old 2 s wait fell short of the 5 s journalctl timeout."""
        state = AppState()
        diag = DiagnosticsService(state)
        page = LogsPage(diag, state=state)
        qtbot.addWidget(page)
        page._ensure_journal_worker()

        recorded: list[int] = []
        real_wait = QThread.wait

        def spy_wait(self, ms=None, *a, **k):
            if ms is not None:
                recorded.append(int(ms))
            return real_wait(self, ms) if ms is not None else real_wait(self)

        monkeypatch.setattr(QThread, "wait", spy_wait)
        page.cleanup()
        assert recorded, "cleanup did not wait on the thread"
        assert recorded[0] >= JOURNAL_TIMEOUT_S * 1000


class TestControlsRenewTimer:
    def test_renew_interval_only_rearms_on_change(self, qtbot):
        """Finding 2.3: setInterval restarts the countdown, so it must only be
        called when the cadence genuinely changes — not every recompute."""
        page = ControlsPage(state=AppState(), profile_service=ProfileService())
        qtbot.addWidget(page)

        calls: list[int] = []
        real = page._override_renew_timer.setInterval
        page._override_renew_timer.setInterval = lambda v: (calls.append(v), real(v))[1]

        page._override_renew_secs = {"c1": 5}
        page._recompute_renew_interval()
        assert calls == [5000]  # first arm

        page._recompute_renew_interval()  # unchanged cadence → no re-arm
        assert calls == [5000]

        page._override_renew_secs = {"c1": 3}  # cadence shrank → re-arm
        page._recompute_renew_interval()
        assert calls == [5000, 3000]


class TestDashboardResetTimer:
    def test_reset_timer_is_owned_single_shot_and_stopped_on_cleanup(self, qtbot):
        """Finding 2.4: replace the uncancellable singleShot with an owned timer
        that cleanup() can stop, so it can't fire on a torn-down widget."""
        page = DashboardPage(state=AppState())
        qtbot.addWidget(page)
        assert page._reset_apply_timer.isSingleShot()

        page._reset_apply_timer.start()
        assert page._reset_apply_timer.isActive()
        page.cleanup()
        assert not page._reset_apply_timer.isActive()

    def test_reset_timer_restores_apply_button(self, qtbot):
        """The timer's timeout still restores the Apply button caption."""
        page = DashboardPage(state=AppState())
        qtbot.addWidget(page)
        page._apply_btn.setText("Applied!")
        page._apply_btn.setEnabled(False)
        page._reset_apply_timer.timeout.emit()
        assert page._apply_btn.text() == "Apply"
        assert page._apply_btn.isEnabled()


# ── Cluster 3 — GUI finesse ────────────────────────────────────────────────


class TestControlCardTheme:
    def test_set_theme_repaints_nub_and_role_dot(self, qtbot):
        """Finding 3.1: the inline-styled link nub (accent) and role dot must be
        repainted by set_theme on a live theme switch, not left stale.

        Asserted on the two inline stylesheets directly — the nub is painted from
        the passed tokens' accent, and a sentinel role colour proves the role dot
        is re-applied. Deliberately avoids the global ``apply_theme`` (an app-wide
        stylesheet re-polish) so the test mutates no shared Qt state."""
        control = LogicalControl(
            id="c1",
            name="Chassis",
            mode=ControlMode.CURVE,
            curve_id="x",
            members=[ControlMember(source="openfan", member_id="openfan:ch00")],
        )
        card = ControlCard(control, [])
        qtbot.addWidget(card)

        card._role_color = lambda _control: "#654321"  # sentinel role colour
        custom = dataclasses.replace(default_dark_theme(), accent_primary="#abcdef")
        card.set_theme(custom)
        assert "#abcdef" in card._link_nub.styleSheet().lower()  # nub uses tokens.accent
        assert "#654321" in card._role_icon.styleSheet().lower()  # role dot re-applied

    def test_controls_page_theme_fans_out_to_control_cards(self, qtbot):
        """Finding 3.1: the page's theme fan-out must call set_theme on control
        cards (it previously only resized them)."""
        from unittest.mock import MagicMock

        page = ControlsPage(state=AppState(), profile_service=ProfileService())
        qtbot.addWidget(page)
        fake = MagicMock()
        page._control_cards["c1"] = fake
        tokens = default_dark_theme()
        page.set_theme(tokens)
        fake.set_theme.assert_called_once_with(tokens)
        fake.apply_card_size.assert_called_once()


class TestThemeEditorSwatch:
    def test_swatch_border_uses_theme_token_not_hardcoded_grey(self, qtbot):
        """Finding 3.2: the swatch border must route through a theme token."""
        swatch = ColorSwatch("accent_primary", "#ff0000")
        qtbot.addWidget(swatch)
        style = swatch.styleSheet()
        assert "#666" not in style
        assert active_theme().border_default in style


class TestControlCardMemberObjectNames:
    def test_member_rows_have_unique_objectnames(self, qtbot):
        """Finding 3.3: every member row + its name/rpm labels need a unique
        objectName keyed by member_id (CLAUDE.md uniqueness rule)."""
        control = LogicalControl(
            id="role1",
            name="Mixed",
            mode=ControlMode.CURVE,
            curve_id="x",
            members=[
                ControlMember(source="openfan", member_id="openfan:ch00"),
                ControlMember(source="hwmon", member_id="hwmon:nct6798:1"),
            ],
        )
        card = ControlCard(control, [])
        qtbot.addWidget(card)

        names: list[str] = []
        for m in control.members:
            row = card.findChild(QWidget, f"ControlCard_MemberRow_role1_{m.member_id}")
            name = card.findChild(QLabel, f"ControlCard_MemberName_role1_{m.member_id}")
            rpm = card.findChild(QLabel, f"ControlCard_MemberRpm_role1_{m.member_id}")
            assert row is not None
            assert name is not None
            assert rpm is not None
            names.extend([row.objectName(), name.objectName(), rpm.objectName()])
        assert len(names) == len(set(names))  # all unique


# ── Cluster 4 — test quality ───────────────────────────────────────────────


class TestFakeClientOverrideUnavailable:
    def test_simulate_unavailable_covers_override_methods(self, fake_client):
        """Finding 4.2: a whole-daemon-down simulation must also fail the live
        override intent calls."""
        fake_client.simulate_unavailable()
        with pytest.raises(DaemonUnavailable):
            fake_client.override_take("c1", 50)
        with pytest.raises(DaemonUnavailable):
            fake_client.override_renew("c1", 1)
        with pytest.raises(DaemonUnavailable):
            fake_client.override_release("c1", 1)

    def test_override_happy_path_returns_grant(self, fake_client):
        """The fake's override methods return plausible results when available."""
        grant = fake_client.override_take("c1", 42)
        assert grant.control_id == "c1"
        assert grant.pwm_percent == 42
        assert grant.override_token == 1
        assert fake_client.override_release("c1", 1).released is True
