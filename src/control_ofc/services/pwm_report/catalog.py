"""What the PWM Test Report can test, on which channel, and what it pre-selects.

Qt-free. Every decision here is advisory: the daemon still runs every guard at
the moment a test starts (its preflight, its own refusals), and the runner
records whatever the daemon says rather than what this module predicted. What
this module owns is the *offer* — which checkboxes exist, which are ticked by
default, and the reason printed beside one that is not available.

The rules, from DEC-404:

* **Active tests run on motherboard (hwmon) headers only** (decision 6). OpenFan
  channels and GPU fans are reported read-only, each with its reason.
* **Tiered depth** (decision 5): the read-only snapshot always runs; the PWM
  control test and tach pairing are pre-selected on writable headers with a fan
  detected; the full sweep and the stall probe are opt-in, per header, and the
  probe is never pre-selected.
* **One flag, one gating shape** (DEC-334): every capability goes through
  :func:`daemon_supports`, and ``is True`` — "the daemon did not say" must not
  enable anything.
* **S4-5**: below daemon 2.52.0 the sweep and the tach pairing are withheld,
  because their figures are known to be wrong there (``PTR-a``/``b``/``c``).
  DEC-405 has no flag, so that one gate is a version comparison.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from control_ofc.api.models import (
    DIAGNOSTIC_CHARACTERIZATION,
    DIAGNOSTIC_CONTROL_PATH,
    DIAGNOSTIC_STALL_PROBE,
    DIAGNOSTIC_VERIFY,
    FanReading,
    HwmonHeader,
)
from control_ofc.services import diagnostic_estimates as est
from control_ofc.services.daemon_features import (
    daemon_meets_minimum,
    daemon_supports,
    minimum_version,
    unsupported_feature_message,
)
from control_ofc.services.pump_protection import header_is_pump_protected

TEST_VERIFY = "verify"
TEST_PAIRING = "pairing"
TEST_SWEEP = "sweep"
TEST_PROBE = "probe"

#: The order tests run in on one header (S4-9 (1)): the quick write check
#: first, then the pairing (read-mostly), then the sweep, and the most
#: disruptive last.
TEST_ORDER: tuple[str, ...] = (TEST_VERIFY, TEST_PAIRING, TEST_SWEEP, TEST_PROBE)

#: S4-1. The sweep asks for 20, 30 … 100 % in both directions. The DAEMON
#: raises the bottom point to a pump's floor and picks the settle time, so the
#: GUI holds no copy of either rule; the run echoes what it actually used.
SWEEP_POINTS_PCT: tuple[int, ...] = tuple(range(20, 101, 10))
SWEEP_BIDIRECTIONAL = True
#: S4-11: the validation session's behaviour dwell.
SWEEP_STABILITY_S = est.STABILITY_DEFAULT_S

#: Display roles the daemon's probe envelope accepts (DEC-407). Advisory here —
#: the daemon re-checks eligibility on every sample.
PROBE_ROLES = frozenset({"chassis_fan", "radiator_fan"})


@dataclass(frozen=True)
class TestSpec:
    test: str
    title: str
    #: The ``/diagnostics/preflight`` token the daemon judges this test by.
    diagnostic: str
    #: What the test does to the header, in plain words, for the consent page.
    #: ``{name}`` is filled with the channel's display name.
    consent: str
    #: Registry id gating the route, or ``None`` when every supported daemon
    #: has it (the PWM control test predates the capability block).
    feature_id: str | None
    #: DEC-404 S4-5: withheld below the DEC-405 evidence fix.
    needs_settled_evidence: bool
    typical_s: int
    worst_s: int


SPECS: Mapping[str, TestSpec] = MappingProxyType(
    {
        TEST_VERIFY: TestSpec(
            test=TEST_VERIFY,
            title="PWM control test",
            diagnostic=DIAGNOSTIC_VERIFY,
            consent=(
                "Sets {name} to a test duty for about six seconds, reads it back, "
                "then puts it back."
            ),
            feature_id=None,
            needs_settled_evidence=False,
            typical_s=est.VERIFY_SECONDS,
            worst_s=est.VERIFY_SECONDS,
        ),
        TEST_PAIRING: TestSpec(
            test=TEST_PAIRING,
            title="Tach pairing",
            diagnostic=DIAGNOSTIC_CONTROL_PATH,
            consent=(
                "Nudges {name} up or down by a few points, twice, and watches every "
                "fan's RPM to see which one follows it."
            ),
            feature_id="control_path_discovery",
            needs_settled_evidence=True,
            typical_s=est.discovery_seconds(),
            worst_s=est.discovery_seconds(worst_case=True),
        ),
        TEST_SWEEP: TestSpec(
            test=TEST_SWEEP,
            title="Full PWM sweep",
            diagnostic=DIAGNOSTIC_CHARACTERIZATION,
            consent=(
                "Drives {name} from 100 % down to 20 % and back up in steps of 10 "
                "(never below a pump's floor), holding each step while RPM settles."
            ),
            feature_id="pwm_behaviour_characterization",
            needs_settled_evidence=True,
            typical_s=est.characterization_seconds(
                len(SWEEP_POINTS_PCT), bidirectional=SWEEP_BIDIRECTIONAL, stability=True
            ),
            worst_s=est.characterization_seconds(
                len(SWEEP_POINTS_PCT), bidirectional=SWEEP_BIDIRECTIONAL, stability=True
            ),
        ),
        TEST_PROBE: TestSpec(
            test=TEST_PROBE,
            title="Stall/restart probe (below 20 %)",
            diagnostic=DIAGNOSTIC_STALL_PROBE,
            consent=(
                "Lowers {name} below 20 % in small steps until the fan stops, then "
                "raises it until it starts again. The fan may stop for a short time. "
                "Any safety stop ends it with a burst at 100 %."
            ),
            feature_id="stall_probe",
            needs_settled_evidence=False,
            typical_s=est.stall_probe_max_seconds(),
            worst_s=est.stall_probe_max_seconds(),
        ),
    }
)


@dataclass(frozen=True)
class Channel:
    """One cooling channel as the report sees it at scope time."""

    channel_id: str
    #: ``hwmon`` | ``openfan`` | ``amd_gpu`` | ``intel_gpu`` | ``nvidia_gpu`` | …
    source: str
    name: str
    role: str = "unknown"
    role_source: str = "none"
    writable: bool = False
    rpm_available: bool = False
    rpm: int | None = None
    in_profile: bool = False
    pump_protected: bool = False
    effective_min_pwm_pct: int | None = None
    stop_permitted: bool | None = None

    @property
    def is_hwmon(self) -> bool:
        return self.source == "hwmon"

    @property
    def fan_detected(self) -> bool:
        """RPM above zero when the scope was taken (S4-9 (2)).

        A header at 0 RPM is still selectable — a fan the profile has stopped
        reads 0 too — it is only not pre-selected.
        """
        return self.rpm is not None and self.rpm > 0


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str = ""


def build_channels(
    headers: Iterable[HwmonHeader],
    fans: Iterable[FanReading],
    capabilities: object | None,
    *,
    name_of: Callable[[str], str],
    profile_member_ids: frozenset[str] = frozenset(),
) -> list[Channel]:
    """Every channel the daemon reports, in stable-id order.

    The union of ``/hwmon/headers`` (a header with no tach still has a PWM) and
    ``/fans`` (OpenFan channels and GPU fans have no header). Identity is the
    daemon's stable id and nothing else (DEC-404): never an index, never a
    ``hwmonN`` number.
    """
    by_header = {h.id: h for h in headers}
    by_fan = {f.id: f for f in fans}
    out: list[Channel] = []
    for cid in sorted(set(by_header) | set(by_fan)):
        header = by_header.get(cid)
        fan = by_fan.get(cid)
        source = fan.source if fan is not None else "hwmon"
        rpm = fan.rpm if fan is not None else None
        if header is not None:
            out.append(
                Channel(
                    channel_id=cid,
                    source="hwmon",
                    name=name_of(cid),
                    role=header.role,
                    role_source=header.role_source,
                    writable=header.is_writable,
                    rpm_available=header.rpm_available,
                    rpm=rpm,
                    in_profile=cid in profile_member_ids,
                    pump_protected=header_is_pump_protected(header, capabilities),
                    effective_min_pwm_pct=header.effective_min_pwm_pct,
                    stop_permitted=header.stop_permitted,
                )
            )
        else:
            out.append(
                Channel(
                    channel_id=cid,
                    source=source,
                    name=name_of(cid),
                    rpm_available=rpm is not None,
                    rpm=rpm,
                    in_profile=cid in profile_member_ids,
                )
            )
    return out


def settled_evidence_supported(capabilities: object | None) -> bool:
    """Whether the connected daemon carries the DEC-405 evidence fix (S4-5).

    A version comparison, and deliberately the only one here: DEC-405 corrected
    what three published figures MEAN and added no flag a client could gate on.
    An unparseable or missing version compares as ``0.0.0`` — below the
    minimum — so the gate fails closed.
    """
    return daemon_meets_minimum("settled_diagnostic_evidence", capabilities)


def _read_only_reason(channel: Channel) -> str:
    if channel.source == "openfan":
        return (
            "OpenFan channels are reported read-only: the report's active tests run "
            "on motherboard headers only."
        )
    if channel.source.endswith("_gpu") or channel.source == "gpu":
        return (
            "GPU fans are reported read-only: the GPU's firmware owns its fan "
            "protection, and the report's active tests run on motherboard headers only."
        )
    return "Active tests run on motherboard headers only; this channel is reported read-only."


def availability(channel: Channel, test: str, capabilities: object | None) -> Availability:
    """Whether *test* can be offered on *channel*, and the reason when it cannot."""
    spec = SPECS[test]
    if not channel.is_hwmon:
        return Availability(False, _read_only_reason(channel))
    if not channel.writable:
        return Availability(False, "This header is read-only, so nothing here can write to it.")
    if spec.feature_id is not None and daemon_supports(spec.feature_id, capabilities) is not True:
        return Availability(False, unsupported_feature_message(spec.feature_id))
    if spec.needs_settled_evidence and not settled_evidence_supported(capabilities):
        return Availability(
            False,
            "Needs control-ofc-daemon "
            f"{minimum_version('settled_diagnostic_evidence')} or newer: before that "
            "version this test's settling and noise figures are known to be wrong.",
        )
    if test == TEST_PROBE:
        return _probe_availability(channel)
    return Availability(True)


def _probe_availability(channel: Channel) -> Availability:
    """DEC-407's envelope, as an offer. The daemon's preflight is authoritative."""
    if channel.pump_protected:
        return Availability(
            False, "Not tested by design: a pump-protected header is never driven below 30 %."
        )
    if channel.role == "cpu_fan":
        return Availability(False, "Not offered on a CPU fan header, by design.")
    if channel.role not in PROBE_ROLES:
        return Availability(
            False,
            "Only offered on a header assigned a chassis-fan or radiator-fan role. "
            "Assign one on the Controls page if that is what this header drives.",
        )
    if not channel.rpm_available:
        return Availability(False, "This header has no tach, so a stall could not be seen.")
    return Availability(True)


def default_selection(
    channels: Iterable[Channel], capabilities: object | None
) -> dict[str, frozenset[str]]:
    """Decision 5: the PWM control test and tach pairing, on writable headers
    with a fan detected. The sweep and the probe are never pre-selected."""
    out: dict[str, frozenset[str]] = {}
    for ch in channels:
        if not (ch.is_hwmon and ch.writable and ch.fan_detected):
            continue
        picked = frozenset(
            t for t in (TEST_VERIFY, TEST_PAIRING) if availability(ch, t, capabilities).available
        )
        if picked:
            out[ch.channel_id] = picked
    return out


def estimate_seconds(selection: Mapping[str, Iterable[str]]) -> tuple[int, int]:
    """(typical, worst) seconds for a selection, before hand-back waits."""
    typical = worst = 0
    for tests in selection.values():
        for t in tests:
            typical += SPECS[t].typical_s
            worst += SPECS[t].worst_s
    return typical, worst


def consent_safety_text(capabilities: object | None) -> str:
    """The review page's safety paragraph — what the user consents to.

    `PTA-i`: the temperature at which a test stops is the DAEMON's
    ``diagnostic_max_temp_c`` (``/capabilities`` ``limits``, daemon >= 2.55.0),
    interpolated rather than restated: the daemon aborts on it, nothing in the
    GUI enforces it, so a literal here promises whatever the constant was when
    the sentence was written. An older daemon does not publish it, and the text
    then names the rule without a figure — never a guessed one.
    """
    limits = getattr(capabilities, "limits", None)
    limit_c = getattr(limits, "diagnostic_max_temp_c", None)
    if isinstance(limit_c, (int, float)) and not isinstance(limit_c, bool):
        hot = f"if a temperature passes {limit_c:g} °C"
    else:
        hot = "if a temperature passes the daemon's diagnostic limit"
    return (
        "The daemon performs every test and puts every header back when a test "
        "ends — even if Control-OFC is closed. A pump-protected header is never "
        "driven below 30 %. A test stops, and the run with it, if the daemon's "
        f"thermal protection becomes active, {hot} or if "
        "temperature readings go stale; the tests after it are listed as not "
        "tested. You can cancel at any time."
    )
