"""DEC-442 (`BRD-a`): the it87 v2.0 chip-name suffix, GUI side.

it87 v2.0 names Gigabyte chips ``<chip>_<siv>`` (``it8696_a008090a``). A current
daemon strips the suffix before it builds any id, so what the GUI must do is
narrow, and each test below pins one piece of it:

* the GUI's canonicaliser agrees with the daemon's, on the shared oracle;
* ids a pre-DEC-442 daemon published, still sitting in this GUI's settings and
  local profile copies, are brought to the id the daemon publishes now;
* ``/etc/sensors.d`` blocks are matched against the sysfs spelling, never the
  canonical one;
* the "a rebuild changes your ids" caution is gated on the daemon's
  ``control.canonical_chip_names``, set through ``AppState.set_capabilities``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from control_ofc.api.models import (
    BoardInfo,
    Capabilities,
    ControlCapability,
    HwmonHeader,
)
from control_ofc.knowledge.chip_name import (
    canonical_chip_name,
    canonical_hwmon_id,
    is_suffixed_hwmon_id,
)
from control_ofc.services.app_settings_service import AppSettings
from control_ofc.services.app_state import AppState
from control_ofc.services.profile_service import Profile
from control_ofc.ui import hwmon_guidance

FIXTURE = Path(__file__).parent / "fixtures" / "chip_name_canonical.json"
_ORACLE = json.loads(FIXTURE.read_text())

_DAEMON_FIXTURE = (
    Path(__file__).parents[2]
    / "control-ofc-daemon"
    / "daemon"
    / "tests"
    / "fixtures"
    / "chip_name_canonical.json"
)

SUFFIXED = "hwmon:it8696_a008090a:it87.2624:pwm5:pwm5"
CANONICAL = "hwmon:it8696:it87.2624:pwm5:pwm5"


# ── The shared oracle ────────────────────────────────────────────────────


@pytest.mark.parametrize("case", _ORACLE["chip_names"], ids=lambda c: c["name"])
def test_chip_names_match_the_cross_stack_oracle(case):
    assert canonical_chip_name(case["raw"]) == case["canonical"]


@pytest.mark.parametrize("case", _ORACLE["ids"], ids=lambda c: c["name"])
def test_ids_match_the_cross_stack_oracle(case):
    assert canonical_hwmon_id(case["raw"]) == case["canonical"]
    assert is_suffixed_hwmon_id(case["raw"]) == (case["raw"] != case["canonical"])


def test_the_oracle_exercises_the_strip_itself():
    """Presence before absence: an oracle of names left alone proves nothing."""
    stripped = [c for c in _ORACLE["chip_names"] if c["raw"] != c["canonical"]]
    assert len(stripped) >= 2


@pytest.mark.skipif(
    not _DAEMON_FIXTURE.exists(), reason="daemon repo not checked out alongside the GUI"
)
def test_chip_name_fixture_copies_are_byte_identical():
    assert FIXTURE.read_bytes() == _DAEMON_FIXTURE.read_bytes(), (
        "chip_name_canonical.json drifted between the GUI and daemon copies"
    )


# ── Settings saved under the suffixed spelling (Q5-a) ────────────────────


def test_every_id_keyed_setting_is_canonicalised_on_load():
    sensor = "hwmon:it8696_a008090a:it87.2624:temp1"
    settings = AppSettings.from_dict(
        {
            "fan_aliases": {SUFFIXED: "Pump"},
            "hidden_chart_series": [f"fan:{SUFFIXED}:rpm", f"sensor:{sensor}"],
            "series_colors": {f"sensor:{sensor}": "#112233"},
            "diagnostics_hidden_sensor_ids": [sensor],
            "sensor_class_overrides": {sensor: "coolant"},
            "hardware_notes": {SUFFIXED: {"notes": "the pump header"}},
        }
    )
    canonical_sensor = "hwmon:it8696:it87.2624:temp1"
    assert settings.fan_aliases == {CANONICAL: "Pump"}
    assert settings.hidden_chart_series == [
        f"fan:{CANONICAL}:rpm",
        f"sensor:{canonical_sensor}",
    ]
    assert list(settings.series_colors) == [f"sensor:{canonical_sensor}"]
    assert settings.diagnostics_hidden_sensor_ids == [canonical_sensor]
    assert settings.sensor_class_overrides == {canonical_sensor: "coolant"}
    assert list(settings.hardware_notes) == [CANONICAL]


@pytest.mark.parametrize("order", ["bare_first", "suffixed_first"])
def test_where_both_spellings_hold_a_fan_name_the_suffixed_one_wins(order):
    """The suffixed entry can only have been written after the rebuild, so it is
    the user's latest choice — whichever order the file lists them in."""
    items = [(CANONICAL, "Before rebuild"), (SUFFIXED, "After rebuild")]
    if order == "suffixed_first":
        items.reverse()
    settings = AppSettings.from_dict({"fan_aliases": dict(items)})
    assert settings.fan_aliases == {CANONICAL: "After rebuild"}


