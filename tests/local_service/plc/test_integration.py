import queue
import socket
from datetime import datetime

import pytest
from opcua import ua

from local_service.plc.bindings import load_bindings, read_values, reset_nodes
from local_service.plc.process import SimulatorProcess
from tests.helpers import eventually

pytestmark = pytest.mark.integration


def test_all_eight_nodes_are_native_writable_scalars(plc_client, plc_nodes):
    from opcua import Node
    assert len(plc_nodes) == 8
    for key, node in plc_nodes.items():
        assert isinstance(node, Node)
        assert ua.AccessLevel.CurrentWrite in node.get_user_access_level()
        assert node.get_value_rank() == ua.ValueRank.Scalar
        binding = load_bindings()[key]
        node.set_value(binding.baseline, binding.ua_type)
    assert reset_nodes(plc_client) is True


def test_repeated_and_combined_native_writes_need_no_intermediate_reset(plc_nodes):
    pressure = plc_nodes["vacuum.pressure_pa"]
    for value in (200000.0, -200000.0, 12345.0):
        pressure.set_value(value, ua.VariantType.Double)
        assert pressure.get_value() == value
    expected = {"vacuum.state_code": 65535, "vacuum.result_code": 3, "vacuum.alarm_code": 7}
    for key, value in expected.items():
        plc_nodes[key].set_value(value, ua.VariantType.UInt16)
    for key, value in expected.items():
        assert plc_nodes[key].get_value() == value
    assert pressure.get_value() == 12345.0


@pytest.mark.parametrize("key,variant", [
    ("vacuum.pressure_pa", ua.Variant(12, ua.VariantType.UInt16)),
    ("vacuum.pressure_pa", ua.Variant("123", ua.VariantType.String)),
    ("vacuum.pressure_pa", ua.Variant([123.0], ua.VariantType.Double)),
    ("vacuum.valve_open", ua.Variant(1, ua.VariantType.UInt16)),
    ("vacuum.state_code", ua.Variant(1.0, ua.VariantType.Double)),
])
def test_wrong_wire_type_is_rejected_without_changing_node(plc_nodes, key, variant):
    before = plc_nodes[key].get_value()
    with pytest.raises(ua.UaStatusCodeError) as error:
        plc_nodes[key].set_value(variant)
    assert error.value.code == ua.StatusCodes.BadTypeMismatch
    assert plc_nodes[key].get_value() == before


def test_remote_quality_timestamps_and_reset_reuse_same_connection(plc_client, plc_nodes):
    pressure = plc_nodes["vacuum.pressure_pa"]
    stamp = datetime(2020, 1, 2, 3, 4, 5)
    data = ua.DataValue(ua.Variant(None, ua.VariantType.Null))
    data.StatusCode = ua.StatusCode(ua.StatusCodes.BadSensorFailure)
    data.SourceTimestamp = stamp
    data.ServerTimestamp = stamp
    pressure.set_value(data)
    observed = read_values(plc_client, plc_nodes)["vacuum.pressure_pa"]
    assert observed.Value.Value is None
    assert observed.StatusCode.value == ua.StatusCodes.BadSensorFailure
    assert observed.SourceTimestamp == observed.ServerTimestamp == stamp
    plc_nodes["vacuum.command"].set_value(2, ua.VariantType.UInt16)
    eventually(lambda: plc_nodes["vacuum.valve_open"].get_value() is True)
    assert read_values(plc_client, plc_nodes)["vacuum.pressure_pa"].SourceTimestamp == stamp
    reset_nodes(plc_client)
    for key, data in read_values(plc_client, plc_nodes).items():
        assert data.Value.Value == load_bindings()[key].baseline
        assert data.StatusCode.is_good()
        assert data.SourceTimestamp > stamp
        assert data.ServerTimestamp > stamp
    pressure.set_value(222.0, ua.VariantType.Double)
    assert pressure.get_value() == 222.0


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf")])
def test_bad_numeric_pressure_does_not_stop_server(plc_client, plc_nodes, value):
    plc_nodes["vacuum.pressure_pa"].set_value(
        ua.Variant(value, ua.VariantType.Null if value is None else ua.VariantType.Double))
    plc_nodes["vacuum.command"].set_value(2, ua.VariantType.UInt16)
    eventually(lambda: plc_nodes["vacuum.command"].get_value() == 0)
    assert plc_nodes["vacuum.valve_open"].get_value() is True
    reset_nodes(plc_client)
    plc_nodes["vacuum.command"].set_value(1, ua.VariantType.UInt16)
    eventually(lambda: plc_nodes["vacuum.pressure_pa"].get_value() < 101325)


def test_native_pressure_write_is_used_on_the_next_tick(plc_nodes):
    plc_nodes["vacuum.command"].set_value(1, ua.VariantType.UInt16)
    eventually(lambda: plc_nodes["vacuum.pressure_pa"].get_value() < 50000)
    # Far below the target: the next exponential update moves upward from here.
    plc_nodes["vacuum.pressure_pa"].set_value(-1000000.0, ua.VariantType.Double)
    after = eventually(lambda: (p if -1000000 < (p := plc_nodes["vacuum.pressure_pa"].get_value()) < 0 else None))
    assert after < 0  # A separately retained normal pressure would still be positive.


class ChangeHandler:
    def __init__(self):
        self.values = queue.Queue()

    def datachange_notification(self, node, value, data):
        self.values.put(value)


def test_reset_preserves_subscription_and_connection(plc_client, plc_nodes):
    handler = ChangeHandler()
    subscription = plc_client.create_subscription(50, handler)
    subscription.subscribe_data_change(plc_nodes["vacuum.pressure_pa"])
    try:
        assert handler.values.get(timeout=3) == 101325
        plc_nodes["vacuum.pressure_pa"].set_value(200000.0, ua.VariantType.Double)
        assert handler.values.get(timeout=3) == 200000
        reset_nodes(plc_client)
        assert handler.values.get(timeout=3) == 101325
        plc_nodes["vacuum.pressure_pa"].set_value(456.0, ua.VariantType.Double)
        assert handler.values.get(timeout=3) == 456
    finally:
        subscription.delete()


def test_owned_process_stops_and_releases_port():
    process = SimulatorProcess().start()
    from urllib.parse import urlparse
    port = urlparse(process.endpoint).port
    child = process.process
    process.stop()
    assert child.poll() == 0
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_port_conflict_leaves_existing_process_alive():
    with SimulatorProcess() as existing:
        from urllib.parse import urlparse
        duplicate = SimulatorProcess(urlparse(existing.endpoint).port)
        try:
            with pytest.raises(RuntimeError, match="startup failed"):
                duplicate.start()
            assert existing.process.poll() is None
        finally:
            duplicate.stop()
