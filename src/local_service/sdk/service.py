"""Independent SDK service: timed rotary-axis model plus return overrides."""
import argparse
import json
import struct
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from .motion import AxisProfile, CONTROL_ERROR, FUNCTIONS, Result, RotaryAxis
from ..common.sequences import Sequences
from ..common.transport import EnvironmentError, Listener, diagnostic_value, json_handler, receive_exact, serve

# Reference ABI wire format: network endian; no native structs or pointers on the wire.
REQUEST = struct.Struct("!4sIidd")
RESPONSE = struct.Struct("!4sidddIIIIII")
MAGIC = b"SX01"


class SDKService:
    def __init__(self, control_socket, sdk_socket, profile=None, error_file=None, clock=time.monotonic):
        self.control_socket, self.sdk_socket = str(control_socket), str(sdk_socket)
        if self.control_socket == self.sdk_socket:
            raise ValueError("SDK and control sockets must use different paths")
        self.error_file = str(error_file) if error_file else self.sdk_socket + ".errors"
        self.axis = RotaryAxis(profile)
        self.sequences = Sequences()
        self.clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._last_time = clock()
        self.failed_reason = None
        self.events = deque(maxlen=2000)
        self.control = Listener(self.control_socket, json_handler(self.dispatch))
        self.native = Listener(self.sdk_socket, self._native_call)

    def start(self):
        if self._thread is not None:
            raise RuntimeError("SDK service already started")
        try:
            # Never truncate another run's diagnostic file.
            with Path(self.error_file).open("x", encoding="utf-8"):
                pass
            self.control.start()
            self.native.start()
            self._last_time = self.clock()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="simulatorx-axis", daemon=True)
            self._thread.start()
            return self
        except BaseException:
            self.stop()
            raise

    def stop(self):
        self._stop.set()
        failures = []
        for listener in (self.native, self.control):
            try:
                listener.stop()
            except Exception as exc:
                failures.append(exc)
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                failures.append(EnvironmentError("Axis thread did not stop"))
            self._thread = None
        if failures:
            raise failures[0]

    def check_health(self):
        if self.failed_reason or not self.control.ready or not self.native.ready or not (
                self._thread and self._thread.is_alive()):
            raise EnvironmentError(self.failed_reason or self.control.failed_reason
                                   or self.native.failed_reason or "SDK service is not running")

    def _check_clients(self):
        path = Path(self.error_file)
        if path.exists() and path.stat().st_size:
            raise EnvironmentError("Native SDK control failure: " + path.read_text(encoding="utf-8")[-4096:])

    def info(self):
        return {"control_socket": self.control_socket, "sdk_socket": self.sdk_socket,
                "error_file": self.error_file, "profile": asdict(self.axis.profile),
                "reference_sdk": "rotary", "functions": list(FUNCTIONS)}

    def _sync(self):
        now = self.clock()
        self.axis.advance(max(0.0, now - self._last_time))
        self._last_time = now

    def _run(self):
        while not self._stop.wait(self.axis.profile.tick_interval):
            try:
                with self._lock:
                    self._sync()
            except Exception as exc:
                self.failed_reason = str(exc)
                return

    @staticmethod
    def _target(target):
        if not isinstance(target, str) or target.count(".") != 1:
            raise ValueError("Target must be SDK.Function, e.g. rotary.MoveAbsolute")
        sdk, function = target.split(".")
        if sdk != "rotary" or function not in FUNCTIONS:
            raise ValueError("Unknown SDK function target")
        return sdk, function

    def invoke(self, sdk, function, args):
        self._target(sdk + "." + function)
        if not isinstance(args, dict):
            raise ValueError("SDK arguments must be an object")
        with self._lock:
            if self.failed_reason:
                raise EnvironmentError(self.failed_reason)
            self._sync()
            normal = self.axis.validate(function, args)
            selection = self.sequences.take(sdk + "." + function)
            override = selection["value"] if selection["overridden"] else 0
            # A failure override is evaluated BEFORE executing any command.
            # A success override still goes through normal validation.
            code = override if override != 0 else int(normal)
            if code == 0:
                code = int(self.axis.execute(function, args))
            state = self.axis.snapshot()
            self.events.append({**selection, "event": "call", "time": self._last_time,
                                "args": diagnostic_value(args), "normal_return": int(normal),
                                "return_value": code, "state": state})
            return {"return_value": code, "state": state}

    def _native_call(self, sock):
        magic, opcode, axis, first, second = REQUEST.unpack(receive_exact(sock, REQUEST.size))
        if magic != MAGIC or not 1 <= opcode <= len(FUNCTIONS):
            return
        function = FUNCTIONS[opcode - 1]
        args = {"axis": axis}
        if function in ("MoveAbsolute", "MoveRelative"):
            args.update(angle=first, speed=second)
        elif function == "Home":
            args["speed"] = first
        result = self.invoke("rotary", function, args)
        state = result["state"]
        flags = sum((1 << i) for i, key in enumerate(
            ("stalled", "positive_limit", "negative_limit")) if state["faults"][key])
        sock.sendall(RESPONSE.pack(MAGIC, result["return_value"], state["position"],
                                   state["velocity"], state["target"], state["enabled"],
                                   state["homed"], state["busy"], state["done"], state["alarm"], flags))

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
                return {"state": self.axis.snapshot(), "sequences": self.sequences.snapshot(),
                        "events": list(self.events), "failed_reason": self.failed_reason,
                        "native_errors": Path(self.error_file).read_text(encoding="utf-8")[-4096:]
                        if Path(self.error_file).exists() else ""}
            self._check_clients()
            if self.failed_reason:
                raise EnvironmentError(self.failed_reason)
            self._sync()
            if operation == "snapshot":
                return self.axis.snapshot()
            if operation == "call":
                return self.invoke(request.get("sdk", "rotary"), request["function"], request.get("args", {}))
            if operation == "set_fault":
                self.axis.set_fault(request["name"], request["active"])
                self.events.append({"event": "fault", "name": request["name"],
                                    "active": request["active"], "time": self._last_time})
                return self.axis.snapshot()
            if operation == "set_sequence":
                target, values = request["target"], request["values"]
                self._target(target)
                if not isinstance(values, list) or len(values) > 10000 or any(
                        type(v) is not int or not CONTROL_ERROR < v < 2 ** 31 for v in values):
                    raise ValueError("Expected at most 10000 int32 return codes; INT32_MIN is reserved")
                self.sequences.set(target, values)
                return self.sequences.snapshot(target)[target]
            if operation == "sequences":
                target = request.get("target")
                if target is not None:
                    self._target(target)
                return self.sequences.snapshot(target)
            if operation == "reset":
                self.axis.reset()
                self.sequences.reset()
                self.events.clear()
                self._last_time = self.clock()
                return True
            raise ValueError("Unknown SDK control operation")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent SimulatorX rotary SDK service")
    parser.add_argument("--control-socket", required=True)
    parser.add_argument("--sdk-socket", required=True)
    parser.add_argument("--error-file")
    parser.add_argument("--profile", help="AxisProfile JSON, in degrees and seconds")
    parser.add_argument("--ready-file")
    parser.add_argument("--managed", action="store_true")
    args = parser.parse_args(argv)
    try:
        profile = AxisProfile(**json.loads(Path(args.profile).read_text(encoding="utf-8"))) if args.profile else None
        service = SDKService(args.control_socket, args.sdk_socket, profile, args.error_file)
        serve(service, args.ready_file, args.managed)
    except Exception as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
