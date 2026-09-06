import pytest
pytest.importorskip("flask")
from demo.tests.support import (
    until, assert_five_real_cases, assert_manual_fault_disconnect_and_recovery,
    assert_forced_cancel_rebuilds_only_active_device,
)
from demo.hub import DemoHub
from demo.web.app import create_app
from demo.tests.support import SDK_REQUIRED

pytestmark = SDK_REQUIRED

DEMO_PROTOCOLS = ("sdk",)


@pytest.mark.parametrize("repeat", range(2))
def test_five_real_cases_repeated(hub, repeat):
    assert_five_real_cases(hub, "sdk")


def test_manual_fault_disconnect_and_recovery(hub):
    assert_manual_fault_disconnect_and_recovery(hub, "sdk", ("enable", "home"))


def test_forced_cancel_rebuilds_only_active_device(hub):
    assert_forced_cancel_rebuilds_only_active_device(hub, "sdk")


def test_lazy_initialization_failure_and_retry_isolated(hub, tmp_path):
    broken = DemoHub(artifacts=tmp_path, library=tmp_path / "missing-library")
    broken.url = hub.url
    try:
        broken.initialize("sdk")
        until(lambda: "sdk" not in broken.starting)
        assert "sdk" in broken.errors
        assert "tcp" not in broken.runtimes
        assert hub.get("plc").snapshot["connected"]
        broken.library = hub.get("sdk").library
        broken.initialize("sdk")
        broken.initialize("sdk")
        until(lambda: "sdk" not in broken.starting)
        assert "sdk" not in broken.errors
        assert broken.get("sdk").snapshot["connected"]
    finally:
        broken.stop()


@pytest.mark.parametrize("payload", [{"action": []}, {"action": "inject", "fault": []},
                                      {"action": "move", "target": 181},
                                      {"action": "move", "speed": True},
                                      {"action": "move", "target": 10**400}])
def test_invalid_manual_inputs_are_rejected(hub, payload):
    assert create_app(hub).test_client().post("/api/demos/sdk/manual", json=payload).status_code == 400
