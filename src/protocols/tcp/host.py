"""TCP hardware connection and configuration; no device behavior here."""
from framework.hosting import ServiceHardware, definition, reject_settings
from .client import TCPClient
from .contracts import Protocol, ResponseModel, TCPDefinition


def components(config, spec):
    codec = spec.protocol_factory()
    model = spec.model_factory()
    if not isinstance(codec, Protocol) or not isinstance(model, ResponseModel):
        raise TypeError("TCP definition requires Protocol and ResponseModel instances")
    return codec, model


class TCPHardware(ServiceHardware):
    pipe_control = True

    def validate(self):
        reject_settings(self.config, {"control_port"})
        reject_settings(self.config, {"control_socket", "sdk_socket", "error_file", "endpoint", "control_endpoint", "mode"})
        self.definition = definition(self.config, TCPDefinition)
        value = self.config.settings.get("port", 0)
        if type(value) is not int or not 0 <= value <= 65535:
            raise ValueError("port must be in 0..65535")
        components(self.config, self.definition)

    def connect(self, info, requests):
        return TCPClient(requests)

    def client_endpoints(self):
        return {"tcp": self.client.endpoint}


def create(config):
    return TCPHardware(config)


def create_service(config):
    from .service import TCPService
    reject_settings(config, {"control_port"})
    codec, model = components(config, definition(config, TCPDefinition))
    return TCPService(codec, model, config.settings.get("port", 0))
