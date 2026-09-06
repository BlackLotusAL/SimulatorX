import math

import pytest

from local_service.sdk.motion import AxisProfile, Result, RotaryAxis


def ready_axis(**settings):
    axis = RotaryAxis(AxisProfile(**settings))
    assert axis.execute("Enable") == Result.OK
    assert axis.execute("Home") == Result.OK
    axis.advance(20)
    assert axis.homed and axis.done
    return axis


def test_enable_home_and_move_are_distinct_operations():
    axis = RotaryAxis(AxisProfile(initial=30))
    assert axis.execute("MoveAbsolute", {"angle": 10}) == Result.NOT_ENABLED
    assert axis.execute("Enable") == Result.OK
    assert axis.execute("MoveAbsolute", {"angle": 10}) == Result.NOT_HOMED
    assert axis.execute("Home") == Result.OK
    assert axis.busy and not axis.homed
    axis.advance(0.1)
    assert 0 < axis.position < 30
    axis.advance(10)
    assert axis.position == 0 and axis.homed and axis.done and not axis.busy


def test_analytic_trapezoid_and_negative_relative_motion():
    axis = ready_axis()
    assert axis.execute("MoveAbsolute", {"angle": 180, "speed": 90}) == Result.OK
    axis.advance(0.5)
    assert axis.position == pytest.approx(22.5)
    assert axis.velocity == pytest.approx(90)
    axis.advance(1.5)
    assert axis.position == pytest.approx(157.5)
    assert axis.velocity == pytest.approx(90)
    axis.advance(0.5)
    assert axis.position == 180 and axis.done and not axis.busy and axis.velocity == 0
    assert axis.execute("MoveRelative", {"angle": -360, "speed": 90}) == Result.OK
    axis.advance(10)
    assert axis.position == -180


def test_short_triangle_and_partition_independent_time():
    coarse, fine = ready_axis(), ready_axis()
    for axis in (coarse, fine):
        axis.execute("MoveAbsolute", {"angle": 1, "speed": 90})
    peak_time = math.sqrt(1 / 180)
    coarse.advance(peak_time)
    for _ in range(100):
        fine.advance(peak_time / 100)
    assert coarse.position == pytest.approx(0.5)
    assert coarse.velocity == pytest.approx(math.sqrt(180))
    assert fine.position == pytest.approx(coarse.position)
    assert fine.velocity == pytest.approx(coarse.velocity)
    coarse.advance(peak_time + 1e-10)
    fine.advance(10)
    assert coarse.position == fine.position == 1


def test_stop_decelerates_and_disable_clears_home():
    axis = ready_axis()
    axis.execute("MoveAbsolute", {"angle": 150, "speed": 90})
    axis.advance(0.75)
    before = axis.position
    axis.execute("Stop")
    assert axis.busy and not axis.done
    axis.advance(0.25)
    assert axis.velocity == pytest.approx(45)
    axis.advance(0.25)
    assert axis.position == pytest.approx(before + 22.5)
    assert not axis.busy and not axis.done and axis.velocity == 0
    axis.execute("Disable")
    assert not axis.homed and not axis.enabled
    assert axis.position == pytest.approx(before + 22.5)


def test_busy_bad_inputs_and_out_of_range_do_not_change_target():
    axis = ready_axis()
    before = axis.snapshot()
    for args, result in [({"angle": 181}, Result.LIMIT), ({"angle": -181}, Result.LIMIT),
                         ({"angle": 0, "speed": 91}, Result.INVALID_ARGUMENT),
                         ({"angle": float("nan")}, Result.INVALID_ARGUMENT),
                         ({"angle": True}, Result.INVALID_ARGUMENT),
                         ({"angle": 0, "axis": 2}, Result.INVALID_ARGUMENT)]:
        assert axis.execute("MoveAbsolute", args) == result
        assert axis.snapshot() == before
    axis.execute("MoveAbsolute", {"angle": 100})
    assert axis.execute("MoveRelative", {"angle": -10}) == Result.BUSY
    assert axis.execute("Home") == Result.BUSY
    assert axis.target == 100


def test_stall_freezes_time_then_replans_and_stop_cancels_stalled_move():
    axis = ready_axis()
    axis.execute("MoveAbsolute", {"angle": 100})
    axis.advance(0.2)
    position = axis.position
    axis.set_fault("stalled", True)
    axis.advance(1000)
    assert axis.position == position and axis.velocity == 0 and axis.busy and not axis.done
    axis.set_fault("stalled", False)
    axis.advance(0.1)
    assert axis.position == pytest.approx(position + 0.9)
    axis.set_fault("stalled", True)
    axis.execute("Stop")
    stopped = axis.position
    axis.set_fault("stalled", False)
    axis.advance(100)
    assert axis.position == stopped and not axis.busy and not axis.done


@pytest.mark.parametrize("fault", ["positive_limit", "negative_limit"])
def test_limit_latches_until_removed_and_cleared(fault):
    axis = ready_axis()
    axis.execute("MoveAbsolute", {"angle": 100})
    axis.advance(0.2)
    position = axis.position
    axis.set_fault(fault, True)
    assert not axis.busy and not axis.done and axis.velocity == 0 and axis.alarm
    assert axis.execute("ClearFault") == Result.LIMIT
    axis.set_fault(fault, False)
    assert axis.execute("MoveAbsolute", {"angle": 0}) == Result.FAULT
    assert axis.execute("ClearFault") == Result.OK
    assert axis.position == position and not axis.alarm
    assert axis.execute("MoveAbsolute", {"angle": 0}) == Result.OK
    axis.advance(10)
    assert axis.done and axis.position == 0


def test_reset_removes_motion_faults_and_reference():
    axis = ready_axis(initial=5)
    axis.execute("MoveAbsolute", {"angle": 100})
    axis.advance(0.3)
    axis.set_fault("stalled", True)
    axis.set_fault("positive_limit", True)
    axis.reset()
    assert axis.snapshot() == RotaryAxis(AxisProfile(initial=5)).snapshot()


@pytest.mark.parametrize("settings", [dict(minimum=180), dict(initial=181), dict(home=-181),
                                         dict(max_speed=0), dict(acceleration=-1),
                                         dict(tick_interval=float("nan")), dict(deceleration=True)])
def test_invalid_profiles_rejected(settings):
    with pytest.raises(ValueError):
        AxisProfile(**settings)
