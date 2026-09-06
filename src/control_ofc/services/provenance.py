"""Evidence provenance — where every reported value actually came from.

AIO Phase 8 Batch 1 §3, and the Overview's non-negotiable design principle:

    Never silently promote a derived or user-supplied value into a direct
    hardware observation.

Six classifications, verbatim from the spec::

    COMMANDED         what Control-OFC requested
    OBSERVED          what Linux/hwmon actually reported
    DERIVED           what Control-OFC inferred from observations
    USER_METADATA     supplied by the user
    DEVICE_METADATA   supplied by a trusted device definition
    UNVERIFIED        physical properties software cannot establish from
                      motherboard hwmon alone

Where the classification lives, and why
---------------------------------------
Split, deliberately (agreed scope, Q9).

* Fields whose provenance is **fixed by definition** are classified here, by
  the table below. ``requested_pwm`` is always COMMANDED; it cannot be anything
  else, on any machine, and putting it on the wire would inflate every payload
  to restate a constant.
* Fields whose provenance genuinely **varies** carry a ``{value, provenance}``
  envelope from the daemon, read by :func:`from_envelope`. Batch 1 publishes no
  such field — the first is Batch 2 §7's RPM, which is OBSERVED when raw and
  DERIVED once corrected by device metadata. The reader exists now so the export
  format does not change shape between batches.

The distinction is not cosmetic. A table entry is a claim this repository can
verify by reading its own code; an envelope is a claim only the daemon can make.
Classifying a variable field here would be exactly the "silent promotion" the
principle forbids — the GUI asserting an observation it did not make.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from control_ofc.api.models import (
    PROVENANCE_COMMANDED,
    PROVENANCE_DERIVED,
    PROVENANCE_DEVICE_METADATA,
    PROVENANCE_OBSERVED,
    PROVENANCE_UNVERIFIED,
    PROVENANCE_USER_METADATA,
)

#: Human-readable labels. The tokens themselves are the contract; these are the
#: GUI's wording and may change without a wire change.
PROVENANCE_LABELS: dict[str, str] = {
    PROVENANCE_COMMANDED: "Commanded",
    PROVENANCE_OBSERVED: "Observed",
    PROVENANCE_DERIVED: "Derived",
    PROVENANCE_USER_METADATA: "User metadata",
    PROVENANCE_DEVICE_METADATA: "Device metadata",
    PROVENANCE_UNVERIFIED: "Unverified",
}

#: One-line explanations, shown in the evidence disclosure.
PROVENANCE_TOOLTIPS: dict[str, str] = {
    PROVENANCE_COMMANDED: "What Control-OFC asked the hardware to do.",
    PROVENANCE_OBSERVED: "What Linux reported back through hwmon.",
    PROVENANCE_DERIVED: "Inferred by Control-OFC from observations.",
    PROVENANCE_USER_METADATA: "Supplied by you, not measured.",
    PROVENANCE_DEVICE_METADATA: "From a device definition, not measured.",
    PROVENANCE_UNVERIFIED: "Software cannot establish this from motherboard hwmon.",
}

#: Fields whose provenance is fixed by definition.
#:
#: Keyed by the wire field name. Anything absent is UNKNOWN rather than guessed —
#: :func:`classify` returns ``""``, and the renderer omits the column rather than
#: inventing a classification. That is the same rule §4 applies to measurement
#: resolution, and for the same reason.
_FIXED: dict[str, str] = {
    # Commanded — what we asked for.
    "requested_pct": PROVENANCE_COMMANDED,
    "requested_pwm": PROVENANCE_COMMANDED,
    "baseline_pct": PROVENANCE_COMMANDED,
    "perturbed_pct": PROVENANCE_COMMANDED,
    "delta_pct": PROVENANCE_COMMANDED,
    "test_pwm_percent": PROVENANCE_COMMANDED,
    "original_pct": PROVENANCE_COMMANDED,
    # Observed — read back from sysfs.
    "readback_pct": PROVENANCE_OBSERVED,
    "readback_raw": PROVENANCE_OBSERVED,
    "pwm_readback_pct": PROVENANCE_OBSERVED,
    "pwm_enable": PROVENANCE_OBSERVED,
    "pwm_enable_mode": PROVENANCE_OBSERVED,
    "rpm": PROVENANCE_OBSERVED,
    "rpm_before": PROVENANCE_OBSERVED,
    "rpm_after": PROVENANCE_OBSERVED,
    "baseline_rpm": PROVENANCE_OBSERVED,
    "perturbed_rpm": PROVENANCE_OBSERVED,
    "alarm": PROVENANCE_OBSERVED,
    "temperature_c": PROVENANCE_OBSERVED,
    "coolant_c": PROVENANCE_OBSERVED,
    "enable_revert_count": PROVENANCE_OBSERVED,
    # Derived — computed by Control-OFC from the above.
    "relationship": PROVENANCE_DERIVED,
    "confidence": PROVENANCE_DERIVED,
    "direction": PROVENANCE_DERIVED,
    "change_pct": PROVENANCE_DERIVED,
    "delta_rpm": PROVENANCE_DERIVED,
    "noise_floor_rpm": PROVENANCE_DERIVED,
    "responded": PROVENANCE_DERIVED,
    "measurement_resolution_ms": PROVENANCE_DERIVED,
    "monotonic": PROVENANCE_DERIVED,
    "dead_zone_upper_pct": PROVENANCE_DERIVED,
    "clamp_pct": PROVENANCE_DERIVED,
    "possible_device_override": PROVENANCE_DERIVED,
    "first_change_ms": PROVENANCE_DERIVED,
    "rpm_response": PROVENANCE_DERIVED,
    # AIO Phase 8 Batch 2 (DEC-334). The daemon also publishes a per-result
    # `provenance` sidecar, which WINS over this table when present — this stays
    # as the fallback for an older daemon and for fields the sidecar omits.
    "settled_ms": PROVENANCE_DERIVED,
    "mean_rpm": PROVENANCE_DERIVED,
    "stddev_rpm": PROVENANCE_DERIVED,
    "cv_pct": PROVENANCE_DERIVED,
    "dropouts": PROVENANCE_DERIVED,
    "outliers": PROVENANCE_DERIVED,
    "hysteresis_pct": PROVENANCE_DERIVED,
    "hysteresis_verdict": PROVENANCE_DERIVED,
    "min_responsive_pct": PROVENANCE_DERIVED,
    "max_responsive_pct": PROVENANCE_DERIVED,
    "low_plateau_to_pct": PROVENANCE_DERIVED,
    "saturation_from_pct": PROVENANCE_DERIVED,
    "plateaus": PROVENANCE_DERIVED,
    "stability_verdict": PROVENANCE_DERIVED,
    "worst_cv_pct": PROVENANCE_DERIVED,
    "typical_response_ms": PROVENANCE_DERIVED,
    "typical_settling_ms": PROVENANCE_DERIVED,
    "outside_learned_range": PROVENANCE_DERIVED,
    "interpretation_states": PROVENANCE_DERIVED,
    # §7. The corrected figure is DERIVED; the factor behind it is trusted,
    # compiled-in device metadata. Keeping them apart is the point: one is
    # arithmetic, the other is a claim about a specific product.
    "estimated_physical_rpm": PROVENANCE_DERIVED,
    # User metadata — typed in, never measured, and read by nothing.
    "user_metadata": PROVENANCE_USER_METADATA,
    "external_measurements": PROVENANCE_USER_METADATA,
    "device_name": PROVENANCE_USER_METADATA,
    # Device metadata — from a compiled-in policy table.
    "device_policy": PROVENANCE_DEVICE_METADATA,
    "effective_min_pwm_pct": PROVENANCE_DEVICE_METADATA,
    "stop_permitted": PROVENANCE_DEVICE_METADATA,
    "rpm_correction_factor": PROVENANCE_DEVICE_METADATA,
    "correction_factor": PROVENANCE_DEVICE_METADATA,
    "correction_source": PROVENANCE_DEVICE_METADATA,
    "expected_rpm_min": PROVENANCE_DEVICE_METADATA,
    "expected_rpm_max": PROVENANCE_DEVICE_METADATA,
}

#: Properties software cannot establish from motherboard hwmon at all.
#:
#: These are listed by the Overview under "Software must not claim without
#: external evidence". They appear in a report as explicitly UNVERIFIED rows
#: rather than being silently omitted — §5: "Never represent an untested item as
#: PASS", and an absent row reads as a pass to most people.
UNVERIFIABLE: tuple[tuple[str, str], ...] = (
    ("physical_rpm", "True physical RPM where device tach scaling is undocumented"),
    ("header_supply_voltage", "Actual 12 V header supply voltage"),
    ("electrical_duty", "Actual electrical PWM duty on pin 4"),
    ("tach_waveform", "Tach waveform shape"),
    ("coolant_flow", "Coolant flow rate"),
    ("pump_head", "Pump head / pressure"),
    ("device_current", "Actual device current"),
    ("split_rpm", "Individual RPM of devices behind a single-tach splitter"),
    ("acoustic_noise", "Acoustic noise"),
)


@dataclass(frozen=True)
class ProvenanceValue:
    """A value the daemon tagged with its own provenance.

    Read from a ``{"value": ..., "provenance": "OBSERVED"}`` envelope. Batch 1
    publishes none; the reader exists so Batch 2 can add one without reshaping
    the export.
    """

    value: Any = None
    provenance: str = ""

    @property
    def label(self) -> str:
        # Unrecognised token verbatim, never blank (273-i): a newer daemon's
        # classification must be visible, not silently erased.
        return PROVENANCE_LABELS.get(self.provenance, self.provenance)


def fixed_classifications() -> dict[str, str]:
    """Every field this module can classify from definition alone.

    A copy, so a caller cannot mutate the table. Public because the export
    embeds the legend in its document (§3: the model must be serialisable and
    reusable by later batches and export formats) — reaching into the private
    mapping to do that would make the export depend on an implementation detail.
    """
    return dict(_FIXED)


def classify(field_name: str) -> str:
    """The provenance of a field whose classification is fixed by definition.

    ``""`` when this repository cannot say — which is a real answer, not a
    failure. Callers render nothing rather than guessing.
    """
    return _FIXED.get(field_name, "")


def from_envelope(raw: Any) -> ProvenanceValue | None:
    """Read a ``{value, provenance}`` envelope, or ``None`` if this is not one.

    Tolerant by design: a plain scalar is not an envelope and must not be turned
    into one with a fabricated classification.
    """
    if not isinstance(raw, dict):
        return None
    if "value" not in raw or "provenance" not in raw:
        return None
    return ProvenanceValue(value=raw.get("value"), provenance=str(raw.get("provenance") or ""))


def classified_rows(payload: dict[str, Any]) -> list[tuple[str, Any, str]]:
    """``(field, value, provenance)`` for every field this module can classify.

    A daemon envelope **wins over the table**: if the wire tagged a field, that
    tag is used verbatim. The table only fills in for fields the wire does not
    tag, which is what keeps the GUI from overriding an observation the daemon
    actually made.

    Fields with no envelope and no table entry are omitted entirely — the
    alternative is a row claiming UNKNOWN provenance for a value whose provenance
    simply was not asked about, which reads as a deficiency rather than a
    non-question.
    """
    rows: list[tuple[str, Any, str]] = []
    for key, raw in payload.items():
        envelope = from_envelope(raw)
        if envelope is not None:
            rows.append((key, envelope.value, envelope.provenance))
            continue
        token = classify(key)
        if token:
            rows.append((key, raw, token))
    return rows
