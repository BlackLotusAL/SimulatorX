import json

import pytest

from framework.contracts import Hardware
from framework.runtime import DeviceRuntime, LifecycleError


@pytest.fixture
def factory(monkeypatch):
    events = []

    class Fake(Hardware):
        def action(self, operation):
            events.append((self.identity.rsplit("/", 1)[-1], operation))
            if self.config.settings.get("fail") == operation:
                raise RuntimeError("injected " + operation)

        def validate(self):
            self.action("validate")

        def start(self):
            self.action("start")

        def stop(self):
            self.action("stop")

        def reset(self):
            self.action("reset")

        def check_health(self):
            self.action("health")

    from protocols.tcp import host as tcp
    monkeypatch.setattr(tcp, "create", Fake)
    return events


def manifest(tmp_path, settings=({}, {})):
    hardware = []
    for index, options in enumerate(settings):
        name = "unit" + str(index)
        hardware.append({"id": name, "type": "tcp", **options})
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"id": "machine", "subsystems": [{"id": "sub", "hardware": hardware}]}))
    return path


def test_configuration_relative_paths_and_instances(tmp_path, factory, monkeypatch):
    path = manifest(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    with DeviceRuntime.from_config(path) as device:
        assert len(device.subsystems["sub"].hardware) == 2
        first, second = device.hardware()
        assert first is not second and first.config.settings is not second.config.settings
        device.reset()
    assert factory[:2] == [("unit0", "validate"), ("unit1", "validate")]
    assert factory[-2:] == [("unit1", "stop"), ("unit0", "stop")]
    device.stop()
    assert factory.count(("unit0", "stop")) == 1


@pytest.mark.parametrize("failure", ["validate", "start", "health"])
def test_failure_preflight_and_rollback(tmp_path, factory, failure):
    device = DeviceRuntime.from_config(manifest(tmp_path, ({}, {"fail": failure})))
    with pytest.raises(RuntimeError, match="injected"):
        device.start()
    if failure == "validate":
        assert not any(op == "start" for _, op in factory)
    else:
        assert factory[-2:] == [("unit1", "stop"), ("unit0", "stop")]
    with pytest.raises(RuntimeError):
        device.start()


def test_reset_failure_attempts_all_and_disables_reuse(tmp_path, factory):
    device = DeviceRuntime.from_config(manifest(tmp_path, ({"fail": "reset"}, {}))).start()
    with pytest.raises(LifecycleError, match="Reset failed"):
        device.reset()
    assert factory[-2:] == [("unit0", "reset"), ("unit1", "reset")]
    with pytest.raises(RuntimeError, match="not reusable"):
        device.check_health()
    device.stop()


def test_cleanup_aggregates_failures_and_attempts_every_instance(tmp_path, factory):
    device = DeviceRuntime.from_config(manifest(tmp_path, ({"fail": "stop"}, {"fail": "stop"}))).start()
    with pytest.raises(LifecycleError) as failure:
        device.stop()
    assert len(failure.value.failures) == 2
    assert factory[-2:] == [("unit1", "stop"), ("unit0", "stop")]
    device.stop()


@pytest.mark.parametrize("change", ["hardware", "subsystem", "selection"])
def test_duplicate_ids_and_unknown_selection_fail_before_creation(tmp_path, factory, change):
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    sub = data["subsystems"][0]
    select = None
    if change == "hardware":
        sub["hardware"][1]["id"] = "unit0"
    elif change == "subsystem":
        data["subsystems"].append(sub)
    elif change == "selection":
        select = ["sub/missing"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        DeviceRuntime.from_config(path, select)
    assert factory == []


def test_selection_avoids_loading_unselected_hardware(tmp_path, factory):
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    data["subsystems"][0]["hardware"][1]["fail"] = "validate"
    path.write_text(json.dumps(data))
    with DeviceRuntime.from_config(path, ["sub/unit0"]):
        pass
    assert {name for name, _ in factory} == {"unit0"}
