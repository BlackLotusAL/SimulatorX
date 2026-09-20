"""Fixed device entrypoints, PLC resources and inline configuration guarantees."""
import json
from dataclasses import replace

import pytest

from protocols.opcua import host as opcua
from framework import config as configuration, hosting
from framework.config import HardwareConfig, load_device, load_protocol
from framework.source import SOURCE_ROOT


def opcua_config(directory=None, **settings):
    return HardwareConfig("arbitrary/vacuum/chamber_plc", "opcua",
                          {"nodeset": "vacuum.xml", **settings},
                          directory or SOURCE_ROOT / "subsystems/vacuum/chamber_plc/resources")


@pytest.mark.integration
def test_xml_name_is_configurable_and_machine_name_does_not_locate_package(tmp_path, monkeypatch):
    config = opcua_config()
    xml = tmp_path / "vendor-export.xml"
    xml.write_bytes((config.directory / "vacuum.xml").read_bytes())
    monkeypatch.chdir(tmp_path)
    hardware = opcua.create(opcua_config(nodeset=str(xml)))
    hardware.validate()
    assert hardware.definition.model_factory.__name__ == "VacuumBehavior"
    assert hardware.bindings_path == config.directory / "bindings.json"
    assert hardware.process is None
    with hardware.create_process() as process:
        assert process.client.check_health()
        assert len(process.client.nodes) == 8
        assert process.client.reset()


@pytest.mark.parametrize("field", ["definition", "bindings", "mode", "endpoint",
                                  "control_endpoint", "control_socket", "sdk_socket", "error_file"])
def test_opcua_rejects_removed_settings(field):
    with pytest.raises(ValueError, match="Removed settings: " + field):
        opcua.create(opcua_config(**{field: "obsolete"})).validate()


@pytest.mark.parametrize("protocol,field", [(kind, field) for kind in ("sdk", "tcp")
                         for field in ("mode", "endpoint", "control_endpoint", "control_socket")])
def test_other_hosts_reject_connection_overrides(protocol, field):
    config = HardwareConfig("a/b/c", protocol, {field: "obsolete"}, SOURCE_ROOT)
    with pytest.raises(ValueError, match="Removed settings: " + field):
        load_protocol(config.type).create(config).validate()


@pytest.mark.parametrize("settings", [{}, {"nodeset": ""}, {"nodeset": "missing.xml"}])
def test_missing_xml_fails_preflight(settings):
    with pytest.raises(ValueError, match="nodeset"):
        opcua.create(replace(opcua_config(), settings=settings)).validate()


@pytest.fixture
def isolated_layout(tmp_path, monkeypatch):
    root = tmp_path / "src"
    package = root / "subsystems" / "vacuum" / "chamber_plc"
    resources = package / "resources"
    resources.mkdir(parents=True)
    monkeypatch.setattr(configuration, "SOURCE_ROOT", root)
    return package, resources


def test_configuration_outside_source_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Resource directory must match"):
        opcua.create(opcua_config(tmp_path / "resources")).validate()


def test_wrong_resource_directory_is_rejected(isolated_layout):
    package, _ = isolated_layout
    with pytest.raises(ValueError, match="Resource directory must match"):
        opcua.create(opcua_config(package / "other")).validate()


def test_missing_model_is_rejected(isolated_layout):
    _, resources = isolated_layout
    with pytest.raises(ValueError, match="Missing device entrypoint"):
        opcua.create(opcua_config(resources)).validate()


@pytest.mark.parametrize("failure", ["missing_create", "missing_bindings"])
def test_entrypoint_and_mapping_errors_are_clear(isolated_layout, monkeypatch, failure):
    package, resources = isolated_layout
    (package / "model.py").write_text("# fixture entrypoint")
    (resources / "vacuum.xml").write_text("<test/>")
    from subsystems.vacuum.chamber_plc.model import create

    def factory_loader(spec):
        assert spec == "subsystems.vacuum.chamber_plc.model:create"
        if failure == "missing_create":
            raise AttributeError("create")
        return create

    monkeypatch.setattr(hosting, "load_factory", factory_loader)
    message = "entrypoint" if failure == "missing_create" else "bindings"
    with pytest.raises(ValueError, match=message):
        opcua.create(opcua_config(resources)).validate()


@pytest.mark.parametrize("field", ["config", "definition", "package", "factory", "opcua_port", "mode"])
def test_removed_manifest_fields_fail_before_hardware_creation(tmp_path, monkeypatch, field):
    entry = {"id": "chamber_plc", "type": "opcua", "nodeset": "vacuum.xml", field: "old"}
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"id": "machine", "subsystems": [{"id": "vacuum", "hardware": [entry]}]}))
    from framework import runtime

    def forbidden(*args):
        raise AssertionError("Invalid manifest must not load hardware")

    monkeypatch.setattr(runtime, "load_protocol", forbidden)
    with pytest.raises(ValueError, match="Removed hardware fields: " + field):
        runtime.DeviceRuntime.from_config(path)


