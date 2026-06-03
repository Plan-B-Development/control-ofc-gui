"""DEC-117 contract tests for ``SensorThresholds`` parsing.

Covers the parser-tolerance rules in :func:`control_ofc.api.models.parse_sensors`:
- Daemons predating DEC-117 (no ``thresholds`` key) parse cleanly with the
  field defaulting to ``None`` — no exception raised, no fabricated values.
- Daemons emitting a full ``thresholds`` object hand back a populated
  :class:`SensorThresholds`.
- Daemons emitting a partial object (only the attributes the chip exposes)
  hand back a struct with only those fields set.
- A daemon erroneously emitting an empty ``{}`` object yields ``None``, not
  a ghost all-None instance.

These tests are pure parser tests — no Qt / GUI dependency.
"""

from __future__ import annotations

from control_ofc.api.models import SensorReading, SensorThresholds, parse_sensors


def _payload(**overrides) -> dict:
    base = {
        "sensors": [
            {
                "id": "hwmon:amdgpu:0000:03:00.0:edge",
                "kind": "gpu_temp",
                "label": "edge",
                "value_c": 42.0,
                "source": "hwmon",
                "age_ms": 12,
                "chip_name": "amdgpu",
            }
        ]
    }
    base["sensors"][0].update(overrides)
    return base


class TestThresholdsParsing:
    def test_missing_field_defaults_to_none(self):
        """Daemons predating DEC-117 don't emit ``thresholds`` at all."""
        sensors = parse_sensors(_payload())
        assert len(sensors) == 1
        assert sensors[0].thresholds is None

    def test_full_object_populates_every_field(self):
        sensors = parse_sensors(
            _payload(
                thresholds={
                    "max_c": 95.0,
                    "min_c": -5.0,
                    "crit_c": 110.0,
                    "crit_hyst_c": 105.0,
                    "emergency_c": 115.0,
                    "emergency_hyst_c": 110.0,
                    "lcrit_c": -10.0,
                    "offset_c": 0.0,
                    "alarm": False,
                    "max_alarm": False,
                    "crit_alarm": False,
                    "fault": False,
                }
            )
        )
        t = sensors[0].thresholds
        assert t is not None
        assert t.max_c == 95.0
        assert t.crit_c == 110.0
        assert t.emergency_c == 115.0
        assert t.crit_alarm is False
        assert t.fault is False

    def test_partial_object_leaves_other_fields_none(self):
        """Mirrors the wire shape for amdgpu edge sensor — only crit and
        emergency are exposed; everything else stays None."""
        sensors = parse_sensors(_payload(thresholds={"crit_c": 110.0, "emergency_c": 115.0}))
        t = sensors[0].thresholds
        assert t is not None
        assert t.crit_c == 110.0
        assert t.emergency_c == 115.0
        assert t.max_c is None
        assert t.alarm is None
        assert t.fault is None

    def test_empty_thresholds_object_collapses_to_none(self):
        """A daemon erroneously emitting ``{"thresholds": {}}`` would otherwise
        produce a ghost all-None SensorThresholds instance — which the UI
        would treat as "section present, just no data". Parser normalises
        this back to ``None`` so the Detail dialog can use the same "no data"
        branch as the missing-field case."""
        sensors = parse_sensors(_payload(thresholds={}))
        assert sensors[0].thresholds is None

    def test_unknown_threshold_field_is_ignored(self):
        """Forward compatibility: a future daemon adds ``tempN_lowest`` — the
        GUI's older SensorThresholds dataclass must drop the unknown key
        rather than raise ``TypeError``."""
        sensors = parse_sensors(_payload(thresholds={"max_c": 95.0, "tempN_lowest_c": 22.0}))
        t = sensors[0].thresholds
        assert t is not None
        assert t.max_c == 95.0

    def test_non_dict_thresholds_yields_none(self):
        """Defensive: a malformed payload sends a string or number for the
        thresholds key. Parser must not crash."""
        sensors = parse_sensors(_payload(thresholds="garbage"))
        assert sensors[0].thresholds is None

    def test_round_trip_through_reading_keeps_object_identity(self):
        """The thresholds attribute, when set, is the *same* SensorThresholds
        instance the parser produced — we never re-wrap or copy. Tests rely
        on this so a future refactor that adds a copy step gets flagged."""
        payload = _payload(thresholds={"crit_c": 110.0})
        sensors = parse_sensors(payload)
        t = sensors[0].thresholds
        assert isinstance(t, SensorThresholds)


class TestSensorThresholdsDataclass:
    def test_default_is_all_none(self):
        t = SensorThresholds()
        assert t.is_empty() is True

    def test_is_empty_false_with_any_field_set(self):
        assert SensorThresholds(crit_c=105.0).is_empty() is False
        assert SensorThresholds(alarm=False).is_empty() is False

    def test_dataclass_field_round_trip(self):
        """Sanity: dataclass fields persist through manual construction
        — used by SensorDetailDialog test fixtures."""
        t = SensorThresholds(max_c=95.0, crit_c=105.0, crit_alarm=True)
        assert t.max_c == 95.0
        assert t.crit_c == 105.0
        assert t.crit_alarm is True


class TestSensorReadingThresholdsField:
    def test_default_thresholds_is_none(self):
        s = SensorReading()
        assert s.thresholds is None

    def test_explicit_thresholds_persist(self):
        t = SensorThresholds(crit_c=110.0)
        s = SensorReading(thresholds=t)
        assert s.thresholds is t