def test_a_list_holding_both_spellings_keeps_one_entry():
    settings = AppSettings.from_dict({"diagnostics_hidden_sensor_ids": [CANONICAL, SUFFIXED]})
    assert settings.diagnostics_hidden_sensor_ids == [CANONICAL]


# ── Profiles saved under the suffixed spelling ───────────────────────────


def _profile(members, sensor_id="hwmon:it8696_a008090a:it87.2624:temp1"):
    return Profile.from_dict(
        {
            "id": "p",
            "name": "P",
            "version": 7,
            "controls": [
                {"id": "c", "name": "C", "curve_id": "k", "minimum_pct": 30, "members": members}
            ],
            "curves": [{"id": "k", "name": "K", "type": "graph", "sensor_id": sensor_id}],
        }
    )


def test_a_profile_member_saved_suffixed_survives_the_runtime_sanitizer():
    """The failure this prevents: DEC-102's sanitizer deletes every hwmon member
    whose id is not live — and publishes the loss. Driven through it, against the
    header set a current daemon reports."""
    profile = _profile([{"source": "hwmon", "member_id": SUFFIXED, "member_label": "Rear"}])
    assert profile.curves[0].sensor_id == "hwmon:it8696:it87.2624:temp1"

    dropped = profile.sanitize_hwmon_members({CANONICAL}, {CANONICAL})
    assert dropped == 0
    assert [m.member_id for m in profile.controls[0].members] == [CANONICAL]


@pytest.mark.parametrize(
    ("bare_label", "suffixed_label", "kept"),
    [
        ("CPU_FAN", "Rear", "CPU_FAN"),  # the floored one wins
        ("Rear", "AIO Pump", "AIO Pump"),
        ("Rear", "Front", "Front"),  # a tie goes to the suffixed one
    ],
)
def test_two_spellings_of_one_member_keep_the_floored_one_else_the_suffixed_one(
    bare_label, suffixed_label, kept
):
    profile = _profile(
        [
            {"source": "hwmon", "member_id": CANONICAL, "member_label": bare_label},
            {"source": "hwmon", "member_id": SUFFIXED, "member_label": suffixed_label},
        ]
    )
    members = profile.controls[0].members
    assert [(m.member_id, m.member_label) for m in members] == [(CANONICAL, kept)]


def test_against_an_older_daemon_publishing_suffixed_ids_the_sweep_keeps_the_member():
    """The skew the security review found: a daemon older than DEC-442 on an
    it87 v2.0 driver still publishes the suffixed id, while this GUI holds the
    canonical one. The sweep must see the same header — before the fix it
    deleted the member, a pump's included, and published the loss."""
    profile = _profile([{"source": "hwmon", "member_id": SUFFIXED, "member_label": "Pump"}])
    assert profile.controls[0].members[0].member_id == CANONICAL  # precondition

    dropped = profile.sanitize_hwmon_members({SUFFIXED}, {SUFFIXED})
    assert dropped == 0
    assert [m.member_label for m in profile.controls[0].members] == ["Pump"]

    # The opposite branch: the same header present but read-only, spelled
    # suffixed, is still dropped — so the canonical compare reached both sets.
    assert profile.sanitize_hwmon_members(set(), {SUFFIXED}) == 1


def test_identical_spellings_are_not_collapsed():
    """Only DIFFERENT spellings collapse; the rename is not a licence to dedupe."""
    for member_id in (CANONICAL, SUFFIXED):
        profile = _profile(
            [
                {"source": "hwmon", "member_id": member_id, "member_label": "A"},
                {"source": "hwmon", "member_id": member_id, "member_label": "B"},
            ]
        )
        assert [m.member_label for m in profile.controls[0].members] == ["A", "B"]


def test_load_rewrites_a_suffixed_profile_as_a_rename_not_a_dec102_drop(
    tmp_path, monkeypatch, caplog
):
    """The local copy is re-saved canonical, and the log says why — an id
    rewrite is not a DEC-102 member drop."""
    import logging

    from control_ofc.paths import profiles_dir
    from control_ofc.services.profile_service import ProfileService

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    d = profiles_dir()
    d.mkdir(parents=True, exist_ok=True)
    fp = d / "p.json"
    fp.write_text(
        json.dumps(
            {
                "id": "p",
                "name": "P",
                "version": 7,
                "controls": [
                    {
                        "id": "c",
                        "name": "C",
                        "curve_id": "k",
                        "minimum_pct": 30,
                        "members": [
                            {"source": "hwmon", "member_id": SUFFIXED, "member_label": "Rear"}
                        ],
                    }
                ],
                "curves": [{"id": "k", "name": "K", "type": "graph", "sensor_id": "cpu"}],
            }
        )
    )

    with caplog.at_level(logging.INFO):
        assert ProfileService().load() == []

    on_disk = json.loads(fp.read_text())
    assert on_disk["controls"][0]["members"][0]["member_id"] == CANONICAL
    messages = [r.getMessage() for r in caplog.records]
    assert any("canonical hwmon chip names" in m for m in messages), messages
    assert not any("DEC-102 member sanitization" in m for m in messages), messages


