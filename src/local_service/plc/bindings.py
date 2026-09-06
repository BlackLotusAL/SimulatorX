import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from opcua import ua

NAMESPACE_URI = "urn:simulatorx:mvp:vacuum"
CHAMBER_ID = "VacuumChamber1"
RESET_ID = "VacuumChamber1.Reset"


def reset_nodes(client):
    idx = client.get_namespace_index(NAMESPACE_URI)
    return client.get_node(ua.NodeId(CHAMBER_ID, idx)).call_method(ua.NodeId(RESET_ID, idx))


def read_values(client, nodes):
    params = ua.ReadParameters()
    params.TimestampsToReturn = ua.TimestampsToReturn.Both
    for node in nodes.values():
        item = ua.ReadValueId()
        item.NodeId, item.AttributeId = node.nodeid, ua.AttributeIds.Value
        params.NodesToRead.append(item)
    return dict(zip(nodes, client.uaclient.read(params)))


def describe(dv):
    value = dv.Value.Value
    if isinstance(value, float) and not math.isfinite(value):
        value = str(value)
    return {"value": value, "status_code": dv.StatusCode.name,
            "source_timestamp": dv.SourceTimestamp.isoformat() if dv.SourceTimestamp else None}


def resource(name: str) -> Path:
    return Path(__file__).resolve().parent / "resources" / name


@dataclass(frozen=True)
class Binding:
    key: str
    namespace_uri: str
    identifier: str
    variant_type: str
    writable: bool
    baseline: Union[bool, int, float]

    @property
    def ua_type(self):
        return getattr(ua.VariantType, self.variant_type)

    def resolve(self, server_or_client):
        idx = server_or_client.get_namespace_index(self.namespace_uri)
        return server_or_client.get_node(ua.NodeId.from_string(f"ns={idx};{self.identifier}"))


def load_bindings(path=None):
    rows = json.loads(Path(path or resource("bindings.json")).read_text(encoding="utf-8"))
    bindings = {row["key"]: Binding(**row) for row in rows}
    if len(bindings) != len(rows):
        raise ValueError("Duplicate binding keys")
    return bindings


def validate_bindings(server, bindings):
    nodes = {}
    seen = set()
    for key, binding in bindings.items():
        node = binding.resolve(server)
        if node.nodeid in seen:
            raise ValueError(f"Duplicate bound NodeId: {node.nodeid}")
        seen.add(node.nodeid)
        if (node.get_node_class() != ua.NodeClass.Variable or node.get_data_type_as_variant_type() != binding.ua_type
                or node.get_value_rank() != ua.ValueRank.Scalar):
            raise ValueError(f"Node type mismatch: {key}")
        for access in (node.get_access_level(), node.get_user_access_level()):
            if ua.AccessLevel.CurrentRead not in access or (ua.AccessLevel.CurrentWrite in access) != binding.writable:
                raise ValueError(f"Node access mismatch: {key}")
        nodes[key] = node
    return nodes
