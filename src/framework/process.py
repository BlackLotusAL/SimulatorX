"""Own only the independent service process created by this manager."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .source import hidden_process_options, source_environment
from .transport import EnvironmentError


class ServiceProcess:
    def __init__(self, module, arguments, connect, pipe_control=False):
        self.module, self.arguments, self.connect = module, arguments, connect
        self.process = self.client = self._temporary = self._log = None
        self.info = None
        self.pipe_control, self.requests = pipe_control, None

    def check_health(self):
        if self.process is None or self.process.poll() is not None:
            raise EnvironmentError("Service process is not running:\n" + self.log())
        return self.client.check_health()

    def start(self, timeout=20):
        if self.process is not None:
            raise RuntimeError("Manager already owns a process")
        self._temporary = tempfile.TemporaryDirectory(prefix="sx-")
        directory = Path(self._temporary.name)
        ready = directory / "ready.json"
        self._log = (directory / "service.log").open("w+", encoding="utf-8")
        try:
            args = [sys.executable, "-X", "utf8", "-m", self.module,
                    "--managed", "--ready-file", str(ready), *self.arguments(directory)]
            if self.pipe_control:
                args.append("--pipe-control")
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE if self.pipe_control else self._log,
                                            stderr=self._log if self.pipe_control else subprocess.STDOUT, text=True,
                                            encoding="utf-8", env=source_environment(),
                                            **hidden_process_options())
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise EnvironmentError("Service startup failed:\n" + self.log())
                if ready.exists():
                    info = json.loads(ready.read_text(encoding="utf-8"))
                    self.info = info
                    if self.pipe_control:
                        from .pipe import ProcessRequests
                        self.requests = ProcessRequests(self.process)
                        self.client = self.connect(info, self.requests)
                    else:
                        self.client = self.connect(info)
                    self.client.check_health()
                    return self
                time.sleep(0.025)
            raise EnvironmentError("Service startup timed out:\n" + self.log())
        except BaseException as cause:
            try:
                self.stop()
            except Exception as cleanup:
                raise EnvironmentError("Startup failed: " + str(cause) + "; cleanup failed: " + str(cleanup)) from cause
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
        if self.client is not None:
            try:
                self.client.close()
            except Exception as exc:
                failures.append(exc)
            finally:
                self.client = None
        if self.process is not None:
            if self.requests is not None:
                self.requests.close()
            if self.process.poll() is None:
                try:
                    if self.requests is not None and (self.requests.failed or self.requests.thread.is_alive()):
                        raise OSError("Pipe request did not finish")
                    if not self.process.stdin.closed:
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
            if self.process.returncode:
                failures.append(EnvironmentError("Service exited unexpectedly:\n" + self.log()))
            if self.requests is not None:
                try:
                    self.requests.finish()
                except Exception as exc:
                    failures.append(exc)
                self.requests = None
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except OSError as exc:
                    failures.append(exc)
            if self.process.stdout:
                self.process.stdout.close()
            self.process = None
        if self._log:
            self._log.close()
            self._log = None
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None
        if failures:
            raise EnvironmentError("Service cleanup failed: " + "; ".join(str(exc) for exc in failures))

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
