"""Local connection handoff; service ownership stays in the demo backend."""
import json
import os
from pathlib import Path
from framework.source import SOURCE_ROOT, source_environment


def demo_environment():
    environment = source_environment()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(SOURCE_ROOT.parent / "examples") + os.pathsep + environment["PYTHONPATH"]
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    return environment


def write_connection(directory, endpoint, contract):
    path = Path(directory) / "connection.json"
    path.write_text(json.dumps({"endpoint": endpoint, "contract": contract}), encoding="utf-8")
    return path
