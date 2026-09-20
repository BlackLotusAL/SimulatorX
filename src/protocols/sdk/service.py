"""SDK host: serialized model calls, periodic advancement and return overrides."""
import threading
import time
from collections import deque
from pathlib import Path

from framework.loop import PeriodicLoop
from framework.sequences import Sequences
from framework.transport import EnvironmentError, Listener, diagnostic_value, json_handler

class SDKService:
    def __init__(self, control_socket, sdk_socket, model, adapter, error_file=None, clock=time.monotonic):
        self.control_socket = str(control_socket) if isinstance(control_socket, (str, Path)) else tuple(control_socket)
        self.sdk_socket = str(sdk_socket) if isinstance(sdk_socket, (str, Path)) else tuple(sdk_socket)
        if isinstance(self.sdk_socket, str) and self.control_socket == self.sdk_socket:
            raise ValueError("SDK and control sockets must use different paths")
        if not error_file and not isinstance(self.sdk_socket, str):
            raise ValueError("TCP SDK transport requires an error file")
        self.error_file = str(error_file) if error_file else self.sdk_socket + ".errors"
        self.model, self.adapter = model, adapter
        self.sequences = Sequences()
        self.clock = clock
        self._lock = threading.RLock()
        self.loop = PeriodicLoop(model.tick_interval, self._tick, self._failed)
        self._last_time = clock()
        self.failed_reason = None
        self.events = deque(maxlen=2000)
        self.control = Listener(self.control_socket, json_handler(self.dispatch))
        self.native = Listener(self.sdk_socket, lambda sock: self.adapter.handle(sock, self.invoke))

    def start(self):
        if self.loop.thread is not None:
            raise RuntimeError("SDK service already started")
        try:
            # Never truncate another run's diagnostic file.
            with Path(self.error_file).open("x", encoding="utf-8"):
                pass
            self.control.start()
            self.native.start()
            self.control_socket, self.sdk_socket = self.control.address, self.native.address
            self._last_time = self.clock()
            self.loop.start()
            return self
        except BaseException:
            self.stop()
            raise

    def stop(self):
        failures = []
        for listener in (self.native, self.control):
            try:
                listener.stop()
            except Exception as exc:
                failures.append(exc)
        try:
            self.loop.stop()
        except Exception as exc:
            failures.append(exc)
        if failures:
            raise failures[0]

    def check_health(self):
        if self.failed_reason or not self.control.ready or not self.native.ready or not (
                self.loop.running):
            raise EnvironmentError(self.failed_reason or self.control.failed_reason
                                   or self.native.failed_reason or "SDK service is not running")

    def _check_clients(self):
        path = Path(self.error_file)
        if path.exists() and path.stat().st_size:
            raise EnvironmentError("Native SDK control failure: " + path.read_text(encoding="utf-8")[-4096:])

    def info(self):
        return {"control_socket": self.control_socket, "sdk_socket": self.sdk_socket,
                "error_file": self.error_file, **self.adapter.info(self.model)}

    def _sync(self):
        now = self.clock()
        self.model.advance(max(0.0, now - self._last_time))
        self._last_time = now

    def _tick(self):
        with self._lock:
            self._sync()

    def _failed(self, exc):
        self.failed_reason = str(exc)

    def invoke(self, sdk, function, args):
        self.adapter.target(sdk + "." + function)
        if not isinstance(args, dict):
            raise ValueError("SDK arguments must be an object")
        with self._lock:
            if self.failed_reason:
                raise EnvironmentError(self.failed_reason)
            self._sync()
            normal = self.model.validate(function, args)
            selection = self.sequences.take(sdk + "." + function)
            override = selection["value"] if selection["overridden"] else 0
            # A failure override is evaluated BEFORE executing any command.
            # A success override still goes through normal validation.
            code = override if override != 0 else int(normal)
            if code == 0:
                code = int(self.model.execute(function, args))
            state = self.model.snapshot()
            self.events.append({**selection, "event": "call", "time": self._last_time,
                                "args": diagnostic_value(args), "normal_return": int(normal),
                                "return_value": code, "state": state})
            return {"return_value": code, "state": state}

    def dispatch(self, request):
        operation = request.get("op")
        if operation == "ping":
            return self.info()
        if operation == "health":
            self.check_health()
            self._check_clients()
            return True
        with self._lock:
            if operation == "diagnostics":
                return {"state": self.model.snapshot(), "sequences": self.sequences.snapshot(),
                        "events": list(self.events), "failed_reason": self.failed_reason,
                        "native_errors": Path(self.error_file).read_text(encoding="utf-8")[-4096:]
                        if Path(self.error_file).exists() else ""}
            self._check_clients()
            if self.failed_reason:
                raise EnvironmentError(self.failed_reason)
            self._sync()
            if operation == "snapshot":
                return self.model.snapshot()
            if operation == "call":
                sdk = request.get("sdk") or self.adapter.info(self.model)["reference_sdk"]
                return self.invoke(sdk, request["function"], request.get("args", {}))
            if operation == "set_fault":
                self.model.set_fault(request["name"], request["active"])
                self.events.append({"event": "fault", "name": request["name"],
                                    "active": request["active"], "time": self._last_time})
                return self.model.snapshot()
            if operation == "set_sequence":
                target, values = request["target"], request["values"]
                self.adapter.target(target)
                self.adapter.validate_returns(values)
                self.sequences.set(target, values)
                return self.sequences.snapshot(target)[target]
            if operation == "sequences":
                target = request.get("target")
                if target is not None:
                    self.adapter.target(target)
                return self.sequences.snapshot(target)
            if operation == "reset":
                try:
                    self.model.reset()
                except Exception as exc:
                    self.failed_reason = "Reset failed: " + str(exc)
                    raise EnvironmentError(self.failed_reason) from exc
                self.sequences.reset()
                self.events.clear()
                self._last_time = self.clock()
                return True
            raise ValueError("Unknown SDK control operation")
