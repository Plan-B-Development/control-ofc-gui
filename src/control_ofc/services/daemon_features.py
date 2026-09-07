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
        # DEC-334, control.pwm_behaviour_characterization. Registered as its OWN
        # id rather than folded into `pwm_characterization`: an older daemon HAS
        # the latter and silently ignores the two new request fields instead of
        # rejecting them, so a client gating on it would render empty hysteresis
        # and stability panels as though the hardware had produced them.
        "pwm_behaviour_characterization": "2.40.0",
        # DEC-335, control.thermal_observation. Gates the thermal-observation
        # session kind; see the flag's own note in `models.py` for why an
        # ungated client mislabels an ordinary session rather than failing.
        "thermal_observation": "2.41.0",
        # `P8-az` (Run 2), control.validation_auto_stop. Gates the
        # `stop_when_diagnostics_complete` field on POST /validation/session.
        # Gate on this, never on `validation_sessions`: an older daemon HAS the
        # session routes and `serde` drops the unknown field rather than
        # rejecting it, so the request returns 200 and the session then records
        # for the full two-hour sample cap while the client says it will stop
        # itself. Same shape as `pwm_behaviour_characterization` above.
        "validation_auto_stop": "2.43.0",
        # DEC-333, control.control_path_discovery / control.diagnostic_preflight
        "control_path_discovery": "2.39.0",
        "diagnostic_preflight": "2.39.0",
        "pump_protection": "2.28.0",  # DEC-311, control.header_roles
        "daemon_config_report": "2.16.0",  # GET /config
        # `remove` array on POST /config/profile-search-dirs. This comment used
        # to cite `DELETE /config/profile-dirs`, a route that has never existed
        # (`WIRE-ad`); the version floor was right, the surface named was not.
        "profile_search_dir_removal": "2.23.0",
    }
)

#: The ``GET /capabilities`` ``control.*`` flag that advertises each feature,
#: where one exists (`WIRE-k`).
#:
#: **The flag outranks the version, and this mapping is why.** A version string
#: says when a feature first *appeared*; a flag says whether the connected build
#: *serves* it. Five of these — `gpu_fan_verify`, `hardware_readiness`,
#: `superio_port_probe`, `preferred_sensors`, `daemon_config_report` — shipped
#: before the daemon had keys for them, so the GUI detected them by comparing the
#: version or by reading a `404` off the route. Neither is a contract: the route
#: fallback's 404 is indistinguishable from a handler's own 404 for an unknown
#: id, which is exactly why `pwm_characterization` and `validation_sessions` were
#: given flags. Daemon 2.36.0 added the missing five.
#:
#: Ids absent from this map have no flag and never will — they are gated by
#: version or by probe alone. That is a deliberate hole, not an oversight: see
#: `daemon_supports`, which reports "did not say" for both cases so a caller
#: cannot tell them apart and cannot accidentally treat one as a denial.
DAEMON_FEATURE_CAPABILITY_FLAGS: MappingProxyType[str, str] = MappingProxyType(
    {
        "gpu_fan_verify": "gpu_fan_verify",
        "hardware_readiness": "hardware_readiness",
        "superio_port_probe": "superio_port_probe",
        "preferred_sensors": "preferred_sensors",
        "daemon_config_report": "daemon_config_report",
        "validation_sessions": "validation_sessions",
        "validation_auto_stop": "validation_auto_stop",
        "pwm_characterization": "pwm_characterization",
        "pwm_behaviour_characterization": "pwm_behaviour_characterization",
        "thermal_observation": "thermal_observation",
        "control_path_discovery": "control_path_discovery",
        "diagnostic_preflight": "diagnostic_preflight",
        # The flag's name is not the feature id: DEC-311 named the capability
        # after what the daemon *classifies* (header roles), while the GUI names
        # the id after what the user *gets* (pump protection).
        "pump_protection": "header_roles",
        "profile_search_dir_removal": "profile_search_dir_remove",
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
        "pwm_behaviour_characterization": "PWM behaviour characterisation",
        "thermal_observation": "thermal observation sessions",
        "validation_auto_stop": "stopping a session when its diagnostics finish",
        "control_path_discovery": "control-path discovery",
        "diagnostic_preflight": "the diagnostic safety preflight",
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


def daemon_supports(feature_id: str, capabilities: object | None) -> bool | None:
    """Whether the connected daemon serves *feature_id*, per its own capabilities.

    Tri-state, and the third state is the point (`WIRE-k`):

    ``True``
        The daemon advertises the flag. Call the route.
    ``False``
        The daemon advertises the flag as false. **Do not call the route** —
        stand the feature down without a request.
    ``None``
        The daemon did not say: it predates the flag, sent no ``control`` block,
        or is not connected. The caller keeps whatever fallback it already had —
        a version comparison, or probe-then-recover.

    **``None`` is only reachable for the five `WIRE-k` ids** — precisely those
    modelled on ``ControlCapability`` as ``bool | None``. Every *other* entry in
    ``DAEMON_FEATURE_CAPABILITY_FLAGS`` is a plain ``bool`` defaulting to
    ``False``, so an older daemon reads as a denial here. That is correct for
    them — absence and false mean the same thing to the GUI, which is why they
    were modelled that way — but it means a caller must not read a ``False``
    from one of them as "the daemon explicitly said no". Do not "fix" this by
    making them tri-state without a consumer that needs the distinction; each of
    their call sites was written for two states.

    **That sentence used to enumerate the plain-``bool`` ids and say there were
    "four".** It was wrong from DEC-333, which added ``control_path_discovery``
    and ``diagnostic_preflight`` without touching it, and would have been wrong
    again at DEC-334. The list and the count are **deleted rather than
    corrected**, for the reason `CLAUDE.md` gives for deleting the open-item
    count: a set restated in prose drifts the moment the set changes, and there
    is a maintained copy — the dataclass itself.
    `tests/test_wire_g13_x87d.py` owns the pin and already had it:
    ``test_the_five_flags_default_to_no_answer_and_the_siblings_default_to_false``
    asserts both defaults against ``ControlCapability`` itself, so the claim above
    cannot rot without that test going red.

    Collapsing ``None`` into ``False`` would be a regression, not a
    simplification. The five flags added for this row exist only from daemon
    2.36.0, while each feature behind them shipped between 1.11.0 and 2.16.0 and
    the published pairing floor is lower still — so a two-state answer would hide
    five working features on every daemon in that range.

    ``capabilities`` is duck-typed rather than annotated as ``Capabilities``
    because this module is Qt-free *and* import-free by design: every page,
    dialog and service reaches it, and a concrete import here would make the API
    model a dependency of the wording layer.
    """
    flag = DAEMON_FEATURE_CAPABILITY_FLAGS.get(feature_id)
    if flag is None:
        return None
    control = getattr(capabilities, "control", None)
    if control is None:
        return None
    # `getattr(..., None)` and not `False`: a client dataclass that has not been
    # extended with a newer daemon's flag must read as "did not say", exactly
    # like an older daemon, rather than as a denial.
    value = getattr(control, flag, None)
    return value if isinstance(value, bool) else None
