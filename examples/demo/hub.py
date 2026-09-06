"""Lazy independent devices with a single global automatic-run reservation."""
from pathlib import Path
import threading
from framework.source import SOURCE_ROOT
from demo.plc.runtime import DemoRuntime
from demo.plc.catalog import CASES
from demo.sdk.runtime import SDKRuntime
from demo.tcp.runtime import TCPRuntime
from demo.common.runtime import ConflictError

RUNTIMES = {"sdk": SDKRuntime, "tcp": TCPRuntime}
CATALOGS = {kind: runtime.catalog for kind, runtime in RUNTIMES.items()}


class DemoHub:
    def __init__(self, *, artifacts=None, library=None, hold=0.8, plc=None):
        self.artifacts = Path(artifacts or SOURCE_ROOT.parent / "artifacts" / "demo").resolve()
        self.library, self.hold = library, hold
        self.runtimes = {"plc": plc or DemoRuntime(artifacts=self.artifacts / "plc", hold=hold)}
        self.errors = {}
        self.starting = set()
        self.lock = threading.RLock()
        self.jobs = []
        self.closed = False
        self.url = None

    def start(self):
        self.runtimes["plc"].start()

    def catalog(self, kind):
        self.validate(kind)
        return CASES if kind == "plc" else CATALOGS[kind]

    @staticmethod
    def validate(kind):
        if kind not in ("plc", "sdk", "tcp"):
            raise ValueError("未知演示协议")

    def get(self, kind):
        self.validate(kind)
        with self.lock:
            if kind in self.starting or kind not in self.runtimes:
                raise ConflictError("演示尚未就绪，请初始化或重试")
            return self.runtimes[kind]

    def summary(self):
        with self.lock:
            active = next((kind for kind, runtime in self.runtimes.items()
                           if runtime.mode in ("running", "cleaning")), None)
            return {"active_run": active, "demos": [
                {"id": kind, "status": "starting" if kind in self.starting else "error" if kind in self.errors else
                 "ready" if kind in self.runtimes else "not_started", "error": self.errors.get(kind),
                 "mode": self.runtimes[kind].mode if kind in self.runtimes else None}
                for kind in ("plc", "sdk", "tcp")]}

    def initialize(self, kind):
        self.validate(kind)
        with self.lock:
            if self.closed:
                raise ConflictError("演示已关闭")
            if kind in self.runtimes or kind in self.starting:
                return
            self.errors.pop(kind, None)
            self.starting.add(kind)
            job = threading.Thread(target=self._initialize, args=(kind,), daemon=True, name="demo-init-" + kind)
            self.jobs.append(job)
            job.start()

    def _initialize(self, kind):
        runtime = RUNTIMES[kind](library=self.library, artifacts=self.artifacts / kind, hold=self.hold)
        try:
            runtime.bridge_url = self.url + "/api/demos/" + kind + "/control"
            runtime.start()
            with self.lock:
                self.runtimes[kind] = runtime
        except Exception as exc:
            with self.lock:
                self.errors[kind] = str(exc)
        finally:
            with self.lock:
                self.starting.discard(kind)

    def start_run(self, kind, case_ids):
        with self.lock:
            if self.summary()["active_run"]:
                raise ConflictError("已有协议正在执行自动用例，请等待本轮结束")
            return self.get(kind).start_run(case_ids)

    def stop(self):
        with self.lock:
            self.closed = True
        for job in self.jobs:
            job.join(timeout=80)
            if job.is_alive():
                raise RuntimeError("Demo initialization did not exit")
        failures = []
        for runtime in self.runtimes.values():
            try:
                runtime.stop()
            except Exception as exc:
                failures.append(str(exc))
        if failures:
            raise RuntimeError("; ".join(failures))
