"""Shared polling and lifecycle assertions; protocol tests select the actions."""
from pathlib import Path
import sys
import time
from xml.etree import ElementTree
import pytest

SDK_SUPPORTED = sys.platform == "linux"
SDK_REQUIRED = pytest.mark.skipif(not SDK_SUPPORTED, reason="Native SDK requires Linux/WSL")


def until(predicate, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Demo condition timed out")


def assert_five_real_cases(hub, kind):
    runtime = hub.get(kind)
    process, endpoint = runtime.process.process, runtime.endpoint
    hub.start_run(kind, [row["id"] for row in hub.catalog(kind)])
    until(lambda: runtime.mode == "idle")
    run = runtime.run
    assert run["status"] == "passed", Path(run["artifacts"], "pytest.log").read_text(encoding="utf-8")
    assert {case["status"] for case in run["cases"].values()} == {"passed"}
    assert len(ElementTree.parse(Path(run["artifacts"], "junit.xml")).findall(".//testcase")) == 5
    assert runtime.process.process is process and runtime.endpoint == endpoint
    assert all(not entry["values"] for entry in runtime.hardware.client.returns.snapshot().values())
    assert runtime.token is None


def assert_manual_fault_disconnect_and_recovery(hub, kind, actions):
    runtime = hub.get(kind)
    runtime.reset()
    until(lambda: runtime.mode == "idle")
    runtime.manual("inject", "timeout")
    for action in actions:
        runtime.manual(action)
        until(lambda: runtime.controller_state["status"] != "running")
    assert runtime.controller_state["fault"] == "timeout"
    runtime.reset()
    until(lambda: runtime.mode == "idle")
    old = runtime.process.process
    old.kill(); old.wait(3)
    until(lambda: runtime.environment_failed)
    assert runtime.snapshot["values"] == {}
    runtime.reset()
    until(lambda: runtime.mode == "idle" and runtime.snapshot["connected"])
    assert runtime.process.process is not old
    assert not runtime.environment_failed


def assert_forced_cancel_rebuilds_only_active_device(hub, kind):
    runtime = hub.get(kind)
    other = hub.get("plc")
    other_process = other.process.process
    old = runtime.process.process
    runtime.start_run(["timeout"])
    until(lambda: any(event["kind"] == "injected" and event["time"] >= runtime.run["started"]
                      for event in runtime.events))
    process = runtime.pytest_process
    runtime.cancel()
    with runtime.lock:
        runtime.cancel_at -= 11
    until(lambda: runtime.mode == "idle")
    assert process.poll() is not None and old.poll() is not None
    assert runtime.process.process is not old
    assert other.process.process is other_process and other_process.poll() is None
    assert runtime.run["status"] == "error" and not runtime.environment_failed
    assert runtime.controller_state["status"] != "running"
