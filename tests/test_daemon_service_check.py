"""Unit tests for the daemon-service first-launch detection helper.

Covers the ``check_daemon_service_state`` decision matrix:
- No systemctl on PATH → ``can_check=False`` (avoid misleading hints).
- systemctl reports ``enabled``/``active`` → ``installed_but_not_enabled=False``.
- systemctl reports ``disabled``/``inactive`` → flag the actionable case.
- subprocess timeout/OSError → ``can_check=False``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from control_ofc.services.daemon_service_check import (
    ENABLE_COMMAND,
    DaemonServiceState,
    check_daemon_service_state,
)

# --------------------------------------------------------------------------- #
# DaemonServiceState semantics
# --------------------------------------------------------------------------- #


class TestDaemonServiceStateSemantics:
    def test_installed_but_not_enabled_requires_can_check(self):
        # can_check=False short-circuits — even a fully "disabled" snapshot
        # must not claim the actionable case if we couldn't probe reliably.
        state = DaemonServiceState(
            socket_exists=False,
            service_enabled=False,
            service_active=False,
            can_check=False,
        )
        assert state.installed_but_not_enabled is False

    def test_installed_but_not_enabled_true_when_disabled(self):
        state = DaemonServiceState(
            socket_exists=False,
            service_enabled=False,
            service_active=False,
            can_check=True,
        )
        assert state.installed_but_not_enabled is True

    def test_installed_but_not_enabled_false_when_enabled(self):
        state = DaemonServiceState(
            socket_exists=True,
            service_enabled=True,
            service_active=True,
            can_check=True,
        )
        assert state.installed_but_not_enabled is False

    def test_installed_but_not_enabled_false_when_active_but_not_enabled(self):
        # Edge: someone ran `systemctl start` without enabling. Still don't
        # show the hint — service is up; the user clearly knows what they're
        # doing.
        state = DaemonServiceState(
            socket_exists=True,
            service_enabled=False,
            service_active=True,
            can_check=True,
        )
        assert state.installed_but_not_enabled is False


# --------------------------------------------------------------------------- #
# check_daemon_service_state probe behavior
# --------------------------------------------------------------------------- #


class TestCheckDaemonServiceState:
    def test_no_systemctl_returns_can_check_false(self, tmp_path):
        socket = tmp_path / "control-ofc.sock"
        with patch("control_ofc.services.daemon_service_check.shutil.which", return_value=None):
            state = check_daemon_service_state(socket)
        assert state.can_check is False
        assert state.service_enabled is False
        assert state.service_active is False

    def test_socket_existence_observed(self, tmp_path):
        present = tmp_path / "present.sock"
        present.touch()
        absent = tmp_path / "absent.sock"
        with patch("control_ofc.services.daemon_service_check.shutil.which", return_value=None):
            assert check_daemon_service_state(present).socket_exists is True
            assert check_daemon_service_state(absent).socket_exists is False

    def test_enabled_active(self, tmp_path):
        socket = tmp_path / "control-ofc.sock"
        # Simulate systemctl outputs: is-enabled "enabled", is-active "active"
        outputs = iter(["enabled", "active"])

        def fake_run(*args, **kwargs):
            class R:
                stdout = next(outputs) + "\n"

            return R()

        with (
            patch(
                "control_ofc.services.daemon_service_check.shutil.which",
                return_value="/usr/bin/systemctl",
            ),
            patch("control_ofc.services.daemon_service_check.subprocess.run", side_effect=fake_run),
        ):
            state = check_daemon_service_state(socket)
        assert state.can_check is True
        assert state.service_enabled is True
        assert state.service_active is True
        assert state.installed_but_not_enabled is False

    def test_disabled_inactive(self, tmp_path):
        socket = tmp_path / "control-ofc.sock"
        outputs = iter(["disabled", "inactive"])

        def fake_run(*args, **kwargs):
            class R:
                stdout = next(outputs) + "\n"

            return R()

        with (
            patch(
                "control_ofc.services.daemon_service_check.shutil.which",
                return_value="/usr/bin/systemctl",
            ),
            patch("control_ofc.services.daemon_service_check.subprocess.run", side_effect=fake_run),
        ):
            state = check_daemon_service_state(socket)
        assert state.can_check is True
        assert state.service_enabled is False
        assert state.service_active is False
        assert state.installed_but_not_enabled is True

    @pytest.mark.parametrize("variant", ["static", "alias", "enabled-runtime"])
    def test_other_enabled_variants_treated_as_enabled(self, tmp_path, variant):
        # systemd reports several flavours of "enabled-ish"; we want to
        # treat all of them as "the service is set up; don't nag the user".
        socket = tmp_path / "control-ofc.sock"
        outputs = iter([variant, "active"])

        def fake_run(*args, **kwargs):
            class R:
                stdout = next(outputs) + "\n"

            return R()

        with (
            patch(
                "control_ofc.services.daemon_service_check.shutil.which",
                return_value="/usr/bin/systemctl",
            ),
            patch("control_ofc.services.daemon_service_check.subprocess.run", side_effect=fake_run),
        ):
            state = check_daemon_service_state(socket)
        assert state.service_enabled is True
        assert state.installed_but_not_enabled is False

    def test_subprocess_timeout_returns_can_check_false(self, tmp_path):
        socket = tmp_path / "control-ofc.sock"

        def boom(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="systemctl", timeout=1.5)

        with (
            patch(
                "control_ofc.services.daemon_service_check.shutil.which",
                return_value="/usr/bin/systemctl",
            ),
            patch("control_ofc.services.daemon_service_check.subprocess.run", side_effect=boom),
        ):
            state = check_daemon_service_state(socket)
        assert state.can_check is False

    def test_subprocess_oserror_returns_can_check_false(self, tmp_path):
        socket = tmp_path / "control-ofc.sock"

        def oserr(*args, **kwargs):
            raise OSError("no such file")

        with (
            patch(
                "control_ofc.services.daemon_service_check.shutil.which",
                return_value="/usr/bin/systemctl",
            ),
            patch("control_ofc.services.daemon_service_check.subprocess.run", side_effect=oserr),
        ):
            state = check_daemon_service_state(socket)
        assert state.can_check is False

    def test_socket_path_accepts_str_and_path(self, tmp_path):
        socket = tmp_path / "control-ofc.sock"
        socket.touch()
        with patch("control_ofc.services.daemon_service_check.shutil.which", return_value=None):
            assert check_daemon_service_state(str(socket)).socket_exists is True
            assert check_daemon_service_state(Path(socket)).socket_exists is True


class TestEnableCommand:
    def test_enable_command_uses_sudo_systemctl_now(self):
        # Locked down so a future drive-by edit can't quietly drop --now (and
        # leave the user with an enabled but stopped daemon until reboot).
        assert ENABLE_COMMAND == "sudo systemctl enable --now control-ofc-daemon"
