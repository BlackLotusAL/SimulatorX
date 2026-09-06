"""Demo-only reporting and SUT fixtures; load explicitly with -p."""
from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from .catalog import CASE_BY_TEST
from .control import Controller, FaultInjector, PLCConnection


def pytest_addoption(parser):
    group = parser.getgroup("plc-demo")
    group.addoption("--demo-connection", required=True, help="Backend-owned PLC connection descriptor")
    group.addoption("--demo-events", required=True, help="Structured JSONL output")
    group.addoption("--demo-cancel-file", required=True, help="Cooperative cancellation marker")
    group.addoption("--demo-hold", type=float, default=0.8, help="Result display dwell in seconds")


from demo.common.pytest_support import Reporter, DemoContext
from demo.common.pytest_support import pytest_configure as configure_reports, pytest_sessionstart


def pytest_configure(config):
    config._demo_case_by_test = CASE_BY_TEST
    configure_reports(config)


@pytest.fixture
def demo_context(device, request):
    context = DemoContext(request)
    context.check_cancelled()
    context.emit("step", "环境已准备，所有节点恢复基线与 Good 质量")
    return context


def register_cleanup(request, actor, context, message):
    def cleanup():
        try:
            actor.stop()
            context.emit("cleanup", message)
        except Exception:
            request.session.shouldstop = "Demo writer/SUT cleanup failed; stop reusing environment"
            raise
    request.addfinalizer(cleanup)


@pytest.fixture
def demo_controller(device, demo_context, request):
    plc_service = device.subsystems["vacuum"].hardware["chamber_plc"].endpoints["opcua"]
    controller = Controller(plc_service, demo_context.emit, contract=device.contract)
    register_cleanup(request, controller, demo_context, "示例控制器已退出")
    return controller


@pytest.fixture
def demo_injector(device, demo_context, request):
    plc_service = device.subsystems["vacuum"].hardware["chamber_plc"].endpoints["opcua"]
    injector = FaultInjector(plc_service, demo_context.emit, contract=device.contract)
    register_cleanup(request, injector, demo_context, "故障写入任务已退出")
    return injector


@pytest.fixture(scope="session")
def demo_connection(request):
    descriptor = json.loads(Path(request.config.getoption("--demo-connection")).read_text(encoding="utf-8"))
    connection = PLCConnection(descriptor["endpoint"], descriptor["contract"])
    with connection:
        yield connection


@pytest.fixture
def device(demo_connection, request):
    """Connect to the backend's PLC; never create or stop a service process."""
    connection = demo_connection
    client = connection.client
    hardware = SimpleNamespace(client=client, nodes=connection.nodes,
                               endpoints={"opcua": client.server_url.geturl()})
    result = SimpleNamespace(subsystems={"vacuum": SimpleNamespace(hardware={"chamber_plc": hardware})},
                             contract=connection.contract)

    def restore():
        failures = []
        try:
            request.config._demo_writer.emit("diagnostics", "PLC node diagnostics", nodes=client.diagnostics())
        except Exception as exc:
            failures.append(exc)
        for action in (client.check_health, connection.reset):
            try:
                action()
            except Exception as exc:
                failures.append(exc)
        if failures:
            request.session.shouldstop = "Demo cleanup failed; environment must not be reused"
            raise RuntimeError("; ".join(str(error) for error in failures))

    request.addfinalizer(restore)
    client.check_health()
    connection.reset()
    return result
