"""SUT implementation checks; these are not business acceptance assertions."""
import threading

import pytest

from reference_sut.operation import OperationSUT
from integration_support import terminal


def test_busy_copies_unknown_id_and_no_premature_success():
    entered, release = threading.Event(), threading.Event()
    sut = OperationSUT()

    def action():
        sut._observe({"position": 1})
        entered.set()
        assert release.wait(2)

    try:
        identity = sut._start(action)
        assert entered.wait(2)
        for _ in range(100):
            status = sut.get_status(identity)
            assert status["state"] == "running"
            status["observed"]["position"] = 999
        assert sut.get_status(identity)["observed"] == {"position": 1}
        with pytest.raises(RuntimeError, match="busy"):
            sut._start(action)
        with pytest.raises(KeyError):
            sut.get_status("unknown")
        release.set()
        assert terminal(sut, identity)["state"] == "succeeded"
    finally:
        release.set()
        sut.close()
    sut.close()
    with pytest.raises(RuntimeError, match="closed"):
        sut._start(action)


def test_new_operation_does_not_reuse_success_or_observations():
    sut = OperationSUT()
    try:
        first = sut._start(lambda: sut._observe({"value": 42}))
        old = terminal(sut, first)
        sut._thread.join(1)

        def failure():
            raise OSError("device disconnected")

        second = sut._start(failure)
        current = terminal(sut, second)
        assert first != second
        assert current["state"] == "failed"
        assert current["error_code"] == "communication_error"
        assert current["observed"] == {} and current["last_synced_at"] is None
        assert sut.get_status(first) == old
    finally:
        sut.close()


def test_close_cancels_and_joins_active_operation():
    entered = threading.Event()
    sut = OperationSUT()

    def action():
        entered.set()
        while True:
            sut._pause()

    identity = sut._start(action)
    assert entered.wait(2)
    sut.close()
    assert not sut._thread.is_alive()
    assert sut.get_status(identity)["error_code"] == "cancelled"


@pytest.mark.parametrize("option", ["timeout", "poll_interval", "io_timeout"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_timing_is_rejected(option, value):
    with pytest.raises(ValueError):
        OperationSUT(**{option: value})
