"""Demo-only reporting and SUT fixtures; load explicitly with -p."""
from pathlib import Path
import time

import pytest

from .catalog import CASE_BY_TEST
from .control import Controller, FaultInjector
from .events import EventWriter


def pytest_addoption(parser):
    group = parser.getgroup("plc-demo")
    group.addoption("--demo-events", required=True, help="Structured JSONL output")
    group.addoption("--demo-cancel-file", required=True, help="Cooperative cancellation marker")
    group.addoption("--demo-hold", type=float, default=0.8, help="Result display dwell in seconds")


def pytest_configure(config):
    config._demo_writer = EventWriter(Path(config.getoption("--demo-events")))
    config._demo_reports = {}
    config._demo_cancelled = set()


def pytest_sessionstart(session):
    session.config.pluginmanager.register(Reporter(session), "demo-reporter")


class Reporter:
    def __init__(self, session):
        self.session = session
        self.config = session.config
        self.writer = self.config._demo_writer

    def pytest_runtest_logstart(self, nodeid, location):
        self.writer.case_id = CASE_BY_TEST.get(nodeid.rsplit("::", 1)[-1], nodeid.rsplit("::", 1)[-1])
        self.config._demo_reports[nodeid] = []
        self.writer.emit("case_start", "准备环境：Reset 与建立连接")

    def pytest_runtest_logreport(self, report):
        self.config._demo_reports.setdefault(report.nodeid, []).append(report)
        phase = {"setup": "准备", "call": "断言", "teardown": "清理"}[report.when]
        self.writer.emit("phase", phase + "：" + report.outcome, phase=report.when,
                         outcome=report.outcome, detail=str(report.longrepr) if report.failed else "")
        if report.when in ("setup", "teardown") and report.failed:
            self.session.shouldstop = "Demo environment setup/cleanup failed"
            self.writer.emit("environment_error", phase + "失败，停止后续用例")
        if report.when == "teardown" and report.passed:
            self.writer.emit("cleanup", "写入任务已退出，Reset 与断开连接已完成")

    def pytest_runtest_logfinish(self, nodeid, location):
        reports = self.config._demo_reports[nodeid]
        if any(r.failed and r.when != "call" for r in reports):
            status = "error"
        elif nodeid in self.config._demo_cancelled:
            status = "cancelled"
        elif any(r.failed for r in reports):
            status = "failed"
        elif any(r.skipped for r in reports):
            status = "skipped"
        elif {r.when for r in reports} == {"setup", "call", "teardown"}:
            status = "passed"
        else:
            status = "error"
        self.writer.emit("case_result", "用例已结束", status=status,
                         duration=sum(r.duration for r in reports))


class DemoContext:
    def __init__(self, request):
        self.request = request
        self.writer = request.config._demo_writer
        self.cancel_file = Path(request.config.getoption("--demo-cancel-file"))

    def emit(self, kind, message, **data):
        self.writer.emit(kind, message, **data)

    def check_cancelled(self):
        if self.cancel_file.exists():
            self.request.config._demo_cancelled.add(self.request.node.nodeid)
            self.request.session.shouldstop = "Demo cancelled by user"
            self.emit("cancelled", "收到停止请求，正在清理用例")
            pytest.skip("Demo cancelled by user")

    def wait(self, predicate, timeout, message):
        deadline = time.monotonic() + timeout
        while True:
            self.check_cancelled()
            value = predicate()
            if value:
                return value
            if time.monotonic() >= deadline:
                raise AssertionError(message)
            time.sleep(0.05)

    def hold(self):
        deadline = time.monotonic() + self.request.config.getoption("--demo-hold")
        while time.monotonic() < deadline:
            self.check_cancelled()
            time.sleep(0.05)


@pytest.fixture
def demo_context(plc_nodes, request):
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
def demo_controller(plc_nodes, plc_service, demo_context, request):
    controller = Controller(plc_service, demo_context.emit)
    register_cleanup(request, controller, demo_context, "示例控制器已退出")
    return controller


@pytest.fixture
def demo_injector(plc_nodes, plc_service, demo_context, request):
    injector = FaultInjector(plc_service, demo_context.emit)
    register_cleanup(request, injector, demo_context, "故障写入任务已退出")
    return injector
