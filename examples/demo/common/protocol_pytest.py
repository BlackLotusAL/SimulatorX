import json
from pathlib import Path
from urllib.request import Request, urlopen
import pytest
from demo.common.pytest_support import DemoContext, pytest_sessionstart
from demo.common.pytest_support import pytest_configure as configure_reports
from framework.control import SequenceControl


def pytest_addoption(parser):
    for name in ("connection", "events", "cancel-file"):
        parser.addoption("--demo-" + name, required=True)
    parser.addoption("--demo-hold", type=float, default=0.8)


def configure_protocol(config, catalog, create_sut):
    config._demo_create_sut = create_sut
    config._descriptor = json.loads(Path(config.getoption("--demo-connection")).read_text(encoding="utf-8"))
    config._demo_case_by_test = {row["test"]: row["id"] for row in catalog}
    configure_reports(config)


class BridgeClient:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.returns = SequenceControl(self)

    def request(self, operation, **fields):
        request = Request(self.descriptor["control_url"], data=json.dumps({"op": operation, **fields}).encode(),
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.descriptor["token"]})
        with urlopen(request, timeout=5) as response:
            return json.load(response)["result"]

    def set_fault(self, name, active):
        return self.request("set_fault", name=name, active=active)


@pytest.fixture
def scenario(request):
    descriptor = request.config._descriptor
    context = DemoContext(request)
    client = BridgeClient(descriptor)
    sut = None
    def cleanup():
        # Failure to stop a writer must prevent reset and further use.
        if sut is not None:
            sut.close()
        context.emit("cleanup", "示例控制器已退出")
        diagnostics = client.request("diagnostics")
        context.emit("diagnostics", "设备诊断已保存", diagnostics=diagnostics)
        client.request("health")
        client.request("reset")
        assert client.request("sequences") == {}
        context.emit("cleanup", "设备已 Reset，响应序列已清空")
    request.addfinalizer(cleanup)
    client.request("health")
    client.request("reset")
    context.check_cancelled()
    sut = request.config._demo_create_sut(descriptor)
    context.emit("step", "已连接页面展示的同一设备实例")
    return descriptor["kind"], client, sut, context
