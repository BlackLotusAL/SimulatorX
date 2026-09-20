"""Real anonymous pipe failure and lifecycle guarantees on Windows and Linux."""
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from framework.pipe import ProcessRequests, encode
from framework.process import ServiceProcess
from framework.source import hidden_process_options
from framework.transport import EnvironmentError, MAX_MESSAGE
from test.helpers import service_process


@pytest.fixture
def child():
    children = []

    def start(code, timeout=2):
        process = subprocess.Popen([sys.executable, "-u", "-c", code], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding="utf-8", **hidden_process_options())
        requests = ProcessRequests(process, timeout)
        children.append((process, requests))
        return process, requests

    yield start
    for process, requests in children:
        requests.close()
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        requests.finish()
        for stream in (process.stdin, process.stdout):
            if not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass


@pytest.mark.integration
def test_pipe_serializes_concurrent_requests(child):
    _, requests = child('''
import sys, json
for line in sys.stdin:
    print(json.dumps({"ok": True, "result": json.loads(line)["value"]}), flush=True)
''')
    values = ["device-" + str(i) for i in range(20)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda value: requests.request({"value": value}), values)) == values
    requests.close()
    with pytest.raises(EnvironmentError, match="closed"):
        requests.request({})


@pytest.mark.parametrize("reply", ["not-json", '{"ok": 1}', ""])
@pytest.mark.integration
def test_broken_pipe_response_disables_reuse(child, reply):
    _, requests = child("import sys; sys.stdin.readline(); print(" + repr(reply) + ", flush=True)")
    with pytest.raises(EnvironmentError, match="communication failed"):
        requests.request({})
    with pytest.raises(EnvironmentError, match="failed"):
        requests.request({})


@pytest.mark.parametrize("reads", [True, False])
@pytest.mark.integration
def test_pipe_read_or_write_timeout_and_process_cleanup(child, reads):
    code = "import sys, time; " + ("sys.stdin.readline(); " if reads else "") + "time.sleep(30)"
    process, requests = child(code, timeout=0.3)
    # A large request fills the OS pipe when the child does not read.
    payload = {} if reads else {"value": "x" * (MAX_MESSAGE // 2)}
    before = time.monotonic()
    with pytest.raises(EnvironmentError, match="timed out"):
        requests.request(payload)
    assert time.monotonic() - before < 3
    with pytest.raises(EnvironmentError, match="failed"):
        requests.request({})
    manager = ServiceProcess("unused", None, None, pipe_control=True)
    manager.process, manager.requests = process, requests
    with pytest.raises(EnvironmentError, match="forced termination"):
        manager.stop()
    assert manager.process is manager.requests is None
    assert not requests.thread.is_alive()
    manager.stop()


def test_invalid_or_oversize_requests_are_rejected_before_send():
    with pytest.raises(ValueError):
        encode({"value": float("nan")})
    with pytest.raises(ValueError, match="too large"):
        encode({"value": "x" * MAX_MESSAGE})


@pytest.mark.integration
def test_tcp_pipe_errors_sequences_and_isolation():
    with service_process("tcp") as first, service_process("tcp") as second:
        assert set(first.info) == {"endpoint", "protocol"}
        assert first.client.endpoint != second.client.endpoint
        assert not hasattr(first.client, "control_endpoint")
        first.client.responses["04:0:2"].set_sequence([{"registers": [9, 1]}])
        assert second.client.responses.snapshot() == {}
        with pytest.raises(ValueError):
            first.client.responses["04:0:2"].set_sequence([{"unknown": 9}])
        assert first.client.check_health()
        assert first.client.diagnostics()["sequences"]
        first.client.reset()
        assert first.client.responses.snapshot() == {}


def test_tcp_rejects_control_port_before_start(tmp_path):
    from framework.runtime import DeviceRuntime
    config = {"id": "machine", "subsystems": [{"id": "detector", "hardware": [
        {"id": "modbus_tcp", "type": "tcp", "control_port": 0}]}]}
    path = tmp_path / "device.json"
    path.write_text(json.dumps(config))
    device = DeviceRuntime.from_config(path)
    with pytest.raises(ValueError, match="Removed settings: control_port"):
        device.start()
    assert all(h.process is None for h in device.hardware())


@pytest.mark.integration
def test_pipe_logs_are_separate_and_parent_eof_stops_service(tmp_path, monkeypatch):
    from framework.config import HardwareConfig
    from framework.source import SOURCE_ROOT
    from protocols.tcp.host import TCPHardware
    (tmp_path / "sitecustomize.py").write_text('''
from protocols.tcp.host import create_service as create_tcp
def create_service(config):
    print("startup noise")
    service = create_tcp(config)
    dispatch = service.dispatch
    def noisy(request):
        print("request noise")
        return dispatch(request)
    service.dispatch = noisy
    return service
import protocols.tcp.host
protocols.tcp.host.create_service = create_service
''')
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    config = HardwareConfig("test/detector/modbus_tcp", "tcp", {},
                            SOURCE_ROOT / "subsystems/detector/modbus_tcp/resources")
    hardware = TCPHardware(config)
    with hardware.create_process() as manager:
        assert manager.client.reset()
        assert "startup noise" in manager.log()
        assert "request noise" in manager.log()
        manager.process.stdin.close()
        assert manager.process.wait(timeout=5) == 0
