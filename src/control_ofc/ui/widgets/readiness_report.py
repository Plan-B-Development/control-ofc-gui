"""Hardware-readiness verdict, "To fix" guidance, and pop-out report
(DEC-113, shared formatters added in DEC-115).

The following live here so the inline Fans-tab card and the pop-out window share
one source of truth (no drift):

* :func:`detect_readiness_problems` — the single problem-detection pass. Both
  the verdict banner and the "To fix" guidance derive from it.
* :func:`readiness_verdict` — the one-line status shown at the top of the card.
* :func:`build_fix_guidance_html` — GUI-authored "To fix" bullets (disclaimer +
  clickable doc links). Deliberately contains **no daemon-supplied strings**, so
  it is safe to render as rich text without the escaping dance DEC-106 requires.
* :func:`board_identity_line` / :func:`header_summary_line` / :func:`chip_rows` /
  :func:`module_rows` / :func:`thermal_line` — shared section-body formatters
  (DEC-115) so the card's widgets and the report's HTML derive their content
  once and cannot drift (before these, the report had silently dropped the chip
  "Status" and module "Mainline" columns). They return **raw** daemon strings;
  HTML consumers escape, plain table-cell consumers do not.
* :func:`build_readiness_report_html` + :class:`ReadinessReportDialog` — the full
  scrollable report shown in its own window; daemon strings ARE escaped here.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, NamedTuple

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from control_ofc.ui.hwmon_guidance import (
    REMEDIATION_DISCLAIMER,
    VendorQuirk,
    advisory_detail_html,
    detect_module_conflicts,
    dual_chip_warning_html,
    format_driver_status,
    lookup_vendor_quirks,
    quirk_key,
    severity_display,
)
from control_ofc.ui.theme import active_theme

if TYPE_CHECKING:
    from control_ofc.api.models import GpuVerifyResult, HardwareDiagnosticsResult

_HW_COMPAT_URL = (
    "https://github.com/Plan-B-Development/control-ofc-gui/blob/main/"
    "docs/19_Hardware_Compatibility.md"
)
# The step-by-step recovery for a missing secondary Super-I/O. In-app copy stays
# short and sends the reader here rather than trying to fit a six-step procedure
# (including "cut mains power, a reboot will not do") into a card.
_MISSING_HEADERS_URL = (
    "https://github.com/Plan-B-Development/control-ofc-gui/blob/main/"
    "manual/hardware-troubleshooting.md"
    "#some-of-my-fan-headers-are-missing--only-5-of-8-show-up"
)


def _link(url: str, title: str) -> str:
    """Render a clickable anchor with an inline, theme-derived colour.

    The colour is set inline (not via palette/stylesheet) because both the
    inline ``QLabel`` and the pop-out ``QTextBrowser`` inherit the app-wide
    stylesheet, which overrides the palette Link role — inline style is the
    only reliably-applied path for readable link contrast.

    ``url``/``title`` are GUI-authored today, so escaping is a no-op for the
    live callers; it is defence-in-depth so a future daemon-derived link can
    never break out of the ``href`` attribute or inject markup (DEC-106).
    """
    return (
        f'<a href="{escape(url, quote=True)}" '
        f'style="color:{active_theme().status_info}">{escape(title)}</a>'
    )


def _base_conditions(diag: HardwareDiagnosticsResult) -> list[dict]:
    """The observed readiness conditions, in display order (DEC-357).

    Each condition is ``{key, label, fix, doc_url, doc_title, severity}`` where
    every string is GUI-authored (no daemon input). ``severity`` is ``"warn"``
    or ``"critical"``.

    Every entry here is derived from something the daemon **measured** on this
    machine — a collision in the loaded-module list, a chip that did not
    enumerate, a reclaim the watchdog counted. That is what makes a condition
    able to clear itself: fix it in BIOS, refetch, and it is gone. Board/chip
    quirks are not conditions and no longer live here; they are reference
    material keyed on which motherboard you bought, and they moved to
    :func:`board_notes` (DEC-357).

    ``severity`` is ``"critical"`` only where the mechanism risks **damaging
    hardware**. Losing fan control is serious and is ``"warn"`` — the page words
    that as ACTION REQUIRED. Ranking the two together is what let a HIGH "the
    BIOS *may* override fan control" advisory paint a healthy board red.
    """
    hw = diag.hwmon
    board = diag.board
    problems: list[dict] = []

    collisions = getattr(diag, "module_collisions", []) or []
    if collisions:
        problems.append(
            {
                "key": "module_collision",
                "label": "Driver module collision",
                "fix": (
                    "Two drivers are fighting for the same chip. Unload one and "
                    "blacklist it (see the per-pair remediation in the alert), "
                    "then reboot."
                ),
                "doc_url": "https://wiki.archlinux.org/title/Fan_speed_control",
                "doc_title": "Arch Wiki: Fan speed control",
                "severity": "critical",
            }
        )
    elif detect_module_conflicts([m.name for m in diag.kernel_modules if m.loaded]):
        problems.append(
            {
                "key": "module_conflict",
                "label": "Conflicting driver modules loaded",
                "fix": (
                    "Blacklist all but one of the conflicting modules in "
                    "/etc/modprobe.d/ and reboot so a single driver owns the chip."
                ),
                "doc_url": "https://wiki.archlinux.org/title/Fan_speed_control",
                "doc_title": "Arch Wiki: Fan speed control",
                "severity": "critical",
            }
        )

    detected = [c.chip_name for c in hw.chips_detected]
    if dual_chip_warning_html(board.name, list(diag.expected_chips), detected):
        problems.append(
            {
                "key": "dual_chip",
                "label": "Super-I/O chip not enumerated",
                # DEC-326 / `UDOC-h`: this line used to prescribe the
                # update/mmio=on/reboot loop unconditionally, which is futile on
                # a board answering DEVID=0x8883.
                #
                # DEC-332 then retracted the replacement's own conclusion. It
                # said 0x8883 "has no local fix", which was measured false: the
                # bridge is latched by a config-mode unlock from
                # nct6775/w83627ehf and clears on a full power cut. Telling a
                # user to give up costs them 3 of 8 headers permanently, so the
                # discriminator now names the remedy for BOTH readings, and the
                # link points at our own step-by-step rather than at an upstream
                # issue thread the reader has to interpret.
                "fix": (
                    "Two different faults look identical here and they need "
                    "different remedies. Run 'dmesg | grep -i it87': "
                    "DEVID=0xFFFF is a Super-I/O stuck in config mode, cleared "
                    "by rebooting without running sensors-detect. DEVID=0x8883 "
                    "is an ITE bridge latched in config mode — suppress the "
                    "nct6775/w83627ehf modules, then power down fully at the "
                    "wall, because a reboot does not clear it. Full steps in "
                    "the guide below."
                ),
                "doc_url": _MISSING_HEADERS_URL,
                "doc_title": "Manual: recovering missing fan headers",
                "severity": "warn",
            }
        )

    if diag.acpi_conflicts:
        has_it87 = any(c.conflicts_with_driver == "it87" for c in diag.acpi_conflicts)
        fix = (
            "Update it87-dkms-git first (2026-03+ builds sidestep most port "
            "claims via MMIO); if the bind still fails, add "
            "'options it87 ignore_resource_conflict=1' to "
            "/etc/modprobe.d/it87.conf (preferred for ITE chips), or add "
            "'acpi_enforce_resources=lax' to the kernel command line."
            if has_it87
            else "Add 'acpi_enforce_resources=lax' to the kernel command line, "
            "or disable hardware monitoring in BIOS."
        )
        problems.append(
            {
                "key": "acpi",
                "label": "ACPI I/O port conflict",
                "fix": fix,
                "doc_url": "https://wiki.archlinux.org/title/Lm_sensors",
                "doc_title": "Arch Wiki: lm_sensors",
                "severity": "warn",
            }
        )

    reverts = getattr(hw, "enable_revert_counts", None) or {}
    if reverts and max(reverts.values()) > 0:
        problems.append(
            {
                "key": "bios_revert",
                "label": "BIOS/EC reclaiming fan control",
                "fix": (
                    "Disable the BIOS's automatic fan control (Q-Fan / Smart Fan "
                    "/ Fan Xpert) for the affected headers, or set them to full "
                    "manual, then re-test."
                ),
                "doc_url": _HW_COMPAT_URL,
                "doc_title": "Hardware Compatibility Guide",
                # DEC-357: never "critical". A reclaim means the BIOS took fan
                # control back and the daemon's watchdog re-asserted it — the
                # user must act, but nothing is being damaged, and CRITICAL is
                # reserved for mechanisms that damage hardware.
                #
                # The count is NOT ignored: `classify_reclaim_severity` still
                # separates the HIGH bucket, and `build_interference_vm` titles
                # the gauge "High Contention Detected" from it. What changed is
                # only that heavy contention no longer paints the health card
                # red. (This module's own `_RECLAIM_HIGH` copy was deleted with
                # the ternary it served — the live threshold has one definition,
                # in `diagnostics_readiness`.)
                "severity": "warn",
            }
        )

    gpu = diag.gpu
    if gpu and gpu.ppfeaturemask and not gpu.ppfeaturemask_bit14_set:
        problems.append(
            {
                "key": "gpu_ppfeaturemask",
                "label": "GPU fan control disabled (ppfeaturemask)",
                "fix": (
                    "Add 'amdgpu.ppfeaturemask=0xffffffff' to your kernel "
                    "command line and reboot to enable PMFW fan control."
                ),
                "doc_url": "https://wiki.archlinux.org/title/AMDGPU#Fan_control",
                "doc_title": "Arch Wiki: AMDGPU fan control",
                "severity": "warn",
            }
        )
    elif gpu and gpu.fan_control_method == "read_only" and not gpu.ppfeaturemask:
        problems.append(
            {
                "key": "gpu_readonly",
                "label": "GPU fan control unavailable",
                "fix": (
                    "RDNA3+ cards need 'amdgpu.ppfeaturemask=0xffffffff' on the "
                    "kernel command line; add it and reboot."
                ),
                "doc_url": "https://wiki.archlinux.org/title/AMDGPU#Fan_control",
                "doc_title": "Arch Wiki: AMDGPU fan control",
                "severity": "warn",
            }
        )

    if hw.total_headers > 0 and hw.writable_headers == 0:
        problems.append(
            {
                "key": "all_readonly",
                "label": "All PWM headers are read-only",
                "fix": (
                    "Check BIOS fan settings and confirm the correct hwmon "
                    "driver is loaded; run Test PWM Control to confirm."
                ),
                "doc_url": _HW_COMPAT_URL,
                "doc_title": "Hardware Compatibility Guide",
                "severity": "warn",
            }
        )
    if len(hw.chips_detected) == 0:
        problems.append(
            {
                "key": "no_chips",
                "label": "No hwmon chips detected",
                "fix": (
                    "Motherboard fan control may require a kernel driver for "
                    "your Super-I/O chip — see the modules table and the guide."
                ),
                "doc_url": _HW_COMPAT_URL,
                "doc_title": "Hardware Compatibility Guide",
                "severity": "warn",
            }
        )

    return problems


# ── Board notes: reference material, with an evidence status (DEC-357) ─────
#
# A board/chip quirk is knowledge about the hardware, not an observation of it.
# It matches on (board vendor, chip prefix, CPU vendor, board name) and nothing
# about the machine's state, so it can never clear — which is ISA-18.2's
# definition of a nuisance alarm: one that "does not return to normal after the
# correct response is taken". Presented as an alarm it trains the user to ignore
# the stack, and the stack is where the real conditions live.
#
# So a note carries an *evidence* status instead of a severity rollup, and only
# an OBSERVED note reaches the condition list.

#: A note has been confirmed on this machine; it is a live problem.
EVIDENCE_OBSERVED = "observed"
#: Measured counter-evidence exists — the mechanism is not happening here.
EVIDENCE_NOT_OBSERVED = "not_observed"
#: Neither confirmed nor refuted; the check that would settle it has not run.
EVIDENCE_UNVERIFIED = "unverified"
#: Nothing about this machine could ever confirm or refute it (reference only).
EVIDENCE_REFERENCE = "reference"

#: Triggers whose ABSENCE is real counter-evidence.
#:
#: The distinction matters more than it looks. `module_collision` is derived
#: from the loaded-module list and `dual_chip` from expected-vs-detected chips:
#: both are measured on every fetch whether or not anything has been written, so
#: "not present" genuinely means "not happening". `bios_revert` is not like
#: that. `enable_revert_counts` only gains an entry when a reclaim is *counted*
#: (`entry(id).or_insert(0) += 1` in the daemon's pwm_control watchdog), so an
#: empty map is indistinguishable between "the BIOS never reclaimed" and "the
#: daemon has never written, so nothing could have been reclaimed". Reading it
#: as the former would let the page claim a quirk was refuted on a machine that
#: has never attempted fan control — the presence-before-absence trap.
_CONCLUSIVE_ABSENCE: frozenset[str] = frozenset({"module_collision", "dual_chip"})

#: Severity a promoted note carries as a condition, by consequence. Q1: CRITICAL
#: is for risk of damage to hardware; everything else is ACTION REQUIRED.
_PROMOTED_SEVERITY: dict[str, str] = {
    "hardware_damage": "critical",
    "control_loss": "warn",
}


class BoardNote(NamedTuple):
    """One board/chip quirk, with what this machine actually says about it."""

    quirk: VendorQuirk
    key: str  # stable across prose edits — see `quirk_key`
    evidence: str  # one of the EVIDENCE_* constants
    evidence_text: str  # the phrasing shown beside the note
    related_key: str  # condition key this note explains ("" if none)


def quirk_evidence(
    diag: HardwareDiagnosticsResult,
    quirk: VendorQuirk,
    condition_keys: set[str],
    *,
    pwm_control_verified: bool | None = None,
) -> tuple[str, str, str]:
    """Return ``(evidence, evidence_text, related_key)`` for one quirk.

    ``pwm_control_verified`` is the outcome of a PWM write verification on this
    machine: ``True`` (writes land), ``False`` (they did not), ``None`` (never
    run). It is the only thing that can settle a quirk whose mechanism is
    "writes are accepted and silently ignored", because no field on
    ``GET /diagnostics/hardware`` reports that — which is exactly why the
    honest answer in the absence of a verify is *unverified* rather than *fine*.
    """
    if quirk.consequence == "none":
        return (EVIDENCE_REFERENCE, "reference for this hardware", "")

    trigger = quirk.trigger
    if trigger:
        # `module_conflict` is the GUI-side fallback for daemons that predate
        # `module_collisions`; it detects the same pair, so it promotes the same
        # notes. Missing it would silence the damage advisories on exactly the
        # older daemons least likely to be protected elsewhere.
        present = trigger in condition_keys or (
            trigger == "module_collision" and "module_conflict" in condition_keys
        )
        if present:
            return (EVIDENCE_OBSERVED, "observed on this system", trigger)
        if trigger in _CONCLUSIVE_ABSENCE:
            return (EVIDENCE_NOT_OBSERVED, "not present on this system", "")
        # Falls through: absence of this trigger proves nothing on its own.

    if pwm_control_verified is True:
        return (EVIDENCE_NOT_OBSERVED, "not observed — fan control tested working", "")
    if pwm_control_verified is False:
        return (EVIDENCE_OBSERVED, "observed — fan control did not test clean", "")
    return (EVIDENCE_UNVERIFIED, "not yet verified on this system", "")


def board_notes(
    diag: HardwareDiagnosticsResult,
    *,
    condition_keys: set[str] | None = None,
    pwm_control_verified: bool | None = None,
) -> list[BoardNote]:
    """Every board/chip quirk matching this hardware, most-severe first.

    ``condition_keys`` is the set of condition keys already detected; omit it
    and the base conditions are derived here. Passing it avoids computing them
    twice when the caller already has them.
    """
    if condition_keys is None:
        # `_base_conditions`, not `detect_readiness_problems`: a trigger must
        # name a condition the daemon MEASURED. Deriving it from the full set
        # would include conditions promoted from notes, letting a note's
        # evidence depend on another note's evidence.
        condition_keys = {p["key"] for p in _base_conditions(diag)}
    notes: list[BoardNote] = []
    for quirk in advisory_rows(diag):
        evidence, text, related = quirk_evidence(
            diag, quirk, condition_keys, pwm_control_verified=pwm_control_verified
        )
        notes.append(BoardNote(quirk, quirk_key(quirk), evidence, text, related))
    return notes


def promoted_conditions(notes: list[BoardNote]) -> list[dict]:
    """Conditions minted by an observed note that no existing condition covers.

    A note whose ``related_key`` is set is already explained by a condition card
    on the page, so promoting it too would print the same problem twice — the
    duplication that made the old rollup say "review the quirk notes above"
    while sorting itself above them. Only a note with no owning condition — one
    whose mechanism only a write can reveal — mints its own.
    """
    out: list[dict] = []
    for note in notes:
        if note.evidence != EVIDENCE_OBSERVED or note.related_key:
            continue
        severity = _PROMOTED_SEVERITY.get(note.quirk.consequence)
        if severity is None:
            continue
        out.append(
            {
                "key": f"quirk_{note.key}",
                "label": note.quirk.summary,
                "fix": (
                    "A fan-control test on this system did not come back clean, "
                    "and this board has a documented quirk that matches. Open the "
                    "board note below for the remedy."
                ),
                "doc_url": _HW_COMPAT_URL,
                "doc_title": "Hardware Compatibility Guide",
                "severity": severity,
            }
        )
    return out


def detect_readiness_problems(
    diag: HardwareDiagnosticsResult,
    *,
    pwm_control_verified: bool | None = None,
) -> list[dict]:
    """Return the conditions requiring the user's attention, in display order.

    Conditions the daemon measured, plus any board note this machine has
    confirmed and no other condition already covers. Same dict shape as before
    (``{key, label, fix, doc_url, doc_title, severity}``).

    ``pwm_control_verified`` defaults to ``None`` ("never tested"), under which
    nothing is promoted — so every existing caller keeps the pre-DEC-357
    contract of "conditions derived from `diag` alone".
    """
    problems = _base_conditions(diag)
    notes = board_notes(
        diag,
        condition_keys={p["key"] for p in problems},
        pwm_control_verified=pwm_control_verified,
    )
    problems.extend(promoted_conditions(notes))
    return problems


def gpu_verify_problems(result: GpuVerifyResult) -> list[dict]:
    """GUI-authored "To fix" guidance for a GPU fan verify outcome (DEC-120).

    Returns problem dicts (same shape as :func:`detect_readiness_problems` —
    ``{key, label, fix, doc_url, doc_title, severity}``) for the failing
    verdicts; an empty list when control verified or the result is purely
    informational (``effective`` / ``zero_rpm_suppressed`` / ``rpm_unavailable``).
    Every string is GUI-authored — the daemon's ``details`` are never rendered
    (DEC-106). These are the *behavioural* failures the static readiness pass
    cannot see: writes accepted but silently ignored, the fan not spinning, or a
    BIOS reclaim.
    """
    arch_url = "https://wiki.archlinux.org/title/AMDGPU#Fan_control"
    arch_title = "Arch Wiki: AMDGPU fan control"
    specs = {
        "curve_not_applied": {
            "key": "gpu_verify_curve_not_applied",
            "label": "GPU fan write had no effect",
            "fix": (
                "The GPU accepted the fan-control write but did not apply it. Add "
                "'amdgpu.ppfeaturemask=0xffffffff' to the kernel command line and "
                "reboot; if it is already set, this is usually an SMU firmware / "
                "driver mismatch — check the GPU advisories above and your kernel "
                "version."
            ),
        },
        "no_rpm_effect": {
            "key": "gpu_verify_no_rpm_effect",
            "label": "GPU fan did not respond",
            "fix": (
                "The fan curve was applied but the fan RPM did not change. This "
                "points to an SMU firmware issue or a known kernel regression for "
                "this GPU — check the advisories above and consider a different "
                "kernel. Confirm the fan is physically connected."
            ),
        },
        "pwm_enable_reverted": {
            "key": "gpu_verify_pwm_reverted",
            "label": "BIOS/EC reclaimed GPU fan control",
            "fix": (
                "pwm1_enable reverted to automatic during the test. Disable any "
                "vendor 'Smart Fan' / EC fan-control option in firmware setup, "
                "then re-test."
            ),
        },
        "write_failed": {
            "key": "gpu_verify_write_failed",
            "label": "GPU fan write was rejected",
            "fix": (
                "The driver/firmware rejected the fan write outright. Ensure "
                "'amdgpu.ppfeaturemask=0xffffffff' is set and the amdgpu driver is "
                "bound to this GPU (not vfio-pci), then re-test."
            ),
        },
    }
    spec = specs.get(result.result)
    if spec is None:
        return []
    return [{**spec, "doc_url": arch_url, "doc_title": arch_title, "severity": "critical"}]


def readiness_verdict(diag: HardwareDiagnosticsResult) -> tuple[str, str]:
    """Return ``(verdict_text, css_class)`` for the readiness banner."""
    hw = diag.hwmon
    problems = detect_readiness_problems(diag)
    ts = diag.thermal_safety
    thermal = f"thermal safety {ts.state}" if ts and ts.state else "thermal safety unknown"
    if not problems:
        return (
            f"✓ System ready — {hw.total_headers} PWM header(s), "
            f"{hw.writable_headers} writable · {thermal}",
            "SuccessChip",
        )
    n = len(problems)
    phrase = "issue needs" if n == 1 else "issues need"
    critical = any(p["severity"] == "critical" for p in problems)
    cls = "CriticalChip" if critical else "WarningChip"
    return (
        f"⚠ {n} {phrase} attention — see the checklist below, "
        f"or open the full report for the complete detail",
        cls,
    )


def build_fix_guidance_html(diag: HardwareDiagnosticsResult) -> str | None:
    """Return the "To fix" block (rich text), or ``None`` when nothing is wrong.

    GUI-authored content only — safe to render as rich text with external
    links enabled (no daemon strings are interpolated; DEC-106).
    """
    problems = detect_readiness_problems(diag)
    if not problems:
        return None
    parts = ["<b>To fix:</b>"]
    for p in problems:
        parts.append(
            f"&nbsp;&nbsp;• <b>{p['label']}</b> — {p['fix']} {_link(p['doc_url'], p['doc_title'])}"
        )
    parts.append(f"<br><i>⚠ {REMEDIATION_DISCLAIMER}</i>")
    return "<br>".join(parts)


# ── Shared section-body formatters (DEC-115) ──────────────────────────────
# One derivation per section, consumed by BOTH the inline card (QLabel /
# QTableWidget) and the pop-out report (HTML). Strings are returned raw — the
# HTML consumer escapes them, the table-cell consumer sets them verbatim.


class ChipRow(NamedTuple):
    """One detected-chip row. ``status`` is computed from driver-load state."""

    chip: str
    driver: str
    status: str
    mainline: str
    headers: str


class ModuleRow(NamedTuple):
    """One kernel-module row."""

    name: str
    loaded: str
    mainline: str


def board_identity_line(diag: HardwareDiagnosticsResult) -> str | None:
    """Return ``"vendor — name — BIOS x"`` (no ``"Board:"`` prefix), or ``None``
    when the board reports neither vendor nor name."""
    board = diag.board
    parts = [p for p in (board.vendor, board.name) if p]
    if not parts:
        return None
    if board.bios_version:
        parts.append(f"BIOS {board.bios_version}")
    return " — ".join(parts)


def header_summary_line(hw) -> str:
    """Return the one-line PWM-header count summary (GUI text; ints only)."""
    return f"{hw.total_headers} PWM header(s) detected, {hw.writable_headers} writable"


def chip_rows(diag: HardwareDiagnosticsResult) -> list[ChipRow]:
    """Per-chip display rows. ``status`` reflects whether the expected driver
    is among the loaded kernel modules (computed once for all chips)."""
    loaded = {m.name for m in diag.kernel_modules if m.loaded}
    return [
        ChipRow(
            chip=c.chip_name,
            driver=c.expected_driver,
            status=format_driver_status(c.chip_name, c.expected_driver in loaded),
            mainline="Yes" if c.in_mainline_kernel else "No (out-of-tree)",
            headers=str(c.header_count),
        )
        for c in diag.hwmon.chips_detected
    ]


def module_rows(diag: HardwareDiagnosticsResult) -> list[ModuleRow]:
    """Per-kernel-module display rows."""
    return [
        ModuleRow(
            name=m.name,
            loaded="Loaded" if m.loaded else "Not loaded",
            mainline="Yes" if m.in_mainline else "No",
        )
        for m in diag.kernel_modules
    ]


def severity_hex(css_class: str, t) -> str:
    """Resolve a severity chip class to its themed hex colour (DEC-158).

    The single place the pop-out report (HTML — no QSS class cascade) derives
    the same colours the inline panel gets from the ``.*Chip`` stylesheet rules,
    so the two presentations cannot drift.
    """
    return {
        "CriticalChip": t.status_crit,
        "WarningChip": t.status_warn,
        "CautionChip": t.status_caution,
        "InfoChip": t.status_info,
        "SuccessChip": t.status_ok,
    }.get(css_class, t.text_primary)


def advisory_rows(diag: HardwareDiagnosticsResult) -> list[VendorQuirk]:
    """Vendor/chip advisories for the detected hardware, most-severe first.

    Shared by the inline Troubleshooting panel and the pop-out report
    (DEC-115/DEC-158) so both render the same advisories in the same order.
    De-duplicated by summary — a board with two Super-I/O chips can match one
    generic vendor quirk twice.
    """
    board = diag.board
    quirks: list[VendorQuirk] = []
    seen: set[str] = set()
    for chip in diag.hwmon.chips_detected:
        for q in lookup_vendor_quirks(
            board.vendor,
            chip.chip_name,
            cpu_vendor=diag.cpu_vendor,
            board_name=board.name,
        ):
            if q.summary in seen:
                continue
            seen.add(q.summary)
            quirks.append(q)
    quirks.sort(key=lambda q: severity_display(q.severity).rank, reverse=True)
    return quirks


def thermal_line(ts) -> str | None:
    """Return the one-line thermal-safety summary, or ``None`` when no thermal
    info is present. Only ``state`` is daemon-supplied (escape in HTML)."""
    if ts is None:
        return None
    # DEC-269: the daemon changed what this field answers. It used to mean "is a
    # CpuTemp sensor present?"; since the freshness filter it means "is there a
    # CURRENT reading?" — false for a sensor that is still listed but has
    # stopped updating. Rendering that as "NOT found" produced
    # "State: emergency · CPU sensor: NOT found" while the emergency was
    # triggered by a sensor sitting right there in the sensor table.
    found = "reading current" if ts.cpu_sensor_found else "no current reading"
    return (
        f"State: {ts.state} · CPU sensor: {found} · "
        f"emergency {ts.emergency_threshold_c:.0f}°C · "
        f"release {ts.release_threshold_c:.0f}°C"
    )


def build_readiness_report_html(diag: HardwareDiagnosticsResult) -> str:
    """Build the full, self-contained HTML report for the pop-out window.

    Daemon-supplied strings are HTML-escaped; GUI guidance text is trusted.
    """
    t = active_theme()
    hw = diag.hwmon
    verdict_text, verdict_cls = readiness_verdict(diag)
    sev_color = severity_hex(verdict_cls, t)

    def h(title: str) -> str:
        return f'<h3 style="color:{t.text_primary};margin-bottom:2px">{escape(title)}</h3>'

    out: list[str] = []
    out.append(
        f'<div style="color:{sev_color};font-size:large;font-weight:bold">'
        f"{escape(verdict_text)}</div>"
    )

    # Board + summary (shared formatters, DEC-115)
    out.append(h("Summary"))
    identity = board_identity_line(diag)
    if identity:
        out.append(f"<div>Board: {escape(identity)}</div>")
    out.append(f"<div>{header_summary_line(hw)}.</div>")

    # Advisories — board/chip quirks, most-severe first, with the same
    # per-severity colour + icon + word as the inline panel (DEC-158). Shared
    # data + presentation (advisory_rows / severity_display) so the report and
    # the panel cannot drift (DEC-115).
    advisories = advisory_rows(diag)
    if advisories:
        out.append(h("Advisories"))
        for q in advisories:
            d = severity_display(q.severity)
            color = severity_hex(d.css_class, t)
            out.append(
                f'<div style="margin-bottom:6px">'
                f'<span style="color:{color};font-weight:bold">'
                f"{d.glyph} {d.word}</span> <b>{escape(q.summary)}</b>"
            )
            detail = advisory_detail_html(q.details)
            if detail:
                out.append(f'<div style="color:{t.text_secondary};margin-left:10px">{detail}</div>')
            out.append("</div>")

    # Detected chips — same five columns as the inline card (DEC-115).
    crows = chip_rows(diag)
    if crows:
        out.append(h("Detected hardware"))
        rows = [
            '<tr><th align="left">Chip</th><th align="left">Driver</th>'
            '<th align="left">Status</th><th align="left">Mainline</th>'
            '<th align="left">Headers</th></tr>'
        ]
        for r in crows:
            rows.append(
                f"<tr><td>{escape(r.chip)}</td><td>{escape(r.driver)}</td>"
                f"<td>{escape(r.status)}</td><td>{escape(r.mainline)}</td>"
                f"<td>{escape(r.headers)}</td></tr>"
            )
        out.append(f'<table cellpadding="4">{"".join(rows)}</table>')

    # Kernel modules — same three columns as the inline card (DEC-115).
    mrows = module_rows(diag)
    if mrows:
        out.append(h("Kernel modules"))
        rows = [
            '<tr><th align="left">Module</th><th align="left">Loaded</th>'
            '<th align="left">Mainline</th></tr>'
        ]
        for r in mrows:
            rows.append(
                f"<tr><td>{escape(r.name)}</td><td>{escape(r.loaded)}</td>"
                f"<td>{escape(r.mainline)}</td></tr>"
            )
        out.append(f'<table cellpadding="4">{"".join(rows)}</table>')

    # Thermal + GPU
    thermal = thermal_line(diag.thermal_safety)
    if thermal:
        out.append(h("Thermal safety"))
        out.append(f"<div>{escape(thermal)}</div>")
    if diag.gpu:
        g = diag.gpu
        out.append(h("GPU"))
        out.append(
            f"<div>{escape(g.model_name or 'AMD D-GPU')} (PCI {escape(g.pci_bdf)}) · "
            f"fan control: {escape(g.fan_control_method)}</div>"
        )

    # To fix
    fix = build_fix_guidance_html(diag)
    if fix:
        out.append(h("To fix"))
        out.append(f'<div style="color:{t.status_warn}">{fix}</div>')

    out.append(
        f'<hr><div style="color:{t.text_secondary};font-size:small">'
        f"For full hardware-compatibility detail see the "
        f"{_link(_HW_COMPAT_URL, 'Hardware Compatibility Guide')}.</div>"
    )

    body = "".join(out)
    return f'<div style="color:{t.text_primary}">{body}</div>'


class ReadinessReportDialog(QDialog):
    """A themed, resizable window showing the full hardware-readiness report.

    Uses a ``QTextBrowser`` so the report scrolls for arbitrary volume and all
    links open externally with a single click.
    """

    def __init__(self, html: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReadinessReport_Dialog")
        self.setWindowTitle("Hardware Readiness — Full Report")
        self.resize(720, 640)

        layout = QVBoxLayout(self)

        self._browser = QTextBrowser()
        self._browser.setObjectName("ReadinessReport_Browser")
        self._browser.setOpenExternalLinks(True)
        self._browser.setHtml(html)
        layout.addWidget(self._browser, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("ReadinessReport_Btn_close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def set_html(self, html: str) -> None:
        """Replace the report contents (used when reopened with fresh data)."""
        self._browser.setHtml(html)
