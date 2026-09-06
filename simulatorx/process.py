"""Own only the simulator subprocess created by this manager."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from opcua import Client
from .bindings import load_bindings


def hidden_process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class SimulatorProcess:
    def __init__(self, opcua_port=0):
        self.opcua_port = opcua_port
        self.process = None
        self.endpoint = None
        self._temporary = self._log = None

    def start(self, timeout=20):
        if self.process is not None:
            raise RuntimeError("This manager already owns a simulator process")
        self._temporary = tempfile.TemporaryDirectory(prefix="simulatorx-")
        directory = Path(self._temporary.name)
        ready = directory / "ready.json"
        self._log = (directory / "service.log").open("w+", encoding="utf-8")
        args = [sys.executable, "-X", "utf8", "-m", "simulatorx", "--managed", "--fast",
                "--opcua-port", str(self.opcua_port), "--ready-file", str(ready)]
        try:
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=self._log, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", **hidden_process_options())
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    self._log.seek(0)
                    raise RuntimeError("Simulator startup failed:\n" + self._log.read())
                if ready.exists():
                    self.endpoint = json.loads(ready.read_text(encoding="utf-8"))["opcua_endpoint"]
                    probe = Client(self.endpoint, timeout=1)
                    try:
                        probe.connect()
                        load_bindings()["vacuum.pressure_pa"].resolve(probe).get_value()
                    finally:
                        probe.disconnect()
                    return self
                time.sleep(0.05)
            raise TimeoutError("Simulator process startup timed out")
        except BaseException:
            self.stop()
            raise

    def stop(self):
        if self.process:
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
            if self.process.stdin:
                self.process.stdin.close()
            self.process = None
        if self._log:
            self._log.close()
            self._log = None
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
