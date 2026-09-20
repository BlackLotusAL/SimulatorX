"""Vacuum business workflow using only the device's OPC UA contract."""
import math

from opcua import Client, ua
from protocols.opcua.bindings import load_bindings

from .operation import BusinessError, OperationSUT, positive


class VacuumSUT(OperationSUT):
    def __init__(self, endpoint, *, bindings_path, pressure_threshold=1020, **options):
        super().__init__(**options)
        self.endpoint = endpoint
        self.bindings = load_bindings(bindings_path)
        self.pressure_threshold = positive(pressure_threshold, "pressure_threshold")

    def start_pump(self):
        return self._start(self._pump)

    def _pump(self):
        client = Client(self.endpoint, timeout=self.io_timeout)
        connected = submitted = completed = False
        try:
            client.connect()
            connected = True
            nodes = {key.split(".")[-1]: binding.resolve(client)
                     for key, binding in self.bindings.items()}
            self._checkpoint()
            nodes["command"].set_value(1, ua.VariantType.UInt16)
            submitted = True
            keys = ("command", "pressure_pa", "state_code", "result_code", "alarm_code")
            while True:
                self._checkpoint()
                # A single native Read; command acknowledgement prevents accepting
                # the previous operation's terminal feedback before this command runs.
                params = ua.ReadParameters()
                for key in keys:
                    item = ua.ReadValueId()
                    item.NodeId = nodes[key].nodeid
                    item.AttributeId = ua.AttributeIds.Value
                    params.NodesToRead.append(item)
                values = client.uaclient.read(params)
                observed = {key: dv.Value.Value for key, dv in zip(keys, values)}
                # JSON diagnostics must remain serializable for invalid device data.
                for key, value in observed.items():
                    if isinstance(value, float) and not math.isfinite(value):
                        observed[key] = str(value)
                observed["quality"] = {key: dv.StatusCode.name for key, dv in zip(keys, values)}
                self._observe(observed)
                if any(not dv.StatusCode.is_good() for dv in values):
                    raise BusinessError("bad_quality", "PLC reported non-Good quality")
                pressure = observed["pressure_pa"]
                if type(pressure) not in (int, float) or pressure < 0:
                    raise BusinessError("invalid_pressure", "PLC pressure is invalid")
                if observed["command"] == 0:
                    if observed["alarm_code"] or observed["state_code"] == 4 or observed["result_code"] == 3:
                        raise BusinessError("vacuum_alarm", "Vacuum workflow reported an alarm")
                    if (observed["state_code"] == 3 and observed["result_code"] == 2
                            and pressure <= self.pressure_threshold):
                        completed = True
                        return
                self._pause()
        except ua.UaStatusCodeError as exc:
            raise BusinessError("communication_error", str(exc)) from exc
        finally:
            try:
                if submitted and not completed:
                    nodes["command"].set_value(3, ua.VariantType.UInt16)
            finally:
                if connected:
                    client.disconnect()
