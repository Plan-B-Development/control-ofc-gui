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

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
PKGBUILD = REPO_ROOT / "packaging" / "PKGBUILD"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

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


# --------------------------------------------------------------------------
# DEC-239 — the GitHub Release carries the clean-room package as an asset, so
# `pacman -U` is a complete install path when the AUR is read-only (the
# 2026-08-02 freeze stranded v2.34.0 for over a day). These guard the wiring:
# each failure mode below produces a *green* release that silently ships no
# usable asset, which is exactly the kind of thing nobody notices until the
# AUR is down and the fallback is needed.
# --------------------------------------------------------------------------


def _release_workflow() -> dict:
    return yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def _step_using(job: dict, action_prefix: str) -> dict | None:
    for step in _steps(job):
        if step.get("uses", "").startswith(action_prefix):
            return step
    return None


def test_release_artifact_name_matches_between_upload_and_download():
    """The artifact name must agree across jobs, or the Release ships no package.

    build-test uploads the built package under a name that github-release
    downloads by name. A typo on either side does not fail the build — the
    download step errors only at release time, and any drift here means the
    `files:` glob resolves to nothing.
    """
    jobs = _release_workflow()["jobs"]
    upload = _step_using(jobs["build-test"], "actions/upload-artifact")
    download = _step_using(jobs["github-release"], "actions/download-artifact")

    assert upload is not None, "build-test must upload the built package as an artifact (DEC-239)"
    assert download is not None, "github-release must download the built package (DEC-239)"
    assert upload["with"]["name"] == download["with"]["name"], (
        "artifact name drift between build-test upload "
        f"({upload['with']['name']!r}) and github-release download "
        f"({download['with']['name']!r}) — the Release would carry no package."
    )


def test_release_attaches_package_files():
    """The Release step must attach the package, not just create an empty Release."""
    jobs = _release_workflow()["jobs"]
    release_step = _step_using(jobs["github-release"], "softprops/action-gh-release")
    assert release_step is not None, "github-release must create the GitHub Release"
    files = release_step["with"].get("files")
    assert files and "pkg.tar.zst" in files, (
        "the Release step must attach the built *.pkg.tar.zst (DEC-239); "
        f"got files={files!r}. Without it the AUR-free install path in README.md "
        "is a dead link."
    )


def test_github_release_gates_on_clean_room_build():
    """github-release must need build-test.

    Two things depend on this. Ordering: without it the two jobs race and the
    download can run before the package exists. Integrity: the attached asset
    must be the artifact the clean-room build actually verified, and an
    unbuildable PKGBUILD must not produce a Release at all.
    """
    jobs = _release_workflow()["jobs"]
    needs = jobs["github-release"].get("needs")
    needs = [needs] if isinstance(needs, str) else (needs or [])
    assert "build-test" in needs, (
        "github-release must declare `needs: build-test` (DEC-239) — otherwise it "
        f"races the build and can publish an unverified or missing asset; got needs={needs!r}"
    )


def test_github_release_gates_on_a_green_test_suite():
    """github-release must also need ci-green.

    DEC-263: `build-test` proves the package *assembles* — it runs no test suite.
    Until this gate existed the two were unrelated, and v2.41.0 published with
    every CI test leg red. Pinned because the failure mode is silent: dropping
    the job does not break the Release, it just stops checking, so nothing
    surfaces the loss.
    """
    wf = _release_workflow()
    needs = wf["jobs"]["github-release"].get("needs")
    needs = [needs] if isinstance(needs, str) else (needs or [])
    assert "ci-green" in needs, (
        "github-release must declare `needs: ci-green` (DEC-263) — build-test only "
        "proves the package builds, so without this a red test suite can still "
        f"publish; got needs={needs!r}"
    )

    run = "\n".join(step.get("run", "") for step in wf["jobs"]["ci-green"].get("steps", []))
    assert "actions/workflows/ci.yml/runs" in run, (
        "ci-green must resolve the tagged commit's ci.yml run via the Checks API (DEC-263)"
    )
    assert "head_sha=$SHA" in run, (
        "ci-green must query CI for THIS commit — a query not pinned to the tagged "
        "SHA would pass on some other commit's green run (DEC-263)"
    )


