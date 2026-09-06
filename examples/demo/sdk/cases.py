from .catalog import inject
from demo.common.scenarios import wait_terminal
import pytest


def exercise(scenario, fault):
    kind, client, sut, context = scenario
    if fault != "normal":
        context.emit("injecting", "预置故障条件：" + fault)
        inject(client, fault)
        context.emit("injected", "故障已预置，仅影响后续调用", fault=fault)
    operation = sut.start_move(30, 90)
    status = wait_terminal(sut, context, operation)
    expected = None if fault in ("normal", "recover") else "timeout" if fault == "timeout" else 'sdk_error'
    assert status["error_code"] == expected, status
    assert status["state"] == ("failed" if expected else "succeeded"), status
    if expected is None:
        assert status["observed"]["position_deg"] == pytest.approx(30, abs=0.01)
        assert status["observed"]["done"] and not status["observed"]["busy"]
    context.emit("assertion", "SUT 业务结果符合预期")
    context.hold()


def test_normal(scenario): exercise(scenario, "normal")
def test_sdk_error(scenario): exercise(scenario, "sdk_error")
def test_query_error(scenario): exercise(scenario, "query_error")
def test_limit(scenario): exercise(scenario, "limit")
def test_timeout(scenario): exercise(scenario, "timeout")
