from .catalog import inject
from demo.common.scenarios import wait_terminal


def exercise(scenario, fault):
    kind, client, sut, context = scenario
    if fault != "normal":
        context.emit("injecting", "预置故障条件：" + fault)
        inject(client, fault)
        context.emit("injected", "故障已预置，仅影响后续调用", fault=fault)
    operation = sut.start_check()
    status = wait_terminal(sut, context, operation)
    expected = None if fault in ("normal", "recover") else "timeout" if fault == "timeout" else 'device_error'
    assert status["error_code"] == expected, status
    assert status["state"] == ("failed" if expected else "succeeded"), status
    if expected is None:
        assert status["observed"]["ready"] and status["observed"]["measurement"] == 100
    context.emit("assertion", "SUT 业务结果符合预期")
    context.hold()


def test_normal(scenario): exercise(scenario, "normal")
def test_recover(scenario): exercise(scenario, "recover")
def test_device_error(scenario): exercise(scenario, "device_error")
def test_measurement_error(scenario): exercise(scenario, "measurement_error")
def test_timeout(scenario): exercise(scenario, "timeout")
