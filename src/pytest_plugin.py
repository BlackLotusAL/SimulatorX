"""Register with pytest_plugins = ["pytest_plugin"]; fixtures start services on demand."""
import pytest

pytest_plugins = ["testing.plc", "testing.sdk", "testing.tcp"]


def pytest_addoption(parser):
    group = parser.getgroup("simulatorx")
    group.addoption("--opcua-endpoint", default=None,
                    help="Existing OPC UA server; omit to start an isolated process")
    group.addoption("--sdk-control", default=None, help="Existing SDK control Unix socket")
    group.addoption("--sdk-profile", default=None, help="JSON rotary-axis profile for an owned SDK service")
    group.addoption("--sdk-library", default=None, help="Use an existing .so for the sdk_library fixture")
    group.addoption("--tcp-control", default=None, help="Existing TCP control endpoint as host:port")
    group.addoption("--tcp-protocol", default=None, help="TCP protocol adapter module:factory")
    group.addoption("--simulatorx-artifacts", default="artifacts/simulatorx",
                    help="Directory for per-test SDK/TCP diagnostics saved before Reset")


def pytest_configure(config):
    if config.getoption("numprocesses", default=None) not in (None, 0):
        raise pytest.UsageError("SimulatorX fixtures require serial tests; use independent CI jobs for parallelism")
