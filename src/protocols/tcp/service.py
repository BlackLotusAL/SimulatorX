"""One device TCP listener; test management is dispatched by the parent pipe."""
import copy
import threading
from collections import deque
import socket
import time

from framework.sequences import Sequences
from framework.transport import EnvironmentError, Listener


class TCPService:
    def __init__(self, protocol, model, port=0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Port must be in 0..65535")
        self.protocol, self.model = protocol, model
        self.sequences = Sequences()
        self.events = deque(maxlen=2000)
        self._lock = threading.RLock()
        self._generation = 0
        self.failed_reason = None
        self.device = Listener(("127.0.0.1", port), self._device_connection)

    def start(self):
        try:
            self.device.start()
            return self
        except BaseException:
            self.stop()
            raise

    def stop(self):
        self.device.stop()

    def check_health(self):
        if self.failed_reason or not self.device.ready:
            raise EnvironmentError(self.failed_reason or self.device.failed_reason
                                   or "TCP service is not running")

    def info(self):
        return {"endpoint": self.device.address,
                "protocol": type(self.protocol).__name__}

    def _device_connection(self, sock):
        buffer = bytearray()
        with self._lock:
            generation = self._generation
        while not self.device.closing.is_set():
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                return
            buffer.extend(chunk)
            try:
                if len(buffer) > self.protocol.max_buffer:
                    raise ValueError("Protocol receive buffer exceeded")
                while True:
                    decoded = self.protocol.decode_request(buffer)
                    if decoded is None:
                        break
                    request, used = decoded
                    if not 0 < used <= len(buffer):
                        raise RuntimeError("Protocol returned an invalid frame length")
                    del buffer[:used]
                    with self._lock:
                        if generation != self._generation or self.failed_reason:
                            return
                        # Validate before consuming any sequence item.
                        normal = self.model.respond(request)
                        target = self.protocol.sequence_target(request, normal)
                        if target is not None:
                            self.protocol.validate_command(target)
                            self.protocol.validate_fields(target, normal)
                        selection = self.sequences.take(target) if target is not None else {}
                        fields = self._fields(target,
                                              selection["value"] if selection["overridden"] else {},
                                              normal) if target is not None else normal
                        encoded = self.protocol.encode_response(request, fields)
                        event = {**selection, "event": "response", "time": time.monotonic(),
                                 "request_id": request.request_id, "fields": fields,
                                 "bytes": len(encoded), "sent": False}
                        self.events.append(event)
                    # Never hold the state lock while a peer is slow to receive.
                    sock.sendall(encoded)
                    with self._lock:
                        event["sent"] = True
            except ValueError as exc:
                with self._lock:
                    self.events.append({"event": "protocol_error", "error": str(exc),
                                        "time": time.monotonic()})
                return

    def dispatch(self, request):
        operation = request.get("op")
        if operation == "ping":
            return self.info()
        if operation == "health":
            self.check_health()
            return True
        with self._lock:
            if operation == "diagnostics":
                return {"sequences": self.sequences.snapshot(), "events": copy.deepcopy(list(self.events)),
                        "failed_reason": self.failed_reason or self.device.failed_reason}
            if self.failed_reason:
                raise EnvironmentError(self.failed_reason)
            if operation == "set_sequence":
                command, values = request["target"], request["values"]
                self.protocol.validate_command(command)
                if not isinstance(values, list) or len(values) > 10000:
                    raise ValueError("Expected a list of at most 10000 response field objects")
                for patch in values:
                    self._fields(command, patch)
                self.sequences.set(command, values)
                return self.sequences.snapshot(command)[command]
            if operation == "sequences":
                target = request.get("target")
                if target is not None:
                    self.protocol.validate_command(target)
                return self.sequences.snapshot(target)
            if operation == "reset":
                self._generation += 1
                self.device.close_clients()
                try:
                    self.model.reset()
                except Exception as exc:
                    self.failed_reason = "Reset failed: " + str(exc)
                    raise EnvironmentError(self.failed_reason) from exc
                self.sequences.reset()
                self.events.clear()
                return True
            raise ValueError("Unknown TCP control operation")


    def _fields(self, command, patch, fields=None):
        fields = self.model.baseline(command) if fields is None else fields
        fields = copy.deepcopy(fields)
        if not isinstance(patch, dict) or set(patch) - set(fields):
            raise ValueError("Sequence entries must contain known response fields")
        fields.update(copy.deepcopy(patch))
        self.protocol.validate_fields(command, fields)
        return fields
