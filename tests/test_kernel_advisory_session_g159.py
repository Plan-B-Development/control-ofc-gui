"""G159 (`DC-ad`): the kernel-advisory popup shows at most once per id per session.

Capabilities are re-fetched every ``CAPABILITIES_REFRESH_INTERVAL_S`` and on every
reconnect, and each emission used to re-open the popup for an advisory the user
had just OK'd — every ~5 minutes until "Don't show again". These tests drive the
real ``MainWindow`` slot through ``AppState.set_capabilities`` (the production
signal path), with ``QMessageBox.exec`` replaced so no modal runs.

The older ``TestKernelWarningPopupAcknowledgement`` re-implements the gating
inline and never calls the window, so it could not have caught this.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from control_ofc.api.models import AmdGpuCapability, Capabilities, KernelWarning
from control_ofc.ui.main_window import MainWindow

DONT_SHOW = "Don't show again"


def _caps(*ids: str, severity: str = "critical") -> Capabilities:
    caps = Capabilities()
    caps.amd_gpu = AmdGpuCapability(
        present=True,
        kernel_warnings=[KernelWarning(id=i, severity=severity, message=f"msg {i}") for i in ids],
    )
    return caps


@pytest.fixture()
def shown(monkeypatch):
    """Every popup `exec()`ed, as (warning text, informative text). The user
    presses OK unless a test re-patches ``clickedButton``."""
    boxes: list[tuple[str, str]] = []

    def _exec(self):
        boxes.append((self.text(), self.informativeText()))
        return 0

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    return boxes


def _window(qtbot, app_state, profile_service, settings_service) -> MainWindow:
    win = MainWindow(
        state=app_state,
        profile_service=profile_service,
        settings_service=settings_service,
        demo_mode=False,
    )
    qtbot.addWidget(win)
    return win


def test_an_advisory_the_user_okd_does_not_return_on_the_next_refresh(
    qtbot, app_state, profile_service, settings_service, shown
):
    win = _window(qtbot, app_state, profile_service, settings_service)
    assert win._kernel_warnings_shown == set(), "precondition: nothing shown yet"

    app_state.set_capabilities(_caps("adv_a"))
    assert [t for t, _ in shown] == ["msg adv_a"], "presence: the first emission pops"

    # The 300 s refresh and a reconnect both re-emit the same payload.
    app_state.set_capabilities(_caps("adv_a"))
    app_state.set_capabilities(_caps("adv_a"))

    assert len(shown) == 1
    # OK records nothing persistent — the id comes back next session.
    assert settings_service.settings.acknowledged_kernel_warnings == []


def test_a_new_advisory_mid_session_still_pops_once(
    qtbot, app_state, profile_service, settings_service, shown
):
    """Per id, not per session: the user's choice for G159."""
    win = _window(qtbot, app_state, profile_service, settings_service)

    app_state.set_capabilities(_caps("adv_a"))
    app_state.set_capabilities(_caps("adv_a", "adv_b"))
    app_state.set_capabilities(_caps("adv_a", "adv_b"))

    assert [t for t, _ in shown] == ["msg adv_a", "msg adv_b"]
    assert win._kernel_warnings_shown == {"adv_a", "adv_b"}


def test_ok_is_session_scoped_so_the_next_session_shows_it_again(
    qtbot, app_state, profile_service, settings_service, shown
):
    # Both windows are bound: a dropped MainWindow can be collected and lose its
    # connections (qt-gui lesson, DEC-356), which would fake this result.
    first = _window(qtbot, app_state, profile_service, settings_service)
    app_state.set_capabilities(_caps("adv_a"))
    assert len(shown) == 1

    # A second window over the same settings is a new session.
    second = _window(qtbot, app_state, profile_service, settings_service)
    app_state.set_capabilities(_caps("adv_a"))
    assert first._kernel_warnings_shown == second._kernel_warnings_shown == {"adv_a"}

    # Both windows are connected to the same AppState. The first has shown the
    # id and stays quiet, so the only new popup is the second window's.
    assert len(shown) == 2


