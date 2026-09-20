"""Small asynchronous business-operation lifecycle shared by the example SUTs."""
import copy
import math
import threading
import time
import uuid
from datetime import datetime, timezone


class BusinessError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")
    return value


class OperationSUT:
    def __init__(self, *, timeout=10, poll_interval=0.05, io_timeout=1):
        self.timeout = positive(timeout, "timeout")
        self.poll_interval = positive(poll_interval, "poll_interval")
        self.io_timeout = positive(io_timeout, "io_timeout")
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._closed = False
        self._thread = None
        self._operations = {}
        self._events = []

    def _start(self, action):
        with self._lock:
            if self._closed:
                raise RuntimeError("SUT is closed")
            if self._thread is not None and self._operations[self._current]["state"] == "running":
                raise RuntimeError("SUT is busy")
            identity = uuid.uuid4().hex
            self._current = identity
            self._operations[identity] = dict(operation_id=identity, state="running",
                                              error_code=None, message=None,
                                              observed={}, last_synced_at=None)
            self._deadline = time.monotonic() + self.timeout
            self._event("started")
            self._thread = threading.Thread(target=self._run, args=(action,), daemon=True)
            self._thread.start()
            return identity

    def _event(self, message):
        with self._lock:
            self._events.append(dict(operation_id=self._current, message=message,
                                     at=datetime.now(timezone.utc).isoformat()))
            self._events = self._events[-2000:]

    def _run(self, action):
        code = message = None
        try:
            self._checkpoint()
            action()
            self._checkpoint()
        except BusinessError as exc:
            code, message = exc.code, str(exc)
        except (OSError, EOFError) as exc:
            code, message = "communication_error", str(exc)
        except Exception as exc:
            code, message = "internal_error", repr(exc)
        with self._lock:
            self._operations[self._current].update(
                state="failed" if code else "succeeded", error_code=code, message=message)
            self._event(code or "succeeded")

    def _observe(self, values):
        with self._lock:
            self._operations[self._current].update(
                observed=copy.deepcopy(values), last_synced_at=datetime.now(timezone.utc).isoformat())

    def _checkpoint(self):
        if self._cancel.is_set():
            raise BusinessError("cancelled", "Operation cancelled during SUT shutdown")
        if time.monotonic() >= self._deadline:
            raise BusinessError("timeout", "Business operation deadline exceeded")

    def _pause(self):
        self._checkpoint()
        self._cancel.wait(min(self.poll_interval, max(0, self._deadline - time.monotonic())))
        self._checkpoint()

    def get_status(self, operation_id):
        with self._lock:
            return copy.deepcopy(self._operations[operation_id])

    def diagnostics(self):
        with self._lock:
            return copy.deepcopy(dict(operations=self._operations, events=self._events))

    def close(self):
        with self._lock:
            self._closed = True
            self._cancel.set()
            thread = self._thread
        if thread:
            thread.join(timeout=max(5, 4 * self.io_timeout + 1))
            if thread.is_alive():
                raise RuntimeError("SUT worker did not stop; environment must not be reused")
