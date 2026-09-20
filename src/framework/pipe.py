"""Bounded parent/child requests over subprocess stdin/stdout, without a listener."""
import json
import queue
import threading

from .messages import (EnvironmentError, MAX_MESSAGE,
                       reject_constant, parse_finite_float)


def encode(value):
    line = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
    if len(line) > MAX_MESSAGE:
        raise ValueError("Pipe message is too large")
    return line


def decode(line):
    if not line or len(line) > MAX_MESSAGE or not line.endswith("\n"):
        raise ValueError("Expected a bounded complete pipe message")
    value = json.loads(line, parse_constant=reject_constant, parse_float=parse_finite_float)
    if not isinstance(value, dict):
        raise ValueError("Pipe message must be an object")
    return value


class ProcessRequests:
    def __init__(self, process, timeout=2.0):
        self.process, self.timeout = process, timeout
        self.closed = self.failed = False
        self._lock = threading.Lock()
        self._jobs = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="simulatorx-pipe", daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            line, result = job
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
                response = decode(self.process.stdout.readline(MAX_MESSAGE + 1))
                if type(response.get("ok")) is not bool:
                    raise ValueError("Invalid pipe response")
                result.put(response)
            except Exception as exc:
                result.put(EnvironmentError("Process communication failed: " + str(exc)))
                return

    def request(self, request):
        line = encode(request)
        if not self._lock.acquire(timeout=self.timeout):
            raise EnvironmentError("Process request is busy")
        try:
            if self.closed or self.failed:
                raise EnvironmentError("Process communication is closed or failed")
            result = queue.Queue()
            self._jobs.put((line, result))
            try:
                response = result.get(timeout=self.timeout)
            except queue.Empty:
                self.failed = True
                raise EnvironmentError("Process request timed out; environment cannot be reused")
            if isinstance(response, Exception):
                self.failed = True
                raise response
            if not response["ok"]:
                kind = EnvironmentError if response.get("environment_error") else ValueError
                raise kind(response.get("error", "Process request failed"))
            return response.get("result")
        finally:
            self._lock.release()

    def close(self):
        if not self.closed:
            self.closed = True
            self._jobs.put(None)
        self.thread.join(timeout=0.1)

    def finish(self):
        # Call after the child exits so blocked reads/writes have been released.
        self.close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise EnvironmentError("Process request worker did not stop")


def serve_requests(service, incoming, outgoing):
    """One ordered exchange per request; EOF/stop terminates the child."""
    while True:
        line = incoming.readline(MAX_MESSAGE + 1)
        if not line or line == "stop\n":
            return
        request = decode(line)
        try:
            response = {"ok": True, "result": service.dispatch(request)}
        except EnvironmentError as exc:
            response = {"ok": False, "environment_error": True, "error": str(exc)}
        except (ValueError, KeyError, TypeError) as exc:
            response = {"ok": False, "error": str(exc)}
        try:
            reply = encode(response)
        except ValueError as exc:
            reply = encode({"ok": False, "environment_error": True, "error": str(exc)})
        outgoing.write(reply)
        outgoing.flush()
