"""OPC UA nodes are the only business state; updates read current node values."""
import threading
import time
from datetime import datetime, timezone

from opcua import Server, ua, uamethod
from framework.loop import PeriodicLoop
from .bindings import load_bindings, validate_bindings
from .contracts import NodeAccess, NodeBehavior


class OPCUAService:
    def __init__(self, definition, nodeset, bindings_path, port=0):
        self.definition = definition
        self.server = Server()
        self.server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/simulatorx/")
        self.server.set_server_name(definition.server_name)
        self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self.server.import_xml(str(nodeset))
        self.bindings = load_bindings(bindings_path)
        self.nodes = validate_bindings(self.server, self.bindings)
        self._lock = threading.RLock()
        access = NodeAccess(lambda key: self._value(key), lambda key, value: self._write(key, value),
                            frozenset(self.nodes))
        self.behavior = definition.model_factory(access)
        if not isinstance(self.behavior, NodeBehavior):
            raise TypeError("OPC UA model factory must return NodeBehavior")
        self.tick_interval = self.behavior.tick_interval
        self.loop = PeriodicLoop(self.tick_interval, self._tick, self._failed)
        self._started = False
        self.failed_reason = None
        self.last_tick = 0.0
        self._last_progress = 0.0
        self._progress = threading.Event()
        service = self.server.iserver.attribute_service
        self._native_write, self._native_read = service.write, service.read
        self._types = {self.nodes[k].nodeid: b.ua_type for k, b in self.bindings.items()}
        service.write, service.read = self._typed_write, self._synchronized_read
        idx = self.server.get_namespace_index(definition.namespace_uri)
        parent = self.server.get_node(ua.NodeId(definition.object_id, idx))

        @uamethod
        def reset_method(parent):
            self.reset()
            return True

        parent.add_method(ua.NodeId(definition.reset_id, idx), ua.QualifiedName("Reset", idx),
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
    def _running(self):
        listener = self.server.bserver._server if self._started else None
        return bool(self._started and not self.failed_reason and self.loop.running
                    and listener and listener.is_serving())

    @property
    def ready(self):
        return self._running and time.monotonic() - self._last_progress < max(2, self.tick_interval * 10)

    def start(self):
        if self._started:
            raise RuntimeError("Simulator already started")
        self.reset()
        self.server.start()
        self._started = True
        self.last_tick = time.monotonic()
        self._last_progress = self.last_tick
        self._progress.clear()
        try:
            self.loop.start()
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self):
        try:
            self.loop.stop()
        finally:
            if self._started:
                self.server.stop()
                self._started = False
            self._progress.set()

    def info(self):
        return {"opcua_endpoint": self.endpoint}

    def check_health(self):
        if not self.ready and self._running:
            # After a scheduling pause the watchdog can wake before the worker.
            # Require fresh progress within a bounded window, rather than kill
            # a live worker solely because its previous timestamp is stale.
            # Do not take the state lock: a stuck step may be holding it.
            self._progress.clear()
            if not self.ready and self._running:
                self._progress.wait(timeout=max(2, self.tick_interval * 10))
        if not self.ready:
            listener = self.server.bserver._server if self._started else None
            raise RuntimeError(self.failed_reason or (
                "OPC UA service stopped: started={}, loop={}, listening={}, tick_age={:.3f}s".format(
                    self._started, self.loop.running, bool(listener and listener.is_serving()),
                    time.monotonic() - self._last_progress)))
        return True

    def step(self, dt):
        with self._lock:
            self.behavior.step(dt)

    def _tick(self):
        with self._lock:
            now = time.monotonic()
            self.step(now - self.last_tick)
            self.last_tick = now
            self._last_progress = time.monotonic()
            self._progress.set()

    def _failed(self, exc):
        self.failed_reason = str(exc) or type(exc).__name__
        self._progress.set()

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
