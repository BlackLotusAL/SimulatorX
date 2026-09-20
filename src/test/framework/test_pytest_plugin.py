"""Exercise the public device fixture in independent pytest processes."""
import json
import subprocess
import sys
from xml.etree import ElementTree

import pytest

pytestmark = pytest.mark.integration

from framework.source import source_environment, hidden_process_options


def run_cases(tmp_path, source, selection="detector/modbus_tcp", conftest="", options=()):
    (tmp_path / "conftest.py").write_text('pytest_plugins = ["pytest_plugin"]\n' + conftest)
    (tmp_path / "test_child.py").write_text(source)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "test_child.py",
                           "--simulatorx-select", selection, "--junitxml=report.xml", *options],
                          cwd=tmp_path, env=source_environment(), capture_output=True, text=True,
                          timeout=60, **hidden_process_options())


def test_plugin_is_lazy(tmp_path):
    result = run_cases(tmp_path, "def test_plain():\n    assert True\n", conftest='''
from framework.runtime import DeviceRuntime
def forbidden(self):
    raise AssertionError("Unexpected startup")
DeviceRuntime.start = forbidden
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_failure_cleanup_writer_order_and_diagnostics(tmp_path):
    result = run_cases(tmp_path, '''
import pytest
from pathlib import Path

@pytest.fixture
def writer(device, request):
    client = device.subsystems["detector"].hardware["modbus_tcp"].client
    client.responses["04:0:2"].set_sequence([{"registers": [77, 1]}])
    original = client.reset
    def reset():
        assert Path("writer-stopped").exists()
        return original()
    client.reset = reset
    request.addfinalizer(lambda: Path("writer-stopped").touch())

def test_01(writer):
    assert False, "intentional assertion failure"

@pytest.fixture
def bad_setup(device):
    client = device.subsystems["detector"].hardware["modbus_tcp"].client
    assert client.responses.snapshot() == {}
    client.responses["04:0:2"].set_sequence([{"registers": [99, 1]}])
    raise RuntimeError("intentional setup failure")

def test_02(bad_setup):
    pass

def test_03(device):
    assert device.subsystems["detector"].hardware["modbus_tcp"].client.responses.snapshot() == {}
''')
    assert result.returncode == 1, result.stdout + result.stderr
    cases = ElementTree.parse(tmp_path / "report.xml").findall(".//testcase")
    assert len(cases) == 3
    assert sum(c.find("failure") is not None for c in cases) == 1
    assert sum(c.find("error") is not None for c in cases) == 1
    reports = [json.loads(p.read_text()) for p in (tmp_path / "artifacts/simulatorx").glob("device-*.json")]
    assert len(reports) == 3
    first = next(r for r in reports if "test_01" in r["test"])
    hardware = next(iter(first["hardware"].values()))
    assert hardware["sequences"]["04:0:2"]["remaining"] == 1


@pytest.mark.parametrize("stage", ["reset", "check_health", "diagnostics"])
def test_cleanup_failure_stops_following_cases(tmp_path, stage):
    result = run_cases(tmp_path, '''
from pathlib import Path
def test_01(device):
    client = device.subsystems["detector"].hardware["modbus_tcp"].client
    def fail():
        raise RuntimeError("intentional cleanup failure")
    client.METHOD = fail
def test_02(device):
    Path("should-not-exist").touch()
'''.replace("METHOD", stage))
    assert result.returncode != 0
    assert "Device cleanup failed" in result.stdout
    assert not (tmp_path / "should-not-exist").exists()
    assert list((tmp_path / "artifacts/simulatorx").glob("device-*.json"))


def test_initial_reset_failure_still_collects_diagnostics_and_stops_reuse(tmp_path):
    result = run_cases(tmp_path, '''
from pathlib import Path
def test_01(device):
    raise AssertionError("setup must fail first")
def test_02(device):
    Path("should-not-exist").touch()
''', conftest='''
from protocols.tcp.client import TCPClient
def fail(self):
    raise RuntimeError("initial reset failure")
TCPClient.reset = fail
''')
    assert result.returncode != 0
    assert not (tmp_path / "should-not-exist").exists()
    assert list((tmp_path / "artifacts/simulatorx").glob("device-*.json"))


@pytest.mark.parametrize("selection", ["vacuum/chamber_plc", "motion/rotary_axis", "detector/modbus_tcp"])
def test_plugin_only_starts_selected_hardware(tmp_path, selection):
    if selection.startswith("motion") and sys.platform not in ("linux", "win32"):
        pytest.skip("Requires Windows/Linux SDK")
    sid, hid = selection.split("/")
    result = run_cases(tmp_path, f'''
def test_selected(device):
    assert list(device.subsystems) == [{sid!r}]
    assert list(device.subsystems[{sid!r}].hardware) == [{hid!r}]
    assert device.check_health()
''', selection=selection)
    assert result.returncode == 0, result.stdout + result.stderr
