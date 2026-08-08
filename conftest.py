"""Repo-root conftest — user-config isolation for *everything* collected here.

This lives at the root rather than in ``tests/`` for two reasons, both learned
the hard way (DEC-244):

1. **Coverage.** A ``tests/conftest.py`` fixture only applies to tests collected
   under ``tests/``. During the review of this very release, an agent wrote a
   probe file outside that directory, ran pytest on it, and wrote straight into
   the developer's real ``~/.config/control-ofc/app_settings.json`` — reproducing
   the exact bug the release fixes, on a machine that already had the fix. At the
   root, the fixture applies to anything collected from the repository. (A file
   outside the repo entirely is still uncovered; pytest only loads conftests from
   the rootdir and the collected file's ancestors. The CI leak assertion is the
   backstop for that.)

2. **Teardown ordering.** Fixtures from a higher-level conftest are set up first
   and therefore torn down *last*. That matters here: ``tests/conftest.py`` holds
   the DEC-230 ``_flush_deferred_deletes`` fixture, which destroys each test's Qt
   widget tree at teardown. If the environment redirect were torn down first, that
   widget-destruction window would run with the developer's real ``HOME``/XDG
   restored, and any write during destruction would land on the real config.
   Rooting this fixture guarantees the sandbox outlives the flush.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """Point every user-config path at ``tmp_path`` (DEC-244).

    Three tests were writing the developer's live config: two built
    ``MainWindow(demo_mode=True)`` with no ``settings_service`` — which
    default-constructs one aimed at the real ``~/.config/control-ofc`` — and one
    drove ``_on_save_profile`` through a real ``ProfileService``. The damage was
    not hypothetical: a suite run reproducibly replaced the author's
    ``hidden_chart_series`` with the 20 synthetic ids from ``DemoService`` and
    emptied ``series_colors``, because ``AppSettingsService.save()`` serialises
    the whole dataclass with no read-modify-write. Running the quality gate
    before a release is what destroyed the config — which is exactly why it
    presented as "the *daemon update* ate my settings".

    Guarding the three call sites would not close this: the next test to
    default-construct a service leaks again. Isolating the environment closes
    the class. ``HOME`` covers ``Path.home()`` (``export_default_dir``); the two
    XDG vars cover ``config_dir``/``cache_dir``. ``paths._overrides`` is reset as
    well — it is module-level mutable state, and an absolute
    ``profiles_dir_override`` escaping one test would defeat the env redirect for
    every test after it.
    """
    from control_ofc import paths

    home = tmp_path / "_home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setattr(paths, "_overrides", {})