def test_release_attests_build_provenance_with_required_permissions():
    """Provenance signing needs both the attest step and its two permissions.

    `actions/attest-build-provenance` fails at runtime without `id-token: write`
    (keyless Sigstore) and `attestations: write`. Dropping either turns the
    documented `gh attestation verify` command in README.md into a lie.
    """
    job = _release_workflow()["jobs"]["github-release"]
    attest = _step_using(job, "actions/attest-build-provenance")
    assert attest is not None, "github-release must attest the package's build provenance (DEC-239)"
    assert "pkg.tar.zst" in attest["with"]["subject-path"], (
        f"attestation must cover the package; got {attest['with']['subject-path']!r}"
    )

    perms = job.get("permissions", {})
    for required in ("id-token", "attestations", "contents"):
        assert perms.get(required) == "write", (
            f"github-release needs `{required}: write` for provenance attestation "
            f"(DEC-239); got permissions={perms!r}"
        )


def test_release_actions_are_pinned_to_a_sha():
    """Every third-party action stays pinned to a full commit SHA.

    A mutable tag on a step that holds `contents: write` and `id-token: write`
    is a supply-chain hole — an upstream tag move would run unreviewed code with
    permission to sign artifacts and publish Releases under this repo's name.
    """
    jobs = _release_workflow()["jobs"]
    unpinned = [
        step["uses"]
        for job in jobs.values()
        for step in _steps(job)
        if "uses" in step and not re.search(r"@[0-9a-f]{40}$", step["uses"])
    ]
    assert not unpinned, f"release.yml actions must be pinned to a 40-char SHA; got {unpinned!r}"


# --------------------------------------------------------------------------
# DEC-240 — the AUR is retired as a publishing channel. `aur-publish` is kept
# in the workflow but gated to a manual `workflow_dispatch`, so a release is
# never reddened by a third party being down. Both guards below protect the
# *silent* failure modes of that change.
# --------------------------------------------------------------------------


def test_aur_publish_never_runs_on_a_tag_push():
    """`aur-publish` must stay gated to a manual dispatch (DEC-240).

    Dropping the `if:` restores the pre-DEC-240 behaviour where an upstream AUR
    outage turns an otherwise-complete release red — the exact failure that
    burned four release attempts on v2.34.0. Nothing else in the workflow fails
    if this condition is removed, so only this test catches it.
    """
    job = _release_workflow()["jobs"]["aur-publish"]
    assert job.get("if") == "github.event_name == 'workflow_dispatch'", (
        "aur-publish must be gated to `if: github.event_name == 'workflow_dispatch'` "
        f"(DEC-240) so it never runs on a tag push; got if={job.get('if')!r}"
    )


def test_version_guards_run_on_the_tag_push_path():
    """The pkgver / pyproject version guards must live in `build-test`.

    They originally sat in `aur-publish`. Because that job no longer runs on a
    tag push (DEC-240), leaving them there would let a forgotten version bump
    produce a GitHub Release whose attached package disagrees with its own tag —
    green, published, and wrong. `build-test` runs on both paths and gates
    `github-release`, so the guards belong there.
    """
    jobs = _release_workflow()["jobs"]
    build_test = jobs["build-test"]
    scripts = "\n".join(step.get("run", "") for step in _steps(build_test))

    assert "packaging/PKGBUILD" in scripts and "pkgver=" in scripts, (
        "build-test must verify packaging/PKGBUILD pkgver against the tag (DEC-240) — "
        "it is the only job on the tag-push path that can catch a missed bump"
    )
    assert "pyproject.toml" in scripts, (
        "build-test must verify pyproject.toml's version against the tag (DEC-240)"
    )
    # The guards compare against the tag, so the job must expose it.
    assert "RELEASE_TAG" in build_test.get("env", {}), (
        "build-test must set RELEASE_TAG in its env for the version guards to "
        f"compare against; got env={build_test.get('env')!r}"
    )
    assert "RELEASE_TAG" in scripts, "the version guards must compare against RELEASE_TAG"


# --------------------------------------------------------------------------
# DEC-241 — the [control-ofc] pacman repository is what makes `pacman -Syu`
# upgrade this package. `notify-repo` is the only thing that tells it a new
# release exists. Every failure below is SILENT: the release goes green, the
# Release object is correct, and users simply never receive the update.
# --------------------------------------------------------------------------


def test_notify_repo_runs_on_a_tag_push():
    """The pacman repository must be told about a release, on the tag path."""
    jobs = _release_workflow()["jobs"]
    assert "notify-repo" in jobs, (
        "release.yml must have a `notify-repo` job (DEC-241) — without it a release "
        "reaches GitHub and never propagates to the pacman repository"
    )
    assert jobs["notify-repo"].get("if") == "github.event_name == 'push'", (
        "notify-repo must be gated to tag pushes so the manual AUR path does not "
        f"also trigger a rebuild; got if={jobs['notify-repo'].get('if')!r}"
    )


