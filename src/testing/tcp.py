"""Independent TCP service and response sequence fixtures."""
import pytest

from .common import _case_environment, _owned_service


@pytest.fixture(scope="session")
def tcp_service(request):
    from local_service.tcp.client import TCPClient
    endpoint = request.config.getoption("--tcp-control")
    if endpoint:
        if request.config.getoption("--tcp-protocol"):
            raise pytest.UsageError("Configure --tcp-protocol when starting the external TCP service")
        try:
            host, port = endpoint.rsplit(":", 1)
            if not host or not 1 <= int(port) <= 65535:
                raise ValueError()
        except ValueError:
            raise pytest.UsageError("--tcp-control must be host:port")
        return TCPClient((host, int(port)))
    return _owned_service(request, "tcp")


@pytest.fixture
def tcp_responses(tcp_service, request):
    return _case_environment(tcp_service, request, "tcp").responses
