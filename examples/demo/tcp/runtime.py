from pathlib import Path
from demo.common.protocol_runtime import ProtocolRuntime
from .catalog import CASES, inject
import socket
import struct
from framework.transport import receive_exact


class TCPRuntime(ProtocolRuntime):
    kind = "tcp"
    selection = "detector/modbus_tcp"
    worker_module = "demo.tcp.worker"
    plugin_module = "demo.tcp.pytest_plugin"
    protocol_directory = Path(__file__).resolve().parent
    catalog = CASES
    inject = staticmethod(inject)
    sample_fields = ("measurement",)
    actions = {"check", "stop"}
    stop_is_local = True

    def observed_values(self, diagnostics):
        return self.controller_state.get("observed", {}).copy()

    def verify_baseline(self):
        # Read holding registers only after Reset; never consume business input sequences.
        with socket.create_connection(self.endpoint, timeout=2) as sock:
            sock.sendall(struct.pack("!HHHBBHH", 1, 0, 6, 1, 3, 0, 2))
            expected = struct.pack("!HHHBBBHH", 1, 0, 7, 1, 3, 4, 1, 100)
            if receive_exact(sock, len(expected)) != expected:
                raise RuntimeError("探测器寄存器未恢复基线")
