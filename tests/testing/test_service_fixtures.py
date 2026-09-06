import subprocess
import sys
from xml.etree import ElementTree

import pytest

from local_service.common.runtime import hidden_process_options, source_environment


def run_case(tmp_path, source, kind, conftest=""):
    (tmp_path / "conftest.py").write_text(
        'pytest_plugins = ["pytest_plugin"]\n' + conftest, encoding="utf-8")
    (tmp_path / "test_child.py").write_text(source, encoding="utf-8")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "test_child.py",
                           "--junitxml=report.xml"], cwd=tmp_path, capture_output=True,
                          text=True, timeout=45, env=source_environment(), **hidden_process_options())


def test_plugin_does_not_start_sdk_or_tcp_without_requested_resources(tmp_path):
    result = run_case(tmp_path, "def test_plain():\n    assert True\n", "tcp", '''
from local_service.process import ServiceProcess
def forbidden(*args, **kwargs):
    raise AssertionError("Service must not start")
ServiceProcess.start = forbidden
''')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["sdk", "tcp"])
def test_setup_assertion_and_initial_reset_failures_still_cleanup(tmp_path, kind):
    if kind == "sdk" and sys.platform != "linux":
        pytest.skip("Linux SDK")
    source = '''
import json
import threading
from pathlib import Path
import pytest
from local_service.CLIENT_MODULE.client import CLIENT_TYPE

def record(name, event):
    with Path("order.jsonl").open("a") as stream:
        stream.write(json.dumps([name, event]) + "\\n")

@pytest.fixture(autouse=True)
def trace(request, monkeypatch):
    original = CLIENT_TYPE.reset
    count = 0
    def reset(client):
        nonlocal count
        original(client)
        count += 1
        record(request.node.name, "reset")
        if request.node.name == "test_00_initial" and count == 1:
            raise RuntimeError("initial reset failure")
        return True
    monkeypatch.setattr(CLIENT_TYPE, "reset", reset)

@pytest.fixture
def controlled(request):
    request.getfixturevalue("RESOURCE")
    return request.getfixturevalue("SERVICE")

def dirty(client):
    client.request("set_sequence", target="TARGET", values=VALUES)

def test_00_initial(controlled):
    pass

@pytest.fixture
def bad_setup(controlled):
    dirty(controlled)
    raise RuntimeError("setup failure")

def test_01_setup(bad_setup):
    pass

@pytest.fixture
def writer(controlled, request):
    stop = threading.Event()
    started = threading.Event()
    failures = []
    def work():
        try:
            while not stop.is_set():
                dirty(controlled)
                started.set()
                stop.wait(0.01)
        except Exception as exc:
            failures.append(exc)
    thread = threading.Thread(target=work)
    def finish():
        stop.set()
        thread.join(timeout=3)
        assert not thread.is_alive() and not failures
        record(request.node.name, "writer_stopped")
    request.addfinalizer(finish)
    thread.start()
    assert started.wait(timeout=3)

def test_02_assertion(writer):
    assert False, "intentional failure"

def test_03_clean(controlled):
    assert controlled.request("sequences") == {}
'''
    replacements = {"CLIENT_TYPE": "SDKClient" if kind == "sdk" else "TCPClient", "CLIENT_MODULE": kind,
                    "RESOURCE": "sdk_axis" if kind == "sdk" else "tcp_responses",
                    "SERVICE": kind + "_service", "TARGET": "rotary.MoveAbsolute" if kind == "sdk" else "READ_STATUS",
                    "VALUES": "[77]" if kind == "sdk" else '[{"status": 77}]'}
    for old, new in replacements.items():
        source = source.replace(old, new)
    result = run_case(tmp_path, source, kind)
    assert result.returncode == 1, result.stdout + result.stderr
    cases = ElementTree.parse(tmp_path / "report.xml").findall(".//testcase")
    assert len(cases) == 4
    assert sum(c.find("failure") is not None for c in cases) == 1
    assert sum(c.find("error") is not None for c in cases) == 2
    import json
    records = [json.loads(line) for line in (tmp_path / "order.jsonl").read_text().splitlines()]
    assert [event for name, event in records if name == "test_02_assertion"] == [
        "reset", "writer_stopped", "reset"]
    assert [event for name, event in records if name == "test_00_initial"] == ["reset", "reset"]
    artifacts = list((tmp_path / "artifacts" / "simulatorx").glob(kind + "-*.json"))
    assert len(artifacts) == 4
    failed = [json.loads(path.read_text()) for path in artifacts
              if "test_02_assertion" in path.read_text()][0]
    assert failed["sequences"][replacements["TARGET"]]["remaining"] == 1


@pytest.mark.parametrize("kind", ["sdk", "tcp"])
@pytest.mark.parametrize("stage", ["reset", "diagnostics", "health"])
def test_cleanup_failure_stops_following_cases(tmp_path, kind, stage):
    if kind == "sdk" and sys.platform != "linux":
        pytest.skip("Linux SDK")
    resource = "sdk_axis" if kind == "sdk" else "tcp_responses"
    service = kind + "_service"
    source = '''
import pytest

def test_01(RESOURCE, SERVICE, monkeypatch):
    def fail():
        raise RuntimeError("intentional cleanup failure")
    monkeypatch.setattr(SERVICE, "METHOD", fail)

def test_02(RESOURCE):
    raise AssertionError("should not execute")
'''
    # A fixture dependent on the simulator must leave the monkeypatch installed
    # until after simulator cleanup; patch directly rather than using monkeypatch's finalizer.
    source = source.replace(", monkeypatch", "").replace('monkeypatch.setattr(SERVICE, "METHOD", fail)',
                                                            'SERVICE.METHOD = fail')
    source = source.replace("RESOURCE", resource).replace("SERVICE", service)
    source = source.replace("METHOD", "check_health" if stage == "health" else stage)
    result = run_case(tmp_path, source, kind)
    assert result.returncode != 0, result.stdout + result.stderr
    assert kind.upper() + " cleanup failed" in result.stdout
    cases = ElementTree.parse(tmp_path / "report.xml").findall(".//testcase")
    assert any(c.attrib["name"] == "test_01" and c.find("error") is not None for c in cases)
    assert all(c.attrib["name"] != "test_02" for c in cases)
