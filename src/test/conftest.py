"""Repository-only build fixture; public device fixtures live in pytest_plugin."""
import pytest

pytest_plugins = ["pytest_plugin"]


@pytest.fixture(scope="session")
def sdk_library(tmp_path_factory):
    from subsystems.motion.rotary_axis.build import build_reference_sdk
    return build_reference_sdk(tmp_path_factory.mktemp("sdk-library"))
