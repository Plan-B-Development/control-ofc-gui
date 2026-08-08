"""The test suite must never write to the real user config (DEC-244).

Three tests did. Two built ``MainWindow(demo_mode=True)`` with no
``settings_service``, which default-constructs one aimed at
``~/.config/control-ofc/app_settings.json``; one drove ``_on_save_profile``
through a real ``ProfileService``. Because ``AppSettingsService.save()``
serialises the whole dataclass with no read-modify-write, a single suite run
replaced the developer's ``hidden_chart_series`` with the 20 synthetic ids from
``DemoService`` and emptied ``series_colors`` — permanently, since nothing
repopulates colours. Running the quality gate before a release is what destroyed
it, so the loss correlated with daemon updates and looked like packaging.

These tests pin the *class* of defect, not those three call sites: they assert
the ``_isolate_user_config`` fixture actually redirects every path helper, and
that even a deliberately unguarded ``MainWindow`` cannot escape the sandbox.
"""

from __future__ import annotations

from pathlib import Path

from control_ofc import paths


def test_every_path_helper_resolves_inside_the_sandbox(tmp_path):
    """``HOME`` plus both XDG vars must cover every path the app can write to.

    ``export_default_dir`` is the one that needs ``HOME`` rather than an XDG
    var — it defaults to bare ``Path.home()``, so an XDG-only fixture would
    leave exports pointed at the developer's home directory.
    """
    for name, resolved in (
        ("config_dir", paths.config_dir()),
        ("cache_dir", paths.cache_dir()),
        ("profiles_dir", paths.profiles_dir()),
        ("themes_dir", paths.themes_dir()),
        ("app_settings_path", paths.app_settings_path()),
        ("export_default_dir", paths.export_default_dir()),
        ("Path.home()", Path.home()),
    ):
        assert tmp_path in resolved.parents or resolved == tmp_path, (
            f"{name} escaped the sandbox: {resolved}"
        )


# The next two tests are deliberately a pair, and each one *asserts before it
# mutates*. That makes them order-independent: whichever pytest runs first, the
# other still sees an empty override map if the fixture is doing its job. A
# single set-then-assert-in-the-next-test pair would silently pass under any
# plugin that shuffles test order.


def test_path_overrides_do_not_leak_out_of_a_test_a(tmp_path):
    assert paths._overrides == {}, "a previous test leaked a path override"
    paths.set_path_overrides(profiles_dir=str(tmp_path / "leak-a"))
    assert paths._overrides != {}


def test_path_overrides_do_not_leak_out_of_a_test_b(tmp_path):
    assert paths._overrides == {}, "a previous test leaked a path override"
    paths.set_path_overrides(themes_dir=str(tmp_path / "leak-b"))
    assert paths._overrides != {}


def test_settings_service_save_lands_in_the_sandbox():
    """A real save through the real code path, not a mocked one."""
    from control_ofc.services.app_settings_service import AppSettingsService

    svc = AppSettingsService()
    svc.load()
    svc.update(theme_name="Sandbox Probe")

    written = paths.app_settings_path()
    assert written.exists()
    assert "Sandbox Probe" in written.read_text()


def test_unguarded_main_window_cannot_escape_the_sandbox(qtbot, tmp_path):
    """The exact shape of the DEC-244 bug, reproduced on purpose.

    Constructed the *unsafe* way — no ``settings_service`` — because that is
    what shipped, and what wiped a real config on every gate run. ``close()``
    then drives ``closeEvent``, the geometry/last-page write. This must stay
    harmless: if ``_isolate_user_config`` is ever weakened or removed, this
    fails instead of quietly eating someone's settings again.
    """
    from control_ofc.ui.main_window import MainWindow

    window = MainWindow(demo_mode=True)
    qtbot.addWidget(window)
    window.close()

    for resolved in (paths.app_settings_path(), paths.profiles_dir(), Path.home()):
        assert tmp_path in resolved.parents or resolved == tmp_path, (
            f"unguarded MainWindow escaped the sandbox: {resolved}"
        )
