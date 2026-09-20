"""Example-only integration: the framework never imports this project."""
import pytest
from opcua import ua

from reference_sut import DetectorSUT, MotionSUT, VacuumSUT
from integration_support import finish_sut

pytest_plugins = ["pytest_plugin"]


def pytest_addoption(parser):
    parser.addoption("--sut-artifacts", default="artifacts/sut", help="Example SUT diagnostics directory")


@pytest.fixture(scope="session")
def sdk_library(tmp_path_factory):
    from subsystems.motion.rotary_axis.build import build_reference_sdk
    return build_reference_sdk(tmp_path_factory.mktemp("example-sdk"))


@pytest.fixture
def scenario(device, request):
    """All environment setup happens before the SUT fixture is constructed."""
    kind, condition = request.param
    ids = {"vacuum": ("vacuum", "chamber_plc"), "motion": ("motion", "rotary_axis"),
           "detector": ("detector", "modbus_tcp")}
    subsystem, hardware = ids[kind]
    if subsystem not in device.subsystems or hardware not in device.subsystems[subsystem].hardware:
        pytest.skip("Required hardware is not selected: " + "/".join(ids[kind]))
    handle = device.subsystems[subsystem].hardware[hardware]
    options = {}
    if condition == "timeout":
        options["timeout"] = 0.25
    if kind == "vacuum":
        if condition == "interlock":
            handle.nodes["vacuum.valve_open"].set_value(True, ua.VariantType.Boolean)
        elif condition == "bad_quality":
            value = ua.DataValue(ua.Variant(101325.0, ua.VariantType.Double))
            value.StatusCode = ua.StatusCode(ua.StatusCodes.BadSensorFailure)
            handle.nodes["vacuum.pressure_pa"].set_value(value)
        elif condition == "invalid_pressure":
            handle.nodes["vacuum.pressure_pa"].set_value(float("nan"), ua.VariantType.Double)
        return kind, handle, options
    if kind == "motion":
        if condition == "sdk_error":
            handle.client.returns["rotary.MoveAbsolute"].set_sequence([-41])
        elif condition == "query_error":
            handle.client.returns["rotary.GetState"].set_sequence([77])
        elif condition == "limit":
            handle.client.set_fault("positive_limit", True)
        elif condition == "timeout":
            handle.client.set_fault("stalled", True)
        return kind, handle, options
    if condition == "recover":
        handle.client.responses["04:0:2"].set_sequence([{"registers": [0, 0]}, {"registers": [0, 1]}])
    elif condition == "device_error":
        handle.client.responses["04:0:2"].set_sequence([{"registers": [7, 1]}])
    elif condition == "success_then_error":
        handle.client.responses["04:0:2"].set_sequence([{"registers": [0, 1]}, {"registers": [7, 1]}])
    elif condition == "measurement_error":
        handle.client.responses["04:2:1"].set_sequence([{"exception": 4}])
    elif condition == "timeout":
        handle.client.responses["04:0:2"].set_sequence([{"registers": [0, 0]}] * 100)
    if condition != "measurement_error":
        handle.client.responses["04:2:1"].set_sequence([{"registers": [125]}])
    return kind, handle, options


@pytest.fixture
def sut(scenario, device, request, monkeypatch):
    kind, handle, options = scenario
    if kind == "vacuum":
        instance = VacuumSUT(handle.endpoints["opcua"], bindings_path=handle.bindings_path, **options)
    elif kind == "motion":
        for name, value in handle.client.launch_environment.items():
            monkeypatch.setenv(name, value)
        instance = MotionSUT(request.getfixturevalue("sdk_library"), **options)
    else:
        instance = DetectorSUT(handle.endpoints["tcp"], **options)
    request.addfinalizer(lambda: finish_sut(instance, device, request))
    return instance