@pytest.mark.parametrize("bad", ["two-parts", "class", "../escape", "3axis", "a.b", ""])
@pytest.mark.parametrize("level", ["subsystem", "hardware"])
def test_ids_must_be_python_package_names(tmp_path, bad, level):
    item = {"id": bad if level == "hardware" else "chamber_plc", "type": "opcua", "nodeset": "vacuum.xml"}
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"id": "machine-with-hyphen", "subsystems": [
        {"id": bad if level == "subsystem" else "vacuum", "hardware": [item]}]}))
    with pytest.raises(ValueError, match="Python package names"):
        load_device(path)


def test_manifest_settings_are_inline_and_resources_follow_ids(tmp_path, monkeypatch):
    path = tmp_path / "device.json"
    path.write_bytes((SOURCE_ROOT / "device.json").read_bytes())
    monkeypatch.chdir(tmp_path)
    _, rows = load_device(path)
    assert len(rows) == 3
    for sid, hid, config in rows:
        assert config.directory == SOURCE_ROOT / "subsystems" / sid / hid / "resources"
        assert not {"config", "definition", "id", "factory", "type"} & config.settings.keys()
    assert [row[2].type for row in rows] == ["opcua", "sdk", "tcp"]
    assert rows[0][2].settings == {"port": 0, "nodeset": "vacuum.xml"}
    assert rows[1][2].settings == {}


@pytest.mark.parametrize("protocol,device", [("opcua", "subsystems.vacuum.chamber_plc"),
                                           ("sdk", "subsystems.motion.rotary_axis"),
                                           ("tcp", "subsystems.detector.modbus_tcp")])
def test_each_host_checks_fixed_definition_type(protocol, device, monkeypatch):
    import importlib
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(importlib.import_module(device + ".model"), "create", lambda: object())
    config = HardwareConfig("test/" + device.removeprefix("subsystems.").replace(".", "/"), protocol, {},
                            SOURCE_ROOT.joinpath(*device.split("."), "resources"))
    expected = {"opcua": "OPCUADefinition", "sdk": "SDKDefinition", "tcp": "TCPDefinition"}[protocol]
    with pytest.raises(TypeError, match="must return " + expected):
        load_protocol(config.type).create(config).validate()


def test_hardware_id_cannot_alias_another_package():
    config = replace(opcua_config(), identity="machine/vacuum/alias")
    config = replace(config, directory=configuration.device_directory(config.identity))
    with pytest.raises(ValueError, match="Missing device entrypoint"):
        opcua.create(config).validate()


@pytest.mark.parametrize("kind", [None, "", "plc", "OPCUA", "protocols.tcp:create", "unknown", True, 1, [], {}])
def test_invalid_protocol_type_is_rejected_before_loading(tmp_path, monkeypatch, kind):
    item = {"id": "chamber_plc", "type": kind, "nodeset": "vacuum.xml"}
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"id": "machine", "subsystems": [{"id": "vacuum", "hardware": [item]}]}))
    def forbidden(name):
        raise AssertionError("Invalid type must not import a module")
    monkeypatch.setattr(configuration.importlib, "import_module", forbidden)
    with pytest.raises(ValueError, match="type must be one of"):
        load_device(path)
    with pytest.raises(ValueError, match="type must be one of"):
        load_protocol(kind)


def test_missing_protocol_type_is_rejected(tmp_path):
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"id": "machine", "subsystems": [{"id": "vacuum", "hardware": [
        {"id": "chamber_plc", "nodeset": "vacuum.xml"}]}]}))
    with pytest.raises(ValueError, match="type must be one of"):
        load_device(path)


@pytest.mark.parametrize("kind,package", [("opcua", "vacuum/chamber_plc"), ("tcp", "detector/modbus_tcp")])
@pytest.mark.parametrize("port", [-1, 65536, True, "4840", 1.5])
def test_invalid_unified_port_is_rejected(kind, package, port):
    config = HardwareConfig("machine/" + package, kind, {"port": port, "nodeset": "vacuum.xml"},
                            SOURCE_ROOT / "subsystems" / package / "resources")
    hardware = load_protocol(kind).create(config)
    with pytest.raises(ValueError, match="port must be in"):
        hardware.validate()
    assert hardware.process is None


@pytest.mark.parametrize("kind,package", [("opcua", "vacuum/chamber_plc"), ("tcp", "detector/modbus_tcp")])
@pytest.mark.integration
def test_unified_port_default_and_fixed_listener(kind, package):
    config = HardwareConfig("machine/" + package, kind, {"nodeset": "vacuum.xml"},
                            SOURCE_ROOT / "subsystems" / package / "resources")
    def running(settings):
        hardware = load_protocol(kind).create(replace(config, settings=settings))
        hardware.validate()
        return hardware.create_process()
    with running(config.settings) as process:
        if kind == "opcua":
            from urllib.parse import urlparse
            port = urlparse(process.info["opcua_endpoint"]).port
        else:
            port = process.info["endpoint"][1]
        assert port > 0
    with running({**config.settings, "port": port}) as process:
        assert process.client.check_health()
        if kind == "opcua":
            assert urlparse(process.info["opcua_endpoint"]).port == port
        else:
            assert process.info["endpoint"][1] == port