# ── /etc/sensors.d matches the sysfs spelling (Q3-a) ─────────────────────


def _state_with_header(sysfs_chip_name: str) -> AppState:
    state = AppState()
    state.board_info = BoardInfo(vendor="Some Vendor", name="Some Board")
    state.set_hwmon_headers(
        [
            HwmonHeader(
                id="hwmon:it8696:it87.2624:pwm5:pwm5",
                label="pwm5",  # the daemon's synthesised placeholder
                chip_name="it8696",
                sysfs_chip_name=sysfs_chip_name,
                pwm_index=5,
            )
        ]
    )
    return state


@pytest.fixture()
def sensors_conf(tmp_path, monkeypatch):
    from control_ofc.knowledge import hwmon_label_resolver as r

    def write(text: str) -> None:
        conf = tmp_path / "board.conf"
        conf.write_text(text)
        monkeypatch.setattr(r, "LIBSENSORS_CONFIG_PATHS", [str(conf)])
        r.clear_libsensors_cache()

    return write


def test_a_sensors_d_block_written_for_the_suffixed_name_labels_the_header(sensors_conf):
    """Upstream's per-board configs are written against the suffixed name; the
    GUI must match them the way `sensors` does — through AppState, the call site
    that feeds the member label and hence the pump floor."""
    sensors_conf('chip "it8696_a008090a-*"\n    label fan5 "SYS_FAN5_PUMP"\n')
    state = _state_with_header("it8696_a008090a")
    assert state.fan_fallback_name("hwmon:it8696:it87.2624:pwm5:pwm5") == "SYS_FAN5_PUMP"


def test_a_sensors_d_block_for_another_board_or_the_bare_name_does_not_match(sensors_conf):
    """Matching on the canonical name would apply another board's labels."""
    sensors_conf(
        'chip "it8696_b0b0b0b0-*"\n    label fan5 "OTHER_BOARD"\n'
        'chip "it8696-*"\n    label fan5 "PRE_RENAME_CONFIG"\n'
    )
    state = _state_with_header("it8696_a008090a")
    assert state.fan_fallback_name("hwmon:it8696:it87.2624:pwm5:pwm5") == "pwm5"


def test_an_older_daemon_without_the_field_matches_on_chip_name(sensors_conf):
    """An older daemon omits `sysfs_chip_name`; its `chip_name` IS the sysfs name."""
    sensors_conf('chip "it8696-*"\n    label fan5 "SYS_FAN5"\n')
    state = _state_with_header("")
    assert state.fan_fallback_name("hwmon:it8696:it87.2624:pwm5:pwm5") == "SYS_FAN5"


# ── The caution follows the connected daemon ─────────────────────────────


def _connect(canonical: bool) -> None:
    AppState().set_capabilities(
        Capabilities(control=ControlCapability(canonical_chip_names=canonical))
    )


def _quirk_with_note():
    for q in hwmon_guidance.VENDOR_QUIRKS_DB:
        if hwmon_guidance._IT87_V2_RENAME_NOTE in q.details:
            return q
    raise AssertionError("precondition: some quirk carries the rename caution")


@pytest.mark.parametrize("canonical", [True, False])
def test_the_rename_caution_follows_the_connected_daemon(canonical):
    quirk = _quirk_with_note()
    _connect(canonical)
    html = hwmon_guidance.advisory_detail_html(quirk.details)
    handled = "stay the same across a rebuild" in html
    stale = "re-check pump roles" in html
    assert (handled, stale) == (canonical, not canonical)


@pytest.mark.parametrize("canonical", [True, False])
def test_the_chip_tooltip_and_verify_text_follow_the_connected_daemon(canonical):
    from control_ofc.services.system_state_view import _chip_tooltip

    chip = next(
        g.chip_prefix
        for g in hwmon_guidance.CHIP_GUIDANCE_DB
        if hwmon_guidance._IT87_V2_RENAME_NOTE in g.known_issues
    )
    _connect(canonical)
    tooltip = _chip_tooltip(chip)
    verify = hwmon_guidance.verification_guidance(
        "no_rpm_effect", "Gigabyte Technology Co., Ltd.", "it8689"
    )
    for text in (tooltip, verify):
        assert ("stay the same across a rebuild" in text) is canonical
        assert ("re-check pump roles" in text) is (not canonical)


@pytest.mark.parametrize("canonical", [True, False])
def test_the_dual_chip_false_alarm_paragraph_only_shows_where_it_can_be_true(canonical):
    _connect(canonical)
    html = hwmon_guidance.dual_chip_warning_html(
        "X870E AORUS MASTER", ["it8696", "it87952"], ["it8696"]
    )
    assert html, "precondition: the warning renders"
    assert ("False alarm check" in html) is (not canonical)
