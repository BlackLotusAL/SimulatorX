import time
import threading

import pytest

pytestmark = pytest.mark.integration
from test.helpers import build_opcua_service
from test.helpers import eventually


@pytest.fixture
def live_chamber():
    chamber = build_opcua_service(port=0)
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


@pytest.fixture
def delayed_tick(live_chamber, monkeypatch):
    """Hold a live tick until the test allows it to finish."""
    entered, release = threading.Event(), threading.Event()

    def delayed(dt):
        entered.set()
        assert release.wait(5), "Test did not release the tick"

    monkeypatch.setattr(live_chamber.behavior, "step", delayed)
    assert entered.wait(2)
    # Represent a scheduling pause without sleeping for the entire stale period.
    live_chamber._last_progress = time.monotonic() - 3
    try:
        yield live_chamber, release
    finally:
        release.set()


def test_stale_but_recovering_tick_does_not_fail_health(delayed_tick):
    chamber, release = delayed_tick
    assert not chamber.ready
    resume = threading.Timer(0.1, release.set)
    resume.start()
    try:
        assert chamber.check_health()
        assert chamber.ready
    finally:
        release.set()
        resume.join()


def test_stuck_tick_still_fails_within_bounded_time(delayed_tick):
    chamber, _ = delayed_tick
    before = time.monotonic()
    with pytest.raises(RuntimeError, match="loop=True.*tick_age="):
        chamber.check_health()
    assert 1.9 <= time.monotonic() - before < 4


def test_reset_cannot_replace_worker_progress(live_chamber):
    chamber = live_chamber
    chamber.loop.stop()
    progress = chamber._last_progress
    before = time.monotonic()
    chamber.reset()
    assert chamber.last_tick >= before
    assert chamber._last_progress == progress
    with pytest.raises(RuntimeError, match="loop=False"):
        chamber.check_health()


def test_empty_worker_exception_is_not_reported_healthy(live_chamber, monkeypatch):
    def fail(dt):
        raise AssertionError()
    monkeypatch.setattr(live_chamber.behavior, "step", fail)
    eventually(lambda: live_chamber.failed_reason)
    with pytest.raises(RuntimeError, match="AssertionError"):
        live_chamber.check_health()


def test_process_pause_and_resume_does_not_kill_plc():
    import os
    import signal
    import sys
    from test.helpers import opcua_process

    if sys.platform != "linux":
        pytest.skip("Requires Linux SIGSTOP/SIGCONT")
    with opcua_process() as manager:
        for _ in range(3):
            os.kill(manager.process.pid, signal.SIGSTOP)
            try:
                time.sleep(2.2)
            finally:
                os.kill(manager.process.pid, signal.SIGCONT)
            time.sleep(0.2)
            assert manager.client.check_health()
            assert manager.client.reset()
            assert manager.process.poll() is None
