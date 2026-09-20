"""OPC UA configuration and connection ownership."""
import xml.etree.ElementTree as ET

from framework.hosting import ServiceHardware, reject_settings, definition
from .bindings import load_bindings
from .client import PLCClient
from .contracts import OPCUADefinition


def resources(config):
    if not isinstance(config.settings.get("nodeset"), str) or not config.settings["nodeset"]:
        raise ValueError("PLC hardware entry requires nodeset: XML file path")
    return config.file("nodeset"), config.file("bindings", "bindings.json")


class OPCUAHardware(ServiceHardware):

    @property
    def nodes(self):
        return self.client.nodes

    def validate(self):
        reject_settings(self.config, {"bindings", "control_socket", "sdk_socket", "error_file"})
        self.definition = definition(self.config, OPCUADefinition)
        nodeset, self.bindings_path = resources(self.config)
        self.bindings = load_bindings(self.bindings_path)
        ET.parse(nodeset)
        port = self.config.settings.get("port", 0)
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be in 0..65535")

    def connect(self, info):
        client = PLCClient(info["opcua_endpoint"],
                           self.bindings, self.definition)
        try:
            client.connect()
        except BaseException:
            client.close()
            raise
        return client

    def client_endpoints(self):
        return {"opcua": self.client.server_url.geturl()}


def create(config):
    return OPCUAHardware(config)


def create_service(config):
    from .service import OPCUAService
    reject_settings(config, {"bindings", "control_socket", "sdk_socket", "error_file"})
    spec = definition(config, OPCUADefinition)
    nodeset, bindings = resources(config)
    return OPCUAService(spec, nodeset, bindings, config.settings.get("port", 0))
