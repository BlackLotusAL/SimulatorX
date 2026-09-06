from pathlib import Path
import time
import pytest
from .events import EventWriter

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
        self.writer.case_id = self.config._demo_case_by_test.get(nodeid.rsplit("::", 1)[-1], nodeid.rsplit("::", 1)[-1])
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
            self.writer.emit("cleanup", "写入任务已退出，诊断与 Reset 已完成")

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