def test_notify_repo_waits_for_the_release_to_exist():
    """`needs: github-release` is correctness, not ordering aesthetics.

    The assembler downloads `*.pkg.tar.zst` from this repo's *latest* Release.
    If notify-repo fires before github-release has created it, the rebuild picks
    up the PREVIOUS version and republishes it as current — a stale package
    served to every user, with a fully green release run.
    """
    needs = _release_workflow()["jobs"]["notify-repo"].get("needs")
    needs = [needs] if isinstance(needs, str) else (needs or [])
    assert "github-release" in needs, (
        "notify-repo must declare `needs: github-release` (DEC-241) — firing early "
        f"rebuilds the pacman repo around the previous version; got needs={needs!r}"
    )


def test_notify_repo_targets_the_pacman_repo_with_a_cross_repo_token():
    """Endpoint and credential must both be right, and neither fails loudly.

    The ambient GITHUB_TOKEN cannot dispatch to another repository, so a swap to
    `github.token` yields a 404 that looks like a typo. A changed endpoint fails
    the same way.
    """
    # Located by content, not by index: the job gained a settle step ahead of the
    # dispatch (DEC-248), and an index-based lookup silently pointed at that
    # instead — a test that breaks when a step is prepended is testing the
    # ordering it did not mean to pin.
    steps = _release_workflow()["jobs"]["notify-repo"]["steps"]
    dispatch = [s for s in steps if "dispatches" in s.get("run", "")]
    assert len(dispatch) == 1, (
        f"expected exactly one dispatching step in notify-repo, got {len(dispatch)}"
    )
    run, env = dispatch[0].get("run", ""), str(dispatch[0].get("env", {}))

    assert "repos/Plan-B-Development/pacman-repo/dispatches" in run, (
        "notify-repo must POST to the pacman-repo dispatches endpoint (DEC-241)"
    )
    assert "package-released" in run, (
        "the dispatch event_type must be `package-released` — publish.yml listens "
        "for exactly that type and ignores anything else"
    )
    assert "PACMAN_REPO_TOKEN" in env, (
        "notify-repo must authenticate with the cross-repo PACMAN_REPO_TOKEN; the "
        f"ambient GITHUB_TOKEN cannot dispatch across repositories. got env={env!r}"
    )


def test_notify_repo_settles_before_dispatching():
    """A coordinated GUI + daemon release must not publish a mismatched pair.

    pacman-repo's publish is declarative — it assembles from whatever is each
    project's latest Release when it runs — so dispatching the instant this
    release finishes can pair the new package with the counterpart's previous
    one and serve that as current (DEC-248). The wait gives a paired release
    time to land.

    A timing heuristic, not a guarantee; this pins that it exists and runs
    BEFORE the dispatch, which is the part that silently stops being true.
    """
    steps = _release_workflow()["jobs"]["notify-repo"]["steps"]
    runs = [s.get("run", "") for s in steps]
    sleep_idx = [i for i, r in enumerate(runs) if "sleep" in r]
    dispatch_idx = [i for i, r in enumerate(runs) if "dispatches" in r]

    assert sleep_idx, (
        "notify-repo must wait before dispatching so a paired cross-stack release "
        "can land first (DEC-248); no sleep step found"
    )
    assert sleep_idx[0] < dispatch_idx[0], (
        "the settle wait must come BEFORE the dispatch — waiting afterwards does "
        f"nothing at all. sleep at {sleep_idx[0]}, dispatch at {dispatch_idx[0]}"
    )


# --- Release-metadata guards -------------------------------------------------
#
# Both of these move a check that was previously CHECKLIST-ONLY (a step in
# /ofc:release Phase 4) into the Standard gates, so it fails at commit time
# rather than after a tag is pushed.
#
# They exist because checklist-only version facts have a measured history of
# going stale here: the README's release line sat at v1.25.0 through five
# releases before an audit caught it, and a missing CHANGELOG section fails the
# `github-release` CI job only AFTER the tag is live, forcing a delete-and-retag
# cycle. Neither failure mode is subtle; both were simply unwatched.

CHANGELOG = REPO_ROOT / "CHANGELOG.md"
README = REPO_ROOT / "README.md"


