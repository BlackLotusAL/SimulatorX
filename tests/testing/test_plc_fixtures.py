import json
import subprocess
import sys
from xml.etree import ElementTree

import pytest

from local_service.plc.bindings import load_bindings, read_values
from local_service.plc.process import hidden_process_options
from local_service.common.runtime import source_environment


def run_child(tmp_path, endpoint, source):
    (tmp_path / "conftest.py").write_text('pytest_plugins = ["pytest_plugin"]\n', encoding="utf-8")
    (tmp_path / "test_failures.py").write_text(source, encoding="utf-8")
    return subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "-q", str(tmp_path),
                           "--opcua-endpoint", endpoint, "--junitxml", str(tmp_path / "junit.xml")],
                          cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
                          timeout=30, env=source_environment(), **hidden_process_options())


def test_loading_plugin_does_not_connect_until_a_fixture_is_requested(tmp_path):
    result = run_child(tmp_path, "opc.tcp://127.0.0.1:1/unavailable/", '''
def test_without_plc_resources():
    assert 1 + 1 == 2
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_cleanup_after_initial_reset_setup_and_assertion_failure(plc_service, plc_client, plc_nodes, tmp_path):
    result = run_child(tmp_path, plc_service, '''
import json
import threading
from pathlib import Path

import pytest
from opcua import ua
from local_service.plc.bindings import load_bindings
import testing.plc as plugin

def record(test, event):
    with Path("cleanup-order.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"test": test, "event": event}) + "\\n")

@pytest.fixture(autouse=True)
def trace_reset(request, monkeypatch):
    original = plugin.reset_nodes
    calls = 0
    def reset(client):
        nonlocal calls
        calls += 1
        original(client)
        record(request.node.name, "reset")
        if request.node.name == "test_00_reset_preparation" and calls == 1:
            raise RuntimeError("intentional initial Reset failure")
    monkeypatch.setattr(plugin, "reset_nodes", reset)

def test_00_reset_preparation(plc_nodes):
    pass

@pytest.fixture
def broken_setup(plc_nodes):
    plc_nodes["vacuum.pressure_pa"].set_value(200000.0, ua.VariantType.Double)
    raise RuntimeError("intentional setup failure")

def test_01_setup(broken_setup):
    pass

@pytest.fixture
def writing_task(plc_nodes, request):
    stopped = threading.Event()
    first_write = threading.Event()
    failures = []
    def write():
        try:
            while not stopped.is_set():
                plc_nodes["vacuum.alarm_code"].set_value(7, ua.VariantType.UInt16)
                first_write.set()
                stopped.wait(0.01)
        except Exception as exc:
            failures.append(exc)
    thread = threading.Thread(target=write, daemon=True)
    def finish():
        stopped.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert not failures
        record(request.node.name, "writer_stopped")
    request.addfinalizer(finish)
    thread.start()
    assert first_write.wait(timeout=3)

def test_02_assertion(writing_task):
    assert False, "intentional assertion failure"

def test_03_clean(plc_nodes):
    for key, node in plc_nodes.items():
        assert node.get_value() == load_bindings()[key].baseline
''')
    assert result.returncode == 1, result.stdout + result.stderr
    cases = ElementTree.parse(tmp_path / "junit.xml").findall(".//testcase")
    assert len(cases) == 4
    assert sum(case.find("failure") is not None for case in cases) == 1
    assert sum(case.find("error") is not None for case in cases) == 2
    for key, data in read_values(plc_client, plc_nodes).items():
        assert data.Value.Value == load_bindings()[key].baseline
        assert data.StatusCode.is_good()
    records = [json.loads(line) for line in (tmp_path / "cleanup-order.jsonl").read_text(encoding="utf-8").splitlines()]
    assertion = [entry["event"] for entry in records if entry["test"] == "test_02_assertion"]
    assert assertion == ["reset", "writer_stopped", "reset"]
    preparation = [entry["event"] for entry in records if entry["test"] == "test_00_reset_preparation"]
    assert preparation == ["reset", "reset"]


@pytest.mark.parametrize("stage", ["reset", "disconnect"])
def test_cleanup_failure_stops_later_cases(plc_service, plc_nodes, tmp_path, stage):
    source = '''
import pytest
from opcua import Client
import testing.plc as plugin

@pytest.fixture(autouse=True)
def break_cleanup(monkeypatch):
    if STAGE == "disconnect":
        original = Client.disconnect
        def fail_after_disconnect(self):
            original(self)
            raise RuntimeError("intentional cleanup failure")
        monkeypatch.setattr(Client, "disconnect", fail_after_disconnect)
        return
    original = plugin.reset_nodes
    calls = 0
    def reset(client):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("intentional cleanup failure")
        return original(client)
    monkeypatch.setattr(plugin, "reset_nodes", reset)

def test_01(plc_nodes):
    pass

def test_02_must_not_run():
    from pathlib import Path
    Path("should-not-exist").write_text("ran")
'''
    result = run_child(tmp_path, plc_service, source.replace("STAGE", repr(stage)))
    assert result.returncode == 2, result.stdout + result.stderr
    assert {"reset": "PLC cleanup failed", "disconnect": "PLC disconnect failed"}[stage] in result.stdout
    assert not (tmp_path / "should-not-exist").exists()
    cases = ElementTree.parse(tmp_path / "junit.xml").findall(".//testcase")
    assert len(cases) == 1
    assert cases[0].find("error") is not None
