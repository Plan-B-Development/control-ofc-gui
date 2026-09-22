"""Did the machine go back to how it was found? (DEC-404 acceptance item 7).

Restoration is **measured, not assumed**: the final snapshot is compared with
the baseline, header by header, from what the daemon reported both times. Each
check returns its own verdict; nothing here collapses into one "restored OK".

The checks:

* each touched header's ``pwm_enable_mode`` against the baseline — DEC-382's
  hand-back gives a header back exactly as it was found;
* readback against command on every header the profile controls, within the
  daemon's own tolerance — which doubles as the read-only D3 check on a daemon
  without DEC-406's reconciliation;
* every test's ``restore_outcome``;
* no override the run did not start with;
* the active profile, by id and by content hash;
* the thermal state;
* ``duty_corrections`` deltas across the run and ``duty_not_holding`` at the end
  (DEC-406), where the daemon reports them — a correction during the run is
  evidence of a second writer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from control_ofc.services.pwm_report import document as d

#: Mirrors the daemon's ``READBACK_TOLERANCE_PCT`` (2 points): the constant
#: behind characterisation's ``readback_verdict`` and DEC-406's drift test. A
#: second copy of a daemon value, stated as such (not on the wire).
READBACK_TOLERANCE_PCT = 2

#: Restore outcomes that mean the header IS back where the test found it.
#: Everything else — including a token this build has never seen — means it is
#: not (the same rule as ``characterization_view._RESTORE_OK``).
RESTORE_OK = frozenset({"", "pending", "restored"})

CHECK_PASS = "pass"
CHECK_FAIL = "fail"
CHECK_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Check:
    """One final-state comparison."""

    rule: str
    channel_id: str | None
    result: str
    statement: str
    evidence: tuple[str, ...]
    #: Whether a failure of this check is something "Re-apply profile" fixes.
    reapply_fixes: bool = False


def _int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def touched_channels(doc: Mapping) -> list[str]:
    """Headers a test may have written to: any step that got past its start."""
    out: list[str] = []
    for step in doc.get("steps") or []:
        if step.get("status") in (d.STEP_PENDING, d.STEP_NOT_TESTED):
            continue
        cid = step.get("channel_id")
        if cid and cid not in out:
            out.append(cid)
    return out


def final_state_checks(doc: Mapping, name_of: Mapping[str, str]) -> list[Check]:
    snaps = doc.get("snapshots") or {}
    base, final = snaps.get("baseline"), snaps.get("final")
    touched = touched_channels(doc)
    checks: list[Check] = []

    if final is None:
        if touched or doc.get("steps"):
            checks.append(
                Check(
                    "final.unavailable",
                    None,
                    CHECK_UNAVAILABLE,
                    "The final state could not be read, so restoration was not verified. "
                    + (doc.get("final_note") or ""),
                    ("snapshots.final",),
                    reapply_fixes=bool(touched),
                )
            )
        return checks

    base_fans, final_fans = d.fan_entries(base), d.fan_entries(final)
    ev_fans = ("snapshots.baseline.fans", "snapshots.final.fans")

    # 1. Control mode back to how it was found, on every header a test touched.
    for cid in touched:
        name = name_of.get(cid, cid)
        before = _int((base_fans.get(cid) or {}).get("pwm_enable_mode"))
        after = _int((final_fans.get(cid) or {}).get("pwm_enable_mode"))
        if before is None or after is None:
            checks.append(
                Check(
                    "final.mode",
                    cid,
                    CHECK_UNAVAILABLE,
                    f"{name}: the control mode was not reported both before and after, "
                    "so its hand-back could not be compared.",
                    ev_fans,
                )
            )
        elif before == after:
            checks.append(
                Check(
                    "final.mode",
                    cid,
                    CHECK_PASS,
                    f"{name}: the control mode is back to how it was found (pwm_enable {after}).",
                    ev_fans,
                )
            )
        else:
            checks.append(
                Check(
                    "final.mode",
                    cid,
                    CHECK_FAIL,
                    f"{name}: the control mode is {after}; it was {before} before the run.",
                    ev_fans,
                    reapply_fixes=True,
                )
            )

    # 2. Readback against command on every header the profile controls — the
    #    read-only D3 check, on any daemon.
    in_profile = {c.get("channel_id") for c in doc.get("channels") or [] if c.get("in_profile")}
    for cid in sorted(in_profile):
        fan = final_fans.get(cid)
        if fan is None or fan.get("source") != "hwmon":
            continue
        name = name_of.get(cid, cid)
        cmd = _int(fan.get("pwm_commanded_pct"))
        rb = _int(fan.get("pwm_readback_pct"))
        if cmd is None or rb is None:
            continue  # nothing commanded, or no readback: nothing to compare
        if abs(cmd - rb) <= READBACK_TOLERANCE_PCT:
            checks.append(
                Check(
                    "final.readback",
                    cid,
                    CHECK_PASS,
                    f"{name}: the duty reads back as commanded ({rb} % for {cmd} %).",
                    ("snapshots.final.fans",),
                )
            )
        else:
            checks.append(
                Check(
                    "final.readback",
                    cid,
                    CHECK_FAIL,
                    f"{name}: the duty reads back as {rb} % while the daemon commands {cmd} % "
                    f"— more than the daemon's {READBACK_TOLERANCE_PCT}-point tolerance. "
                    "Something else may be writing it.",
                    ("snapshots.final.fans",),
                    reapply_fixes=True,
                )
            )

    # 3. Every test's own restore.
    for step in doc.get("steps") or []:
        result = step.get("result")
        if not isinstance(result, Mapping):
            continue
        cid = step.get("channel_id")
        name = name_of.get(cid, cid)
        token = str(step.get("restore_outcome") or "")
        if token in RESTORE_OK and not result.get("restore_failed"):
            continue
        original = _int(result.get("original_pct"))
        checks.append(
            Check(
                "final.restore",
                cid,
                CHECK_FAIL,
                f"{name}: after its {step.get('test')} test the daemon reported the header "
                f"was not put back ({_restore_words(token)})"
                + (f"; it was at {original} % before the test." if original is not None else "."),
                (f"steps.{step.get('step_id')}",),
                reapply_fixes=token != "skipped_thermal_force",
            )
        )

    # 4. No override the run did not start with.
    base_over = _override_ids(d.status_body(base))
    final_over = _override_ids(d.status_body(final))
    stray = sorted(final_over - base_over)
    if stray:
        checks.append(
            Check(
                "final.override",
                None,
                CHECK_FAIL,
                "An override is active that was not active when the run began: "
                + ", ".join(stray)
                + ".",
                ("snapshots.final.status",),
            )
        )
    else:
        checks.append(
            Check(
                "final.override",
                None,
                CHECK_PASS,
                "No override is active that was not already active when the run began.",
                ("snapshots.final.status",),
            )
        )

    # 5. The profile, by id and by content.
    base_cfg = doc.get("configuration") or {}
    final_status = d.status_body(final)
    base_id = base_cfg.get("active_profile_id")
    final_id = final_status.get("active_profile_id")
    final_hash = d.profile_hash(d.snapshot_body(final, "profile"))
    if base_id == final_id and (final_hash is None or final_hash == base_cfg.get("profile_hash")):
        checks.append(
            Check(
                "final.profile",
                None,
                CHECK_PASS,
                "The active profile is the one the run began with"
                + (" (same content)." if final_hash else "."),
                ("snapshots.baseline.status", "snapshots.final.status"),
            )
        )
    else:
        checks.append(
            Check(
                "final.profile",
                None,
                CHECK_FAIL,
                f"The active profile changed during the run ({base_id or 'none'} → "
                f"{final_id or 'none'}), or its content did.",
                ("snapshots.baseline.status", "snapshots.final.status"),
            )
        )

    # 6. Thermal state.
    thermal = final_status.get("thermal_state") or "normal"
    checks.append(
        Check(
            "final.thermal",
            None,
            CHECK_PASS if thermal == "normal" else CHECK_FAIL,
            "The daemon's thermal state is normal."
            if thermal == "normal"
            else f"The daemon's thermal state is '{thermal}' at the end of the run.",
            ("snapshots.final.status",),
        )
    )

    # 7. DEC-406 — corrections during the run, and anything no longer holding.
    for cid in sorted(final_fans):
        fan = final_fans[cid]
        if fan.get("source") != "hwmon":
            continue
        name = name_of.get(cid, cid)
        after = _int(fan.get("duty_corrections"))
        before = _int((base_fans.get(cid) or {}).get("duty_corrections"))
        if after is not None and before is not None and after > before:
            checks.append(
                Check(
                    "final.corrections",
                    cid,
                    CHECK_FAIL,
                    f"{name}: the daemon corrected its duty {after - before} time(s) during the "
                    "run — something else changed it (a second writer).",
                    ev_fans,
                )
            )
        if fan.get("duty_not_holding") is True:
            checks.append(
                Check(
                    "final.not_holding",
                    cid,
                    CHECK_FAIL,
                    f"{name}: the daemon has stopped correcting this header's duty because "
                    "its corrections did not hold.",
                    ("snapshots.final.fans",),
                    reapply_fixes=True,
                )
            )
    return checks


def _override_ids(status: Mapping) -> set[str]:
    out: set[str] = set()
    for entry in status.get("overrides") or []:
        if isinstance(entry, Mapping) and entry.get("control_id"):
            out.add(str(entry["control_id"]))
    return out


_RESTORE_WORDS = {
    "write_failed": "the restore write failed",
    "skipped_thermal_force": "thermal protection was forcing the fans, so the restore was skipped",
    "skipped_shutting_down": "the daemon was shutting down",
    "no_original_duty": "its starting duty could not be read",
}


def _restore_words(token: str) -> str:
    if not token:
        return "the restore failed"
    return _RESTORE_WORDS.get(token, token)
