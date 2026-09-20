"""Configuration-based service ownership, with protocol-specific connection hooks."""
import json

from .config import load_factory, device_directory
from .contracts import ProcessHardware
from .process import ServiceProcess


def reject_settings(config, fields):
    removed = sorted(set(config.settings) & set(fields))
    if removed:
        raise ValueError("Removed settings: " + ", ".join(removed) +
                         "; remove connection/mode overrides and move business constants "
                         "and protocol mapping into the independent device package")


def definition(config, expected):
    removed = sorted(set(config.settings) & {"factory", "opcua_port"})
    if removed:
        raise ValueError("Removed settings: " + ", ".join(removed) +
                         "; replace factory with type (opcua/sdk/tcp) and opcua_port with port")
    reject_settings(config, {"config", "definition", "package", "mode", "profile", "parameters", "protocol",
                             "endpoint", "control_endpoint"})
    directory = device_directory(config.identity)
    if config.directory.resolve() != directory:
        raise ValueError("Resource directory must match subsystem/hardware IDs: " + str(directory))
    module_path = directory.parent / "model.py"
    if not module_path.is_file():
        raise ValueError("Missing device entrypoint: " + str(module_path) + ":create")
    _, sid, hid = config.identity.split("/")
    entrypoint = "subsystems." + sid + "." + hid + ".model:create"
    try:
        factory = load_factory(entrypoint)
    except (ImportError, AttributeError, ValueError) as exc:
        raise ValueError("Cannot load device entrypoint " + entrypoint + ": " + str(exc)) from exc
    value = factory()
    if not isinstance(value, expected):
        raise TypeError(entrypoint + " must return " + expected.__name__)
    return value


class ServiceHardware(ProcessHardware):
    pipe_control = False

    def service_settings(self, directory):
        return dict(self.config.settings)

    def connect(self, info):
        """Return a connected client, cleaning up if connection fails."""
        raise NotImplementedError

    def client_endpoints(self):
        raise NotImplementedError

    def start(self):
        if self.client is not None:
            raise RuntimeError("Hardware already started")
        self.process = self.create_process()
        self.process.start()
        self.client = self.process.client
        self.endpoints = self.client_endpoints()
        return self

    def create_process(self):
        """Construct a manager after validation; construction does not start resources."""
        def arguments(directory):
            path = directory / "settings.json"
            path.write_text(json.dumps({"identity": self.identity,
                "directory": str(self.config.directory),
                "settings": self.service_settings(directory)}), encoding="utf-8")
            return ["--type", self.config.type, "--config", str(path)]
        return ServiceProcess("framework.service", arguments, self.connect, pipe_control=self.pipe_control)
