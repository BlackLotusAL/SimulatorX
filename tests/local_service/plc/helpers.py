from opcua import ua


def read_values(client, nodes):
    params = ua.ReadParameters()
    params.TimestampsToReturn = ua.TimestampsToReturn.Both
    for node in nodes:
        rv = ua.ReadValueId()
        rv.NodeId = node.nodeid
        rv.AttributeId = ua.AttributeIds.Value
        params.NodesToRead.append(rv)
    return client.uaclient.read(params)
