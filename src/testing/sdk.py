"""SDK service, motion, return sequence and native library fixtures."""
from pathlib import Path

import pytest

from .common import _case_environment, _owned_service


@pytest.fixture(scope="session")
def sdk_service(request):
    """Independent SDK control client; SUT lifetime belongs to the consuming project."""
    from local_service.sdk.client import SDKClient
    endpoint = request.config.getoption("--sdk-control")
    if endpoint and request.config.getoption("--sdk-profile"):
        raise pytest.UsageError("Configure --sdk-profile when starting the external SDK service")
    return SDKClient(endpoint) if endpoint else _owned_service(request, "sdk")


@pytest.fixture
def _sdk_case(sdk_service, request):
    return _case_environment(sdk_service, request, "sdk")


@pytest.fixture
def sdk_axis(_sdk_case):
    """Read model state and set faults without consuming SDK return sequences."""
    return _sdk_case


@pytest.fixture
def sdk_returns(_sdk_case):
    return _sdk_case.returns


@pytest.fixture(scope="session")
def sdk_library(request, tmp_path_factory):
    """Reference Linux .so, or an explicitly provided vendor-compatible test shim."""
    existing = request.config.getoption("--sdk-library")
    if existing:
        path = Path(existing).resolve()
        if not path.is_file():
            raise pytest.UsageError("--sdk-library does not exist: " + str(path))
        return path
    from local_service.sdk.build import build_reference_sdk
    return build_reference_sdk(tmp_path_factory.mktemp("sdk-library"))
