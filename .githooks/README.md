# Git hooks

Tracked-in-repo git hooks. Not enabled by default — opt in by pointing
git's hook resolver at this directory:

```bash
git config core.hooksPath .githooks
```

This setting is per-clone and per-repo, so each fresh clone needs to
run it once. There is no equivalent global flag.

## Hooks

### `pre-commit`

Regenerates `packaging/.SRCINFO` whenever `packaging/PKGBUILD` is
staged for commit, so the in-repo `.SRCINFO` cannot drift out of sync
with the PKGBUILD.

The hook regenerates from the **staged** PKGBUILD content (via
`git show :packaging/PKGBUILD`), so it captures exactly what's about
to be committed regardless of unstaged tweaks in the working tree, and
re-stages the new `.SRCINFO` automatically.

It's a no-op when:
- `packaging/PKGBUILD` isn't part of the staged change.
- `makepkg` is not on `PATH` (typical on non-Arch CI workers).
- The user is `root` (makepkg refuses to run as root anyway).
