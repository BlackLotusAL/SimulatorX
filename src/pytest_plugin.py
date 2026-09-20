"""Public pytest integration: explicit device selection and per-case Reset."""
import hashlib
import json
from pathlib import Path
import pytest
from framework.runtime import DeviceRuntime
from framework.config import DEFAULT_DEVICE


def pytest_addoption(parser):
    group = parser.getgroup("simulatorx")
    group.addoption("--simulatorx-device", default=str(DEFAULT_DEVICE))
    group.addoption("--simulatorx-select", action="append", default=None,
                    help="subsystem/hardware; repeat to select a subset")
    group.addoption("--simulatorx-artifacts", default="artifacts/simulatorx")


def pytest_configure(config):
    if config.getoption("numprocesses", default=None) not in (None, 0):
        raise pytest.UsageError("SimulatorX fixtures require serial tests; use independent CI jobs for parallelism")


@pytest.fixture(scope="session")
def device_service(request):
    runtime = DeviceRuntime.from_config(request.config.getoption("--simulatorx-device"),
                                        request.config.getoption("--simulatorx-select"))
    request.addfinalizer(runtime.stop)
    return runtime.start()


@pytest.fixture
def device(device_service, request):
    def restore():
        failures = []
        try:
            diagnostics = device_service.diagnostics()
            root = Path(request.config.getoption("--simulatorx-artifacts"))
            root.mkdir(parents=True, exist_ok=True)
            identity = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:16]
            (root / ("device-" + identity + ".json")).write_text(
                json.dumps({"test": request.node.nodeid, "hardware": diagnostics},
                           ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
            if any("diagnostic_error" in info for info in diagnostics.values()):
                failures.append(RuntimeError("Hardware diagnostics failed"))
        except Exception as exc:
            failures.append(exc)
        for action in (device_service.check_health, device_service.reset):
            try:
                action()
            except Exception as exc:
                failures.append(exc)
        if failures:
            request.session.shouldstop = "Device cleanup failed; environment must not be reused"
            raise RuntimeError(request.session.shouldstop + ": " + "; ".join(str(e) for e in failures))

    request.addfinalizer(restore)
    device_service.check_health()
    device_service.reset()
    return device_service
