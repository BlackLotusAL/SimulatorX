"""Vacuum behavior reads and writes nodes; it owns no server or worker."""
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from protocols.opcua.contracts import NodeBehavior, OPCUADefinition
from protocols.opcua.bindings import load_bindings

NAMESPACE_URI = "urn:simulatorx:mvp:vacuum"
CHAMBER_ID = "VacuumChamber1"
RESET_ID = "VacuumChamber1.Reset"


def resource(name):
    return Path(__file__).resolve().parent / "resources" / name


def create():
    return OPCUADefinition(VacuumBehavior, NAMESPACE_URI, CHAMBER_ID,
                           RESET_ID, "SimulatorX Vacuum PLC")


ATMOSPHERE = 101325.0


@dataclass(frozen=True)
class _Parameters:
    pump_tau: float = 0.2
    vent_tau: float = 0.15
    target_pressure: float = 1000.0
    tolerance: float = 20.0
    tick_interval: float = 0.1

    def __post_init__(self):
        if not all(type(v) in (int, float) and math.isfinite(v) and v > 0
                   for v in asdict(self).values()):
            raise ValueError("Simulation settings must be positive finite numbers")
        if not self.tolerance < self.target_pressure < ATMOSPHERE - self.tolerance:
            raise ValueError("Target pressure must lie between tolerance and atmosphere")


# Device-local constants, matching the reference machine's former fast profile.
PARAMETERS = _Parameters()


class VacuumBehavior(NodeBehavior):
    def __init__(self, nodes):
        PARAMETERS.__post_init__()
        self.__dict__.update(asdict(PARAMETERS))
        missing = set(load_bindings(resource("bindings.json"))) - nodes.keys
        if missing:
            raise ValueError("Missing chamber nodes: " + str(sorted(missing)))
        self._write, self._value = nodes.write, nodes.read

    def _valve(self, opened):
        self._write("vacuum.valve_result", 1)
        self._write("vacuum.valve_open", opened)
        self._write("vacuum.valve_result", 2)

    def _fail_vacuum(self, alarm):
        self._write("vacuum.state_code", 4)
        self._write("vacuum.result_code", 3)
        self._write("vacuum.alarm_code", alarm)

    def step(self, dt):
        """One update, from nodes; callable without a running thread for unit tests."""
        if not math.isfinite(dt) or dt < 0:
            raise ValueError("dt must be finite and nonnegative")
        valve_command = self._value("vacuum.valve_command")
        if valve_command not in (None, 0):
            if valve_command in (1, 2):
                self._valve(valve_command == 1)
            else:
                self._write("vacuum.valve_result", 3)
                self._write("vacuum.alarm_code", 2)
            self._write("vacuum.valve_command", 0)

        command = self._value("vacuum.command")
        if command not in (None, 0):
            if command == 1:
                if self._value("vacuum.valve_open") is False:
                    self._write("vacuum.alarm_code", 0)
                    self._write("vacuum.result_code", 1)
                    self._write("vacuum.state_code", 1)
                else:
                    self._fail_vacuum(1)
            elif command == 2:
                # Switch away from pumping before opening the vent valve.
                self._write("vacuum.state_code", 2)
                self._write("vacuum.result_code", 1)
                self._write("vacuum.alarm_code", 0)
                self._valve(True)
            elif command == 3:
                self._write("vacuum.state_code", 0)
                self._write("vacuum.result_code", 0)
                self._valve(False)
            else:
                self._fail_vacuum(2)
            self._write("vacuum.command", 0)

        opened = self._value("vacuum.valve_open")
        state = self._value("vacuum.state_code")
        if state == 1 and opened is True:
            self._fail_vacuum(1)
            state = 4
        pressure = self._value("vacuum.pressure_pa")
        if type(pressure) not in (int, float) or not math.isfinite(pressure):
            return
        if opened is True:
            target, tau = ATMOSPHERE, self.vent_tau
        elif opened is False and state == 1:
            target, tau = self.target_pressure, self.pump_tau
        else:
            return
        remaining = math.exp(-dt / tau)
        pressure = pressure * remaining + target * (1 - remaining)
        self._write("vacuum.pressure_pa", pressure)
        if abs(pressure - target) <= self.tolerance:
            if state == 1 and opened is False:
                self._write("vacuum.state_code", 3)
                self._write("vacuum.result_code", 2)
            elif state == 2 and opened is True:
                self._write("vacuum.state_code", 0)
                self._write("vacuum.result_code", 2)
