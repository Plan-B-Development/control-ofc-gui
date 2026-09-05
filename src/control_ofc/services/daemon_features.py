"""Wording for capability-gated daemon features (`UDOC-l`).

Qt-free, so widgets, pages and services all reach the same rule — the
`services/pump_protection.py` precedent, and CLAUDE.md's standing one: "a rule
that lives inside one consumer is a rule the other consumers cannot follow".

**Why this module exists.** Six surfaces told the user a feature was
unavailable and stopped there — no required version, no way to check the
running one, no upgrade route:

    This daemon version does not support GPU fan verification.

(quoted without delimiters on purpose — `tests/test_udoc_guidance_truthfulness.py`
sweeps `src/` for that sentence as a string literal, and a guard that matches its
own explanation is a guard that fails on the file defining the rule. CLAUDE.md
records the same trap from `polling.rs`.)

That is true and useless, and it fails the second half of what user-facing
guidance is for. It was never a limitation: every required version is known at
the call site, several were written in a comment directly above the string, and
`settings_page.py` already phrased it correctly three times over. Six sites
simply did not follow the pattern the same file used.

The fix is one registry rather than eleven literals. A call site names a
**feature id**; it never restates a version number or the phrasing, so the two
things that drift cannot drift apart.
`tests/test_udoc_guidance_truthfulness.py::TestUnsupportedFeatureMessagesAreActionable`
asserts every id used in `src/` resolves here and that no hand-written copy of the
dead-end phrasing comes back.
"""

from __future__ import annotations

from types import MappingProxyType

#: Minimum daemon version per feature, with the ADR that introduced it.
#: Read-only at runtime — a mutable module global is a drift vector of its own.
DAEMON_FEATURE_MINIMUMS: MappingProxyType[str, str] = MappingProxyType(
    {
        "gpu_fan_verify": "1.11.0",  # DEC-120, POST /gpu/{id}/fan/verify
        "hardware_readiness": "2.11.0",  # DEC-207, GET /inventory/hardware-readiness
        "superio_port_probe": "2.7.0",  # DEC-203, POST /inventory/superio/probe
        "preferred_sensors": "2.6.0",  # DEC-200, GET /inventory/hwmon
        "validation_sessions": "2.32.0",  # DEC-317, control.validation_sessions
        "pwm_characterization": "2.29.0",  # DEC-313, control.pwm_characterization
        "pump_protection": "2.28.0",  # DEC-311, control.header_roles
        "daemon_config_report": "2.16.0",  # GET /config
        # `remove` array on POST /config/profile-search-dirs. This comment used
        # to cite `DELETE /config/profile-dirs`, a route that has never existed
        # (`WIRE-ad`); the version floor was right, the surface named was not.
        "profile_search_dir_removal": "2.23.0",
    }
)

#: Human-readable feature names, in the grammar of "does not support {name}".
#: Lower-case and un-punctuated so they compose into a sentence.
DAEMON_FEATURE_LABELS: MappingProxyType[str, str] = MappingProxyType(
    {
        "gpu_fan_verify": "GPU fan verification",
        "hardware_readiness": "the combined hardware-readiness report",
        "superio_port_probe": "the active Super-I/O port probe",
        "preferred_sensors": "preferred sensors",
        "validation_sessions": "validation sessions",
        "pwm_characterization": "PWM characterisation",
        "pump_protection": "pump protection",
        "daemon_config_report": "reporting its own configuration",
        "profile_search_dir_removal": "removing a profile search directory",
    }
)


def minimum_version(feature_id: str) -> str:
    """The daemon version that first shipped *feature_id*.

    Raises `KeyError` for an unknown id — deliberately. A silent fallback would
    let a typo render "requires control-ofc-daemon  or newer", which is the
    dead end this module exists to remove, wearing a plausible shape.
    """
    return DAEMON_FEATURE_MINIMUMS[feature_id]


def requires_daemon(feature_id: str) -> str:
    """The canonical parenthetical: ``(requires control-ofc-daemon X.Y.Z or newer)``.

    Byte-identical to the three literals already in `settings_page.py`, which is
    why those could be pointed here without changing a single rendered string.
    Use this to append the requirement to a sentence that has its own lead-in;
    use `unsupported_feature_message` when the whole sentence is the message.
    """
    return f"(requires control-ofc-daemon {minimum_version(feature_id)} or newer)"


def unsupported_feature_message(feature_id: str) -> str:
    """The full "this daemon cannot do X, here is what would" sentence.

    Names the required version *and* where to read the running one, because
    "does not support" alone leaves the user with nothing to act on — the
    finding this module was written for.
    """
    return (
        f"This daemon does not support {DAEMON_FEATURE_LABELS[feature_id]} "
        f"{requires_daemon(feature_id)}. The connected daemon's version is "
        f"shown on the Overview page."
    )
