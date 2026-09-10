"""Regression tests for the `UDOC-*` user-facing-truthfulness fixes.

Three defects, one theme: the application told the user something that was not
true, or true and unactionable. Each test below asserts at the **call site** —
the string a user actually sees — because in all three cases the underlying
rule was already correct somewhere and only the reachable copy was wrong. A
unit test on the helper would have passed against every one of these bugs.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import (
    BoardInfo,
    Capabilities,
    ControlCapability,
    HardwareDiagnosticsResult,
    HwmonChipInfo,
    HwmonDiagnostics,
    KernelModuleInfo,
    ThermalSafetyInfo,
    parse_capabilities,
)
from control_ofc.services.daemon_features import (
    DAEMON_FEATURE_CAPABILITY_FLAGS,
    DAEMON_FEATURE_LABELS,
    DAEMON_FEATURE_MINIMUMS,
    requires_daemon,
    unsupported_feature_message,
)
from control_ofc.services.pump_protection import (
    daemon_protects_pumps,
    pump_identify_warning,
)
from control_ofc.services.system_state_view import build_issue_cards
from control_ofc.ui.hwmon_guidance import dual_chip_warning_html
from control_ofc.ui.widgets.readiness_report import detect_readiness_problems

VENDOR_GB = "Gigabyte Technology Co., Ltd."

# Advice that DEC-326 measured as futile on a board whose secondary answers
# 0x8883. Each phrase is checked case-insensitively against the whole rendered
# surface, not against one string, because the defect was that two cards
# disagreed — reading either one alone would have missed it.
RETRACTED_REMEDIES = (
    "options it87 mmio=on",
    "mmio=on",
)


def _master_diag() -> HardwareDiagnosticsResult:
    """X870E AORUS MASTER with only the primary chip enumerated.

    The measured DEC-326 board: the daemon still publishes both expected chips
    (`chip_db.rs` `DualChipEntry` for this board), the secondary never appears,
    so the dual-chip alert fires. Kernel module is the out-of-tree it87 build,
    i.e. the user has already done the thing the old copy told them to do.
    """
    return HardwareDiagnosticsResult(
        hwmon=HwmonDiagnostics(
            chips_detected=[HwmonChipInfo(chip_name="it8696", header_count=5)],
            total_headers=5,
            writable_headers=5,
        ),
        board=BoardInfo(vendor=VENDOR_GB, name="X870E AORUS MASTER"),
        kernel_modules=[KernelModuleInfo(name="it87", loaded=True, in_mainline=False)],
        thermal_safety=ThermalSafetyInfo(state="normal", cpu_sensor_found=True),
        expected_chips=["it8696", "it87952"],
    )


class TestDualChipAlertTruthfulness:
    """`UDOC-h` — the alert must not promise a fix the board cannot have."""

    def test_alert_gives_the_discriminator_not_a_universal_remedy(self):
        html = dual_chip_warning_html("X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"])
        assert html is not None
        lowered = html.lower()

        # The discriminator is the whole point: the user must be able to find
        # out WHICH fault they have before changing anything.
        assert "dmesg" in lowered
        assert "0x8883" in lowered
        assert "0xffff" in lowered

        # And the unfixable case must be named as unfixable.
        assert "power" in lowered and ("wall" in lowered or "power cut" in lowered), (
            "DEC-332: the 0x8883 case is RECOVERABLE, so this copy must give "
            "the remedy rather than tell the user to give up. It must also say "
            "the machine has to be powered down at the wall — a reboot does "
            "not clear the latch, and a user who reboots, sees no change and "
            "concludes the chip is dead is exactly the failure this asserts "
            "against. Got: " + repr(lowered)
        )

        # The retracted claim must be gone. This is the specific false sentence:
        # it promised control of the secondary chip as a property of current
        # builds, which is what sent 0x8883 owners round the loop.
        assert "both enumerate" not in lowered

    def test_alert_does_not_prescribe_mmio_as_a_remedy(self):
        """`mmio` may be MENTIONED, but only to say setting it changes nothing."""
        html = dual_chip_warning_html("X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"])
        assert html is not None
        lowered = html.lower()
        if "mmio" in lowered:
            assert "already the driver default" in lowered, (
                "mmio may only appear as a thing NOT to try; it is already the "
                "driver default, so 'enable it' names a state already in effect"
            )

    def test_no_warning_when_every_expected_chip_enumerated(self):
        """The opposite branch — a stuck predicate would pass the tests above."""
        assert (
            dual_chip_warning_html(
                "X870E AORUS ELITE", ["it8696", "it87952"], ["it8696", "it87952"]
            )
            is None
        )
        assert dual_chip_warning_html("Some Board", [], []) is None

    def test_rendered_system_state_cards_do_not_contradict_each_other(self):
        """The defect was a CONTRADICTION, so this asserts across the surface.

        `build_issue_cards` is what the System State page renders. It emits the
        dual-chip problem card *and* the Gigabyte/IT8696E vendor advisory for
        this board. Before the fix one said "no local fix" and the other handed
        the user a numbered fix — on the same screen. Asserting on either card
        alone cannot see that, which is why this reads the whole rendered set.
        """
        cards = build_issue_cards(_master_diag())
        keys = {c.key for c in cards}
        assert "dual_chip" in keys, "precondition: the dual-chip card must render"
        assert any(k.startswith("vendor_quirk") or k == "vendor_quirk" for k in keys), (
            "precondition: the corrected vendor advisory must render alongside it "
            "— without both cards present this test cannot detect a contradiction"
        )

        def text_of(card) -> str:
            return " ".join((card.title, card.description, card.detail or "")).lower()

        surface = " ".join(text_of(c) for c in cards)
        # The honest verdict reaches the screen at all.
        assert "power" in surface and ("wall" in surface or "power cut" in surface), (
            "DEC-332: the 0x8883 case is RECOVERABLE, so this copy must give "
            "the remedy rather than tell the user to give up. It must also say "
            "the machine has to be powered down at the wall — a reboot does "
            "not clear the latch, and a user who reboots, sees no change and "
            "concludes the chip is dead is exactly the failure this asserts "
            "against. Got: " + repr(surface)
        )

        # ...and NO SINGLE CARD prescribes a remedy DEC-326 measured as futile.
        #
        # This is asserted per card, not against the joined surface, and the
        # difference is the whole test: the vendor advisory legitimately
        # contains "already the driver default", so a joined-surface search
        # finds that phrase no matter which card the retracted remedy came
        # from. Measured — the earlier joined version of this assertion PASSED
        # with the fix deleted. Same family as CLAUDE.md's "'something changed'
        # is not evidence a rule fired".
        for card in cards:
            body = text_of(card)
            for phrase in RETRACTED_REMEDIES:
                if phrase in body:
                    assert "already the driver default" in body, (
                        f"card {card.key!r} prescribes {phrase!r} without saying "
                        f"it is already in effect, while the report elsewhere "
                        f"states there is no local fix — the UDOC-h contradiction"
                    )

    def test_readiness_fix_line_points_at_the_discriminator(self):
        problems = {p["key"]: p for p in detect_readiness_problems(_master_diag())}
        assert "dual_chip" in problems
        fix = problems["dual_chip"]["fix"].lower()
        assert "dmesg" in fix, "the one-line fix must tell the user how to tell the cases apart"
        assert "power" in fix and ("wall" in fix or "power cut" in fix), (
            "DEC-332: the 0x8883 case is RECOVERABLE, so this copy must give "
            "the remedy rather than tell the user to give up. It must also say "
            "the machine has to be powered down at the wall — a reboot does "
            "not clear the latch, and a user who reboots, sees no change and "
            "concludes the chip is dead is exactly the failure this asserts "
            "against. Got: " + repr(fix)
        )
        assert "mmio=on" not in fix


class TestPumpIdentifyPromiseIsCapabilityGated:
    """`UDOC-i` — the wizard may only promise what the connected daemon does."""

    @staticmethod
    def _caps(*, header_roles: bool) -> Capabilities:
        return Capabilities(control=ControlCapability(header_roles=header_roles))

    @pytest.mark.parametrize("header_roles", [True, False])
    def test_promise_tracks_the_capability_not_a_constant(self, header_roles):
        """Asserted as a RELATIONSHIP (DEC-324 rule 1).

        A literal expectation here would pass against a hardcoded string, which
        is precisely the bug: the old label said "never stopped" unconditionally.
        """
        caps = self._caps(header_roles=header_roles)
        text = pump_identify_warning(caps)
        promises_protection = "never stopped" in text
        assert promises_protection == daemon_protects_pumps(caps)

    def test_unprotected_branch_says_the_pump_IS_stopped_and_how_to_fix_it(self):
        text = pump_identify_warning(self._caps(header_roles=False))
        assert "including a pump" in text
        # Actionable, per UDOC-l's rule applied to the same change.
        assert requires_daemon("pump_protection") in text
        assert "2.28.0" in text

    def test_missing_capabilities_object_is_not_read_as_protected(self):
        """`None` must fail safe — towards the weaker promise, never the stronger."""
        assert daemon_protects_pumps(None) is False
        assert "never stopped" not in pump_identify_warning(None)

    def test_wizard_intro_renders_the_gated_bullet(self, qtbot):
        """The CALL SITE. The rule being right is not the same as it being used.

        Uses the real page and the real `initializePage`, because the original
        defect was a label frozen in `__init__` that never consulted the
        capability at all.
        """
        from control_ofc.services.app_state import AppState
        from control_ofc.ui.widgets.fan_wizard import IntroPage

        for header_roles in (True, False):
            state = AppState()
            state.capabilities = self._caps(header_roles=header_roles)
            page = IntroPage(state)
            qtbot.addWidget(page)
            page.initializePage()
            rendered = page._warning_label.text()
            assert rendered, "the label must be populated by initializePage"
            assert ("never stopped" in rendered) == header_roles
            # `<br>` not `\n`: the label is rich text (it carries <b> tags), so
            # newlines would collapse and hide this bullet in a run-on paragraph.
            assert "<br>" in rendered
            assert "\n" not in rendered

    def test_intro_label_re_derives_on_reshow(self, qtbot):
        """A reconnect to a different daemon must not leave a stale promise."""
        from control_ofc.services.app_state import AppState
        from control_ofc.ui.widgets.fan_wizard import IntroPage

        state = AppState()
        state.capabilities = self._caps(header_roles=True)
        page = IntroPage(state)
        qtbot.addWidget(page)
        page.initializePage()
        assert "never stopped" in page._warning_label.text()

        state.capabilities = self._caps(header_roles=False)
        page.initializePage()
        assert "never stopped" not in page._warning_label.text()


class TestUnsupportedFeatureMessagesAreActionable:
    """`UDOC-l` — "does not support X" must name the version that does."""

    def test_every_feature_id_has_both_a_version_and_a_label(self):
        assert set(DAEMON_FEATURE_MINIMUMS) == set(DAEMON_FEATURE_LABELS)
        for fid, version in DAEMON_FEATURE_MINIMUMS.items():
            assert version and version[0].isdigit(), f"{fid} has no usable version"
            assert DAEMON_FEATURE_LABELS[fid].strip(), f"{fid} has no label"

    @pytest.mark.parametrize("feature_id", sorted(DAEMON_FEATURE_MINIMUMS))
    def test_message_names_the_version_and_a_way_to_check(self, feature_id):
        msg = unsupported_feature_message(feature_id)
        assert DAEMON_FEATURE_MINIMUMS[feature_id] in msg
        assert "control-ofc-daemon" in msg
        assert "or newer" in msg
        # The second axis: a user must be able to act. Naming the required
        # version without saying where to read the running one is half a fix.
        assert "Overview page" in msg

    def test_unknown_feature_id_raises_rather_than_rendering_a_blank(self):
        """A silent fallback would print "requires control-ofc-daemon  or newer"."""
        with pytest.raises(KeyError):
            unsupported_feature_message("no_such_feature")
        with pytest.raises(KeyError):
            requires_daemon("no_such_feature")

    def test_every_feature_id_used_in_src_resolves(self):
        """Every literal passed to the registry must be a real key.

        The registry raises `KeyError` on an unknown id — correct, but a typo
        would then surface as an exception inside a worker's `except DaemonError`
        handler, where the user gets no message at all rather than a wrong one.
        Nothing else catches it: `ruff` does not check string literals, and the
        parametrised tests above iterate the registry rather than the call sites,
        so they are green no matter what the call sites pass.

        This is also what makes the module docstring's claim true rather than
        merely stated — it named a test file that did not exist.
        """
        import pathlib
        import re

        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "control_ofc"
        call = re.compile(
            r"(?:unsupported_feature_message|requires_daemon)\(\s*[\"']([^\"']+)[\"']"
        )
        seen: list[tuple[str, str]] = []
        for path in src.rglob("*.py"):
            if path.name == "daemon_features.py":
                continue  # defines the registry; its own docstring is prose
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for m in call.finditer(line):
                    seen.append((f"{path.relative_to(src)}:{n}", m.group(1)))

        assert seen, "precondition: the sweep must actually find call sites"
        unknown = [f"{loc} -> {fid!r}" for loc, fid in seen if fid not in DAEMON_FEATURE_MINIMUMS]
        assert not unknown, (
            "unknown feature id(s) — these raise KeyError at runtime:\n" + "\n".join(unknown)
        )

    def test_every_daemon_supports_id_has_a_capability_flag(self):
        """The THIRD entry point into the registry, and the only silent one.

        The sweep above covers `unsupported_feature_message` and `requires_daemon`
        because those **raise** on an unknown id. `daemon_supports` does not — it
        returns `None`, which is falsy, so an unregistered id makes every
        `if daemon_supports(...)` call site dead on every daemon forever, with no
        exception, no log line and no failing test.

        That is not hypothetical: it is what DEC-334 shipped. The
        `pwm_behaviour_characterization` token was gated on an id that was in no
        registry, so the behaviour diagnostic could not be selected in a
        validation session on **any** daemon, including one advertising the flag.
        Fixed for 2.63.1; this test is what stops the next one.

        Note the registry checked here is `DAEMON_FEATURE_CAPABILITY_FLAGS`, not
        `DAEMON_FEATURE_MINIMUMS` — `daemon_supports` resolves through the former
        and returns `None` for anything absent from it, whatever the latter holds.
        """
        import pathlib
        import re

        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "control_ofc"
        call = re.compile(r"daemon_supports\(\s*[\"']([^\"']+)[\"']")
        seen: list[tuple[str, str]] = []
        for path in src.rglob("*.py"):
            if path.name == "daemon_features.py":
                continue  # defines the registry; its own docstring is prose
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for m in call.finditer(line):
                    seen.append((f"{path.relative_to(src)}:{n}", m.group(1)))

        assert seen, "precondition: the sweep must actually find call sites"
        unflagged = [
            f"{loc} -> {fid!r}" for loc, fid in seen if fid not in DAEMON_FEATURE_CAPABILITY_FLAGS
        ]
        assert not unflagged, (
            "feature id(s) gated through `daemon_supports` with no entry in "
            "DAEMON_FEATURE_CAPABILITY_FLAGS — these return None forever, so the "
            "gated feature is unreachable on every daemon:\n" + "\n".join(unflagged)
        )

    def test_no_capability_flag_is_gated_by_a_raw_getattr_chain(self):
        """`P8-ag`: one flag, one gating shape.

        The sweep above proves every `daemon_supports` id resolves. This is its
        converse, and it is the half DEC-334 actually shipped on: a flag gated
        through the registry in one place and by a raw `getattr(..., "flag",
        False)` chain in another. Both shapes work, which is precisely the
        problem — the working one hides the broken one, and a rename
        desynchronises them silently, via the shape that fails without an
        exception, a log line or a failing test.

        Matched on the FLAG NAME as a `getattr` attribute argument, so
        `getattr(caps, "control", None)` — a navigation step, not a gate — does
        not trip it.
        """
        import pathlib
        import re

        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "control_ofc"
        flags = set(DAEMON_FEATURE_CAPABILITY_FLAGS.values())
        assert flags, "precondition: the registry must not be empty"

        def raw_gates(text: str) -> list[tuple[int, str]]:
            """Every `getattr(...)` call whose arguments name a capability flag.

            Paren-matched rather than regex-bounded, and run over the WHOLE file
            rather than line by line. A `[^)]*` pattern cannot cross the inner
            call of `getattr(getattr(caps, "control", None), "flag", False)` —
            which is precisely the shape this package removed — and a per-line
            scan cannot see a call `ruff` wrapped across lines at the 100-column
            limit. Both would be false negatives in a guard whose entire job is
            catching the next one.
            """
            hits: list[tuple[int, str]] = []
            for m in re.finditer(r"\bgetattr\(", text):
                i, depth = m.end(), 1
                while i < len(text) and depth:
                    depth += (text[i] == "(") - (text[i] == ")")
                    i += 1
                args = text[m.end() : i - 1]
                for flag in flags:
                    if f'"{flag}"' in args or f"'{flag}'" in args:
                        hits.append((text.count("\n", 0, m.start()) + 1, flag))
            return hits

        # The guard's own regression case: the nested form MUST be caught.
        sample = next(iter(sorted(flags)))
        nested = f'getattr(getattr(caps, "control", None), "{sample}", False)'
        assert raw_gates(nested), (
            "the sweep cannot see a nested getattr chain — the exact shape "
            "`P8-ag` was about, so it would have missed the next one"
        )
        assert not raw_gates(f'daemon_supports("{sample}", caps) is True'), (
            "the sweep must not flag the correct shape"
        )

        # **The allowlist is gone, and that is the assertion.** `G29` fixed the
        # sites inside `P8-ag`'s stated scope and enumerated the rest as a
        # `known_backlog` of five, recorded as `P8-by`; those five were converted
        # by this change, so the ratchet has finished tightening and this is now
        # a CLEAN SWEEP over the whole shipped tree. The allowlist and the
        # "an entry that stopped matching is an allowlist that stopped
        # protecting anything" half that guarded it are deleted rather than left
        # empty: an empty allowlist makes that half assert nothing, and a test
        # that passes by asserting nothing is the trap `CLAUDE.md § Hard-won
        # lessons` records against. Do not reintroduce it — a flag that cannot
        # go through `daemon_supports` needs a registry entry, not an exemption.
        offenders: list[str] = []
        for path in src.rglob("*.py"):
            if path.name == "daemon_features.py":
                continue  # the registry itself resolves flags by name, by design
            rel = str(path.relative_to(src))
            for n, flag in raw_gates(path.read_text(encoding="utf-8")):
                offenders.append(f"{rel}:{n} -> {flag!r}")

        assert not offenders, (
            "capability flag(s) read by a raw `getattr` chain instead of "
            "`daemon_supports` — two gating shapes for one flag is what let the "
            "DEC-334 defect ship:\n" + "\n".join(offenders)
        )

    def test_call_sites_no_longer_dead_end(self):
        """The CALL SITES, swept from source — the helper being right proves nothing.

        Greps the shipped tree for the dead-end phrasing this change removed. A
        new copy written by hand is the failure mode the registry exists to
        prevent, and it would not be caught by any test of the registry itself.
        """
        import pathlib
        import re

        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "control_ofc"
        offenders: list[str] = []
        # "does not support/provide <something>" followed by a closing quote —
        # i.e. the sentence ends without naming a version.
        pattern = re.compile(r'"This daemon(?: version)? does not (?:support|provide) [^"]*\."')
        for path in src.rglob("*.py"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(src)}:{n}: {line.strip()}")
        assert not offenders, (
            "unsupported-feature strings must come from "
            "services.daemon_features so they name the required version:\n" + "\n".join(offenders)
        )


class TestEveryConvertedGateTracksItsWireFlag:
    """`P8-by`: the five gates the sweep above used to allowlist.

    The sweep proves no raw ``getattr`` chain remains. It cannot prove the
    replacement gates on the RIGHT flag — ``daemon_supports`` answers ``None``
    for an unregistered id, ``None`` is falsy, and a gate handed the wrong id is
    therefore dead on every daemon with no exception, no log line and no failing
    test. That is the DEC-334 defect exactly, and converting five call sites is
    five fresh chances to reintroduce it.

    **Asserted against the WIRE FIELD, never against ``daemon_supports``**
    (DEC-334's second rule). Comparing a gate to ``daemon_supports(id, caps)``
    would be satisfied by the defect itself: delete the registry entry and both
    sides go falsy together. ``ControlCapability.<flag>`` is the one term that
    stays true independently of the registry, so it is the right-hand side here.

    The four page/widget gates are bound to a stub ``self`` rather than a
    constructed page: each reads ``self._state`` and nothing else, and the
    production function object is what runs. Constructing four QWidgets would
    test Qt, not the gate.
    """

    class _Stub:
        """The only attribute the five gates touch."""

        def __init__(self, state):
            self._state = state

    class _State:
        def __init__(self, capabilities):
            self.capabilities = capabilities

    class _NoControlBlock:
        """A capabilities object from a daemon that sent no ``control`` block.

        Not reachable through ``Capabilities`` — its ``control`` has a
        ``default_factory`` — but it is the shape ``daemon_supports`` documents
        as "did not say", and the branch a converted gate must still read as
        False rather than as permission.
        """

    @staticmethod
    def _gates():
        """(name, wire flag, callable taking a capabilities object) for all five."""
        from control_ofc.ui.pages.controls_page import ControlsPage
        from control_ofc.ui.pages.settings_page import SettingsPage
        from control_ofc.ui.pages.system_state_page import SystemStatePage
        from control_ofc.ui.widgets.fan_wizard import FanConfigWizard

        def bound(fn):
            def call(caps):
                stub = TestEveryConvertedGateTracksItsWireFlag._Stub(
                    TestEveryConvertedGateTracksItsWireFlag._State(caps)
                )
                return fn(stub)

            return call

        return [
            ("pump_protection.daemon_protects_pumps", "header_roles", daemon_protects_pumps),
            (
                "ControlsPage._supports_header_roles",
                "header_roles",
                bound(ControlsPage._supports_header_roles),
            ),
            (
                "FanConfigWizard.supports_cooling_step",
                "header_roles",
                bound(FanConfigWizard.supports_cooling_step),
            ),
            (
                "SettingsPage._daemon_supports_dir_removal",
                "profile_search_dir_remove",
                bound(SettingsPage._daemon_supports_dir_removal),
            ),
            (
                "SystemStatePage._supports_characterization",
                "pwm_characterization",
                bound(SystemStatePage._supports_characterization),
            ),
        ]

    def test_the_sweep_covers_every_gate_this_change_converted(self):
        """Precondition: five gates were converted, so five must be exercised.

        Without this the parametrisation could silently shrink to one and every
        assertion below would still pass — "assert you did not skip every case".
        """
        assert len(self._gates()) == 5

    @pytest.mark.parametrize("advertised", [True, False])
    def test_a_gate_opens_exactly_when_its_wire_flag_is_true(self, advertised):
        for name, flag, gate in self._gates():
            caps = Capabilities(control=ControlCapability(**{flag: advertised}))
            # The RELATIONSHIP: the gate must equal the wire field, not a
            # literal and not the registry's answer about it.
            assert gate(caps) is advertised, (
                f"{name} returned {gate(caps)!r} while the daemon advertised "
                f"control.{flag}={advertised!r} — a gate that ignores its own "
                f"wire flag is either dead on every daemon or open on all of them"
            )

    def test_no_answer_is_read_as_a_denial_not_as_permission(self):
        """``None`` capabilities and a missing ``control`` block both fail safe."""
        for name, _flag, gate in self._gates():
            assert gate(None) is False, f"{name} opened with no capabilities at all"
            assert gate(self._NoControlBlock()) is False, (
                f"{name} opened against a daemon that sent no control block"
            )

    def test_a_non_bool_wire_value_closes_the_gate_rather_than_opening_it(self):
        """The conversion NARROWS, and the narrowing is on the safe side.

        ``_filter_fields`` filters unknown keys and coerces identity fields to
        ``str``; it does not coerce booleans, and a dataclass does not enforce its
        annotations at runtime — so a malformed ``control`` block really can put a
        non-bool in one of these fields (measured: ``parse_capabilities`` keeps
        ``"yes"`` verbatim). The old ``bool(getattr(...))`` read that as **True**.
        ``daemon_supports`` returns ``None`` for a non-bool, so ``is True`` reads it
        as False.

        That direction is the one that matters. ``daemon_protects_pumps`` gates the
        Fan Wizard's promise that a pump is *never stopped* — opening it wrongly
        tells the user a guarantee exists when the daemon may drive the pump to 0,
        which is the DEC-311 lie the capability gate exists to prevent. Closing it
        wrongly only downgrades the copy to the weaker, true statement.
        """
        for name, flag, gate in self._gates():
            caps = parse_capabilities({"control": {flag: "yes"}})
            assert getattr(caps.control, flag) == "yes", (
                f"precondition: parsing must really keep a non-bool in control.{flag}, "
                f"or this test asserts nothing"
            )
            assert gate(caps) is False, (
                f"{name} opened on a non-bool control.{flag} — the old getattr shape "
                f"did exactly that, and for the pump gate it promises a guarantee the "
                f"daemon has not made"
            )

    def test_each_gate_resolves_through_a_registered_id(self):
        """The converted ids must be in the registry that ``daemon_supports`` reads.

        The sibling sweep ``test_every_daemon_supports_id_has_a_capability_flag``
        already scans source for this. Named here too because that sweep proves
        the id is registered and this proves it is registered to the flag the
        gate is actually about — a swap between two registered ids passes the
        sweep and fails here.
        """
        for name, flag, _gate in self._gates():
            matches = [k for k, v in DAEMON_FEATURE_CAPABILITY_FLAGS.items() if v == flag]
            assert matches, f"{name} gates on control.{flag}, which no registry id maps to"
