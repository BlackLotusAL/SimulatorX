"""Host preflight and process ownership guarantees."""
import importlib
import sys

import pytest

from framework.config import HardwareConfig, load_protocol
from test.helpers import service_process


@pytest.mark.integration
def test_failed_client_close_still_stops_child(monkeypatch):
    process = service_process("tcp").start()
    child = process.process

    def fail():
        raise RuntimeError("client close failed")

    monkeypatch.setattr(process.client, "close", fail)
    with pytest.raises(RuntimeError, match="client close failed"):
        process.stop()
    assert child.poll() == 0
    assert process.client is process.process is process._temporary is process._log is None
    process.stop()


@pytest.mark.parametrize("protocol,device", [
    ("opcua", "subsystems.vacuum.chamber_plc"), ("sdk", "subsystems.motion.rotary_axis"),
    ("tcp", "subsystems.detector.modbus_tcp"),
])
@pytest.mark.parametrize("field,value", [("profile", "missing.json"),
                                        ("parameters", {}), ("protocol", "missing:factory")])
def test_rejects_removed_configuration_before_model_creation(
        protocol, device, field, value, tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("Invalid configuration must fail before device assembly")

    monkeypatch.setattr(importlib.import_module(device + ".model"), "create", forbidden)
    monkeypatch.setattr(sys, "platform", "linux")
    config = HardwareConfig("test/sub/unit", protocol,
                            {field: value}, tmp_path)
    hardware = load_protocol(config.type).create(config)
    with pytest.raises(ValueError, match="Removed settings: " + field + ".*independent device"):
        hardware.validate()
    assert hardware.client is hardware.process is None