def test_changelog_has_a_section_for_the_current_version():
    """CHANGELOG must carry a `## [X.Y.Z]` section matching pyproject's version.

    `.github/workflows/release.yml` extracts the GitHub Release notes from the
    section matching the pushed tag, and the job FAILS when there is none. Since
    the tag is already public by then, the repair is delete-and-re-push. Catching
    it here costs nothing.
    """
    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    changelog_text = CHANGELOG.read_text(encoding="utf-8")

    # Accept `## [X.Y.Z]` with or without a trailing date/dash suffix.
    pattern = rf"^## \[{re.escape(version)}\]"
    assert re.search(pattern, changelog_text, re.MULTILINE) is not None, (
        f"CHANGELOG.md has no '## [{version}]' section, but pyproject.toml is at "
        f"{version!r}. CI extracts the GitHub Release notes from that section and "
        f"the github-release job fails without it — after the tag is already "
        f"pushed. Add the section before tagging."
    )


def test_pkgbuild_daemon_floor_matches_readme_pairing():
    """The daemon-version floor is stated twice; the two statements must agree.

    `packaging/PKGBUILD` `depends=('control-ofc-daemon>=X')` is what pacman
    actually enforces at install time. The README's "Pairs with … >= vX" line is
    what a human reads. They are two hand-maintained statements of one fact, and
    nothing compared them — so a floor bump in one could silently disagree with
    the other, either blocking installs that should work or promising
    compatibility that pacman will refuse.
    """
    deps = _parse_pkgbuild_depends(PKGBUILD.read_text(encoding="utf-8"))
    daemon_dep = next((d for d in deps if d.startswith("control-ofc-daemon")), None)
    assert daemon_dep is not None, (
        f"packaging/PKGBUILD depends declares no control-ofc-daemon entry. Got: {deps!r}"
    )

    dep_match = re.search(r">=\s*v?(\d+\.\d+\.\d+)", daemon_dep)
    assert dep_match is not None, (
        f"control-ofc-daemon dep {daemon_dep!r} has no '>=X.Y.Z' floor. The GUI "
        f"must declare the minimum daemon it requires."
    )
    pkgbuild_floor = dep_match.group(1)

    readme_line = next(
        (ln for ln in README.read_text(encoding="utf-8").splitlines() if "Pairs with" in ln),
        None,
    )
    assert readme_line is not None, (
        "README.md has no 'Pairs with' line. It states the daemon floor for humans "
        "and is owned only by /ofc:release Phase 4 — do not drop it."
    )

    # The line names several capability-gated versions in parentheses; the FLOOR
    # is the first one, immediately after the >= / ≥ sign.
    readme_match = re.search(r"(?:>=|≥)\s*v?(\d+\.\d+\.\d+)", readme_line)
    assert readme_match is not None, (
        f"README 'Pairs with' line states no '>= vX.Y.Z' floor:\n  {readme_line}"
    )
    readme_floor = readme_match.group(1)

    assert pkgbuild_floor == readme_floor, (
        f"daemon floor drift: packaging/PKGBUILD requires >={pkgbuild_floor} but "
        f"README.md advertises >= v{readme_floor}. pacman enforces the PKGBUILD "
        f"value; the README is what users read. Bump both together."
    )


def test_a_nightly_ci_run_cannot_veto_a_release():
    """DEC-270: `ci-green` gates publication on the newest `ci.yml` run for the
    tagged SHA, and this repo's `ci.yml` also runs on a nightly `schedule` whose
    matrix is deliberately WIDER than the per-push one — it adds the py3.14 leg
    and restores the canary's full loop count, neither `continue-on-error`.

    Without a filter the newest run for a tagged commit can be last night's cron,
    so a failure on a leg the push path never runs would block a release whose own
    CI was green — and `ci-green` fails the release rather than waiting, so there
    is no self-correction.

    Conditional on the nightly actually existing, so removing the nightly removes
    the requirement rather than stranding a workaround.
    """
    ci_workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    ci = yaml.safe_load(ci_workflow.read_text(encoding="utf-8"))
    # PyYAML parses a bare `on:` key as the boolean True.
    triggers = ci.get("on", ci.get(True, {}))
    if "schedule" not in triggers:
        return

    steps = _steps(_release_workflow()["jobs"]["ci-green"])
    body = "\n".join(s.get("run", "") for s in steps)
    # Comments in the step explain *why* `event=push` is the wrong filter, so the
    # negative check below has to look at code or it matches the explanation.
    code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))

    assert 'select(.event != "schedule")' in code, (
        "ci.yml runs a nightly with a wider matrix than the push path, so ci-green "
        "must exclude scheduled runs when picking the newest run for the tagged "
        "SHA — otherwise a py3.14-only nightly failure vetoes a green release"
    )
    assert "event=push" not in code, (
        'filter on `.event != "schedule"`, not `event=push`: an operator '
        "re-dispatching ci.yml produces a `workflow_dispatch` run, and that is the "
        "documented escape hatch for tagging a docs-only commit"
    )
