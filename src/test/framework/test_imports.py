"""Fresh interpreters exercise namespace discovery and import-order independence."""
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration

from framework.source import hidden_process_options, source_environment


@pytest.mark.parametrize("modules", [
    ["framework.pipe", "framework.transport", "framework.runner"],
    ["framework.runner", "framework.transport", "framework.pipe"],
    ["subsystems.motion.rotary_axis.model", "subsystems.motion.rotary_axis.adapter"],
    ["subsystems.motion.rotary_axis.adapter", "subsystems.motion.rotary_axis.model"],
])
def test_import_order_outside_repository(modules, tmp_path):
    script = """
import importlib
import sys
for name in sys.argv[1:]:
    importlib.import_module(name)
from framework.config import DEFAULT_DEVICE, load_device, load_protocol
from framework.hosting import definition
from protocols.opcua.contracts import OPCUADefinition
from protocols.sdk.contracts import SDKDefinition
from protocols.tcp.contracts import TCPDefinition
from framework import messages, transport
from subsystems.motion.rotary_axis import abi, model
assert transport.EnvironmentError is messages.EnvironmentError
assert model.Result is abi.Result
assert model.FUNCTIONS is abi.FUNCTIONS
expected = {'opcua': OPCUADefinition, 'sdk': SDKDefinition, 'tcp': TCPDefinition}
_, rows = load_device(DEFAULT_DEVICE)
assert len(rows) == 3
for _, _, config in rows:
    host = load_protocol(config.type)
    assert host.create(config).identity == config.identity
    definition(config, expected[config.type])
"""
    result = subprocess.run([sys.executable, "-c", script, *modules], cwd=tmp_path,
                            env=source_environment(), capture_output=True, text=True,
                            timeout=30, **hidden_process_options())
    assert result.returncode == 0, result.stdout + result.stderr
