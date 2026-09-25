"""Chip-family knowledge base for hardware readiness guidance.

Maps Super I/O chip name prefixes to driver information, BIOS tips,
known manufacturer quirks, and external documentation links.
Also provides vendor+chip specific quirk entries for boards where
BIOS firmware actively interferes with Linux fan control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

# Shown beneath every "To fix" block across the diagnostics UI (DEC-113).
# Lives here (the lowest-level guidance module) so both the dual-chip warning
# and the readiness "To fix" guidance can share it without a circular import.
REMEDIATION_DISCLAIMER = (
    "These steps change kernel parameters, driver/module configuration, or "
    "firmware (UEFI/BIOS) settings. Apply them at your own risk and back up "
    "your configuration first — an incorrect kernel parameter can stop the "
    "system booting."
)


# ---------------------------------------------------------------------------
# Advisory severity presentation (DEC-158)
#
# One source of truth for how each advisory severity renders across the
# diagnostics UI — badge word, monochrome glyph, themed chip class, ordering
# rank, default open/closed state, and badge weight. Shared by the inline
# Troubleshooting panel (Qt widgets) and the pop-out report (HTML) so the two
# cannot drift (the DEC-115 single-source rule).
#
# Colour is paired with a glyph AND the severity word so it is never the only
# cue (WCAG 1.4.1 "Use of Color"). Glyphs carry a U+FE0E VARIATION SELECTOR-15
# suffix where the base code point has an emoji presentation (⚠ U+26A0, ⛔
# U+26D4) so Qt 6.9+ renders them as monochrome text, not colour emoji. The
# word is authoritative: if a glyph is missing from the user's font the meaning
# is still carried by the word + colour.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeverityDisplay:
    """Presentation metadata for one advisory-severity tier."""

    severity: str  # canonical key ("critical"|"high"|"medium"|"info"|...)
    word: str  # uppercase badge label, e.g. "CRITICAL"
    glyph: str  # leading monochrome glyph
    css_class: str  # themed chip class (defined in theme.py)
    rank: int  # higher = more severe (used to sort / aggregate)
    default_expanded: bool  # open the detail by default at author time
    bold: bool  # badge weight — an extra, non-colour severity cue


# Canonical map. "warn" is the issue-checklist vocabulary
# (``detect_readiness_problems`` emits only "warn"/"critical"); it shares the
# HIGH presentation but keeps its own word so the checklist still reads "WARN".
# Ranks are compared RELATIVELY everywhere (sorting, ">= warn", "> info") and
# never asserted as absolute values, so inserting a tier only requires keeping
# the order right.
#
# "low" was added 2026-08-26. It had been shipping on a Gigabyte X870 quirk
# without a tier, which made it *unknown*, and an unknown severity took the INFO
# presentation in the advisory panel while `detect_readiness_problems` — testing
# `severity != "info"`, a string — still counted it a problem and rolled it up as
# WARN. Giving it a real tier fixes the mismatch without silencing it: it stays
# actionable (rank above info), but reads calmly, which is what "low" means.
_SEVERITY_DISPLAY: dict[str, SeverityDisplay] = {
    "critical": SeverityDisplay("critical", "CRITICAL", "⛔︎", "CriticalChip", 6, True, True),
    "high": SeverityDisplay("high", "HIGH", "⚠︎", "WarningChip", 5, True, True),
    "warn": SeverityDisplay("warn", "WARN", "⚠︎", "WarningChip", 4, True, True),
    "medium": SeverityDisplay("medium", "MEDIUM", "⚠︎", "CautionChip", 3, False, False),
    "low": SeverityDisplay("low", "LOW", "⚠︎", "CautionChip", 2, False, False),
    "info": SeverityDisplay("info", "INFO", "ⓘ", "InfoChip", 1, False, False),
}

_INFO_DISPLAY = _SEVERITY_DISPLAY["info"]

# ⚠ A tier is a statement about the ADVISORY, never evidence about the MACHINE
# (DEC-357). Do not add a consumer that escalates an alarm from a severity: a
# quirk matches on board identity, so ranking off this map painted the System
# State health card CRITICAL on a board where nothing had been observed at all.
# `docs/03 § Alarm policy` is the rule — severity comes from an observed
# consequence (`VendorQuirk.consequence`), never from which table row matched.
#
# The two predicates that read this map for exactly that purpose,
# `is_actionable_severity` and `is_high_severity`, lost their last production
# caller in DEC-357 and now live in `tests/severity_invariants.py` (row `SSN-k`).
# They still pin the vocabulary rule they always encoded — nothing the panel
# paints as INFO may be counted a problem, and only CRITICAL/HIGH escalate — but
# they are no longer reachable from any render path, which is the point.


def severity_display(severity: str) -> SeverityDisplay:
    """Return the presentation metadata for an advisory severity (DEC-158).

    Unknown or not-yet-emitted severities (e.g. a future ``"low"``) degrade to
    the calm INFO treatment rather than masquerading as a warning (D1), but keep
    their own word so nothing is mislabelled.
    """
    key = (severity or "").strip().lower()
    known = _SEVERITY_DISPLAY.get(key)
    if known is not None:
        return known
    return SeverityDisplay(
        severity=key or "info",
        word=(severity or "INFO").strip().upper() or "INFO",
        glyph=_INFO_DISPLAY.glyph,
        css_class=_INFO_DISPLAY.css_class,
        rank=_INFO_DISPLAY.rank,
        default_expanded=_INFO_DISPLAY.default_expanded,
        bold=_INFO_DISPLAY.bold,
    )


# Detail items at or below this length, when there are >=3 of them, read as a
# scannable parallel list and render as bullets; anything longer or fewer
# renders as prose paragraphs (NN/g: reserve bullets for >=3 short, parallel
# items; prefer prose otherwise).
_ADVISORY_BULLET_MAXLEN = 90


def advisory_detail_html(details: list[str]) -> str:
    """Render advisory detail items as clean rich text (DEC-158, D6).

    Reduces bullet overuse: one or two items — or any long item — render as
    prose paragraphs; only three or more short, parallel items render as a
    compact bullet list. Every item is HTML-escaped: the strings are
    GUI-authored, but escaping keeps future edits safe inside a rich-text label.
    Returns an empty string when there is no detail to show.
    """
    items = [d.strip() for d in details if d and d.strip()]
    if not items:
        return ""
    listy = len(items) >= 3 and all(len(d) <= _ADVISORY_BULLET_MAXLEN for d in items)
    if listy:
        return "<br>".join(f"&#8226;&nbsp;{escape(d)}" for d in items)
    return "<br><br>".join(escape(d) for d in items)


@dataclass(frozen=True)
class ChipGuidance:
    chip_prefix: str
    driver_name: str
    in_mainline: bool
    driver_package: str
    driver_url: str
    bios_tips: list[str] = field(default_factory=list)
    known_issues: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class VendorQuirk:
    """Board-vendor + chip combination with known BIOS interference.

    `platform` (DEC-110) optionally scopes a quirk to one CPU vendor:
    ``"intel"`` / ``"amd"`` / ``None`` (matches any). Used to distinguish
    e.g. the MSI Z890 NCT6687DR ``msi_alt1`` quirk from MSI AMD X870E
    boards that ship the same chip but a different BIOS surface. Empty
    or ``None`` preserves the pre-DEC-110 behaviour (matches all).

    `board_pattern` (DEC-110) optionally scopes a quirk to a board-name
    substring (case-insensitive) on top of vendor + chip. Used when a
    chip name appears on boards from the same vendor across multiple
    platforms (e.g. NCT6687D auto-detected on MSI Z690/Z790 vs.
    NCT6687DR ``msi_alt1`` on MSI Z890). Empty string preserves the
    pre-DEC-110 behaviour (no board scoping).

    `consequence` + `trigger` (DEC-357) are the machine-readable half of what
    the prose already says, and they exist because `severity` alone cannot
    answer the only two questions a reader actually has:

    * **`consequence`** — what happens if the mechanism fires. A quirk that can
      corrupt non-volatile fan registers and one that means "the BIOS may take
      your fan curve back" are not the same kind of bad, and ranking them on one
      `severity` axis made the second read like the first.
    * **`trigger`** — the *observable* condition under which the quirk stops
      being reference material and becomes a live problem. It names an existing
      key from :func:`detect_readiness_problems`, so a quirk never mints a
      condition of its own; it attaches to the condition that already detects
      its mechanism. ``""`` means the quirk is permanently reference-only.

    Both default, so a quirk that is genuinely just a note needs neither. They
    are validated against :data:`QUIRK_CONSEQUENCES` / :data:`QUIRK_TRIGGERS` by
    ``test_every_quirk_declares_a_registered_consequence_and_trigger`` — an unregistered
    token fails *silently* otherwise (it simply never promotes), which is the
    failure mode DEC-334 says to weight highest.
    """

    vendor_pattern: str
    chip_prefix: str
    severity: str  # "critical" | "high" | "medium" | "info"
    summary: str
    #: Stable, declared identity — see :func:`quirk_key`. Required on every entry
    #: in :data:`VENDOR_QUIRKS_DB` and swept by
    #: ``test_every_quirk_declares_a_unique_stable_id``; the default exists only
    #: so an ad-hoc quirk built in a test need not invent one.
    id: str = ""
    details: list[str] = field(default_factory=list)
    platform: str | None = None  # "intel" | "amd" | None (DEC-110)
    board_pattern: str = ""  # case-insensitive substring (DEC-110)
    consequence: str = "none"  # see QUIRK_CONSEQUENCES (DEC-357)
    trigger: str = ""  # see QUIRK_TRIGGERS (DEC-357)


# What actually happens if a quirk's mechanism fires (DEC-357).
#
# "hardware_damage" is the only tier that earns CRITICAL, and on the current
# data set exactly one mechanism qualifies: the out-of-tree `nct6687` claiming
# an NCT6797D/NCT6798D and writing into its non-volatile fan registers, which
# has bricked a CPU_FAN header in the wild. "control_loss" is the far commoner
# and far milder case — the BIOS/EC takes fan control back, the daemon's
# watchdog re-asserts it, and nothing is harmed. Before this split the two were
# ranked on one axis and a HIGH "the BIOS *may* override fan control" advisory
# rendered as a red CRITICAL on a perfectly healthy board.
QUIRK_CONSEQUENCES: frozenset[str] = frozenset({"hardware_damage", "control_loss", "none"})

# The observable condition that promotes a quirk from reference material to a
# live problem (DEC-357). Every token is a `key` emitted by
# :func:`~control_ofc.ui.widgets.readiness_report.detect_readiness_problems`,
# deliberately: a quirk attaches to the condition that already detects its
# mechanism rather than minting a second card saying the same thing. "" means
# the quirk is never promotable — it is permanent reference material.
#
# `module_collision` covers `module_conflict` too (the GUI-side fallback for
# daemons predating `module_collisions`); the promotion check tests both keys.
QUIRK_TRIGGERS: frozenset[str] = frozenset({"module_collision", "bios_revert", "dual_chip", ""})


def quirk_key(quirk: VendorQuirk) -> str:
    """A stable identity for one quirk, safe to persist (DEC-357).

    It is the quirk's **declared** ``id``, and both halves of that matter.

    *Declared*, because a derived one does not work. The obvious derivation —
    the four scope fields, which are what make an entry distinct in the lookup —
    is **not unique**: six of the 36 entries share a scope tuple with another
    (four MSI ``nct6687`` quirks alone), because one board/chip pair can carry
    several unrelated notes. An acknowledgement stored against that key would
    silence a *different* note than the one the user dismissed.

    *Stable*, because the alternative derivation — hashing ``summary`` — moves
    the moment someone corrects a typo, and the silence the user asked for would
    be lost with it. Same reasoning as keying a fan alias on a stable id rather
    than on a display name.

    An ad-hoc quirk with no id (only tests build those, and nothing persists
    them) falls back to the scope tuple so callers need not special-case it.
    """
    if quirk.id:
        return quirk.id
    return ":".join(
        (
            quirk.vendor_pattern,
            quirk.chip_prefix,
            quirk.platform or "*",
            quirk.board_pattern or "*",
        )
    )


# Gigabyte "Full Speed" and the reclaim remedy, worded once (DEC-421, BRD-16).
#
# "Full Speed" is a FAIL-SAFE, never a way to make Linux control work: it sets
# what the firmware does while it owns the fan. On some boards it also locks
# manual mode out (frankcrawford/it87 #115: "A fixed-speed mode rejects
# pwm_enable=1"), and on a pre-PR #128 build one owner saw Full-Speed fans drop
# to 0% as the module loaded (#79). Earlier copy presented it as required, or
# as "the safe fallback" for a header that keeps being taken back, worded
# differently in each place; every entry now shares these two strings.
_GB_FULL_SPEED_NOTE = (
    "BIOS 'Full Speed' for a header is a fail-safe, not a fix: the firmware runs "
    "that fan at 100% whenever it owns it, but on some boards it also locks Linux "
    "out of the header (frankcrawford/it87 #115), and on driver builds older than "
    "2026-08-24 one owner saw Full-Speed fans drop to 0% as the driver loaded "
    "(#79). Use it only for a fan you would rather run flat out than control."
)
# it87 v2.0 (frankcrawford/it87 PR #132, 2026-09-09) names Gigabyte chips after
# the board's SIV — `it8696_a008090a` — whenever it can read one. Every stable
# header id embeds the chip name, so a rebuild orphans every persisted id on
# those boards (register row BRD-a). One wording for every entry that mentions
# it. c567739 is NOT "the last pre-rename master" — 533b88c (2026-09-07) came
# after it — but that commit only touched the Makefile's version string, so
# c567739 carries the same driver code and is the build this project runs on its
# own X870E AORUS MASTER.
_IT87_V2_RENAME_NOTE = (
    "⚠ it87-dkms-git builds from 2026-09-09 (it87 v2.0) rename Gigabyte chips "
    "after the board's ID (e.g. it8696_a008090a), which changes every fan "
    "header's id: re-check pump roles, fan names and profile members after "
    "rebuilding. To keep the old names, build commit c567739 (2026-08-25) — the "
    "same driver code as the last build before the rename, including PR #128."
)
_GB_RECLAIM_NOTE = (
    "If a header keeps being taken back: keep it87-dkms-git current — PR #128 "
    "(2026-08-24) addressed the firmware logic that retakes headers on IT8689E, "
    "IT8688E revision 2 and the secondary IT879x chips — and let the daemon's "
    "watchdog re-assert manual mode. If a header still will not hold, use another "
    "header or an external fan controller."
)

CHIP_GUIDANCE_DB: list[ChipGuidance] = [
    # DEC-106: narrower nct679x entries take precedence over the generic
    # nct679 fallthrough below thanks to longest-prefix matching in
    # `lookup_chip_guidance`. Each entry calls out a chip-specific quirk
    # or supported-board hint without changing the underlying driver
    # binding (still `nct6775` in-kernel for all of them).
    # Curator 2026-09-24 (DEC-421): mainline reports a whole ID class as
    # `nct6799` — NCT6799D, NCT6796D-S (0xd801/0xd802) and the ASUS NCT6701D
    # (0xd806) all land here — so the entry describes the class, not one part.
    ChipGuidance(
        chip_prefix="nct6799",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        known_issues=[
            "The in-kernel driver reports several parts under this one name: "
            "NCT6799D (ASUS AM5 600-series boards), NCT6796D-S (ASRock X870 Nova "
            "WiFi and the AM5 Pro RS / PG Lightning boards), and the NCT6701D on "
            "ASUS AM5 800-series and Z890/B860 boards.",
            "ASUS NCT6701D boards: fans and voltages read correctly but most "
            "temperatures are not meaningful (lm-sensors issue #544); B850, B840, "
            "B860 and Z890 boards are not on the kernel's ASUS WMI access lists, "
            "so an ACPI I/O-port conflict may block the bind; and the firmware "
            "has been seen to put a header straight back into automatic mode "
            "after a manual write. Run Test PWM Control before relying on it.",
            "ASRock Taichi boards (X870E / X670E / B650E Taichi and Taichi Lite) "
            "pair this chip with an NCT6686D. Both `nct6775` and the NCT6686D "
            "driver are legitimately loaded there — see the ASRock dual-Nuvoton "
            "board note.",
        ],
        notes=(
            "Nuvoton NCT6799D / NCT6796D-S / NCT6701D (reported as nct6799) — "
            "mainline kernel support via nct6775."
        ),
    ),
    ChipGuidance(
        chip_prefix="nct6798",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        known_issues=[
            "Driver class 0xd428 (a real chip usually reports 0xd42b) — distinct "
            "from NCT6797D's 0xd450. The same name also covers the NCT5585D "
            "(kernel 7.3+) and the NCT6796D-E/-R that some ASRock boards carry.",
            "A current out-of-tree `nct6687` does not claim this chip, so the "
            "DEC-105 brick-risk collision does not apply — unless `nct6687` is "
            "loaded with force=1, which since nct6687d PR #174 attaches to any "
            "chip ID in 0xD000-0xDFFF, this one included. Never do that here.",
        ],
        notes=(
            "Nuvoton NCT6798D — mainline kernel support. Common on AM4 "
            "500-series ASUS / ASRock boards and on ASUS / ASRock Intel "
            "600/700-series boards (e.g. ASUS TUF GAMING X570-PLUS, ASRock "
            "B550 Steel Legend, ASUS ROG STRIX Z790)."
        ),
    ),
    ChipGuidance(
        chip_prefix="nct6796",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        known_issues=[
            "A chip reported as nct6796 is the plain NCT6796D (e.g. ASRock Z790 "
            "PG Lightning / Z790 Pro RS, per ASRock's manuals). The NCT6796D-S on "
            "ASRock AM5 boards such as the X870 Nova WiFi is reported as nct6799 "
            "instead, and the NCT6796D-E as nct6798.",
        ],
        notes="Nuvoton NCT6796D — mainline kernel support via nct6775.",
    ),
    ChipGuidance(
        chip_prefix="nct679",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        bios_tips=[
            "Disable ACPI hardware monitoring (AMW0) if the driver fails to bind.",
            "Under nct6775 the pwm files are always writable and the daemon sets "
            "manual mode itself — no BIOS setting unlocks them. If the firmware "
            "keeps taking a header back, see this board's notes.",
        ],
        known_issues=[
            "ASUS boards may have ACPI OpRegion conflicts on I/O ports 0x0290-0x0299.",
            "MSI boards may need 'acpi_enforce_resources=lax' kernel parameter.",
        ],
        notes="Nuvoton NCT679x series — widely supported in mainline kernel.",
    ),
    ChipGuidance(
        chip_prefix="nct677",
        driver_name="nct6775",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6775.html",
        bios_tips=[
            "Disable ACPI hardware monitoring if the driver fails to bind.",
        ],
        notes="Nuvoton NCT677x series — mainline kernel support.",
    ),
    ChipGuidance(
        chip_prefix="nct6686",
        driver_name="nct6683",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6683.html",
        bios_tips=[
            "ASRock boards: disable 'Smart Fan' in BIOS if PWM writes have no effect.",
        ],
        known_issues=[
            "The in-kernel nct6683 driver supports NCT6686D monitoring but does not "
            "offer PWM control here at all: it exposes pwm as read-only (mode 0444) "
            "for every board except Mitac OEM systems, and publishes no pwm_enable "
            "attribute whatsoever. So the headers show up read-only and writes are "
            "refused — this is a driver-capability limit, not a BIOS override, and "
            "no BIOS setting will unlock it. It also only loads on boards whose "
            "customer ID it knows, otherwise it needs force=1.",
            "Both the in-kernel driver and the out-of-tree nct6687d name this "
            "device 'nct6686', so the name does not tell you which one is bound: "
            "`ls -l /sys/class/hwmon/hwmon*/device/driver` does.",
            "Out-of-tree options that do write: asrock-nct6683 "
            "(github.com/branchmispredictor/asrock-nct6683) enables PWM on the "
            "ASRock boards it lists by exact name; nct6687d (Fred78290) drives "
            "several ASRock NCT6686D boards with MSI's register map — its fan "
            "labels are MSI's, so check which header is which before trusting a "
            "'Pump Fan' label. s25g5d4/nct6686d was only ever tested on the "
            "A620I Lightning WiFi. None of the three has an AUR package except "
            "nct6687d-dkms-git.",
        ],
        notes=(
            "Nuvoton NCT6686D — ASRock AM5 Steel Legend / LiveMixer / Lightning "
            "boards, Taichi boards (paired with an NCT6796D-S), and ASRock Intel "
            "Z590/Z790/Z890 boards. The in-kernel nct6683 gives monitoring only."
        ),
    ),
    ChipGuidance(
        chip_prefix="nct6683",
        driver_name="nct6683",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/nct6683.html",
        bios_tips=[
            "MSI boards: if monitoring works but PWM writes do not, try the "
            "out-of-tree nct6687d driver instead.",
        ],
        known_issues=[
            "Covers the NCT6683D/NCT6686D/NCT6687D chip family, and names each "
            "hwmon device after the chip (nct6683 / nct6686 / nct6687) — the same "
            "names the out-of-tree nct6687d uses, so a device called nct6687 may "
            "well be this in-kernel driver.",
            "It never enables PWM writes except on Mitac OEM systems: the pwm "
            "files are read-only and writes are refused even as root. That is by "
            "driver design, not a BIOS setting.",
            "MSI boards: nct6687d-dkms-git is the fix — and blacklist nct6683 "
            "when you install it, or both can bind the same chip and garble the "
            "readings.",
        ],
        notes=(
            "Nuvoton NCT6683 family — in-kernel driver covering NCT6683D/NCT6686D/"
            "NCT6687D. Monitoring only."
        ),
    ),
    ChipGuidance(
        chip_prefix="nct6687",
        driver_name="nct6687",
        in_mainline=False,
        driver_package="nct6687d-dkms-git (AUR)",
        driver_url="https://github.com/Fred78290/nct6687d",
        known_issues=[
            "Out-of-tree driver — must be rebuilt after kernel updates (DKMS handles this).",
            "If the pwm files are read-only (-r--r--r--, 'Permission denied' even "
            "with sudo), the in-kernel nct6683 is bound, not this driver — the "
            "two use the same hwmon name. Blacklist nct6683 and load nct6687: "
            "nct6687d always makes its pwm files writable. The BIOS 'Smart Fan "
            "Mode' setting is not what makes them read-only.",
            "On B840/B850/B860/X870/X870E/Z890 boards (the 'msi_alt1' register "
            "map) system-fan writes may only take effect with the [BETA] "
            "msi_fan_brute_force=1 parameter, which also needs nct6683 "
            "blacklisted; current builds return an I/O error (EIO) when a write "
            "does not stick.",
            "Never load nct6687 with force=1 on a board whose chip is an NCT679x: "
            "since nct6687d PR #174 it attaches to any chip ID in 0xD000-0xDFFF, "
            "which re-opens the collision that has bricked a CPU fan header.",
        ],
        notes=(
            "Nuvoton NCT6687D (reports 0xd592) — MSI B550/A520, B650/X670, the "
            "X570S MPG boards and Intel 600-series onward, plus the B840/B850/"
            "B860/X870/Z890 boards that use the alternate 'msi_alt1' register map. "
            "The original 2019 X570 and AM4 300/400 MSI boards use an NCT6797D/"
            "NCT6795D (in-kernel nct6775) instead. Requires the out-of-tree driver "
            "for fan control."
        ),
    ),
    ChipGuidance(
        chip_prefix="it8688",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        bios_tips=[
            "Gigabyte Smart Fan 5: for a 4-pin fan set 'FAN Control Mode' to PWM.",
            _GB_RECLAIM_NOTE,
            _GB_FULL_SPEED_NOTE,
        ],
        known_issues=[
            "Out-of-tree driver — must be rebuilt after kernel updates (DKMS handles this).",
            "The pwm files are writable; what goes wrong on some boards is that "
            "the chip's SmartFan logic keeps or retakes the fan. IT8688E revision "
            "2 has extra curve vectors that the out-of-tree driver disables since "
            "PR #128 (2026-08-24). Writing pwmN while pwmN_enable is 2 (automatic) "
            "returns 'Device or resource busy' by design.",
        ],
        notes=(
            "ITE IT8688E — Gigabyte X570 / B550 / TRX40 / Z390 / Z490 boards, "
            "usually paired with an IT8792E. Requires out-of-tree "
            "frankcrawford/it87 driver."
        ),
    ),
    ChipGuidance(
        chip_prefix="it8689",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        bios_tips=[
            "Update it87-dkms-git before touching the BIOS: since PR #128 "
            "(2026-08-24) the driver disables the IT8689E's extra curve vectors "
            "itself, and two boards have been reported working with no BIOS "
            "changes at all.",
            "Never give the BIOS curve a 0% point: it runs the fans at boot and "
            "whenever the daemon is not controlling them. The old IT8689E-only "
            "stopgap (fork README, removed once PR #128 made it unnecessary) was "
            "PWM 40,40,40,40,40,40,100 with temperatures 0,90,90,90,90,90,90 — "
            "only relevant to builds older than 2026-08-24.",
            "ACPI conflicts: keep the driver current first — 2026-03+ builds default "
            "MMIO on, which sidesteps the port claim on this chip generation "
            "(frankcrawford/it87 issue #92). If the bind still fails, prefer the "
            "driver-local 'ignore_resource_conflict=1' over the system-wide "
            "'acpi_enforce_resources=lax' kernel parameter.",
        ],
        known_issues=[
            "Mainline it87 gained IT8689E support — six PWM channels — in kernel "
            "7.1 (commit 66b8eaf, merged 2026-03-31; 7.1 released 2026-06-14). The "
            "DKMS build is still recommended: the 6.12 / 6.18 LTS kernels most "
            "people run lack it, mainline has no fix for the extra curve vectors "
            "that make writes ineffective on some Gigabyte boards, and the "
            "out-of-tree driver's IT8689E contributor says mainline's "
            "FEAT_FANCTL_ONOFF flag is wrong for this chip.",
            "Before PR #128, IT8689E boards (Rev 1 especially, e.g. X670E Aorus "
            "Master) accepted PWM writes with zero effect while a normal BIOS "
            "curve was active — the chip's extra vector curves overrode manual "
            "mode (frankcrawford/it87 issue #96).",
            "PR #128 merged 2026-08-24 with manual-mode fixes for IT8688/IT8689/"
            "IT8790/IT8792/IT8795/IT87952 (an earlier candidate, PR #114, was "
            "rejected). Reports so far: a Z790 AORUS MASTER (IT8689E rev 1) "
            "tracking duty across five steps with a clean restore, on the "
            "pre-merge head; since the merge, a B660M GAMING AC DDR4 (rev 1, "
            "working after a reboot) and a B550M DS3H R2 (rev 2). None yet on a "
            "dual-chip IT8689E board or on it87 v2.0, so verify with Test PWM "
            "Control after updating rather than assuming.",
            _IT87_V2_RENAME_NOTE,
            "Where a header still cannot be controlled on a current build, the "
            "known causes are board-specific (on high-end boards with more than "
            "8 headers, two sit on an ITE IT57xx embedded controller that only "
            "current builds reach) — not a whole chipset series.",
        ],
        notes=(
            "ITE IT8689E — Gigabyte Intel 600/700-series (Z690/Z790/B660/B760) and "
            "AM5 600-series (X670/X670E/B650) boards; dual-chip AM5 600 boards pair "
            "it with an IT8792E, Intel ones with an IT87952E."
        ),
    ),
    ChipGuidance(
        chip_prefix="it8696",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        # Curator 2026-09-24 (DEC-421, BRD-01): the "all temperature points
        # identical, 0% PWM except the final point" recipe this entry used to
        # publish has no upstream source, and the BIOS curve is what runs the
        # fans at boot, after the daemon hands a header back, on it87 unload and
        # across suspend — a 0% point there stops fans. Never reintroduce it.
        bios_tips=[
            "Gigabyte Smart Fan 6: for a 4-pin fan set 'FAN Control Mode' to PWM. "
            "Never give the BIOS curve a 0% point: it runs the fans at boot and "
            "whenever the daemon is not controlling them.",
            _GB_RECLAIM_NOTE,
            _GB_FULL_SPEED_NOTE,
        ],
        known_issues=[
            "Out-of-tree driver — must be rebuilt after kernel updates (DKMS handles this).",
            "Gigabyte SmartFan 6 may take a header back even when the driver is "
            "loaded. The daemon's pwm_enable watchdog detects and re-asserts manual "
            "mode; run Test PWM Control to see whether it holds on your board.",
            _IT87_V2_RENAME_NOTE,
        ],
        notes=(
            "ITE IT8696E — Gigabyte AM5 800-series (X870E / X870 / B850) and Z890 "
            "boards, plus the later B650E EAGLE WIFI6E / B650EM models; dual-chip "
            "boards pair it with an IT87952E."
        ),
    ),
    ChipGuidance(
        chip_prefix="it8686",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        bios_tips=[
            _GB_RECLAIM_NOTE,
            _GB_FULL_SPEED_NOTE,
        ],
        notes=(
            "ITE IT8686E — Gigabyte AM4 400-series, X399 and some ASUS AM4 boards; "
            "dual-chip Gigabyte boards pair it with an IT8792E. Requires the "
            "out-of-tree driver."
        ),
    ),
    ChipGuidance(
        chip_prefix="it8625",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        notes=(
            "ITE IT8625E — requires the out-of-tree driver. There has been no "
            "mainline submission since the October 2024 v2 series, which the "
            "hwmon maintainer sent back for changes; not in 7.3 or hwmon-next "
            "(checked 2026-09-24)."
        ),
    ),
    # DEC-144: IT87952E — the secondary Super-I/O on dual-chip Gigabyte
    # boards (X870E/X870/B850, Z690/Z790/Z890, X570S generations — NOT the
    # AM5 600-series X670E/B650E, whose secondary is an IT8792E). Mainline
    # gained the chip ID in kernel 6.3 (torvalds/linux d44cb4cd7456; v6.2
    # lacks it — corrected from "6.4" 2026-09-24), so the in-kernel driver
    # can *enumerate* it — but secondary-chip fan control on these boards
    # comes from the DKMS build's ISA-bridge MMIO/H2RAM access path
    # (frankcrawford/it87 PR #102, issue #64).
    ChipGuidance(
        chip_prefix="it87952",
        driver_name="it87",
        in_mainline=True,
        driver_package="linux (built-in); control needs it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        known_issues=[
            "Mainline kernel ≥ 6.3 enumerates IT87952E (sensors/RPM), but on "
            "dual-chip Gigabyte boards fan CONTROL of this secondary chip needs "
            "the frankcrawford/it87 DKMS build — its ISA-bridge MMIO/H2RAM path "
            "(merged 2026-04, PR #102) plus the smartfan-enable handling "
            "(issue #64, closed 2025-12) made these headers writable.",
            "Older DKMS builds (pre-2026-03) need 'options it87 mmio=on'; "
            "current builds default MMIO on (PR #95).",
            _IT87_V2_RENAME_NOTE,
        ],
        notes=(
            "ITE IT87952E — secondary chip on dual-IO Gigabyte boards. "
            "Enumeration is mainline ≥ 6.3; reliable fan control comes from a "
            "current it87-dkms-git build."
        ),
    ),
    # DEC-144: IT8665E (X399/TR4-era boards, e.g. ASUS ROG Zenith Extreme)
    # is NOT in the mainline it87 enum — it needs the DKMS build. The 2026-03+
    # master mmio=on default had BROKEN IT8665E PWM writes (maintainer-confirmed
    # broken legacy FEAT_MMIO path, frankcrawford/it87 issue #106). Curator
    # 2026-07-29: PR #120 (merged 2026-07-22) removes the MMIO path for IT8665E,
    # so an updated DKMS build fixes it with no param; mmio=off is the fallback
    # for older builds.
    ChipGuidance(
        chip_prefix="it8665",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR)",
        driver_url="https://github.com/frankcrawford/it87",
        known_issues=[
            "The 2026-03+ mmio=on default BROKE IT8665E fan control: PWM writes "
            "were mangled (writing 180 stored ~4), a maintainer-confirmed "
            "regression in the legacy FEAT_MMIO path (frankcrawford/it87 "
            "issue #106).",
            "Fix: update the driver — frankcrawford/it87 PR #120 (merged "
            "2026-07-22) removes the MMIO path for IT8665E, so rebuilding "
            "it87-dkms-git fixes the fan with no kernel parameter. Fallback for "
            "builds older than the merge: create /etc/modprobe.d/it87.conf with "
            "'options it87 mmio=off' and reboot.",
        ],
        notes=(
            "ITE IT8665E — ASUS AM4 300/400-series boards (PRIME X470-PRO, ROG "
            "STRIX X470-F/-I, ROG STRIX B450-F, TUF B450-PLUS, ROG STRIX X370-F — "
            "chip markings and sensors-detect, frankcrawford/it87 #27) and "
            "X399/TR4-era boards (e.g. ASUS ROG Zenith Extreme). Requires the "
            "out-of-tree driver; update it to a build ≥ 2026-07-22 (PR #120) — "
            "older builds need mmio=off (issue #106)."
        ),
    ),
    # DEC-144: IT8622E is in the mainline it87 enum (verified against
    # torvalds/linux drivers/hwmon/it87.c v7.1 / 7.2-rc1 `enum chips`) — no DKMS
    # build required. Listed so boards with this chip resolve to honest
    # "built-in" guidance instead of the generic it87 fallthrough.
    ChipGuidance(
        chip_prefix="it8622",
        driver_name="it87",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/it87.html",
        notes="ITE IT8622E — supported in the mainline kernel it87 driver.",
    ),
    # DEC-106 (D4.A) → corrected 2026-07 → corrected again by DEC-326 → and
    # corrected a THIRD time by DEC-332, which is the one that finally has a
    # controlled experiment behind it rather than a reading of upstream issues.
    #
    # DEC-326's measurements all stand: `mmio` already defaults to true
    # (it87.c:314); #81's owner applied force_id + mmio=on and still lost three
    # fans and a pump; `0x8883` appears nowhere in the driver while
    # IT87952E_DEVID 0x8695 IS defined and handled, so the secondary is
    # unreachable rather than unsupported.
    #
    # What DEC-326 got wrong was the CONCLUSION it drew from them — "there is no
    # local fix". The bridge answering is a STATE, not a property of the board,
    # and 2026-09-05 measured both the cause and the cure on an X870E AORUS
    # MASTER: loading `nct6775` (which writes a config-mode unlock to 0x4E
    # before reading the DEVID) turned a working it87952 into
    # `Unsupported chip (DEVID=0x8883)` inside one boot, against an it87-reload
    # control that changed nothing; suppressing nct6775/w83627ehf and cutting
    # mains power brought back 3 fans, 3 PWMs and 3 thermistor temps.
    #
    # This entry survives so a lookup on the bogus "it8883" chip name explains
    # what the reader is actually looking at — and now tells them how to fix it.
    ChipGuidance(
        chip_prefix="it8883",
        driver_name="it87",
        in_mainline=False,
        driver_package="it87-dkms-git (AUR) — installed, but see below",
        # NOT issue #64: it is where the bridge reading comes from but was
        # closed in 2025-12, and pointing a user at a closed issue as their
        # entry point is the `HOST-c` defect (citing an upstream issue as
        # something it is not). The repository is the honest destination.
        driver_url="https://github.com/frankcrawford/it87",
        known_issues=[
            "There is no 'IT8883' sensor chip. Device-ID 0x8883 at the "
            "secondary Super-I/O address is an ITE IT8883 eSPI-to-LPC bridge "
            "answering in place of the chip behind it (ITE's own product page "
            "lists it as an eSPI-to-LPC bridge on standby power; the it87 owner "
            "calls it 'a bus chip connecting the it87952'). The mechanism was "
            "confirmed locally on 2026-09-05 by reproducing the latch on demand "
            "and then clearing it.",
            "Measured on Gigabyte X870E AORUS MASTER: the it87 driver finds "
            "the primary IT8696E over MMIO and the secondary simply never "
            "appears — one hwmon device instead of two, costing 3 of 8 fan "
            "headers and 3 of 9 temperatures. The 'Unsupported chip "
            "(DEVID=0x8883)' line is debug-level: you only see it with it87 "
            "dynamic debug on, and a read of 0xFFFF prints nothing at all. "
            "Upstream sees 0xFFFF and 0x8883 as two views of the same blocked "
            "bridge, so do not try to tell them apart.",
            "This IS fixable, but not by any driver setting. Something wrote a "
            "Super-I/O unlock to the bridge: the nct6775 and w83627ehf modules "
            "do (before they even read the device ID, so they do the damage "
            "while failing to load on an ITE board), and so does sensors-detect. "
            "Stop the trigger (the control-ofc-daemon package ships a guard that "
            "suppresses the two modules on known boards) and reboot; if the chip "
            "is still missing, power down FULLY at the wall — on some boards the "
            "latch survives a reboot and a normal shut-down, because the bridge "
            "stays powered on standby.",
            "Do NOT use mmio=on (already the driver default) or force_id "
            "(issue #81's reporter tried it and still lost three fans and a "
            "water pump), and do not run sensors-detect — it writes the same "
            "unlock and is one of the things that causes this.",
        ],
        notes=(
            "ITE 0x8883 — not a real sensor chip; an eSPI→LPC bridge latched "
            "in config mode and masking the secondary Super-I/O. Recoverable: "
            "stop what writes the unlock (nct6775/w83627ehf/sensors-detect), "
            "reboot, then cut mains power if it persists. DEC-332 (measured "
            "2026-09-05) and DEC-421 (one recovery ladder, 2026-09-24)."
        ),
    ),
    # ── Out-of-tree-only ITE parts (verified 2026-08-26) ────────────────
    # Each of these appears in frankcrawford/it87's device list but is ABSENT
    # from the mainline `enum chips`, byte-verified at both the v7.2 tag and
    # master. Without an explicit entry, `it8785`/`it8736`/`it8738` matched the
    # generic "it87" prefix below and were reported as mainline built-in — a
    # false claim that tells the user no DKMS driver is needed when one is
    # mandatory. The rest resolved to None ("Unknown chip"), which was honest
    # but gave no guidance at all.
    #
    # Confidence limit, deliberately encoded in the notes: no primary source
    # establishes which retail boards carry these parts, so every entry here is
    # driver-level evidence only — never claim board-level support from them.
    *[
        ChipGuidance(
            chip_prefix=prefix,
            driver_name="it87",
            in_mainline=False,
            driver_package="it87-dkms-git (AUR)",
            driver_url="https://github.com/frankcrawford/it87",
            known_issues=[
                f"{label} is not in the mainline it87 driver's chip list "
                "(checked against kernel 7.3-rc4 on 2026-09-24), so the "
                "in-kernel driver will not bind to it. Fan control requires the "
                "out-of-tree it87-dkms-git build.",
                *extra,
            ],
            notes=(
                f"ITE {label} — out-of-tree it87 support only. Which retail boards "
                "carry this chip is not documented upstream, so treat driver "
                "support as established and board behaviour as unverified until "
                "you have tested your own header."
            ),
        )
        for prefix, label, extra in (
            (
                "it8698",
                "IT8698E",
                (
                    "Covered by the driver's MMIO/H2RAM path, which current "
                    "builds enable by default (mmio=on is already in effect — "
                    "setting it changes nothing).",
                ),
            ),
            (
                "it8613",
                "IT8613E",
                (
                    "Mainline support is queued: the IT8613E series was applied "
                    "to hwmon-next on 2026-08-30 and should reach kernel 7.4 — "
                    "it is not in 7.3. sensors-detect recognises device ID "
                    "0x8613 but records its driver as 'to-be-written', so a "
                    "sensors-detect run will not give you a usable driver name.",
                ),
            ),
            ("it8785", "IT8785E", ()),
            ("it8736", "IT8736F", ()),
            ("it8738", "IT8738E", ()),
            ("it8655", "IT8655E", ()),
            ("it8606", "IT8606E", ()),
            ("it8607", "IT8607E", ()),
        )
    ],
    # ── Mainline ITE parts outside the "it87" prefix (curator 2026-09-24) ──
    # `it8603` / `it8620` / `it8628` do not start with "it87", so without these
    # entries they fell through to None and rendered "Unknown chip" although the
    # in-kernel driver supports all three (Documentation/hwmon/it87.rst,
    # v7.3-rc4: "IT8603E/IT8623E — Prefix: 'it8603'", "IT8620E", "IT8628E").
    *[
        ChipGuidance(
            chip_prefix=prefix,
            driver_name="it87",
            in_mainline=True,
            driver_package="linux (built-in)",
            driver_url="https://www.kernel.org/doc/html/latest/hwmon/it87.html",
            known_issues=[
                "If the kernel log says 'Detected broken BIOS defaults, "
                "disabling PWM interface', the driver has hidden PWM control on "
                "purpose because the BIOS left the PWM polarity inverted. The "
                "fix_pwm_polarity parameter is marked DANGEROUS upstream and "
                "inverted the fan on at least one board (frankcrawford/it87 "
                "#111) — do not use it to work around this.",
            ],
            notes=f"ITE {label} — supported in the mainline kernel it87 driver.",
        )
        for prefix, label in (
            ("it8603", "IT8603E / IT8623E (reported as it8603)"),
            ("it8620", "IT8620E"),
            ("it8628", "IT8628E"),
        )
    ],
    ChipGuidance(
        chip_prefix="it87",
        driver_name="it87",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/it87.html",
        known_issues=[
            "This is the fallback entry for older ITE parts that mainline has "
            "long supported (IT8705/8712/8716/8718/8720/8721/8728/8732/8771/"
            "8772/8781/8782/8783/8786/8790/8792). If your chip is NOT one of "
            "those, do not read 'mainline' as settled: ITE ships new Super-I/O "
            "parts faster than the in-kernel driver adopts them, and several "
            "current ones need the out-of-tree it87-dkms-git build. Check your "
            "exact model against the kernel's it87 documentation before "
            "concluding no driver install is needed.",
        ],
        notes=(
            "ITE IT87xx (older models) — supported in mainline kernel. Newer "
            "IT86xx/IT87xx parts often are not; see the per-chip entries above."
        ),
    ),
    ChipGuidance(
        chip_prefix="f71882",
        driver_name="f71882fg",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/f71882fg.html",
        notes="Fintek F71882FG — mainline kernel support.",
    ),
    ChipGuidance(
        chip_prefix="f718",
        driver_name="f71882fg",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/f71882fg.html",
        notes="Fintek F718xx series — mainline kernel support.",
    ),
    ChipGuidance(
        chip_prefix="sch5627",
        driver_name="sch5627",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/sch5627.html",
        notes="SMSC SCH5627 — mainline kernel support.",
    ),
    ChipGuidance(
        chip_prefix="sch5636",
        driver_name="sch5636",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://www.kernel.org/doc/html/latest/hwmon/sch5636.html",
        notes="SMSC SCH5636 — mainline kernel support.",
    ),
    # Curator 2026-09-24 (DEC-421): the kernel registers these hwmon devices as
    # `asusec` and `atk0110` — NOT the module names this table used to key on —
    # so each entry is listed under both spellings: the real hwmon name that a
    # daemon actually reports, and the module-style key older callers pass.
    *[
        ChipGuidance(
            chip_prefix=prefix,
            driver_name="asus_ec_sensors",
            in_mainline=True,
            driver_package="linux (built-in)",
            driver_url="https://docs.kernel.org/hwmon/asus_ec_sensors.html",
            known_issues=[
                "Provides extra board sensors (temperatures, RPMs) via ASUS EC "
                "registers. This is a sensor-enrichment driver, NOT a PWM write path.",
                "Do not use this driver for fan control — look for the board's "
                "Super I/O driver (usually nct6775) as the actual PWM write endpoint.",
                "The board list grows with every kernel (55 boards in 7.2, 60 in "
                "the 7.3 release candidates) and a newer entry is not in older "
                "kernels — the LTS kernels 6.18 and 6.12 carry fewer. Check "
                "docs.kernel.org for the kernel you run. From 7.3 an unconnected "
                "T_Sensor or water-probe socket reads as unavailable instead of a "
                "-62/-60/-40 °C placeholder.",
            ],
            notes=(
                "ASUS EC Sensors (hwmon name 'asusec') — additional motherboard "
                "sensors on listed ASUS boards (ROG, PRIME, ProArt, TUF). Read-only."
            ),
        )
        for prefix in ("asusec", "asus_ec_sensors")
    ],
    ChipGuidance(
        chip_prefix="asus_wmi_sensors",
        driver_name="asus_wmi_sensors",
        in_mainline=True,
        driver_package="linux (built-in)",
        driver_url="https://docs.kernel.org/hwmon/asus_wmi_sensors.html",
        bios_tips=[
            "The kernel warns that some ASUS BIOS WMI implementations are buggy — "
            "fans stopping, fans stuck at maximum, or readings freezing — and that "
            "the risk grows with polling. It advises a soak test before leaving "
            "the machine unattended. A BIOS whose WMI method version is 2 or later "
            "'should rectify the issue' (the driver refuses older versions since "
            "kernel 5.17).",
        ],
        known_issues=[
            "Sensor-enrichment driver only — does NOT provide PWM write capability. "
            "On the AM4 boards it supports, the fan-control chip is usually an ITE "
            "IT8665E, driven by the out-of-tree it87 — not a Nuvoton chip.",
            "PRIME X470-PRO is singled out in the kernel documentation as 'particularly bad'.",
            "Supported boards (kernel DMI table): PRIME X399-A, PRIME X470-PRO, "
            "ROG CROSSHAIR VI EXTREME, CROSSHAIR VI HERO (and WI-FI AC), ROG "
            "CROSSHAIR VII HERO (and WI-FI), ROG STRIX B450-E / B450-F / B450-F "
            "II / B450-I GAMING, ROG STRIX X399-E GAMING, ROG STRIX X470-F / "
            "X470-I GAMING, ROG ZENITH EXTREME (and ALPHA).",
            "The driver reads each WMI sensor group at most about once a second, "
            "however many programs read its files.",
        ],
        notes=(
            "ASUS WMI Sensors — exposes extra sensors via BIOS WMI interface on "
            "AMD X370/X470/B450/X399 boards. Read-only."
        ),
    ),
    *[
        ChipGuidance(
            chip_prefix=prefix,
            driver_name="asus_atk0110",
            in_mainline=True,
            driver_package="linux (built-in)",
            # No kernel.org hwmon doc page exists for asus_atk0110 (verified
            # absent from https://docs.kernel.org/hwmon/index.html, again at
            # 7.3-rc4). Link to the mainline driver source instead.
            driver_url=(
                "https://github.com/torvalds/linux/blob/master/drivers/hwmon/asus_atk0110.c"
            ),
            known_issues=[
                "Sensor-enrichment driver only — does NOT provide PWM write capability.",
                "Loaded automatically on ASUS boards whose firmware provides the "
                "legacy ACPI ATK0110 device.",
                "Look for nct6775, it87, or another Super I/O driver as the actual PWM write path.",
            ],
            notes=(
                "ASUS ATK0110 ACPI hwmon (hwmon name 'atk0110') — exposes board "
                "sensors via the ACPI ATK0110 method on older ASUS boards. Read-only."
            ),
        )
        for prefix in ("atk0110", "asus_atk0110")
    ],
]


def lookup_chip_guidance(chip_name: str) -> ChipGuidance | None:
    """Find the best-matching guidance entry for a chip name.

    Matches are checked from most-specific prefix to least-specific,
    so "it8688" matches the IT8688E entry before the generic "it87" entry.
    """
    lower = chip_name.lower()
    best: ChipGuidance | None = None
    best_len = 0
    for entry in CHIP_GUIDANCE_DB:
        if lower.startswith(entry.chip_prefix) and len(entry.chip_prefix) > best_len:
            best = entry
            best_len = len(entry.chip_prefix)
    return best


def format_driver_status(chip_name: str, loaded: bool) -> str:
    """Human-readable one-liner for driver load state."""
    guidance = lookup_chip_guidance(chip_name)
    if guidance is None:
        return f"Unknown chip '{chip_name}' — driver status unavailable"

    if loaded and guidance.in_mainline:
        return f"{guidance.driver_name} loaded (mainline kernel)"
    if loaded and not guidance.in_mainline:
        return f"{guidance.driver_name} loaded (out-of-tree: {guidance.driver_package})"
    if not loaded and guidance.in_mainline:
        return f"{guidance.driver_name} not loaded — try: sudo modprobe {guidance.driver_name}"
    return f"{guidance.driver_name} not loaded — install {guidance.driver_package}"


# ---------------------------------------------------------------------------
# Vendor + chip quirk database
# ---------------------------------------------------------------------------

VENDOR_QUIRKS_DB: list[VendorQuirk] = [
    VendorQuirk(
        id="gb-it8689-bios-actively-overrides",
        vendor_pattern="gigabyte",
        chip_prefix="it8689",
        severity="critical",
        consequence="control_loss",
        trigger="bios_revert",
        summary="Gigabyte SmartFan 6 + IT8689E — BIOS actively overrides fan control",
        details=[
            "On driver builds older than 2026-08-24, IT8689E boards (Rev 1 "
            "especially, e.g. X670E Aorus Master) accepted PWM writes with zero "
            "hardware effect while a normal BIOS fan curve was active: the chip's "
            "extra vector curves overrode manual mode (frankcrawford/it87 #96).",
            "Driver status: the fix (frankcrawford/it87 PR #128) merged "
            "2026-08-24. A Z790 AORUS MASTER with IT8689E rev 1 tracked duty "
            "across five steps on the pre-merge patch; since the merge a B660M "
            "GAMING AC DDR4 (rev 1, after a reboot) and a B550M DS3H R2 (rev 2) "
            "report working, and a B650 Eagle AX needed no BIOS change at all. "
            "Update it87-dkms-git first, reboot, then verify with Test PWM "
            "Control.",
            _IT87_V2_RENAME_NOTE,
            "Only on a build older than PR #128: the fork's old stopgap was a "
            "BIOS curve of PWM 40,40,40,40,40,40,100 with temperatures "
            "0,90,90,90,90,90,90 (lower 90 to your BIOS maximum), and it only "
            "reliably restored the CPU-fan header. Never use a curve with a 0% "
            "point: the BIOS curve runs the fans at boot and whenever the daemon "
            "is not controlling them.",
        ],
    ),
    VendorQuirk(
        id="gb-it8696-bios-override",
        vendor_pattern="gigabyte",
        chip_prefix="it8696",
        severity="high",
        consequence="control_loss",
        trigger="bios_revert",
        summary="Gigabyte SmartFan 6 + IT8696E — BIOS may override fan control",
        details=[
            "The EC firmware continuously evaluates its own fan curves and can "
            "take a header back from Linux within seconds.",
            "The daemon's pwm_enable watchdog detects and re-writes manual mode "
            "when the BIOS reclaims it — run Test PWM Control to see whether "
            "control holds on your board.",
            _GB_RECLAIM_NOTE,
            _GB_FULL_SPEED_NOTE,
        ],
    ),
    VendorQuirk(
        id="gb-it8688-bios-override",
        vendor_pattern="gigabyte",
        chip_prefix="it8688",
        severity="high",
        consequence="control_loss",
        trigger="bios_revert",
        summary="Gigabyte SmartFan 5 + IT8688E — BIOS may override fan control",
        details=[
            "SmartFan 5 on Gigabyte X570/B550 boards can keep or retake a header "
            "from Linux. The pwm files stay writable — a write while the header "
            "is in automatic mode returns 'Device or resource busy' by design — "
            "and IT8688E revision 2's extra curve vectors are handled by the "
            "out-of-tree driver since PR #128 (2026-08-24): keep it current.",
            "For a 4-pin fan set 'FAN Control Mode' to PWM in BIOS → Smart Fan 5.",
            _GB_RECLAIM_NOTE,
            _GB_FULL_SPEED_NOTE,
            "Note: the secondary ITE controller (IT8792E/IT87952E) was historically "
            "read-only on some of these boards, but that is not a fixed property — "
            "the ISA-bridge MMIO path (PR #95/#102) and the manual-mode fixes in "
            "PR #128 cover IT8792/IT8795/IT87952. Keep it87-dkms-git current and "
            "verify per-header writability rather than assuming the secondary chip "
            "cannot be controlled.",
        ],
    ),
    VendorQuirk(
        id="gb-it8686-bios-override",
        vendor_pattern="gigabyte",
        chip_prefix="it8686",
        severity="high",
        consequence="control_loss",
        trigger="bios_revert",
        summary="Gigabyte SmartFan 5 + IT8686E — BIOS may override fan control",
        details=[
            "Same behaviour as IT8688E: SmartFan 5 can keep or retake a header "
            "from Linux; the daemon's watchdog re-asserts manual mode.",
            _GB_RECLAIM_NOTE,
            _GB_FULL_SPEED_NOTE,
        ],
    ),
    VendorQuirk(
        id="msi-nct6687-headers-read-only",
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="medium",
        # Curator 2026-09-24 (DEC-421, BRD-06): the id is kept (it is persisted by
        # acknowledgements), the cause is corrected. nct6687d makes every pwm
        # file 0644 unconditionally; a read-only header on an MSI NCT6687 board
        # is the in-kernel nct6683 bound in its place — both name the device
        # "nct6687". "Smart Fan Mode" was never the cause.
        summary="MSI + NCT6687 — read-only headers mean the in-kernel nct6683 is bound",
        details=[
            "If the pwm files are read-only (ls -l shows -r--r--r--, and writes "
            "fail with 'Permission denied' even with sudo), the in-kernel nct6683 "
            "driver is bound, not nct6687d. It names its device 'nct6687' too, and "
            "it never enables PWM writes on MSI boards.",
            "Check which driver is bound: ls -l /sys/class/hwmon/hwmon*/device/"
            "driver. The out-of-tree nct6687d always makes its pwm files writable.",
            "Fix: install nct6687d-dkms-git and blacklist the in-kernel driver: "
            "echo 'blacklist nct6683' | sudo tee /etc/modprobe.d/nct6683_blacklist.conf "
            "— then reboot. With both loaded they can bind the same chip and "
            "garble the readings.",
            "The BIOS 'Smart Fan Mode' setting does not make the files read-only. "
            "If writes are accepted but the fan ignores them, see the msi_alt1 / "
            "msi_fan_brute_force notes for this board instead.",
        ],
    ),
    VendorQuirk(
        id="asus-nct679-acpi-port-conflict",
        vendor_pattern="asustek",
        chip_prefix="nct679",
        severity="medium",
        summary="ASUS + NCT679x — ACPI I/O port conflict may prevent driver loading",
        details=[
            "ASUS boards frequently claim Super I/O I/O port ranges (0x0290-0x0299) "
            "via ACPI OperationRegions, preventing the nct6775 driver from binding.",
            "On many supported ASUS boards no workaround is needed: nct6775 reaches "
            "the chip through an ASUS ACPI WMI method (the WMBD path, matched on the "
            "board's ASUS WMI device ID) instead of the contested I/O ports, which "
            "sidesteps the conflict entirely. The kernel keeps that list by exact "
            "board name, and as of 7.3 it has no B850, B840, B860 or Z890 board — "
            "those go through the ports directly, so the conflict is more likely "
            "there.",
            "If the bind still fails, add 'acpi_enforce_resources=lax' to the kernel "
            "boot parameters. Unlike it87, nct6775 has NO driver-local escape — its "
            "only module parameters are force_id and fan_debounce — so the "
            "system-wide kernel parameter really is the only option here. Treat it "
            "as the larger change it is.",
        ],
    ),
    VendorQuirk(
        id="asus-asuswmi-high-frequency-polling",
        vendor_pattern="asustek",
        chip_prefix="asus_wmi",
        severity="high",
        # Sensor enrichment only — this driver is never the PWM write path, and
        # the advisory's own text says 1 Hz is within the kernel-documented safe
        # band. Nothing here can be observed or resolved, so it never promotes.
        consequence="none",
        summary="ASUS WMI sensors — high-frequency polling may cause fan/sensor failure",
        details=[
            "Some ASUS BIOS WMI implementations are buggy: the kernel documents "
            "fans stopping, fans stuck at maximum, or sensor readings getting "
            "stuck, more likely the more often the interface is polled.",
            "PRIME X470-PRO is specifically called out in upstream kernel docs "
            "as particularly affected.",
            "The kernel names no safe polling rate. The driver itself calls the "
            "BIOS at most about once a second per sensor group, however many "
            "programs read it, so extra readers do not add WMI calls. The kernel's "
            "advice is a soak test before leaving the machine unattended, and a "
            "BIOS with WMI method version 2 or later.",
            "These drivers provide sensor enrichment only — they are NOT the "
            "PWM write path. Look for the board's Super I/O driver for actual fan "
            "control (on these AM4 boards usually an ITE IT8665E, out-of-tree it87).",
        ],
    ),
    VendorQuirk(
        id="msi-nct6687-system-respond-single",
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="high",
        # Observable only by writing: a single PWM write is accepted and the fan
        # does not move. No `/diagnostics/hardware` field reports that, so this
        # promotes off a failed PWM verify rather than off a condition key.
        consequence="control_loss",
        summary="MSI X870/B850 — system fans may not respond to single PWM writes",
        details=[
            "On MSI boards that use the 'msi_alt1' register map (B840/B850/B860/"
            "X870/X870E/Z890), system-fan writes often only take effect when the "
            "driver writes all 7 BIOS fan-curve points. Current nct6687d builds "
            "return an I/O error (EIO) when a write does not stick — on some "
            "boards (e.g. PRO B850M-P WIFI) even for CPU_FAN — until brute force "
            "is enabled.",
            "Historically CPU_FAN and PUMP_FAN worked before the system fans did; "
            "that is not guaranteed on current builds.",
            "Workaround: Load the nct6687d driver with 'msi_fan_brute_force=1' "
            "(upstream marks this parameter BETA): "
            "sudo modprobe nct6687 msi_fan_brute_force=1",
            "REQUIRED alongside it: blacklist the in-kernel nct6683 driver. "
            "Upstream states this as a prerequisite — with nct6683 also loaded, "
            "both drivers can bind the same chip, readings garble and PWM writes "
            "fail (commonly EIO; nct6687d #202, #204). "
            "echo 'blacklist nct6683' | sudo tee /etc/modprobe.d/nct6683_blacklist.conf",
            "Persist across reboots with all three: "
            "'options nct6687 msi_fan_brute_force=1' in /etc/modprobe.d/nct6687_msi.conf, "
            "the nct6683 blacklist above, and 'nct6687' in "
            "/etc/modules-load.d/nct6687.conf. Then reboot.",
            "The driver snapshots all 7 original curve points before entering manual "
            "control and restores them when automatic mode is written, when the module "
            "is unloaded, or when its fan-control watchdog expires.",
            "3-pin fans: set the header's fan type to DC in BIOS; no report since "
            "brute force arrived isolates a DC-specific failure.",
        ],
    ),
    VendorQuirk(
        id="asrock-nct6686-monitoring-works-but",
        vendor_pattern="asrock",
        chip_prefix="nct6686",
        severity="medium",
        summary="ASRock + NCT6686D — monitoring works but PWM writes may not",
        details=[
            "ASRock boards with an NCT6686D — e.g. the AM5 Steel Legend, LiveMixer "
            "and Lightning boards, the AM5 and Z790/Z890 Taichi boards, Z590 "
            "Taichi and most Z890 boards — show sensors and RPMs, but under the "
            "in-kernel nct6683 the headers are read-only: manual PWM writes are "
            "refused, not accepted-and-ignored.",
            "That is by driver design, not a board fault: nct6683 makes pwm "
            "writable only for Mitac OEM customer IDs and provides no pwm_enable "
            "attribute, so there is no in-kernel path to fan control here. Changing "
            "BIOS settings will not help; changing driver will.",
            "Out-of-tree options (board-specific; none but nct6687d has an AUR "
            "package):\n"
            "  1. asrock-nct6683 (github.com/branchmispredictor/asrock-nct6683) — "
            "enables PWM on the boards it lists by exact name (B550 Taichi and "
            "Razer Edition, A620I / B650I Lightning WiFi, X570 Creator, X670E "
            "Steel Legend, Z370M Pro4, Z890 Nova WiFi)\n"
            "  2. nct6687d (github.com/Fred78290/nct6687d) — drives several "
            "ASRock NCT6686D boards with MSI's register map; its fan labels are "
            "MSI's, so confirm which header is which before trusting 'Pump Fan'\n"
            "  3. nct6686d (github.com/s25g5d4/nct6686d) — only tested on the "
            "A620I Lightning WiFi",
            "Where the board has a second Nuvoton chip, the headers are split: "
            "on Z890 Taichi and Z890 Nova WiFi the NCT6686D carries every fan "
            "header but the MOS fan, and the second chip nct6775 binds has little "
            "or nothing wired; on Z790 Taichi five headers are on the NCT6686D and "
            "three on an NCT5585D that nct6775 drives; on an X870E Taichi an owner "
            "measured CHA_FAN1/2, CPU_FAN2 and AIO_PUMP on the nct6799 chip and "
            "CHA_FAN3/4 on the NCT6686D (nct6687d #155).",
            "Test write capability on a non-critical chassis fan header first.",
        ],
    ),
    VendorQuirk(
        id="asrock-nct6683-sensors-visible-but",
        vendor_pattern="asrock",
        chip_prefix="nct6683",
        severity="medium",
        summary="ASRock + NCT6683 — sensors visible, PWM read-only in the kernel driver",
        details=[
            "The in-kernel nct6683 driver gives temperatures and RPMs on ASRock "
            "boards but publishes the pwm files read-only (writes are refused, "
            "even as root) — it enables writes only on Mitac OEM systems. B550 "
            "Taichi and B550 Taichi Razer Edition carry their fans on this chip.",
            "The asrock-nct6683 out-of-tree driver makes PWM writable on the ASRock "
            "boards it lists by exact name; see the NCT6686D note for the list.",
            "Consider board-specific out-of-tree drivers — ASRock boards are a "
            "strong candidate for a per-model driver selection rather than a "
            "single driver rule.",
        ],
    ),
    VendorQuirk(
        id="gb-it87-prefer-driver-local",
        vendor_pattern="gigabyte",
        chip_prefix="it87",
        severity="info",
        summary="Gigabyte ITE — prefer driver-local conflict resolution over system-wide",
        details=[
            "Keep it87-dkms-git current before reaching for parameters: "
            "2026-03+ builds default MMIO on, which sidesteps the ACPI I/O "
            "port claim on newer ITE chips (frankcrawford/it87 issue #81 "
            "discussion), and master carries a built-in DMI ACPI-exemption "
            "table (it87_acpi_ignore) for known-safe boards.",
            _IT87_V2_RENAME_NOTE,
            "If ACPI I/O port conflicts still block the bind, prefer the "
            "driver-local 'ignore_resource_conflict=1' parameter for the "
            "it87 module over the system-wide 'acpi_enforce_resources=lax' "
            "kernel parameter.",
            "Add 'options it87 ignore_resource_conflict=1' to "
            "/etc/modprobe.d/it87.conf to persist across reboots.",
            "Note: this is still inherently risky as ACPI and the driver may "
            "access the chip concurrently — use only when needed.",
            "Do NOT use 'force_id' as a workaround — it is intended for "
            "testing only and should not be used in production.",
        ],
    ),
    # ── AM4 400-series additions (DEC-105) ──────────────────────────
    VendorQuirk(
        id="msi-nct6797-out-tree-nct6687",
        vendor_pattern="micro-star",
        chip_prefix="nct6797",
        severity="critical",
        consequence="hardware_damage",
        trigger="module_collision",
        summary=(
            "MSI AM4 + NCT6797D — out-of-tree nct6687 can mis-claim this chip "
            "and corrupt fan registers"
        ),
        details=[
            "Older out-of-tree nct6687 builds declare chip ID 0xd450 — the "
            "same ID assigned to the legitimate NCT6797D found on MSI AM4 "
            "boards (B450M MORTAR, MAG B450 TOMAHAWK MAX, MAG X570 TOMAHAWK "
            "WIFI, X570-A PRO, the original MPG X570 boards, and similar). When "
            "both nct6687 and nct6775 are loaded, whichever driver binds first "
            "claims the chip and the other may write into the wrong registers. "
            "The 0xd450 claim was removed upstream in Fred78290/nct6687d PR #164 "
            "(2026-05-19), so a current build no longer claims it by default — "
            "but already-loaded modules, not-yet-updated packages, and any "
            "nct6687 loaded with force=1 remain at risk: since PR #174 force=1 "
            "attaches to any chip ID in 0xD000-0xDFFF, this one included.",
            "Public incident: a user lost their CPU fan header on an MSI MAG "
            "X570 TOMAHAWK WIFI because nct6687 wrote into NCT6797D's "
            "non-volatile state. Same chip family is used on AM4 400-series "
            "MSI boards.",
            "If diagnostics detected the (nct6687, nct6775) collision, DO NOT "
            "write PWM until you have resolved the load ordering.",
            "Workaround: identify which chip the board actually has "
            "(cat /sys/class/hwmon/hwmon*/name on a known-good kernel) and "
            "blacklist the wrong driver. For NCT6797D, blacklist nct6687: "
            "echo 'blacklist nct6687' | sudo tee /etc/modprobe.d/blacklist-nct6687.conf "
            "— and never load nct6687 with force=1 on this board.",
            "The Bazzite report (ublue-os/bazzite #4498) documents a bricked "
            "CPU_FAN header from this exact collision. It was closed in "
            "September 2026 without a distro fix; current Bazzite images instead "
            "ship nct6687d autoloaded with nct6683 blacklisted, which is safe on "
            "an NCT6797D board only because a current nct6687d no longer claims "
            "0xd450 — so do not add force=1 there either.",
        ],
    ),
    VendorQuirk(
        id="msi-nct6798-out-tree-nct6687",
        vendor_pattern="micro-star",
        chip_prefix="nct6798",
        severity="critical",
        consequence="hardware_damage",
        trigger="module_collision",
        summary=(
            "MSI + NCT6798D — out-of-tree nct6687 can mis-claim this chip and corrupt fan registers"
        ),
        details=[
            "Same trap as NCT6797D: an out-of-tree nct6687 that claims this "
            "chip — any build loaded with force=1, which since nct6687d PR #174 "
            "attaches to every chip ID in 0xD000-0xDFFF — leaves the wrong driver "
            "bound, and its writes can scribble into non-volatile fan registers.",
            "If diagnostics detected the (nct6687, nct6775) collision, DO NOT "
            "write PWM until you have resolved the load ordering.",
            "Workaround: blacklist nct6687 unless you are intentionally "
            "running the out-of-tree driver on a board that needs it.",
        ],
    ),
    VendorQuirk(
        id="msi-nct6795-mainline-kernel-coverage",
        vendor_pattern="micro-star",
        chip_prefix="nct6795",
        severity="info",
        summary="MSI AM4 + NCT6795D — mainline kernel coverage is solid",
        details=[
            "NCT6795D is the chip on common AM4 400-series MSI boards such "
            "as the X470 GAMING PRO and X470 GAMING PRO CARBON. The in-kernel "
            "nct6775 driver supports monitoring and PWM writes out of the box.",
            "Do NOT install the out-of-tree nct6687 driver on these boards — "
            "nothing here needs it, and loaded with force=1 it attaches to any "
            "Nuvoton chip ID from 0xD000 to 0xDFFF, this one included, and races "
            "nct6775 for it.",
        ],
    ),
    VendorQuirk(
        id="asus-asuswmisensors-kernel-documented-buggy",
        vendor_pattern="asustek",
        chip_prefix="asus_wmi_sensors",
        severity="high",
        # As with the asus_wmi entry above: enrichment only, never the write path.
        consequence="none",
        summary=("ASUS AM4 + asus_wmi_sensors — kernel-documented buggy WMI on specific boards"),
        details=[
            "The kernel supports this driver on 16 boards by exact name: PRIME "
            "X399-A, PRIME X470-PRO, ROG CROSSHAIR VI EXTREME, ROG CROSSHAIR VI "
            "HERO (and WI-FI AC), ROG CROSSHAIR VII HERO (and WI-FI), ROG STRIX "
            "B450-E GAMING, ROG STRIX B450-F GAMING (and II), ROG STRIX B450-I "
            "GAMING, ROG STRIX X399-E GAMING, ROG STRIX X470-F GAMING, ROG STRIX "
            "X470-I GAMING and ROG ZENITH EXTREME (and ALPHA). Its docs warn that "
            "some ASUS BIOS WMI "
            "implementations are buggy — fans stopping, fans stuck at maximum, "
            "or readings freezing, more likely the more often it is polled — and "
            "name the PRIME X470-PRO as particularly bad.",
            "The kernel names no safe polling rate. Its advice is a soak test "
            "while polling before you leave the machine unattended, and a BIOS "
            "whose WMI method version is 2 or later. The driver calls the BIOS "
            "at most about once a second per sensor group however many programs "
            "read it.",
            "asus_wmi_sensors is sensor enrichment ONLY — it never provides "
            "the PWM write path. On the boards with evidence (PRIME X470-PRO, "
            "ROG STRIX B450-F, X470-F and X470-I) the fan chip is an ITE IT8665E, "
            "which has no mainline driver: fan control needs the out-of-tree it87 "
            "(frankcrawford/it87 #27).",
        ],
    ),
    # Curator 2026-09-24 (DEC-421): these two quirks never fired before — they
    # were keyed on the MODULE names, and the kernel names the hwmon devices
    # "asusec" and "atk0110". Nothing could have acknowledged them, so the ids
    # are kept for continuity rather than for any persisted state.
    VendorQuirk(
        id="asus-asusecsensors-prime-x470-pro",
        vendor_pattern="asustek",
        chip_prefix="asusec",
        severity="info",
        platform="amd",
        board_pattern="X470",
        summary="ASUS X470 + asus_ec_sensors — sensor enrichment, not fan control",
        details=[
            "Three AM4 400-series boards are on the kernel's asus_ec_sensors "
            "list: PRIME X470-PRO, ROG STRIX X470-I GAMING (kernel 6.19+) and "
            "ROG STRIX X470-F GAMING (kernel 7.1+).",
            "Sensor enrichment only — NOT a PWM write path. On these boards "
            "the fan chip is an ITE IT8665E driven by the out-of-tree it87.",
        ],
    ),
    VendorQuirk(
        id="asus-asusatk0110-acpi-sensor-read",
        vendor_pattern="asustek",
        chip_prefix="atk0110",
        severity="info",
        summary="ASUS + asus_atk0110 — ACPI sensor read-only path",
        details=[
            "asus_atk0110 exposes board sensors via the ACPI ATK0110 method "
            "(hwmon name 'atk0110'). It is read-only.",
            "If you see this driver loaded but no controllable PWM headers, "
            "the PWM path is on a separate Super I/O driver (nct6775, it87, "
            "or similar). Check that driver's binding status.",
        ],
    ),
    VendorQuirk(
        id="asrock-nct6779-mainline-kernel-coverage",
        vendor_pattern="asrock",
        chip_prefix="nct6779",
        severity="info",
        summary="ASRock AM4 + NCT6779D — mainline kernel coverage is solid",
        details=[
            "NCT6779D is the chip on common AM4 300/400-series ASRock ATX and "
            "micro-ATX boards (B450 Pro4, B450 Steel Legend, X470 Taichi and "
            "similar). The in-kernel nct6775 driver supports monitoring and PWM "
            "writes out of the box.",
            "Under nct6775 the pwm files are always writable and the daemon "
            "switches a header to manual itself — no BIOS setting unlocks them. "
            "If a fan does not follow, check that the header's fan type in BIOS "
            "matches the fan (DC for 3-pin, PWM for 4-pin), then run Test PWM "
            "Control.",
        ],
    ),
    VendorQuirk(
        id="asrock-nct6792-mainline-kernel-coverage",
        vendor_pattern="asrock",
        chip_prefix="nct6792",
        severity="info",
        summary="ASRock AM4 + NCT6792D — mainline kernel coverage is solid",
        details=[
            "NCT6792D is the chip on AM4 ASRock ITX boards (e.g. B450 "
            "Gaming-ITX/ac, X470 Gaming-ITX/ac, B550M-ITX/ac). The in-kernel "
            "nct6775 driver supports monitoring and PWM writes.",
            "Fan headers on the B450 Gaming-ITX/ac: CPU_FAN1, CHA_FAN1, CHA_FAN2 "
            "per the upstream lm-sensors config, which Control-OFC's built-in "
            "label table follows.",
        ],
    ),
    VendorQuirk(
        id="gb-it8686-dual-secondary-it8792e",
        vendor_pattern="gigabyte",
        chip_prefix="it8686",
        severity="info",
        summary=("Gigabyte AM4 400-series AORUS + IT8686E — dual-chip board (secondary IT8792E)"),
        details=[
            "AM4 400-series AORUS boards (X470 AORUS ULTRA GAMING, X470 "
            "AORUS GAMING 5/7 WIFI, B450 AORUS PRO/PRO-CF) pair the primary "
            "IT8686E with a secondary IT8792E. On some of them the secondary "
            "carries no fan header at all — the B450 AORUS PRO's five headers "
            "are all on the IT8686E — so a secondary with no fans is normal there.",
            "If the System State page reports a missing chip, update "
            "it87-dkms-git first — 2026-03+ builds default mmio=on and merge "
            "the ISA-bridge MMIO path that fixes secondary-chip enumeration "
            "(frankcrawford/it87 PR #95/#102). On older builds set "
            "'options it87 mmio=on' in /etc/modprobe.d/it87.conf. Then "
            "reboot. Avoid running sensors-detect after boot.",
            _IT87_V2_RENAME_NOTE,
            "The secondary IT8792E was historically read-only on some "
            "Gigabyte AM4 boards; verify per-header writability before "
            "assigning fans to it in profiles.",
        ],
    ),
    # ── DEC-106: AM4 500-series, AM5 600-series, AM5 800-series ──
    VendorQuirk(
        id="gb-it8688-common-dual-topology",
        vendor_pattern="gigabyte",
        chip_prefix="it8688",
        severity="info",
        summary="Gigabyte AM4 500-series AORUS + IT8688E — common dual-chip topology",
        details=[
            "Most AM4 500-series Gigabyte AORUS boards (X570 AORUS MASTER/"
            "PRO/PRO WIFI/ULTRA/XTREME, B550 AORUS MASTER/PRO, B550 VISION D) "
            "pair the primary IT8688E with a secondary IT8792E for additional "
            "fan headers. Single-chip variants (B550M AORUS PRO, B550I AORUS "
            "PRO AX) ship only the IT8688E.",
            "If the System State page reports a missing secondary chip, "
            "update it87-dkms-git first — 2026-03+ builds default mmio=on "
            "and merge the ISA-bridge MMIO path that fixes secondary-chip "
            "enumeration (frankcrawford/it87 PR #95/#102). On older builds "
            "set 'options it87 mmio=on' in /etc/modprobe.d/it87.conf. Then "
            "reboot. Avoid running sensors-detect after boot — it can leave "
            "the SuperIO bridge in configuration mode (frankcrawford/it87 "
            "issue #70).",
            _IT87_V2_RENAME_NOTE,
            "X570-generation boards can lose IT8792E fan control after "
            "suspend/resume (frankcrawford/it87 issue #99). The reporter found "
            "it working after rebuilding from master in September 2026, and the "
            "author of PR #128 credits that patch's sleep/suspend changes, but "
            "the issue is still open. The daemon re-asserts pwm_enable after "
            "resume; if headers stay stuck, a reboot is the reliable reset.",
        ],
    ),
    VendorQuirk(
        id="msi-nct6687-msi-alt1-auto",
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="info",
        summary="MSI AM5 800-series + nct6687d — msi_alt1 auto-allowlist",
        details=[
            "Current nct6687d builds ship an auto-enabled board allowlist covering "
            "a growing list of MSI B840 / B850 / B860 / X870 / X870E / Z890 boards "
            "(see Fred78290/nct6687d source: `nct6687.c::nct6687_msi_alt_boards[]` — "
            "the authoritative, continuously-updated list; don't trust a "
            "point-in-time count). Each entry is a full DMI board name with its "
            "MS-number, so a variant edition (PZ, WHITE, MAX, a WIFI/non-WIFI "
            "twin) can be missing even when its sibling is listed. On listed "
            "boards the driver enables the "
            "alt1 register layout automatically — no module parameter required.",
            "DO NOT force fan_config=msi_alt1 on a board outside those series. "
            "The chip is the same NCT6687D (it reports 0xd592) on every MSI "
            "generation; what differs is the EC register layout, and only the "
            "B840 / B850 / B860 / X870 / X870E / Z890 boards use the alt1 one "
            "(monitoring tools label them 'NCT6687DR'). Earlier MSI series — "
            "B650 / B660 / X670 / Z690 / Z790 — use the DEFAULT mapping and are "
            "detected correctly. Forcing alt1 there reads EC offsets 0x154-0x15E, "
            "which are zero on those boards, so every SYS_FAN reports 0 RPM while "
            "CPU_FAN keeps working (upstream issue #167, MSI MPG B650 CARBON WIFI).",
            "Check which mapping is active — the driver always prints it: "
            "`sudo dmesg | grep 'active fan config'`. That line reveals a stale "
            "forced setting at a glance. If you previously added "
            "fan_config=msi_alt1 to /etc/modprobe.d/ as a troubleshooting "
            "attempt on an earlier-series board, remove it.",
            "If your board IS in one of those series but is missing from the "
            "allowlist and system fans ignore PWM writes, fan_config=msi_alt1 "
            "is the correct manual override: "
            "`sudo modprobe -r nct6687 && sudo modprobe nct6687 fan_config=msi_alt1`.",
            "msi_fan_brute_force=1 is a SEPARATE, current [BETA] parameter — "
            "not an older-driver alternative to fan_config. It writes all 7 "
            "fan-curve points for system fans (not CPU or pump) and requires "
            "blacklisting nct6683. The two parameters are orthogonal and the "
            "same MSI boards are listed for both.",
        ],
    ),
    VendorQuirk(
        id="msi-nct6687-out-tree-driver",
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="medium",
        summary="MSI AM4 500-series + NCT6687D — out-of-tree driver path",
        details=[
            "MSI B550 boards and the 2021 X570S refresh carry an NCT6687D (MAG "
            "B550 TOMAHAWK, B550-A PRO, MPG B550 GAMING PLUS / GAMING EDGE WIFI, "
            "MPG X570S EDGE MAX WIFI, MPG X570S CARBON MAX WIFI). The original "
            "2019 X570 boards (X570-A PRO, MPG X570 GAMING PLUS / EDGE / CARBON, "
            "MAG X570 TOMAHAWK) are NCT6797D instead — see that note. PWM needs "
            "the out-of-tree `nct6687d` driver from Fred78290/nct6687d: the "
            "in-kernel `nct6683` reads the chip but publishes the pwm files "
            "read-only.",
            "Blacklist nct6683 when you install nct6687d — with both loaded they "
            "can bind the same chip and garble the readings (nct6687d #204, "
            "B550-A PRO).",
            "Reminder: the NCT6687D reports 0xd592 (both drivers match it as "
            "0xd590), which does not overlap NCT6797D's 0xd451 — loading nct6687d "
            "alongside the kernel's nct6775 on a genuine NCT6687D board is safe, "
            "as long as nct6687 is not loaded with force=1 (that attaches to any "
            "Nuvoton chip). The DEC-105 brick risk applies to boards whose chip is "
            "actually an NCT679x.",
        ],
    ),
    # Curator 2026-09-24 (DEC-421): scoped to Taichi boards. Unscoped, this
    # "legitimate dual-Nuvoton" note fired on every ASRock AM5 board, and most
    # of those (Pro RS, PG Lightning, HDV, X870 Nova) have ONE Nuvoton chip.
    # The single-chip case has its own entry below.
    VendorQuirk(
        id="asrock-nct6799-legitimate-dual-nuvoton",
        vendor_pattern="asrock",
        chip_prefix="nct6799",
        severity="info",
        board_pattern="Taichi",
        summary="ASRock AM5 Taichi — legitimate dual-Nuvoton config",
        details=[
            "The AM5 Taichi boards (X670E / X870E / B650E Taichi and their Lite "
            "editions) ship TWO Super-I/O chips: an NCT6686D at I/O 0x0a20 and "
            "an NCT6796D-S at 0x0290, which mainline nct6775 reports as "
            "'NCT6796D-S/NCT6799D-R' (hwmon name nct6799), behind a Fintek "
            "eSPI-to-LPC bridge. Both drivers MUST be loaded concurrently to "
            "reach all fan headers; the in-kernel nct6683 reads the NCT6686D but "
            "publishes it read-only, so writes there need nct6687d.",
            "⚠ nct6687d names the NCT6686D's channels with MSI's labels. On an "
            "X870E Taichi an owner measured its 'Pump Fan' channel driving a "
            "chassis header (CHA_FAN3/4), while the real AIO_PUMP is on the "
            "nct6799 chip (nct6687d #155). Assign the pump role to the real "
            "pump header in the fan wizard so the pump floor covers it; the "
            "mislabelled chassis header may keep a floor it does not need, "
            "which is harmless.",
            "DEC-106 refines the daemon's collision detector so this "
            "configuration is no longer flagged CRITICAL. The brick risk "
            "from DEC-105 applies where nct6687 claims an NCT679x chip — on "
            "current builds only when it is loaded with force=1; on X870E Taichi "
            "Lite and its siblings each driver binds its own physical chip.",
            "If the System State page does surface a (nct6687, nct6775) "
            "collision banner on this board, it means only one nct6 chip "
            "enumerated — verify both chips appear in "
            "`cat /sys/class/hwmon/hwmon*/name` before changing module "
            "blacklists.",
            "References: Fred78290/nct6687d issue #155, ASRock X870E Taichi "
            "Lite manual (block diagram), Level1Techs ASRock Taichi X870E forum "
            "thread.",
        ],
    ),
    VendorQuirk(
        id="asrock-nct6799-mainline-kernel-coverage",
        vendor_pattern="asrock",
        chip_prefix="nct6799",
        severity="info",
        summary="ASRock AM5 + NCT6796D-S — mainline kernel coverage",
        details=[
            "Most ASRock AM5 boards carry an NCT6796D-S — on its own on the Pro "
            "RS, PG Lightning and HDV boards and the X870 Nova WiFi, paired with "
            "a second chip on the Taichi and X870E Nova boards. Mainline nct6775 "
            "reports it as 'NCT6796D-S/NCT6799D-R' (hwmon name nct6799) and "
            "supports monitoring and PWM writes; no out-of-tree driver is needed "
            "for this chip.",
            "Do NOT load nct6687 with force=1 on these boards: it attaches to "
            "any Nuvoton chip ID from 0xD000 to 0xDFFF, this one included.",
        ],
    ),
    VendorQuirk(
        id="asrock-nct6798-mainline-kernel-coverage",
        vendor_pattern="asrock",
        chip_prefix="nct6798",
        severity="info",
        summary="ASRock AM4 500-series + NCT6798D — mainline kernel coverage",
        details=[
            "ASRock AM4 500-series ATX boards report an NCT6798D-class chip "
            "(B550 Steel Legend — physically an NCT6796D-E — B550 Extreme4, "
            "B550 PG Velocita, X570 Taichi, X570 Steel Legend) and are covered "
            "by the in-kernel `nct6775` driver. No out-of-tree driver needed.",
            "Exception: on the B550 Taichi (and its Razer Edition) the fans are "
            "on a separate NCT6683D-class chip; the NCT6798D there reads 0 RPM "
            "on every channel. See the NCT6683 note.",
            "Under nct6775 the pwm files are always writable and the daemon "
            "switches a header to manual itself — no BIOS setting unlocks them. "
            "If a fan does not follow, check that the header's fan type in BIOS "
            "matches the fan (DC for 3-pin, PWM for 4-pin), then run Test PWM "
            "Control.",
        ],
    ),
    VendorQuirk(
        id="asrock-nct6796-mainline-kernel-coverage",
        vendor_pattern="asrock",
        chip_prefix="nct6796",
        severity="info",
        summary="ASRock + NCT6796D — mainline kernel coverage",
        details=[
            "The plain NCT6796D (e.g. the Z790 PG Lightning and Z790 Pro RS, "
            "whose manuals name it) is covered by the in-kernel `nct6775` "
            "driver; no out-of-tree driver needed.",
            "Its variants report under other names: the NCT6796D-S (X870 Nova "
            "WiFi and most AM5 boards) as 'nct6799', and the NCT6796D-E as "
            "'nct6798'.",
            "Do NOT load nct6687 with force=1 on this board: it attaches to "
            "any Nuvoton chip ID from 0xD000 to 0xDFFF.",
        ],
    ),
    VendorQuirk(
        id="asus-nct6798-mainline",
        vendor_pattern="asus",
        chip_prefix="nct6798",
        severity="info",
        summary="ASUS AM4 500-series & Intel + NCT6798D — mainline",
        details=[
            "ASUS AM4 500-series boards (TUF GAMING X570-PLUS, ROG STRIX "
            "X570 / B550 series, PRIME X570-PRO) and LGA1700 boards ship an "
            "NCT6798D, covered by mainline `nct6775`. ASUS AM5 boards use a "
            "different chip — see the nct6799 note. Many also expose extra "
            "sensors via `asus_ec_sensors` (hwmon name 'asusec') — that is a "
            "READ-ONLY enrichment path, not the PWM control path.",
            "Check the `asus_ec_sensors` and `asus_wmi_sensors` mainline "
            "board lists for your specific board: kernel docs at "
            "docs.kernel.org/hwmon/asus_ec_sensors.html.",
        ],
    ),
    # Curator 2026-09-24 (DEC-421): ASUS AM5 boards had no entry, and the
    # prose elsewhere called their chip an NCT6798D. 600-series boards carry an
    # NCT6799D(-R); 800-series AM5 and Intel Z890/B860 boards an NCT6701D (ID
    # 0xd806), which mainline binds as `nct6799`. Sources: zeule/asus-ec-sensors
    # #45 and #100, lm-sensors #416 / #542 / #544, LibreHardwareMonitor #2526.
    VendorQuirk(
        id="asus-nct6799-am5-and-z890",
        vendor_pattern="asustek",
        chip_prefix="nct6799",
        severity="medium",
        consequence="control_loss",
        trigger="bios_revert",
        summary="ASUS AM5 / Z890 + nct6799 — NCT6799D or NCT6701D, firmware may retake fans",
        details=[
            "ASUS AM5 600-series boards carry an NCT6799D(-R). 800-series AM5 "
            "boards (X870E / X870 / B850) and Intel Z890 / B860 boards carry an "
            "NCT6701D (chip ID 0xd806). Mainline nct6775 reports both as "
            "'nct6799' — dmesg says 'NCT6796D-S/NCT6799D-R or compatible chip' — "
            "and sensors-detect calls the NCT6701D an unknown chip.",
            "On NCT6701D boards fans and voltages read correctly, but most "
            "temperature channels are not meaningful (lm-sensors #544). Drive "
            "fan curves from a sensor you can verify, such as the CPU's own "
            "k10temp Tctl or coretemp, not an unlabelled SYSTIN or AUXTIN.",
            "One owner of a ROG STRIX X870E-E measured the firmware switching a "
            "header straight back from manual (pwm_enable 1) to full speed "
            "(zeule/asus-ec-sensors #100), reached through the I/O ports. Kernel "
            "7.1 moved X870 / X870E boards to the ASUS WMI access path; whether "
            "that stops the reclaim is unverified, and B850, Z890 and B860 boards "
            "still use the ports. The daemon's watchdog re-asserts manual mode — "
            "run Test PWM Control to see whether control holds on your board.",
        ],
    ),
    # DEC-326 (2026-09-04): this quirk fires for ANY Gigabyte + IT8696E board,
    # and those boards do not all behave the same way — so it must not promise
    # one outcome. #89's X870E AORUS ELITE X3D genuinely works; the X870E AORUS
    # MASTER measured here loses its secondary while the bridge is latched.
    # DEC-326 said no local setting changes that; DEC-332 measured it
    # recoverable (stop the trigger, then a power cut), and DEC-421 made the
    # recovery one ladder. The text before DEC-326 promised the working outcome
    # to everyone and named `mmio=on` as the remedy, already the driver default.
    VendorQuirk(
        id="gb-it8696-outcome-varies",
        vendor_pattern="gigabyte",
        chip_prefix="it8696",
        severity="low",
        summary="Gigabyte dual Super-I/O (IT8696E + IT87952E) — outcome varies by board",
        details=[
            "Most of these boards pair a primary IT8696E with a secondary "
            "IT87952E. Only the primary is needed for basic fan control; the "
            "secondary carries the remaining headers. Some — the X870E AORUS "
            "ELITE WIFI7 and Z890 AORUS ELITE WIFI7 — have the IT8696E only.",
            "Confirmed working: X870E AORUS ELITE X3D reports both chips with "
            "control working on it87-dkms-git (frankcrawford/it87 issue #89).",
            "Seen failing on X870E AORUS MASTER: one hwmon device appears "
            "instead of two, costing 3 of 8 fan headers and 3 of 9 "
            "temperatures while it lasts. The cause was an ITE eSPI-to-LPC "
            "bridge latched in configuration mode — it answers device ID "
            "0x8883, or 0xFFFF on a read without the unlock key; those are two "
            "views of one blocked state, and the driver logs neither at the "
            "default log level.",
            "It is recoverable (measured 2026-09-05). The latch is written by "
            "the nct6775/w83627ehf modules, which unlock Super-I/O config mode "
            "before reading the device ID and so do the damage even though they "
            "cannot bind to an ITE board (sensors-detect does the same). Stop "
            "them loading, reboot, and if the chip is still missing, power down "
            "fully at the wall — the bridge keeps standby power, so a reboot "
            "alone may not clear it. Do not use mmio=on (already the driver "
            "default, so it changes nothing) or force_id; neither touches this.",
            _IT87_V2_RENAME_NOTE,
        ],
    ),
    # ── DEC-144: B650 GAMING X AX V2 ACPI bind failure ──────────────
    # frankcrawford/it87 issue #92: this board's firmware claims the
    # Super-I/O ports via ACPI, so `modprobe it87` fails with "Device or
    # resource busy". The driver's built-in DMI ACPI-exemption table
    # (it87_acpi_ignore) does NOT include this board as of 2026-09 (fork
    # HEAD bc06d34, re-checked by the curator), so the driver-local
    # parameter remains the documented remediation.
    VendorQuirk(
        id="gb-it8689-amd-b650gamingxaxv2-acpi-conflict-block",
        vendor_pattern="gigabyte",
        chip_prefix="it8689",
        severity="medium",
        platform="amd",
        board_pattern="B650 GAMING X AX V2",
        summary="Gigabyte B650 GAMING X AX V2 — ACPI conflict can block the it87 bind",
        details=[
            "This board's firmware claims the Super-I/O I/O ports via ACPI, "
            "so `modprobe it87` can fail with 'Device or resource busy' "
            "(frankcrawford/it87 issue #92; IT8689E rev 2 at 0x0a40).",
            "Update it87-dkms-git first: 2026-03+ builds default MMIO on, "
            "which per the same issue sidesteps the port conflict on this "
            "chip.",
            "If the bind still fails (or on older builds), use the "
            "driver-local parameter: 'options it87 "
            "ignore_resource_conflict=1' in /etc/modprobe.d/it87.conf — "
            "preferred over the system-wide acpi_enforce_resources=lax.",
            "The driver's built-in DMI ACPI-exemption table does NOT "
            "include this board as of the September 2026 master (its 'B650M "
            "GAMING X AX' entry is a different board), so do not assume a "
            "driver update alone removes the need for the parameter when MMIO "
            "is disabled.",
        ],
    ),
    # ── DEC-110: Intel platform quirks (LGA1700 / LGA1851) ─────────
    # Each entry is platform-scoped so that boards from the same vendor on
    # the opposite platform (e.g. MSI AMD X870E) do not match. Sources
    # cited in DEC-110 / docs/23.
    # Curator 2026-09-24 (DEC-421): keyed on "asusec" — the hwmon name the
    # kernel gives the asus_ec_sensors device. Keyed on the module name it
    # never matched a real board, so it never fired.
    VendorQuirk(
        id="asus-asusecsensors-intel-kernel-documented-allowlist",
        vendor_pattern="asustek",
        chip_prefix="asusec",
        severity="info",
        platform="intel",
        summary="ASUS Intel + asus_ec_sensors — kernel-documented board list",
        details=[
            "The in-tree asus_ec_sensors driver (hwmon name 'asusec') "
            "carries a per-board list that GROWS with every kernel "
            "release — 55 boards in 7.2, 60 in the 7.3 release "
            "candidates — so any list "
            "reproduced here would be wrong within a release. Check "
            "your own board against "
            "docs.kernel.org/hwmon/asus_ec_sensors.html for the "
            "kernel you actually run. Intel LGA1700 coverage in 7.2 is "
            "the ROG MAXIMUS Z690 FORMULA, ROG MAXIMUS Z790 EXTREME "
            "and five ROG STRIX Z690 / Z790 boards; 7.3 adds the ROG "
            "MAXIMUS Z790 HERO and ProArt Z690-CREATOR WIFI (7.3 "
            "release candidates). No Z890 "
            "or B860 board is on the list. Where supported, the driver "
            "provides semantic sensor labels (VRM, T_Sensor, "
            "Water_In/Out, Chipset).",
            "asus_ec_sensors is sensor enrichment only — it never "
            "provides the PWM write path. Fan control on these boards "
            "uses the mainline nct6775 driver (NCT6798D on LGA1700, an "
            "NCT6701D reported as nct6799 on Z890). If the System State "
            "page lists no controllable headers, check that nct6775 is "
            "loaded.",
            "Unlike the AMD side, ASUS Intel WMI sensor bugs (PRIME "
            "X470-PRO etc.) DO NOT apply here — asus_wmi_sensors is "
            "AMD-only per upstream kernel docs.",
        ],
    ),
    VendorQuirk(
        id="asus-nct6798-intel-mainline-kernel-coverage",
        vendor_pattern="asustek",
        chip_prefix="nct6798",
        severity="info",
        platform="intel",
        summary="ASUS Intel Z690/Z790 + NCT6798D — mainline kernel coverage",
        details=[
            "ASUS LGA1700 boards (ROG STRIX Z690/Z790, TUF GAMING "
            "Z690/Z790, PRIME Z690/Z790 series) commonly ship NCT6798D "
            "as the primary Super-I/O chip. The in-kernel nct6775 "
            "driver supports monitoring and PWM writes out of the box.",
            "The DEC-105 chip-ID overlap warning (NCT6797D vs out-of-"
            "tree nct6687) does NOT apply on these Intel boards — they "
            "ship NCT6798D (it reports 0xd42b; the driver matches the "
            "0xd428 class), not NCT6797D (0xd450). Do NOT install the "
            "out-of-tree nct6687d driver on ASUS LGA1700 boards: nothing "
            "here needs it, and loaded with force=1 it would attach to "
            "this chip too.",
        ],
    ),
    VendorQuirk(
        id="msi-nct6687-intel-auto-detect-msi",
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="info",
        platform="intel",
        summary="MSI Intel Z690/Z790 + NCT6687D (plain) — auto-detect, no msi_alt1",
        details=[
            "Per Fred78290/nct6687d (nct6687.c::nct6687_msi_alt_boards[]), "
            "the plain NCT6687D chip on MSI Intel Z690/Z790 boards "
            "(MAG MORTAR, MPG EDGE, MEG ACE, PRO-A) is auto-detected "
            "without `msi_alt1` — the default register mapping is "
            "correct for this generation.",
            "If the pwm files are read-only, the in-kernel nct6683 is bound "
            "instead of nct6687d (it names its device 'nct6687' too) — blacklist "
            "nct6683 and reboot. Never force fan_config=msi_alt1 on these "
            "boards: it reads the wrong registers and every SYS fan reads 0 RPM "
            "(upstream #167).",
            "Distinct from MSI Z890 boards, which need `msi_alt1` — "
            "see the Z890-scoped quirk for that case.",
        ],
    ),
    VendorQuirk(
        id="msi-nct6687-intel-z890-needs-config-msi",
        vendor_pattern="micro-star",
        chip_prefix="nct6687",
        severity="high",
        # Same shape as the X870/B850 entry: "writes accepted, RPM unchanged" is
        # only visible from a verify, never from static discovery.
        consequence="control_loss",
        platform="intel",
        board_pattern="Z890",
        summary="MSI Z890 + NCT6687D (alt register map) — needs fan_config=msi_alt1",
        details=[
            "MSI Z890 boards carry the same NCT6687D (it reports 0xd592) as "
            "earlier MSI boards, with a different EC register layout — "
            "monitoring tools label these boards 'NCT6687DR'. Per "
            "Fred78290/nct6687d (nct6687.c::nct6687_msi_alt_boards[]), the "
            "alt1 register layout is required for correct PWM and "
            "fan-tach register addressing. Current builds of the out-of-tree "
            "driver enable it automatically for the Z890 boards on that list.",
            "If your specific Z890 SKU is NOT yet on the upstream "
            "allowlist, load the driver with fan_config=msi_alt1: "
            "`sudo modprobe -r nct6687 && sudo modprobe nct6687 "
            "fan_config=msi_alt1`. Persist via /etc/modprobe.d/nct6687.conf: "
            "`options nct6687 fan_config=msi_alt1`.",
            "Symptoms of msi_alt1 being needed-but-missing: PWM writes "
            "are accepted but fan RPM does not change, or fan-tach "
            "values read back as 0 / 65535. Check "
            "`sudo dmesg | grep 'active fan config'`.",
            "Several Z890 boards (e.g. MAG Z890 TOMAHAWK WIFI, PRO Z890-P WIFI) "
            "also needed msi_fan_brute_force=1, with nct6683 blacklisted, "
            "before system-fan writes stuck (nct6687d #148, #185) — see the "
            "brute-force note.",
            "Same NCT6687DR chip ships on MSI AMD X870/X870E boards, "
            "but this quirk is Intel-scoped — the AMD case is covered "
            "by the existing AM5 800-series MSI quirk.",
        ],
    ),
    VendorQuirk(
        id="gb-it8689-intel-dual-it87952e",
        vendor_pattern="gigabyte",
        chip_prefix="it8689",
        severity="high",
        consequence="control_loss",
        trigger="dual_chip",
        platform="intel",
        summary="Gigabyte Intel Z690/Z790 AORUS + IT8689E — dual-chip with IT87952E",
        details=[
            "Gigabyte Intel Z690/Z790 AORUS boards (Z690 AORUS PRO / "
            "MASTER, Z790 AORUS MASTER / XTREME / PRO X) pair the primary "
            "IT8689E with a secondary IT87952E for additional fan headers "
            "(on the Z790 AORUS MASTER the secondary sits at 0x0b10 rather "
            "than 0x0a60). The Z790 AORUS ELITE / ELITE AX has the IT8689E "
            "only, and the AMD X670E AORUS boards pair their IT8689E with an "
            "IT8792E instead.",
            "If the System State page reports a missing secondary chip, "
            "update it87-dkms-git first (2026-03+ builds default mmio=on "
            "and fix secondary-chip enumeration and control via the "
            "ISA-bridge MMIO path — PR #95/#102). On older builds set "
            "`options it87 mmio=on` in /etc/modprobe.d/it87.conf. Then "
            "reboot. Avoid running sensors-detect after boot "
            "(frankcrawford/it87 issue #70).",
            _IT87_V2_RENAME_NOTE,
            "BIOS: Smart Fan 6 can take a header back from Linux; the "
            "pwm_enable watchdog detects and re-asserts manual mode.",
            _GB_RECLAIM_NOTE,
            _GB_FULL_SPEED_NOTE,
            "IT8689E revision 1 boards accepted PWM writes with no effect on "
            "driver builds older than the fix (frankcrawford/it87 PR #128, "
            "merged 2026-08-24). The revision is in the kernel log line "
            "'Found IT8689E chip at …, revision N' (`sudo dmesg | grep -i "
            "'found it8'`). The clearest hardware report for the fix is on an "
            "Intel board of exactly this family — a Z790 AORUS MASTER rev 1.0 "
            "with IT8689E revision 1, where fan speed tracked duty across five "
            "steps. Update it87-dkms-git, then verify: that report tested the "
            "patch before it was merged.",
        ],
    ),
    VendorQuirk(
        id="gb-it8696-intel-dual-it87952e",
        vendor_pattern="gigabyte",
        chip_prefix="it8696",
        severity="high",
        consequence="control_loss",
        trigger="dual_chip",
        platform="intel",
        summary="Gigabyte Intel Z890 AORUS + IT8696E — dual-chip with IT87952E",
        details=[
            "Gigabyte Intel Z890 AORUS MASTER boards (MASTER, MASTER AI TOP) "
            "ship the same IT8696E + IT87952E topology as their AMD X870E "
            "counterparts, and the it87 sensor catalogue lists the Z890 AORUS "
            "XTREME AI TOP, PRO ICE and ELITE X ICE the same way. The Z890 AORUS "
            "ELITE WIFI7 (and its ICE / PLUS / DUO X editions) has the IT8696E "
            "only.",
            "Apply the same dual-chip remediation if the secondary chip "
            "fails to enumerate: update it87-dkms-git first (2026-03+ "
            "builds default mmio=on); on older builds set "
            "`options it87 mmio=on`. The daemon's board table includes the "
            "Z890 AORUS MASTER; other Z890 boards are added as exact-board "
            "evidence appears.",
            _IT87_V2_RENAME_NOTE,
            "BIOS: Smart Fan 6 can take a header back from Linux within "
            "seconds; the daemon's watchdog re-asserts manual mode.",
            _GB_RECLAIM_NOTE,
            _GB_FULL_SPEED_NOTE,
        ],
    ),
    VendorQuirk(
        id="asrock-nct6798-intel-mainline-kernel-coverage",
        vendor_pattern="asrock",
        chip_prefix="nct6798",
        severity="info",
        platform="intel",
        summary="ASRock Intel Z690/Z790 + NCT6798D — mainline kernel coverage",
        details=[
            "ASRock LGA1700 boards such as the Z690 Steel Legend and Z690 "
            "Extreme report an NCT6798D-class chip ('nct6798'). On some the "
            "physical part is a sibling that shares the ID — the Z690 Extreme "
            "and Z790 Steel Legend WiFi carry an NCT6796D-E, the Z790 Taichi an "
            "NCT5585D, and the Z790 Nova WiFi one of each. The in-kernel nct6775 "
            "driver supports "
            "monitoring and PWM writes — no out-of-tree driver needed for this "
            "chip.",
            "Under nct6775 the pwm files are always writable and the daemon "
            "switches a header to manual itself — no BIOS setting unlocks them. "
            "If a fan does not follow, check that the header's fan type in BIOS "
            "matches the fan (DC for 3-pin, PWM for 4-pin), then run Test PWM "
            "Control.",
            "On the Z790 Taichi only three headers are on this chip; the other "
            "five are on an NCT6686D, which the in-kernel nct6683 reads but "
            "publishes read-only — see the NCT6686D note. Taichi-class boards "
            "from B550/Z590 on put some or all headers on such a chip.",
        ],
    ),
]


def lookup_vendor_quirks(
    board_vendor: str,
    chip_name: str,
    *,
    cpu_vendor: str = "",
    board_name: str = "",
) -> list[VendorQuirk]:
    """Find vendor+chip specific quirks matching a board and chip.

    DEC-110 additions:
        - ``cpu_vendor`` (``"Intel"``/``"AMD"``/``""``) filters quirks with
          a non-``None`` ``platform`` field. Empty / unknown disables
          platform filtering (matches the pre-DEC-110 behaviour).
        - ``board_name`` filters quirks with a non-empty
          ``board_pattern`` (case-insensitive substring). Empty disables.

    A quirk matches when every non-default scope field also matches —
    so pre-DEC-110 quirks (``platform=None``, ``board_pattern=""``)
    continue to match purely on vendor + chip exactly as before.
    """
    if not board_vendor or not chip_name:
        return []
    vendor_lower = board_vendor.lower()
    chip_lower = chip_name.lower()
    cpu_vendor_lower = cpu_vendor.lower()  # "intel" | "amd" | ""
    board_name_lower = board_name.lower()

    matches: list[VendorQuirk] = []
    for q in VENDOR_QUIRKS_DB:
        if q.vendor_pattern not in vendor_lower:
            continue
        if not chip_lower.startswith(q.chip_prefix):
            continue
        # Platform scope: when a quirk declares one, the caller must supply a
        # matching cpu_vendor. Unknown cpu_vendor (empty) suppresses
        # platform-scoped quirks rather than firing them indiscriminately —
        # the truthful direction is "we don't know, so don't claim".
        if q.platform is not None and (
            not cpu_vendor_lower or cpu_vendor_lower != q.platform.lower()
        ):
            continue
        # Board scope: when set, only fire if the board name contains the
        # substring (case-insensitive). Same suppression rule: empty
        # board_name skips board-scoped quirks rather than firing.
        if q.board_pattern and (
            not board_name_lower or q.board_pattern.lower() not in board_name_lower
        ):
            continue
        matches.append(q)
    return matches


# ---------------------------------------------------------------------------
# Module conflict detection
# ---------------------------------------------------------------------------

CONFLICTING_MODULE_SETS: list[tuple[str, str, str]] = [
    (
        "nct6683",
        "nct6687",
        "Both nct6683 (in-kernel) and nct6687 (out-of-tree) are loaded. "
        "Both can bind the same chip at once, so readings garble and PWM "
        "writes fail (nct6687d #202, #204). Blacklist nct6683 if using nct6687d: "
        "echo 'blacklist nct6683' | sudo tee /etc/modprobe.d/blacklist-nct6683.conf",
    ),
    # DEC-105: GUI-side fallback for daemons that predate the daemon's
    # `module_collisions` field. When the daemon DOES emit module_collisions,
    # system_state_view.py suppresses this banner so the user does not see
    # two warnings for the same problem.
    (
        "nct6687",
        "nct6775",
        "nct6687 (out-of-tree) and nct6775 (in-kernel) are both loaded. If "
        "nct6687 has claimed an NCT679x chip (e.g. the NCT6797D on MSI AM4 "
        "boards) it can scribble into non-volatile fan registers — CPU_FAN has "
        "been bricked by this in the wild. Older nct6687 builds claimed chip ID "
        "0xd450 by default (removed 2026-05-19, nct6687d PR #164), and any build "
        "loaded with force=1 claims every Nuvoton ID from 0xD000 to 0xDFFF. "
        "Do NOT write PWM until resolved. "
        "(1) Identify the chip FIRST: sudo dmesg | grep -i 'found nct' — if "
        "both drivers report a chip at the same address, they claimed the "
        "same one. "
        "(2) For an NCT6797D / NCT6798D (MSI AM4 boards e.g. B450M MORTAR, "
        "MAG B450 TOMAHAWK MAX, MAG X570 TOMAHAWK WIFI), blacklist nct6687: "
        "echo 'blacklist nct6687' | sudo tee /etc/modprobe.d/blacklist-nct6687.conf. "
        "(3) For a genuine NCT6687D (MSI B550 and newer), keep nct6687 and "
        "never load it with force=1; nct6775 has nothing of its own to bind "
        "there unless the board has a second Nuvoton chip (ASRock AM5 Taichi "
        "boards need both drivers). "
        "Blacklisting the wrong driver removes the working fan-control path.",
    ),
]


@dataclass(frozen=True)
class ModuleConflict:
    """Two loaded modules that may interfere with each other."""

    module_a: str
    module_b: str
    explanation: str


def detect_module_conflicts(loaded_modules: list[str]) -> list[ModuleConflict]:
    """Check for known conflicting driver combinations among loaded modules."""
    loaded_set = {m.lower() for m in loaded_modules}
    conflicts = []
    for mod_a, mod_b, explanation in CONFLICTING_MODULE_SETS:
        if mod_a in loaded_set and mod_b in loaded_set:
            conflicts.append(ModuleConflict(mod_a, mod_b, explanation))
    return conflicts


# ---------------------------------------------------------------------------
# AMD GPU advisory database (DEC-098)
#
# Knowledge entries for AMD GPU + kernel combinations. Keyed by the
# `KernelWarning.id` the daemon emits in `amd_gpu.kernel_warnings`, so the
# GUI can render a longer guidance text alongside the daemon's pre-formatted
# message. Distinct from `ChipGuidance` (Super I/O chips) so the two
# concerns don't bleed into each other.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AmdGpuGuidance:
    """Per-warning-ID guidance text the GUI renders next to a kernel advisory.

    `warning_id` matches `KernelWarning.id` on the daemon. `summary` is a
    short headline; `details` is a multi-line list of bullets. References
    point at upstream sources so the user can verify and follow the
    diagnosis themselves.
    """

    warning_id: str
    summary: str
    details: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)


AMD_GPU_GUIDANCE_DB: list[AmdGpuGuidance] = [
    # Curator 2026-09-24 (DEC-421): both entries corrected. The daemon still
    # raises these ids with its own (older) message until its detection rules
    # are revised (register row BRD-b); this text is shown under that message
    # and must be true for anyone who sees it — on any 6.18/6.19 RDNA3/4
    # kernel, and on any R9700 with a PMFW fan_curve.
    AmdGpuGuidance(
        warning_id="rdna_hang_kernel_6_18_6_19",
        summary=(
            "Some RDNA3/RDNA4 hangs were reported on 6.18/6.19; one is fixed in "
            "6.18.7. Stay on a maintained kernel — 6.15-6.17 were never longterm."
        ),
        details=[
            "Phoronix (December 2025) reported RDNA3 (RX 7000) and RDNA4 "
            "(RX 9000) hard hangs under benchmark load on kernels 6.18 and "
            "6.19. It was unbisected at the time, and no follow-up has tied it "
            "to a fix or to a later kernel.",
            "One bisected RDNA4 hang on 6.18 (drm/amd #4765, a 3D workload with "
            "a compute job running alongside it) was fixed in 6.18.7 and "
            "6.19 (commit 3fd20580b96a). Phoronix has since published working "
            "RX 7000 and RX 9000 results on 6.18 and on 7.x kernels.",
            "If you see hangs: update to the latest 6.18 longterm point release "
            "or a current stable 7.x kernel. Do NOT move to 6.15, 6.16 or 6.17 "
            "— none of them was ever a longterm kernel, and all are end-of-life "
            "with no further fixes.",
            "Recovery from a hang typically requires a hard reboot, and while "
            "the system is hung nothing can change a fan's speed — motherboard "
            "fans hold their last duty. Running fan control on a kernel that "
            "hangs is not safe.",
        ],
        references=[
            "https://www.phoronix.com/review/old-amdgpu-eoy2025",
            "https://gitlab.freedesktop.org/drm/amd/-/issues/4765",
            "https://www.kernel.org/category/releases.html",
        ],
    ),
    AmdGpuGuidance(
        warning_id="smu_mismatch_navi48_r9700",
        summary=(
            "The SMU interface-version message on Navi 48 cards is not a fault; "
            "a few R9700 units have unresolved fan faults of their own."
        ),
        details=[
            "The SMU interface-version message (driver 0x2E, firmware 0x32 or "
            "0x33) appears on every Navi 48 card, the RX 9070 XT included, and "
            "is not a fault: the firmware is designed to be backward "
            "compatible, and kernel 7.0 removed the message because 'it just "
            "leads to user confusion' (commit e471627d5627).",
            "pwm1 is read-only on every RDNA4 card by driver design — fan "
            "control goes through the firmware's (PMFW) fan_curve interface, "
            "and that path works on at least some R9700s: an owner changed the "
            "curve with LACT, and an AMD engineer on ROCm #6101 confirmed the "
            "path.",
            "Separately, a few R9700 owners report the fan not responding under "
            "load — one card reached 109 °C (ROCm #6101). Those reports are "
            "per-unit and unresolved; AMD advised a replacement for the "
            "original reporter's card. If your fan does not follow a curve, "
            "return it to automatic mode (the firmware's own curve), watch its "
            "RPM under load, and consider a warranty claim rather than a "
            "kernel change.",
            "PCI device 0x7551 covers the R9700, R9700S and R9600D; the RX 9070 "
            "family is device 0x7550.",
        ],
        references=[
            "https://github.com/ROCm/ROCm/issues/6101",
            "https://git.kernel.org/torvalds/c/e471627d56272a791972f25e467348b611c31713",
        ],
    ),
]


def lookup_amd_gpu_guidance(warning_id: str) -> AmdGpuGuidance | None:
    """Find the GUI-side guidance entry for a daemon-emitted kernel warning."""
    if not warning_id:
        return None
    for entry in AMD_GPU_GUIDANCE_DB:
        if entry.warning_id == warning_id:
            return entry
    return None


# ---------------------------------------------------------------------------
# Post-verification guidance
# ---------------------------------------------------------------------------


def verification_guidance(
    result: str,
    board_vendor: str,
    chip_name: str,
) -> str | None:
    """Return actionable next-step text based on a PWM verify result and board context.

    *result* is one of the daemon's verify outcomes: "effective",
    "pwm_enable_reverted", "pwm_value_clamped", "no_rpm_effect",
    "rpm_unavailable", or — daemon >= 2.48.0 — "pwm_readback_unavailable", or
    — DEC-418 — "pump_protected_mid_run".

    Returns ``None`` for any token this function has no advice for, including
    an unrecognised one. That is the right default and not an oversight: the
    advice here is board-specific next steps, and inventing one for a token
    whose meaning this GUI does not know is how a verdict gets fabricated
    (``verify_view._UNRECOGNISED_HINT`` owns saying so instead).
    """
    if result == "effective":
        return None

    vendor_lower = (board_vendor or "").lower()
    chip_lower = (chip_name or "").lower()

    if result == "pwm_enable_reverted":
        # Curator 2026-09-24 (DEC-421): the "degenerate fan curve" alternative is
        # gone — the only published one was the pre-PR #128 stopgap, and some
        # copies of it had a 0% point, which is what the fans run at whenever the
        # daemon is not in control. "Full Speed" is named only as the fail-safe
        # it is (BRD-16), never as the remedy.
        if "gigabyte" in vendor_lower and chip_lower.startswith("it8"):
            return (
                "The BIOS reclaimed fan control (pwm_enable reverted). "
                f"{_GB_RECLAIM_NOTE} {_GB_FULL_SPEED_NOTE}"
            )
        if "micro-star" in vendor_lower:
            return (
                "The board's EC reclaimed fan control. On MSI boards that use the "
                "msi_alt1 register map (B840/B850/B860/X870/X870E/Z890), load nct6687 "
                "with 'msi_fan_brute_force=1' and blacklist nct6683 — it writes the "
                "duty into all 7 BIOS curve points so the EC's curve cannot pull it "
                "back. The BIOS 'Smart Fan Mode' setting is not a known cause."
            )
        return (
            "The BIOS or EC firmware reclaimed fan control (pwm_enable reverted to "
            "automatic). The daemon's watchdog re-asserts manual mode. Check the "
            "board notes and the BIOS fan settings for this header; a fixed 'full "
            "speed' setting keeps the fan safe while the firmware owns it, but can "
            "also stop Linux controlling it."
        )

    if result == "no_rpm_effect":
        if "gigabyte" in vendor_lower and chip_lower.startswith("it8689"):
            return (
                "PWM writes were accepted but fan speed did not change. On Gigabyte "
                "IT8689E Rev 1 boards (e.g. X670E Aorus Master), driver builds older "
                "than 2026-08-24 let the EC's vector-curve control override manual "
                "mode while a normal BIOS fan curve was active. Update it87-dkms-git "
                "first: the driver fix (frankcrawford/it87 PR #128) merged 2026-08-24 "
                "and three users reported working IT8689E control the day before, "
                "including on Rev 1, and more boards have reported working since the "
                "merge. Re-run this test after updating to confirm on your own board "
                "rather than assume. If it still fails, use a different fan header "
                f"or an external fan controller. {_IT87_V2_RENAME_NOTE}"
            )
        if "asrock" in vendor_lower and chip_lower.startswith("nct6"):
            return (
                "PWM writes were accepted but fan speed did not change. Note this "
                "is not the in-kernel nct6683 failure mode — that driver publishes "
                "pwm read-only on ASRock systems, so its headers are refused "
                "outright rather than accepted. Accepted-but-ineffective writes "
                "point at an out-of-tree driver bound to a board it does not fully "
                "match, a header wired to a different chip than you expect, or a "
                "BIOS override. Try another out-of-tree driver: asrock-nct6683, "
                "nct6687d or nct6686d (see the System State page for links); check "
                "that the header's BIOS fan type matches the fan (DC for 3-pin, PWM "
                "for 4-pin)."
            )
        return (
            "PWM writes were accepted but the fan did not respond. This could mean "
            "the driver's write path is incomplete for this board, the fan is "
            "disconnected, or the BIOS is overriding the value. Check BIOS fan "
            "settings and consider running the verification test on a different header."
        )

    if result == "pwm_value_clamped":
        return (
            "The PWM value was changed by the hardware after writing. The BIOS or "
            "EC may be clamping fan speeds to its own range. Check BIOS fan curve "
            "settings — the EC may override values outside its configured range."
        )

    if result == "rpm_unavailable":
        return (
            "The PWM value was written but RPM feedback is not available on this "
            "header, so the actual effect cannot be confirmed. Listen for fan speed "
            "changes or check another monitoring tool to verify control is working."
        )

    # `ACK-m` / DEC-373. Deliberately different advice from the branch above,
    # because the user's next step is different: there the write is known to
    # have been accepted and only the confirmation is missing, so "listen to
    # the fan" closes it. Here nothing about the write was established, so the
    # next step is to re-run — and to check whether the chip is still there.
    if result == "pwm_readback_unavailable":
        return (
            "The test duty was written, but reading the header back afterwards "
            "failed, so whether it held could not be confirmed. This is usually "
            "transient — re-run the test. If it repeats, check `dmesg` for the "
            "sensor chip's driver; a chip that was removed or unbound mid-test "
            "produces exactly this result."
        )

    # `TS-aw` / DEC-418. Not a fault: pump evidence arrived mid-test, so the
    # daemon stopped a test it had planned for an ordinary fan. Re-running is
    # the whole next step — the next verify is planned for a pump.
    if result == "pump_protected_mid_run":
        return (
            "The header became pump-protected while the test was running (a profile "
            "naming it a pump was activated, or it was assigned the pump role), so the "
            "test stopped before it measured anything. Re-run it: the verify now uses "
            "pump-safe duties."
        )

    return None


# ---------------------------------------------------------------------------
# Dual-chip board warning (DEC-101)
# ---------------------------------------------------------------------------


# Pretty model names for the chips we surface in the warning. Falls back to
# the upper-cased chip name for anything not in the table.
_CHIP_PRETTY_NAMES: dict[str, str] = {
    "it8688": "IT8688E",
    "it8689": "IT8689E",
    "it8696": "IT8696E",
    "it8792": "IT8792E/IT8795E",
    "it87952": "IT87952E",
    "it8686": "IT8686E",
}


def _pretty_chip(chip: str) -> str:
    return _CHIP_PRETTY_NAMES.get(chip.lower(), chip.upper())


def dual_chip_warning_html(
    board_name: str,
    expected_chips: list[str],
    detected_chip_names: list[str],
    *,
    firmware_fan_count: int | None = None,
    reachable_fan_count: int | None = None,
) -> str | None:
    """Return rich-text HTML for the dual-chip board warning, or None.

    Returns None when:
        - ``expected_chips`` is empty (daemon does not know this board)
        - every expected chip is in ``detected_chip_names`` (the kernel
          enumerated the full set — nothing to warn about)

    When some expected chips are missing, returns an HTML string suitable for
    display in a `Qt.RichText` label, naming *which* chip is missing so users
    can correlate with their hardware docs.

    **The text gives ONE recovery ladder, not a discriminator (DEC-421).** It used
    to tell the user to read the secondary chip's device ID from `dmesg` and
    branch: `DEVID=0xFFFF` ("left in config mode — reboot") versus
    `DEVID=0x8883` ("latched bridge — suppress the modules, power down at the
    wall"). That discriminator does not exist in practice, for three measured
    reasons: the driver prints `Unsupported chip (DEVID=…)` with `pr_debug`, so
    it is invisible at the default log level; a `0xFFFF` read exits `it87_find()`
    silently, so that value can never be printed at all; and on Arch/CachyOS
    kernels (`CONFIG_SECURITY_DMESG_RESTRICT=y`) a plain `dmesg` fails for a
    normal user. The two values are also not two faults: frankcrawford/it87 #70
    reads `0xFFFF` without the unlock key and `0x8883` with it on the same
    blocked chip. So the copy gives the ladder upstream gives — stop the trigger,
    reboot, then remove mains power — which is correct whichever value the chip
    would have answered. The daemon's `hwmon/superio.rs::ite_unbound_tail` says
    the same.

    History, kept because the tests still guard it: DEC-326 / `UDOC-h` replaced a
    universal update/`mmio=on`/reboot loop (futile on a latched bridge) with the
    discriminator, and DEC-332 established that the latched case is recoverable
    by a power cut. Both halves survive: `mmio` is still named only as a thing
    not to try, and the wall-power step is still the step people do not guess.
    Per-board specifics are added on top by `lookup_vendor_quirks`, which is
    where measured per-board outcomes live.

    **A single-chip row gets its own heading.** DEC-421 lists a few single-chip
    Gigabyte boards (e.g. X870E AORUS ELITE WIFI7) so the modprobe guard covers
    them; if that one chip is missing, "dual-chip board" would be false.

    **it87 v2.0 renames the chips** (`it8696_a008090a`, from 2026-09-09 builds,
    register row `BRD-a`). The comparison below is exact, so on such a build the
    chips are present and this warning is a false alarm until the name matching
    is widened; the copy says so rather than sending the user round the ladder.

    *board_name* is the DMI ``board_name`` (used only for the heading);
    callers should pass the empty string when DMI is unavailable and the
    function will use a generic heading instead.

    ``firmware_fan_count`` / ``reachable_fan_count`` add a **measurement** to an
    otherwise inferred warning (``X87-d``). Everything above is derived from a
    curated DMI table, so it is only ever as good as that table; where the board's
    own firmware declares a header count (``board_firmware_counts`` on
    ``GET /diagnostics/hardware``) the deficit can be stated as a fact instead.
    Both are optional and both must be present for the sentence to render — a
    half-known deficit is not a measurement.

    **The rendered sentence says "expose a controllable fan header", not
    "reachable", and the distinction is load-bearing.** The only count available on
    this endpoint is `hwmon.total_headers`, which is `pwmN`-capable headers;
    monitor-only tachometers (a `fanN_input` with no matching `pwmN`) live on
    `GET /inventory/hwmon` and are a **disjoint** set. On a board with tach-only
    headers on a *detected* chip — AIO pump tachs routinely land there — calling
    the difference "unreachable" would overstate the deficit by exactly those
    headers. Saying what was actually counted is true on every board and needs no
    second request.
    """
    if not expected_chips:
        return None

    detected_lower = {c.lower() for c in detected_chip_names}
    missing = [c for c in expected_chips if c.lower() not in detected_lower]
    if not missing:
        return None

    expected_count = len(expected_chips)
    detected_count = expected_count - len(missing)

    # Heading uses the board name verbatim when available so users
    # immediately recognise their machine.
    board_part = f"This board ({escape(board_name)})" if board_name.strip() else "This board"
    if expected_count == 1:
        heading = (
            f"<b>ITE Super-IO chip not detected — missing PWM headers</b><br>"
            f"{board_part} is expected to expose 1 ITE Super-IO chip, but the kernel "
            f"enumerated none: "
        )
    elif board_name.strip():
        heading = (
            f"<b>Dual-chip board detected — missing PWM headers</b><br>"
            f"{board_part} is expected to expose {expected_count} ITE "
            f"Super-IO chips, but the kernel only enumerated {detected_count}: "
        )
    else:
        heading = (
            f"<b>Missing PWM headers detected</b><br>"
            f"{board_part} is expected to expose {expected_count} ITE Super-IO chips, "
            f"but the kernel only enumerated {detected_count}: "
        )

    # `X87-d`: state the deficit as a measurement where the board's firmware
    # supplied one. Rendered only when the firmware count genuinely EXCEEDS what
    # is reachable — equal counts mean the missing chip carries no fan headers on
    # this board, and a firmware count BELOW the reachable one means something is
    # wrong with our own arithmetic, not with the user's board, so neither is
    # worth telling them about here.
    measured = ""
    if (
        firmware_fan_count is not None
        and reachable_fan_count is not None
        and firmware_fan_count > reachable_fan_count
    ):
        measured = (
            f"Your board's firmware declares <b>{firmware_fan_count}</b> fan headers "
            f"and <b>{reachable_fan_count}</b> expose a controllable fan header. The "
            f"first count comes from the board itself, not from a lookup table."
            f"<br><br>"
        )

    # _pretty_chip echoes the raw (daemon-supplied) chip name for anything not in
    # the pretty-name table, so escape its output before it lands in rich text.
    expected_pretty = ", ".join(f"<b>{escape(_pretty_chip(c))}</b>" for c in expected_chips)
    missing_pretty = ", ".join(f"<b>{escape(_pretty_chip(c))}</b>" for c in missing)
    chip_summary = (
        f"expected {expected_pretty}; missing {missing_pretty}.<br><br>"
        f"{measured}"
        f"<b>First, is the driver loaded?</b> Run "
        f"<code>sudo dmesg | grep -i it87</code>. A healthy chip prints a "
        f"<i>Found IT8xxxE chip</i> line. No <code>it87</code> lines at all means "
        f"the out-of-tree driver is not loaded: install <code>it87-dkms-git</code>, "
        f"reboot, then click <i>Rescan Hardware</i> (in the footer).<br><br>"
        f"<b>If the driver is loaded and a chip is still missing, work down this "
        f"list, re-checking after each step:</b><br>"
        f"&nbsp;&nbsp;1. Stop whatever is unlocking the Super-I/O: do not run "
        f"<code>sensors-detect</code>, and keep the <code>nct6775</code> / "
        f"<code>w83627ehf</code> modules from loading — they write the unlock "
        f"key even on boards they cannot drive. The daemon package's guard "
        f"does this for the boards it knows: "
        f"<code>sudo journalctl -b -t control-ofc-superio-guard</code> shows a "
        f"<i>not loading nct6775</i> line when it did. (An empty "
        f"<code>lsmod</code> proves nothing — the modules fail to load on these "
        f"boards even when their probe has done the damage.)<br>"
        f"&nbsp;&nbsp;2. Reboot, then click <i>Rescan Hardware</i>.<br>"
        f"&nbsp;&nbsp;3. Still missing: shut down, switch the power supply off or "
        f"unplug it <i>at the wall</i>, wait about 10 seconds, then boot. The ITE "
        f"eSPI-to-LPC bridge that latches keeps standby power, so a reboot or a "
        f"normal shut-down may not clear it.<br><br>"
        f"The kernel log cannot tell you which blocked state you have: the "
        f"driver prints the ID it read (<code>0x8883</code> from the latched "
        f"bridge) only at debug level, and prints nothing at all when it reads "
        f"<code>0xFFFF</code> (nothing answering). Both are cleared by the same "
        f"steps. Do not spend time "
        f"on <code>mmio</code> (already the driver default), <code>force_id</code>, "
        f"or reinstalling the driver — none of them touch this. The full "
        f"walk-through is in the manual, linked below.<br><br>"
        f"<b>False alarm check:</b> if <code>sensors</code> lists your chips with "
        f"a suffix — e.g. <code>it8696_a008090a</code> — they are present. it87 "
        f"builds from 2026-09-09 rename Gigabyte chips that way, and this check "
        f"does not recognise the new names yet.<br><br>"
        f"<i>⚠ {REMEDIATION_DISCLAIMER}</i><br><br>"
        f"<b>Outcomes differ per board, not per board family</b> — two "
        f"boards with the same pair of chips can differ. The frankcrawford/it87 "
        f'<a href="https://github.com/frankcrawford/it87/issues/70">issue #70</a> '
        f"thread tracks the recoverable case on similar boards. See the "
        f"project's "
        f'<a href="https://github.com/Plan-B-Development/control-ofc-gui/blob/main/'
        f'docs/19_Hardware_Compatibility.md">Hardware Compatibility Guide</a> '
        f"for the per-board table, and the manual's "
        f'<a href="https://github.com/Plan-B-Development/control-ofc-gui/blob/main/'
        f'manual/hardware-troubleshooting.md">Hardware Troubleshooting</a> guide '
        f"for the full walk-through."
    )
    return heading + chip_summary


def is_known_dual_chip_board(expected_chips: list[str]) -> bool:
    """Cheap check used by post-verify guidance (DEC-101 / 2F).

    Any board where the daemon emitted ≥2 expected chips is considered a
    dual-chip target. Single-chip lookup hits (or empty lookups) return
    False so the verify-result wording stays unchanged on those boards.
    """
    return len(expected_chips) >= 2


def dual_chip_verify_hint(
    result: str,
    expected_chips: list[str],
    detected_chip_names: list[str],
) -> str | None:
    """Return a one-line follow-up note for the verify result panel
    when the verify outcome could plausibly be tied to the dual-chip
    enumeration problem (DEC-101 / 2F).

    Triggers only on `pwm_value_clamped` and `no_rpm_effect` results
    AND when the board is a known dual-chip target with at least one
    chip missing — the union of "verify suggests something off" and
    "we know about a board-level enumeration gap that would explain
    fewer headers being available than the user expected".

    Returns None when:
        - the result is `effective` (working correctly — no dual-chip
          confusion to explain)
        - the result is `pwm_enable_reverted`, `rpm_unavailable` or
          `pwm_readback_unavailable` — the first is clearly BIOS/EC-driven,
          the second is not a failure at all but an absent tachometer
          (DEC-358 ranks it *inconclusive*), and the third established
          nothing about the write either way (`ACK-m` / DEC-373), so a
          dual-chip hint would be noise in all three cases
        - the board is not a dual-chip target
        - no chips are missing (all expected chips already detected)
    """
    if result not in ("pwm_value_clamped", "no_rpm_effect"):
        return None
    if not is_known_dual_chip_board(expected_chips):
        return None
    detected_lower = {c.lower() for c in detected_chip_names}
    missing = [c for c in expected_chips if c.lower() not in detected_lower]
    if not missing:
        return None
    return (
        "If you also have fan headers missing from the list (your board has "
        f"{len(expected_chips)} ITE chips but only "
        f"{len(expected_chips) - len(missing)} were enumerated), see the "
        "dual-chip notice on the System State page — fixing the enumeration may also "
        "make this header behave."
    )
