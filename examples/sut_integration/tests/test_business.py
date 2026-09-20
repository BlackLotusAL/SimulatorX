"""Business assertions read only SUT results, never simulator state."""
import sys

import pytest

from integration_support import terminal

pytestmark = [pytest.mark.business, pytest.mark.integration]


@pytest.mark.parametrize("scenario,expected", [
    (("vacuum", "normal"), None),
    (("vacuum", "interlock"), "vacuum_alarm"),
    (("vacuum", "bad_quality"), "bad_quality"),
    (("vacuum", "invalid_pressure"), "invalid_pressure"),
    (("vacuum", "timeout"), "timeout"),
], indirect=["scenario"])
def test_pump(sut, expected):
    status = terminal(sut, sut.start_pump())
    assert status["error_code"] == expected, status
    assert status["state"] == ("failed" if expected else "succeeded"), status
    assert status["last_synced_at"] is not None
    if expected is None:
        assert status["observed"]["pressure_pa"] <= 1020
        assert status["observed"]["state_code"] == 3


@pytest.mark.linux_sdk
@pytest.mark.skipif(sys.platform != "linux", reason="Requires actual Linux .so")
@pytest.mark.parametrize("scenario,expected", [
    (("motion", "normal"), None),
    (("motion", "sdk_error"), "sdk_error"),
    (("motion", "query_error"), "sdk_error"),
    (("motion", "limit"), "sdk_error"),
    (("motion", "timeout"), "timeout"),
], indirect=["scenario"])
def test_move(sut, expected):
    status = terminal(sut, sut.start_move(30, 90))
    assert status["error_code"] == expected, status
    assert status["state"] == ("failed" if expected else "succeeded"), status
    if expected is None:
        assert status["observed"]["position_deg"] == pytest.approx(30, abs=0.01)
        assert status["observed"]["done"] and not status["observed"]["busy"]


@pytest.mark.parametrize("scenario,expected", [
    (("detector", "normal"), None),
    (("detector", "recover"), None),
    (("detector", "device_error"), "device_error"),
    (("detector", "measurement_error"), "device_error"),
    (("detector", "timeout"), "timeout"),
], indirect=["scenario"])
def test_communication(sut, expected):
    status = terminal(sut, sut.start_check())
    assert status["error_code"] == expected, status
    assert status["state"] == ("failed" if expected else "succeeded"), status
    if expected is None:
        assert status["observed"]["ready"] is True
        assert status["observed"]["measurement"] == 125


@pytest.mark.parametrize("scenario", [("detector", "success_then_error")], indirect=True)
def test_new_check_reports_current_failure_instead_of_previous_success(sut):
    first = sut.start_check()
    previous = terminal(sut, first)
    assert previous["state"] == "succeeded"
    second = sut.start_check()
    current = terminal(sut, second)
    assert first != second
    assert current["state"] == "failed" and current["error_code"] == "device_error"
    assert current["observed"]["status"] == 7
    assert "measurement" not in current["observed"]
    assert sut.get_status(first) == previous


@pytest.mark.parametrize("scenario", [("vacuum", "normal")], indirect=True)
def test_repeated_pump_has_separate_operation_results(sut):
    first = sut.start_pump()
    previous = terminal(sut, first)
    assert previous["state"] == "succeeded"
    second = sut.start_pump()
    current = terminal(sut, second)
    assert first != second
    assert current["state"] == "succeeded"
    assert current["last_synced_at"] > previous["last_synced_at"]
    assert sut.get_status(first) == previous


@pytest.mark.linux_sdk
@pytest.mark.skipif(sys.platform != "linux", reason="Requires actual Linux .so")
@pytest.mark.parametrize("scenario", [("motion", "normal")], indirect=True)
def test_repeated_move_waits_for_new_target(sut):
    first = sut.start_move(30, 90)
    previous = terminal(sut, first)
    assert previous["state"] == "succeeded"
    second = sut.start_move(-10, 90)
    current = terminal(sut, second)
    assert first != second
    assert current["state"] == "succeeded"
    assert current["observed"]["position_deg"] == pytest.approx(-10, abs=0.01)
    assert sut.get_status(first) == previous
