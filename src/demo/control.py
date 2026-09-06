"""A small example SUT and native fault writers, separate from the PLC model."""
import math
import threading
import time

from opcua import Client, ua
from local_service.plc.bindings import load_bindings, read_values, reset_nodes


def quiet_event(kind, message, **data):
    pass


class PLCConnection:
    def __init__(self, endpoint):
        self.client = Client(endpoint, timeout=1)
        self.nodes = {}

    def __enter__(self):
        try:
            self.client.connect()
            self.nodes = {key: binding.resolve(self.client) for key, binding in load_bindings().items()}
            return self
        except BaseException:
            try:
                self.client.disconnect()
            except Exception:
                pass
            raise

    def __exit__(self, *exc):
        self.client.disconnect()

    def read(self):
        return read_values(self.client, self.nodes)

    def write(self, key, value):
        self.nodes[key].set_value(value, load_bindings()[key].ua_type)

    def command(self, key, value):
        # Never replace a command the PLC has not acknowledged yet.
        deadline = time.monotonic() + 2
        while self.nodes[key].get_value() != 0:
            if time.monotonic() > deadline:
                raise TimeoutError("PLC command acknowledgement timed out")
            time.sleep(0.025)
        self.write(key, value)
        while self.nodes[key].get_value() != 0:
            if time.monotonic() > deadline:
                raise TimeoutError("PLC command acknowledgement timed out")
            time.sleep(0.025)

    def reset(self):
        reset_nodes(self.client)
        for key, data in self.read().items():
            if not data.StatusCode.is_good() or data.Value.Value != load_bindings()[key].baseline:
                raise RuntimeError("Reset baseline verification failed: " + key)


def measurement_fault(values):
    """Return an example SUT diagnosis; never invent a PLC alarm code."""
    pressure = values["vacuum.pressure_pa"]
    if not pressure.StatusCode.is_good():
        return "sensor_quality", "压力传感器质量异常：" + pressure.StatusCode.name
    value = pressure.Value.Value
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        return "invalid_pressure", "压力测量无效"
    for key in ("vacuum.state_code", "vacuum.result_code", "vacuum.alarm_code", "vacuum.valve_open"):
        if not values[key].StatusCode.is_good() or values[key].Value.Value is None:
            return "invalid_feedback", "PLC 状态反馈无效"
    if values["vacuum.alarm_code"].Value.Value == 1:
        return "interlock", "抽气与开阀冲突，触发联锁"
    if values["vacuum.state_code"].Value.Value == 4:
        return "plc_fault", "PLC 报告真空操作故障"
    return None


class Controller:
    """One operation at a time, with its own OPC UA connection and cancellation."""
    def __init__(self, endpoint, emit=quiet_event, pump_timeout=12, vent_timeout=8):
        self.endpoint, self.emit = endpoint, emit
        self.timeouts = {"pump": pump_timeout, "vent": vent_timeout}
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.state = {"status": "idle", "operation": None, "fault": None, "message": "等待操作"}

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def _update(self, status, message, fault=None):
        with self.lock:
            self.state.update(status=status, message=message, fault=fault)
            state = dict(self.state)
        self.emit("controller", message, controller=state)

    def start(self, operation):
        if operation not in self.timeouts:
            raise ValueError("Expected pump or vent")
        if self.thread and self.thread.is_alive():
            raise RuntimeError("控制器正在执行操作")
        if self.snapshot()["fault"]:
            raise RuntimeError("请先重置环境，再启动控制器")
        self.stop_event.clear()
        with self.lock:
            self.state["operation"] = operation
        self._update("running", "正在抽真空" if operation == "pump" else "正在破真空")
        self.thread = threading.Thread(target=self._work, args=(operation,), name="demo-controller", daemon=True)
        self.thread.start()

    def _work(self, operation):
        try:
            with PLCConnection(self.endpoint) as plc:
                try:
                    plc.command("vacuum.valve_command", 2)
                    plc.command("vacuum.command", 1 if operation == "pump" else 2)
                    self.emit("control", "抽气命令已执行" if operation == "pump" else "破真空命令已执行")
                    deadline = time.monotonic() + self.timeouts[operation]
                    while not self.stop_event.is_set():
                        values = plc.read()
                        diagnosis = measurement_fault(values)
                        if diagnosis:
                            self._update("fault", diagnosis[1], diagnosis[0])
                            break
                        state = values["vacuum.state_code"].Value.Value
                        result = values["vacuum.result_code"].Value.Value
                        if result == 2 and state == (3 if operation == "pump" else 0):
                            self._update("completed", "真空已达标" if operation == "pump" else "已恢复常压")
                            return
                        if time.monotonic() >= deadline:
                            self._update("fault", "抽气超时" if operation == "pump" else "破真空超时", "timeout")
                            break
                        self.stop_event.wait(0.05)
                    if self.stop_event.is_set() and self.snapshot()["status"] != "fault":
                        self._update("stopped", "控制器已停止")
                finally:
                    if self.snapshot()["status"] != "completed":
                        plc.command("vacuum.command", 3)
                        self.emit("control", "安全停止完成，破真空阀已关闭")
        except Exception as exc:
            self._update("error", "控制连接异常：" + str(exc), "environment")
            self.emit("environment_error", "控制器执行失败：" + str(exc))

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("Controller did not stop; environment must not be reused")
        if self.snapshot()["status"] == "error":
            raise RuntimeError(self.snapshot()["message"])


class FaultInjector:
    def __init__(self, endpoint, emit=quiet_event):
        self.endpoint, self.emit = endpoint, emit
        self.thread = None
        self.stop_event = threading.Event()
        self.first_write = threading.Event()
        self.error = None
        self.active = None

    def inject(self, fault):
        if fault not in ("interlock", "sensor", "pressure", "timeout"):
            raise ValueError("Unknown fault")
        if self.active:
            raise RuntimeError("已有注入条件，请先重置环境")
        self.active = fault
        self.emit("injecting", "正在设置故障条件", fault=fault)
        if fault == "timeout":
            self.thread = threading.Thread(target=self._hold_pressure, name="demo-pressure-writer", daemon=True)
            self.thread.start()
            if not self.first_write.wait(3) or self.error:
                raise RuntimeError("持续写入任务启动失败：" + str(self.error))
        else:
            with PLCConnection(self.endpoint) as plc:
                if fault == "interlock":
                    plc.command("vacuum.valve_command", 1)
                elif fault == "pressure":
                    plc.write("vacuum.pressure_pa", -50000.0)
                else:
                    data = ua.DataValue(ua.Variant(None, ua.VariantType.Null))
                    data.StatusCode = ua.StatusCode(ua.StatusCodes.BadSensorFailure)
                    plc.nodes["vacuum.pressure_pa"].set_value(data)
        self.emit("injected", "故障条件已写入 PLC", fault=fault)

    def _hold_pressure(self):
        try:
            with PLCConnection(self.endpoint) as plc:
                while not self.stop_event.is_set():
                    plc.write("vacuum.pressure_pa", 101325.0)
                    self.first_write.set()
                    self.stop_event.wait(0.02)
        except Exception as exc:
            self.error = exc
            self.first_write.set()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                raise RuntimeError("Pressure writer did not stop; environment must not be reused")
        if self.error:
            raise RuntimeError("Pressure writer failed: " + str(self.error))
