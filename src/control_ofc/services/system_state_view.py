"""Qt-free view-model layer for the System State page (DEC-211).

Pure builders that turn a ``HardwareDiagnosticsResult`` into frozen ``…VM``
dataclasses the thin ``SystemStatePage`` renderer consumes. All derivation lives
here — the issue-card unification, the interference gauge fraction, the safety/GPU
rows, the hardware registry — so it is unit-testable without a ``QApplication``.

Reuses the confirmed-pure helpers directly (`detect_readiness_problems`,
`advisory_rows`, `chip_rows`, `module_rows`, `readiness_verdict`,
`board_identity_line`, `header_summary_line`, `severity_display`,
`classify_reclaim_severity`, `format_driver_status`, `advisory_detail_html`,
`dual_chip_warning_html`, `detect_module_conflicts`, `lookup_chip_guidance`) and
Qt-free-reimplements the GPU-diagnostics / ACPI / module-collision / interference
blocks formerly inlined in the retired Diagnostics page's ``populate_hw_diagnostics`` (DEC-216).
Nothing here imports PySide6 at author intent (the transitively-pulled
`readiness_report` module imports Qt for its dialog, but these functions are
Qt-free and the app always has PySide6 — the existing pure tests import them the
same way).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from html import escape
from typing import NamedTuple

from control_ofc.api.models import HardwareDiagnosticsResult, HwmonHeader
from control_ofc.services import health_ack
from control_ofc.services.health_ack import Occurrence
from control_ofc.ui.hwmon_guidance import (
    advisory_detail_html,
    detect_module_conflicts,
    dual_chip_warning_html,
    lookup_chip_guidance,
    severity_display,
)
from control_ofc.ui.pages.diagnostics_readiness import classify_reclaim_severity
from control_ofc.ui.widgets import readiness_report as readiness
from control_ofc.ui.widgets.readiness_report import (
    board_identity_line,
    chip_rows,
    detect_readiness_problems,
    header_summary_line,
    module_rows,
    readiness_verdict,
)
from control_ofc.ui.widgets.readiness_report import (
    board_notes as board_notes_for,
)

_HW_COMPAT_URL = (
    "https://github.com/Plan-B-Development/control-ofc-gui/blob/main/"
    "docs/19_Hardware_Compatibility.md"
)
_RECLAIM_HIGH = 10  # revert count at/above which contention is HIGH (== classify_reclaim_severity)

# Severity chip-class → StatusPill/border state. `severity_display` maps every
# problem ("warn"/"critical") + advisory ("critical"/"high"/"medium"/"info")
# severity to a chip class; this collapses those to the pill vocabulary.
_STATE_BY_CSS: dict[str, str] = {
    "CriticalChip": "crit",
    "WarningChip": "warn",
    "CautionChip": "warn",
    "InfoChip": "info",
    "SuccessChip": "ok",
}

# Daemon `thermal_state` → pill state.
#
# DEC-257: this had drifted badly. It carried "warning"/"throttling"/"critical" —
# none of which the daemon has ever sent — and was MISSING "recovery" and
# "no_sensor_fallback", the two states that mean the daemon is actively forcing
# fans. Both fell through `.get(..., "neutral")` and rendered as a calm grey
# pill, so a live thermal recovery looked like nothing was happening. Keys are
# now pinned against the wire vocabulary by
# `test_thermal_state_maps_cover_the_wire_vocabulary`; severities match
# `ui.status_banner.THERMAL_STATES`' chip classes.
_THERMAL_STATE: dict[str, str] = {
    "normal": "ok",
    "recovery": "warn",
    "emergency": "crit",
    "no_sensor_fallback": "warn",
}


def severity_to_state(severity: str) -> str:
    """Map a problem/advisory severity string to the pill/border state."""
    return _STATE_BY_CSS.get(severity_display(severity).css_class, "info")


def daemon_version_at_least(version: str, minimum: tuple[int, int, int]) -> bool:
    """Best-effort semantic ``>=`` for a ``daemon_version`` string (DEC-120).

    Copy of ``diagnostics_page._daemon_version_at_least`` — documented duplication
    (the old page is untouched this stage). Tolerates ``1.11.0-rc1`` / ``1.11`` and
    compares an unparseable/empty version as *below* ``minimum``.
    """
    return version_tuple(version) >= minimum


def version_tuple(version: str) -> tuple[int, int, int]:
    """Parse a version string to a 3-tuple, tolerating ``1.11.0-rc1`` / ``1.11``.

    An unparseable or empty version yields ``(0, 0, 0)`` — i.e. sorts *below*
    every real version, so a comparison against it fails safe.
    """
    core = version.strip().split("-", 1)[0].split("+", 1)[0]
    nums: list[int] = []
    for part in core.split(".")[:3]:
        try:
            nums.append(int(part))
        except ValueError:
            break
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def gui_meets_daemon_floor(app_version: str, min_supported_gui: str) -> bool:
    """Whether this GUI satisfies the daemon's declared minimum GUI version.

    DEC-257. ``min_supported_gui`` is the floor the *daemon* places on the *GUI*
    — the opposite direction from the ``autonomous_control`` gate, which is about
    the daemon being too old. The single place the field was previously used got
    that backwards, rendering it as "this GUI needs control-ofc-daemon >= X". It
    read correctly only because both numbers happened to be 2.0.0.

    An empty floor means the daemon declares none (older daemons omit it), which
    is not a failure — treat it as satisfied rather than as version 0.

    An unparseable *GUI* version is treated the same way, and the symmetry is the
    point. ``constants.APP_VERSION`` falls back to the literal ``"dev"`` whenever
    ``importlib.metadata.version()`` raises ``PackageNotFoundError`` — i.e. every
    run from a source checkout that was not ``pip install``ed, which is the
    documented ``PYTHONPATH=src`` workflow. ``version_tuple("dev")`` is
    ``(0, 0, 0)``, which sorts below every real floor, so the original one-sided
    check raised a permanent, non-dismissible "your GUI is too old" banner against
    a perfectly current daemon. Failing an unknown floor open while failing an
    unknown GUI version closed is an asymmetry with no justification: in both
    cases the answer is "this comparison is not meaningful", and the honest
    response is to not claim a violation.
    """
    if not min_supported_gui.strip():
        return True
    if not _is_comparable_version(app_version):
        return True
    return version_tuple(app_version) >= version_tuple(min_supported_gui)


def _is_comparable_version(version: str) -> bool:
    """Whether *version* carries a real leading number, rather than ``dev``/``""``.

    ``version_tuple`` deliberately coerces junk to ``(0, 0, 0)`` so ordering never
    raises; this distinguishes "genuinely version 0" from "no version at all",
    which that coercion throws away.
    """
    head = version.strip().split("-", 1)[0].split("+", 1)[0].split(".", 1)[0]
    return head.isdigit()


def interference_gauge_fraction(count: int) -> float:
    """Gauge fill (0..1) for a revert count; saturates the ring at HIGH (10)."""
    if count <= 0:
        return 0.0
    return min(count, _RECLAIM_HIGH) / _RECLAIM_HIGH


# ─── View-model dataclasses ────────────────────────────────────────────────


class ConditionCards(NamedTuple):
    """What `build_condition_cards` returns.

    A named pair rather than a bare tuple: the hidden count is not optional
    bookkeeping, it is what reconciles the `N ACTION REQUIRED` pill with a
    shorter list, and a caller that unpacks two anonymous values is one rename
    away from silently dropping it.
    """

    cards: list[IssueCardVM]
    hidden_count: int


@dataclass(frozen=True)
class SilenceState:
    """Everything the page knows about what the user has quietened (DEC-359).

    One carrier rather than five keyword arguments threaded through four
    builders. `CLAUDE.md` records the failure mode it avoids: two arguments to
    the same call derived from different sources eventually disagree, and the
    call site is where nobody looks. Here the two halves are read from one
    settings object and one session set, at one place, and travel together.

    `acknowledged` is **session-only** and `dismissed` **persists** — one rule
    on every surface (the user's decision, 2026-09-11). `kernel_warnings` is the
    pre-existing `acknowledged_kernel_warnings` set, honoured here so a
    "Don't show again" pressed on the startup popup also quietens the identical
    advisory on this page (`ACK-h`); it is persistent because it always was.
    """

    acknowledged: frozenset[str] = frozenset()
    dismissed: frozenset[str] = frozenset()
    kernel_warnings: frozenset[str] = frozenset()
    allow_acknowledge: bool = True
    allow_dismiss: bool = True

    def index(self, rank_of, known_level=None) -> tuple[dict, dict]:
        """`(acknowledged_index, dismissed_index)` for one rank scale."""
        return (
            health_ack.build_index(self.acknowledged, rank_of, known_level),
            health_ack.build_index(self.dismissed, rank_of, known_level),
        )


@dataclass(frozen=True)
class SilenceVM:
    """How one silenceable item should be rendered.

    `hidden` is the caller's decision to drop the row entirely; `quiet` demotes
    an item that must stay on screen. The two are deliberately different, and
    which one applies is a property of the *item*, not of the button pressed —
    see `build_interference_vm`.
    """

    token: str = ""  # what a press stores; "" when the item cannot be silenced
    acknowledged: bool = False
    dismissed: bool = False
    can_acknowledge: bool = False
    can_dismiss: bool = False

    @property
    def quiet(self) -> bool:
        return self.acknowledged or self.dismissed


@dataclass(frozen=True)
class IssueCardVM:
    key: str
    title: str
    description: str  # plain text (problem fix; "" for advisories)
    detail: str | None  # HTML bordered box (workaround / collision / ACPI / dual-chip / advisory)
    doc_url: str | None
    doc_title: str | None
    severity: str  # raw ("critical"|"high"|"warn"|"medium"|"info")
    severity_state: str  # crit | warn | info
    severity_word: str
    severity_glyph: str
    #: DEC-359, corrected by DEC-363. **Which of the two acts decides what
    #: happens to the card: Dismiss removes it, Acknowledge demotes it** — the
    #: bracket goes neutral, the title greys and an *Acknowledged* pill appears,
    #: and it keeps its place in the severity sort because only `severity_state`
    #: is neutralised, never the raw `severity` the sort reads. A status
    #: *reading* is only ever demoted. The `N ACTION REQUIRED` pill counts the
    #: card either way.
    #:
    #: This comment said "silencing it REMOVES the card" until 2026-09-12, which
    #: stated one rule for two different actions and is where `ACK-r` came from:
    #: Acknowledge relabelled its own button and changed nothing else for three
    #: releases. Left standing it would invite the defect straight back.
    silence: SilenceVM = field(default_factory=SilenceVM)


@dataclass(frozen=True)
class BoardNoteVM:
    """One board/chip reference note (DEC-357).

    Carries an *evidence* status rather than being ranked into the condition
    stack: a quirk matches on hardware identity, so it can never clear on its
    own, and an alarm that cannot return to normal is ISA-18.2's definition of a
    nuisance alarm. `severity` is still here because the tier is real
    information — it is simply no longer what decides whether the page shouts.
    """

    key: str  # stable quirk identity (survives prose edits)
    title: str
    detail: str | None  # HTML detail box
    severity: str  # raw ("critical"|"high"|"medium"|"low"|"info")
    severity_css: str  # themed chip class — all four DEC-158 hues
    severity_word: str
    severity_glyph: str
    evidence: str  # observed | not_observed | unverified | reference
    evidence_text: str
    related_key: str  # condition key this note explains ("" if none)
    default_expanded: bool
    #: `ACK-o`. One carrier, like every other silenceable surface. This used to
    #: be four flat fields (`ack_key` / `acknowledged` / `can_acknowledge` /
    #: `can_dismiss`) that the widget adapted into a `SilenceVM` at render time
    #: — two shapes for one concept, with the adapter as the place they could
    #: drift. `silence.token` is :func:`note_ack_key`, and `silence.dismissed`
    #: is always False by construction because a dismissed note is dropped from
    #: `notes` rather than rendered quiet.
    silence: SilenceVM = field(default_factory=SilenceVM)


@dataclass(frozen=True)
class BoardNotesVM:
    notes: list[BoardNoteVM]  # visible (dismissed ones are excluded)
    total: int  # matched for this hardware, before dismissals
    hidden_count: int
    acknowledged_count: int
    related_count: int  # notes explaining a currently-active condition
    unverified_count: int  # notes a PWM verify would settle
    title: str
    subtitle: str


@dataclass(frozen=True)
class InterferenceVM:
    has_contention: bool
    highest_count: int
    header_id: str
    severity: str  # ok | warn | high
    severity_state: str  # ok | warn | crit
    gauge_fraction: float
    title: str
    explanation: str
    #: DEC-359 — demote-not-delete; see `build_interference_vm`.
    silence: SilenceVM = field(default_factory=SilenceVM)


@dataclass(frozen=True)
class GpuConstraintRowVM:
    label: str
    value: str
    state: str  # ok | warn | crit | neutral
    #: DEC-359. **Only the kernel-warning advisory rows are silenceable**, at
    #: every state including `info` — see `build_safety_gpu_vm`. Every other row
    #: here takes the default empty `SilenceVM`, including three that genuinely
    #: raise an alarm (`Overdrive: disabled`, `ppfeaturemask NOT set` and
    #: `amdgpu binding not bound`, all `warn`).
    #:
    #: `ACK-t`: this comment used to say the opposite — "only a row that can
    #: raise an alarm is silenceable" — which is a rule nobody wrote and which
    #: reads as a promise to whoever extends this list. Whether those three warn
    #: rows *should* become silenceable is a behaviour question, deliberately
    #: not settled here; it has its own register row.
    silence: SilenceVM = field(default_factory=SilenceVM)


@dataclass(frozen=True)
class SafetyGpuVM:
    thermal_text: str
    thermal_limit_text: str
    thermal_state: str
    has_gpu: bool
    gpu_model: str
    gpu_rows: list[GpuConstraintRowVM]
    speed_min: int | None
    speed_max: int | None
    speed_bar_visible: bool
    #: DEC-359 — the CPU thermal row, silenceable by the user's explicit
    #: decision. Demoted, never removed: see `build_safety_gpu_vm`.
    thermal_silence: SilenceVM = field(default_factory=SilenceVM)


@dataclass(frozen=True)
class ChipRegistryRowVM:
    kind: str  # "chip" | "module"
    status_label: str  # LOADED | MISSING | MODULE
    status_state: str  # ok | warn | info | neutral
    component: str
    driver: str
    driver_status: str
    mainline: str
    mainline_state: str  # ok | warn
    headers: str
    tooltip: str


@dataclass(frozen=True)
class SystemStateVM:
    board_line: str | None
    summary_line: str
    verdict_text: str
    verdict_state: str
    issues_requiring_attention: int
    issue_count_label: str
    issue_count_state: str  # ok | warn | crit
    issue_cards: list[IssueCardVM]
    #: Conditions the user dismissed. Counted in the pill, absent from the list
    #: — this is what lets a reader reconcile the two (DEC-359).
    conditions_hidden_count: int
    board_notes: BoardNotesVM
    interference: InterferenceVM
    safety_gpu: SafetyGpuVM
    registry_rows: list[ChipRegistryRowVM]


# ─── Detail-box builders (Qt-free reimplementations of the inlined blocks) ──


def build_acpi_detail(diag: HardwareDiagnosticsResult) -> str | None:
    """HTML detail for the ACPI issue card (reimplements the inlined block)."""
    if not diag.acpi_conflicts:
        return None
    lines: list[str] = []
    has_it87 = False
    for c in diag.acpi_conflicts:
        lines.append(
            f"{escape(c.io_range)} claimed by '{escape(c.claimed_by)}' "
            f"— conflicts with {escape(c.conflicts_with_driver)}"
        )
        if c.conflicts_with_driver == "it87":
            has_it87 = True
    if has_it87:
        lines.append(
            "Tip (ITE chips): prefer driver-local 'ignore_resource_conflict=1' "
            "('options it87 ignore_resource_conflict=1' in /etc/modprobe.d/it87.conf) "
            "over the system-wide 'acpi_enforce_resources=lax' kernel parameter."
        )
    else:
        lines.append(
            "Tip: add 'acpi_enforce_resources=lax' to kernel parameters, "
            "or disable ACPI hardware monitoring in BIOS."
        )
    return "<br>".join(lines)


def build_module_collision_detail(diag: HardwareDiagnosticsResult) -> str | None:
    """HTML detail for the module-collision issue card (daemon + GUI fallback)."""
    parts: list[str] = []
    daemon_collisions = getattr(diag, "module_collisions", []) or []
    for col in daemon_collisions:
        parts.append(
            f"<b>{escape(col.module_a)}</b> + <b>{escape(col.module_b)}</b> "
            f"({escape(col.severity.upper())})<br>{escape(col.summary)}<br>"
            f"<i>Remediation:</i> {escape(col.remediation)}"
        )
    daemon_pairs = {tuple(sorted([c.module_a, c.module_b])) for c in daemon_collisions}
    loaded = [m.name for m in diag.kernel_modules if m.loaded]
    for mc in detect_module_conflicts(loaded):
        if tuple(sorted([mc.module_a, mc.module_b])) in daemon_pairs:
            continue
        parts.append(
            f"<b>{escape(mc.module_a)}</b> + <b>{escape(mc.module_b)}</b><br>"
            f"{escape(mc.explanation)}"
        )
    return "<br><br>".join(parts) if parts else None


# ─── Issue-card unification ────────────────────────────────────────────────


def _issue_card_from_problem(diag: HardwareDiagnosticsResult, problem: dict) -> IssueCardVM:
    key = problem["key"]
    if key in ("module_collision", "module_conflict"):
        detail = build_module_collision_detail(diag)
    elif key == "acpi":
        detail = build_acpi_detail(diag)
    elif key == "dual_chip":
        detected = [c.chip_name for c in diag.hwmon.chips_detected]
        # `X87-d`: hand the warning the board's own firmware-declared header
        # count where the daemon read one, so the deficit reads as a measurement
        # rather than an inference from a curated DMI table. `None` on every
        # board that publishes no descriptor, and on daemons before 2.36.0 — the
        # warning then renders exactly as it did.
        #
        # `total_headers`, not `writable_headers`: a BIOS-owned read-only header
        # is discovered and counting it as missing would report a phantom
        # deficit on a working board.
        #
        # It is also the ONLY count this endpoint carries. Monitor-only
        # tachometers (`fanN_input` with no `pwmN`) are a disjoint set living on
        # `GET /inventory/hwmon`, which this path does not fetch — which is why
        # the rendered sentence says "expose a controllable fan header" rather
        # than "are reachable". Claiming reachability would overstate the deficit
        # on a board with tach-only headers on a detected chip.
        firmware = diag.board_firmware_counts
        detail = dual_chip_warning_html(
            diag.board.name,
            list(diag.expected_chips),
            detected,
            firmware_fan_count=firmware.fan_count if firmware else None,
            reachable_fan_count=diag.hwmon.total_headers if firmware else None,
        )
    else:
        detail = None
    sd = severity_display(problem["severity"])
    return IssueCardVM(
        key=key,
        title=problem["label"],
        description=problem["fix"],
        detail=detail,
        doc_url=problem.get("doc_url"),
        doc_title=problem.get("doc_title"),
        severity=problem["severity"],
        severity_state=severity_to_state(problem["severity"]),
        severity_word=sd.word,
        severity_glyph=sd.glyph,
    )


def build_condition_cards(
    diag: HardwareDiagnosticsResult,
    *,
    pwm_control_verified: bool | None = None,
    silence: SilenceState | None = None,
) -> ConditionCards:
    """The severity-sorted cards for conditions requiring the user's attention.

    DEC-357 removed the vendor advisories from this list. They were merged in
    here at DEC-211 and the merge is what made the stack unreadable: a static
    table match on "which motherboard did you buy" sorted alongside — and above
    — conditions the daemon had actually measured, could never clear, and
    carried a severity nothing had observed. Advisories are now
    :func:`build_board_notes`, one collapsed section below.
    """
    silence = silence or SilenceState()
    ack_index, dismiss_index = silence.index(state_rank, known_state)

    cards: list[IssueCardVM] = []
    hidden = 0
    for problem in detect_readiness_problems(diag, pwm_control_verified=pwm_control_verified):
        occ = condition_occurrence(diag, problem)
        rank = state_rank(occ.level)
        if health_ack.is_silenced(dismiss_index, occ, rank):
            hidden += 1
            continue
        card = _issue_card_from_problem(diag, problem)
        silence_vm = SilenceVM(
            token=health_ack.occurrence_token(occ),
            acknowledged=health_ack.is_silenced(ack_index, occ, rank),
            can_acknowledge=silence.allow_acknowledge,
            can_dismiss=silence.allow_dismiss,
        )
        cards.append(
            replace(
                card,
                silence=silence_vm,
                # `ACK-r`: demoted, not deleted — the same move the interference
                # gauge, the thermal row and the GPU advisories already make.
                # Acknowledge used to relabel its own button and change nothing
                # else, leaving the crit bracket and the severity colour exactly
                # as loud as before on the loudest surface this page has. The
                # raw `severity` is untouched, so the card keeps its rank in the
                # sort and does not jump under the user; only the alarm state
                # drops. Dismiss is the other half and already worked — that
                # card is filtered out above.
                severity_state="neutral" if silence_vm.quiet else card.severity_state,
            )
        )
    cards.sort(key=lambda c: severity_display(c.severity).rank, reverse=True)
    return ConditionCards(cards, hidden)


# ─── Board notes (DEC-357) ─────────────────────────────────────────────────


def note_ack_key(key: str, evidence: str) -> str:
    """The identity an acknowledgement or dismissal is stored against.

    **The evidence status is part of the key, deliberately.** `services/alerts`
    learned this the hard way at DEC-282: an acknowledgement stored against a
    bare condition key muted every future recurrence of that condition, forever.
    ISA-18.2's answer is that acknowledgement marks an *occurrence*, so it
    cannot reach the next one.

    The stored format is unchanged from DEC-357 — what changed at DEC-358 is how
    it is *matched*. Equality made every evidence change void the silence,
    including changes in the good direction: dismiss two notes, press the page's
    own **Test fan control** button, have it come back clean, and both notes
    reappeared because `unverified` had become `not_observed`. The page invited
    the press and then punished it. Matching is now a rank comparison
    (:func:`is_silenced`), so a silence survives improvement and breaks only on
    escalation. Read this key as *"silenced no later than this evidence"*.
    """
    return f"{key}@{evidence}"


#: Rank for the pill-state vocabulary shared by conditions, the Interference
#: Monitor, the thermal row and the GPU advisory rows (DEC-359).
#:
#: One scale for four surfaces, because they already share `severity_to_state`.
#: The alternative — a scale per surface — is the shape DEC-334 calls "two
#: gating shapes for one flag", and it would mean a condition and the monitor
#: explaining it could disagree about whether something had got worse.
_STATE_RANK: dict[str, int] = {"neutral": 0, "ok": 0, "info": 1, "warn": 2, "crit": 3}


def known_state(state: str) -> bool:
    """Is this a level from the pill-state vocabulary? (DEC-359 remediation.)

    Passed to `health_ack.build_index` so a STORED level from another
    vocabulary — an evidence word, a corrupt token, an imported one — voids its
    silence instead of maximising it. Needed because DEC-359 merged the board
    notes (evidence scale) and every other surface (pill scale) into one list.
    """
    return state in _STATE_RANK


def state_rank(state: str) -> int:
    """Rank a pill state; an unknown state ranks ABOVE every known one.

    Same direction as `evidence_rank`, for the same reason: a state this build
    does not understand must break a silence rather than hide inside it.
    """
    return _STATE_RANK.get(state, max(_STATE_RANK.values()) + 1)


def condition_fingerprint(diag: HardwareDiagnosticsResult, key: str) -> str:
    """What produced this condition, as a digest (DEC-359).

    The fingerprint is what makes a silence an *occurrence* rather than a
    permanent mute: dismiss "ACPI I/O port conflict" for the two ranges you know
    about, and a third range makes it a new occurrence that speaks again.

    Three of these deserve their reasoning recorded, because the obvious choice
    is wrong in each:

    * ``bios_revert`` fingerprints the severity **bucket**, never the raw
      reclaim count. The count is monotonic within a daemon lifetime
      (`ACK-d`), so fingerprinting it would mint a new occurrence on every
      single reclaim — the dismissal would survive for exactly one tick, which
      is indistinguishable from not having one.
    * ``module_collision`` fingerprints the *pairs*, not the count: swapping
      which two modules collide is a different problem with a different fix.
    * a binary condition gets ``""`` — there is only one way for it to be true,
      so the rank comparison alone carries the escalation.
    """
    hw = diag.hwmon
    if key == "module_collision":
        # `module_a`/`module_b`, NOT `driver_a`/`driver_b`. The first draft used
        # the latter through `getattr(..., "")`, which does not raise — it
        # silently yields empty strings, so every collision fingerprinted
        # identically and a *different* pair of modules would have been covered
        # by an older dismissal. Found by writing the test, not by reading the
        # code, which is why the field names are asserted in it.
        return health_ack.fingerprint(
            f"{c.module_a}:{c.module_b}" for c in (getattr(diag, "module_collisions", []) or [])
        )
    if key == "module_conflict":
        # The conflicting PAIR, not the loaded known-module set. `kernel_modules`
        # is the daemon's curated `KNOWN_MODULES` filtered to what is loaded —
        # Fintek, Winbond, SMSC, three ASUS WMI drivers and more — so
        # fingerprinting all of it meant loading ANY unrelated sensor module
        # changed the occurrence and resurrected the dismissal. The app invites
        # exactly that: the manual tells users to press *Rescan Hardware* right
        # after loading a sensor module. Mirror of the `module_collision` arm's
        # bug in the opposite direction — that one was too narrow, this too wide.
        return health_ack.fingerprint(
            f"{c.module_a}:{c.module_b}"
            for c in detect_module_conflicts([m.name for m in diag.kernel_modules if m.loaded])
        )
    if key == "dual_chip":
        detected = {c.chip_name for c in hw.chips_detected}
        return health_ack.fingerprint(set(diag.expected_chips) - detected)
    if key == "acpi":
        return health_ack.fingerprint(
            f"{c.io_range}:{c.claimed_by}:{c.conflicts_with_driver}" for c in diag.acpi_conflicts
        )
    if key == "bios_revert":
        reverts = getattr(hw, "enable_revert_counts", None) or {}
        if not reverts:
            return ""
        # The bucket, not the number — see the docstring.
        return health_ack.fingerprint([classify_reclaim_severity(max(reverts.values()))])
    return ""


def condition_occurrence(diag: HardwareDiagnosticsResult, problem: dict) -> Occurrence:
    """The occurrence identity for one condition dict."""
    key = problem["key"]
    return Occurrence(
        key=key,
        fingerprint=condition_fingerprint(diag, key),
        level=severity_to_state(problem["severity"]),
    )


def build_board_notes(
    diag: HardwareDiagnosticsResult,
    *,
    pwm_control_verified: bool | None = None,
    acknowledged: set[str] | None = None,
    dismissed: set[str] | None = None,
    allow_acknowledge: bool = True,
    allow_dismiss: bool = True,
) -> BoardNotesVM:
    """Reference notes for this board/chip, with what this machine says of each."""
    ack_index = health_ack.build_index(
        acknowledged or set(), readiness.evidence_rank, readiness.known_evidence
    )
    dismiss_index = health_ack.build_index(
        dismissed or set(), readiness.evidence_rank, readiness.known_evidence
    )
    # `condition_keys` is deliberately NOT passed. Precomputing it here meant
    # passing `detect_readiness_problems`' full set — base conditions *plus*
    # anything promoted from a note — which is exactly the shape
    # `readiness_report.board_notes` documents as wrong: a trigger must name a
    # condition the daemon measured, or a note's evidence comes to depend on
    # another note's evidence. Inert as written (a promoted key is `quirk_…`
    # and no trigger token can match one), and it stayed inert only by an
    # accident of naming. Letting the callee derive it also stops this
    # function computing the conditions, and the notes, twice.
    notes = board_notes_for(diag, pwm_control_verified=pwm_control_verified)

    rows: list[BoardNoteVM] = []
    hidden = 0
    for note in notes:
        ack_key = note_ack_key(note.key, note.evidence)
        occ = Occurrence(key=note.key, fingerprint="", level=note.evidence)
        note_rank = readiness.evidence_rank(note.evidence)
        if health_ack.is_silenced(dismiss_index, occ, note_rank):
            hidden += 1
            continue
        sd = severity_display(note.quirk.severity)
        rows.append(
            BoardNoteVM(
                key=note.key,
                title=note.quirk.summary,
                detail=advisory_detail_html(note.quirk.details) or None,
                severity=note.quirk.severity,
                # The themed chip class, not the coarse pill state. This is what
                # restores DEC-158's four-hue separation on the page: MEDIUM and
                # LOW resolve to CautionChip (amber) and INFO to InfoChip (blue),
                # where `severity_to_state` collapses both into the pill's single
                # "warn". The class is applied with `set_chip_class`, so it also
                # repaints on a live theme change — an interpolated token in an
                # inline stylesheet would freeze at render time.
                severity_css=sd.css_class,
                severity_word=sd.word,
                severity_glyph=sd.glyph,
                evidence=note.evidence,
                evidence_text=note.evidence_text,
                related_key=note.related_key,
                # DEC-158's other lost rule. The detail box has rendered
                # unconditionally expanded since the DEC-211 move, which is what
                # turned this panel into a wall; `default_expanded` had no
                # production consumer at all while a test went on asserting its
                # values. An observed note opens regardless of tier — it is the
                # one the user has to read.
                default_expanded=(
                    sd.default_expanded or note.evidence == readiness.EVIDENCE_OBSERVED
                ),
                silence=SilenceVM(
                    token=ack_key,
                    acknowledged=health_ack.is_silenced(ack_index, occ, note_rank),
                    can_acknowledge=allow_acknowledge,
                    can_dismiss=allow_dismiss,
                ),
            )
        )

    related = sum(1 for r in rows if r.related_key)
    unverified = sum(1 for r in rows if r.evidence == readiness.EVIDENCE_UNVERIFIED)
    acked = sum(1 for r in rows if r.silence.acknowledged)
    total = len(notes)
    if total:
        subtitle = f"Reference material for {diag.board.name or 'this board'}"
    else:
        subtitle = "No documented quirks for this board and chip combination."
    return BoardNotesVM(
        notes=rows,
        total=total,
        hidden_count=hidden,
        acknowledged_count=acked,
        related_count=related,
        unverified_count=unverified,
        title=f"Board notes for this hardware ({total})" if total else "Board notes",
        subtitle=subtitle,
    )


# ─── Interference / safety / registry ──────────────────────────────────────


def _historic_reclaim_text(hw) -> str:
    """Wording for a reclaim nothing has repeated recently.

    The figure is interpolated from what the daemon reported, never restated
    (DEC-292: a threshold spelled into a string drifts the moment it moves).
    """
    ages = getattr(hw, "enable_revert_last_seen_ms", None) or {}
    counts = getattr(hw, "enable_revert_counts", None) or {}
    newest = min((ages[h] for h in counts if h in ages), default=0)
    hours = max(1, round(newest / 3_600_000))
    return (
        f"No reclaim in about {hours} hour(s). The daemon watchdog re-enabled manual "
        "mode at the time and fan control has been stable since, so this is a record "
        "of what happened rather than something to act on. The count is kept because "
        "it never resets while the daemon is running."
    )


def build_interference_vm(
    diag: HardwareDiagnosticsResult,
    *,
    silence: SilenceState | None = None,
) -> InterferenceVM:
    """The BIOS-reclaim gauge, with a silencing decision attached (DEC-359).

    **Silencing this card demotes it; it never removes it.** The distinction is
    a property of the item, not of the button: a condition card is an *alarm*
    and dismissing an alarm you have dealt with is the whole request, but this
    card is a *reading* — `docs/07` calls it and Safety & GPU "always-visible"
    and says the page "keeps its safety-relevant readings on screen". Deleting
    the reading would make the page quieter by making it less true, which is the
    opposite of the fix. Quietened, the gauge keeps its number and loses its
    alarm state.

    Fingerprinted on the severity **bucket**, matching `condition_fingerprint`'s
    `bios_revert` arm — the count is monotonic within a daemon lifetime
    (`ACK-d`), so fingerprinting the raw number would mint a new occurrence on
    every reclaim and the silence would last exactly one tick.
    """
    reverts = getattr(diag.hwmon, "enable_revert_counts", None) or {}
    positive = {k: v for k, v in reverts.items() if v > 0}
    if not positive:
        return InterferenceVM(
            has_contention=False,
            highest_count=0,
            header_id="",
            severity="ok",
            severity_state="ok",
            gauge_fraction=0.0,
            title="No Interference Detected",
            explanation="No BIOS/EC fan-control interference has been observed on any header.",
        )
    header_id = max(positive, key=lambda k: positive[k])
    highest = positive[header_id]
    severity = classify_reclaim_severity(highest)  # "warn" | "high"
    state = {"ok": "ok", "warn": "warn", "high": "crit"}[severity]

    # DEC-360: the monitor keeps the count either way, but says which it is.
    # An hours-old reclaim the watchdog already remediated is history, and
    # rendering it identically to an active fight is what made this panel
    # permanent — the user could neither clear it nor tell the two apart.
    historic = readiness.reclaims_are_historic(diag.hwmon)
    if historic:
        state = "neutral"

    silence = silence or SilenceState()
    ack_index, dismiss_index = silence.index(state_rank, known_state)
    occ = Occurrence(
        key="interference",
        fingerprint=health_ack.fingerprint([severity]),
        level=state,
    )
    rank = state_rank(state)
    silence_vm = SilenceVM(
        token=health_ack.occurrence_token(occ),
        acknowledged=health_ack.is_silenced(ack_index, occ, rank),
        dismissed=health_ack.is_silenced(dismiss_index, occ, rank),
        can_acknowledge=silence.allow_acknowledge,
        can_dismiss=silence.allow_dismiss,
    )
    return InterferenceVM(
        has_contention=True,
        highest_count=highest,
        header_id=header_id,
        severity=severity,
        # Demoted, not deleted: the count and the gauge stay exactly as they
        # are and only the alarm state drops to neutral.
        severity_state="neutral" if silence_vm.quiet else state,
        gauge_fraction=interference_gauge_fraction(highest),
        title=(
            "Interference (quietened)"
            if silence_vm.quiet
            else "Past Interference"
            if historic
            else "High Contention Detected"
            if severity == "high"
            else "Interference Detected"
        ),
        explanation=(
            _historic_reclaim_text(diag.hwmon)
            if historic
            else "The daemon watchdog automatically re-enables manual mode on every reclaim. "
            "Persistently HIGH counts indicate ongoing BIOS contention — see the health "
            "issues above for the BIOS settings to change."
        ),
        silence=silence_vm,
    )


def _fan_method_state(method: str) -> str:
    if method in ("pmfw_curve", "hwmon_pwm"):
        return "ok"
    if method in ("read_only", "none", ""):
        return "warn"
    return "neutral"


def build_safety_gpu_vm(
    diag: HardwareDiagnosticsResult,
    *,
    live_thermal_state: str | None = None,
    silence: SilenceState | None = None,
) -> SafetyGpuVM:
    """Assemble the Safety & GPU card.

    ``live_thermal_state`` is the 1 Hz ``DaemonStatus.thermal_state`` — the same
    field `StatusBanner`, the footer and the ribbon read. It wins over the copy
    in ``diag.thermal_safety`` because the two are not equally fresh:
    ``/diagnostics/hardware`` is fetched once per page visit, so its thermal
    state is a snapshot that can be hours old, and this row was rendering it as
    if it were current. ``None`` (no poll yet) falls back to the snapshot.

    The *threshold* stays on the snapshot deliberately — it is configuration,
    not state — and is interpolated from what the daemon reported. Never compare
    it to a literal: the trip point is per-machine (DEC-308).
    """
    silence = silence or SilenceState()
    ack_index, dismiss_index = silence.index(state_rank, known_state)

    ts = diag.thermal_safety
    snapshot_state = ts.state if ts else ""
    wire_state = live_thermal_state if live_thermal_state else snapshot_state
    state_key = wire_state.strip().lower()
    thermal_text = wire_state.capitalize() if wire_state else "Unknown"
    thermal_limit_text = f"Limit: {ts.emergency_threshold_c:.0f} °C" if ts else ""
    thermal_state = _THERMAL_STATE.get(state_key, "neutral")

    # The thermal row, silenceable by the user's explicit decision (2026-09-11)
    # against this investigation's recommendation. Two things make that safe and
    # both are load-bearing:
    #
    #  * it is DEMOTED, never removed — the state and the per-machine limit stay
    #    on screen, and only the alarm colour drops;
    #  * the silence is keyed on the LIVE poll state (DEC-358), so an escalation
    #    to `emergency` outranks any silence taken at `normal` and the row
    #    speaks again within a second. Before DEC-358 this row read a snapshot
    #    fetched once, which could never escalate — a dismissal there would have
    #    been the permanent mute this whole register exists to remove.
    #
    # And the live alarm the user must never miss does not come through here at
    # all: `thermal_state` reaches `StatusBanner`, the footer and the ribbon by
    # their own paths, none of which consults a silencing list. The daemon acts
    # regardless of every one of them (DEC-165).
    thermal_occ = Occurrence(key="thermal", fingerprint="", level=thermal_state)
    thermal_rank = state_rank(thermal_state)
    # **You cannot permanently mute an alarm while it is firing.** A dismissal is
    # stored at the level it was taken, and this row has a fixed empty
    # fingerprint, so one taken at `crit` satisfies `rank <= stored` for every
    # future state — permanent, and across restarts. The comment here used to
    # claim escalation always wins; that is true of a silence taken at a quieter
    # state and FALSE of one taken at the top, which is the case that matters.
    # So Dismiss is withheld while the row is critical. Acknowledge stays: it is
    # session-only, which is exactly the "I have seen it" that ISA-18.2 says an
    # active alarm should accept.
    thermal_alarm_active = thermal_rank >= state_rank("crit")
    thermal_silence = SilenceVM(
        token=health_ack.occurrence_token(thermal_occ),
        acknowledged=health_ack.is_silenced(ack_index, thermal_occ, thermal_rank),
        dismissed=health_ack.is_silenced(dismiss_index, thermal_occ, thermal_rank),
        can_acknowledge=silence.allow_acknowledge,
        can_dismiss=silence.allow_dismiss and not thermal_alarm_active,
    )
    if thermal_silence.quiet:
        thermal_state = "neutral"

    rows: list[GpuConstraintRowVM] = []
    gpu_model = ""
    speed_min: int | None = None
    speed_max: int | None = None

    gpu = diag.gpu
    if gpu:
        gpu_model = gpu.model_name or "AMD D-GPU"
        rows.append(
            GpuConstraintRowVM(
                "Fan Control", gpu.fan_control_method, _fan_method_state(gpu.fan_control_method)
            )
        )
        rows.append(
            GpuConstraintRowVM(
                "Overdrive",
                "enabled" if gpu.overdrive_enabled else "disabled",
                "ok" if gpu.overdrive_enabled else "warn",
            )
        )
        if gpu.ppfeaturemask:
            rows.append(
                GpuConstraintRowVM(
                    "ppfeaturemask",
                    f"bit 14 {'set' if gpu.ppfeaturemask_bit14_set else 'NOT set'}",
                    "ok" if gpu.ppfeaturemask_bit14_set else "warn",
                )
            )
        elif gpu.fan_control_method == "read_only":
            rows.append(
                GpuConstraintRowVM("ppfeaturemask", "not set on kernel command line", "warn")
            )
        rows.append(
            GpuConstraintRowVM(
                "Zero-RPM", "available" if gpu.zero_rpm_available else "not available", "neutral"
            )
        )
        # The third of the WIRE-v trio, and the only one scoped to *this* GPU.
        # Defaults True (an hwmon node implies a bound driver), so it is worth a
        # row only when the daemon actually says otherwise — an hwmon node
        # without a bound driver is a contradiction the user needs to see.
        if not gpu.amdgpu_driver_bound:
            rows.append(
                GpuConstraintRowVM(
                    "amdgpu binding",
                    "not bound to this GPU's PCI device — fan control will not work",
                    "warn",
                )
            )
        if gpu.fan_speed_min_pct is not None and gpu.fan_speed_max_pct is not None:
            speed_min = gpu.fan_speed_min_pct
            speed_max = gpu.fan_speed_max_pct
        if gpu.fan_minimum_pwm is not None:
            rows.append(
                GpuConstraintRowVM("Firmware min PWM", f"{gpu.fan_minimum_pwm}%", "neutral")
            )
        for kw in gpu.kernel_warnings:
            state = severity_to_state(kw.severity)
            # `ACK-h`: "Don't show again" on the startup popup stores `kw.id` in
            # `acknowledged_kernel_warnings`, and this row ignored it — the same
            # advisory, dismissed once, re-rendered here forever. `api/models`
            # states outright that this list mirrors
            # `/capabilities.amd_gpu.kernel_warnings`, so the ids are one
            # vocabulary and the filter was one `in` away. A silencing decision
            # belongs to the ITEM, not to whichever widget showed it first.
            occ = Occurrence(key=f"gpu_advisory_{kw.id}", fingerprint="", level=state)
            rank = state_rank(state)
            already = kw.id in silence.kernel_warnings
            rows.append(
                GpuConstraintRowVM(
                    f"Advisory ({kw.severity})",
                    kw.message,
                    "neutral"
                    if already
                    or health_ack.is_silenced(ack_index, occ, rank)
                    or health_ack.is_silenced(dismiss_index, occ, rank)
                    else state,
                    silence=SilenceVM(
                        token=health_ack.occurrence_token(occ),
                        acknowledged=health_ack.is_silenced(ack_index, occ, rank),
                        dismissed=already or health_ack.is_silenced(dismiss_index, occ, rank),
                        can_acknowledge=silence.allow_acknowledge,
                        can_dismiss=silence.allow_dismiss,
                    ),
                )
            )

    # WIRE-v: the daemon documents these as a deliberate diagnostic TRIO —
    # `amd_pci_devices[].amdgpu_bound`, `amdgpu_module_loaded` and the per-GPU
    # `amdgpu_driver_bound`. Only the first was read, and one third of the trio
    # cannot distinguish "module blacklisted or missing" from "module loaded but
    # the bind failed" — which are different problems with different fixes.
    for dev in diag.amd_pci_devices:
        if dev.amdgpu_bound:
            continue
        driver = dev.driver or "none"
        if diag.amdgpu_module_loaded:
            detail = (
                f"amdgpu is loaded but NOT bound to this device (driver: {driver}) — bind failure"
            )
        else:
            detail = (
                f"the amdgpu module is not loaded (driver: {driver}) "
                "— blacklisted, missing, or passed through"
            )
        rows.append(GpuConstraintRowVM(f"AMD {dev.pci_bdf}", detail, "warn"))

    if diag.intel_gpu:
        ig = diag.intel_gpu
        gpu_model = gpu_model or (ig.model_name or "Intel D-GPU")
        rows.append(
            GpuConstraintRowVM(
                f"Intel {ig.model_name or 'D-GPU'}",
                f"{ig.fan_control_method} (firmware-managed)",
                "neutral",
            )
        )

    if diag.nvidia_gpu:
        ng = diag.nvidia_gpu
        gpu_model = gpu_model or (ng.model_name or "NVIDIA D-GPU")
        rows.append(
            GpuConstraintRowVM(
                f"NVIDIA {ng.model_name or 'D-GPU'}",
                f"{ng.fan_control_method} (read-only)",
                "neutral",
            )
        )

    return SafetyGpuVM(
        thermal_text=thermal_text,
        thermal_limit_text=thermal_limit_text,
        thermal_state=thermal_state,
        thermal_silence=thermal_silence,
        has_gpu=bool(rows),
        gpu_model=gpu_model,
        gpu_rows=rows,
        speed_min=speed_min,
        speed_max=speed_max,
        speed_bar_visible=speed_min is not None and speed_max is not None,
    )


def _chip_tooltip(chip_name: str) -> str:
    g = lookup_chip_guidance(chip_name)
    if g is None:
        return ""
    parts: list[str] = []
    if g.bios_tips:
        parts.append("BIOS tips:\n" + "\n".join(f"• {t}" for t in g.bios_tips))
    if g.known_issues:
        parts.append("Known issues:\n" + "\n".join(f"• {i}" for i in g.known_issues))
    if g.driver_url:
        parts.append(f"Driver docs: {g.driver_url}")
    return "\n\n".join(parts)


def build_registry_rows(diag: HardwareDiagnosticsResult) -> list[ChipRegistryRowVM]:
    """Chips + kernel modules unified into one registry table (reuses
    `chip_rows`/`module_rows` for the text, adds the status-pill structure)."""
    loaded = {m.name for m in diag.kernel_modules if m.loaded}
    rows: list[ChipRegistryRowVM] = []
    for c, r in zip(diag.hwmon.chips_detected, chip_rows(diag), strict=True):
        is_loaded = c.expected_driver in loaded
        rows.append(
            ChipRegistryRowVM(
                kind="chip",
                status_label="LOADED" if is_loaded else "MISSING",
                status_state="ok" if is_loaded else "warn",
                component=r.chip,
                driver=r.driver,
                driver_status=r.status,
                mainline=r.mainline,
                mainline_state="ok" if c.in_mainline_kernel else "warn",
                headers=r.headers,
                tooltip=_chip_tooltip(r.chip),
            )
        )
    for r in module_rows(diag):
        rows.append(
            ChipRegistryRowVM(
                kind="module",
                status_label="MODULE",
                status_state="info",
                component=r.name,
                driver="—",
                driver_status=r.loaded,
                mainline=r.mainline,
                mainline_state="ok" if r.mainline == "Yes" else "warn",
                headers="—",
                tooltip="",
            )
        )
    return rows


def build_verify_headers(
    headers: list[HwmonHeader],
    display_name: Callable[[str], str] | None = None,
) -> list[tuple[str, str]]:
    """Writable-header combo entries: ``[("name (id)", id), …]``.

    ``display_name`` is ``AppState.fan_display_name`` (DEC-229). Without it the
    combo showed the raw ``HwmonHeader.label``, which on a chip that publishes
    no label files is the daemon's synthesised ``pwmN`` — so the user picked a
    header to verify by an id they had never seen anywhere else in the app.
    Optional so the pure layer stays callable without an ``AppState``.
    """
    resolve = display_name or (lambda hid: "")
    return [
        (f"{resolve(h.id) or h.label or h.id} ({h.id})", h.id) for h in headers if h.is_writable
    ]


def build_system_state_vm(
    diag: HardwareDiagnosticsResult,
    *,
    pwm_control_verified: bool | None = None,
    acknowledged_notes: set[str] | None = None,
    dismissed_notes: set[str] | None = None,
    allow_acknowledge: bool = True,
    allow_dismiss: bool = True,
    live_thermal_state: str | None = None,
    silence: SilenceState | None = None,
) -> SystemStateVM:
    problems = detect_readiness_problems(diag, pwm_control_verified=pwm_control_verified)
    n = len(problems)
    verdict_text, verdict_cls = readiness_verdict(diag)
    if n == 0:
        issue_count_label = "SYSTEM READY"
        issue_count_state = "ok"
    else:
        # DEC-357: one label, the state carries the tier. The old wording
        # ("N ISSUES REQUIRE ATTENTION") counted `problems` while the list below
        # rendered problems *plus* advisories, so the pill and the stack
        # disagreed about how many things were wrong. ISA-18.2's framing is the
        # honest one and it is now literally true of this list: an alarm is a
        # condition requiring a response, and every entry is one.
        issue_count_label = f"{n} ACTION REQUIRED"
        issue_count_state = "crit" if any(p["severity"] == "critical" for p in problems) else "warn"
    # `n` above is `len(problems)` and is deliberately computed BEFORE any
    # silencing, so the pill counts what is wrong with the machine rather than
    # what is on screen. `AlertLedger.active_count` states the rule: a health
    # number is never quietened by a button, and a page that can be made to read
    # SYSTEM READY while fan control is dead is a worse defect than the noise
    # this change removes. `conditions_hidden_count` is what reconciles the two
    # for the reader — without it the pill and the list simply disagree.
    issue_cards, conditions_hidden = build_condition_cards(
        diag, pwm_control_verified=pwm_control_verified, silence=silence
    )
    return SystemStateVM(
        board_line=board_identity_line(diag),
        summary_line=header_summary_line(diag.hwmon),
        verdict_text=verdict_text,
        verdict_state=_STATE_BY_CSS.get(verdict_cls, "info"),
        issues_requiring_attention=n,
        issue_count_label=issue_count_label,
        issue_count_state=issue_count_state,
        issue_cards=issue_cards,
        conditions_hidden_count=conditions_hidden,
        board_notes=build_board_notes(
            diag,
            pwm_control_verified=pwm_control_verified,
            acknowledged=set(silence.acknowledged) if silence else acknowledged_notes,
            dismissed=set(silence.dismissed) if silence else dismissed_notes,
            allow_acknowledge=silence.allow_acknowledge if silence else allow_acknowledge,
            allow_dismiss=silence.allow_dismiss if silence else allow_dismiss,
        ),
        interference=build_interference_vm(diag, silence=silence),
        safety_gpu=build_safety_gpu_vm(
            diag, live_thermal_state=live_thermal_state, silence=silence
        ),
        registry_rows=build_registry_rows(diag),
    )
