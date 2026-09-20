"""Native node metadata, reset and binding contracts against the real OPC UA stack."""
from dataclasses import replace

import pytest
from opcua import Server, ua
from protocols.opcua.bindings import load_bindings, validate_bindings
from subsystems.vacuum.chamber_plc.model import resource
from test.helpers import build_opcua_service

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def node_service():
    # No worker is started. Every case resets real nodes before and after use.
    service = build_opcua_service(port=0)
    yield service
    service.stop()


@pytest.fixture
def chamber(node_service):
    node_service.reset()
    try:
        yield node_service
    finally:
        node_service.reset()


def write(chamber, key, value):
    chamber.nodes[key].set_value(value, chamber.bindings[key].ua_type)


def value(chamber, key):
    return chamber.nodes[key].get_value()


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -float("inf")])
def test_unusable_pressure_skips_math_but_accepts_commands_and_reset(chamber, bad):
    data = ua.DataValue(ua.Variant(bad, ua.VariantType.Null if bad is None else ua.VariantType.Double))
    chamber.nodes["vacuum.pressure_pa"].set_value(data)
    write(chamber, "vacuum.command", 2)
    chamber.step(0.1)
    assert value(chamber, "vacuum.command") == 0
    assert value(chamber, "vacuum.valve_open") is True
    assert value(chamber, "vacuum.result_code") == 1
    after = chamber.nodes["vacuum.pressure_pa"].get_data_value()
    assert after.SourceTimestamp == data.SourceTimestamp
    chamber.reset()
    assert value(chamber, "vacuum.pressure_pa") == 101325


def test_status_and_alarm_edits_persist_until_a_relevant_action(chamber):
    write(chamber, "vacuum.state_code", 65535)
    write(chamber, "vacuum.result_code", 7)
    write(chamber, "vacuum.alarm_code", 42)
    before = chamber.nodes["vacuum.state_code"].get_data_value()
    chamber.step(5)
    assert chamber.nodes["vacuum.state_code"].get_data_value() == before
    assert value(chamber, "vacuum.result_code") == 7
    assert value(chamber, "vacuum.alarm_code") == 42


def test_namespace_remapping_and_contract_validation():
    server = Server()
    server.register_namespace("urn:extra:first")
    server.import_xml(str(resource("vacuum.xml")))
    bindings = load_bindings(resource("bindings.json"))
    nodes = validate_bindings(server, bindings)
    assert len(nodes) == 8
    assert nodes["vacuum.pressure_pa"].nodeid.NamespaceIndex == 3
    for field, setting, message in (("variant_type", "UInt16", "type mismatch"),
                                    ("writable", False, "access mismatch")):
        wrong = dict(bindings)
        wrong["vacuum.pressure_pa"] = replace(bindings["vacuum.pressure_pa"], **{field: setting})
        with pytest.raises(ValueError, match=message):
            validate_bindings(server, wrong)
