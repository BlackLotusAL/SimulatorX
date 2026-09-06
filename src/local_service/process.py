"""Own only the independent service process created by this manager."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .common.runtime import hidden_process_options, source_environment
from .common.transport import EnvironmentError


class ServiceProcess:
    def __init__(self, kind, *, profile=None, port=0, protocol=None):
        if kind not in ("sdk", "tcp"):
            raise ValueError("Expected sdk or tcp")
        self.kind, self.profile, self.port, self.protocol = kind, profile, port, protocol
        self.process = self.client = self._temporary = self._log = None

    def start(self, timeout=20):
        if self.process is not None:
            raise RuntimeError("Manager already owns a process")
        if self.kind == "sdk" and sys.platform != "linux":
            raise EnvironmentError("SDK .so integration requires Linux; run it in Linux or WSL")
        self._temporary = tempfile.TemporaryDirectory(prefix="sx-")
        directory = Path(self._temporary.name)
        ready = directory / "ready.json"
        self._log = (directory / "service.log").open("w+", encoding="utf-8")
        args = [sys.executable, "-X", "utf8", "-m", "local_service." + self.kind + ".service",
                "--managed", "--ready-file", str(ready)]
        if self.kind == "sdk":
            args += ["--control-socket", str(directory / "control.sock"),
                     "--sdk-socket", str(directory / "sdk.sock"),
                     "--error-file", str(directory / "native.errors")]
            if self.profile:
                args += ["--profile", str(Path(self.profile).resolve())]
        else:
            args += ["--port", str(self.port)]
            if self.protocol:
                args += ["--protocol", self.protocol]
        try:
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=self._log,
                                            stderr=subprocess.STDOUT, text=True,
                                            encoding="utf-8", env=source_environment(),
                                            **hidden_process_options())
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise EnvironmentError("Service startup failed:\n" + self.log())
                if ready.exists():
                    info = json.loads(ready.read_text(encoding="utf-8"))
                    if self.kind == "sdk":
                        from .sdk.client import SDKClient
                        self.client = SDKClient(info["control_socket"])
                    else:
                        from .tcp.client import TCPClient
                        self.client = TCPClient(tuple(info["control_endpoint"]))
                    self.client.check_health()
                    return self
                time.sleep(0.025)
            raise EnvironmentError("Service startup timed out:\n" + self.log())
        except BaseException:
            self.stop()
            raise

    def log(self):
        if self._log is None:
            return ""
        self._log.flush()
        # Read with a separate descriptor; seeking the child's inherited output
        # descriptor could make a concurrent write overwrite earlier log bytes.
        return Path(self._log.name).read_text(encoding="utf-8", errors="replace")

    def stop(self):
        failures = []
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    self.process.stdin.write("stop\n")
                    self.process.stdin.flush()
                    self.process.wait(timeout=8)
                except (OSError, subprocess.TimeoutExpired):
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=3)
                    failures.append(EnvironmentError("Service required forced termination"))
            if self.process.returncode and not failures:
                failures.append(EnvironmentError("Service exited unexpectedly:\n" + self.log()))
            if self.process.stdin:
                self.process.stdin.close()
            self.process = None
        if self._log:
            self._log.close()
            self._log = None
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None
        if failures:
            raise failures[0]

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
