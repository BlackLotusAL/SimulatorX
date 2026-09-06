"""Shared device observation, manual ownership, pytest execution and recovery."""
from collections import deque
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from framework.source import SOURCE_ROOT, hidden_process_options


class ConflictError(RuntimeError):
    pass


class BaseRuntime:
    def __init__(self, *, artifacts=None, hold=0.8):
        self.contract = None
        self.case_by_id = self.CASE_BY_ID
        self.artifacts = Path(artifacts or SOURCE_ROOT.parent / "artifacts" / "demo").resolve()
        self.hold = hold
        self.instance_id = uuid.uuid4().hex
        self.process = None
        self.device = None
        self.lock = threading.RLock()
        self.action_lock = threading.Lock()
        self.mode = "starting"
        self.closed = False
        self.events = deque(maxlen=2000)
        self.samples = deque(maxlen=1200)
        self.event_seq = self.sample_seq = 0
        self.snapshot = {"connected": False, "time": None, "nodes": {}}
        self.controller_state = {"status": "idle", "fault": None, "message": "等待操作"}
        self.run = None
        self.worker = None
        self.pytest_process = None
        self.observer = None
        self.observer_stop = threading.Event()
        self.observed = threading.Event()
        self.manual_controller = self.manual_injector = None
        self.cancel_at = None
        self.environment_failed = False
        self._event_offset = 0


    def emit(self, kind, message, **data):
        self.publish({"time": time.time(), "kind": kind, "message": message, "case_id": None, **data})


    def publish(self, event):
        with self.lock:
            self.event_seq += 1
            event = {**event, "seq": self.event_seq}
            self.events.append(event)
            if event["kind"] == "controller":
                self.controller_state = event["controller"]
            if event["kind"] == "environment_error":
                self.environment_failed = True
            if self.run and event.get("case_id") in self.run["cases"]:
                case = self.run["cases"][event["case_id"]]
                if event["kind"] == "case_start":
                    case.update(status="running", started=event["time"])
                    self.run["current_case"] = event["case_id"]
                    self.controller_state = {"status": "idle", "fault": None, "message": "正在准备用例"}
                if event["kind"] in ("step", "assertion", "phase", "cleanup", "injected"):
                    case["step"] = event["message"]
                if event["kind"] == "case_result":
                    case.update(status=event["status"], duration=event["duration"])


    def start(self):
        try:
            self._start_environment()
            self.mode = "idle"
            self.emit("environment", "仿真环境就绪，可运行用例或手动操作")
            return self
        except BaseException:
            self.stop()
            raise


    def state(self, after=0, sample_after=0):
        with self.lock:
            return copy.deepcopy({"instance_id": self.instance_id, "mode": self.mode, "endpoint": self.endpoint,
                                  "needs_reset": self.environment_failed,
                                  "snapshot": self.snapshot, "controller": self.controller_state,
                                  "run": self.run, "active_fault": self.manual_injector.active if self.manual_injector else None,
                                  "events": [e for e in self.events if e["seq"] > after],
                                  "samples": [s for s in self.samples if s["seq"] > sample_after],
                                  "event_seq": self.event_seq, "sample_seq": self.sample_seq})


    def _require_idle(self, require_healthy=True):
        if self.closed or self.mode != "idle":
            raise ConflictError("用例或环境清理正在进行，请等待完成")
        if require_healthy and self.environment_failed:
            raise ConflictError("环境发生过连接或控制错误，请先重置环境")


    def reset(self):
        with self.action_lock:
            self._require_idle(require_healthy=False)
            self.mode = "resetting"
            self.worker = threading.Thread(target=self._reset_job, name="demo-reset", daemon=True)
            self.worker.start()


    def _reset_job(self):
        try:
            self._recover()
            self.controller_state = {"status": "idle", "fault": None, "message": "环境已重置"}
            self.environment_failed = False
        except Exception as exc:
            self.emit("environment_error", "环境恢复失败：" + str(exc))
        finally:
            self.mode = "closed" if self.closed else "idle"


    def start_run(self, case_ids):
        if (not isinstance(case_ids, list) or not case_ids or
                any(not isinstance(key, str) or key not in self.case_by_id for key in case_ids) or
                len(case_ids) != len(set(case_ids))):
            raise ValueError("请选择不重复的内置用例")
        with self.action_lock:
            self._require_idle()
            if not self.snapshot["connected"]:
                raise ConflictError("设备已断连，请先重置环境")
            if self.controller_state.get("status") == "running":
                raise ConflictError("手动业务正在运行，请先停止")
            run_id = uuid.uuid4().hex
            directory = self.artifacts / run_id
            directory.mkdir(parents=True)
            with self.lock:
                self.run = {"id": run_id, "status": "running", "started": time.time(), "ended": None,
                            "current_case": None, "exit_code": None,
                            "cases": {key: {"status": "pending", "step": "等待运行", "duration": 0}
                                      for key in case_ids}, "artifacts": str(directory)}
                self.mode = "running"
                self.cancel_at = None
                self.environment_failed = False
            self.worker = threading.Thread(target=self._run_suite, args=(case_ids, directory),
                                           name="demo-pytest", daemon=True)
            self.worker.start()
            return run_id


    def cancel(self, environment=False):
        with self.lock:
            if self.run and self.mode == "running":
                if environment:
                    self.environment_failed = True
                if self.cancel_at is None:
                    self.cancel_at = time.monotonic()
                    (Path(self.run["artifacts"]) / "cancel").touch()
                    self.emit("cancel", "已请求停止，等待当前用例清理")


    def _tail_events(self, path):
        if not path.exists():
            return
        with path.open("rb") as stream:
            stream.seek(self._event_offset)
            for line in stream:
                if not line.endswith(b"\n"):
                    break
                self.publish(json.loads(line.decode("utf-8")))
                self._event_offset += len(line)


    def _run_suite(self, case_ids, directory):
        result = "error"
        forced = False
        try:
            self._stop_manual()
            self._reset_environment()
            event_path = directory / "events.jsonl"
            self._event_offset = 0
            case_file, config_file, plugin, manifest, environment = self.suite_config(directory)
            args = [sys.executable, "-X", "utf8", "-m", "pytest", "-c", str(config_file), "-p", plugin,
                    "--rootdir", str(case_file.parent), "--confcutdir", str(case_file.parent),
                    "--demo-connection", str(manifest), "--demo-events", str(event_path),
                    "--demo-cancel-file", str(directory / "cancel"), "--demo-hold", str(self.hold),
                    "--junitxml", str(directory / "junit.xml"), "--tb=short", "-q"]
            args += [str(case_file) + "::" + self.case_by_id[key]["test"] for key in case_ids]
            self.emit("environment", "pytest 已获得本轮仿真控制权")
            with (directory / "pytest.log").open("w", encoding="utf-8") as log:
                self.pytest_process = subprocess.Popen(args, cwd=str(SOURCE_ROOT.parent), env=environment,
                                                       stdin=subprocess.DEVNULL,
                                                       stdout=log, stderr=subprocess.STDOUT,
                                                       **hidden_process_options())
                while self.pytest_process.poll() is None:
                    self._tail_events(event_path)
                    if self.cancel_at is not None and time.monotonic() - self.cancel_at >= 10:
                        forced = True
                        self.pytest_process.kill()
                        self.pytest_process.wait(timeout=3)
                        self.emit("environment_error", "取消超时，已终止本轮 pytest，将重建设备")
                        break
                    time.sleep(0.05)
                code = self.pytest_process.wait(timeout=3)
                self._tail_events(event_path)
            self.run["exit_code"] = code
            statuses = [case["status"] for case in self.run["cases"].values()]
            if self.environment_failed or forced or code not in (0, 1, 2):
                result = "error"
            elif self.cancel_at is not None:
                result = "cancelled"
            elif code == 0 and all(status == "passed" for status in statuses):
                result = "passed"
            elif code == 1 and "failed" in statuses:
                result = "failed"
        except Exception as exc:
            self.emit("environment_error", "本轮执行异常：" + str(exc))
            if self.pytest_process and self.pytest_process.poll() is None:
                self.pytest_process.kill()
                self.pytest_process.wait(timeout=3)
        finally:
            self.pytest_process = None
            self.end_suite()
            self.mode = "cleaning"
            try:
                if not self.closed:
                    self._recover(rebuild=forced or self.environment_failed or result == "error")
            except Exception as exc:
                result = "error"
                self.emit("environment_error", "最终环境恢复失败：" + str(exc))
            with self.lock:
                for case in self.run["cases"].values():
                    if case["status"] in ("pending", "running"):
                        case["status"] = "cancelled" if result == "cancelled" else "not_run"
                self.run.update(status=result, ended=time.time())
                if self.controller_state.get("status") == "running":
                    self.controller_state = {"status": "stopped", "fault": None,
                                             "message": "本轮控制器已停止，环境已清理"}
                if self.controller_state.get("fault"):
                    self.controller_state = {**self.controller_state,
                                             "message": "上例：" + self.controller_state["message"]}
                self.mode = "closed" if self.closed else "idle"
            self.emit("run_result", "本轮运行结束", status=result)


    def stop(self):
        self.cancel()
        self.closed = True
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=30)
            if self.worker.is_alive():
                raise RuntimeError("Demo worker did not exit")
        errors = []
        for action in (self._stop_manual, self._stop_observer,
                       self.device.stop if self.device else lambda: None):
            try:
                action()
            except Exception as exc:
                errors.append(str(exc))
        self.mode = "closed"
        if errors:
            raise RuntimeError("; ".join(errors))


    def end_suite(self):
        pass
