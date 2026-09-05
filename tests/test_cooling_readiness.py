"""Cooling Hardware Readiness — pure mapping, model, client, worker (DEC-207).

Covers the pure Readiness ⊕ Super-I/O mapping (``control_ofc.ui.cooling_readiness``),
the ``HardwareReadiness`` model + parse, the ``hardware_readiness`` client wrapper,
and the shared ``_HardwareReadinessWorker`` (off-thread fetch/refresh/probe, with
404-degrade semantics).

The ``CoolingReadinessView`` widget + the DiagnosticsPage Readiness tab it lived on
were retired in DEC-216; the merged readiness UI (verdict, grouped checks, recommended
actions, Super-I/O table + copy-command + probe, last-scanned, degraded-scan note) now
lives on the Hardware page and is covered by ``test_hardware_page`` / ``test_hardware_view``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import (
    HardwareReadiness,
    ReadinessItem,
    ReadinessRollup,
    SuperIoChip,
    SuperIoRecommendation,
    SuperIoReport,
    parse_hardware_readiness,
)
from control_ofc.ui import cooling_readiness as cr
from control_ofc.ui.readiness_merge import (
    ACTION_DEEP_LINK,
    ACTION_IN_SURFACE,
    ACTION_TAB_SWITCH,
    ActionSpec,
)

# ── Fixtures / builders ──────────────────────────────────────────────


def _hw(overall="warning", items=None, superio=None, **kw) -> HardwareReadiness:
    return HardwareReadiness(
        overall=overall,
        rollup=ReadinessRollup(overall=overall, top_summary=kw.pop("top", None) or None),
        items=items if items is not None else [],
        superio=superio if superio is not None else SuperIoReport(arch_supported=True),
        **kw,
    )


def _unbound_chip(name="it8688") -> SuperIoChip:
    return SuperIoChip(
        chip_name=name,
        vendor="ite",
        confidence="medium",
        expected_module="it87",
        evidence=["dmi_board_table"],
        hwmon_present=False,
        recommendation=SuperIoRecommendation(
            module="it87",
            in_mainline=False,
            load_hint="sudo modprobe it87",
            reason="Chip present but no driver bound.",
            risk_notes=["Needs it87-dkms-git"],
        ),
    )


# ── Pure mapping (control_ofc.ui.cooling_readiness) ──────────────────


def test_build_readiness_items_sorted_most_severe_first_ok_last():
    hw = _hw(
        items=[
            ReadinessItem(code="cpu_sensor_present", severity="ok"),
            ReadinessItem(code="no_pwm_controls", severity="warning"),
            ReadinessItem(code="cpu_sensor_missing", severity="critical"),
            ReadinessItem(code="cpu_default_low_confidence", severity="info"),
        ]
    )
    items = cr.build_readiness_items(hw)
    assert [i.code for i in items] == [
        "cpu_sensor_missing",
        "no_pwm_controls",
        "cpu_default_low_confidence",
        "cpu_sensor_present",
    ]
    assert items[-1].is_ok is True


def test_action_mapping_deep_links_and_in_surface_and_tab_switch():
    def action(code: str) -> ActionSpec:
        (item,) = cr.build_readiness_items(
            _hw(items=[ReadinessItem(code=code, severity="warning")])
        )
        return item.action

    assert action("cpu_sensor_missing") == ActionSpec(
        ACTION_DEEP_LINK, "Pick a CPU sensor", "preferred_cpu"
    )
    assert action("selected_mb_sensor_missing").target == "preferred_mb"
    assert action("pwm_control_unverified") == ActionSpec(
        ACTION_TAB_SWITCH, "Test PWM control", "pwm_verify"
    )
    # Super-I/O codes point at the on-page section, NOT a (retired) tab-switch.
    assert action("no_pwm_controls") == ActionSpec(
        ACTION_IN_SURFACE, "View Super-I/O details", "superio"
    )
    assert action("superio_driver_unloaded").target == "superio"
    assert action("sensors_unavailable") == ActionSpec(ACTION_TAB_SWITCH, "View sensors", "sensors")


def test_doc_links_present_for_problems_absent_for_ok():
    (problem,) = cr.build_readiness_items(
        _hw(items=[ReadinessItem(code="cpu_sensor_missing", severity="critical")])
    )
    assert problem.doc_url.startswith("https://github.com/Plan-B-Development/control-ofc-gui")
    assert "24_Cooling_Hardware_Readiness_Guide.md#" in problem.doc_url
    (ok,) = cr.build_readiness_items(
        _hw(items=[ReadinessItem(code="cpu_sensor_present", severity="ok")])
    )
    assert ok.doc_url == ""


def test_group_mapping_covers_the_four_groups():
    assert cr.group_for("cpu_sensor_missing") == cr.GROUP_TEMP
    assert cr.group_for("no_pwm_controls") == cr.GROUP_FANS
    assert cr.group_for("superio_driver_unloaded") == cr.GROUP_SUPERIO
    assert cr.group_for("sensors_unavailable") == cr.GROUP_SENSORS
    # An unknown code is not dropped — it lands in Sensor configuration.
    assert cr.group_for("brand_new_code") == cr.GROUP_SENSORS


def test_daemon_detail_stays_in_plain_slot_not_html():
    (item,) = cr.build_readiness_items(
        _hw(
            items=[
                ReadinessItem(
                    code="cpu_sensor_missing",
                    severity="critical",
                    detail="<b>hi</b>",
                    recommended_action="do X",
                )
            ]
        )
    )
    # The daemon detail + action are PlainText content; no GUI HTML is fabricated.
    assert "<b>hi</b>" in item.plain_detail
    assert "→ do X" in item.plain_detail
    assert item.html_detail == ""


# ── Model parse ──────────────────────────────────────────────────────


def test_parse_hardware_readiness_full():
    hw = parse_hardware_readiness(
        {
            "api_version": 1,
            "overall": "warning",
            "rollup": {"overall": "warning", "warning": 2, "top_code": "no_pwm_controls"},
            "items": [{"code": "no_pwm_controls", "severity": "warning"}],
            "superio": {"arch_supported": True, "chips": [{"chip_name": "it8688"}]},
            "scanned_age_ms": 1200,
            "generation": 7,
        }
    )
    assert hw.overall == "warning"
    assert hw.rollup.warning == 2
    assert hw.items[0].code == "no_pwm_controls"
    assert hw.superio.chips[0].chip_name == "it8688"
    assert hw.scanned_age_ms == 1200
    assert hw.generation == 7


def test_parse_hardware_readiness_defaults_and_malformed_tolerated():
    # Absent nested objects → empty defaults; malformed types don't raise.
    hw = parse_hardware_readiness({})
    assert hw.overall == "ok"
    assert hw.rollup.overall == "ok"
    assert hw.items == []
    assert hw.superio.chips == []
    hw2 = parse_hardware_readiness(
        {"rollup": "nope", "superio": 5, "scanned_age_ms": "bad", "generation": None}
    )
    assert isinstance(hw2.rollup, ReadinessRollup)
    assert isinstance(hw2.superio, SuperIoReport)
    assert hw2.scanned_age_ms == 0 and hw2.generation == 0


# ── Client ───────────────────────────────────────────────────────────


def test_client_hardware_readiness_get_and_force():
    from control_ofc.api.client import DaemonClient

    client = DaemonClient.__new__(DaemonClient)
    client._get = MagicMock(return_value={"overall": "ok", "items": []})
    client.hardware_readiness()
    client._get.assert_called_once_with("/inventory/hardware-readiness", params=None)
    client._get.reset_mock()
    client.hardware_readiness(refresh=True)
    client._get.assert_called_once_with("/inventory/hardware-readiness", params={"refresh": "true"})


def test_the_readiness_parameter_is_named_for_the_wire_key():
    """`WIRE-ab`: the parameter was `force` while the query key is `refresh`.

    Verified correct at the time — the request really did send `?refresh=true` —
    but a mismatched pair invites an edit that renames one and not the other.
    Asserted as a relationship between the signature and the emitted key, so it
    cannot drift apart again without failing here.
    """
    import inspect

    from control_ofc.api.client import DaemonClient

    (param,) = [
        n for n in inspect.signature(DaemonClient.hardware_readiness).parameters if n != "self"
    ]
    client = DaemonClient.__new__(DaemonClient)
    client._get = MagicMock(return_value={"overall": "ok", "items": []})
    client.hardware_readiness(**{param: True})
    (_route, kwargs) = client._get.call_args[0][0], client._get.call_args[1]
    assert param in kwargs["params"], (
        f"the parameter is named {param!r} but the wire key is "
        f"{sorted(kwargs['params'])} — rename one and you must rename both"
    )


# ── Worker (off-thread call, run synchronously here) ─────────────────


class _WorkerClient:
    def __init__(self, hw=None, superio=None, error=None, probe_error=None):
        self._hw = hw if hw is not None else _hw(overall="ok")
        self._superio = superio if superio is not None else SuperIoReport(arch_supported=True)
        self._error = error
        self._probe_error = probe_error
        self.force_calls: list[bool] = []
        self.socket_path = "/tmp/control-ofc-cr-test.sock"

    def hardware_readiness(self, refresh: bool = False) -> HardwareReadiness:
        # Named for the wire key, like the real client (`WIRE-ab`). A stub whose
        # keyword differs from the thing it stands in for is a stub that passes
        # while the production call site would raise TypeError.
        self.force_calls.append(refresh)
        if self._error is not None:
            raise self._error
        return self._hw

    def superio_probe(self) -> SuperIoReport:
        if self._probe_error is not None:
            raise self._probe_error
        return self._superio

    def close(self) -> None:
        pass


def _worker(client):
    from control_ofc.ui.pages.diagnostics_workers import _HardwareReadinessWorker

    w = _HardwareReadinessWorker("/tmp/x.sock")
    w._client = client  # inject: _ensure_client returns this instead of building one
    return w


def test_worker_do_fetch_and_do_refresh_emit_and_flag_force():
    client = _WorkerClient(hw=_hw(overall="warning"))
    w = _worker(client)
    got = []
    w.fetch_ok.connect(got.append)
    w.do_fetch()
    w.do_refresh()
    assert [r.overall for r in got] == ["warning", "warning"]
    assert client.force_calls == [False, True]


def test_worker_404_signals_unsupported():
    from control_ofc.api.errors import DaemonError

    err = DaemonError(status=404, code="not_found", message="nope")
    w = _worker(_WorkerClient(error=err))
    cats = []
    w.fetch_error.connect(lambda cat, msg: cats.append(cat))
    w.do_fetch()
    assert cats == ["unsupported"]


def test_worker_503_hardware_unavailable_signals_unavailable():
    """DEC-231: a 503 hardware_unavailable is a transient, retryable soft state —
    surfaced as 'unavailable' (like the timeout/daemon-down arms), not a hard
    'error'. Both trigger conditions (503 status, hardware_unavailable code) map
    to 'unavailable'."""
    from control_ofc.api.errors import DaemonError

    def _category(err):
        w = _worker(_WorkerClient(error=err))
        cats = []
        w.fetch_error.connect(lambda cat, _msg: cats.append(cat))
        w.do_fetch()
        return cats

    assert _category(DaemonError(status=503, code="hardware_unavailable", message="x")) == [
        "unavailable"
    ]
    assert _category(DaemonError(status=503, code="other", message="x")) == ["unavailable"]
    assert _category(DaemonError(status=0, code="hardware_unavailable", message="x")) == [
        "unavailable"
    ]


def test_worker_probe_uses_dedicated_signals():
    w = _worker(_WorkerClient(superio=SuperIoReport(arch_supported=True, chips=[_unbound_chip()])))
    got = []
    w.probe_ok.connect(got.append)
    w.do_probe()
    assert got and got[0].chips[0].chip_name == "it8688"


def test_worker_probe_404_is_transient_error_not_unsupported():
    from control_ofc.api.errors import DaemonError

    w = _worker(_WorkerClient(probe_error=DaemonError(status=404, code="not_found", message="x")))
    errs = []
    w.probe_error.connect(lambda cat, msg: errs.append(cat))
    w.do_probe()
    assert errs == ["error"]  # a probe 404 must NOT flip the passive panel unsupported
