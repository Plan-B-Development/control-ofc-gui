"""G5 — the Hardware page states its cooling-device precondition (`WIRE-z`).

The two call sites were *transitively* safe: the device cards can only be
populated from an inventory `polling.py:142` refuses to fetch without
`control.cooling_devices`. That is a precondition held by someone else's code,
and an edit to the poller could remove it without touching this file — so the
gate is asserted here, at the site that depends on it.
"""

from __future__ import annotations

import pytest

from control_ofc.api.models import Capabilities, ControlCapability
from control_ofc.ui.pages.hardware_page import HardwarePage


class _RecordingClient:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.fetched = 0

    def delete_cooling_device(self, device_id: str) -> None:
        self.deleted.append(device_id)

    def get_cooling_devices(self):
        self.fetched += 1
        raise AssertionError("must not be called without control.cooling_devices")


@pytest.fixture()
def page(qapp, app_state):
    client = _RecordingClient()
    return HardwarePage(state=app_state, client=client), client


def _caps(supported: bool) -> Capabilities:
    return Capabilities(control=ControlCapability(cooling_devices=supported))


def test_forget_is_refused_without_the_capability(page, app_state) -> None:
    p, client = page
    app_state.capabilities = _caps(False)
    p._forget_device("dev-1")
    assert client.deleted == []
    assert client.fetched == 0
    assert "does not support cooling-device topology" in p._diag_result.text()


def test_forget_proceeds_with_the_capability(page, app_state) -> None:
    """The opposite branch — without it, an unconditional refusal passes above."""
    p, client = page
    app_state.capabilities = _caps(True)
    p._forget_device("dev-1")
    assert client.deleted == ["dev-1"]


def test_the_gate_matches_the_one_every_other_call_site_uses(page, app_state) -> None:
    """Relationship, not a literal.

    Asserted against the Controls page's own predicate rather than against
    `True`/`False`, so a gate that read some other capability key — or inverted
    it — fails here even though it would satisfy the two tests above.
    """
    from control_ofc.ui.pages.controls_page import ControlsPage

    p, _ = page
    observed = []
    for supported in (True, False):
        app_state.capabilities = _caps(supported)
        mine = p._supports_cooling_devices()
        theirs = ControlsPage._supports_cooling_devices(p)
        assert mine is theirs, f"gates disagree when cooling_devices={supported}"
        observed.append(mine)
    # Precondition: the predicate actually moved. Without this the assertion
    # above is satisfied by two gates that both return a constant.
    assert observed == [True, False], observed
