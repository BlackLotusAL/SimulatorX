from demo.common.protocol_pytest import (
    pytest_addoption, pytest_sessionstart, scenario, configure_protocol,
)
from .catalog import CASES
from .business import create_sut


def pytest_configure(config):
    configure_protocol(config, CASES, create_sut)
