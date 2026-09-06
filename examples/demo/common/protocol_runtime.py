import json
from pathlib import Path
import secrets
import threading
import time
from types import SimpleNamespace

from demo.common.runtime import BaseRuntime, ConflictError
from framework.config import DEFAULT_DEVICE
from framework.runtime import DeviceRuntime
from demo.common.environment import demo_environment
from .actors import ManualActor


class ProtocolRuntime(BaseRuntime):
    """Shared lifecycle; protocol modules supply device-specific hooks."""
    stop_is_local = False
    extra_control_operations = {}

    def prepare_hardware(self):
        pass

    def initial_baseline(self):
        return None

    def observed_values(self, diagnostics):
        raise NotImplementedError

    def verify_baseline(self):
        raise NotImplementedError

    def validate_manual(self, action, target, speed):
        if action not in self.actions:
            raise ValueError("未知控制操作")

    def __init__(self, *, library=None, **options):
        self.CASE_BY_ID = {row["id"]: row for row in self.catalog}
        super().__init__(**options)
        self.library = str(Path(library).resolve()) if library else None
        self.bridge_url = None
        self.token = None
        self.control_lock = threading.RLock()
        self.hardware = None
        self.manual_injector = SimpleNamespace(active=None)

    @property
    def endpoint(self):
        return self.hardware.endpoints.get(self.kind) if self.hardware else None

    def _start_environment(self):
        self.device = DeviceRuntime.from_config(DEFAULT_DEVICE, [self.selection])
        self.hardware = next(self.device.hardware())
        self.prepare_hardware()
        self.device.start()
        self.process = self.hardware.process
        self.baseline = self.initial_baseline()
        self._reset_environment()
        self.observer_stop = threading.Event()
        self.observed.clear()
        self.observer = threading.Thread(target=self._observe, daemon=True, name="demo-" + self.kind)
        self.observer.start()
        if not self.observed.wait(5):
            raise RuntimeError("无法建立设备观测连接")

    def publish(self, event):
        super().publish(event)
        if event["kind"] == "case_start":
            with self.lock:
                self.controller_state["observed"] = {}

    def _observe(self):
        next_health = 0
        while not self.observer_stop.is_set():
            try:
                if time.monotonic() >= next_health:
                    self.hardware.check_health()
                    next_health = time.monotonic() + 1
                diagnostics = self.hardware.client.diagnostics()
                if diagnostics.get("failed_reason") or diagnostics.get("native_errors"):
                    raise RuntimeError(diagnostics.get("failed_reason") or diagnostics["native_errors"])
                values = self.observed_values(diagnostics)
                now = time.time()
                with self.lock:
                    self.snapshot = {"connected": True, "time": now, "nodes": {}, "values": values,
                                     "sequences": diagnostics["sequences"], "traffic": diagnostics["events"][-50:]}
                    self.sample_seq += 1
                    self.samples.append({"seq": self.sample_seq, "time": now,
                        **{key: values.get(key) for key in self.sample_fields}})
                self.observed.set()
            except Exception as exc:
                with self.lock:
                    self.snapshot = {"connected": False, "time": time.time(), "nodes": {}, "values": {}}
                    self.sample_seq += 1
                    self.samples.append({"seq": self.sample_seq, "time": time.time(),
                                         "position": None, "velocity": None, "target": None, "measurement": None})
                if not self.environment_failed and not self.observer_stop.is_set():
                    self.emit("environment_error", "设备断连，请重置环境", detail=str(exc))
                    self.cancel(environment=True)
            self.observer_stop.wait(0.1)

    def _stop_observer(self):
        self.observer_stop.set()
        if self.observer:
            self.observer.join(timeout=6)
            if self.observer.is_alive():
                raise RuntimeError("Device observer did not exit")
            self.observer = None
        with self.lock:
            self.snapshot = {"connected": False, "time": time.time(), "nodes": {}, "values": {}}

    def _stop_manual(self):
        actor = self.manual_controller
        if actor:
            try:
                actor.stop()
            except Exception as exc:
                self.emit("environment_error", "手动控制器未正常停止，请重置环境", detail=str(exc))
                raise
            self.manual_controller = None

    def _reset_environment(self):
        client = self.hardware.client
        client.check_health()
        client.reset()
        if client.returns.snapshot():
            raise RuntimeError("响应序列未清空")
        self.verify_baseline()
        self.manual_injector.active = None

    def _recover(self, rebuild=False):
        try:
            self._stop_manual()
        except Exception:
            rebuild = True
        if not rebuild:
            try:
                self._reset_environment()
            except Exception:
                rebuild = True
        if rebuild:
            self._stop_observer()
            try:
                self.device.stop()
            except Exception:
                if self.process and self.process.process is not None:
                    raise
            if self.closed:
                return
            self._start_environment()
        self.environment_failed = False
        self.emit("environment", "设备已恢复，基线与响应序列已验证")

    def manual(self, action, fault=None, target=30, speed=90):
        if not isinstance(action, str):
            raise ValueError("控制操作必须为字符串")
        with self.action_lock:
            self._require_idle()
            if not self.snapshot["connected"]:
                raise ConflictError("设备已断连，请先重置")
            if action == "inject":
                if self.manual_injector.active:
                    raise ConflictError("已有故障条件，请先重置")
                self.inject(self.hardware.client, fault)
                self.manual_injector.active = fault
                self.emit("injected", "已预置故障，仅影响后续调用", fault=fault)
                return
            self.validate_manual(action, target, speed)
            if self.controller_state.get("status") == "running" and action != "stop":
                raise ConflictError("手动业务正在执行，可停止或注入故障")
            self._stop_manual()
            if self.stop_is_local and action == "stop":
                self.controller_state = {"status": "stopped", "fault": None, "message": "检测已停止", "observed": {}}
                return
            descriptor = self.descriptor()
            descriptor.update(action=action, target=target, speed=speed)
            self.controller_state = {"status": "running", "fault": None, "message": "正在启动业务", "observed": {}}
            try:
                self.manual_controller = ManualActor(descriptor, self.launch_environment(), self.emit, self.worker_module)
            except Exception as exc:
                self.controller_state = {"status": "fault", "fault": "environment", "message": str(exc)}
                self.emit("environment_error", "手动控制启动失败", detail=str(exc))
                raise

    def descriptor(self):
        return {"kind": self.kind, "endpoint": self.endpoint, "library": self.library}

    def launch_environment(self):
        return {}

    def suite_config(self, directory):
        self.token = secrets.token_urlsafe(32)
        descriptor = {**self.descriptor(), "control_url": self.bridge_url, "token": self.token}
        manifest = directory / "connection.json"
        manifest.write_text(json.dumps(descriptor), encoding="utf-8")
        return (self.protocol_directory / "cases.py", Path(__file__).resolve().parents[1] / "pytest.ini",
                self.plugin_module, manifest, {**demo_environment(), **self.launch_environment()})

    def end_suite(self):
        with self.control_lock:
            self.token = None

    def control(self, token, data):
        with self.control_lock:
            if not self.token or not secrets.compare_digest(token or "", self.token) or self.mode != "running":
                raise ConflictError("测试控制令牌已失效")
            operation = data.get("op")
            if not isinstance(operation, str):
                raise ValueError("测试控制操作必须为字符串")
            allowed = {"reset": set(), "health": set(), "diagnostics": set(), "sequences": set(),
                       "set_sequence": {"target", "values"}}
            allowed.update(self.extra_control_operations)
            if operation not in allowed or set(data) != {"op"} | allowed[operation]:
                raise ValueError("不允许的测试控制操作")
            return self.hardware.client.request(operation, **{k: v for k, v in data.items() if k != "op"})
