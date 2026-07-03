"""Pure, dependency-light knowledge base for hardware interpretation.

Modules here are stdlib-only (no Qt, no services, no daemon client) so both
the ``services`` and ``ui`` layers can import them at module top without a
back-reference cycle. They encode *static* domain knowledge:

- :mod:`control_ofc.knowledge.sensor_knowledge` — chip/label/temp-type →
  rich, truthful sensor descriptions, plus classification helpers.
- :mod:`control_ofc.knowledge.hwmon_label_resolver` — three-tier resolution
  of a motherboard PWM header's human-readable label (``/etc/sensors.d`` +
  in-repo fallback table).

Reading ``/etc/sensors.d`` config is plain user-space file I/O, not hardware
access, so it does not violate the GUI ↔ daemon boundary.
"""

from __future__ import annotations
