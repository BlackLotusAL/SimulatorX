"""Source discovery for child interpreters; no framework installation is needed."""
import os
import subprocess
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2]


def source_environment(environment=None):
    """Return a child environment with this source tree first on PYTHONPATH."""
    result = dict(os.environ if environment is None else environment)
    previous = result.get("PYTHONPATH")
    result["PYTHONPATH"] = str(SOURCE_ROOT) + (os.pathsep + previous if previous else "")
    return result


def hidden_process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
