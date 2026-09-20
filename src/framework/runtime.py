"""Ordered composition of hardware instances, independent of device implementations."""
from dataclasses import dataclass, field

from .config import load_device, load_protocol
from .contracts import Hardware


class LifecycleError(RuntimeError):
    def __init__(self, operation, failures):
        self.failures = failures
        super().__init__(operation + ": " + "; ".join(str(error) for error in failures))


@dataclass
class Subsystem:
    id: str
    hardware: dict = field(default_factory=dict)


class DeviceRuntime:
    def __init__(self, identity, rows):
        self.id = identity
        self.subsystems = {}
        self._active = []
        self.state = "new"
        for sid, hid, config in rows:
            hardware = load_protocol(config.type).create(config)
            if not isinstance(hardware, Hardware):
                raise TypeError("Protocol host must return Hardware: " + config.type)
            self.subsystems.setdefault(sid, Subsystem(sid)).hardware[hid] = hardware

    @classmethod
    def from_config(cls, path, select=None):
        return cls(*load_device(path, select))

    def hardware(self):
        for subsystem in self.subsystems.values():
            yield from subsystem.hardware.values()

    def start(self):
        if self.state != "new":
            raise RuntimeError("Create a new runtime to start after stop or failure")
        try:
            # Validate every selected instance before any service starts.
            for hardware in self.hardware():
                hardware.validate()
            for hardware in self.hardware():
                self._active.append(hardware)
                hardware.start()
                hardware.check_health()
            self.state = "running"
            return self
        except BaseException as cause:
            self.state = "failed"
            try:
                self.stop()
            except Exception as cleanup:
                raise LifecycleError("Startup and rollback failed", [cause, cleanup]) from cause
            raise

    def _running(self):
        if self.state != "running":
            raise RuntimeError("Device is not reusable: " + self.state)

    def check_health(self):
        self._running()
        try:
            for hardware in self._active:
                hardware.check_health()
        except Exception:
            self.state = "failed"
            raise
        return True

    def reset(self):
        self._running()
        failures = []
        for hardware in self._active:
            try:
                hardware.reset()
            except Exception as exc:
                failures.append(RuntimeError(hardware.identity + ": " + str(exc)))
        if failures:
            self.state = "failed"
            raise LifecycleError("Reset failed; environment must not be reused", failures)

    def diagnostics(self):
        result = {}
        for hardware in self._active:
            try:
                result[hardware.identity] = hardware.diagnostics()
            except Exception as exc:
                result[hardware.identity] = {"diagnostic_error": str(exc)}
        return result

    def stop(self):
        failures = []
        while self._active:
            hardware = self._active.pop()
            try:
                hardware.stop()
            except Exception as exc:
                failures.append(RuntimeError(hardware.identity + ": " + str(exc)))
        if failures or self.state == "failed":
            self.state = "failed"
        else:
            self.state = "stopped"
        if failures:
            raise LifecycleError("Cleanup failed", failures)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
