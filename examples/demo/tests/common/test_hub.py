"""Cross-protocol reservation, switching and unsupported-platform isolation."""
import pytest
pytest.importorskip("flask")
from demo.web.app import create_app
from demo.tests.support import until, SDK_SUPPORTED

DEMO_PROTOCOLS = ("tcp",)


def test_global_reservation_switching_and_cancel(hub):
    client = create_app(hub).test_client()
    runtime = hub.get("tcp")
    hub.start_run("tcp", ["timeout", "normal"])
    assert client.get("/api/demos/plc/state").status_code == 200
    assert client.post("/api/demos/plc/runs", json={"case_ids": ["normal"]}).status_code == 409
    assert client.post("/api/demos/tcp/manual", json={"action": "check"}).status_code == 409
    assert client.post("/api/demos/tcp/reset", json={}).status_code == 409
    until(lambda: runtime.controller_state["status"] == "running")
    token = runtime.token
    assert client.post("/api/demos/tcp/control", json={"op": "reset"}).status_code == 409
    assert client.post("/api/demos/tcp/control", json={"op": "call"},
                       headers={"Authorization": "Bearer " + token}).status_code == 400
    runtime.cancel()
    until(lambda: runtime.mode == "idle")
    assert runtime.run["status"] == "cancelled"
    assert client.post("/api/demos/tcp/control", json={"op": "reset"},
                       headers={"Authorization": "Bearer " + token}).status_code == 409


@pytest.mark.skipif(SDK_SUPPORTED, reason="SDK is supported on this platform")
def test_unsupported_sdk_does_not_block_other_demos(hub):
    client = create_app(hub).test_client()
    processes = {kind: hub.get(kind).process.process for kind in ("plc", "tcp")}
    for _ in range(2):
        assert client.post("/api/demos/sdk/initialize", json={}).status_code == 202
        until(lambda: "sdk" not in hub.starting)
        assert "Linux" in hub.errors["sdk"]
        assert "sdk" not in hub.runtimes
        for kind, process in processes.items():
            assert hub.get(kind).process.process is process and process.poll() is None
            assert client.get("/api/demos/" + kind + "/state").status_code == 200
    hub.get("tcp").manual("check")
    until(lambda: hub.get("tcp").controller_state["status"] == "completed")
