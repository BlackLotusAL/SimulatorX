import time

import pytest
from local_service.plc.service import build_simulator
from tests.helpers import eventually


@pytest.fixture
def live_chamber():
    chamber = build_simulator(opcua_port=0)
    chamber.start()
    try:
        yield chamber
    finally:
        chamber.stop()


def test_reset_restores_nodes_and_tick_clock(live_chamber):
    from opcua import ua
    chamber = live_chamber
    chamber.nodes["vacuum.command"].set_value(1, ua.VariantType.UInt16)
    eventually(lambda: chamber.nodes["vacuum.state_code"].get_value() == 1)
    before = time.monotonic()
    chamber.reset()
    assert chamber.last_tick >= before
    for key, node in chamber.nodes.items():
        assert node.get_value() == chamber.bindings[key].baseline


def test_failed_reset_marks_environment_unusable(live_chamber, monkeypatch):
    chamber = live_chamber

    def fail(*args):
        raise RuntimeError("reset write failed")

    monkeypatch.setattr(chamber, "_write", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        chamber.reset()
    assert not chamber.ready
    with pytest.raises(RuntimeError, match="restart"):
        chamber.reset()


def test_unexpected_loop_failure_is_not_reported_ready(live_chamber, monkeypatch):
    def fail(*args):
        raise RuntimeError("loop failed")
    monkeypatch.setattr(live_chamber, "step", fail)
    eventually(lambda: not live_chamber.ready)
    assert live_chamber.failed_reason == "loop failed"
