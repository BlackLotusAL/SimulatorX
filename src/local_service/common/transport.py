"""Bounded local RPC and owned listener lifecycle; independent of OPC UA."""
import json
import math
import os
import socket
import socketserver
import threading
from pathlib import Path

MAX_MESSAGE = 8 * 1024 * 1024


def diagnostic_value(value):
    """Keep invalid native numeric inputs observable without producing invalid JSON."""
    if isinstance(value, dict):
        return {key: diagnostic_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [diagnostic_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _reject_constant(text):
    raise ValueError("Non-finite JSON number: " + text)


def _parse_float(text):
    value = float(text)
    if not math.isfinite(value):
        _reject_constant(text)
    return value


class EnvironmentError(RuntimeError):
    """An unusable test environment, rather than an injected device error."""


def receive_exact(sock, size):
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise ConnectionError("Connection closed before a complete message")
        chunks.extend(part)
    return bytes(chunks)


def read_json(sock):
    with sock.makefile("rb") as stream:
        line = stream.readline(MAX_MESSAGE + 1)
    if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
        raise ValueError("Expected one bounded newline-delimited JSON message")
    value = json.loads(line, parse_constant=_reject_constant, parse_float=_parse_float)
    if not isinstance(value, dict):
        raise ValueError("JSON request/response must be an object")
    return value


def send_json(sock, value):
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(encoded) > MAX_MESSAGE:
        raise ValueError("Control message is too large")
    sock.sendall(encoded)


def rpc(endpoint, request, timeout=2.0):
    family = socket.AF_UNIX if isinstance(endpoint, str) else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(endpoint if isinstance(endpoint, str) else tuple(endpoint))
            send_json(sock, request)
            response = read_json(sock)
    except (OSError, ValueError) as exc:
        raise EnvironmentError("Control communication failed: " + str(exc)) from exc
    if not response.get("ok"):
        kind = EnvironmentError if response.get("environment_error") else ValueError
        raise kind(response.get("error", "Control request failed"))
    return response["result"]


class _Threads(socketserver.ThreadingMixIn):
    daemon_threads = False
    block_on_close = True


class _TCP(_Threads, socketserver.TCPServer):
    allow_reuse_address = False


if hasattr(socketserver, "UnixStreamServer"):
    class _Unix(_Threads, socketserver.UnixStreamServer):
        pass
else:
    _Unix = None


class Listener:
    def __init__(self, address, handler):
        self.address = address
        self.handler = handler
        self.server = self.thread = None
        self.failed_reason = None
        self.closing = threading.Event()
        self._clients = set()
        self._lock = threading.Lock()
        self._socket_identity = None

    def start(self):
        if self.server is not None:
            raise RuntimeError("Listener already started")
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                with owner._lock:
                    if owner.closing.is_set():
                        return
                    owner._clients.add(self.request)
                try:
                    self.request.settimeout(2.0)
                    owner.handler(self.request)
                except (ConnectionError, socket.timeout, OSError):
                    # A peer may disconnect at any point, including after consuming a result.
                    pass
                except Exception as exc:
                    owner.failed_reason = str(exc)
                finally:
                    with owner._lock:
                        owner._clients.discard(self.request)

        cls = _Unix if isinstance(self.address, str) else _TCP
        if cls is None:
            raise EnvironmentError("Unix domain sockets are unavailable on this platform")
        self.closing.clear()
        self.server = cls(self.address, Handler)
        self.address = self.server.server_address
        if isinstance(self.address, str):
            info = os.stat(self.address)
            self._socket_identity = (info.st_dev, info.st_ino)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        return self

    @property
    def ready(self):
        return bool(self.thread and self.thread.is_alive() and not self.failed_reason
                    and not self.closing.is_set())

    def close_clients(self):
        with self._lock:
            for sock in list(self._clients):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def stop(self):
        self.closing.set()
        if self.server is not None:
            if self.thread and self.thread.is_alive():
                self.server.shutdown()
            self.close_clients()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=5)
                if self.thread.is_alive():
                    raise EnvironmentError("Listener thread did not stop")
            self.server = None
        if self._socket_identity:
            try:
                info = os.stat(self.address)
                if (info.st_dev, info.st_ino) == self._socket_identity:
                    Path(self.address).unlink()
            except FileNotFoundError:
                pass
            self._socket_identity = None


def json_handler(dispatch):
    def handle(sock):
        try:
            result = dispatch(read_json(sock))
            reply = {"ok": True, "result": result}
        except EnvironmentError as exc:
            reply = {"ok": False, "environment_error": True, "error": str(exc)}
        except (ValueError, KeyError, TypeError) as exc:
            reply = {"ok": False, "error": str(exc)}
        send_json(sock, reply)
    return handle


def serve(service, ready_file=None, managed=False):
    """Stop on parent stdin EOF, explicit input, interrupt, or failed health."""
    import sys
    stopped = threading.Event()
    try:
        service.start()
        info = service.info()
        if ready_file:
            path = Path(ready_file)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(info), encoding="utf-8")
            temporary.replace(path)
        print(json.dumps({"event": "ready", **info}), flush=True)
        if managed:
            def parent():
                sys.stdin.readline()
                stopped.set()
            threading.Thread(target=parent, daemon=True).start()
        while not stopped.wait(0.05):
            service.check_health()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
