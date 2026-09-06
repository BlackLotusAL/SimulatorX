"""Exercise the plugin's ownership and isolation contract through real services."""
import subprocess
import sys
from contextlib import ExitStack

import pytest
from opcua import Client, ua

from local_service.common.runtime import hidden_process_options, source_environment
from local_service.plc.bindings import load_bindings, reset_nodes
from local_service.plc.process import SimulatorProcess
from local_service.process import ServiceProcess


linux = pytest.mark.skipif(sys.platform != "linux", reason="All three services include the Linux SDK")


def run_tests(directory, source, conftest="", options=()):
    (directory / "conftest.py").write_text(
        'pytest_plugins = ["pytest_plugin"]\n' + conftest, encoding="utf-8")
    (directory / "test_case.py").write_text(source, encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_case.py", *options],
                            cwd=directory, env=source_environment(), capture_output=True,
                            text=True, timeout=60, **hidden_process_options())
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", [None, "plc", "sdk", "tcp"])
def test_plugin_only_starts_the_requested_component(tmp_path, kind):
    if kind == "sdk" and sys.platform != "linux":
        pytest.skip("Linux SDK")
    guard = '''
from local_service.plc.process import SimulatorProcess
from local_service.process import ServiceProcess

original_plc = SimulatorProcess.start
original_other = ServiceProcess.start

def start_plc(self, *args, **kwargs):
    assert REQUESTED == "plc", "Unexpected PLC startup"
    return original_plc(self, *args, **kwargs)

def start_other(self, *args, **kwargs):
    assert self.kind == REQUESTED, "Unexpected SDK/TCP startup"
    return original_other(self, *args, **kwargs)

SimulatorProcess.start = start_plc
ServiceProcess.start = start_other
'''.replace("REQUESTED", repr(kind))
    resource = {None: "", "plc": "plc_nodes", "sdk": "sdk_axis", "tcp": "tcp_responses"}[kind]
    run_tests(tmp_path, "def test_case(" + resource + "):\n    assert " + (resource or "True") + "\n", guard)


@linux
@pytest.mark.parametrize("target", ["plc", "sdk", "tcp"])
def test_reset_only_changes_the_selected_component(target, plc_client, plc_nodes, sdk_axis, tcp_service, tcp_responses):
    plc_nodes["vacuum.alarm_code"].set_value(7, ua.VariantType.UInt16)
    assert sdk_axis.call("Enable")["return_value"] == 0
    sdk_axis.returns["rotary.MoveAbsolute"].set_sequence([77])
    tcp_responses["READ_STATUS"].set_sequence([{"status": 23}])
    sdk_before = sdk_axis.returns.snapshot()
    tcp_before = tcp_responses.snapshot()

    {"plc": lambda: reset_nodes(plc_client), "sdk": sdk_axis.reset, "tcp": tcp_service.reset}[target]()

    assert plc_nodes["vacuum.alarm_code"].get_value() == (0 if target == "plc" else 7)
    assert sdk_axis.snapshot()["enabled"] == (target != "sdk")
    assert sdk_axis.returns.snapshot() == ({} if target == "sdk" else sdk_before)
    assert tcp_responses.snapshot() == ({} if target == "tcp" else tcp_before)
    assert sdk_axis.check_health() and tcp_service.check_health()


@linux
@pytest.mark.parametrize("target", ["plc", "sdk", "tcp"])
def test_stopping_one_owned_process_preserves_other_services(target):
    with ExitStack() as owners:
        processes = {"plc": owners.enter_context(SimulatorProcess()),
                     "sdk": owners.enter_context(ServiceProcess("sdk")),
                     "tcp": owners.enter_context(ServiceProcess("tcp"))}
        sdk, tcp = processes["sdk"].client, processes["tcp"].client
        sdk.call("Enable")
        tcp.responses["READ_STATUS"].set_sequence([{"status": 23}])
        child = processes[target].process
        processes[target].stop()
        assert child.poll() == 0

        if target != "plc":
            with Client(processes["plc"].endpoint, timeout=1) as client:
                assert load_bindings()["vacuum.alarm_code"].resolve(client).get_value() == 0
        if target != "sdk":
            assert sdk.check_health() and sdk.snapshot()["enabled"]
        if target != "tcp":
            assert tcp.check_health()
            assert tcp.responses["READ_STATUS"].snapshot()["remaining"] == 1


@linux
def test_external_services_are_reset_but_not_stopped(tmp_path, plc_service, plc_nodes, sdk_axis, tcp_service, tcp_responses):
    plc_nodes["vacuum.alarm_code"].set_value(7, ua.VariantType.UInt16)
    sdk_axis.call("Enable")
    tcp_responses["READ_STATUS"].set_sequence([{"status": 23}])
    source = '''
from opcua import Client, ua
from local_service.plc.bindings import load_bindings

def test_00_service_fixtures_do_not_reset(plc_service, sdk_service, tcp_service):
    with Client(plc_service) as client:
        assert load_bindings()["vacuum.alarm_code"].resolve(client).get_value() == 7
    assert sdk_service.snapshot()["enabled"]
    assert tcp_service.responses["READ_STATUS"].snapshot()["remaining"] == 1

def test_01_resource_fixtures_reset_before_and_after(plc_nodes, sdk_axis, sdk_returns, tcp_responses):
    assert plc_nodes["vacuum.alarm_code"].get_value() == 0
    assert not sdk_axis.snapshot()["enabled"]
    assert sdk_returns.snapshot() == {} and tcp_responses.snapshot() == {}
    plc_nodes["vacuum.alarm_code"].set_value(9, ua.VariantType.UInt16)
    sdk_axis.call("Enable")
    sdk_returns["rotary.MoveAbsolute"].set_sequence([88])
    tcp_responses["READ_STATUS"].set_sequence([{"status": 42}])
'''
    host, port = tcp_service.control_endpoint
    run_tests(tmp_path, source, options=["--opcua-endpoint", plc_service,
                                        "--sdk-control", sdk_axis.control_endpoint,
                                        "--tcp-control", f"{host}:{port}"])
    assert plc_nodes["vacuum.alarm_code"].get_value() == 0
    assert sdk_axis.check_health() and not sdk_axis.snapshot()["enabled"]
    assert sdk_axis.returns.snapshot() == {}
    assert tcp_service.check_health() and tcp_responses.snapshot() == {}


@linux
def test_sdk_resources_share_one_reset_per_case_boundary(tmp_path):
    guard = '''
from pathlib import Path
from local_service.sdk.client import SDKClient

original_reset = SDKClient.reset
def reset(self):
    with Path("resets.txt").open("a") as stream:
        stream.write("reset\\n")
    return original_reset(self)
SDKClient.reset = reset
'''
    source = '''
from pathlib import Path

def test_both(sdk_returns, sdk_axis):
    assert Path("resets.txt").read_text().splitlines() == ["reset"]
    assert sdk_returns is sdk_axis.returns
'''
    run_tests(tmp_path, source, guard)
    assert (tmp_path / "resets.txt").read_text().splitlines() == ["reset", "reset"]
