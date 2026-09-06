"""Load with pytest_plugins = ['simulatorx.pytest_plugin']; request fixtures to use them."""

import pytest
from opcua import Client

from .bindings import load_bindings, reset_nodes
from .process import SimulatorProcess


def pytest_addoption(parser):
    group = parser.getgroup("simulatorx")
    group.addoption("--opcua-endpoint", default=None,
                    help="Existing OPC UA server; omit to start an isolated process")


def pytest_configure(config):
    if config.getoption("numprocesses", default=None) not in (None, 0):
        raise pytest.UsageError("SimulatorX fixtures require serial tests; use independent CI jobs for parallelism")


@pytest.fixture(scope="session")
def plc_service(request):
    """Return an endpoint; stop only a service created by this fixture."""
    endpoint = request.config.getoption("--opcua-endpoint")
    if not endpoint:
        process = SimulatorProcess()
        request.addfinalizer(process.stop)
        process.start()
        endpoint = process.endpoint
    return endpoint


@pytest.fixture
def plc_client(plc_service, request):
    """Connect a native client for one test and disconnect after dependent fixtures."""
    client = Client(plc_service, timeout=1)
    try:
        client.connect()
    except BaseException:
        try:
            client.disconnect()
        except Exception:
            pass
        raise

    def disconnect():
        try:
            client.disconnect()
        except Exception:
            request.session.shouldstop = "PLC disconnect failed; environment must not be reused"
            raise

    request.addfinalizer(disconnect)
    return client


@pytest.fixture
def plc_nodes(plc_client, request):
    """Reset before/after a test and return logical names mapped to native Node objects.

    Framework-owned SUT and writer fixtures must depend on this fixture so their
    teardown finishes before the final Reset.
    """
    def restore():
        try:
            reset_nodes(plc_client)
        except Exception:
            request.session.shouldstop = "PLC cleanup failed; restart the service"
            raise

    # Register before preparation: setup and assertion failures also reset.
    request.addfinalizer(restore)
    reset_nodes(plc_client)
    return {key: binding.resolve(plc_client) for key, binding in load_bindings().items()}
