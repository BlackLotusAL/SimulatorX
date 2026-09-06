from pathlib import Path
from demo.common.protocol_runtime import ProtocolRuntime
from .catalog import CASES, inject


class SDKRuntime(ProtocolRuntime):
    kind = "sdk"
    selection = "motion/rotary_axis"
    worker_module = "demo.sdk.worker"
    plugin_module = "demo.sdk.pytest_plugin"
    protocol_directory = Path(__file__).resolve().parent
    catalog = CASES
    inject = staticmethod(inject)
    sample_fields = ("position", "velocity", "target")
    actions = {"enable", "disable", "home", "move", "relative", "stop", "clear"}
    extra_control_operations = {"set_fault": {"name", "active"}}

    def prepare_hardware(self):
        if not self.library:
            self.library = str(self.hardware.build_sdk(self.artifacts / "native"))
        if not Path(self.library).is_file():
            raise RuntimeError("SDK 原生库不存在：" + self.library)
        # Verify loadability in an isolated interpreter before starting the service.
        import subprocess
        import sys
        from framework.source import hidden_process_options
        result = subprocess.run([sys.executable, "-c",
            "import ctypes,sys; lib=ctypes.CDLL(sys.argv[1]); "
            "[getattr(lib,'SX_'+name) for name in "
            "('Enable','Disable','Home','MoveAbsolute','MoveRelative','Stop','ClearFault','GetPosition','GetState')]",
            self.library], capture_output=True, text=True, timeout=10, **hidden_process_options())
        if result.returncode:
            raise RuntimeError("SDK 库加载失败：" + result.stderr)

    def initial_baseline(self):
        return self.hardware.client.snapshot()

    def observed_values(self, diagnostics):
        return diagnostics["state"]

    def verify_baseline(self):
        if self.hardware.client.snapshot() != self.baseline:
            raise RuntimeError("旋转轴未恢复基线")

    def launch_environment(self):
        return self.hardware.client.launch_environment

    def validate_manual(self, action, target, speed):
        super().validate_manual(action, target, speed)
        if any(type(value) not in (int, float) or not -1e10 < value < 1e10 for value in (target, speed)):
            raise ValueError("目标和速度必须为有限数值")
        if not 0 < speed <= 90 or not -180 <= target <= 180:
            raise ValueError("目标范围 −180..180°，速度范围 (0,90]°/s")
        if action == "relative" and not -180 <= self.hardware.client.snapshot()["position"] + target <= 180:
            raise ValueError("相对运动终点超出轴行程")
