#!/usr/bin/env bash
# release-aur.sh — sync this repo's packaging/PKGBUILD to the AUR.
#
# Usage:
#   ./scripts/release-aur.sh <version>           # interactive (default)
#   ./scripts/release-aur.sh <version> --yes     # skip confirmation prompts
#   ./scripts/release-aur.sh <version> --no-push # commit but do not push
#
# Example:
#   ./scripts/release-aur.sh 1.0.1
#
# Preconditions:
#   1. packaging/PKGBUILD in this repo is already updated to <version>
#      (pkgver, pkgrel, sha256sums).
#   2. The GitHub release tag v<version> exists and its tarball is reachable.
#   3. You have SSH access to aur@aur.archlinux.org as the package maintainer.
#   4. makepkg is installed (Arch host).
#
# What it does (in order):
#   - Sanity-checks prerequisites and the PKGBUILD version
#   - Downloads the GitHub tarball and verifies it matches PKGBUILD sha256sums
#   - Clones (or ff-pulls) the AUR repo into ~/Development/aur/<pkgname>/
#   - Copies packaging/PKGBUILD into the clone
#   - Regenerates .SRCINFO via `makepkg --printsrcinfo`
#   - Stages PKGBUILD + .SRCINFO only, shows diff, confirms commit
#   - Pushes to AUR origin master after a final confirmation
#
# What it does NOT do:
#   - Modify packaging/PKGBUILD in this repo (you must bump it yourself before
#     running this script).
#   - Build or install the package on your system (run makepkg / pacman -U
#     separately if you want local verification).
#   - Push anything without your explicit confirmation (unless --yes).

set -euo pipefail

# ---- Configuration ---------------------------------------------------------
PKGNAME="control-ofc-gui"
AUR_URL="ssh://aur@aur.archlinux.org/${PKGNAME}.git"
AUR_CLONE_DIR="${HOME}/Development/aur/${PKGNAME}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
SOURCE_PKGBUILD="${REPO_ROOT}/packaging/PKGBUILD"

# ---- Argument parsing ------------------------------------------------------
VERSION=""
ASSUME_YES=0
NO_PUSH=0

for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=1 ;;
        --no-push) NO_PUSH=1 ;;
        -h|--help)
            sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        -*)
            echo "ERROR: unknown flag: $arg" >&2
            exit 2
            ;;
        *)
            if [[ -n "$VERSION" ]]; then
                echo "ERROR: version already set to '$VERSION', got '$arg'" >&2
                exit 2
            fi
            VERSION="$arg"
            ;;
    esac
done

if [[ -z "$VERSION" ]]; then
    echo "Usage: $0 <version> [--yes] [--no-push]" >&2
    exit 2
fi

# ---- Helpers ---------------------------------------------------------------
say() { printf '\e[1;36m==>\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m!!\e[0m %s\n' "$*" >&2; }
die() { printf '\e[1;31mXX\e[0m %s\n' "$*" >&2; exit 1; }

