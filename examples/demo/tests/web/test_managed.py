"""Managed web service shutdown preserves the bridge during test cleanup."""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen
import pytest
pytest.importorskip("flask")
from framework.source import SOURCE_ROOT, hidden_process_options
from demo.tests.support import until


def test_managed_exit_keeps_bridge_alive_for_active_test_cleanup(tmp_path):
    ready = tmp_path / "ready.json"
    artifacts = tmp_path / "reports"
    with (tmp_path / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-X", "utf8", str(SOURCE_ROOT / "main.py"),
            "demo", "--no-browser", "--managed", "--ready-file", str(ready), "--artifacts", str(artifacts)],
            cwd=tmp_path, env={**os.environ, "PYTHONUTF8": "1"}, stdin=subprocess.PIPE,
            stdout=log, stderr=subprocess.STDOUT, text=True, **hidden_process_options())
        try:
            until(ready.exists)
            base = json.loads(ready.read_text(encoding="utf-8"))["url"]
            def api(path, data=None):
                request = Request(base + path, data=None if data is None else json.dumps(data).encode(),
                                  headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=5) as response:
                    return json.load(response)
            api("/api/demos/tcp/initialize", {})
            until(lambda: next(d for d in api("/api/demos")["demos"] if d["id"] == "tcp")["status"] == "ready")
            run = api("/api/demos/tcp/runs", {"case_ids": ["timeout", "normal"]})
            def injected():
                state = api("/api/demos/tcp/state")
                assert state["run"]["status"] == "running", (artifacts / "tcp" / run["run_id"] / "pytest.log").read_text(encoding="utf-8")
                return any(e["kind"] == "injected" for e in state["events"])
            until(injected)
            process.communicate("stop\n", timeout=20)
            assert process.returncode == 0, (tmp_path / "server.log").read_text(encoding="utf-8")
            events = [json.loads(line) for line in (artifacts / "tcp" / run["run_id"] / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            assert any(e["kind"] == "cleanup" for e in events)
            assert any(e["kind"] == "case_result" and e["status"] == "cancelled" for e in events)
            assert not any(e["kind"] == "environment_error" for e in events)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
