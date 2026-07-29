"""Packaging-dependency regression tests (DEC-103).

`colorama` is a required transitive runtime dep of `pyqtgraph`: the import
fires unconditionally at module load (`pyqtgraph/util/cprint.py` lines 6-7),
and Arch's upstream `python-pyqtgraph` package omits it from its declared
deps. Without an explicit declaration in `pyproject.toml` and the AUR
`packaging/PKGBUILD`, fresh installs crash at GUI launch with
`ModuleNotFoundError: colorama` before Qt is even initialised.

This has shipped twice already (v1.9.0 added it; v1.10.2 / DEC-100 P1.2
removed it on a grep-only audit; v1.11.1 was the first release affected).
DEC-103 is the rule that says do not remove it. These tests are the gate
that catches the next audit attempting it again.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
PKGBUILD = REPO_ROOT / "packaging" / "PKGBUILD"

_DEC103_HINT = (
    "colorama is required transitively by pyqtgraph (see "
    "pyqtgraph/util/cprint.py lines 6-7 — `from colorama.win32 import …`, "
    "`from colorama.winterm import …`, the platform check is *after* the "
    "imports). Arch's python-pyqtgraph omits the dep upstream, so removing "
    "this declaration crashes the GUI on clean installs. See DEC-103."
)


def _parse_pkgbuild_depends(pkgbuild_text: str) -> list[str]:
    """Extract entries from the `depends=(...)` array in a PKGBUILD.

    Handles the common multi-line form:

        depends=('foo' 'bar'
                 'baz')
    """
    match = re.search(
        r"^depends=\((?P<body>.*?)\)\s*$",
        pkgbuild_text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "PKGBUILD has no top-level depends=(...) array"
    body = match.group("body")
    # Strip shell-style comment lines that PKGBUILDs sometimes carry.
    body = re.sub(r"#[^\n]*", "", body)
    return re.findall(r"'([^']+)'", body)


def test_pyproject_declares_colorama():
    """`pyproject.toml` `dependencies` must declare colorama (DEC-103)."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(re.match(r"^colorama(\b|[<>=!~])", dep) for dep in deps), (
        f"pyproject.toml dependencies missing 'colorama'. {_DEC103_HINT}\nGot: {deps!r}"
    )


def test_pkgbuild_declares_python_colorama():
    """AUR `packaging/PKGBUILD` `depends` must declare python-colorama (DEC-103)."""
    deps = _parse_pkgbuild_depends(PKGBUILD.read_text(encoding="utf-8"))
    assert "python-colorama" in deps, (
        f"packaging/PKGBUILD depends missing 'python-colorama'. {_DEC103_HINT}\nGot: {deps!r}"
    )


def test_pkgbuild_pkgver_matches_pyproject_version():
    """The two version strings must agree — drift produces broken AUR releases."""
    pyproject_data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pyproject_ver = pyproject_data["project"]["version"]

    pkgbuild_text = PKGBUILD.read_text(encoding="utf-8")
    match = re.search(r"^pkgver=([^\s]+)$", pkgbuild_text, re.MULTILINE)
    assert match is not None, "PKGBUILD has no pkgver= line"
    pkgbuild_ver = match.group(1)

    assert pyproject_ver == pkgbuild_ver, (
        f"version drift between pyproject.toml ({pyproject_ver!r}) and "
        f"packaging/PKGBUILD ({pkgbuild_ver!r}). The release workflow "
        f"(.github/workflows/release.yml) verifies pkgver against the git tag — "
        f"they must be in sync at commit time so the tag-driven publish succeeds."
    )


def test_bundled_fonts_shipped_in_wheel():
    """DEC-208 ships DM Sans + Space Grotesk INSIDE the wheel via package-data;
    register_bundled_fonts() loads them from a __file__-relative dir at startup.
    If the glob or the .ttf files go missing, a packaged install silently renders
    with fallback fonts (audit 2026-07-15 Phase 5)."""
    data = tomllib.loads(PYPROJECT.read_text())
    package_data = data["tool"]["setuptools"]["package-data"]["control_ofc"]
    assert "ui/fonts/*.ttf" in package_data, (
        f"pyproject package-data must ship the bundled fonts (DEC-208); got {package_data}"
    )
    fonts_dir = REPO_ROOT / "src" / "control_ofc" / "ui" / "fonts"
    ttfs = list(fonts_dir.glob("*.ttf"))
    assert ttfs, f"no .ttf fonts found under {fonts_dir} — the package-data glob would ship nothing"


def test_desktop_entry_declares_spec_version():
    """The `.desktop` file must declare `Version` — the freedesktop Desktop
    Entry *Specification* version, not the app version (audit 2026-07-29).

    Without it the entry is spec-incomplete: `desktop-file-validate` and some
    menu implementations treat the absent key as an unversioned legacy file.
    It is deliberately NOT the application version — bumping it on a release
    would be wrong, so this pins the value.
    """
    desktop = REPO_ROOT / "packaging" / "control-ofc-gui.desktop"
    lines = [
        ln.strip()
        for ln in desktop.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert lines[0] == "[Desktop Entry]", "the group header must come first"
    entries = dict(ln.split("=", 1) for ln in lines[1:] if "=" in ln)
    assert entries.get("Version") == "1.0", (
        "packaging/control-ofc-gui.desktop must declare Version=1.0 (the "
        f"freedesktop spec version, not the app version); got {entries.get('Version')!r}"
    )
    # Guard the confusion directly: the app version must never leak in here.
    assert entries["Exec"] == "control-ofc-gui"
