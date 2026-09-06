"""Business completion requires pressure evidence as well as PLC status."""
import pytest
from opcua import ua
from demo.plc import control


@pytest.mark.parametrize('operation,state,pressure', [('pump', 3, 101325), ('vent', 0, 1000)])
def test_inconsistent_completion_feedback_times_out(monkeypatch, operation, state, pressure):
    values = {name: ua.DataValue(ua.Variant(value)) for name, value in {
        'vacuum.pressure_pa': pressure,
        'vacuum.state_code': state,
        'vacuum.result_code': 2,
        'vacuum.alarm_code': 0,
        'vacuum.valve_open': False,
    }.items()}
    commands = []

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return values
        def command(self, key, value): commands.append((key, value))

    monkeypatch.setattr(control, 'PLCConnection', lambda *args: Connection())
    controller = control.Controller('unused', pump_timeout=0, vent_timeout=0)
    controller._work(operation)
    assert controller.snapshot()['status'] == 'fault'
    assert controller.snapshot()['fault'] == 'timeout'
    assert commands[-1] == ('vacuum.command', 3)
