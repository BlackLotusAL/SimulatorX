"""OPC UA nodes are the only business state; updates read current node values."""
import logging
import math
import threading
import time
from datetime import datetime, timezone

from opcua import ua, uamethod
from .bindings import CHAMBER_ID, RESET_ID, NAMESPACE_URI, load_bindings, validate_bindings

log = logging.getLogger(__name__)
ATMOSPHERE = 101325.0


class VacuumSimulator:
    def __init__(self, server, bindings=None, *, pump_tau=3.0, vent_tau=1.5,
                 target_pressure=1000.0, tolerance=20.0, tick_interval=0.1):
        if not all(math.isfinite(v) and v > 0 for v in
                   (pump_tau, vent_tau, target_pressure, tolerance, tick_interval)):
            raise ValueError("Simulation settings must be positive finite numbers")
        if not tolerance < target_pressure < ATMOSPHERE - tolerance:
            raise ValueError("Target pressure must lie between tolerance and atmosphere")
        self.server = server
        self.bindings = bindings or load_bindings()
        missing = set(load_bindings()) - self.bindings.keys()
        if missing:
            raise ValueError(f"Missing chamber nodes: {sorted(missing)}")
        self.nodes = validate_bindings(server, self.bindings)
        self.pump_tau, self.vent_tau = pump_tau, vent_tau
        self.target_pressure, self.tolerance = target_pressure, tolerance
        self.tick_interval = tick_interval
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._started = False
        self.failed_reason = None
        self.last_tick = 0.0
        # This library version lacks scalar Value type checks. Validate only
        # wire types here; do not restrict business values or create fault rules.
        service = server.iserver.attribute_service
        self._native_write = service.write
        self._native_read = service.read
        self._types = {self.nodes[k].nodeid: b.ua_type for k, b in self.bindings.items()}
        service.write = self._typed_write
        service.read = self._synchronized_read
        idx = server.get_namespace_index(NAMESPACE_URI)
        chamber = server.get_node(ua.NodeId(CHAMBER_ID, idx))

        @uamethod
        def reset_method(parent):
            self.reset()
            return True

        chamber.add_method(ua.NodeId(RESET_ID, idx), ua.QualifiedName("Reset", idx),
                           reset_method, [], [ua.VariantType.Boolean])

    def _synchronized_read(self, params, *args, **kwargs):
        # One native Read sees a completed tick/Reset, not a partial update.
        with self._lock:
            return self._native_read(params, *args, **kwargs)

    def _typed_write(self, params, *args, **kwargs):
        results = []
        with self._lock:
            for item in params.NodesToWrite:
                expected = self._types.get(item.NodeId)
                variant = item.Value.Value
                valid = True
                if expected is not None and item.AttributeId == ua.AttributeIds.Value:
                    valid = not variant.is_array and variant.VariantType in (expected, ua.VariantType.Null)
                    if variant.VariantType == ua.VariantType.UInt16:
                        valid = valid and type(variant.Value) is int and 0 <= variant.Value <= 65535
                    elif variant.VariantType == ua.VariantType.Boolean:
                        valid = valid and type(variant.Value) is bool
                    elif variant.VariantType == ua.VariantType.Double:
                        valid = valid and type(variant.Value) in (int, float)
                if not valid:
                    results.append(ua.StatusCode(ua.StatusCodes.BadTypeMismatch))
                    continue
                single = ua.WriteParameters()
                single.NodesToWrite = [item]
                results.extend(self._native_write(single, *args, **kwargs))
        return results

    @property
    def endpoint(self):
        port = self.server.bserver.port if self._started else self.server.endpoint.port
        return self.server.endpoint._replace(netloc=f"{self.server.endpoint.hostname}:{port}").geturl()

    @property
    def ready(self):
        listener = self.server.bserver._server if self._started else None
        return bool(self._started and not self.failed_reason and self._thread and self._thread.is_alive()
                    and listener and listener.is_serving()
                    and time.monotonic() - self.last_tick < max(2, self.tick_interval * 10))

    def start(self):
        if self._started:
            raise RuntimeError("Simulator already started")
        self.reset()
        self.server.start()
        self._started = True
        self._stop.clear()
        self.last_tick = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="simulatorx-chamber", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("Chamber loop did not stop")
        if self._started:
            self.server.stop()
            self._started = False

    def reset(self):
        with self._lock:
            if self.failed_reason:
                raise RuntimeError("Simulator failed; restart it before reuse")
            try:
                for key, binding in self.bindings.items():
                    self._write(key, binding.baseline)
                self.last_tick = time.monotonic()
            except Exception as exc:
                self.failed_reason = f"Reset failed: {exc}"
                raise

    def _write(self, key, value):
        data = ua.DataValue(ua.Variant(value, self.bindings[key].ua_type))
        data.SourceTimestamp = data.ServerTimestamp = datetime.now(timezone.utc).replace(tzinfo=None)
        self.nodes[key].set_value(data)

    def _value(self, key):
        dv = self.nodes[key].get_attributes([ua.AttributeIds.Value])[0]
        return dv.Value.Value if dv.StatusCode.is_good() else None

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
        with self._lock:
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

    def _run(self):
        while not self._stop.wait(self.tick_interval):
            try:
                with self._lock:
                    now = time.monotonic()
                    self.step(now - self.last_tick)
                    self.last_tick = now
            except Exception as exc:
                self.failed_reason = str(exc)
                log.exception("Chamber loop failed")
                return
