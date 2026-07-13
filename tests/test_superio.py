"""Super-I/O detection: model parse + client method (DEC-202). The Super-I/O tab
was retired into the merged Cooling Hardware Readiness page (DEC-207) — its page
integration is now covered by ``test_cooling_readiness.py``."""

from __future__ import annotations

from unittest.mock import MagicMock

from control_ofc.api.models import parse_superio_report

# ── Model parsing ────────────────────────────────────────────────────


def test_parse_superio_report_full():
    data = {
        "api_version": 1,
        "arch_supported": True,
        "chips": [
            {
                "chip_name": "it8688",
                "vendor": "ite",
                "evidence": ["dmi_board_table", "kernel_log"],
                "confidence": "high",
                "expected_module": "it87",
                "module_loaded": False,
                "hwmon_present": False,
                "recommendation": {
                    "module": "it87",
                    "in_mainline": False,
                    "load_hint": "install it87-dkms-git",
                    "reason": "board lists it8688",
                    "risk_notes": ["needs DKMS"],
                },
                "caveats": [],
                "future_field": "ignored",  # forward-compat: unknown key dropped
            }
        ],
        "acpi_conflict_drivers": ["it87"],
        "notes": ["detection is not control"],
    }
    r = parse_superio_report(data)
    assert r.arch_supported is True
    assert len(r.chips) == 1
    c = r.chips[0]
    assert c.chip_name == "it8688"
    assert c.vendor == "ite"
    assert c.evidence == ["dmi_board_table", "kernel_log"]
    assert c.recommendation is not None
    assert c.recommendation.module == "it87"
    assert c.recommendation.in_mainline is False
    assert c.recommendation.risk_notes == ["needs DKMS"]
    assert r.acpi_conflict_drivers == ["it87"]
    assert r.notes == ["detection is not control"]


def test_parse_superio_report_defaults_and_forward_compat():
    # Absent arch_supported → False (AIP-180 safe default); absent recommendation
    # → None; unknown chip keys ignored (forward compatibility).
    r = parse_superio_report({"chips": [{"chip_name": "nct6799", "new_thing": 1}]})
    assert r.api_version == 1
    assert r.arch_supported is False
    assert len(r.chips) == 1
    assert r.chips[0].chip_name == "nct6799"
    assert r.chips[0].recommendation is None


def test_parse_superio_report_non_x86():
    r = parse_superio_report(
        {"arch_supported": False, "chips": [], "notes": ["unsupported architecture"]}
    )
    assert r.arch_supported is False
    assert r.chips == []
    assert r.notes == ["unsupported architecture"]


def test_parse_superio_report_skips_non_dict_chips():
    r = parse_superio_report({"chips": ["garbage", {"chip_name": "it8628"}]})
    assert [c.chip_name for c in r.chips] == ["it8628"]


# ── Client method ────────────────────────────────────────────────────


def test_client_superio_detect_calls_get_and_parses():
    from control_ofc.api.client import DaemonClient

    client = DaemonClient.__new__(DaemonClient)
    client._get = MagicMock(
        return_value={
            "arch_supported": True,
            "chips": [{"chip_name": "it8688", "expected_module": "it87"}],
        }
    )
    report = client.superio_detect()
    client._get.assert_called_once_with("/inventory/superio")
    assert report.chips[0].chip_name == "it8688"


def test_client_superio_probe_posts_and_parses():
    from control_ofc.api.client import DaemonClient

    client = DaemonClient.__new__(DaemonClient)
    client._post = MagicMock(
        return_value={
            "arch_supported": True,
            "chips": [{"chip_name": "it8688", "evidence": ["port_probe"]}],
            "port_probe_available": True,
            "port_probe_reason": "available",
        }
    )
    report = client.superio_probe()
    client._post.assert_called_once_with("/inventory/superio/probe", json={})
    assert report.port_probe_available is True
    assert report.chips[0].evidence == ["port_probe"]


def test_parse_superio_report_port_probe_fields_default_false():
    r = parse_superio_report({"chips": []})
    assert r.port_probe_available is False
    assert r.port_probe_reason == ""
