import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from local_service.sdk.motion import CONTROL_ERROR, Result
from local_service.sdk.service import SDKService
from local_service.process import ServiceProcess
from tests.helpers import eventually


def test_function_sequences_are_atomic_independent_and_do_not_mask_validation(tmp_path):
    now = [0.0]
    service = SDKService(tmp_path / "control", tmp_path / "sdk", clock=lambda: now[0])
    invoke = lambda function, **args: service.invoke("rotary", function, args)["return_value"]
    service.dispatch({"op": "set_sequence", "target": "rotary.MoveAbsolute", "values": [19, 20, 0]})
    assert invoke("MoveAbsolute", angle=90) == 19
    assert invoke("GetState") == 0
    assert invoke("MoveAbsolute", angle=90) == 20
    assert invoke("MoveAbsolute", angle=90) == Result.NOT_ENABLED
    assert service.axis.position == 0 and not service.axis.busy
    assert invoke("Enable") == invoke("Home") == 0
    assert invoke("MoveAbsolute", angle=90) == 0
    now[0] = 0.5
    assert service.dispatch({"op": "snapshot"})["position"] == pytest.approx(22.5)
    before = service.sequences.snapshot()
    service.dispatch({"op": "snapshot"})
    service.dispatch({"op": "diagnostics"})
    assert service.sequences.snapshot() == before
    service.dispatch({"op": "set_sequence", "target": "rotary.MoveAbsolute", "values": [99]})
    assert invoke("MoveAbsolute", angle=-90) == 99
    assert service.axis.target == 90 and service.axis.busy
    service.dispatch({"op": "reset"})
    assert service.sequences.snapshot() == {} and list(service.events) == []
    assert service.axis.position == 0 and not service.axis.homed


def test_concurrent_calls_consume_each_item_once(tmp_path):
    service = SDKService(tmp_path / "control", tmp_path / "sdk", clock=lambda: 0)
    service.dispatch({"op": "set_sequence", "target": "rotary.GetState", "values": list(range(1, 65))})
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: service.invoke("rotary", "GetState", {})["return_value"], range(64)))
    assert sorted(codes) == list(range(1, 65))
    assert service.invoke("rotary", "GetState", {})["return_value"] == 0


def test_bad_native_numbers_remain_diagnosable(tmp_path):
    import json
    service = SDKService(tmp_path / "c", tmp_path / "s", clock=lambda: 0)
    for value in (float("nan"), float("inf"), 10 ** 400):
        assert service.invoke("rotary", "MoveAbsolute", {"angle": value})["return_value"] == Result.INVALID_ARGUMENT
    diagnostics = service.dispatch({"op": "diagnostics"})
    json.dumps(diagnostics, allow_nan=False)
    assert diagnostics["events"][0]["args"]["angle"] == "nan"


def test_unknown_sdk_or_function_cannot_silently_accept_unused_injection(tmp_path):
    service = SDKService(tmp_path / "c", tmp_path / "s", clock=lambda: 0)
    for target in ("typo.MoveAbsolute", "rotary.Missing", "MoveAbsolute"):
        with pytest.raises(ValueError):
            service.dispatch({"op": "set_sequence", "target": target, "values": [99]})
    assert service.sequences.snapshot() == {}


@pytest.mark.parametrize("values", [[True], [2 ** 31], [CONTROL_ERROR], [1.5], "bad"])
def test_invalid_override_rejected_without_replacing_sequence(tmp_path, values):
    service = SDKService(tmp_path / "c", tmp_path / "s", clock=lambda: 0)
    service.dispatch({"op": "set_sequence", "target": "rotary.Enable", "values": [5]})
    with pytest.raises(ValueError):
        service.dispatch({"op": "set_sequence", "target": "rotary.Enable", "values": values})
    assert service.invoke("rotary", "Enable", {})["return_value"] == 5


@pytest.mark.skipif(sys.platform != "linux", reason="Linux SDK service")
def test_service_advances_without_polling_and_stall_has_no_catchup():
    with ServiceProcess("sdk") as process:
        client = process.client
        assert client.call("Enable")["return_value"] == 0
        assert client.call("Home")["return_value"] == 0
        assert client.call("MoveAbsolute", angle=90, speed=90)["return_value"] == 0
        time.sleep(0.15)
        assert client.snapshot()["position"] > 0
        stalled = client.set_fault("stalled", True)
        time.sleep(0.15)
        assert client.snapshot()["position"] == stalled["position"]
        client.set_fault("stalled", False)
        eventually(lambda: client.snapshot()["position"] > stalled["position"])
        client.call("Stop")
        eventually(lambda: not client.snapshot()["busy"])
        assert client.check_health()
