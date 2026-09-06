"""Launch from outside the repository without an installed framework package."""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from opcua import Client

from local_service.common.runtime import SOURCE_ROOT, hidden_process_options
from local_service.plc.bindings import load_bindings
from local_service.process import ServiceProcess
from local_service.sdk.client import SDKClient
from local_service.tcp.client import TCPClient
from tests.helpers import eventually


@pytest.mark.parametrize("kind", ["plc", "sdk", "tcp"])
def test_source_launcher_from_another_working_directory(tmp_path, kind):
    if kind == "sdk" and sys.platform != "linux":
        pytest.skip("Linux SDK")
    # Keep Unix socket names short even when the test workspace path is long.
    with tempfile.TemporaryDirectory(prefix="sx-cli-") as runtime:
        runtime = Path(runtime)
        ready = runtime / "ready.json"
        args = [sys.executable, str(SOURCE_ROOT / "main.py"), kind, "--managed", "--ready-file", str(ready)]
        if kind == "sdk":
            args += ["--control-socket", str(runtime / "control.sock"), "--sdk-socket", str(runtime / "sdk.sock")]
        elif kind == "plc":
            args += ["--opcua-port", "0", "--fast"]
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        log_path = tmp_path / "service.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(args, cwd=tmp_path, env=environment, stdin=subprocess.PIPE,
                                       stdout=log, stderr=subprocess.STDOUT, text=True,
                                       **hidden_process_options())
            try:
                def ready_info():
                    assert process.poll() is None, log_path.read_text(encoding="utf-8")
                    return json.loads(ready.read_text(encoding="utf-8")) if ready.exists() else None

                info = eventually(ready_info, timeout=20)
                if kind == "plc":
                    with Client(info["opcua_endpoint"], timeout=1) as client:
                        assert len(load_bindings()) == 8
                        assert load_bindings()["vacuum.alarm_code"].resolve(client).get_value() == 0
                elif kind == "sdk":
                    client = SDKClient(info["control_socket"])
                    assert client.check_health()
                    assert client.call("Enable")["return_value"] == 0
                    assert client.snapshot()["enabled"]
                else:
                    client = TCPClient(tuple(info["control_endpoint"]))
                    assert client.check_health() and client.endpoint == tuple(info["endpoint"])
                process.communicate("stop\n", timeout=10)
                assert process.returncode == 0, log_path.read_text(encoding="utf-8")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)


def test_child_keeps_custom_protocol_search_path(tmp_path, monkeypatch):
    (tmp_path / "custom_protocol.py").write_text('''
from local_service.tcp.protocol import DemoProtocol

class CustomProtocol(DemoProtocol):
    baselines = {"READ_STATUS": {"status": 17, "ready": False}}
''', encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    with ServiceProcess("tcp", protocol="custom_protocol:CustomProtocol") as process:
        assert process.client.request("ping")["protocol"] == "CustomProtocol"
        assert process.client.check_health()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native .so")
def test_build_sdk_through_source_launcher(tmp_path):
    output = tmp_path / "native"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(SOURCE_ROOT / "main.py"), "build-sdk", "--output", str(output)],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    library = ctypes.CDLL(str(output / "libsimulatorx_sdk.so"))
    assert library.SX_Enable and library.SX_GetPosition
