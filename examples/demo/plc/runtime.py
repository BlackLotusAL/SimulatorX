"""Own the local PLC, observation loop, manual actors and one pytest process."""
import math
from pathlib import Path
import threading
import time

from protocols.opcua.bindings import describe
from framework.runtime import DeviceRuntime
from framework.config import DEFAULT_DEVICE
from demo.common.environment import demo_environment, write_connection
from .catalog import CASE_BY_ID
from .control import Controller, FaultInjector, PLCConnection


from demo.common.runtime import BaseRuntime, ConflictError


class DemoRuntime(BaseRuntime):
    CASE_BY_ID = CASE_BY_ID

    @property
    def endpoint(self):
        return self.device.subsystems["vacuum"].hardware["chamber_plc"].endpoints.get("opcua") if self.device else None


    def _new_manual_actors(self):
        self.manual_controller = Controller(self.endpoint, self.emit, contract=self.contract)
        self.manual_injector = FaultInjector(self.endpoint, self.emit, contract=self.contract)


    def _start_environment(self):
        self.device = DeviceRuntime.from_config(DEFAULT_DEVICE, ["vacuum/chamber_plc"])
        hardware = self.device.subsystems["vacuum"].hardware["chamber_plc"]
        self.device.start()
        self.process = hardware.process
        self.contract = {"bindings_path": str(hardware.bindings_path),
                         **{name: getattr(hardware.definition, name)
                            for name in ("namespace_uri", "object_id", "reset_id")}}
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
                with PLCConnection(self.endpoint, self.contract) as plc:
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


    def _reset_environment(self):
        with PLCConnection(self.endpoint, self.contract) as plc:
            plc.reset()


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
            self.emit("environment", "正在重建本轮自建的 PLC 环境")
            self._stop_observer()
            if self.process:
                try:
                    self.device.stop()
                except Exception as exc:
                    # An already-dead service is expected on this recovery path.
                    # Rebuild only after its manager has released the process.
                    if self.process.process is not None:
                        raise
                    self.emit("environment", "旧 PLC 已回收，正在替换失败环境", detail=str(exc))
            # Closing the old PLC also releases any in-flight manual I/O.
            # Do not expose a replacement while a writer still exists.
            for actor in (self.manual_injector, self.manual_controller):
                if actor and actor.thread:
                    actor.thread.join(timeout=3)
                    if actor.thread.is_alive():
                        raise RuntimeError("Manual actor still running after PLC shutdown")
            if self.closed:
                return
            self._start_environment()
        else:
            self._new_manual_actors()
        self.environment_failed = False
        self.emit("environment", "环境恢复完成，节点基线与 Good 质量已确认")


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
                with PLCConnection(self.endpoint, self.contract) as plc:
                    plc.command("vacuum.command" if action == "stop" else "vacuum.valve_command",
                                {"open": 1, "close": 2, "stop": 3}[action])
                self.emit("control", {"open": "手动开阀完成", "close": "手动关阀完成", "stop": "手动停止并关阀完成"}[action])
            else:
                raise ValueError("未知控制操作")


    def suite_config(self, directory):
        return (Path(__file__).with_name("cases.py"), Path(__file__).resolve().parents[1] / "pytest.ini",
                "demo.plc.pytest_plugin", write_connection(directory, self.endpoint, self.contract),
                demo_environment())
