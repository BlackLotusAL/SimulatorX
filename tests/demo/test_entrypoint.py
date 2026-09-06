"""The browser entrypoint owns its ports and works outside the repository."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest
pytest.importorskip("flask", reason="Install requirements-demo.lock")

from local_service.common.runtime import SOURCE_ROOT, hidden_process_options


def test_demo_source_entrypoint_port_conflict_and_managed_shutdown(tmp_path):
    ready = tmp_path / "ready.json"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    args = [sys.executable, str(SOURCE_ROOT / "main.py"), "demo", "--no-browser", "--managed",
            "--ready-file", str(ready), "--artifacts", str(tmp_path / "reports")]
    with (tmp_path / "demo.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(args, cwd=str(tmp_path), env=environment, stdin=subprocess.PIPE,
                                   stdout=log, stderr=subprocess.STDOUT, text=True, **hidden_process_options())
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                assert process.poll() is None, (tmp_path / "demo.log").read_text()
                assert time.monotonic() < deadline
                time.sleep(0.05)
            info = json.loads(ready.read_text())
            url = info["url"]
            with urlopen(url, timeout=3) as response:
                assert response.status == 200
                assert "真空腔室" in response.read().decode("utf-8")
            with urlopen(url + "/api/state", timeout=3) as response:
                assert json.load(response)["snapshot"]["connected"]
            duplicate = subprocess.run([sys.executable, str(SOURCE_ROOT / "main.py"), "demo", "--no-browser",
                                        "--port", str(urlparse(url).port)], cwd=str(tmp_path), env=environment,
                                       capture_output=True, text=True, timeout=15, **hidden_process_options())
            assert duplicate.returncode != 0
            assert process.poll() is None
            with urlopen(url + "/api/cases", timeout=3) as response:
                assert len(json.load(response)["cases"]) == 5
            process.communicate("stop\n", timeout=15)
            assert process.returncode == 0, (tmp_path / "demo.log").read_text()
            for address in (url, info["opcua_endpoint"]):
                with socket.socket() as probe:
                    probe.settimeout(1)
                    assert probe.connect_ex(("127.0.0.1", urlparse(address).port)) != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
