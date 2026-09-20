"""Business positioning through the published native ABI."""
import ctypes as c
import math
import sys

from .operation import BusinessError, OperationSUT, positive


class AxisState(c.Structure):
    _fields_ = [(name, c.c_double) for name in ("position_deg", "velocity_deg_s", "target_deg")] + [
        (name, c.c_uint32) for name in ("enabled", "homed", "busy", "done", "alarm", "faults")]


class MotionSUT(OperationSUT):
    def __init__(self, library_path, *, position_tolerance=0.01, **options):
        super().__init__(**options)
        if sys.platform != "linux":
            raise RuntimeError("Reference native SDK requires Linux/WSL")
        self.position_tolerance = positive(position_tolerance, "position_tolerance")
        self._sdk = c.CDLL(str(library_path))
        for name in ("Enable", "Disable", "Stop", "ClearFault"):
            self._declare(name, [c.c_int32])
        self._declare("Home", [c.c_int32, c.c_double])
        for name in ("MoveAbsolute", "MoveRelative"):
            self._declare(name, [c.c_int32, c.c_double, c.c_double])
        self._declare("GetPosition", [c.c_int32, c.POINTER(c.c_double)])
        self._declare("GetState", [c.c_int32, c.POINTER(AxisState)])

    def _declare(self, name, arguments):
        function = getattr(self._sdk, "SX_" + name)
        function.argtypes, function.restype = arguments, c.c_int32

    def _call(self, name, *arguments):
        result = getattr(self._sdk, "SX_" + name)(1, *arguments)
        if result:
            code = "communication_error" if result == -(2 ** 31) else "sdk_error"
            raise BusinessError(code, "%s returned %s" % (name, result))

    def start_move(self, target_deg, speed_deg_s):
        if type(target_deg) not in (int, float) or not math.isfinite(target_deg):
            raise ValueError("target_deg must be finite")
        positive(speed_deg_s, "speed_deg_s")
        return self._start(lambda: self._move(target_deg, speed_deg_s))

    def _wait_position(self, target, stage):
        while True:
            self._checkpoint()
            state = AxisState()
            self._call("GetState", c.byref(state))
            observed = {name: getattr(state, name) for name, _ in AxisState._fields_}
            observed["stage"] = stage
            if any(not math.isfinite(observed[key]) for key in
                   ("position_deg", "velocity_deg_s", "target_deg")):
                raise BusinessError("invalid_position", "Nonfinite SDK feedback")
            self._observe(observed)
            if state.alarm or state.faults & 6:
                raise BusinessError("motion_alarm", "Axis alarm or limit active")
            if (state.homed and state.done and not state.busy and state.velocity_deg_s == 0
                    and abs(state.position_deg - target) <= self.position_tolerance):
                return
            self._pause()

    def _move(self, target, speed):
        completed = False
        try:
            self._call("Enable")
            self._checkpoint()
            self._call("Home", speed)
            self._wait_position(0, "homing")
            self._checkpoint()
            self._call("MoveAbsolute", target, speed)
            self._wait_position(target, "positioning")
            completed = True
        finally:
            if not completed:
                self._call("Stop")
