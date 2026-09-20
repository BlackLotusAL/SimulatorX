import json
import sys
import socket

import pytest
from opcua import ua

from framework.runtime import DeviceRuntime
from framework.source import SOURCE_ROOT


def reference(select=None):
    return DeviceRuntime.from_config(SOURCE_ROOT / "device.json", select)


@pytest.mark.integration
def test_two_runtimes_of_same_device_are_isolated():
    with reference(["vacuum/chamber_plc"]) as first, reference(["vacuum/chamber_plc"]) as second:
        left, right = next(first.hardware()), next(second.hardware())
        assert left.identity == right.identity
        assert left.endpoints != right.endpoints
        assert left.process.process.pid != right.process.process.pid
        left.nodes["vacuum.alarm_code"].set_value(7, ua.VariantType.UInt16)
        right.nodes["vacuum.alarm_code"].set_value(9, ua.VariantType.UInt16)
        first.reset()
        assert left.nodes["vacuum.alarm_code"].get_value() == 0
        assert right.nodes["vacuum.alarm_code"].get_value() == 9
        first.stop()
        assert second.check_health()
        second.reset()
        assert right.nodes["vacuum.alarm_code"].get_value() == 0


def test_sdk_platform_rejected_before_any_process_starts(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    device = reference()
    with pytest.raises(RuntimeError, match="Linux or WSL"):
        device.start()
    assert all(h.process is None for h in device.hardware())


@pytest.mark.integration
def test_port_conflict_rolls_back_other_hardware_without_stopping_owner(tmp_path):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        path = tmp_path / "device.json"
        path.write_text(json.dumps({"id": "machine", "subsystems": [
            {"id": "vacuum", "hardware": [{"id": "chamber_plc", "type": "opcua", "nodeset": "vacuum.xml"}]},
            {"id": "detector", "hardware": [{"id": "modbus_tcp", "type": "tcp",
                                                   "port": occupied.getsockname()[1]}]}]}))
        device = DeviceRuntime.from_config(path)
        with pytest.raises(RuntimeError, match="startup failed"):
            device.start()
        assert all(h.process is None for h in device.hardware())
        with socket.create_connection(occupied.getsockname(), timeout=2):
            pass


@pytest.mark.skipif(sys.platform != "linux", reason="Native SDK requires Linux")
@pytest.mark.integration
def test_full_machine_and_individual_reset():
    with reference() as device:
        plc = device.subsystems["vacuum"].hardware["chamber_plc"]
        axis = device.subsystems["motion"].hardware["rotary_axis"]
        tcp = device.subsystems["detector"].hardware["modbus_tcp"]
        plc.nodes["vacuum.alarm_code"].set_value(7, ua.VariantType.UInt16)
        axis.client.call("Enable")
        tcp.client.responses["04:0:2"].set_sequence([{"registers": [9, 1]}])
        axis.reset()
        assert not axis.client.snapshot()["enabled"]
        assert plc.nodes["vacuum.alarm_code"].get_value() == 7
        assert tcp.client.responses.snapshot()
        device.reset()
        assert plc.nodes["vacuum.alarm_code"].get_value() == 0
        assert tcp.client.responses.snapshot() == {}
