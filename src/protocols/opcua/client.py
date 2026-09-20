"""Native OPC UA client with the hardware lifecycle control operations."""
from opcua import Client, ua

from .bindings import describe, read_values


def reset_nodes(client, definition):
    """Call the device-defined native Reset method on an existing connection."""
    idx = client.get_namespace_index(definition.namespace_uri)
    return client.get_node(ua.NodeId(definition.object_id, idx)).call_method(
        ua.NodeId(definition.reset_id, idx))


class PLCClient(Client):
    def __init__(self, endpoint, bindings, definition):
        super().__init__(endpoint, timeout=2)
        self.bindings = bindings
        self.definition = definition
        self.nodes = {}

    def connect(self):
        super().connect()
        self.nodes = {key: binding.resolve(self) for key, binding in self.bindings.items()}

    def check_health(self):
        # Read attributes directly so injected Bad quality is not a transport failure.
        read_values(self, self.nodes)
        return True

    def reset(self):
        return reset_nodes(self, self.definition)

    def diagnostics(self):
        return {"nodes": {key: describe(value) for key, value in read_values(self, self.nodes).items()}}

    def close(self):
        self.disconnect()