def test_dont_show_again_persists_the_id(
    qtbot, app_state, profile_service, settings_service, shown, monkeypatch
):
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(b for b in self.buttons() if b.text() == DONT_SHOW),
    )
    win = _window(qtbot, app_state, profile_service, settings_service)

    app_state.set_capabilities(_caps("adv_a"))

    assert settings_service.settings.acknowledged_kernel_warnings == ["adv_a"]
    # The persisted set suppresses it now, so the session set lets it go.
    assert "adv_a" not in win._kernel_warnings_shown


def test_clear_dismissed_brings_a_same_session_dismissal_back(
    qtbot, app_state, profile_service, settings_service, shown, monkeypatch
):
    """G159 review: the popup says Clear dismissed brings an advisory back.
    Dismissed and cleared in one session, it must pop at the next refresh."""
    from control_ofc.ui.pages.settings_page import SettingsPage

    dismissing = {"on": True}

    def _clicked(self):
        if dismissing["on"]:
            return next(b for b in self.buttons() if b.text() == DONT_SHOW)
        return None

    monkeypatch.setattr(QMessageBox, "clickedButton", _clicked)
    win = _window(qtbot, app_state, profile_service, settings_service)

    app_state.set_capabilities(_caps("adv_a"))
    app_state.set_capabilities(_caps("adv_a"))
    assert len(shown) == 1, "precondition: dismissed, and suppressed on refresh"

    dismissing["on"] = False
    page = win.findChild(SettingsPage)
    assert page is not None
    # What arriving on the page does (`SettingsPage.showEvent`): re-read the
    # counts, which enables the button now that one advisory is dismissed.
    page._refresh_reset_buttons()
    assert page._clear_kernel_warnings_btn.isEnabled()
    page._clear_kernel_warnings_btn.click()
    assert settings_service.settings.acknowledged_kernel_warnings == []

    app_state.set_capabilities(_caps("adv_a"))
    assert len(shown) == 2


def test_a_refresh_inside_the_open_popup_does_not_open_a_second_copy(
    qtbot, app_state, profile_service, settings_service, monkeypatch
):
    """`exec()` runs a nested event loop, so the 300 s refresh can land while
    the popup is open. The id is marked shown before the modal runs."""
    opened: list[str] = []

    def _exec(self):
        opened.append(self.text())
        if len(opened) == 1:
            app_state.set_capabilities(_caps("adv_a"))  # re-entrant refresh
        return 0

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    win = _window(qtbot, app_state, profile_service, settings_service)

    app_state.set_capabilities(_caps("adv_a"))

    assert opened == ["msg adv_a"]
    assert win._kernel_warnings_shown == {"adv_a"}


def test_low_severity_advisories_never_pop(
    qtbot, app_state, profile_service, settings_service, shown
):
    win = _window(qtbot, app_state, profile_service, settings_service)
    app_state.set_capabilities(_caps("adv_info", severity="info"))

    assert shown == []
    # And a skipped id is not recorded as shown, so it cannot mask a later
    # high-severity entry that reuses it.
    assert win._kernel_warnings_shown == set()


def test_the_popup_names_what_ok_and_dont_show_again_actually_do(
    qtbot, app_state, profile_service, settings_service, shown
):
    """The old text promised a dismissal lasted "until the warning ID changes
    (e.g. you boot a different kernel)"; ids are per issue, so it never did."""
    from control_ofc.ui.pages.settings_page import SettingsPage

    win = _window(qtbot, app_state, profile_service, settings_service)
    app_state.set_capabilities(_caps("adv_a"))
    (_, informative) = shown[0]

    assert "warning ID changes" not in informative
    # Relationship, not literal: the text must name the Settings control that
    # really clears the dismissal.
    page = win.findChild(SettingsPage)
    assert page is not None
    assert page._clear_kernel_warnings_btn.text() in informative
    assert "Prompts & Dismissals" in informative
