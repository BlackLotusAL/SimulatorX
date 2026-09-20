"""Configured CLI ownership and discovery from outside the repository."""
import ctypes
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

from framework.source import SOURCE_ROOT, hidden_process_options, source_environment
from test.helpers import eventually, service_process


@pytest.mark.parametrize("selection", ["vacuum/chamber_plc", "motion/rotary_axis", "detector/modbus_tcp"])
def test_source_launcher_from_another_working_directory(tmp_path, selection):
    if selection.startswith("motion") and sys.platform not in ("linux", "win32"):
        pytest.skip("Requires Windows/Linux SDK")
    ready = tmp_path / "ready.json"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    args = [sys.executable, str(SOURCE_ROOT / "main.py"), "run", "--select", selection,
            "--managed", "--ready-file", str(ready)]
    with (tmp_path / "service.log").open("w") as log:
        process = subprocess.Popen(args, cwd=tmp_path, env=environment, stdin=subprocess.PIPE,
                                   stdout=log, stderr=subprocess.STDOUT, text=True, **hidden_process_options())
        try:
            def ready_info():
                assert process.poll() is None, (tmp_path / "service.log").read_text()
                return json.loads(ready.read_text()) if ready.exists() else None
            info = eventually(ready_info, timeout=25)
            assert list(info["hardware"]) == ["reference_machine/" + selection]
            assert all(info["hardware"].values())
            process.communicate("stop\n", timeout=15)
            assert process.returncode == 0, (tmp_path / "service.log").read_text()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_child_keeps_fixed_host_with_extra_search_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    from framework.config import HardwareConfig, load_protocol
    config = HardwareConfig("test/detector/modbus_tcp", "tcp", {},
                            SOURCE_ROOT / "subsystems/detector/modbus_tcp/resources")
    hardware = load_protocol(config.type).create(config)
    hardware.validate()
    with hardware.create_process() as process:
        assert process.client.check_health()


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="Requires Windows/Linux native SDK")
def test_build_sdk_through_source_launcher(tmp_path):
    output = tmp_path / "native"
    result = subprocess.run([sys.executable, str(SOURCE_ROOT / "main.py"), "build-sdk",
                             "--hardware", "motion/rotary_axis", "--output", str(output)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    library = ctypes.CDLL(str(output / ("simulatorx_sdk.dll" if sys.platform == "win32" else "libsimulatorx_sdk.so")))
    assert library.SX_Enable and library.SX_GetPosition


def test_cli_health_failure_stops_remaining_children(tmp_path):
    # A test hardware module terminates its service after initial readiness.
    (tmp_path / "failing_hardware.py").write_text('''
import threading
from protocols.tcp.host import TCPHardware
class Failing(TCPHardware):
    def start(self):
        super().start()
        timer = threading.Timer(2, self.process.process.kill)
        timer.daemon = True
        timer.start()
        return self
def create(config):
    return Failing(config)
if __name__ == "__main__":
    import protocols.tcp.host
    protocols.tcp.host.create = create
    from main import main
    main()
''')
    config = tmp_path / "device.json"
    config.write_text(json.dumps({"id": "machine", "subsystems": [
        {"id": "vacuum", "hardware": [{"id": "chamber_plc", "type": "opcua", "nodeset": "vacuum.xml"}]},
        {"id": "detector", "hardware": [{"id": "modbus_tcp", "type": "tcp"}]}]}))
    ready = tmp_path / "ready.json"
    environment = source_environment()
    environment["PYTHONPATH"] += os.pathsep + str(tmp_path)
    result = subprocess.run([sys.executable, str(tmp_path / "failing_hardware.py"), "run", "--device", str(config),
                             "--ready-file", str(ready)], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=30, **hidden_process_options())
    assert result.returncode != 0
    info = json.loads(ready.read_text())
    for hardware in info["hardware"].values():
        for address in hardware.values():
            with socket.socket() as probe:
                probe.settimeout(1)
                if isinstance(address, str):
                    from urllib.parse import urlparse
                    endpoint = urlparse(address)
                    address = (endpoint.hostname, endpoint.port)
                assert probe.connect_ex(tuple(address)) != 0
