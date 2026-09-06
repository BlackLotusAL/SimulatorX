import json
import os
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from local_service.sdk.motion import CONTROL_ERROR
from local_service.sdk import build
from local_service.common.transport import EnvironmentError
from tests.helpers import eventually

pytestmark = [pytest.mark.linux_sdk, pytest.mark.skipif(sys.platform != "linux", reason="Linux native .so")]


@pytest.fixture(scope="session")
def native_driver(sdk_library, tmp_path_factory):
    native = Path(build.__file__).resolve().parent / "native"
    executable = tmp_path_factory.mktemp("native-driver") / "driver"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(native), str(Path(__file__).with_name("native_client.c")),
                    "-L", str(sdk_library.parent), "-lsimulatorx_sdk",
                    "-Wl,-rpath," + str(sdk_library.parent), "-o", str(executable)],
                   check=True, timeout=30)
    return executable


def read_line(process, timeout=3):
    ready, _, _ = select.select([process.stdout], [], [], timeout)
    assert ready, "Native process did not respond before deadline"
    line = process.stdout.readline()
    assert line, "Native process exited unexpectedly"
    return line


@pytest.fixture
def native_sut(native_driver, sdk_axis, request):
    process = subprocess.Popen([str(native_driver)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               env={**os.environ, **sdk_axis.launch_environment})

    def stop():
        try:
            process.stdin.close()
            try:
                code = process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
                raise AssertionError("Native SUT did not stop")
            assert code == 0, process.stderr.read()
        finally:
            process.stdout.close()
            process.stderr.close()

    request.addfinalizer(stop)
    assert read_line(process).strip() == "ready"

    def command(text):
        process.stdin.write(text + "\n")
        process.stdin.flush()
        return json.loads(read_line(process))

    return command


def test_real_shared_library_transfers_parameters_outputs_and_consumes_overrides(native_sut, sdk_axis, sdk_returns):
    call = native_sut
    assert call("enable")["return"] == 0
    assert call("home")["return"] == 0
    assert call("state")["homed"] == 1
    sdk_returns["rotary.MoveAbsolute"].set_sequence([-41, -42])
    assert call("move 30 90")["return"] == -41
    assert call("move 30 90")["return"] == -42
    assert sdk_axis.snapshot()["position"] == 0 and not sdk_axis.snapshot()["busy"]
    assert call("move 30 90")["return"] == 0
    assert call("state")["busy"] == 1
    eventually(lambda: call("state")["done"] == 1)
    assert call("position")["position"] == 30
    sdk_returns["rotary.GetPosition"].set_sequence([77])
    assert call("position") == {"return": 77, "position": -9999}
    # Exhaustion reads the current model position, rather than the Reset baseline.
    assert call("position") == {"return": 0, "position": 30}
    assert call("relative -10 90")["return"] == 0
    eventually(lambda: call("state")["done"] == 1)
    assert call("position")["position"] == 20


def test_native_limit_and_stall_feedback(native_sut, sdk_axis):
    call = native_sut
    call("enable")
    call("home")
    call("move 100 90")
    eventually(lambda: call("position")["position"] > 1)
    stalled = sdk_axis.set_fault("stalled", True)
    assert call("state")["busy"] == 1
    assert call("position")["position"] == stalled["position"]
    sdk_axis.set_fault("positive_limit", True)
    assert call("state")["busy"] == 0
    assert call("clear")["return"] != 0
    sdk_axis.set_fault("positive_limit", False)
    sdk_axis.set_fault("stalled", False)
    assert call("move 0 90")["return"] != 0
    assert call("clear")["return"] == 0
    assert call("move 0 90")["return"] == 0
    eventually(lambda: call("state")["done"] == 1)
    assert call("position")["position"] == 0


def test_native_control_failure_is_logged_and_does_not_retry(native_driver, tmp_path):
    import tempfile
    with tempfile.TemporaryDirectory(prefix="sx-timeout-") as directory:
        endpoint = str(Path(directory) / "sdk.sock")
        error_file = str(Path(directory) / "errors")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(endpoint)
            listener.listen()
            listener.settimeout(2)
            received = []

            def consume_without_reply():
                connection, _ = listener.accept()
                with connection:
                    received.append(connection.recv(100))
                    time.sleep(0.3)

            worker = threading.Thread(target=consume_without_reply)
            worker.start()
            started = time.monotonic()
            result = subprocess.run([str(native_driver)], input="enable\n", capture_output=True,
                                    text=True, timeout=3,
                                    env={**os.environ, "SIMULATORX_CONTROL_SOCKET": endpoint,
                                         "SIMULATORX_SDK_ERROR_FILE": error_file,
                                         "SIMULATORX_CONTROL_TIMEOUT_MS": "100"})
            worker.join(timeout=3)
            assert result.returncode == 0
            assert time.monotonic() - started < 2
            assert json.loads(result.stdout.splitlines()[1])["return"] == CONTROL_ERROR
            assert len(received) == 1 and len(received[0]) == 28
            assert "SDK control failure" in Path(error_file).read_text()
            listener.settimeout(0.05)
            with pytest.raises(socket.timeout):
                listener.accept()


def test_service_reports_native_error_marker_as_environment_failure():
    from local_service.process import ServiceProcess
    with ServiceProcess("sdk") as process:
        environment = process.client.launch_environment
        Path(environment["SIMULATORX_SDK_ERROR_FILE"]).write_text("native connection failed\n")
        with pytest.raises(EnvironmentError):
            process.client.check_health()
        # Diagnostics remain available even when the case is no longer reusable.
        assert "connection failed" in process.client.diagnostics()["native_errors"]
        with pytest.raises(EnvironmentError):
            process.client.reset()
