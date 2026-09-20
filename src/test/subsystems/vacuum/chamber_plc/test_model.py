import math
from dataclasses import replace
from types import SimpleNamespace

import pytest
from subsystems.vacuum.chamber_plc.model import resource, VacuumBehavior
from protocols.opcua.contracts import NodeAccess
from protocols.opcua.bindings import load_bindings


@pytest.fixture
def chamber():
    values = {key: binding.baseline for key, binding in load_bindings(resource("bindings.json")).items()}
    access = NodeAccess(values.__getitem__, values.__setitem__, frozenset(values))
    return SimpleNamespace(step=VacuumBehavior(access).step, values=values)


def write(chamber, key, value):
    chamber.values[key] = value


def value(chamber, key):
    return chamber.values[key]


def test_next_calculation_uses_the_written_pressure(chamber):
    write(chamber, "vacuum.command", 1)
    chamber.step(0.1)
    assert value(chamber, "vacuum.pressure_pa") == pytest.approx(1000 + (101325 - 1000) * math.exp(-0.1 / 0.2))
    write(chamber, "vacuum.pressure_pa", 200000.0)
    chamber.step(0.1)
    assert value(chamber, "vacuum.pressure_pa") == pytest.approx(1000 + (200000 - 1000) * math.exp(-0.1 / 0.2))
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


@pytest.mark.parametrize("settings", [{"pump_tau": 0}, {"vent_tau": float("nan")},
                                     {"target_pressure": 101325}, {"tick_interval": -1}])
def test_invalid_device_constants_are_rejected(settings):
    from subsystems.vacuum.chamber_plc.model import PARAMETERS
    with pytest.raises(ValueError):
        replace(PARAMETERS, **settings)
