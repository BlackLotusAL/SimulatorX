"""Cross-platform wire validation with the actual native DLL/.so."""
import ctypes as c
import os
from pathlib import Path
import shutil
import socket
import struct
import sys
import threading
import time

import pytest
from framework.runtime import DeviceRuntime
from framework.source import SOURCE_ROOT
from framework.transport import receive_exact
from subsystems.motion.rotary_axis.abi import CONTROL_ERROR

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="Native SDK platform")]


class State(c.Structure):
    _fields_ = [(key, c.c_double) for key in ("position", "velocity", "target")] + [
        (key, c.c_uint32) for key in ("enabled", "homed", "busy", "done", "alarm", "faults")]


def load(path):
    lib = c.CDLL(str(path))
    lib.SX_GetState.argtypes = [c.c_int32, c.POINTER(State)]
    lib.SX_GetState.restype = c.c_int32
    lib.SX_Enable.argtypes = [c.c_int32]
    lib.SX_Enable.restype = c.c_int32
    return lib


@pytest.mark.parametrize("reply", ["fragmented", "truncated", "bad_magic", "nan", "slow"])
def test_native_wire_validation_and_unicode_diagnostics(sdk_library, tmp_path, monkeypatch, reply):
    native_dir = tmp_path / "原生 库"
    native_dir.mkdir()
    library = native_dir / sdk_library.name
    shutil.copy2(sdk_library, library)
    lib = load(library)
    assert c.sizeof(State) == 48
    error_file = tmp_path / "诊断 错误.txt"
    monkeypatch.setenv("SIMULATORX_SDK_ERROR_FILE", str(error_file))
    monkeypatch.setenv("SIMULATORX_CONTROL_TIMEOUT_MS", "1000" if reply == "fragmented" else "150")
    # Linux Unix paths must fit sockaddr_un; use a short system temporary path.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="sx-wire-") as directory:
        windows = sys.platform == "win32"
        with socket.socket(socket.AF_INET if windows else socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            endpoint = ("127.0.0.1", 0) if windows else str(Path(directory) / "s")
            listener.bind(endpoint)
            listener.listen()
            listener.settimeout(2)
            monkeypatch.setenv("SIMULATORX_SDK_ENDPOINT" if windows else "SIMULATORX_CONTROL_SOCKET",
                               "tcp://%s:%s" % listener.getsockname() if windows else endpoint)
            requests = []
            failures = []
            def peer():
                try:
                    connection, _ = listener.accept()
                    with connection:
                        requests.append(receive_exact(connection, 28))
                        response = struct.pack("!4sidddIIIIII", b"BAD!" if reply == "bad_magic" else b"SX01", 0,
                                               float("nan") if reply == "nan" else 12.5, 0, 12.5, 1, 1, 0, 1, 0, 0)
                        if reply == "slow":
                            time.sleep(0.3)
                        elif reply == "truncated":
                            connection.sendall(response[:11])
                        elif reply == "fragmented":
                            for index in range(0, len(response), 3):
                                connection.sendall(response[index:index + 3])
                                time.sleep(0.001)
                        else:
                            connection.sendall(response)
                except Exception as exc:
                    failures.append(exc)
            thread = threading.Thread(target=peer)
            thread.start()
            state = State()
            state.position = -999
            started = time.monotonic()
            code = lib.SX_GetState(1, c.byref(state))
            elapsed = time.monotonic() - started
            thread.join(3)
            assert not thread.is_alive() and not failures
            assert elapsed < (2 if reply == "fragmented" else 1)
            assert len(requests) == 1 and requests[0][:4] == b"SX01"
            if reply == "fragmented":
                assert code == 0 and state.position == 12.5 and state.done == 1
                assert not error_file.exists()
            else:
                assert code == CONTROL_ERROR and state.position == -999
                assert "SDK control failure" in error_file.read_text(encoding="utf-8")
            listener.settimeout(0.05)
            with pytest.raises(socket.timeout):
                listener.accept()  # No retry after a possibly executed request.


def test_native_library_routes_to_independent_instances(sdk_library, monkeypatch):
    lib = load(sdk_library)
    with DeviceRuntime.from_config(SOURCE_ROOT / "device.json", ["motion/rotary_axis"]) as first, \
         DeviceRuntime.from_config(SOURCE_ROOT / "device.json", ["motion/rotary_axis"]) as second:
        left, right = next(first.hardware()).client, next(second.hardware()).client
        for key, value in left.launch_environment.items():
            monkeypatch.setenv(key, value)
        assert lib.SX_Enable(1) == 0
        assert left.snapshot()["enabled"] and not right.snapshot()["enabled"]
        for key, value in right.launch_environment.items():
            monkeypatch.setenv(key, value)
        right.returns["rotary.Enable"].set_sequence([-41])
        assert lib.SX_Enable(1) == -41
        assert not right.snapshot()["enabled"]
        assert lib.SX_Enable(1) == 0
        left.reset()
        assert right.snapshot()["enabled"] and not left.snapshot()["enabled"]
