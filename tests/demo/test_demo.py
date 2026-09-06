"""Acceptance checks for real PLC/pytest ownership, faults and recovery."""
import json
from pathlib import Path
import subprocess
import sys
import time
from xml.etree import ElementTree

import pytest
pytest.importorskip("flask", reason="Install requirements-demo.lock for the browser demo tests")

from demo.app import create_app
from demo.catalog import CASES
from demo.control import PLCConnection, measurement_fault
from demo.runtime import DemoRuntime
from local_service.common.runtime import hidden_process_options, source_environment
from local_service.plc.bindings import describe, load_bindings, resource
from opcua import ua


def until(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for demo condition")


@pytest.fixture(scope="module")
def shared_runtime(tmp_path_factory):
    runtime = DemoRuntime(profile=resource("fast.json"), artifacts=tmp_path_factory.mktemp("demo-runs"), hold=0)
    runtime.start()
    yield runtime
    runtime.stop()


@pytest.fixture
def runtime(shared_runtime):
    runtime = shared_runtime
    until(lambda: runtime.mode == "idle")
    runtime.reset()
    until(lambda: runtime.mode == "idle" and runtime.snapshot["connected"])
    yield runtime
    runtime.cancel()
    until(lambda: runtime.mode == "idle", 40)


def assert_baseline(runtime):
    with PLCConnection(runtime.endpoint) as plc:
        for key, value in plc.read().items():
            assert value.StatusCode.is_good(), key
            assert value.Value.Value == load_bindings()[key].baseline, key


@pytest.mark.parametrize("repeat", [0, 1])
def test_all_five_real_pytest_cases_repeat_and_restore(runtime, repeat):
    client = create_app(runtime).test_client()
    result = client.post("/api/runs", json={"case_ids": [case["id"] for case in CASES]})
    assert result.status_code == 202
    until(lambda: runtime.mode == "idle", 70)
    run = runtime.state()["run"]
    log = (Path(run["artifacts"]) / "pytest.log").read_text()
    assert run["status"] == "passed", log
    assert run["exit_code"] == 0
    assert {c["status"] for c in run["cases"].values()} == {"passed"}
    junit = ElementTree.parse(Path(run["artifacts"]) / "junit.xml")
    assert len(junit.findall(".//testcase")) == 5
    assert not junit.findall(".//failure") and not junit.findall(".//error")
    events = [json.loads(line) for line in (Path(run["artifacts"]) / "events.jsonl").read_text().splitlines()]
    for case in CASES:
        own = [event for event in events if event["case_id"] == case["id"]]
        assert own[-1]["kind"] == "case_result" and own[-1]["status"] == "passed"
        assert any(event["kind"] == "cleanup" for event in own)
    assert client.get("/api/artifacts/junit.xml").status_code == 200
    assert_baseline(runtime)


def test_cancel_active_writer_and_prevent_concurrent_mutations(runtime):
    client = create_app(runtime).test_client()
    assert client.post("/api/runs", json={"case_ids": ["timeout", "normal"]}).status_code == 202
    assert client.post("/api/runs", json={"case_ids": ["normal"]}).status_code == 409
    assert client.post("/api/manual", json={"action": "open"}).status_code == 409
    assert client.post("/api/reset", json={}).status_code == 409
    until(lambda: any(e["kind"] == "injected" and e.get("fault") == "timeout"
                      and e["time"] >= runtime.run["started"] for e in runtime.events))
    assert client.post("/api/runs/cancel", json={}).status_code == 202
    until(lambda: runtime.mode == "idle", 20)
    assert runtime.run["status"] == "cancelled"
    assert runtime.run["cases"]["normal"]["status"] == "cancelled"
    assert runtime.run["cases"]["timeout"]["status"] == "cancelled"
    assert_baseline(runtime)
    time.sleep(0.25)
    assert_baseline(runtime)  # A surviving continuous writer would dirty the baseline.


def test_manual_controller_fault_and_reset(runtime):
    client = create_app(runtime).test_client()
    assert client.post("/api/manual", json={"action": "pump"}).status_code == 200
    until(lambda: runtime.snapshot["nodes"].get("vacuum.state_code", {}).get("value") == 1)
    assert client.post("/api/manual", json={"action": "inject", "fault": "sensor"}).status_code == 200
    until(lambda: runtime.manual_controller.snapshot()["status"] == "fault")
    runtime.manual_controller.stop()
    assert runtime.manual_controller.snapshot()["fault"] == "sensor_quality"
    assert client.post("/api/reset", json={}).status_code == 202
    until(lambda: runtime.mode == "idle")
    assert runtime.manual_injector.active is None
    assert_baseline(runtime)
    assert client.post("/api/manual", json={"action": "open"}).status_code == 200
    with PLCConnection(runtime.endpoint) as plc:
        assert plc.read()["vacuum.valve_open"].Value.Value is True
    assert client.post("/api/manual", json={"action": "stop"}).status_code == 200


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf")])
def test_abnormal_pressure_is_valid_json_and_measurement_fault(runtime, value):
    with PLCConnection(runtime.endpoint) as plc:
        plc.nodes["vacuum.pressure_pa"].set_value(ua.Variant(value, ua.VariantType.Null if value is None else ua.VariantType.Double))
        values = plc.read()
        assert measurement_fault(values)[0] == "invalid_pressure"
        expected = describe(values["vacuum.pressure_pa"])["value"]
    until(lambda: runtime.snapshot["nodes"]["vacuum.pressure_pa"]["value"] == expected)
    response = create_app(runtime).test_client().get("/api/state")
    def invalid_constant(value):
        raise AssertionError("Non-standard JSON constant: " + value)
    decoded = json.loads(response.data, parse_constant=invalid_constant)
    assert decoded["snapshot"]["nodes"]["vacuum.pressure_pa"]["value"] == expected
    assert decoded["samples"][-1]["pressure"] is None


def test_bad_quality_is_visible_without_crashing_observer(runtime):
    with PLCConnection(runtime.endpoint) as plc:
        data = ua.DataValue(ua.Variant(None, ua.VariantType.Null))
        data.StatusCode = ua.StatusCode(ua.StatusCodes.BadSensorFailure)
        plc.nodes["vacuum.pressure_pa"].set_value(data)
        assert measurement_fault(plc.read())[0] == "sensor_quality"
    until(lambda: runtime.snapshot["nodes"]["vacuum.pressure_pa"]["status_code"] == "BadSensorFailure")
    response = create_app(runtime).test_client().get("/api/state").get_json()
    assert response["snapshot"]["connected"]
    assert response["snapshot"]["nodes"]["vacuum.pressure_pa"]["good"] is False


def test_api_validation_and_local_origin(runtime):
    client = create_app(runtime).test_client()
    for ids in ([], ["unknown"], ["normal", "normal"], "normal", [None], None):
        assert client.post("/api/runs", json={"case_ids": ids}).status_code == 400
    assert client.post("/api/manual", json={"action": "arbitrary"}).status_code == 400
    assert client.post("/api/manual", json={"action": "inject", "fault": "arbitrary"}).status_code == 400
    assert client.post("/api/reset", json={}, headers={"Origin": "https://example.com"}).status_code == 403
    assert client.post("/api/reset", data="{}").status_code == 415
    assert client.get("/api/state?after=invalid").status_code == 400
    assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400


def test_reconnected_environment_requires_reset_before_reuse(runtime):
    runtime.emit("environment_error", "simulated recovered transport failure")
    client = create_app(runtime).test_client()
    assert runtime.snapshot["connected"]
    assert client.get("/api/state").get_json()["needs_reset"]
    assert client.post("/api/manual", json={"action": "open"}).status_code == 409
    assert client.post("/api/runs", json={"case_ids": ["normal"]}).status_code == 409
    assert client.post("/api/reset", json={}).status_code == 202
    until(lambda: runtime.mode == "idle")
    assert not runtime.environment_failed
    assert not client.get("/api/state").get_json()["needs_reset"]
    assert client.post("/api/manual", json={"action": "close"}).status_code == 200


def test_service_disconnect_invalidates_data_and_reset_rebuilds(runtime):
    runtime.process.process.kill()
    runtime.process.process.wait(timeout=3)
    until(lambda: not runtime.snapshot["connected"])
    assert runtime.snapshot["nodes"] == {}
    client = create_app(runtime).test_client()
    assert client.post("/api/manual", json={"action": "open"}).status_code == 409
    assert client.post("/api/runs", json={"case_ids": ["normal"]}).status_code == 409
    assert client.post("/api/reset", json={}).status_code == 202
    until(lambda: runtime.mode == "idle" and runtime.snapshot["connected"], 35)
    assert runtime.process.process.poll() is None
    assert_baseline(runtime)


def test_forced_cancel_rebuilds_only_owned_environment(runtime):
    runtime.start_run(["timeout"])
    until(lambda: any(e["kind"] == "injected" and e["time"] >= runtime.run["started"]
                      for e in runtime.events))
    old_plc = runtime.process.process
    old_pytest = runtime.pytest_process
    runtime.cancel()
    # Move the deadline forward instead of adding a ten-second sleep to the test.
    with runtime.lock:
        runtime.cancel_at -= 11
    until(lambda: runtime.mode == "idle", 35)
    assert old_pytest.poll() is not None
    assert old_plc.poll() is not None
    assert runtime.process.process is not old_plc
    assert runtime.process.process.poll() is None
    assert runtime.run["status"] == "error"
    assert_baseline(runtime)


@pytest.mark.parametrize("stage", ["setup", "call", "teardown"])
def test_failure_reports_and_cleanup_order(runtime, tmp_path, stage):
    source = '''
import pytest
import testing.plc as plc_fixtures

@pytest.fixture(autouse=True)
def fail_reset(monkeypatch):
    if STAGE != "teardown":
        return
    original = plc_fixtures.reset_nodes
    count = 0
    def reset(client):
        nonlocal count
        count += 1
        original(client)
        if count == 2:
            raise RuntimeError("intentional cleanup failure")
    monkeypatch.setattr(plc_fixtures, "reset_nodes", reset)

@pytest.fixture
def actors(demo_controller, demo_injector):
    demo_controller.start("pump")
    demo_injector.inject("timeout")
    if STAGE == "setup":
        raise RuntimeError("intentional preparation failure")

def test_01(actors):
    if STAGE == "call":
        assert False, "intentional assertion failure"

def test_02(plc_nodes):
    assert all(node.get_value() != 7 for node in plc_nodes.values())
'''
    test_file = tmp_path / "test_lifecycle.py"
    test_file.write_text(source.replace("STAGE", repr(stage)), encoding="utf-8")
    events_file = tmp_path / "events.jsonl"
    environment = source_environment()
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "pytest_plugin", "-p", "demo.pytest_plugin",
                             "--opcua-endpoint", runtime.endpoint, "--demo-events", str(events_file),
                             "--demo-cancel-file", str(tmp_path / "cancel"), "--demo-hold", "0", "-q", str(test_file)],
                            cwd=str(tmp_path), env=environment, capture_output=True, text=True, timeout=30,
                            **hidden_process_options())
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    own = [event for event in events if event["case_id"] == "test_01"]
    final = [event for event in own if event["kind"] == "case_result"][0]
    assert final["status"] == ("failed" if stage == "call" else "error"), result.stdout + result.stderr
    messages = [event["message"] for event in own]
    assert "故障写入任务已退出" in messages
    assert "示例控制器已退出" in messages
    if stage != "teardown":
        reset_index = messages.index("写入任务已退出，Reset 与断开连接已完成")
        assert messages.index("故障写入任务已退出") < reset_index
        assert messages.index("示例控制器已退出") < reset_index
    if stage != "call":
        assert not any(event["case_id"] == "test_02" for event in events)
        assert result.returncode == 2
    else:
        assert result.returncode == 1
    time.sleep(0.2)
    assert_baseline(runtime)
