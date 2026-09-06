import math
from dataclasses import replace

import pytest
from opcua import Server, ua
from simulatorx.__main__ import build_simulator
from simulatorx.bindings import load_bindings, resource, validate_bindings


@pytest.fixture
def chamber():
    simulator = build_simulator(opcua_port=0)
    simulator.reset()
    return simulator


def write(chamber, key, value):
    chamber.nodes[key].set_value(value, chamber.bindings[key].ua_type)


def value(chamber, key):
    return chamber.nodes[key].get_value()


def test_next_calculation_uses_the_written_pressure(chamber):
    write(chamber, "vacuum.command", 1)
    chamber.step(0.1)
    assert value(chamber, "vacuum.pressure_pa") == pytest.approx(1000 + (101325 - 1000) * math.exp(-0.1 / 3))
    write(chamber, "vacuum.pressure_pa", 200000.0)
    chamber.step(0.1)
    assert value(chamber, "vacuum.pressure_pa") == pytest.approx(1000 + (200000 - 1000) * math.exp(-0.1 / 3))
    assert value(chamber, "vacuum.command") == 0
    assert value(chamber, "vacuum.result_code") == 1
    chamber.step(100)
    assert value(chamber, "vacuum.state_code") == 3
    assert value(chamber, "vacuum.result_code") == 2


def test_open_valve_fails_pump_but_valve_itself_succeeds(chamber):
    write(chamber, "vacuum.command", 1)
    chamber.step(0.1)
    before = value(chamber, "vacuum.pressure_pa")
    write(chamber, "vacuum.valve_command", 1)
    chamber.step(0.1)
    assert value(chamber, "vacuum.valve_result") == 2
    assert value(chamber, "vacuum.result_code") == 3
    assert value(chamber, "vacuum.state_code") == 4
    assert value(chamber, "vacuum.alarm_code") == 1
    assert value(chamber, "vacuum.pressure_pa") > before


def test_pump_requires_closed_valve_and_vent_switches_safely(chamber):
    write(chamber, "vacuum.valve_open", True)
    write(chamber, "vacuum.command", 1)
    chamber.step(0.1)
    assert value(chamber, "vacuum.alarm_code") == 1
    write(chamber, "vacuum.valve_command", 2)
    write(chamber, "vacuum.command", 1)
    chamber.step(1)
    assert value(chamber, "vacuum.state_code") == 1
    write(chamber, "vacuum.command", 2)
    chamber.step(0.1)
    assert value(chamber, "vacuum.state_code") == 2
    assert value(chamber, "vacuum.valve_open") is True
    assert value(chamber, "vacuum.alarm_code") == 0
    chamber.step(100)
    assert value(chamber, "vacuum.state_code") == 0
    assert value(chamber, "vacuum.result_code") == 2
    assert value(chamber, "vacuum.pressure_pa") == pytest.approx(101325)


def test_stop_closes_valve_and_results_are_independent(chamber):
    write(chamber, "vacuum.command", 2)
    chamber.step(0.1)
    write(chamber, "vacuum.command", 3)
    chamber.step(0.1)
    assert value(chamber, "vacuum.valve_open") is False
    assert value(chamber, "vacuum.valve_result") == 2
    assert value(chamber, "vacuum.result_code") == 0
    assert value(chamber, "vacuum.state_code") == 0
    for command in (1, 1, 2, 2):
        write(chamber, "vacuum.valve_command", command)
        chamber.step(0.1)
        assert value(chamber, "vacuum.valve_command") == 0
        assert value(chamber, "vacuum.valve_open") is (command == 1)
        assert value(chamber, "vacuum.valve_result") == 2
        assert value(chamber, "vacuum.result_code") == 0


@pytest.mark.parametrize("command_key,result_key", [
    ("vacuum.command", "vacuum.result_code"),
    ("vacuum.valve_command", "vacuum.valve_result"),
])
def test_invalid_commands_are_consumed_and_report_failure(chamber, command_key, result_key):
    write(chamber, command_key, 65535)
    chamber.step(0.1)
    assert value(chamber, command_key) == 0
    assert value(chamber, result_key) == 3
    assert value(chamber, "vacuum.alarm_code") == 2


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
    bindings = load_bindings()
    nodes = validate_bindings(server, bindings)
    assert len(nodes) == 8
    assert nodes["vacuum.pressure_pa"].nodeid.NamespaceIndex == 3
    for field, setting, message in (("variant_type", "UInt16", "type mismatch"),
                                    ("writable", False, "access mismatch")):
        wrong = dict(bindings)
        wrong["vacuum.pressure_pa"] = replace(bindings["vacuum.pressure_pa"], **{field: setting})
        with pytest.raises(ValueError, match=message):
            validate_bindings(server, wrong)


@pytest.mark.parametrize("settings", [{"pump_tau": 0}, {"vent_tau": float("nan")},
                                     {"target_pressure": 101325}, {"tick_interval": -1}])
def test_invalid_configuration_is_rejected(settings):
    from simulatorx.simulator import VacuumSimulator
    with pytest.raises(ValueError):
        VacuumSimulator(Server(), **settings)
