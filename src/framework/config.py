"""Load trusted, local device manifests without starting hardware."""
import importlib
import json
import keyword
import re
from dataclasses import dataclass
from pathlib import Path

from .source import SOURCE_ROOT

DEFAULT_DEVICE = SOURCE_ROOT / "device.json"

PROTOCOL_TYPES = ("opcua", "sdk", "tcp")


def read_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object: " + str(path))
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value):
        raise ValueError("IDs must start with a letter and contain letters, digits, _ or -")
    return value


def package_identifier(value):
    if not isinstance(value, str) or not value.isidentifier() or keyword.iskeyword(value):
        raise ValueError("Subsystem and hardware IDs must be Python package names: " + str(value))
    return value


def device_directory(identity):
    parts = identity.split("/")
    if len(parts) != 3:
        raise ValueError("Hardware identity must be machine/subsystem/hardware")
    sid, hid = (package_identifier(part) for part in parts[1:])
    return SOURCE_ROOT.resolve() / "subsystems" / sid / hid / "resources"


def load_factory(spec):
    if not isinstance(spec, str) or spec.count(":") != 1:
        raise ValueError("Factory must be module:factory")
    module, name = spec.split(":")
    factory = getattr(importlib.import_module(module), name)
    if not callable(factory):
        raise ValueError("Factory is not callable: " + spec)
    return factory


def protocol_type(value):
    if not isinstance(value, str) or value not in PROTOCOL_TYPES:
        raise ValueError("type must be one of: opcua, sdk, tcp")
    return value


def load_protocol(value):
    """Only built-in protocol hosts; device factories remain package-local."""
    return importlib.import_module("protocols." + protocol_type(value) + ".host")


@dataclass(frozen=True)
class HardwareConfig:
    identity: str
    type: str
    settings: dict
    directory: Path

    def file(self, key, default=None):
        value = self.settings.get(key, default)
        if value is None:
            return None
        path = Path(value)
        path = (self.directory / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file():
            raise ValueError("Missing " + key + " resource: " + str(path))
        return path


def load_device(path, select=None):
    path = Path(path).resolve()
    data = read_object(path)
    device_id = identifier(data["id"])
    subsystems = data["subsystems"]
    if not isinstance(subsystems, list) or not subsystems:
        raise ValueError("Device must contain a nonempty subsystem list")
    selections = set(select or [])
    all_keys, subsystem_ids, rows = set(), set(), []
    for subsystem in subsystems:
        sid = package_identifier(subsystem["id"])
        if sid in subsystem_ids:
            raise ValueError("Duplicate subsystem: " + sid)
        subsystem_ids.add(sid)
        hardware = subsystem["hardware"]
        if not isinstance(hardware, list) or not hardware:
            raise ValueError("Subsystem must contain hardware: " + sid)
        for item in hardware:
            hid = package_identifier(item["id"])
            key = sid + "/" + hid
            if key in all_keys:
                raise ValueError("Duplicate hardware: " + key)
            all_keys.add(key)
            removed = sorted(set(item) & {"mode", "config", "definition", "package", "factory", "opcua_port"})
            if removed:
                raise ValueError("Removed hardware fields: " + ", ".join(removed) +
                                 "; use type (opcua/sdk/tcp), port and inline settings with IDs matching the device package")
            kind = protocol_type(item.get("type"))
            if selections and key not in selections:
                continue
            identity = device_id + "/" + key
            settings = {name: value for name, value in item.items() if name not in {"id", "type"}}
            rows.append((sid, hid, HardwareConfig(identity, kind, settings,
                                                 device_directory(identity))))
    if selections - all_keys:
        raise ValueError("Unknown hardware selection: " + ", ".join(sorted(selections - all_keys)))
    return device_id, rows
