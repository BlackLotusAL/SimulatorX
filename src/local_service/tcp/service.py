"""Independent request/response TCP simulator with a separate control listener."""
import argparse
import copy
import importlib
import socket
import threading
import time
from collections import deque

from ..common.sequences import Sequences
from ..common.transport import EnvironmentError, Listener, json_handler, serve
from .protocol import DemoProtocol, Protocol, Request


class TCPService:
    def __init__(self, port=0, control_port=0, protocol=None):
        for value in (port, control_port):
            if type(value) is not int or not 0 <= value <= 65535:
                raise ValueError("Ports must be in 0..65535")
        self.protocol = protocol or DemoProtocol()
        self.sequences = Sequences()
        self.events = deque(maxlen=2000)
        self._lock = threading.RLock()
        self._generation = 0
        self.device = Listener(("127.0.0.1", port), self._device_connection)
        self.control = Listener(("127.0.0.1", control_port), json_handler(self.dispatch))

    def start(self):
        try:
            self.device.start()
            self.control.start()
            return self
        except BaseException:
            self.stop()
            raise

    def stop(self):
        failures = []
        for listener in (self.device, self.control):
            try:
                listener.stop()
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise failures[0]

    def check_health(self):
        if not self.device.ready or not self.control.ready:
            raise EnvironmentError(self.device.failed_reason or self.control.failed_reason
                                   or "TCP service is not running")

    def info(self):
        return {"endpoint": self.device.address, "control_endpoint": self.control.address,
                "protocol": type(self.protocol).__name__}

    def _fields(self, command, patch):
        fields = self.protocol.baseline(command)
        if not isinstance(patch, dict) or set(patch) - set(fields):
            raise ValueError("Sequence entries must contain known response fields")
        fields.update(copy.deepcopy(patch))
        self.protocol.validate_fields(command, fields)
        return fields

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
                        raise RuntimeError("Protocol adapter returned an invalid frame length")
                    del buffer[:used]
                    with self._lock:
                        if generation != self._generation:
                            return
                        # Validate before consuming any sequence item.
                        self.protocol.baseline(request.command)
                        selection = self.sequences.take(request.command)
                        fields = self._fields(request.command,
                                              selection["value"] if selection["overridden"] else {})
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
                        "failed_reason": self.device.failed_reason or self.control.failed_reason}
            if operation == "set_sequence":
                command, values = request["target"], request["values"]
                self.protocol.baseline(command)
                if not isinstance(values, list) or len(values) > 10000:
                    raise ValueError("Expected a list of at most 10000 response field objects")
                for patch in values:
                    fields = self._fields(command, patch)
                    self.protocol.encode_response(Request(command, 0, {}), fields)
                self.sequences.set(command, values)
                return self.sequences.snapshot(command)[command]
            if operation == "sequences":
                target = request.get("target")
                if target is not None:
                    self.protocol.baseline(target)
                return self.sequences.snapshot(target)
            if operation == "reset":
                self._generation += 1
                self.device.close_clients()
                self.sequences.reset()
                self.events.clear()
                return True
            raise ValueError("Unknown TCP control operation")


def load_protocol(spec):
    if not spec:
        return DemoProtocol()
    module, separator, factory = spec.partition(":")
    if not separator:
        raise ValueError("Protocol must be module:factory")
    protocol = getattr(importlib.import_module(module), factory)()
    if not isinstance(protocol, Protocol):
        raise ValueError("Protocol factory must return a Protocol instance")
    return protocol


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent SimulatorX TCP response service")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--control-port", type=int, default=0)
    parser.add_argument("--protocol", help="Importable protocol adapter module:factory; default is DemoProtocol")
    parser.add_argument("--ready-file")
    parser.add_argument("--managed", action="store_true")
    args = parser.parse_args(argv)
    try:
        serve(TCPService(args.port, args.control_port, load_protocol(args.protocol)),
              args.ready_file, args.managed)
    except Exception as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
