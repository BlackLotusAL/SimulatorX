"""One finite-travel rotary axis. No transport, wall clock, or vendor ABI here."""
import math
from dataclasses import asdict, dataclass
from enum import IntEnum


class Result(IntEnum):
    OK = 0
    INVALID_ARGUMENT = 1
    NOT_ENABLED = 2
    NOT_HOMED = 3
    BUSY = 4
    LIMIT = 5
    FAULT = 6
    UNKNOWN_FUNCTION = 7


FUNCTIONS = ("Enable", "Disable", "Home", "MoveAbsolute", "MoveRelative",
             "Stop", "ClearFault", "GetPosition", "GetState")
CONTROL_ERROR = -(2 ** 31)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class AxisProfile:
    minimum: float = -180.0
    maximum: float = 180.0
    initial: float = 0.0
    home: float = 0.0
    max_speed: float = 90.0
    acceleration: float = 180.0
    deceleration: float = 180.0
    tick_interval: float = 0.01

    def __post_init__(self):
        if not all(finite(v) for v in asdict(self).values()):
            raise ValueError("Axis profile values must be finite numbers")
        if not self.minimum < self.maximum:
            raise ValueError("minimum must be less than maximum")
        if not all(self.minimum <= v <= self.maximum for v in (self.initial, self.home)):
            raise ValueError("initial and home must be within the travel limits")
        if min(self.max_speed, self.acceleration, self.deceleration, self.tick_interval) <= 0:
            raise ValueError("Speed, acceleration, deceleration and tick interval must be positive")
        if not math.isfinite(self.maximum - self.minimum):
            raise ValueError("Travel range is too large")


class _Trajectory:
    def __init__(self, start, target, speed, acceleration, deceleration):
        self.start, self.target = start, target
        self.direction = 1 if target >= start else -1
        distance = abs(target - start)
        self.a, self.d = acceleration, deceleration
        self.peak = min(speed, math.sqrt(distance * 2 / (1 / acceleration + 1 / deceleration)))
        self.ta, self.td = self.peak / acceleration, self.peak / deceleration
        ramp_distance = self.peak * (self.ta + self.td) / 2
        self.tc = max(0.0, (distance - ramp_distance) / self.peak) if self.peak else 0.0
        self.duration = self.ta + self.tc + self.td

    def sample(self, t):
        if t >= self.duration:
            return self.target, 0.0, True
        if t < self.ta:
            distance, speed = self.a * t * t / 2, self.a * t
        elif t < self.ta + self.tc:
            distance = self.peak * self.ta / 2 + self.peak * (t - self.ta)
            speed = self.peak
        else:
            remaining = self.duration - t
            return (self.target - self.direction * self.d * remaining * remaining / 2,
                    self.direction * self.d * remaining, False)
        return self.start + self.direction * distance, self.direction * speed, False


class _Brake:
    def __init__(self, position, velocity, deceleration):
        self.position, self.velocity, self.deceleration = position, velocity, deceleration
        self.direction = 1 if velocity >= 0 else -1
        self.duration = abs(velocity) / deceleration

    def sample(self, t):
        t = min(t, self.duration)
        return (self.position + self.velocity * t - self.direction * self.deceleration * t * t / 2,
                self.velocity - self.direction * self.deceleration * t,
                t >= self.duration)


