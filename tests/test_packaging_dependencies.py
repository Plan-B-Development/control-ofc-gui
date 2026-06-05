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
