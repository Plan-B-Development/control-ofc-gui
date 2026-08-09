# Third-Party License Notices

`control-ofc-gui` is distributed under the **MIT License** (see `project.license`
in `pyproject.toml`). It depends on third-party packages under their own
licenses. This notice records only the cases whose license is *not* permissive —
the ones where a reader could reasonably ask what obligations they inherit —
rather than restating the whole dependency set.

## Runtime dependencies

- **PySide6** — **LGPL-3.0** (also available under a commercial Qt license).
  <https://doc.qt.io/qtforpython/licenses.html>

  LGPL-3.0 permits use by an application that does not derive from it, provided
  the user can replace the library. This project imports PySide6 dynamically at
  run time as a normal Python package and links nothing statically, so the
  replaceability condition is satisfied by the ordinary Python import mechanism:
  a user may substitute their own PySide6 build without modifying or rebuilding
  this application. The Arch package depends on the distribution's own
  `pyside6`, which the user controls.

  This does not change the project's own license. Note that **DEC-043's
  objection to LGPL-3.0 was scoped to statically-linked Rust crates in the
  daemon** and does not apply here — the concern there was linkage, not the
  license in the abstract.

- **certifi** — **MPL-2.0** (a transitive dependency of `httpx`).
  <https://github.com/certifi/python-certifi>

  MPL-2.0 is weak, file-level copyleft: it permits use within an MIT-licensed
  application and does not change this project's license. The obligation to
  provide MPL-covered source on request applies to certifi's own files and is
  satisfied by that project's public upstream repository and its PyPI
  publication. Same class as the daemon's `serialport` case (DEC-155).

Everything else in the runtime set (`httpx`, `pyqtgraph`, `numpy`, `colorama`)
is MIT or BSD.

## Development dependencies

Not distributed with the application and therefore not a license obligation for
users. Listed under `project.optional-dependencies.dev` in `pyproject.toml`.

---

The full set can be regenerated with `pip-licenses` or
`pip freeze --exclude-editable`; this notice records only the non-permissive
cases (DEC-258, mirroring the daemon's `NOTICE.md`).
