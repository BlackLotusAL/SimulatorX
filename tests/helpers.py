import time

from opcua import ua


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Condition did not become true before deadline")


def read_values(client, nodes):
    params = ua.ReadParameters()
    params.TimestampsToReturn = ua.TimestampsToReturn.Both
    for node in nodes:
        rv = ua.ReadValueId()
        rv.NodeId = node.nodeid
        rv.AttributeId = ua.AttributeIds.Value
        params.NodesToRead.append(rv)
    return client.uaclient.read(params)
