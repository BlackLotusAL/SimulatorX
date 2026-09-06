"""Shared pytest phase aggregation and recovery, exercised through TCP."""
import pytest
pytest.importorskip("flask")
from demo.tests.support import until

DEMO_PROTOCOLS = ("tcp",)


@pytest.mark.parametrize("stage", ["setup", "call", "teardown"])
def test_real_pytest_failure_aggregation_and_recovery(hub, tmp_path, monkeypatch, stage):
    runtime = hub.get("tcp")
    source = '''
import pytest
from demo.common.protocol_pytest import BridgeClient
from demo.tcp.cases import exercise

@pytest.fixture(autouse=True)
def failure(monkeypatch):
    original = BridgeClient.request
    resets = 0
    def request(client, operation, **fields):
        nonlocal resets
        if operation == "reset":
            resets += 1
            if STAGE == "teardown" and resets == 2:
                raise RuntimeError("intentional cleanup failure")
        return original(client, operation, **fields)
    monkeypatch.setattr(BridgeClient, "request", request)
    if STAGE == "setup":
        raise RuntimeError("intentional preparation failure")

def test_normal(scenario):
    exercise(scenario, "normal")
    if STAGE == "call":
        assert False, "intentional assertion failure"

def test_recover(scenario):
    exercise(scenario, "recover")
'''
    path = tmp_path / "test_phases.py"
    path.write_text(source.replace("STAGE", repr(stage)), encoding="utf-8")
    original = runtime.suite_config
    def config(directory):
        _, *rest = original(directory)
        return path, *rest
    monkeypatch.setattr(runtime, "suite_config", config)
    runtime.start_run(["normal", "recover"])
    until(lambda: runtime.mode == "idle")
    assert runtime.run["status"] == ("failed" if stage == "call" else "error")
    assert runtime.run["cases"]["recover"]["status"] == ("passed" if stage == "call" else "not_run")
    assert runtime.snapshot["connected"] and not runtime.environment_failed