class RotaryAxis:
    """Caller serializes access and advances simulation time explicitly."""
    def __init__(self, profile=None):
        self.profile = profile or AxisProfile()
        self.reset()

    def reset(self):
        self.position = self.target = self.profile.initial
        self.velocity = 0.0
        self.enabled = self.homed = self.busy = self.done = False
        self.alarm = 0
        self.phase = "idle"
        self.faults = {"stalled": False, "positive_limit": False, "negative_limit": False}
        self._trajectory = None
        self._elapsed = 0.0
        self._speed = self.profile.max_speed

    def snapshot(self):
        return {"axis": 1, "position": self.position, "velocity": self.velocity,
                "target": self.target, "enabled": self.enabled, "homed": self.homed,
                "busy": self.busy, "done": self.done, "alarm": self.alarm,
                "phase": self.phase, "faults": dict(self.faults)}

    def validate(self, function, args):
        if function not in FUNCTIONS:
            return Result.UNKNOWN_FUNCTION
        if not isinstance(args, dict):
            return Result.INVALID_ARGUMENT
        allowed = {"axis"}
        if function == "Home":
            allowed.add("speed")
        if function in ("MoveAbsolute", "MoveRelative"):
            allowed.update(("angle", "speed"))
        if set(args) - allowed or type(args.get("axis", 1)) is not int or args.get("axis", 1) != 1:
            return Result.INVALID_ARGUMENT
        if function in ("Home", "MoveAbsolute", "MoveRelative"):
            speed = args.get("speed", self.profile.max_speed)
            if not finite(speed) or not 0 < speed <= self.profile.max_speed:
                return Result.INVALID_ARGUMENT
            if function != "Home" and not finite(args.get("angle")):
                return Result.INVALID_ARGUMENT
            if self.alarm:
                return Result.FAULT
            if not self.enabled:
                return Result.NOT_ENABLED
            if self.busy:
                return Result.BUSY
            if function != "Home" and not self.homed:
                return Result.NOT_HOMED
            target = self._target(function, args)
            if not self.profile.minimum <= target <= self.profile.maximum:
                return Result.LIMIT
        if function in ("ClearFault", "Enable") and (
                self.faults["positive_limit"] or self.faults["negative_limit"]):
            return Result.LIMIT
        if function == "Enable" and self.alarm:
            return Result.FAULT
        return Result.OK

    def _target(self, function, args):
        if function == "Home":
            return self.profile.home
        return args["angle"] + (self.position if function == "MoveRelative" else 0)

    def execute(self, function, args=None):
        args = {} if args is None else args
        code = self.validate(function, args)
        if code != Result.OK:
            return code
        if function == "Enable":
            self.enabled = True
        elif function == "Disable":
            self._cancel()
            self.enabled = self.homed = False
        elif function == "ClearFault":
            self.alarm = 0
        elif function in ("Home", "MoveAbsolute", "MoveRelative"):
            self.target = self._target(function, args)
            self._speed = args.get("speed", self.profile.max_speed)
            self.phase = "homing" if function == "Home" else "moving"
            if function == "Home":
                self.homed = False
            self.busy, self.done = True, False
            self._plan()
        elif function == "Stop" and self.busy:
            if self.velocity == 0:
                self._cancel()
            else:
                self.phase, self.done = "stopping", False
                self._trajectory = _Brake(self.position, self.velocity, self.profile.deceleration)
                self._elapsed = 0.0
                self.target = self._trajectory.sample(self._trajectory.duration)[0]
        return Result.OK

    def _plan(self):
        self._trajectory = _Trajectory(self.position, self.target, self._speed,
                                       self.profile.acceleration, self.profile.deceleration)
        self._elapsed = 0.0
        self.velocity = 0.0
        self.advance(0)

    def _cancel(self):
        self.busy = self.done = False
        self.velocity = 0.0
        self.target = self.position
        self.phase = "idle"
        self._trajectory = None

    def advance(self, dt):
        if not finite(dt) or dt < 0:
            raise ValueError("dt must be finite and nonnegative")
        if not self.busy or self.faults["stalled"]:
            return
        self._elapsed += dt
        self.position, self.velocity, finished = self._trajectory.sample(self._elapsed)
        # Clamp rounding at the boundaries, not invalid commands.
        self.position = min(self.profile.maximum, max(self.profile.minimum, self.position))
        if finished:
            if self.phase == "homing":
                self.homed = True
            self.done = self.phase != "stopping"
            self.busy, self.velocity = False, 0.0
            self.phase, self._trajectory = "idle", None

    def set_fault(self, name, active):
        if name not in self.faults or type(active) is not bool:
            raise ValueError("Expected stalled/positive_limit/negative_limit and a Boolean")
        if self.faults[name] == active:
            return
        self.faults[name] = active
        if name != "stalled" and active:
            self._cancel()
            self.alarm = 1 if name == "positive_limit" else 2
        elif name == "stalled" and self.busy:
            if self.phase == "stopping":
                self._cancel()
            elif active:
                self.velocity = 0.0
            else:
                self._plan()
