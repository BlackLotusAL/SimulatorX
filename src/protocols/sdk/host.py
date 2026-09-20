"""SDK hardware ownership; native library building is supplied by the device."""
import sys
import struct
from pathlib import Path

from framework.hosting import ServiceHardware, definition, reject_settings
from .client import SDKClient
from .contracts import SDKDefinition, SDKModel, SDKAdapter


def components(config, spec):
    model = spec.model_factory()
    adapter = spec.adapter_factory()
    if not isinstance(model, SDKModel) or not isinstance(adapter, SDKAdapter):
        raise TypeError("SDK definition requires SDKModel and SDKAdapter instances")
    return model, adapter


class SDKHardware(ServiceHardware):

    def validate(self):
        reject_settings(self.config, {"control_socket", "sdk_socket", "error_file", "endpoint", "control_endpoint", "mode"})
        if sys.platform not in ("linux", "win32"):
            raise RuntimeError("SDK integration requires Windows x64, Linux or WSL")
        if sys.platform == "win32" and struct.calcsize("P") != 8:
            raise RuntimeError("SDK integration requires 64-bit Python on Windows")
        self.definition = definition(self.config, SDKDefinition)
        components(self.config, self.definition)

    def service_settings(self, directory):
        return {**super().service_settings(directory),
                "control_socket": ["127.0.0.1", 0] if sys.platform == "win32" else str(directory / "control.sock"),
                "sdk_socket": ["127.0.0.1", 0] if sys.platform == "win32" else str(directory / "sdk.sock"),
                "error_file": str(directory / "native.errors")}

    def connect(self, info):
        if not isinstance(info["control_socket"], str):
            return SDKClient(tuple(info["control_socket"]))
        path = Path(info["control_socket"])
        return SDKClient(str((self.config.directory / path).resolve()))

    def client_endpoints(self):
        info = self.client.request("ping")
        return {"control": self.client.control_endpoint, "sdk": info["sdk_socket"]}

    def build_sdk(self, output):
        return definition(self.config, SDKDefinition).build_sdk(output)


def create(config):
    return SDKHardware(config)


def create_service(config):
    from .service import SDKService
    model, adapter = components(config, definition(config, SDKDefinition))
    return SDKService(config.settings["control_socket"], config.settings["sdk_socket"],
                      model, adapter, config.settings.get("error_file"))