confirm() {
    local prompt="$1"
    if [[ "$ASSUME_YES" -eq 1 ]]; then
        say "$prompt [auto-yes]"
        return 0
    fi
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

# ---- Prerequisites ---------------------------------------------------------
command -v makepkg >/dev/null || die "makepkg not found (Arch host required)"
command -v git >/dev/null || die "git not found"
command -v curl >/dev/null || die "curl not found"
command -v sha256sum >/dev/null || die "sha256sum not found"

[[ -f "$SOURCE_PKGBUILD" ]] || die "source PKGBUILD not found: $SOURCE_PKGBUILD"

# ---- Verify PKGBUILD pkgver matches requested version ----------------------
pkgbuild_ver="$(grep -E '^pkgver=' "$SOURCE_PKGBUILD" | cut -d= -f2)"
if [[ "$pkgbuild_ver" != "$VERSION" ]]; then
    die "packaging/PKGBUILD pkgver is '$pkgbuild_ver' but you asked for '$VERSION'. Bump it first."
fi
say "PKGBUILD pkgver matches: $VERSION"

# ---- Verify tarball sha256 matches PKGBUILD --------------------------------
tarball_url="https://github.com/Plan-B-Development/${PKGNAME}/archive/v${VERSION}.tar.gz"
tmp_tarball="$(mktemp --suffix=".tar.gz")"
trap 'rm -f "$tmp_tarball"' EXIT

say "Downloading tarball to verify sha256: $tarball_url"
if ! curl -sfL -o "$tmp_tarball" "$tarball_url"; then
    die "Could not fetch tarball — is the v${VERSION} GitHub release published?"
fi

actual_sha="$(sha256sum "$tmp_tarball" | cut -d' ' -f1)"
expected_sha="$(grep -E "^sha256sums=" "$SOURCE_PKGBUILD" | grep -oE "[a-f0-9]{64}" | head -n1)"

if [[ -z "$expected_sha" ]]; then
    die "Could not parse sha256sums from $SOURCE_PKGBUILD"
fi

if [[ "$actual_sha" != "$expected_sha" ]]; then
    warn "sha256 mismatch!"
    warn "  PKGBUILD: $expected_sha"
    warn "  tarball:  $actual_sha"
    die "Fix packaging/PKGBUILD sha256sums and re-run."
fi
say "sha256 verified: $actual_sha"

# ---- Ensure AUR clone exists -----------------------------------------------
mkdir -p "$(dirname "$AUR_CLONE_DIR")"
if [[ ! -d "$AUR_CLONE_DIR/.git" ]]; then
    say "Cloning AUR repo → $AUR_CLONE_DIR"
    git clone "$AUR_URL" "$AUR_CLONE_DIR"
else
    say "Pulling latest from AUR → $AUR_CLONE_DIR"
    git -C "$AUR_CLONE_DIR" fetch origin
    # AUR uses 'master', not 'main'
    git -C "$AUR_CLONE_DIR" checkout master
    git -C "$AUR_CLONE_DIR" pull --ff-only origin master
fi

# Refuse to proceed if the AUR clone has uncommitted changes from a prior run
if ! git -C "$AUR_CLONE_DIR" diff --quiet \
    || ! git -C "$AUR_CLONE_DIR" diff --cached --quiet; then
    warn "AUR clone has uncommitted changes:"
    git -C "$AUR_CLONE_DIR" status --short
    die "Resolve or discard them before running this script."
fi

# ---- Copy PKGBUILD + regenerate .SRCINFO -----------------------------------
cp "$SOURCE_PKGBUILD" "$AUR_CLONE_DIR/PKGBUILD"
say "Copied PKGBUILD → $AUR_CLONE_DIR/PKGBUILD"

(cd "$AUR_CLONE_DIR" && makepkg --printsrcinfo > .SRCINFO)
say "Regenerated .SRCINFO"

# ---- Show diff -------------------------------------------------------------
echo
say "Changes staged for AUR:"
git -C "$AUR_CLONE_DIR" status --short
echo
git -C "$AUR_CLONE_DIR" --no-pager diff -- PKGBUILD .SRCINFO
echo

if git -C "$AUR_CLONE_DIR" diff --quiet -- PKGBUILD .SRCINFO; then
    say "No changes — AUR is already at $VERSION. Nothing to do."
    exit 0
fi

# ---- Commit ----------------------------------------------------------------
if ! confirm "Stage and commit these changes?"; then
    say "Aborted — no commit made. Files left in working tree of $AUR_CLONE_DIR."
    exit 0
fi

git -C "$AUR_CLONE_DIR" add PKGBUILD .SRCINFO
git -C "$AUR_CLONE_DIR" commit -m "Update to ${VERSION}"
say "Committed to AUR clone."
git -C "$AUR_CLONE_DIR" --no-pager log -1 --oneline

# ---- Push ------------------------------------------------------------------
if [[ "$NO_PUSH" -eq 1 ]]; then
    say "--no-push set. To push later:"
    printf '  git -C %q push origin master\n' "$AUR_CLONE_DIR"
    exit 0
fi

echo
if ! confirm "Push to AUR origin master now?"; then
    say "Not pushed. To push later:"
    printf '  git -C %q push origin master\n' "$AUR_CLONE_DIR"
    exit 0
fi

git -C "$AUR_CLONE_DIR" push origin master
say "Done. AUR package $PKGNAME is now at $VERSION."
