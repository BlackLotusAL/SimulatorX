"""Run isolated pytest sessions to exercise real finalization and abort behavior."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

from framework.source import hidden_process_options, source_environment


@pytest.mark.parametrize("failure", ["assertion", "setup", "close", "environment"])
def test_failure_cleanup_and_reuse(tmp_path, failure):
    project = Path(__file__).resolve().parents[1]
    environment = source_environment()
    environment["PYTHONPATH"] += os.pathsep + str(project)
    (tmp_path / "conftest.py").write_text('''
import pytest
from pathlib import Path
from reference_sut import DetectorSUT
from integration_support import finish_sut
pytest_plugins = ["pytest_plugin"]

def pytest_addoption(parser):
    parser.addoption("--sut-artifacts", default="artifacts/sut")

@pytest.fixture
def managed(device, request):
    endpoint = device.subsystems["detector"].hardware["modbus_tcp"]
    sut = DetectorSUT(endpoint.endpoints["tcp"])
    close = sut.close
    def tracked_close():
        close()
        Path("stopped").touch()
        if FAILURE == "close":
            raise RuntimeError("injected SUT close failure")
    sut.close = tracked_close
    reset = endpoint.client.reset
    def tracked_reset():
        assert Path("stopped").exists()
        Path("reset-after-stop").touch()
        return reset()
    endpoint.client.reset = tracked_reset
    request.addfinalizer(lambda: finish_sut(sut, device, request))
    sut.start_check()
    if FAILURE == "environment":
        endpoint.process.process.kill()
        endpoint.process.process.wait(timeout=3)
    if FAILURE == "setup":
        raise RuntimeError("injected setup failure")
    return sut
'''.replace("FAILURE", repr(failure)), encoding="utf-8")
    (tmp_path / "test_child.py").write_text('''
from pathlib import Path

def test_01(managed):
    if FAILURE == "assertion":
        assert False, "injected assertion failure"

def test_02(device):
    Path("next-case").touch()
'''.replace("FAILURE", repr(failure)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_child.py",
         "--simulatorx-select", "detector/modbus_tcp"],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        timeout=40, **hidden_process_options())
    assert result.returncode != 0, result.stdout + result.stderr
    assert (tmp_path / "stopped").exists()
    assert list((tmp_path / "artifacts/simulatorx").glob("device-*.json"))
    reports = list((tmp_path / "artifacts/sut").glob("sut-*.json"))
    assert reports and json.loads(reports[0].read_text())["operations"]
    if failure in ("close", "environment"):
        assert not (tmp_path / "next-case").exists()
        assert not (tmp_path / "reset-after-stop").exists()
        assert ("SUT cleanup failed" if failure == "close" else "Device cleanup failed") in result.stdout
    else:
        assert (tmp_path / "next-case").exists()
        assert (tmp_path / "reset-after-stop").exists()
