"""Own the local PLC, observation loop, manual actors and one pytest process."""
from collections import deque
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from local_service.common.runtime import SOURCE_ROOT, hidden_process_options, source_environment
from local_service.plc.bindings import describe
from local_service.plc.process import SimulatorProcess
from .catalog import CASE_BY_ID
from .control import Controller, FaultInjector, PLCConnection


class ConflictError(RuntimeError):
    pass


class DemoRuntime:
    def __init__(self, *, profile=None, artifacts=None, hold=0.8):
        self.profile = Path(profile or Path(__file__).with_name("profile.json")).resolve()
        self.artifacts = Path(artifacts or SOURCE_ROOT.parent / "artifacts" / "demo").resolve()
        self.hold = hold
        self.instance_id = uuid.uuid4().hex
        self.process = None
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

    @property
    def endpoint(self):
        return self.process.endpoint if self.process else None

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
            self._start_plc()
            self.mode = "idle"
            self.emit("environment", "仿真环境就绪，可运行用例或手动操作")
            return self
        except BaseException:
            self.stop()
            raise

    def _new_manual_actors(self):
        self.manual_controller = Controller(self.endpoint, self.emit)
        self.manual_injector = FaultInjector(self.endpoint, self.emit)

    def _start_plc(self):
        self.process = SimulatorProcess(profile=self.profile).start()
        self._new_manual_actors()
        self.observer_stop = threading.Event()
        self.observed.clear()
        self.observer = threading.Thread(target=self._observe, args=(self.observer_stop,),
                                         name="demo-observer", daemon=True)
        self.observer.start()
        if not self.observed.wait(5):
            raise RuntimeError("无法建立 PLC 观测连接")

    def _observe(self, stopped):
        notified = False
        while not stopped.is_set():
            try:
                with PLCConnection(self.endpoint) as plc:
                    while not stopped.is_set():
                        values = plc.read()
                        nodes = {key: {**describe(value), "good": value.StatusCode.is_good()}
                                 for key, value in values.items()}
                        pressure = values["vacuum.pressure_pa"].Value.Value
                        valid = (values["vacuum.pressure_pa"].StatusCode.is_good()
                                 and type(pressure) in (int, float) and math.isfinite(pressure) and pressure > 0)
                        now = time.time()
                        with self.lock:
                            self.snapshot = {"connected": True, "time": now, "nodes": nodes}
                            self.sample_seq += 1
                            self.samples.append({"seq": self.sample_seq, "time": now,
                                                 "pressure": pressure if valid else None})
                        self.observed.set()
                        notified = False
                        stopped.wait(0.1)
            except Exception as exc:
                with self.lock:
                    self.snapshot = {"connected": False, "time": time.time(), "nodes": {}, "error": str(exc)}
                    self.sample_seq += 1
                    self.samples.append({"seq": self.sample_seq, "time": time.time(), "pressure": None})
                if not stopped.is_set() and not notified:
                    self.emit("environment_error", "PLC 连接中断，观测数据已失效", detail=repr(exc))
                    notified = True
                    if self.mode == "running":
                        self.cancel(environment=True)
                stopped.wait(0.5)

    def _stop_observer(self):
        self.observer_stop.set()
        if self.observer:
            self.observer.join(timeout=4)
            if self.observer.is_alive():
                raise RuntimeError("PLC observer did not exit")
            self.observer = None
        with self.lock:
            self.snapshot = {"connected": False, "time": time.time(), "nodes": {}}

    def _stop_manual(self):
        failures = []
        for actor in (self.manual_injector, self.manual_controller):
            if actor:
                try:
                    actor.stop()
                except Exception as exc:
                    failures.append(str(exc))
        if failures:
            raise RuntimeError("; ".join(failures))

    def _reset_plc(self):
        with PLCConnection(self.endpoint) as plc:
            plc.reset()

    def _recover(self, rebuild=False):
        try:
            self._stop_manual()
        except Exception:
            rebuild = True
        if not rebuild:
            try:
                self._reset_plc()
            except Exception:
                rebuild = True
        if rebuild:
            self.emit("environment", "正在重建本轮自建的 PLC 环境")
            self._stop_observer()
            if self.process:
                self.process.stop()
            # Closing the old PLC also releases any in-flight manual I/O.
            # Do not expose a replacement while a writer still exists.
            for actor in (self.manual_injector, self.manual_controller):
                if actor and actor.thread:
                    actor.thread.join(timeout=3)
                    if actor.thread.is_alive():
                        raise RuntimeError("Manual actor still running after PLC shutdown")
            if self.closed:
                return
            self._start_plc()
        else:
            self._new_manual_actors()
        self.environment_failed = False
        self.emit("environment", "环境恢复完成，节点基线与 Good 质量已确认")

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

    def manual(self, action, fault=None):
        with self.action_lock:
            self._require_idle()
            if not self.snapshot["connected"]:
                raise ConflictError("PLC 已断连，请先重置环境")
            if action in ("pump", "vent"):
                if self.manual_injector.active:
                    raise ConflictError("请先重置故障条件，再启动控制器")
                if self.manual_controller.snapshot()["status"] == "running":
                    raise ConflictError("控制器正在运行，可停止操作或注入故障")
                self.manual_controller.start(action)
            elif action == "inject":
                if self.manual_injector.active:
                    raise ConflictError("已有注入条件，请先重置环境")
                self.manual_injector.inject(fault)
            elif action in ("open", "close", "stop"):
                if action == "stop":
                    self.manual_controller.stop()
                with PLCConnection(self.endpoint) as plc:
                    plc.command("vacuum.command" if action == "stop" else "vacuum.valve_command",
                                {"open": 1, "close": 2, "stop": 3}[action])
                self.emit("control", {"open": "手动开阀完成", "close": "手动关阀完成", "stop": "手动停止并关阀完成"}[action])
            else:
                raise ValueError("未知控制操作")

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
                any(not isinstance(key, str) or key not in CASE_BY_ID for key in case_ids) or
                len(case_ids) != len(set(case_ids))):
            raise ValueError("请选择不重复的内置用例")
        with self.action_lock:
            self._require_idle()
            if not self.snapshot["connected"]:
                raise ConflictError("PLC 已断连，请先重置环境")
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
            self._reset_plc()
            event_path = directory / "events.jsonl"
            self._event_offset = 0
            case_file = Path(__file__).with_name("cases.py")
            args = [sys.executable, "-X", "utf8", "-m", "pytest", "-p", "pytest_plugin", "-p", "demo.pytest_plugin",
                    "--opcua-endpoint", self.endpoint, "--demo-events", str(event_path),
                    "--demo-cancel-file", str(directory / "cancel"), "--demo-hold", str(self.hold),
                    "--junitxml", str(directory / "junit.xml"), "--tb=short", "-q"]
            args += [str(case_file) + "::" + CASE_BY_ID[key]["test"] for key in case_ids]
            environment = source_environment()
            environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            environment.pop("PYTEST_ADDOPTS", None)
            environment.pop("PYTEST_PLUGINS", None)
            self.emit("environment", "pytest 已获得本轮仿真控制权")
            with (directory / "pytest.log").open("w", encoding="utf-8") as log:
                self.pytest_process = subprocess.Popen(args, cwd=str(SOURCE_ROOT.parent), env=environment,
                                                       stdout=log, stderr=subprocess.STDOUT,
                                                       **hidden_process_options())
                while self.pytest_process.poll() is None:
                    self._tail_events(event_path)
                    if self.cancel_at is not None and time.monotonic() - self.cancel_at >= 10:
                        forced = True
                        self.pytest_process.kill()
                        self.pytest_process.wait(timeout=3)
                        self.emit("environment_error", "取消超时，已终止本轮 pytest，将重建 PLC")
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
                       self.process.stop if self.process else lambda: None):
            try:
                action()
            except Exception as exc:
                errors.append(str(exc))
        self.mode = "closed"
        if errors:
            raise RuntimeError("; ".join(errors))
